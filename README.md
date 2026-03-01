# 🌐 HyperTask Hub: 多智能体协同编排中枢 (Open Source Edition)

> **"让 Agent 们像交响乐团一样协同工作，拒绝‘磨洋工’，实现真正的自动化闭环。"**

**HyperTask Hub** 是一套面向未来的多智能体 (Multi-Agent) 编排与指挥系统。它通过一个精致的 Web 控制台，将不同特性的 AI 特工（Gemini, DeepSeek, OpenClaw）缝合成一个具备自我修正能力的“智体机群”。

<!-- 16:9 主界面效果图占位 -->
<div align="center">
  <img src="images/banner_16_9.png" width="100%" alt="HyperTask Hub Main Dashboard" />
  <p><em>(此处建议上传 16:9 的主界面高清截图)</em></p>
</div>

---

## ✨ 核心亮点

### 1. 🤖 强力执行者：Gemini Bot
**Gemini Bot** 是系统中的核心打工人。它不仅拥有极高的逻辑推理能力，更被赋予了**本地系统执行权**。它能通过 CLI 直接操控您的文件系统、运行复杂脚本，并将执行过程实时回传给指挥中心。

### 2. 👮 督察官：Supervisor Agent (反“磨洋工”系统)
这是本系统的“灵魂”。主管 Agent 会在后台实时监控所有特工的心跳与任务进度：
- **防止磨洋工**：如果某个特工在任务执行中停滞过久，主管会立刻下达“催办 (Nudge)”指令。
- **智能排障**：当检测到特工因报错或环境问题卡壳时，主管会介入分析，并为其提供具体的**解决方案或绕过路径**，甚至可以调动其他特工协助排障。

### 3. 🎙️ 沉浸式语音反馈
内置 **DOTA2 英雄语音包**。任务的每一个关键节点（上线、运行中、成功、停滞、失败）都由冰女、火女等英雄实时播报，让自动化过程充满实感。

### 4. 🕹️ 人工介入与一键即停
- **上帝视角**：允许人类指挥官随时在 CMD 框输入指令，中断或接管任意特工的当前任务。
- **一键即停 (Emergency Stop)**：遇到紧急情况或模型幻觉，可立即熔断所有正在运行的进程，确保系统安全。

---

## 📸 移动端适配
<!-- 9:16 移动端/电报端效果图占位 -->
<div align="center">
  <img src="images/mobile_9_16.png" width="40%" alt="Telegram Monitor View" />
  <p><em>(此处建议上传 9:16 的 Telegram 机器人或手机浏览器截图)</em></p>
</div>

---

## 🚀 快速开始

1. **克隆与环境初始化**
   ```bash
   git clone https://github.com/aierlanjiu/HYPER-TASK-HUB.git
   cd HYPER-TASK-HUB
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **配置配置项**
   复制 `.env.example` 到 `.env`，填入您的 API Keys 和 Telegram Token。

3. **一键拉起机群**
   ```bash
   pm2 start ecosystem.config.js
   ```
   访问 `http://localhost:8000` 即可进入指挥大厅。

---

## 🤝 诚邀社区大牛深度改造
本项目目前处于 **V1.0 架构阶段**，诚邀大神们参与：
- **容器化部署** (Docker Compose)。
- **多租户权限隔离** (Auth)。
- **任务审计录像** (Playback)。

---
*Created with ❤️ by Gemini CLI, OpenClaw & Master papazed.*
