import streamlit as st
import streamlit.components.v1 as components
import sqlite3
import json
import pandas as pd
from datetime import datetime
from pathlib import Path
import time
from typing import Optional, Union

# --- 原始 HTML 加载（必须在 get_html_content() 使用前定义） ---
# 优先从同目录下的 HTML 文件读取，避免 USER_ORIGINAL_HTML 未定义导致运行时错误。
_html_path = Path(__file__).with_name("工作流工作记录分析系统 - V46.html")
try:
    USER_ORIGINAL_HTML = _html_path.read_text(encoding="utf-8")
except Exception as e:
    USER_ORIGINAL_HTML = f"""
<!DOCTYPE html>
<html lang="zh-CN">
  <head><meta charset="utf-8"><title>HTML 加载失败</title></head>
  <body style="font-family: sans-serif; padding: 24px;">
    <h2>无法读取 {_html_path.name}</h2>
    <pre>{str(e)}</pre>
  </body>
</html>
"""

def _safe_json_loads(s: Optional[str]) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


def _format_hhmmss(seconds: Union[float, int]) -> str:
    try:
        s = int(max(0, seconds))
    except Exception:
        s = 0
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    return f"{h:02d}:{m:02d}:{ss:02d}"


def _format_hhmm(seconds: Union[float, int]) -> str:
    try:
        s = int(max(0, seconds))
    except Exception:
        s = 0
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h:02d}:{m:02d}"


def _default_state(user_display_name: str = "") -> dict:
    return {
        "tasks": [],
        "attendance": [],
        "activeTaskId": None,
        "isClockedIn": False,
        "isMeeting": False,
        "isResting": False,
        "meetingSeconds": 0,
        "restSeconds": 0,
        "meetingHistory": [],
        "restHistory": [],
        "clockInTime": None,
        "clockInFullMs": None,
        "userName": user_display_name or "",
        # 任务工时（全局累计，不绑定某一任务）：从“点击开始任务”开始计时，停止任务/切会议休息/下班则暂停累计
        "workSeconds": 0,
        "lastWorkTimestamp": None,
    }


def _load_state_from_db(state_json: Optional[str], user_display_name: str = "") -> dict:
    data = _safe_json_loads(state_json)
    base = _default_state(user_display_name=user_display_name)
    if isinstance(data, dict):
        base.update(data)
    # 兜底字段
    base.setdefault("tasks", [])
    base.setdefault("attendance", [])
    base.setdefault("meetingHistory", [])
    base.setdefault("restHistory", [])
    base.setdefault("meetingSeconds", 0)
    base.setdefault("restSeconds", 0)
    return base


def _compute_admin_status(state: dict) -> dict:
    """
    计算管理员要展示的“一条状态栏”：
    - 打卡状态（● 未打卡 / ● 已上班）
    - 模式（待机/会议中/休息中/工作中/空闲）
    - 任务累计（当前任务的计时，和 HTML 顶栏一致）
    - 今日会议/休息（含进行中的实时增量）
    """
    now_ms = int(time.time() * 1000)
    is_clocked = bool(state.get("isClockedIn"))
    is_meeting = bool(state.get("isMeeting"))
    is_resting = bool(state.get("isResting"))
    active_id = state.get("activeTaskId")

    # 模式
    if not is_clocked:
        mode = "待机"
    elif is_meeting:
        mode = "会议中"
    elif is_resting:
        mode = "休息中"
    elif active_id:
        mode = "工作中"
    else:
        mode = "空闲"

    # “正在执行：xxx”（对齐原 HTML 巨幕状态）
    active_task_name = ""
    if active_id:
        for t in state.get("tasks", []):
            if str(t.get("id")) == str(active_id):
                active_task_name = str(t.get("name") or "")
                break

    if not is_clocked:
        giant = "待机中"
    elif is_meeting:
        giant = "会议进行中..."
    elif is_resting:
        giant = "休息中..."
    elif active_id:
        giant = f"正在执行：{active_task_name or '任务'}"
    else:
        giant = "任务：无 (请开启记录！)"

    # 任务累计（任务工时）：使用前端维护的全局计时器 workSeconds，避免绑定某一个任务
    task_seconds = 0.0
    try:
        task_seconds = float(state.get("workSeconds") or 0)
    except Exception:
        task_seconds = 0.0
    if is_clocked and active_id and state.get("lastWorkTimestamp"):
        try:
            task_seconds += max(0.0, (now_ms - int(state["lastWorkTimestamp"])) / 1000.0)
        except Exception:
            pass

    # 会议/休息累计：优先用 history 汇总（避免刷新后归零），并对进行中的条目叠加实时增量
    def _sum_history(history_key: str, running_flag: bool) -> float:
        total = 0.0
        history = state.get(history_key) or []
        for h in history:
            dur = h.get("duration")
            if dur is None:
                # 进行中：用 startMs -> now 计算
                if running_flag and not h.get("end") and h.get("startMs"):
                    try:
                        dur = max(0.0, (now_ms - int(h["startMs"])) / 1000.0)
                    except Exception:
                        dur = 0.0
                else:
                    dur = 0.0
            try:
                total += float(dur or 0)
            except Exception:
                pass
        return total

    meeting_seconds = _sum_history("meetingHistory", is_clocked and is_meeting)
    rest_seconds = _sum_history("restHistory", is_clocked and is_resting)

    # 兜底：如果历史为空但 seconds 字段有值，取更大者
    try:
        meeting_seconds = max(meeting_seconds, float(state.get("meetingSeconds") or 0))
    except Exception:
        pass
    try:
        rest_seconds = max(rest_seconds, float(state.get("restSeconds") or 0))
    except Exception:
        pass

    return {
        "clock_text": "● 已上班" if is_clocked else "● 未打卡",
        "clock_color": "#10b981" if is_clocked else "#94a3b8",
        "mode": mode,
        "giant": giant,
        "task": _format_hhmmss(task_seconds) if is_clocked else "00:00:00",
        "meeting": _format_hhmmss(meeting_seconds) if is_clocked else "00:00:00",
        "rest": _format_hhmmss(rest_seconds) if is_clocked else "00:00:00",
    }


