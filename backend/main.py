from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import sqlite3
import datetime
import urllib.request
import urllib.error
import subprocess
import yaml
import glob
import uuid
import asyncio

# 自动加载项目根目录 .env 文件
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, val = line.partition('=')
                    os.environ.setdefault(key.strip(), val.strip())
_load_dotenv()

from backend.database import init_db, get_db

app = FastAPI()

# Calculate base directory relative to this file (backend/main.py)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "hub_v2.db")
SKILL_ROUTER_PATH = os.path.join(BASE_DIR, "data", "skill_router.json")
ACTIVE_SKILLS_DIR = os.path.join(BASE_DIR, "backend", "active_skills")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

init_db()

# 跨域支持
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections = {}
    async def connect(self, websocket: WebSocket, agent_id: str):
        await websocket.accept()
        self.active_connections[agent_id] = websocket
    def disconnect(self, agent_id: str):
        if agent_id in self.active_connections:
            del self.active_connections[agent_id]
    async def broadcast(self, message: str):
        for agent_id, connection in list(self.active_connections.items()):
            try:
                await connection.send_text(message)
            except:
                pass
    async def send_personal(self, message: str, target_agent: str):
        if target_agent in self.active_connections:
            try:
                await self.active_connections[target_agent].send_text(message)
                return True
            except:
                pass
        return False

# 全局系统状态
SYSTEM_STATE = {
    "audit_mode": "agent"  # agent or manual
}

# ========== 特工健康状态追踪 ==========
# { agent_id: { error_count, last_error, last_error_time, status, repair_attempts, repair_start_time } }
AGENT_HEALTH = {}

# 诊断-修复-熔断 协议边界
DIAG_CONFIG = {
    "max_repair_attempts": 2,       # 每个特工每任务最多修复 2 次
    "repair_timeout_sec": 300,      # 修复总时长上限 5 分钟
    "error_threshold": 3,           # 连续 N 次错误 → 标记 degraded
    "degraded_cooldown_sec": 600,   # 降级冷却期 10 分钟
    "error_keywords": [             # 触发诊断的错误关键词
        "fetch failed", "ECONNREFUSED", "ETIMEDOUT", "rate limit",
        "api key", "401", "403", "500", "503", "timeout"
    ]
}

manager = ConnectionManager()




