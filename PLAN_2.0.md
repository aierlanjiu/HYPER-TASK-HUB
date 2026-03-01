# HyperTask Hub 2.0: The Command Nexus - Design Document

**Target Audience:** Antigravity (Developer)
**Status:** Approved for Implementation
**Date:** 2026-02-24

## 1. Executive Summary
Transform HyperTask Hub from a passive log viewer into a **State-Synchronized Command Nexus**.
- **Key Capability:** Real-time bi-directional control (Agent <-> Hub).
- **Visualization:** Kanban Board (Swimlanes) + Detailed Step Execution.
- **Data Integrity:** SQLite-backed persistence for historical replay.

## 2. Core Architecture (The "Nexus" Pattern)
- **Hybrid Protocol:**
  - **Reporting (Upstream):** HTTP POST for reliable, structured status updates.
  - **Control (Downstream):** WebSocket for instant commands (PAUSE/RESUME/CANCEL).

## 3. Database Schema (SQLite)

```sql
-- tasks: Macro-level unit of work
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,          -- UUID
    title TEXT NOT NULL,
    status TEXT DEFAULT 'PENDING',-- PENDING, RUNNING, PAUSED, DONE, FAILED, CANCELLED
    progress INTEGER DEFAULT 0,   -- 0-100
    assignee TEXT,                -- 'OpenClaw', 'Gemini', 'Swarm-Pixel'
    priority TEXT DEFAULT 'NORMAL',-- LOW, NORMAL, HIGH, URGENT
    context JSON,                 -- Parameters, input args
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP
);

-- steps: Micro-level execution trace
CREATE TABLE steps (
    id TEXT PRIMARY KEY,          -- UUID
    task_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'PENDING',-- PENDING, RUNNING, DONE, FAILED, SKIPPED
    logs TEXT,                    -- Detailed output/error log
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- agents: Registered worker nodes
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'OFFLINE',-- ONLINE, OFFLINE, BUSY, IDLE
    last_heartbeat TIMESTAMP
);
```

## 4. API Specification (v2)

### 4.1 Task Management
- `POST /api/v2/tasks`
  - Body: `{ "title": "Refactor Queue", "assignee": "OpenClaw", "context": {...} }`
  - Return: `{ "id": "task-uuid" }`

- `POST /api/v2/tasks/{id}/progress`
  - Body: `{ "progress": 45, "status": "RUNNING", "message": "Compiling assets..." }`
  - Effect: Updates `tasks.progress`, broadcasts `TASK_UPDATE`.

- `POST /api/v2/tasks/{id}/steps`
  - Body: `{ "name": "Check DB Connection" }`
  - Return: `{ "step_id": "step-uuid" }`

- `PUT /api/v2/steps/{id}`
  - Body: `{ "status": "DONE", "logs": "Connected to localhost:5432" }`
  - Effect: Updates step status, broadcasts `STEP_UPDATE`.

### 4.2 Control Plane
- `POST /api/v2/tasks/{id}/control`
  - Body: `{ "action": "PAUSE" }` (or RESUME, CANCEL)
  - Effect:
    1. Updates DB status to `PAUSING` (intermediate state).
    2. Broadcasts `CONTROL_SIGNAL` to specific Agent via WebSocket.
    3. Agent acks -> DB status updates to `PAUSED`.

### 4.3 WebSocket Events (`/ws/nexus`)
- **Server -> Client (Frontend):**
  - `TASK_CREATED`: `{ "task": {...} }`
  - `TASK_UPDATED`: `{ "id": "...", "changes": {...} }`
  - `STEP_UPDATED`: `{ "step_id": "...", "task_id": "...", "status": "..." }`
  - `AGENT_STATUS`: `{ "agent_id": "...", "status": "ONLINE" }`

- **Server -> Agent (Worker):**
  - `CONTROL_SIGNAL`: `{ "task_id": "...", "action": "PAUSE" }`

## 5. UI Design (Brandi Cyber-Dashboard)

### 5.1 Layout
- **Left Sidebar:** Agent Status (CPU/Mem/Active Task)
- **Center:** Kanban Board
  - **Backlog:** Tasks waiting to be picked up.
  - **Active:** Currently running tasks. **MUST show:**
    - Title & Assignee
    - Progress Bar (Animated)
    - Current Step (Blinking)
    - "Pause/Cancel" Controls (Hover)
  - **Done/Failed:** Historical record.
- **Right Sidebar:** Live Event Stream (Global Log)

### 5.2 Interaction
- **Click Card:** Opens Modal with **Step Timeline**.
- **Step Timeline:** Vertical list of steps. Running step pulses. Failed step shows red error log.
- **Drag & Drop:** (Future) Drag task from Backlog to Active to auto-assign.

## 6. Implementation Plan (Antigravity)

1.  **Backend Setup:**
    - Initialize SQLite with new schema.
    - Implement FastAPI routes for `/api/v2/tasks` and `/api/v2/steps`.
    - Implement WebSocket manager with room support (Frontend Room / Agent Room).

2.  **Frontend Dev:**
    - Build `KanbanBoard` component.
    - Build `TaskCard` component with progress bar.
    - Build `StepTimeline` component for details.
    - Connect `WebSocket` to Redux/State store.

3.  **Agent SDK (Python):**
    - `NexusClient` class:
      - `create_task(title)`
      - `start_step(name)`
      - `complete_step(step_id, logs)`
      - `listen_for_commands(callback)`

4.  **Integration Test:**
    - Run a dummy script using SDK.
    - Verify UI updates in real-time.
    - Verify "Pause" button actually stops the script.