def _build_admin_tables(state: dict, employee_username: str) -> dict:
    # 表格一：任务汇总
    tasks_rows = []
    for t in state.get("tasks", []):
        spent = float(t.get("spentSeconds") or 0)
        tasks_rows.append(
            {
                "任务名称": t.get("name") or "",
                "状态": "已完成" if t.get("completed") else "进行中",
                "制定日期": t.get("createdAt") or "--",
                "预计(h)": t.get("estTime") or "",
                "完成日期": t.get("completedAt") or "--",
                "总耗时": _format_hhmm(spent),
                "产出证明": (t.get("dev") or "").strip(),
            }
        )

    # 表格二：工时统计
    att_rows = []
    for a in state.get("attendance", []):
        task_total = float(a.get("taskTotal") or 0)
        meeting = float(a.get("meeting") or 0)
        rest = float(a.get("rest") or 0)
        total_clocked = float(a.get("totalClocked") or 0)
        other = max(0.0, total_clocked - task_total - meeting - rest)
        att_rows.append(
            {
                "日期": a.get("date") or "",
                "上班打卡": a.get("clockIn") or "",
                "下班打卡": a.get("clockOut") or "",
                "任务总长": _format_hhmm(task_total),
                "会议总长": _format_hhmm(meeting),
                "休息总长": _format_hhmm(rest),
                "其他碎型": _format_hhmm(other),
            }
        )

    # 表格三：全流水详细审计日志
    now_ms = int(time.time() * 1000)
    logs = []
    if state.get("clockInFullMs"):
        logs.append(
            {
                "ms": int(state["clockInFullMs"]),
                "动作名称": "【上班打卡】",
                "关联内容": "--",
                "开始时间": state.get("clockInTime") or "--",
                "结束时间": "--",
                "时长": "--",
                "详细记录": "",
            }
        )

    active_id = state.get("activeTaskId")
    for t in state.get("tasks", []):
        for s in (t.get("solutions") or []):
            for h in (s.get("history") or []):
                start_ms = h.get("startMs")
                if start_ms is None:
                    continue
                end = h.get("end")
                dur = h.get("duration")
                if dur is None and str(active_id) == str(t.get("id")) and not end:
                    dur = max(0.0, (now_ms - int(start_ms)) / 1000.0)
                logs.append(
                    {
                        "ms": int(start_ms),
                        "动作名称": "任务执行",
                        "关联内容": f"{t.get('name','')}-{s.get('text','')}",
                        "开始时间": h.get("start") or "",
                        "结束时间": end or "进行中",
                        "时长": _format_hhmm(dur) if dur and dur > 0 else "--",
                        "详细记录": (s.get("researchNote") or "").strip(),
                    }
                )

    for h in state.get("meetingHistory", []):
        start_ms = h.get("startMs")
        if start_ms is None:
            continue
        end = h.get("end")
        dur = h.get("duration")
        if dur is None and state.get("isMeeting") and not end:
            dur = max(0.0, (now_ms - int(start_ms)) / 1000.0)
        logs.append(
            {
                "ms": int(start_ms),
                "动作名称": "会议沟通",
                "关联内容": "内部沟通",
                "开始时间": h.get("start") or "",
                "结束时间": end or "进行中",
                "时长": _format_hhmm(dur) if dur and dur > 0 else "--",
                "详细记录": "",
            }
        )

    for h in state.get("restHistory", []):
        start_ms = h.get("startMs")
        if start_ms is None:
            continue
        end = h.get("end")
        dur = h.get("duration")
        if dur is None and state.get("isResting") and not end:
            dur = max(0.0, (now_ms - int(start_ms)) / 1000.0)
        logs.append(
            {
                "ms": int(start_ms),
                "动作名称": "休息午休",
                "关联内容": "--",
                "开始时间": h.get("start") or "",
                "结束时间": end or "进行中",
                "时长": _format_hhmm(dur) if dur and dur > 0 else "--",
                "详细记录": "",
            }
        )

    # 下班打卡（如果已下班，取最后一条考勤）
    if not state.get("isClockedIn") and state.get("attendance"):
        last_att = state["attendance"][-1]
        if last_att.get("clockOutFullMs"):
            logs.append(
                {
                    "ms": int(last_att["clockOutFullMs"]),
                    "动作名称": "【下班打卡】",
                    "关联内容": "--",
                    "开始时间": last_att.get("clockOut") or "--",
                    "结束时间": "--",
                    "时长": "--",
                    "详细记录": "",
                }
            )

    logs = sorted(logs, key=lambda x: x.get("ms", 0))
    for row in logs:
        row.pop("ms", None)

    return {
        "tasks": pd.DataFrame(tasks_rows),
        "attendance": pd.DataFrame(att_rows),
        "logs": pd.DataFrame(logs),
    }

