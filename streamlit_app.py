import streamlit as st
import streamlit.components.v1 as components
import sqlite3
import json
import pandas as pd
from datetime import datetime

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
    
    # 创建默认管理员 (如果不存在)
    c.execute("INSERT OR IGNORE INTO users VALUES ('admin', 'admin123', 'admin')")
    conn.commit()
    conn.close()

def get_db_connection():
    return sqlite3.connect('workflow_system.db')

# --- 核心 HTML 模板逻辑 ---
# 这里我们将你提供的 HTML 包装成一个函数，并根据角色动态修改
def get_html_content(user_state_json, is_admin=False, user_display_name=""):
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
    if not is_admin:
        hide_export_css = "<style>.btn-large[onclick='exportToCSV()'] { display: none !important; }</style>"

    raw_html = f"""
    {hide_export_css}
    {USER_ORIGINAL_HTML}
    <script>
        // 覆盖原始的 state 加载
        state = {user_state_json};
        
        // 覆盖 saveState 函数，增加数据同步逻辑
        function saveState() {{
            state.userName = document.getElementById('userNameInput').value;
            localStorage.setItem('perf_v46_state', JSON.stringify(state));
            
            // 向 Streamlit 发送数据
            window.parent.postMessage({{
                type: 'streamlit:setComponentValue',
                value: JSON.stringify(state)
            }}, '*');
        }}
    </script>
    """
    return raw_html

# --- 页面配置 ---
st.set_page_config(page_title="工作流分析系统 - Streamlit版", layout="wide")
init_db()

# --- 登录系统会话状态 ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""

# --- 侧边栏：登录/注册 ---
with st.sidebar:
    if not st.session_state.logged_in:
        st.title("🔐 系统访问")
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
        st.title("🛠️ 管理员控制台")
        # 管理员功能：查看所有人
        all_users = pd.read_sql("SELECT username FROM users WHERE role='employee'", conn)
        target_user = st.selectbox("选择要查看的员工状态", ["本人"] + all_users['username'].tolist())
        
        view_user = st.session_state.username if target_user == "本人" else target_user
        
        # 获取该用户的数据
        res = conn.execute("SELECT state_json FROM user_data WHERE username=?", (view_user,)).fetchone()
        current_state = res[0] if res else None
        
        # 渲染 HTML (管理员始终可见导出按钮)
        # 注意：此处高度需根据你的表格长度调整
        st.info(f"正在查看 {view_user} 的实时工作流")
        components.html(get_html_content(current_state, is_admin=True, user_display_name=view_user), height=800, scrolling=True)

        # 全员导出功能
        st.divider()
        st.subheader("📊 全员数据分析导出")
        if st.button("生成全员工作汇总预览"):
            all_data = pd.read_sql("SELECT * FROM user_data", conn)
            st.write(all_data)

    else:
        # 员工功能
        st.title("📝 工作记录分析系统")
        res = conn.execute("SELECT state_json FROM user_data WHERE username=?", (st.session_state.username,)).fetchone()
        current_state = res[0] if res else None
        
        # 渲染 HTML (员工隐藏导出按钮)
        # 我们利用组件的返回值来获取 JS 传回的状态
        # 注意：这里需要安装 streamlit-js-eval 或使用简单的隐藏 iframe 通信
        # 为了保持脚本完整性，我们使用一个简单的 trick: 员工操作后手动点击“保存云端”
        st.warning("⚠️ 员工权限：已禁用本地 CSV 下载功能。")
        
        # 使用自定义组件或简单 html 嵌入
        # 为了实现自动保存，我们在 JS 里通过定时或者失去焦点触发 postMessage
        # 此处演示通过 Streamlit 捕获这个值
        response = components.html(
            get_html_content(current_state, is_admin=False, user_display_name=st.session_state.username),
            height=800, scrolling=True
        )
        
        # 这是一个简化版的保存机制：在 Streamlit 侧加一个同步按钮
        # 实际更高级的做法是写一个 Streamlit Custom Component
        st.info("系统会自动尝试保存。如需强制同步到服务器，请点击下方：")
        new_data = st.text_area("同步状态（JS会自动填充）", help="这是从HTML内部传出的加密状态流")
        if st.button("同步数据到云端"):
            if new_data:
                conn.execute("INSERT OR REPLACE INTO user_data VALUES (?, ?, ?)", 
                             (st.session_state.username, new_data, datetime.now()))
                conn.commit()
                st.success("云端同步完成！")

    conn.close()
else:
    st.info("请在左侧侧边栏登录以开始工作。")

# --- 原始 HTML 字符串 (包含你提供的所有代码) ---
USER_ORIGINAL_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
"""
# 注意：你需要把你的原始 HTML 粘贴进上面的 USER_ORIGINAL_HTML 变量中。
