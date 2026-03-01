# HyperTask Hub 外部特工接入协议标准 (Nexus Protocol V2.1)

本规范用于定义任何第三方智能体（Agent）、爬虫脚手架、或各大聊天软件机器人（Telegram, Discord, 飞书）如何将自身的任务流程安全、优雅地挂载至 **HyperTask Hub** 任务总线看板。

**⚠️ 核心原则：任务 ID 是追踪链的锚点**
> 每次任务下发时，Hub 都会随命令携带一个全局唯一的 `task_id`。特工必须在整个执行过程中始终携带并上报该 `task_id`，确保信息传递的准确性，避免跨任务干扰。

---

## 1. 系统要求与术语

* **Hub URL:** 默认中心地址为 `http://localhost:8000`
* **Worker ID (`assignee`):** 这是外部系统在 Hub 上的代号，如 `deepseek-nas`, `gemini-bot`，可由第三方自定义。
* **Task (任务):** 用户下发的大指令，每条任务有唯一 `task_id`。
* **Step (步骤):** 为该 Task 细分的推理动作或执行日志。
* **Supervisor:** 审计专员，代号 `supervisor`，是调度者，**不是执行者**，不会被催办。

---

## 2. 任务生命周期协议（必须遵守）

特工收到任务后，**必须按以下状态机流转**，否则任务将永久停留在 PENDING，触发超时告警：

```
PENDING → RUNNING → DONE
                 ↘ FAILED
```

| 状态 | 含义 | 触发方式 |
|------|------|---------|
| `PENDING` | 任务已创建，等待特工领取 | Hub 自动创建 |
| `RUNNING` | 特工已接单，正在执行 | **特工必须主动推送** |
| `DONE` | 任务成功完成 | **特工必须主动推送** |
| `FAILED` | 任务执行失败 | **特工必须主动推送** |

---

## 3. 交互全生命周期 (Restful API)

### 阶段 0：接收任务（Task ID 锚定）

当 Hub 通过 WebSocket 下发 `execute` 类型消息时，其中包含 `task_id`。特工必须提取并保存该 ID：

```json
// Hub 下发的 execute 消息格式
{
  "type": "execute",
  "command": "帮我分析一下最近的市场趋势",
  "task_id": "a1b2c3d4-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

> **⚠️ 强制要求：** 特工接收到 `execute` 消息后，第一步必须调用 `/progress` 接口将状态推进到 `RUNNING`，否则任务会因永久 PENDING 被识别为"从未启动"。

### 阶段 1：领取任务 → 状态推进为 RUNNING

特工接单后，立即用 `task_id` 上报"已开始执行"：

```http
POST /api/v2/tasks/{task_id}/progress
Content-Type: application/json

{
  "progress": 5,
  "status": "RUNNING"
}
```

### 阶段 2：推进与实时步骤拆解 (Live Steps)

特工作业时，每走到一个有意义的分形节点（比如搜索、推理、查库）时，发送一次进度声明。

**2.1 开设一个步骤**

```http
POST /api/v2/tasks/{task_id}/steps
Content-Type: application/json

{ "name": "正在调用海量互联网检索..." }
```

**Response:** 返回该步骤的 `step_id`。

**2.2 上报当前任务完成进度**

```http
POST /api/v2/tasks/{task_id}/progress
Content-Type: application/json

{ "progress": 35, "status": "RUNNING" }
```

**2.3 闭合（完结/失败）特定步骤**

```http
PUT /api/v2/steps/{step_id}
Content-Type: application/json

{
  "status": "DONE",
  "logs": "成功爬取了1.2w字维基词条并进行了向量化压缩"
}
```

*(若是致命错误，status 填 `FAILED`)*

### 阶段 3：宣告终结 (Task Complete)

```http
POST /api/v2/tasks/{task_id}/progress
Content-Type: application/json

{ "progress": 100, "status": "DONE" }
```

---

## 4. 向审计专员汇报（进阶）

任务执行的关键节点，特工可以通过 `/api/v2/agent-reply` 将结果主动广播给 Dashboard 和审计专员。

```http
POST /api/v2/agent-reply
Content-Type: application/json

{
  "agent_id": "gemini-bot",
  "task_id": "a1b2c3d4-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "content": "已完成分析，结果如下：...",
  "status": "SUCCESS"
}
```

---

## 5. 双向控制权接入 (WebSocket) [进阶]

如果您希望您的 Agent 具备被 Hub 的【审计专员 (Supervisor)】临时干涉（督促、暂停、中断重启）的能力：

* **Endpoint:** `ws://localhost:8000/ws/{your-agent-id}`

```json
// 中心下发执行新任务（必须保存 task_id！）
{ "type": "execute", "command": "去查一下今天的天气", "task_id": "a1b2c3d4-..." }

// 审计专员催办（仅针对真实执行特工，supervisor 自身不会被催办）
{ "type": "CONTROL_SIGNAL", "action": "SUPERVISE_STALL", "task_id": "xxx", "task_title": "..." }

// 急停信号
{ "type": "CONTROL_SIGNAL", "action": "STOP_ALL" }
```

---

## 6. 可监控特工列表

只有以下特工的 RUNNING 任务会被审计员监控和催办：

| Agent ID | 名称 | 类型 |
|----------|------|------|
| `openclaw` / `openclaw-bridge` | OpenClaw | CLI 执行型 |
| `gemini-bot` | Gemini Bot | WebSocket 监听型 |
| `deepseek-nas` | DeepSeek NAS | WebSocket 监听型 |

> **注意：** `supervisor` 是调度者，其任务不纳入停滞监控，永远不会被催办。

---

## 7. 完整示例（Python 伪代码）

```python
import requests, websockets, json, asyncio

HUB = "http://localhost:8000"
AGENT_ID = "my-agent"

async def main():
    async with websockets.connect(f"ws://localhost:8000/ws/{AGENT_ID}") as ws:
        while True:
            raw = await ws.recv()
            data = json.loads(raw)
            
            if data.get("type") == "execute":
                task_id = data["task_id"]   # ⚠️ 必须保存！
                command = data["command"]
                
                # Step 1: 立即推进为 RUNNING
                requests.post(f"{HUB}/api/v2/tasks/{task_id}/progress",
                    json={"progress": 5, "status": "RUNNING"})
                
                # Step 2: 开始执行，记录步骤
                step = requests.post(f"{HUB}/api/v2/tasks/{task_id}/steps",
                    json={"name": "开始处理..."}).json()
                step_id = step["step_id"]
                
                # ... 执行实际任务逻辑 ...
                result = do_work(command)
                
                # Step 3: 闭合步骤
                requests.put(f"{HUB}/api/v2/steps/{step_id}",
                    json={"status": "DONE", "logs": result[:500]})
                
                # Step 4: 宣告完成
                requests.post(f"{HUB}/api/v2/tasks/{task_id}/progress",
                    json={"progress": 100, "status": "DONE"})
                
                # Step 5: 广播回复给 Dashboard
                requests.post(f"{HUB}/api/v2/agent-reply",
                    json={"agent_id": AGENT_ID, "task_id": task_id,
                          "content": result, "status": "SUCCESS"})

asyncio.run(main())
```