async def monitor_stalled_tasks():
    """
    任务停滞审计循环 (V3 - 指数退避策略)
    
    设计原则:
    - 每 60 秒轮询一次
    - 任务停滞 5 分钟才触发第一次催办
    - 每次催办后的冷却时长指数增长: 第1次5min→第2次10min→第3次20min→第4次40min→第5次80min
    - 同一任务最多被 AI 催促 5 次，超过后升级为人工处理
    - 升级后全频道广播告警，不再消耗 AI token
    """
    print("🛡️ Task Monitor started (V3 Exponential Backoff: 5→10→20→40→80min, max 5 nudges).")
    
    # { task_id: { "last_alert_time": datetime, "nudge_count": int, "escalated": bool } }
    alert_state = {}
    
    POLL_INTERVAL = 60          # 轮询间隔: 60 秒
    STALL_THRESHOLD = 300       # 停滞检测阈值: 5 分钟 (300 秒)
    # 指数退避冷却列表 (秒): 第N次催办后，需至少等待此时长才能触发第N+1次
    # index 0 = 第1次催办前的等待(即 STALL_THRESHOLD)
    # index N = 第N次催办后的冷却时长
    NUDGE_COOLDOWNS = [300, 600, 1200, 2400, 4800]  # 5,10,20,40,80 分钟
    MAX_NUDGES = 5              # 最大 AI 催促次数(超过升级人工)
    
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            # 💡 只有在代理审计模式下才执行自动下令
            if SYSTEM_STATE.get("audit_mode") != "agent":
                continue

            conn = get_db()
            import datetime as dt
            now = dt.datetime.utcnow()
            
            # 0. 代理存活检测 (心跳 30 秒)
            conn.execute(
                "UPDATE agents SET status = 'OFFLINE' WHERE status = 'ONLINE' AND datetime(last_heartbeat) < datetime('now', '-30 seconds')"
            )
            conn.commit()
            
            # 只监察真正执行中的任务，且排除 supervisor 自身的管理任务
            # 包括 PENDING 状态，防止任务因为特工未接单而永久卡死
            EXECUTOR_AGENTS = {'openclaw', 'openclaw-bridge', 'gemini-bot', 'deepseek-nas'}
            running_tasks = conn.execute(
                'SELECT id, title, assignee, status, updated_at FROM tasks WHERE status IN ("RUNNING", "PENDING") AND assignee != "supervisor"'
            ).fetchall()
            # 进一步过滤: 只监控真实执行特工的任务
            running_tasks = [t for t in running_tasks if any(ea in t['assignee'].lower() for ea in EXECUTOR_AGENTS)]
            
            for task in running_tasks:
                try:
                    updated = dt.datetime.strptime(task['updated_at'], '%Y-%m-%d %H:%M:%S')
                except Exception:
                    try:
                        updated = dt.datetime.fromisoformat(task['updated_at'])
                    except Exception:
                        continue
                seconds_stalled = (now - updated).total_seconds()
                task_id = task['id']
                
                # 未达到停滞阈值，跳过
                if seconds_stalled < STALL_THRESHOLD:
                    continue
                
                # 初始化该任务的警报状态
                if task_id not in alert_state:
                    alert_state[task_id] = {"last_alert_time": None, "nudge_count": 0, "escalated": False}
                
                state = alert_state[task_id]
                
                # 指数退避冷却期检查
                # 根据当前已发出的催办次数决定下次催办需等待的时长
                current_nudge = state["nudge_count"]
                # 取对应冷却时长，超出列表范围则用最后一个
                cooldown = NUDGE_COOLDOWNS[min(current_nudge, len(NUDGE_COOLDOWNS) - 1)]
                if state["last_alert_time"] and (now - state["last_alert_time"]).total_seconds() < cooldown:
                    continue
                
                state["last_alert_time"] = now
                
                # ========== 已升级为人工模式：只广播，不动用 AI ==========
                if state["escalated"]:
                    stall_min = int(seconds_stalled / 60)
                    await manager.broadcast(json.dumps({
                        'type': 'system',
                        'content': f'🆘 [人工介入等待] 特工 {task["assignee"]} 已停滞 {stall_min} 分钟，AI 审计已达上限，请人工排查。任务: {task["title"][:40]}'
                    }))
                    print(f"🆘 [Escalated] Task {task_id} still stalled ({stall_min}min). Human-only broadcast sent.")
                    continue
                
                # ========== AI 催促（有次数限制） ==========
                state["nudge_count"] += 1
                nudge_n = state["nudge_count"]
                
                recent_steps = conn.execute(
                    'SELECT name, logs FROM steps WHERE task_id = ? ORDER BY started_at DESC LIMIT 3', 
                    (task_id,)
                ).fetchall()
                context_logs = "\n".join([f"- {s['name']}: {str(s['logs'])[:80]}" for s in recent_steps])
                if not context_logs and task["status"] == "PENDING":
                    context_logs = "(任务尚未被特工接收)"
                
                stall_min = int(seconds_stalled / 60)
                status_desc = "卡在 PENDING" if task["status"] == "PENDING" else "停滞"
                
                # ========== 🔬 错误模式检测：区分「普通停滞」和「错误阻断」 ==========
                agent_id = task['assignee']
                detected_errors = []
                all_logs_text = context_logs.lower()
                for kw in DIAG_CONFIG["error_keywords"]:
                    if kw.lower() in all_logs_text:
                        detected_errors.append(kw)
                
                # 更新特工健康状态
                if agent_id not in AGENT_HEALTH:
                    AGENT_HEALTH[agent_id] = {
                        "error_count": 0, "last_error": None, "last_error_time": None,
                        "status": "healthy", "repair_attempts": 0, "repair_start_time": None
                    }
                
                health = AGENT_HEALTH[agent_id]
                
                if detected_errors:
                    health["error_count"] += 1
                    health["last_error"] = ", ".join(detected_errors)
                    health["last_error_time"] = now
                    if health["error_count"] >= DIAG_CONFIG["error_threshold"]:
                        health["status"] = "degraded"
                
                # ========== 根据健康状态决定信号类型 ==========
                if detected_errors:
                    # 🔬 检测到错误阻断 → 发 DIAGNOSE_AGENT（而非普通催办）
                    print(f"🔬 [Diag] Task {task_id} 检测到错误阻断: {detected_errors}. Agent {agent_id} 健康: {health['status']}")
                    
                    # 检查修复预算是否耗尽
                    repair_exhausted = health["repair_attempts"] >= DIAG_CONFIG["max_repair_attempts"]
                    if health["repair_start_time"] and (now - health["repair_start_time"]).total_seconds() > DIAG_CONFIG["repair_timeout_sec"]:
                        repair_exhausted = True
                    
                    await manager.send_personal(json.dumps({
                        'type': 'CONTROL_SIGNAL',
                        'action': 'DIAGNOSE_AGENT',
                        'stalled_agent': agent_id,
                        'stall_minutes': stall_min,
                        'context_logs': context_logs,
                        'task_id': task_id,
                        'task_title': f'[{task["status"]}] {task["title"]}',
                        'detected_errors': detected_errors,
                        'agent_health': health["status"],
                        'repair_attempts': health["repair_attempts"],
                        'repair_exhausted': repair_exhausted,
                    }), 'supervisor')
                    
                    await manager.broadcast(json.dumps({
                        'type': 'system',
                        'content': f'🔬 [诊断 {nudge_n}/{MAX_NUDGES}] 特工 {agent_id} 检测到故障: {", ".join(detected_errors)}。审计专员正在进行故障诊断...'
                    }))
                else:
                    # 普通停滞 → 常规催办
                    print(f"🚨 [Audit] Task {task_id} {status_desc} ({stall_min}min). Nudge {nudge_n}/{MAX_NUDGES}.")
                    
                    await manager.send_personal(json.dumps({
                        'type': 'CONTROL_SIGNAL',
                        'action': 'SUPERVISE_STALL',
                        'stalled_agent': agent_id,
                        'stall_minutes': stall_min,
                        'context_logs': context_logs,
                        'task_id': task_id,
                        'task_title': f'[{task["status"]}] {task["title"]}'
                    }), 'supervisor')
                    
                    await manager.broadcast(json.dumps({
                        'type': 'system',
                        'content': f'🚨 [AI 审计 {nudge_n}/{MAX_NUDGES}] 特工 {agent_id} {status_desc} {stall_min} 分钟，已通知指挥官盘询。'
                    }))
                
                # 达到上限 → 升级为人工
                if nudge_n >= MAX_NUDGES:
                    state["escalated"] = True
                    print(f"🆘 [ESCALATE] Task {task_id}: {MAX_NUDGES} nudges exhausted. Switching to human-only mode.")
                    await manager.broadcast(json.dumps({
                        'type': 'system',
                        'content': f'🆘🆘🆘 [升级告警] 特工 {task["assignee"]} 连续卡顿 {MAX_NUDGES} 次仍无进展，AI 审计已暂停。请人工介入处理，避免 token 浪费。任务: {task["title"][:50]}'
                    }))

            # 清理已完成任务的旧警报状态
            active_ids = {t['id'] for t in running_tasks}
            for old_id in list(alert_state.keys()):
                if old_id not in active_ids:
                    del alert_state[old_id]

            conn.close()
        except Exception as e:
            print(f"Monitor error: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(monitor_stalled_tasks())

@app.get('/api/v2/system/config')
async def get_system_config():
    return SYSTEM_STATE

@app.post('/api/v2/system/config')
async def update_system_config(request: Request):
    try:
        body = await request.json()
        if "audit_mode" in body:
            SYSTEM_STATE["audit_mode"] = body["audit_mode"]
            await manager.broadcast(json.dumps({
                'type': 'system',
                'content': f'⚙️ 系统审计模式已切换为: {"🤖 代理审计" if SYSTEM_STATE["audit_mode"] == "agent" else "👨‍💻 人工审计"}'
            }))
            await manager.broadcast(json.dumps({
                'type': 'CONFIG_UPDATED',
                'config': SYSTEM_STATE
            }))
        return SYSTEM_STATE
    except Exception as e:
        return {"error": str(e)}

@app.delete('/api/v2/tasks')
async def clear_all_tasks():
    """清空全部任务和步骤（重置看板），发送全局停机信号，并使用 PM2 直接停止所有进程"""
    import subprocess
    try:
        conn = get_db()
        conn.execute("DELETE FROM steps")
        conn.execute("DELETE FROM tasks")
        conn.commit()
        conn.close()
        
        # 1. 广播系统日志
        await manager.broadcast(json.dumps({'type': 'system', 'content': '🚨 [紧急状态] 已下达全局停机指令，看板已重置，正在停止所有 PM2 进程。'}))
        
        # 2. 广播全局控制信号，要求所有特工强制终止当前循环
        await manager.broadcast(json.dumps({
            'type': 'CONTROL_SIGNAL',
            'action': 'STOP_ALL'
        }))

        # 3. 直接通过 PM2 停止所有进程
        try:
            subprocess.run(["pm2", "stop", "all"], check=False)
        except Exception as e:
            print(f"Error executing pm2 stop all: {e}")
            await manager.broadcast(json.dumps({'type': 'system', 'content': f'❌ [错误] 停止 PM2 进程失败: {str(e)}'}))
        
        return {"success": True, "message": "ALL PM2 PROCESSES STOPPED"}
    except Exception as e:
        return {"error": str(e)}

@app.get('/api/v2/tasks')
async def list_tasks_v2():
    try:
        conn = get_db()
        tasks = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 15").fetchall()
        tasks_list = []
        for t in tasks:
            task_dict = dict(t)
            # Find steps for this task
            steps = conn.execute("SELECT * FROM steps WHERE task_id = ? ORDER BY started_at ASC", (task_dict['id'],)).fetchall()
            task_dict['steps'] = [dict(s) for s in steps]
            tasks_list.append(task_dict)
        conn.close()
        return tasks_list
    except Exception as e:
        return []

@app.post('/api/v2/tasks')
async def create_task_v2(request: Request):
    try:
        body = await request.json()
        title = body.get('title', 'Unnamed Task')
        assignee = body.get('assignee', 'Unknown')
        context_data = body.get('context', {})
        task_id = str(uuid.uuid4())
        
        conn = get_db()
        conn.execute(
            "INSERT INTO tasks (id, title, status, assignee, context) VALUES (?, ?, ?, ?, ?)", 
            (task_id, title, 'PENDING', assignee, json.dumps(context_data))
        )
        conn.commit()
        conn.close()
        
        await manager.broadcast(json.dumps({
            'type': 'TASK_CREATED', 
            'task': {'id': task_id, 'title': title, 'assignee': assignee, 'status': 'PENDING'}
        }))
        print(f"📡 [Broadcast] Task {task_id} created by {assignee}. Notifying all agents (including Gemini Bot)...")
        return {"id": task_id}
    except Exception as e:
        print(f"DB Error: {e}")
        return {"error": str(e)}

@app.put('/api/v2/tasks/{task_id}')
async def update_task(task_id: str, request: Request):
    try:
        body = await request.json()
        title = body.get('title')
        assignee = body.get('assignee')
        
        conn = get_db()
        if title and assignee:
            conn.execute("UPDATE tasks SET title = ?, assignee = ? WHERE id = ?", (title, assignee, task_id))
        elif title:
            conn.execute("UPDATE tasks SET title = ? WHERE id = ?", (title, task_id))
        elif assignee:
            conn.execute("UPDATE tasks SET assignee = ? WHERE id = ?", (assignee, task_id))
            
        conn.commit()
        conn.close()
        
        await manager.broadcast(json.dumps({
            'type': 'TASK_UPDATED',
            'task_id': task_id,
            'title': title,
            'assignee': assignee
        }))
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

@app.post('/api/v2/tasks/{task_id}/progress')
async def update_task_progress(task_id: str, request: Request):
    try:
        body = await request.json()
        progress = body.get('progress', 0)
        status = body.get('status', 'RUNNING')
        
        conn = get_db()
        conn.execute("UPDATE tasks SET progress = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (progress, status, task_id))
        conn.commit()
        conn.close()
        
        await manager.broadcast(json.dumps({
            'type': 'TASK_UPDATED',
            'id': task_id,
            'changes': {'progress': progress, 'status': status}
        }))
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

@app.post('/api/v2/tasks/{task_id}/steps')
async def create_step(task_id: str, request: Request):
    try:
        body = await request.json()
        name = body.get('name', 'Unnamed Step')
        step_id = str(uuid.uuid4())
        
        conn = get_db()
        conn.execute(
            "INSERT INTO steps (id, task_id, name, status, started_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)", 
            (step_id, task_id, name, 'PENDING')
        )
        # 更新父任务时间防止误报
        conn.execute("UPDATE tasks SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
        
        await manager.broadcast(json.dumps({
            'type': 'STEP_CREATED',
            'step': {'id': step_id, 'task_id': task_id, 'name': name, 'status': 'PENDING'}
        }))
        return {"step_id": step_id}
    except Exception as e:
        return {"error": str(e)}

@app.put('/api/v2/steps/{step_id}')
async def update_step(step_id: str, request: Request):
    try:
        body = await request.json()
        status = body.get('status', 'DONE')
        logs = body.get('logs', '')
        
        conn = get_db()
        ended_at = datetime.datetime.now() if status in ['DONE', 'FAILED'] else None
        
        if ended_at:
            conn.execute("UPDATE steps SET status = ?, logs = ?, ended_at = ? WHERE id = ?", (status, logs, ended_at, step_id))
        else:
            conn.execute("UPDATE steps SET status = ?, logs = ? WHERE id = ?", (status, logs, step_id))
            
        # Get task_id for broadcasting
        step_row = conn.execute("SELECT task_id FROM steps WHERE id = ?", (step_id,)).fetchone()
        task_id = step_row['task_id'] if step_row else None
        
        if task_id:
            conn.execute("UPDATE tasks SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (task_id,))
            
        conn.commit()
        conn.close()
        
        if task_id:
            await manager.broadcast(json.dumps({
                'type': 'STEP_UPDATED',
                'step_id': step_id,
                'task_id': task_id,
                'status': status,
                'logs': logs
            }))
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

@app.post('/api/v2/messages')
async def send_agent_message(request: Request):
    """跨客户端 Agent 通信端点，用于从当前 Agent 将消息发往另一个 Agent"""
    try:
        body = await request.json()
        target_agent = body.get('target_agent')
        if target_agent:
            await manager.send_personal(json.dumps(body), target_agent)
            return {"success": True, "delivered": target_agent in manager.active_connections}
        return {"error": "Missing target_agent"}
    except Exception as e:
        return {"error": str(e)}

@app.post('/api/v2/agents/heartbeat')
async def update_agent_heartbeat(request: Request):
    """供特工上报心跳包及系统资源指标"""
    try:
        body = await request.json()
        agent_id = body.get('agent_id')
        name = body.get('name', agent_id)
        cpu_percent = body.get('cpu_percent', 0)
        memory_mb = body.get('memory_mb', 0)
        disk_percent = body.get('disk_percent', 0)
        platform_info = body.get('platform_info', '')
        
        if not agent_id:
            return {"error": "Missing agent_id"}
            
        conn = get_db()
        existing = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE agents SET status = 'ONLINE', last_heartbeat = CURRENT_TIMESTAMP, "
                "cpu_percent = ?, memory_mb = ?, disk_percent = ?, platform_info = ? WHERE id = ?",
                (cpu_percent, memory_mb, disk_percent, platform_info, agent_id)
            )
        else:
            conn.execute(
                "INSERT INTO agents (id, name, status, last_heartbeat, cpu_percent, memory_mb, disk_percent, platform_info) "
                "VALUES (?, ?, 'ONLINE', CURRENT_TIMESTAMP, ?, ?, ?, ?)",
                (agent_id, name, cpu_percent, memory_mb, disk_percent, platform_info)
            )
        conn.commit()
        conn.close()
        
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

@app.get('/api/v2/agents')
async def get_agents():
    """获取目前在网格中的特工存活树"""
    try:
        conn = get_db()
        agents = conn.execute("SELECT * FROM agents").fetchall()
        conn.close()
        return [dict(a) for a in agents]
    except Exception as e:
        return []
        
@app.post('/api/v2/tasks/{task_id}/control')
async def control_task(task_id: str, request: Request):
    try:
        body = await request.json()
        action = body.get('action', 'PAUSE') # PAUSE, RESUME, CANCEL
        
        conn = get_db()
        # Find the agent assigned to this task
        task = conn.execute("SELECT id, assignee FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            return {"error": "Task not found"}
            
        assignee = task['assignee']
        
        status_map = {
            'PAUSE': 'PAUSING',
            'RESUME': 'RUNNING',
            'CANCEL': 'CANCELLED'
        }
        new_status = status_map.get(action, 'PENDING')
        
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (new_status, task_id))
        conn.commit()
        conn.close()
        
        await manager.broadcast(json.dumps({
            'type': 'TASK_UPDATED',
            'id': task_id,
            'changes': {'status': new_status}
        }))
        
        await manager.broadcast(json.dumps({
            'type': 'CONTROL_SIGNAL',
            'task_id': task_id,
            'action': action,
            'target_agent': assignee
        }))
        
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

# ===== 计划文件服务 (打破沙盒限制) =====
PLAN_SEARCH_DIRS = [
    os.path.join(BASE_DIR, "docs", "plans"),
    BASE_DIR,
]

def _get_plan_env_dirs():
    """从环境变量 PLAN_DIRS 读取额外的计划文件目录（逗号分隔）"""
    extra = os.environ.get("PLAN_DIRS", "")
    if extra:
        return [d.strip() for d in extra.split(",") if d.strip()]
    return []

@app.get('/api/v2/plans')
async def list_plans():
    """列出所有注册目录中的 .md 计划文件，任何 Agent 通过 HTTP 拉取"""
    plans = []
    search_dirs = PLAN_SEARCH_DIRS + _get_plan_env_dirs()
    seen = set()
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if fname.endswith('.md') and not fname.startswith('.') and fname not in seen:
                fpath = os.path.join(d, fname)
                seen.add(fname)
                plans.append({
                    'name': fname,
                    'dir': d,
                    'size': os.path.getsize(fpath),
                })
    return plans

@app.get('/api/v2/plans/{plan_name}')
async def read_plan(plan_name: str):
    """读取指定计划文件完整内容，Agent 可通过 HTTP 跨沙盒获取"""
    search_dirs = PLAN_SEARCH_DIRS + _get_plan_env_dirs()
    for d in search_dirs:
        fpath = os.path.join(d, plan_name)
        if os.path.isfile(fpath):
            with open(fpath, 'r', encoding='utf-8') as f:
                return {"name": plan_name, "content": f.read()}
    return {"error": f"Plan '{plan_name}' not found"}

@app.post('/api/v2/plans/decompose')
async def decompose_plan(request: Request):
    """将计划文件自动拆解为里程碑任务 (每个 ## 章节 → 一条可追踪任务)"""
    try:
        body = await request.json()
        plan_name = body.get('plan_name')
        assignee = body.get('assignee', 'gemini-bot')
        if not plan_name:
            return {"error": "Missing plan_name"}

        search_dirs = PLAN_SEARCH_DIRS + _get_plan_env_dirs()
        content = None
        for d in search_dirs:
            fpath = os.path.join(d, plan_name)
            if os.path.isfile(fpath):
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
                break
        if not content:
            return {"error": f"Plan '{plan_name}' not found"}

        import re
        sections = re.findall(r'^##\s+(.+)$', content, re.MULTILINE)
        if not sections:
            return {"error": "No ## sections found in plan"}

        created = []
        conn = get_db()
        for i, sec in enumerate(sections):
            tid = str(uuid.uuid4())
            title = f"[M{i+1}/{len(sections)}] {sec.strip()}"
            conn.execute(
                "INSERT INTO tasks (id, title, status, progress, assignee, priority, context) VALUES (?, ?, 'PENDING', 0, ?, 'HIGH', ?)",
                (tid, title, assignee, json.dumps({
                    'source': 'PLAN_DECOMPOSE',
                    'plan_file': plan_name,
                    'milestone': i+1,
                    'total': len(sections),
                }))
            )
            created.append({'id': tid, 'title': title})
        conn.commit()
        conn.close()

        await manager.broadcast(json.dumps({
            'type': 'system',
            'content': f'📋 计划 [{plan_name}] 已拆解为 {len(created)} 个里程碑任务 → {assignee}'
        }))
        return {"success": True, "milestones": len(created), "tasks": created}
    except Exception as e:
        return {"error": str(e)}

@app.post('/api/report')
async def report_progress(request: Request):
    try:
        body = await request.json()
        agent_id = body.get('agent_id', 'Unknown Agent')
        content = body.get('content', '')
        status = body.get('status', 'INFO')
        task_id = body.get('task_id')
        
        # 广播给前端
        await manager.broadcast(json.dumps({
            'type': 'agent_report',
            'agent_id': agent_id,
            'content': content,
            'status': status,
            'task_id': task_id,
            'timestamp': datetime.datetime.now().isoformat()
        }))
        
        # 如果有任务ID，尝试更新任务状态（可选）
        if task_id and status in ['COMPLETED', 'FAILED']:
            conn = get_db()
            conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
            conn.commit()
            conn.close()
            await manager.broadcast(json.dumps({'type': 'task_update'}))
            
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

@app.get('/api/skills')
async def get_skills():
    RAW_SKILLS_PATH = os.path.join(BASE_DIR, "data", "raw_skills.json")
    
    desc_map = {}
    try:
        with open(RAW_SKILLS_PATH, "r") as rf:
            raw = json.load(rf)
            for s in raw.get("skills", []):
                desc_map[s["name"]] = s.get("description", "").strip().split("\n")[0][:60]
    except:
        pass
    
    usage_map = {}
    try:
        conn = get_db()
        rows = conn.execute("SELECT skill_name, use_count FROM skill_usage").fetchall()
        for r in rows:
            usage_map[r['skill_name']] = r['use_count']
        conn.close()
    except:
        pass
    
    try:
        result_dict = {}
        openclaw_skills = []
        gemini_skills = []
        
        def parse_skills(dir_path, target_list, agent_id):
            full_path = os.path.expanduser(dir_path)
            # Find all SKILL.md in subdirectories and any standalone .md files
            files = glob.glob(os.path.join(full_path, "*/SKILL.md")) + glob.glob(os.path.join(full_path, "*.md"))
            for md_file in files:
                try:
                    with open(md_file, "r") as mf:
                        file_content = mf.read()
                        if file_content.startswith("---"):
                            end_idx = file_content.find("---", 3)
                            if end_idx != -1:
                                frontmatter_str = file_content[3:end_idx].strip()
                                frontmatter = yaml.safe_load(frontmatter_str) or {}
                                name = frontmatter.get("name", os.path.basename(os.path.dirname(md_file)) if md_file.endswith("SKILL.md") else os.path.basename(md_file).replace(".md", ""))
                                desc = frontmatter.get("description", desc_map.get(name, "Dynamic Custom Skill"))
                                target_list.append({
                                    "name": name, 
                                    "description": desc, 
                                    "use_count": usage_map.get(name, 0),
                                    "agent": agent_id
                                })
                except Exception as ex:
                    print(f"Failed to parse skill Markdown {md_file}: {ex}")

        parse_skills("~/gemini/active_skills", openclaw_skills, "openclaw-bridge")
        parse_skills("~/.gemini/skills", gemini_skills, "gemini-bot")
        
        skill_map = {}
        for s in openclaw_skills:
            skill_map[s["name"]] = {"name": s["name"], "description": s["description"], "use_count": s["use_count"], "agents": {"openclaw-bridge"}}
        for s in gemini_skills:
            if s["name"] in skill_map:
                skill_map[s["name"]]["agents"].add("gemini-bot")
            else:
                skill_map[s["name"]] = {"name": s["name"], "description": s["description"], "use_count": s["use_count"], "agents": {"gemini-bot"}}
                
        result_dict["Shared Skills"] = []
        result_dict["OpenClaw Exclusives"] = []
        result_dict["Gemini Exclusives"] = []
        
        for name, data in skill_map.items():
            agents = list(data["agents"])
            out_s = {
                "name": data["name"],
                "description": data["description"],
                "use_count": data["use_count"],
                "agents": agents
            }
            if "openclaw-bridge" in agents and "gemini-bot" in agents:
                result_dict["Shared Skills"].append(out_s)
            elif "openclaw-bridge" in agents:
                result_dict["OpenClaw Exclusives"].append(out_s)
            else:
                result_dict["Gemini Exclusives"].append(out_s)

        # Include basic static skills from router config if they are missing
        try:
            with open(SKILL_ROUTER_PATH, "r") as f:
                static_data = json.load(f)
                for cat, skills in static_data.items():
                    if cat not in result_dict:
                        result_dict[cat] = []
                    for skill_name in skills:
                        if skill_name not in skill_map:
                            result_dict[cat].append({
                                "name": skill_name,
                                "description": desc_map.get(skill_name, ""),
                                "use_count": usage_map.get(skill_name, 0),
                                "agents": ["openclaw-bridge", "gemini-bot"] # default system skills available to both
                            })
        except:
            pass
            
        # Clean up empty categories
        result_dict = {k: v for k, v in result_dict.items() if len(v) > 0}
        
        return result_dict
    except Exception as e:
        print(f"Error loading skills: {e}")
        return {}

@app.post('/api/v2/skills/use')
async def record_skill_use(request: Request):
    """记录技能使用频率"""
    try:
        body = await request.json()
        skill_name = body.get('skill_name')
        if not skill_name:
            return {"error": "Missing skill_name"}
        conn = get_db()
        existing = conn.execute("SELECT use_count FROM skill_usage WHERE skill_name = ?", (skill_name,)).fetchone()
        if existing:
            conn.execute("UPDATE skill_usage SET use_count = use_count + 1, last_used = CURRENT_TIMESTAMP WHERE skill_name = ?", (skill_name,))
        else:
            conn.execute("INSERT INTO skill_usage (skill_name, use_count, last_used) VALUES (?, 1, CURRENT_TIMESTAMP)", (skill_name,))
        conn.commit()
        conn.close()
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

@app.get('/api/v2/skills/suggest')
async def suggest_skills(q: str = ""):
    """根据用户输入推荐技能 (前缀匹配 + 频率加权)"""
    try:
        skills_dict = await get_skills()
        all_skills = []
        seen = set()
        
        for category, skills_list in skills_dict.items():
            for s in skills_list:
                name = s.get("name")
                if name not in seen:
                    seen.add(name)
                    skill_copy = dict(s)
                    skill_copy["category"] = category
                    all_skills.append(skill_copy)
        
        query = q.lower().strip()
        if not query:
            # 无输入时返回使用频率最高的 5 个
            top = sorted(all_skills, key=lambda s: s.get('use_count', 0), reverse=True)[:5]
            return top
        
        # 关键词匹配 + 频率加权
        scored = []
        keywords = query.split()
        for s in all_skills:
            score = 0
            name_lower = s.get('name', '').lower()
            desc_lower = s.get('description', '').lower()
            cat_lower = s.get('category', '').lower()
            for kw in keywords:
                if kw in name_lower:
                    score += 10
                if kw in desc_lower:
                    score += 5
                if kw in cat_lower:
                    score += 3
            # 频率加权
            score += min(s.get('use_count', 0), 20)
            if score > 0:
                scored.append((score, s))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:5]]
    except Exception as e:
        print(f"Error suggesting skills: {e}")
        return []

# ========================================
# Agent 回复广播 → Dashboard 实时显示
# ========================================
@app.post('/api/v2/agent-reply')
async def agent_reply(request: Request):
    """Agent 执行完毕后调此接口广播结果到所有 Dashboard"""
    try:
        body = await request.json()
        agent_id = body.get('agent_id', 'unknown')
        task_id = body.get('task_id')
        content = body.get('content', '')
        status = body.get('status', 'SUCCESS')
        
        await manager.broadcast(json.dumps({
            'type': 'agent_reply',
            'agent_id': agent_id,
            'task_id': task_id,
            'content': content,
            'status': status
        }))
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

@app.post('/api/v2/nudge')
async def nudge_agent(request: Request):
    """向特工发送催办消息，不创建新任务记录（专用于审计催办）"""
    try:
        body = await request.json()
        target = body.get('target')
        message = body.get('message', '')
        task_id = body.get('task_id')
        if not target or not message:
            return {"error": "Missing target or message"}
        
        await manager.send_personal(json.dumps({
            "type": "execute",
            "command": message,
            "task_id": task_id,
            "nudge": True  # 标记为催办，特工应识别并上报进度而非创建新任务
        }), target)
        print(f"📣 [Nudge] → [{target}] task={task_id}: {message[:60]}")
        return {"success": True, "msg": f"Nudge sent to {target}"}
    except Exception as e:
        return {"error": str(e)}

@app.post('/api/v2/commands')
async def handle_commands(request: Request):
    try:
        body = await request.json()
        target = body.get('target', 'gemini-bot')
        prompt = body.get('prompt') or body.get('command')
        if not prompt:
            return {"error": "Missing command prompt"}
        
        # 🔑 agent_session 表示 OpenClaw 子会话（如 security-expert）
        agent_session = body.get('agent_session')
        
        # 构建语义化的 assignee 标签，用于看板显示
        # 例: openclaw/security-expert，而非只显示 openclaw-bridge
        if (target == 'openclaw-bridge' or target == 'openclaw') and agent_session:
            assignee_label = f"openclaw/{agent_session}"
        else:
            assignee_label = target
            
        print(f"📡 CMD Routing: [{assignee_label}] -> {prompt[:50]}")
        
        # ========================================
        # 任务记录创建（或复用）
        # ========================================
        existing_task_id = body.get('task_id')
        skip_task_creation = body.get('skip_task_creation', False)
        
        task_id = existing_task_id or str(uuid.uuid4())
        task_title = prompt[:60] + ("..." if len(prompt) > 60 else "")
        
        if not (skip_task_creation and existing_task_id):
            try:
                conn = get_db()
                # INSERT OR IGNORE: 如果 task_id 已存在（如被 supervisor 预创建），不报错
                conn.execute(
                    "INSERT OR IGNORE INTO tasks (id, title, status, assignee, context) VALUES (?, ?, ?, ?, ?)",
                    (task_id, task_title, 'PENDING', assignee_label, json.dumps({
                        "source": "DASHBOARD" if not existing_task_id else "SUPERVISOR/OTHER",
                        "full_prompt": prompt,
                        "agent_session": agent_session
                    }))
                )
                # ✅ 关键！如果任务已存在（supervisor 预创建的），必须刷新 assignee 到实际执行者
                # 否则看板上 assignee 永远停在 "supervisor"，导致监控器找不到真正的执行特工
                if existing_task_id:
                    conn.execute(
                        "UPDATE tasks SET assignee = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (assignee_label, task_id)
                    )
                conn.commit()
                conn.close()
                
                await manager.broadcast(json.dumps({
                    'type': 'TASK_CREATED',
                    'task': {'id': task_id, 'title': task_title, 'assignee': assignee_label, 'status': 'PENDING'}
                }))
                print(f"📋 Task {task_id[:8]}... created/updated on kanban → [{assignee_label}]")
            except Exception as db_err:
                print(f"⚠️ Failed to create/reuse task record: {db_err}")
        
        # ========================================
        # 路由分发 → 始终直发给目标 Agent
        # ========================================
        audit_mode = body.get('audit_mode', 'manual')
        
        if target == 'openclaw-bridge' or target == 'openclaw':
            # ✅ 使用正确的 agent_session（如 security-expert），而非硬编码 'main'
            session = agent_session or 'main'
            
            # ✅ 注入 NEXUS_TASK_BINDING 头部 — 与 supervisor 分派格式完全一致
            # 这样子代理能从 prompt 中提取 task_id 并主动上报进度
            task_title_short = prompt[:60] + ("..." if len(prompt) > 60 else "")
            if task_id:
                nexus_header = (
                    f"[NEXUS_TASK_BINDING]\n"
                    f"task_id: {task_id}\n"
                    f"assignee: openclaw/{session}\n"
                    f"title: {task_title_short}\n"
                    f"hub_url: http://localhost:8000\n"
                    f"protocol: 接到任务后，请先调用 POST /api/v2/tasks/{{task_id}}/progress 上报 RUNNING，\n"
                    f"         执行完毕后上报 DONE，失败时上报 FAILED。\n"
                    f"         每个里程碑可通过 POST /api/v2/tasks/{{task_id}}/steps 记录步骤。\n"
                    f"         协议详情见 workspace 根目录 HUB_PROTOCOL.md 和 AGENT_INTEGRATION_SPEC.md\n"
                    f"[/NEXUS_TASK_BINDING]\n\n"
                )
                full_prompt = nexus_header + prompt
            else:
                full_prompt = prompt
            
            # --message 参数需要对双引号转义后放入 shell 命令
            escaped_prompt = full_prompt.replace('"', '\\"').replace('\n', '\\n')
            openclaw_cli = os.environ.get("OPENCLAW_CLI", "openclaw")
            cmd = f'{openclaw_cli} agent --agent {session} --message "{escaped_prompt}" --json'
            
            captured_task_id = task_id
            captured_session = session
            
            async def run_openclaw():
                try:
                    # 立即将任务推进为 RUNNING（不等 agent 自报，避免卡在 PENDING）
                    if captured_task_id:
                        try:
                            c = get_db()
                            c.execute(
                                "UPDATE tasks SET status = 'RUNNING', progress = 5, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (captured_task_id,)
                            )
                            c.commit()
                            c.close()
                            await manager.broadcast(json.dumps({
                                'type': 'TASK_UPDATED',
                                'id': captured_task_id,
                                'changes': {'progress': 5, 'status': 'RUNNING'}
                            }))
                        except:
                            pass
                    
                    process = await asyncio.create_subprocess_shell(
                        cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    
                    # ✅ 传递确认屏障：90秒超时，防止 Gateway 挂死导致任务静默消失
                    try:
                        stdout, stderr = await asyncio.wait_for(
                            process.communicate(), timeout=90
                        )
                    except asyncio.TimeoutError:
                        # 超时 → Gateway 可能挂了
                        process.kill()
                        await manager.broadcast(json.dumps({
                            'type': 'system',
                            'content': f'🚨 [传递超时] OpenClaw/{captured_session} 90秒无响应，Gateway 可能已宕机！任务已标记失败。'
                        }))
                        if captured_task_id:
                            try:
                                c = get_db()
                                c.execute(
                                    "UPDATE tasks SET status = 'FAILED', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                    (captured_task_id,)
                                )
                                c.commit()
                                c.close()
                            except: pass
                        return
                    
                    out_str = stdout.decode("utf-8", errors="ignore").strip()
                    err_str = stderr.decode("utf-8", errors="ignore").strip()
                    
                    # ✅ 检查 exit code：非零意味着 CLI 执行失败
                    if process.returncode != 0:
                        error_msg = err_str[:200] if err_str else f"exit code {process.returncode}"
                        await manager.broadcast(json.dumps({
                            'type': 'system',
                            'content': f'⚠️ [CLI 错误] OpenClaw/{captured_session} 执行失败: {error_msg}'
                        }))
                        if captured_task_id:
                            try:
                                c = get_db()
                                c.execute(
                                    "UPDATE tasks SET status = 'FAILED', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                    (captured_task_id,)
                                )
                                c.commit()
                                c.close()
                            except: pass
                        return
                    
                    await manager.broadcast(json.dumps({
                        'type': 'agent_report',
                        'agent_id': f'OpenClaw/{captured_session}',
                        'content': 'CLI 执行完毕。具体进度请查看看板。',
                        'status': 'SUCCESS'
                    }))
                except Exception as e:
                    await manager.broadcast(json.dumps({
                        'type': 'agent_report',
                        'agent_id': f'OpenClaw/{captured_session}',
                        'content': str(e),
                        'status': 'ERROR'
                    }))
                    if captured_task_id:
                        try:
                            c = get_db()
                            c.execute(
                                "UPDATE tasks SET status = 'FAILED', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (captured_task_id,)
                            )
                            c.commit()
                            c.close()
                        except:
                            pass
            
            asyncio.create_task(run_openclaw())
            dispatch_msg = f"命令已送达 OpenClaw/{session}（含 NEXUS 任务锚点 {task_id[:8] if task_id else 'N/A'}）。任务已挂载看板。"
            
        else:
            # 其他 Agent: 通过 WebSocket 推送
            await manager.send_personal(json.dumps({
                "type": "execute",
                "command": prompt,
                "task_id": task_id,
                "agent_session": agent_session
            }), target)
            dispatch_msg = f"命令已直发 {target}。任务已挂载看板。"
        
        # ========================================
        # 审计模式: 通知 supervisor 做事后监督
        # ========================================
        if audit_mode == 'agent' and task_id:
            try:
                # ✅ 传递真实被监控的特工标签，而非只传 target
                await manager.send_personal(json.dumps({
                    "type": "audit_watch",
                    "task_id": task_id,
                    "target_agent": assignee_label,
                    "prompt": prompt
                }), "supervisor")
                dispatch_msg += " 🤖 审计专员监控中。"
            except Exception as audit_err:
                print(f"⚠️ 审计通知失败: {audit_err}")
        
        return {"success": True, "msg": dispatch_msg, "task_id": task_id}

    except Exception as e:
        return {"error": str(e)}



@app.get('/api/agents')
def get_agents():
    # Start with agents proactively connected to the WebSocket bus
    connected = list(manager.active_connections.keys())
    
    # 1. Proactively Check DeepSeek NAS API availability
    if "deepseek-nas" not in connected:
        try:
            deepseek_url = os.environ.get("DEEPSEEK_API_URL", "").replace("/chat/completions", "/models")
            deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
            if deepseek_url and deepseek_key:
                req = urllib.request.Request(deepseek_url, headers={"Authorization": f"Bearer {deepseek_key}"}, timeout=1.5)
                res = urllib.request.urlopen(req)
                if res.getcode() == 200:
                    connected.append("deepseek-nas")
        except Exception:
            pass

    # Gather local process snapshot for the local agents
    try:
        ps_output = subprocess.check_output("ps aux", shell=True, text=True).lower()
        
        # 2. Check OpenClaw presence
        if "openclaw-bridge" not in connected:
            if "openclaw" in ps_output or "next dev" in ps_output:
                connected.append("openclaw-bridge")
                
        # 3. Check Gemini Bot script running presence
        if "gemini-bot" not in connected:
            if "bot.py" in ps_output or "remote_bridge" in ps_output:
                connected.append("gemini-bot")
    except Exception:
        pass

    return {"connected": connected}

@app.get('/')
async def index():
    return FileResponse(os.path.join(FRONTEND_DIR, 'index.html'))

@app.websocket('/ws/{agent_id}')
async def websocket_endpoint(websocket: WebSocket, agent_id: str):
    await manager.connect(websocket, agent_id)
    try:
        await manager.broadcast(json.dumps({'type': 'system', 'content': f'Agent {agent_id} is ONLINE'}))
        while True:
            raw_data = await websocket.receive_text()
            try:
                data = json.loads(raw_data)
                if data.get('type') == 'command':
                    target = data.get('target')
                    if target in manager.active_connections:
                        await manager.active_connections[target].send_text(json.dumps({
                            'type': 'execute',
                            'command': data.get('content')
                        }))
                        await manager.broadcast(json.dumps({'type': 'system', 'content': f'Command routed to {target}'}))
                else:
                    await manager.broadcast(json.dumps({'type': 'message', 'from': agent_id, 'content': raw_data}))
            except:
                await manager.broadcast(json.dumps({'type': 'message', 'from': agent_id, 'content': raw_data}))
    except WebSocketDisconnect:
        manager.disconnect(agent_id)
        await manager.broadcast(json.dumps({'type': 'system', 'content': f'Agent {agent_id} is OFFLINE'}))

IMAGES_DIR = os.path.join(BASE_DIR, "images")
if os.path.exists(IMAGES_DIR):
    app.mount('/images', StaticFiles(directory=IMAGES_DIR), name='images')

AUDIO_DIR = os.path.join(FRONTEND_DIR, "audio")
if os.path.exists(AUDIO_DIR):
    app.mount('/audio', StaticFiles(directory=AUDIO_DIR), name='audio')

app.mount('/static', StaticFiles(directory=FRONTEND_DIR), name='static')
