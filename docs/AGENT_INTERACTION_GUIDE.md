# HyperTask Hub: Agent Interaction & Command Protocol Guide
**To: Antigravity (Frontend/Full-Stack Developer)**
**From: System Architecture Team**
**Date: 2026-02-25**

This document serves as the definitive guide for implementing **Skill Triggers** and **Manual CMD Inputs** within the HyperTask Hub UI. It details the idiosyncratic communication protocols required to control our two primary agents: **OpenClaw** (The Worker) and **Gemini Bot** (The Supervisor).

---

## 1. System Architecture Overview

The HyperTask Hub (`http://localhost:8000`) acts as the central nervous system (Nexus). Agents do not talk directly to each other due to network isolation and platform restrictions (e.g., Telegram bots cannot "see" each other). 

Instead, all interactions follow a **Hub-and-Spoke model**:
1. **Frontend (UI)** sends commands to the **Hub Backend (`main.py`)**.
2. **Hub Backend** routes the command to the appropriate Agent using the most reliable channel for that specific Agent.
3. **Agents** execute the command and report progress back to the Hub (via REST APIs or log tailing).

---

## 2. Interacting with OpenClaw (The Worker)

OpenClaw handles heavy lifting (coding, tool execution, complex RAG). 

### 🚫 The "Front Door" (Do Not Use for Simple CMDs)
OpenClaw exposes a local WebSocket gateway on `ws://127.0.0.1:18789`. However, it implements a highly secure, cryptographic **Challenge-Response authentication protocol** (`connect.challenge`). Building a client to parse the nonce, sign the payload with RSA keys, and maintain the session is overly complex for simple UI command injections.

### ✅ The "God Mode" Backdoor (Highly Recommended)
Because OpenClaw is running locally on the same Mac Mini as the Hub Backend, we can bypass the network layer entirely and use its **native CLI**. This method is synchronous, 100% reliable, and runs with maximum privileges.

**Command Signature:**
```bash
openclaw agent --agent main --message "<YOUR_PROMPT>" --json
```

**How Antigravity Should Implement This:**
When a user clicks a "Trigger Skill" button or types a CMD meant for OpenClaw in the Hub UI:
1. Frontend sends `POST /api/v2/commands { "target": "openclaw", "prompt": "Execute Skill X: ..." }`.
2. Hub Backend (`main.py`) uses Python's `subprocess` to execute the CLI command above.
3. Hub Backend parses the JSON output (`stdout`) to extract the plain text reply.
4. Hub Backend broadcasts the result back to the Frontend via the `/ws/nexus` channel for display in a terminal UI or toast notification.

---

## 3. Interacting with Gemini Bot (The Supervisor)

The Gemini Bot (`remote_bridge/bot.py`) is the system's auditor and fallback executor. It maintains a persistent WebSocket connection to the Hub.

### ✅ The WebSocket Channel
The Hub Backend communicates with the Gemini Bot by pushing JSON payloads down its dedicated WebSocket pipeline (`ws://localhost:8000/ws/gemini-bot`).

**How Antigravity Should Implement This:**
To send a CMD or trigger a skill on the Gemini Bot:
1. Frontend sends `POST /api/v2/commands { "target": "gemini-bot", "command": "!generate_poster" }`.
2. Hub Backend (`main.py`) uses its `ConnectionManager` to push the command:
   ```python
   await manager.send_personal(json.dumps({
       "type": "execute",
       "command": "!generate_poster"
   }), "gemini-bot")
   ```
3. The `bot.py` script receives this, executes the corresponding Python logic (e.g., triggering the `subprocess` for the news script), and automatically streams the updates back to the Hub via `POST /api/report`.
4. Frontend listens to `{"type": "agent_report"}` events on the main WebSocket to render the live stream.

---

## 4. Advanced: The "Cross-Agent" Automation (Auto-Audit)

To understand how powerful this architecture is, look at the **Stall Supervision Loop** already implemented in `main.py` and `bot.py`:

1. `main.py` detects a task in the SQLite DB hasn't updated its `progress` in 15 minutes.
2. `main.py` sends a `SUPERVISE_STALL` JSON payload to the **Gemini Bot** over WebSocket.
3. **Gemini Bot** receives it, posts a public warning in the Telegram Group.
4. **Gemini Bot** then uses the **OpenClaw CLI Backdoor** (`subprocess.run(...)`) to forcefully inject a prompt asking OpenClaw why it is stuck.
5. OpenClaw responds. Gemini Bot takes the response, analyzes it using the `gemini` CLI tool, and generates an Audit Report.
6. **Gemini Bot** pushes the final Audit Report back to the Hub via `PUT /api/v2/steps/{step_id}` to update the Kanban board.

---

## 5. Action Items for Antigravity (Next Steps)

To complete the Command Nexus UI, please focus on the following:

1. **Build a Global Command Palette (CMD+K style)**:
   - Needs a dropdown to select the target agent (`openclaw` vs `gemini-bot`).
   - A text input for raw prompts.
   - A grid of "Quick Skills" (e.g., "Generate News", "Refactor Code", "Trigger Morning Nurse").

2. **Implement `POST /api/v2/commands` in `main.py`**:
   - Create the unified routing endpoint that translates the UI's intent into either a `subprocess` call (for OpenClaw) or a `manager.send_personal` WS push (for Gemini Bot), as detailed in Sections 2 & 3.

3. **Wire up the Live Terminal UI**:
   - Ensure the Frontend is subscribing to `{"type": "agent_report"}` WebSocket events and rendering them in a scrolling console view, so the user can see the output of their manual CMD inputs in real-time without checking Telegram.