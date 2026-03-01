#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "Installing requirements..."
pip install -r requirements.txt

# 捕获退出信号 (Ctrl+C)，统一杀掉后台所有的 Agent 子进程
trap 'echo -e "\n🛑 正在一键关停所有的引擎与主服务..."; kill 0' SIGINT SIGTERM EXIT

# 注册外部开发计划目录 (逗号分隔)
# 将 OpenClaw 或其他项目的路径加入此处，Hub 就能通过 API 读取它们的计划文件
# 例如: export PLAN_DIRS="/path/to/openclaw/docs,/path/to/other/project"
export PLAN_DIRS=""

echo "=========================================="
echo "⚡️ 正在一键启动 HyperTask Hub 及所有特工 ⚡️"
echo "=========================================="

# 1. 后台拉起所有的 Agent (子特工)
echo "🔗 接入 OpenClaw 本地日志桥接服务..."
python backend/openclaw_bridge.py &

echo "🧠 接入飞牛 NAS DeepSeek (R1) 深度推理节点..."
python backend/deepseek_agent.py &

echo "🤖 接入本地 NotebookLM (Gemini) 助手节点..."
python backend/gemini_bot.py &

# 稍微等待 1 秒缓冲，避免日志混在一起
sleep 1

# 2. 前台启动任务中心主服务，阻塞在这里
echo "=========================================="
echo "🎯 超级任务中心主控台已就绪: http://localhost:8000"
echo "按 Ctrl+C 可一键停止全部"
echo "=========================================="
uvicorn backend.main:app --host 0.0.0.0 --port 8000