# --- 数据库初始化 ---
def init_db():
    conn = sqlite3.connect('workflow_system.db')
    c = conn.cursor()
    # 用户表: username, password, role (admin/employee)
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT, role TEXT)''')
    # 数据表: username, state_json (存储工作流的所有数据)
    c.execute('''CREATE TABLE IF NOT EXISTS user_data 
                 (username TEXT PRIMARY KEY, state_json TEXT, last_updated TIMESTAMP)''')
    
    # 创建默认管理员 (如果不存在)，并确保默认密码为 admin
    c.execute("INSERT OR IGNORE INTO users VALUES ('admin', 'admin', 'admin')")
    c.execute("UPDATE users SET password='admin', role='admin' WHERE username='admin'")
    conn.commit()
    conn.close()

def get_db_connection():
    return sqlite3.connect('workflow_system.db')

# --- HTML 注入工具：把 CSS/JS 插到正确的位置（避免把脚本拼在 </html> 之后导致不执行/不稳定） ---
def _inject_before_tag(html: str, tag: str, insertion: str) -> str:
    """
    在 html 中第一个出现的 tag（不区分大小写）之前插入 insertion。
    若找不到 tag，则直接在末尾追加 insertion。
    """
    if not html:
        return insertion
    lower = html.lower()
    idx = lower.find(tag.lower())
    if idx == -1:
        return html + insertion
    return html[:idx] + insertion + html[idx:]


# --- 核心 HTML 模板逻辑 ---
# 这里我们将你提供的 HTML 包装成一个函数，并根据角色动态修改
def get_html_content(
    user_state_json,
    is_admin=False,
    user_display_name="",
    hide_export: bool = False,
    readonly: bool = False,
    enable_sync: bool = True,
):
    # 如果数据库里没数据，使用默认初始状态
    if not user_state_json:
        user_state_json = json.dumps({
            "tasks": [], "attendance": [], "activeTaskId": None,
            "isClockedIn": False, "isMeeting": False, "isResting": False,
            "meetingSeconds": 0, "restSeconds": 0,
            "meetingHistory": [], "restHistory": [],
            "clockInTime": None, "clockInFullMs": None, "userName": user_display_name
        })

    # 注入一部分 JS 代码，用于将数据传回 Streamlit
    # 并且根据角色隐藏导出按钮
    hide_export_css = ""
    if hide_export or (not is_admin):
        hide_export_css = "<style>.btn-large[onclick='exportToCSV()'] { display: none !important; }</style>"

    readonly_css = ""
    if readonly:
        # 只读投射：禁用编辑/拖拽，并隐藏会改变状态/触发计时/导出/新增等按钮
        readonly_css = """
        <style>
          /* 禁用所有可编辑区域 */
          [contenteditable="true"] { pointer-events: none !important; user-select: text !important; }
          .drag-handle { pointer-events: none !important; }

          /* 隐藏会改变状态的控制区 */
          #btnClock, #btnMeeting, #btnRest { display: none !important; }
          .add-task-row { display: none !important; }
          button[onclick*="confirmAddTask"] { display: none !important; }
          button[onclick*="toggleTask"] { display: none !important; }
          button[onclick*="completeTask"] { display: none !important; }
          button[onclick*="reopen"] { display: none !important; }
          button[onclick*="addSolu"] { display: none !important; }

          /* 避免只读下看起来像可点 */
          button { cursor: default !important; }
        </style>
        """

    # 注入：员工侧把 state 同步到本机的 sync_server（SQLite）
    # 注意：管理员的“只读投射”必须关闭同步，否则会把旧快照反向覆盖数据库。
    sync_js = ""
    if enable_sync and (not readonly):
        sync_js = f"""
        <script>
          const __syncUser = {json.dumps(user_display_name)};
          function __getSyncBase() {{
            try {{
              if (window.parent && window.parent.location && window.parent.location.hostname) {{
                return `http://${{window.parent.location.hostname}}:8502`;
              }}
            }} catch (e) {{}}
            return 'http://localhost:8502';
          }}

          let __syncTimer = null;
          function __postSync() {{
            if (!__syncUser) return;
            try {{
              const url = __getSyncBase() + '/sync';
              const payload = JSON.stringify({{ username: __syncUser, state }});

              // 优先用 sendBeacon（更适合页面切换/iframe，且不会触发 CORS 预检）
              if (navigator && navigator.sendBeacon) {{
                const blob = new Blob([payload], {{ type: 'text/plain' }});
                navigator.sendBeacon(url, blob);
                return;
              }}

              // fallback：使用 text/plain 避免 application/json 导致的 OPTIONS 预检
              fetch(url, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'text/plain' }},
                keepalive: true,
                body: payload
              }});
            }} catch (e) {{}}
          }}
          function __scheduleSync() {{
            if (!__syncUser) return;
            if (__syncTimer) clearTimeout(__syncTimer);
            __syncTimer = setTimeout(() => {{
              __postSync();
            }}, 300);
          }}

          // 心跳：确保状态稳定写回 DB
          setInterval(() => {{
            try {{
              if (state && (state.isClockedIn || state.isMeeting || state.isResting || state.activeTaskId)) {{
                __postSync();
              }}
            }} catch (e) {{}}
          }}, 2000);
        </script>
        """

    # 把 CSS 放到 </head> 前（若没有 head，则追加）
    css_inject = f"{hide_export_css}\n{readonly_css}\n"
    html = USER_ORIGINAL_HTML
    html = _inject_before_tag(html, "</head>", css_inject)

    # 把覆盖逻辑插到 </body> 前（确保脚本在文档内，且执行顺序可控）
    js_inject = f"""
{sync_js}
<script>
  // 覆盖原始 state（来自 DB），并主动刷新 UI（兼容原脚本已运行的情况）
  try {{
    state = {user_state_json};
    if (typeof renderTable === 'function') renderTable();
    if (typeof updateUIStatus === 'function') updateUIStatus();
  }} catch (e) {{}}
</script>
"""

    # 只有员工端才启用“强制同步”逻辑；管理员投射只读预览必须禁用，避免反向覆盖。
    if enable_sync and (not readonly):
        js_inject += """
<script>
  // 初始化“任务工时”全局计时器字段（不绑定某个任务）
  try {
    if (typeof state.workSeconds === 'undefined' || state.workSeconds === null) state.workSeconds = 0;
    if (typeof state.lastWorkTimestamp === 'undefined') state.lastWorkTimestamp = null;
  } catch (e) {}

  function __forceSyncNow() {
    try {
      if (typeof document !== 'undefined' && document.getElementById('userNameInput')) {
        state.userName = document.getElementById('userNameInput').value;
      }
    } catch (e) {}
    try { __postSync(); } catch (e) {}
    try { __scheduleSync(); } catch (e) {}
  }

  const __origSaveState = (typeof saveState === 'function') ? saveState : null;
  function saveState() {
    try { if (__origSaveState) __origSaveState(); } catch (e) {}
    try {
      if (typeof document !== 'undefined' && document.getElementById('userNameInput')) {
        state.userName = document.getElementById('userNameInput').value;
      }
      localStorage.setItem('perf_v46_state', JSON.stringify(state));
    } catch (e) {}
    __forceSyncNow();
  }

  function __wrap(fnName) {
    try {
      const fn = window[fnName];
      if (typeof fn !== 'function') return;
      window[fnName] = function() {
        const ret = fn.apply(this, arguments);
        try { __forceSyncNow(); } catch (e) {}
        return ret;
      }
    } catch (e) {}
  }
  ['toggleClock','toggleMeeting','toggleRest','toggleTask','completeTask','reopen','confirmAddTask','addSolu'].forEach(__wrap);

  } catch (e) {}

  try {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') __forceSyncNow();
    });
    window.addEventListener('beforeunload', () => __forceSyncNow());
  } catch (e) {}
</script>
"""
    html = _inject_before_tag(html, "</body>", js_inject)
    return html

# --- 页面配置 ---
st.set_page_config(page_title="工作流工作记录分析系统", layout="wide", initial_sidebar_state="expanded")
init_db()

# 主区域只显示 HTML：隐藏 Streamlit 顶栏/页脚，减少默认留白
st.markdown(
    """
<style>
footer { display: none !important; }
div.block-container { padding-top: 0.25rem !important; padding-bottom: 0.25rem !important; }
</style>
""",
    unsafe_allow_html=True,
)

# --- 登录系统会话状态 ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""

# --- 侧边栏：登录/注册 ---
with st.sidebar:
    if not st.session_state.logged_in:
        st.subheader("系统访问")
        tab1, tab2 = st.tabs(["登录", "注册"])
        
        with tab1:
            l_user = st.text_input("账号", key="l_user")
            l_pwd = st.text_input("密码", type="password", key="l_pwd")
            if st.button("进入系统"):
                conn = get_db_connection()
                res = conn.execute("SELECT role FROM users WHERE username=? AND password=?", (l_user, l_pwd)).fetchone()
                conn.close()
                if res:
                    st.session_state.logged_in = True
                    st.session_state.username = l_user
                    st.session_state.role = res[0]
                    st.rerun()
                else:
                    st.error("账号或密码错误")
        
        with tab2:
            r_user = st.text_input("新账号", key="r_user")
            r_pwd = st.text_input("设置密码", type="password", key="r_pwd")
            r_role = st.selectbox("角色", ["employee", "admin"])
            if st.button("提交注册"):
                try:
                    conn = get_db_connection()
                    conn.execute("INSERT INTO users VALUES (?, ?, ?)", (r_user, r_pwd, r_role))
                    conn.commit()
                    conn.close()
                    st.success("注册成功，请登录")
                except:
                    st.error("账号已存在")
    else:
        st.write(f"当前用户: **{st.session_state.username}**")
        st.write(f"权限角色: `{st.session_state.role}`")
        if st.button("退出登录"):
            st.session_state.logged_in = False
            st.rerun()

# --- 主界面逻辑 ---
if st.session_state.logged_in:
    conn = get_db_connection()
    
    if st.session_state.role == "admin":
        # 管理员：不显示“本人”，只看员工的一条实时状态 + 展开查看 CSV 三表（不下载）
        all_users = pd.read_sql("SELECT username FROM users WHERE role='employee'", conn)
        employees = all_users["username"].tolist()

        with st.sidebar:
            st.markdown("### 员工列表")
            if employees:
                selected_employee = st.radio("选择员工", employees, label_visibility="collapsed")
            else:
                selected_employee = None
                st.info("暂无员工账号（role=employee）")

            refresh_sec = st.selectbox("自动刷新（秒）", [0, 2, 5, 10], index=0)
            if st.button("手动刷新"):
                st.rerun()

        if selected_employee:
            res = conn.execute(
                "SELECT state_json, last_updated FROM user_data WHERE username=?",
                (selected_employee,),
            ).fetchone()
            state_json = res[0] if res else None
            last_updated = res[1] if res else None
            state = _load_state_from_db(state_json, user_display_name=selected_employee)

            status = _compute_admin_status(state)
            sync_hint = ""
            try:
                if last_updated:
                    # sqlite timestamp usually "YYYY-MM-DD HH:MM:SS.mmmmmm"
                    dt = datetime.fromisoformat(str(last_updated))
                    age = max(0, int((datetime.now() - dt).total_seconds()))
                    sync_hint = f"同步：{age}s前"
                else:
                    sync_hint = "同步：无记录"
            except Exception:
                sync_hint = f"同步：{last_updated}"
            st.markdown(
                f"""
<div style="position:sticky; top:0; z-index:999; background:#1e293b; color:white; padding:12px 16px; border-radius:10px; display:flex; justify-content:space-between; align-items:flex-start; gap:16px;">
  <div style="display:flex; flex-direction:column; gap:4px;">
    <div style="display:flex; align-items:center; gap:14px;">
      <span style="color:{status['clock_color']}; font-weight:800;">{status['clock_text']}</span>
      <span style="background:#374151; padding:4px 10px; border-radius:6px; font-size:14px;">{status['mode']}</span>
      <span style="opacity:0.85; font-size:14px;">{selected_employee}</span>
    </div>
    <div style="color:#cbd5e1; font-size:12px; line-height:1.2;">
      {status['giant']}
    </div>
    <div style="color:#94a3b8; font-size:11px; line-height:1.2;">
      {sync_hint}
    </div>
  </div>
  <div style="display:flex; align-items:center; gap:18px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;">
    <span>任务累计：<b style="color:#60a5fa;">{status['task']}</b></span>
    <span>今日会议：<b style="color:#60a5fa;">{status['meeting']}</b></span>
    <span>今日休息：<b style="color:#60a5fa;">{status['rest']}</b></span>
  </div>
</div>
""",
                unsafe_allow_html=True,
            )

            with st.expander(f"{selected_employee} - CSV 三表实时展示", expanded=True):
                tables = _build_admin_tables(state, selected_employee)
                st.markdown("#### 表格一：任务汇总")
                st.dataframe(tables["tasks"], use_container_width=True, hide_index=True)
                st.markdown("#### 表格二：工时统计")
                st.dataframe(tables["attendance"], use_container_width=True, hide_index=True)
                st.markdown("#### 表格三：全流水详细审计日志")
                st.dataframe(tables["logs"], use_container_width=True, hide_index=True)

            # 员工界面投射（只读，不显示开始/完成/打卡/会议/休息/新增/导出按钮）
            with st.expander(f"{selected_employee} - 员工界面预览（只读）", expanded=False):
                components.html(
                    get_html_content(
                        state_json,
                        is_admin=True,
                        user_display_name=selected_employee,
                        hide_export=True,
                        readonly=True,
                        enable_sync=False,
                    ),
                    height=950,
                    scrolling=True,
                )

            # 自动刷新（可选）：默认关闭，避免页面不断重跑导致“空白感”
            if refresh_sec and refresh_sec > 0:
                st.caption(f"自动刷新已开启：{int(refresh_sec)} 秒（建议用“手动刷新”更稳定）")

    else:
        # 员工功能
        res = conn.execute("SELECT state_json FROM user_data WHERE username=?", (st.session_state.username,)).fetchone()
        current_state = res[0] if res else None
        
        # 员工端追求“纯 HTML”：隐藏 header（注意不要在未登录/管理员时隐藏，否则手机端无法打开侧边栏）
        st.markdown(
            """
<style>
header { display: none !important; }
</style>
""",
            unsafe_allow_html=True,
        )

        # 主区域仅渲染 HTML（员工隐藏导出按钮）
        components.html(
            get_html_content(current_state, is_admin=False, user_display_name=st.session_state.username),
            height=950,
            scrolling=True,
        )

    conn.close()
else:
    # 主界面不展示 Streamlit 提示文案，保持纯 HTML 画面
    st.empty()

# --- 原始 HTML 字符串 ---
# 兼容：历史遗留的大段 HTML（已被文件顶部读取的 USER_ORIGINAL_HTML 替代）
USER_ORIGINAL_HTML_UNUSED = """
<!DOCTYPE html>
<!-- saved from url=(0097)https://stackblitzstartersbdkkpwkv-cfed-%2D8080-%2D365214aa.local-credentialless.webcontainer.io/ -->
<html lang="zh-CN"><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>工作流工作记录分析系统 - V46</title>
    <style>
        :root {
            --primary-color: #2563eb;
            --success-color: #10b981;
            --danger-color: #ef4444;
            --warning-color: #f59e0b;
            --info-color: #6366f1;
            --rest-color: #f97316;
            --header-bg: #f8fafc;
            --border-color: #e2e8f0;
        }

        body { font-family: 'Segoe UI', system-ui, sans-serif; margin: 0; padding: 0; background-color: #f1f5f9; color: #1e293b; }

        /* 巨幕状态栏 */
        #status-giant-banner {
            background-color: #0f172a;
            color: #f8fafc;
            padding: 20px 30px;
            text-align: center;
            border-bottom: 2px solid #334155;
            z-index: 1001;
        }
        #giant-status-text { font-size: 2.5rem; font-weight: 800; letter-spacing: 2px; }
        
        .blink-red { color: var(--danger-color) !important; animation: alert-blink 0.8s infinite; }
        @keyframes alert-blink { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }

        #current-status-bar {
            position: sticky; top: 0; width: 100%; background-color: #1e293b; color: white; 
            padding: 12px 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.3); z-index: 1000; 
            display: flex; justify-content: space-between; align-items: center; box-sizing: border-box;
        }

        .status-group { display: flex; align-items: center; gap: 20px; }
        .timer-badge { font-family: monospace; font-size: 1.4rem; color: #60a5fa; font-weight: bold; }
        .mode-tag { font-size: 14px; padding: 4px 10px; border-radius: 4px; background: #374151; }

        .container { padding: 20px; max-width: 100%; margin: 0 auto; }

        .header-section {
            display: flex; flex-direction: column; gap: 20px; background: white; 
            padding: 25px; border-radius: 10px; margin-bottom: 20px; 
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        
        .top-row { display: flex; justify-content: space-between; align-items: center; }
        .controls { display: flex; flex-wrap: wrap; gap: 12px; }
        
        .btn-large { padding: 14px 28px !important; font-size: 16px !important; border-radius: 8px !important; font-weight: bold; cursor: pointer; border: none; transition: 0.2s; color: white; }

        .add-task-row {
            display: flex; align-items: center; gap: 10px; padding: 15px;
            background: #f8fafc; border: 1px dashed var(--primary-color); border-radius: 6px;
        }
        .add-task-row input { padding: 10px 14px; border: 1px solid var(--border-color); border-radius: 4px; outline: none; }
        .input-name { flex: 2; }
        .input-est { flex: 0.5; }

        .user-box { display: flex; align-items: center; gap: 12px; font-weight: bold; font-size: 1.1rem; }
        .user-box input { padding: 10px; border: 1px solid var(--border-color); border-radius: 4px; width: 150px; outline: none; }

        table { width: 100%; border-collapse: collapse; background: white; border: 1px solid var(--border-color); table-layout: fixed; }
        th { background-color: var(--header-bg); padding: 14px 10px; border: 1px solid var(--border-color); text-align: left; font-size: 13px; color: #64748b; }
        td { border: 1px solid var(--border-color); vertical-align: top; }
        tr.is-completed { background-color: #f9fafb; opacity: 0.7; }

        .edit-cell { width: 100%; height: 100%; min-height: 55px; padding: 12px 10px; box-sizing: border-box; border: none; outline: none; font-size: 13.5px; white-space: pre-wrap; word-wrap: break-word; position: relative; }
        .edit-cell[contenteditable="true"]:focus { background-color: #fff; box-shadow: inset 0 0 0 2px var(--primary-color); }
        
        .dev-cell:empty::before { content: "填写 commit 记录"; color: #94a3b8; font-style: italic; pointer-events: none; }
        .locked-cell { background-color: #f1f5f9; color: #64748b; cursor: not-allowed; }

        .delivery-text { font-size: 11px; font-weight: bold; color: var(--primary-color); text-align: center; padding: 12px 0; }
        .completed-text { font-size: 11px; font-weight: bold; color: var(--success-color); text-align: center; padding: 12px 0; }

        .time-col { padding: 10px; text-align: center; }
        .time-val { font-family: monospace; font-weight: bold; font-size: 14px; }

        .progress-container { width: 100%; height: 6px; background: #e2e8f0; border-radius: 3px; margin-top: 5px; overflow: hidden; }
        .progress-bar { height: 100%; width: 0%; transition: width 0.3s; background: var(--success-color); }

        .solution-item { font-size: 11px; background: #f8fafc; padding: 10px; border-radius: 4px; border-left: 3px solid #cbd5e1; margin-bottom: 8px; position: relative;}
        .solu-title { display: flex; justify-content: space-between; font-weight: bold; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; margin-bottom: 6px; }
        .solu-note-area { min-height: 40px; outline: none; color: #334155; font-size: 11px; line-height: 1.4; white-space: pre-wrap; cursor: text; }
        .solu-note-area:empty::before { content: "⚠️ 必填记录..."; color: #f87171; font-style: italic; }

        .btn { padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 600; color: white; transition: 0.2s; }
        .btn-confirm { background-color: var(--primary-color); }
        .btn-clock { background-color: var(--info-color); }
        .btn-clock.out { background-color: #4b5563; }
        .btn-meeting { background-color: #8b5cf6; }
        .btn-rest { background-color: var(--rest-color); }
        .active-btn { background-color: var(--danger-color) !important; }
        .btn-start { background-color: #e2e8f0; color: #1e293b; width: 100%; border: 1px solid #cbd5e1; }
        .btn-start.active { background-color: var(--danger-color); color: white; border-color: transparent; }

        /* 列宽分配 */
        .w-drag { width: 35px; } .w-created { width: 90px; } .w-name { width: 12%; }
        .w-est { width: 50px; } .w-delivery { width: 100px; } .w-spent { width: 11%; }
        .w-solu { width: 33%; } .w-dev { width: 12%; } .w-rem { width: 8%; } .w-op { width: 85px; }
    </style>
</head>
<body>

<div id="status-giant-banner">
    <div id="giant-status-text">待机中</div>
</div>

<div id="current-status-bar">
    <div class="status-group">
        <span id="clock-status" style="color: rgb(148, 163, 184);">● 未打卡</span>
        <span id="mode-display" class="mode-tag">待机</span>
    </div>
    <div class="status-group">
        <span>任务累计：<span class="timer-badge" id="banner-task-timer">00:00:00</span></span>
        <span>今日会议：<span class="timer-badge" id="banner-meeting-timer">00:00:00</span></span>
        <span>今日休息：<span class="timer-badge" id="banner-rest-timer">00:00:00</span></span>
    </div>
</div>

<div class="container">
    <div class="header-section">
        <div class="top-row">
            <div class="user-box">
                负责人：<input type="text" id="userNameInput" placeholder="姓名" onblur="saveState()">
            </div>
            <div class="controls">
                <button id="btnClock" class="btn btn-clock out btn-large" onclick="toggleClock()">上班打卡</button>
                <button id="btnMeeting" class="btn btn-meeting btn-large " onclick="toggleMeeting()">开始会议</button>
                <button id="btnRest" class="btn btn-rest btn-large " onclick="toggleRest()">开始休息</button>
                <button class="btn btn-large" style="background:#10b981" onclick="exportToCSV()">三表导出记录</button>
            </div>
        </div>

        <div class="add-task-row">
            <strong>🚀 新增：</strong>
            <input type="text" id="newTaskName" class="input-name" placeholder="名称">
            <input type="number" id="newTaskEst" class="input-est" placeholder="预计(h)">
            <button class="btn btn-confirm" onclick="confirmAddTask()">确认添加</button>
        </div>
    </div>

    <table>
        <thead>
            <tr>
                <th class="w-drag"></th>
                <th class="w-created">制定日期</th>
                <th class="w-name">任务名称</th>
                <th class="w-est">工时</th>
                <th class="w-delivery">排期 / 交付</th>
                <th class="w-spent">实际用时</th>
                <th class="w-solu">方案演进与调研记录 (必填)</th>
                <th class="w-dev">交付物 (必填)</th>
                <th class="w-rem">备注</th>
                <th class="w-op">操作</th>
            </tr>
        </thead>
        <tbody id="taskBody">
            <!-- 任务行将由 JS 渲染 -->
        </tbody>
    </table>
</div>

<script>
    let state = JSON.parse(localStorage.getItem('perf_v46_state')) || {
        tasks: [], attendance: [], activeTaskId: null,
        isClockedIn: false, isMeeting: false, isResting: false,
        meetingSeconds: 0, restSeconds: 0,
        meetingHistory: [], restHistory: [],
        clockInTime: null, clockInFullMs: null, userName: ""
    };

    let dragSourceIndex = null;
    window.onload = () => {
        document.getElementById('userNameInput').value = state.userName || "";
        renderTable(); updateUIStatus(); startTicker();
    };

    function pad(n) { return n.toString().padStart(2, '0'); }
    function formatTime(s) { s = Math.max(0, Math.floor(s)); return `${pad(Math.floor(s/3600))}:${pad(Math.floor((s%3600)/60))}:${pad(s%60)}`; }
    function formatTimeCSV(s) { s = Math.max(0, Math.floor(s)); return `${pad(Math.floor(s/3600))}:${pad(Math.floor((s%3600)/60))}`; }
    function getFullTimestamp() { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; }
    
    function getFullDateTimeStr(timeOnlyStr) {
        const d = new Date();
        const datePart = `${d.getFullYear()}/${pad(d.getMonth()+1)}/${pad(d.getDate())}`;
        if (!timeOnlyStr || timeOnlyStr.includes("进行中") || timeOnlyStr.includes("未下班") || timeOnlyStr.includes("--")) return timeOnlyStr || datePart;
        const hm = timeOnlyStr.split(':').slice(0,2).join(':');
        return `${datePart} ${hm}`;
    }

    function getNowStr() { const d = new Date(); return `${pad(d.getMonth()+1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; }

    function saveState() { state.userName = document.getElementById('userNameInput').value; localStorage.setItem('perf_v46_state', JSON.stringify(state)); }
    function saveAndRender() { saveState(); renderTable(); }

    function stopCurrentTaskTimer() {
        if (!state.activeTaskId) return;
        const task = state.tasks.find(t => t.id == state.activeTaskId);
        if (task && task.lastStartTimestamp) {
            const now = Date.now();
            const elapsed = (now - task.lastStartTimestamp) / 1000;
            task.spentSeconds += elapsed;
            const lastSolu = task.solutions[task.solutions.length - 1];
            if (lastSolu) {
                lastSolu.seconds += elapsed;
                const curH = lastSolu.history[lastSolu.history.length - 1];
                if (curH && !curH.end) { curH.end = getFullTimestamp(); curH.duration = elapsed; }
            }
            task.lastStartTimestamp = null;
        }
        saveState();
    }

    function startTaskTimer(id) {
        const task = state.tasks.find(t => t.id == id);
        task.lastStartTimestamp = Date.now();
        task.solutions[task.solutions.length - 1].history.push({ start: getFullTimestamp(), end: null, startMs: Date.now() });
        state.activeTaskId = id;
        saveState();
    }

    function toggleClock() {
        const now = new Date(); const ts = getFullTimestamp();
        if (state.isClockedIn) {
            if(!confirm("确定下班打卡？系统将同步导出全天记录。")) return;
            stopCurrentTaskTimer();
            if(state.isMeeting) endMeeting(); if(state.isResting) endRest();
            const totalTaskSec = state.tasks.reduce((sum, t) => sum + t.spentSeconds, 0);
            const totalClockedSec = (Date.now() - state.clockInFullMs) / 1000;
            state.attendance.push({ date: now.toLocaleDateString(), clockIn: state.clockInTime, clockOut: ts, clockInFullMs: state.clockInFullMs, clockOutFullMs: Date.now(), taskTotal: totalTaskSec, meeting: state.meetingSeconds, rest: state.restSeconds, totalClocked: totalClockedSec });
            
            state.activeTaskId = null; state.isClockedIn = false;
            
            // 下班归零显示
            document.getElementById('banner-task-timer').innerText = "00:00:00";
            document.getElementById('banner-meeting-timer').innerText = "00:00:00";
            document.getElementById('banner-rest-timer').innerText = "00:00:00";

            saveAndRender(); updateUIStatus();
            exportToCSV("下班记录");
        } else {
            state.isClockedIn = true; state.clockInTime = ts; state.clockInFullMs = Date.now();
            state.meetingSeconds = 0; state.restSeconds = 0; state.meetingHistory = []; state.restHistory = [];
            saveAndRender(); updateUIStatus();
        }
    }

    function toggleMeeting() {
        if (!state.isClockedIn) return alert("请先上班打卡");
        if (!state.isMeeting) { stopCurrentTaskTimer(); state.activeTaskId = null; if (state.isResting) endRest(); state.isMeeting = true; state.lastMeetingTimestamp = Date.now(); state.meetingHistory.push({ start: getFullTimestamp(), end: null, startMs: Date.now() }); } 
        else { endMeeting(); }
        saveAndRender(); updateUIStatus();
    }
    function endMeeting() { if (state.lastMeetingTimestamp) { const dur = (Date.now()-state.lastMeetingTimestamp)/1000; state.meetingSeconds += dur; state.meetingHistory[state.meetingHistory.length-1].end = getFullTimestamp(); state.meetingHistory[state.meetingHistory.length-1].duration = dur; state.lastMeetingTimestamp = null; } state.isMeeting = false; }

    function toggleRest() {
        if (!state.isClockedIn) return alert("请先上班打卡");
        if (!state.isResting) { stopCurrentTaskTimer(); state.activeTaskId = null; if (state.isMeeting) endMeeting(); state.isResting = true; state.lastRestTimestamp = Date.now(); state.restHistory.push({ start: getFullTimestamp(), end: null, startMs: Date.now() }); } 
        else { endRest(); }
        saveAndRender(); updateUIStatus();
    }
    function endRest() { if (state.lastRestTimestamp) { const dur = (Date.now()-state.lastRestTimestamp)/1000; state.restSeconds += dur; state.restHistory[state.restHistory.length-1].end = getFullTimestamp(); state.restHistory[state.restHistory.length-1].duration = dur; state.lastRestTimestamp = null; } state.isResting = false; }

    function toggleTask(id) {
        if (!state.isClockedIn) return alert("请先打卡");
        if (state.isMeeting) endMeeting(); if (state.isResting) endRest();
        if (state.activeTaskId == id) { stopCurrentTaskTimer(); state.activeTaskId = null; } 
        else { if (state.activeTaskId) stopCurrentTaskTimer(); startTaskTimer(id); }
        saveAndRender(); updateUIStatus();
    }

    function addSolu(id) { 
        const t = state.tasks.find(x => x.id == id);
        const lastSolu = t.solutions[t.solutions.length - 1];
        if (!lastSolu.researchNote || lastSolu.researchNote.trim() === "") {
            return alert("⚠️ 请先补全【当前方案/阶段】的调研记录，再开启新阶段记录。");
        }
        const isRunning = (state.activeTaskId == id);
        if (isRunning) stopCurrentTaskTimer();
        t.solutions.push({ text: `新阶段${t.solutions.length + 1}`, seconds: 0, history: [], researchNote: "" });
        if (isRunning) startTaskTimer(id);
        saveAndRender();
    }

    function completeTask(id) {
        const idx = state.tasks.findIndex(x => x.id == id);
        const t = state.tasks[idx];
        if (!t.dev || t.dev.trim() === "") return alert("⚠️ 请填写交付证明。");
        const lastSolu = t.solutions[t.solutions.length - 1];
        if (!lastSolu.researchNote || lastSolu.researchNote.trim() === "") return alert("⚠️ 请先填写当前阶段的详细调研记录。");
        
        stopCurrentTaskTimer(); if (state.activeTaskId == id) state.activeTaskId = null;
        t.completed = true; t.completedAt = getNowStr();
        const estH = parseFloat(t.estTime); const diff = t.spentSeconds - estH * 3600;
        t.deviationLabel = diff > 0 ? `延时${formatTime(diff)}` : `提前${formatTime(Math.abs(diff))}`;
        t.deviationClass = diff > 0 ? "info-delayed" : "info-early";

        // 核心修改：完成的任务移到底部
        state.tasks.splice(idx, 1);
        state.tasks.push(t);

        saveAndRender(); updateUIStatus();
    }

    function reopen(id) { 
        const idx = state.tasks.findIndex(x => x.id == id);
        const t = state.tasks[idx];
        t.completed = false; t.completedAt = null; 
        
        // 核心修改：重开的任务移回顶部
        state.tasks.splice(idx, 1);
        state.tasks.unshift(t);
        
        saveAndRender(); 
    }

    function exportToCSV(customSuffix = "") {
        const name = state.userName || "未姓名";
        const d = new Date();
        const dateStamp = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
        const timeStamp = `${pad(d.getHours())}${pad(d.getMinutes())}`;
        
        let csv = "\uFEFF表格一：任务汇总\n负责人,状态,制定日期,任务名称,预计(h),完成日期,总耗时,产出证明\n";
        state.tasks.forEach(t => csv += `"${name}","${t.completed?'已完成':'进行中'}","${t.createdAt?getFullDateTimeStr(t.createdAt.split(' ')[1]):'--'}","${t.name}","${t.estTime}","${t.completedAt?getFullDateTimeStr(t.completedAt.split(' ')[1]):'--'}","${formatTimeCSV(t.spentSeconds)}","${t.dev.replace(/"/g,'""')}"\n`);

        csv += "\n表格二：工时统计\n日期,上班打卡,下班打卡,任务总长,会议总长,休息总长,其他碎型\n";
        state.attendance.forEach(a => {
            const otherSec = a.totalClocked - a.taskTotal - a.meeting - a.rest;
            csv += `"${a.date}","${getFullDateTimeStr(a.clockIn)}","${getFullDateTimeStr(a.clockOut)}","${formatTimeCSV(a.taskTotal)}","${formatTimeCSV(a.meeting)}","${formatTimeCSV(a.rest)}","${formatTimeCSV(otherSec)}"\n`;
        });

        csv += "\n表格三：全流水详细审计日志\n动作名称,关联内容,开始时间,结束时间,时长,详细记录\n";
        let logs = [];
        logs.push({ ms: state.clockInFullMs || 0, act: "【上班打卡】", obj: "--", s: state.clockInTime, e: "--", dur: 0, note: "" });
        state.tasks.forEach(t => t.solutions.forEach(s => s.history.forEach(h => {
            let dur = h.duration || (state.activeTaskId == t.id && !h.end ? (Date.now()-h.startMs)/1000 : 0);
            logs.push({ ms: h.startMs, act: "任务执行", obj: `${t.name}-${s.text}`, s: h.start, e: h.end || "进行中", dur: dur, note: s.researchNote });
        })));
        state.meetingHistory.forEach(h => logs.push({ ms: h.startMs, act: "会议沟通", obj: "内部沟通", s: h.start, e: h.end || "进行中", dur: h.duration || (state.isMeeting ? (Date.now()-h.startMs)/1000 : 0), note: "" }));
        state.restHistory.forEach(h => logs.push({ ms: h.startMs, act: "休息午休", obj: "--", s: h.start, e: h.end || "进行中", dur: h.duration || (state.isResting ? (Date.now()-h.startMs)/1000 : 0), note: "" }));
        if (!state.isClockedIn && state.attendance.length > 0) {
            const lastAtt = state.attendance[state.attendance.length - 1];
            logs.push({ ms: lastAtt.clockOutFullMs, act: "【下班打卡】", obj: "--", s: lastAtt.clockOut, e: "--", dur: 0, note: "" });
        }
        logs.sort((a,b) => a.ms - b.ms).forEach(l => {
            csv += `"${l.act}","${l.obj}","${getFullDateTimeStr(l.s)}","${(l.e==='进行中'||l.e==='--')?l.e:getFullDateTimeStr(l.e)}","${l.dur>0?formatTimeCSV(l.dur):'--'}","${l.note.replace(/"/g,'""')}"\n`;
        });

        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        const finalSuffix = customSuffix ? `_${customSuffix}` : "";
        a.download = `${name}_详细分析报告${finalSuffix}_${dateStamp}_${timeStamp}.csv`;
        a.click();
    }

    function startTicker() {
        setInterval(() => {
            if (!state.isClockedIn) return;
            if (state.activeTaskId && !state.isMeeting && !state.isResting) {
                const t = state.tasks.find(x => x.id == state.activeTaskId);
                if (t && t.lastStartTimestamp) {
                    const elapsed = (Date.now() - t.lastStartTimestamp) / 1000;
                    const liveT = t.spentSeconds + elapsed;
                    document.getElementById('banner-task-timer').innerText = formatTime(liveT);
                    const tCell = document.getElementById(`total-time-${t.id}`); if (tCell) tCell.innerText = formatTime(liveT);
                    const activeIdx = t.solutions.length - 1;
                    const sCell = document.getElementById(`solu-dur-${t.id}-${activeIdx}`); if (sCell) sCell.innerText = formatTime(t.solutions[activeIdx].seconds + elapsed);
                }
            }
            if (state.isMeeting) document.getElementById('banner-meeting-timer').innerText = formatTime(state.meetingSeconds + (Date.now() - state.lastMeetingTimestamp) / 1000);
            if (state.isResting) document.getElementById('banner-rest-timer').innerText = formatTime(state.restSeconds + (Date.now() - state.lastRestTimestamp) / 1000);
        }, 1000);
    }

    function renderTable() {
        const tbody = document.getElementById('taskBody'); tbody.innerHTML = ''; let cum = 0;
        state.tasks.forEach((t, i) => {
            const act = state.activeTaskId == t.id; const tr = document.createElement('tr');
            if (t.completed) tr.className = 'is-completed';
            cum += t.completed ? 0 : parseFloat(t.estTime || 0);
            tr.innerHTML = `
                <td class="drag-handle" draggable="true" ondragstart="dragSourceIndex=${i}" ondragover="event.preventDefault()" ondrop="handleDrop(${i})">${t.completed?'✅':'⠿'}</td>
                <td style="font-size:11px;text-align:center">${t.createdAt}</td>
                <td class="locked-cell"><div class="edit-cell">${t.name}</div></td>
                <td class="locked-cell" style="text-align:center">${t.estTime}</td>
                <td>${t.completed?`<div class="completed-text">完成:${t.completedAt}</div>`:`<div class="delivery-text">预计:${getSmartDeliveryDate(cum)}</div>`}</td>
                <td class="time-col"><div class="time-val" id="total-time-${t.id}">${formatTime(t.spentSeconds)}</div></td>
                <td>
                    <div class="solution-container">
                        ${t.solutions.map((s, si) => `<div class="solution-item"><div class="solu-title"><span contenteditable="true" onblur="updateSoluTitle(${t.id}, ${si}, this.innerText)">${s.text}</span><span id="solu-dur-${t.id}-${si}" style="color:var(--primary-color)">${formatTime(s.seconds)}</span></div><div class="solu-note-area" contenteditable="true" onblur="updateSoluNote(${t.id}, ${si}, this.innerText)">${s.researchNote || ''}</div></div>`).join('')}
                        ${!t.completed ? `<button onclick="addSolu(${t.id})" style="font-size:9px; width:100%; padding:5px;">+ 方案调整/新阶段记录</button>` : ''}
                    </div>
                </td>
                <td><div class="edit-cell dev-cell" contenteditable="true" onblur="updateField(${t.id}, 'dev', this.innerText)">${t.dev||''}</div></td>
                <td><div class="edit-cell" contenteditable="true" onblur="updateField(${t.id}, 'rem', this.innerText)">${t.rem||''}</div></td>
                <td style="text-align:center">${!t.completed ? `<button class="btn-start ${act?'active':''}" onclick="toggleTask(${t.id})">${act?'停止':'开始'}</button><button class="btn-confirm" style="margin-top:5px;width:100%;font-size:10px" onclick="completeTask(${t.id})">完成</button>` : `<button class="btn" style="background:#64748b" onclick="reopen(${t.id})">重开</button>`}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    function updateSoluNote(taskId, soluIdx, val) { const t = state.tasks.find(x => x.id == taskId); if (t && t.solutions[soluIdx]) { t.solutions[soluIdx].researchNote = val.trim(); saveState(); } }
    function updateSoluTitle(tid, si, val) { const t = state.tasks.find(x => x.id == tid); if(t && t.solutions[si]) { t.solutions[si].text = val.trim(); saveState(); } }
    function getSmartDeliveryDate(hours) { let d = new Date(); let hLeft = hours; const norm = (date) => { if (date.getHours() >= 18) { date.setDate(date.getDate() + 1); date.setHours(9,0,0,0); } if (date.getHours() < 9) date.setHours(9,0,0,0); while(date.getDay()===0 || date.getDay()===6) { date.setDate(date.getDate()+1); date.setHours(9,0,0,0); } }; norm(d); while (hLeft > 0) { let avail = 18 - d.getHours(); if (avail >= hLeft) { d.setMinutes(d.getMinutes() + hLeft * 60); hLeft = 0; } else { hLeft -= avail; d.setDate(d.getDate() + 1); d.setHours(9,0,0,0); norm(d); } } return `${pad(d.getMonth()+1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; }
    function updateUIStatus() { const cs = document.getElementById('clock-status'); const mode = document.getElementById('mode-display'); const giant = document.getElementById('giant-status-text'); cs.innerText = state.isClockedIn ? "● 已上班" : "● 未打卡"; cs.style.color = state.isClockedIn ? "#10b981" : "#94a3b8"; document.getElementById('btnClock').innerText = state.isClockedIn ? "下班打卡" : "上班打卡"; document.getElementById('btnMeeting').innerText = state.isMeeting ? "结束会议" : "开始会议"; document.getElementById('btnRest').innerText = state.isResting ? "结束休息" : "开始休息"; document.getElementById('btnMeeting').className = `btn btn-meeting btn-large ${state.isMeeting?'active-btn':''}`; document.getElementById('btnRest').className = `btn btn-rest btn-large ${state.isResting?'active-btn':''}`; giant.classList.remove('blink-red'); if (!state.isClockedIn) { giant.innerText = "待机中"; mode.innerText = "待机"; } else if (state.isMeeting) { giant.innerText = "会议进行中..."; mode.innerText = "会议中"; } else if (state.isResting) { giant.innerText = "休息中..."; mode.innerText = "休息中"; } else if (state.activeTaskId) { const t = state.tasks.find(x => x.id == state.activeTaskId); giant.innerText = "正在执行：" + (t ? t.name : "任务"); mode.innerText = "工作中"; } else { giant.innerText = "任务：无 (请开启记录！)"; giant.classList.add('blink-red'); mode.innerText = "空闲"; } }
    function confirmAddTask() { const n = document.getElementById('newTaskName'); const e = document.getElementById('newTaskEst'); if (!n.value || !e.value) return alert("请填写完整"); state.tasks.unshift({ id: Date.now(), createdAt: getNowStr(), completedAt: null, name: n.value, estTime: e.value, spentSeconds: 0, lastStartTimestamp: null, solutions: [{text: '初始阶段', seconds: 0, history: [], researchNote: ""}], dev: '', rem: '', completed: false, deviationLabel: "", deviationClass: "" }); n.value = ''; e.value = ''; saveAndRender(); }
    function updateField(id, f, v) { const t = state.tasks.find(x => x.id == id); if(t){ t[f] = v.trim(); saveState(); } }
    function handleDrop(targetIdx) { const item = state.tasks.splice(dragSourceIndex, 1)[0]; state.tasks.splice(targetIdx, 0, item); saveAndRender(); }
</script>
</body>
</html>
"""
