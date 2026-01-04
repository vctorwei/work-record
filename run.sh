#!/bin/bash
set -e

# 启动同步服务（用于员工端把 state 写回 SQLite，管理员端读取即实时）
# 注意：用 0.0.0.0 才能让手机/另一台电脑通过 Network/External URL 访问到同步服务
python3 sync_server.py --host 0.0.0.0 --port 8502 --db workflow_system.db &
SYNC_PID=$!
trap "kill $SYNC_PID 2>/dev/null || true" EXIT

./venv/bin/streamlit run streamlit_app.py --server.headless=true --server.port=8501

