# 🌐 HyperTask Hub (开源增强版)

**HyperTask Hub** 是一套面向未来的多智能体（Multi-Agent）中枢编排系统。它作为整个 AI 工作流的“中枢神经系统”，通过统一的控制台和 API 接口，对底层的多类 AI Agent（如 OpenClaw、Gemini Supervisor Bot、DeepSeek）进行统一的任务分发、状态监听和技能调度。

![HyperTask Hub Dashboard](images/banner.png)

---

## ✨ 核心特性

- **🤖 多 Agent 协同与编排**：统一管理不同特性的 Agent。
- **📦 技能动态挂载**：自动实时监听并解析本地 `~/.gemini/skills/` 目录中的 `SKILL.md`，支持 UI 输入框斜杠 `/` 命令实时联想。
- **⚡ 实时双向通讯**：基于 FastAPI WebSocket 构建的 `/ws/nexus` 管道，将 Agent 的状态更新、终端日志输出实时广播至前端 Dashboard。
- **🎙️ 沉浸式语音播报**：内置 DOTA2 英雄语音包（冰女、火女等），在任务执行的关键节点提供沉浸式反馈（纯音频，已剔除原生机械 TTS）。
- **🔄 进程级守护**：完全基于 PM2 生态进行多服务进程管理。

---

## 🚀 部署与运行

项目依赖 `Python 3.10+` 以及 `Node.js (PM2)` 环境。

### 1. 环境变量配置
复制环境模板并填入您的私有 Key：
```bash
cp .env.example .env
```
在 `.env` 中填入：
- `DEEPSEEK_API_KEY`: 您的 DeepSeek API Token（或本地 NAS 的 Token）
- `TELEGRAM_BOT_TOKEN`: 您的 TG 机器人 Token (如果使用 Supervisor Bot)

### 2. 环境初始化
```bash
# 创建并激活 Python 虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装所有后端依赖
pip install -r requirements.txt
```

### 3. 一键启动
本系统由 `ecosystem.config.js` 统一定义，包含核心的 FastAPI 路由以及多个桥接 Agent。

```bash
# 安装 PM2 (如未安装)
npm install -g pm2

# 启动整个集群
pm2 start ecosystem.config.js

# 查看所有微服务在线状态
pm2 status
```

访问 `http://localhost:8000` 即可进入控制台。

---

## 🧩 扩展您的技能库 (Skills)

HyperTask Hub 采用“即插即用”的架构：
1. 在宿主机的 `~/.gemini/skills/` 下新建一个目录（如 `my-skill`）。
2. 在该目录下创建 `SKILL.md`：
   ```yaml
   ---
   name: my-skill
   description: 一句话描述这个技能的作用。
   ---
   ```
3. **无需重启服务**，在 Hub 界面底部的 CMD 输入框内打字即可立刻调用这个新技能！

---

## 🤖 包含的子特工 (Sub-Agents)

- **`hypertask-hub`**: 核心前端与路由分发器 (Port 8000)。
- **`openclaw-bridge`**: 本地高权限实干家，通过 CLI 与 OpenClaw 通信。
- **`deepseek-agent`**: 深度推理引擎接口，专注解决复杂架构问题。
- **`supervisor-agent`**: 内部调度主管，负责异常熔断与重试。
- **`gemini-supervisor-bot`** (附加): 远端 Telegram 监控机器人，用于移动端收取进度报告。

---
*Open-sourced under MIT License.*