"""
HyperTask Hub 审计专员 (Supervisor Agent)
独立进程，负责：
1. 监听 Hub WebSocket 接收用户命令
2. 调用本地 Gemini CLI 理解意图
3. 解析结构化标签 → 自动创建任务并路由到对应特工
4. 监督任务进度，卡顿时发出告警

启动方式: pm2 start supervisor_agent.py --name supervisor --interpreter python3
"""
import asyncio
import json
import os
import sys
import time
import tempfile
import threading
import uuid

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

import requests
import websockets
import discord

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from ai_parser import parse_ai_response

# ========== 配置 ==========
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
HUB_URL = f"http://{os.environ.get('HUB_HOST', 'localhost')}:{os.environ.get('HUB_PORT', '8000')}"
HUB_WS = f"ws://{os.environ.get('HUB_HOST', 'localhost')}:{os.environ.get('HUB_PORT', '8000')}/ws/supervisor"
GEMINI_CLI = os.environ.get("GEMINI_CLI", "gemini")
OPENCLAW_CLI = os.environ.get("OPENCLAW_CLI", "openclaw")
PROMPT_FILE = os.path.join(os.path.dirname(__file__), "supervisor_prompt.md")
AGENT_ID = "supervisor"

# ========== Discord 模块 ==========
intents = discord.Intents.default()
# 需要开启 message_content intent 来读取消息内容
intents.message_content = True
discord_client = discord.Client(intents=intents)

async def notify_discord(message: str):
    """向 Discord 服务器发送全局审计/系统通知，优先发往常规频道"""
    if not discord_client.is_ready():
        return
        
    public_keywords = ["常规", "general", "公共频道", "audit", "hub"]
    
    for guild in discord_client.guilds:
        target_channel = None
        # 1. 尝试寻找名称匹配的公共频道
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                if any(kw in channel.name.lower() for kw in public_keywords):
                    target_channel = channel
                    break
        # 2. 回退到第一个可用频道
        if not target_channel:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    target_channel = channel
                    break
                    
        if target_channel:
            try:
                await target_channel.send(message)
                return
            except:
                pass

@discord_client.event
async def on_ready():
    print(f"🎮 Discord 服务器已连接，Bot 身份: {discord_client.user}")
    await notify_discord("🛡️ **HyperTask 审计专员已上线 (V2 架构)**\n目前只接管 Discord 审计频道。")

@discord_client.event
async def on_message(message: discord.Message):
    """处理 Discord 中的消息，根据频道名称进行路由"""
    # 忽略机器人自己的消息
    if message.author == discord_client.user:
        return
        
    # 只响应 @ 机器人的消息
    if discord_client.user in message.mentions:
        # 提取真实内容
        content = message.content.replace(f'<@{discord_client.user.id}>', '').strip()
        channel_name = message.channel.name.lower()
        
        claw_sessions = ["main", "fe-expert", "be-expert", "art-director", "qa-expert", "security-expert", "growth-expert", "finance-expert", "news-expert"]
        
        target = None
        agent_session = None
        
        if channel_name in claw_sessions:
            target = "openclaw"
            agent_session = channel_name
        elif channel_name == "deepseek-nas":
            target = "deepseek-nas"
            
        print(f"💬 [Discord] 收到消息 - 频道: #{channel_name} | 内容: {content[:30]}...")
            
        if target:
            # 在特定子代理频道，直发给 Hub 进行调度
            payload = {
                "target": target,
                "prompt": content,
                "audit_mode": "agent"  # 强制要求审计专员监控
            }
            if agent_session:
                payload["agent_session"] = agent_session
                
            try:
                # 提示在当前频道已受理，并说明结果会由 audit 监控输出
                await message.channel.send(f"✅ 已直派给 `{target}{' : ' + agent_session if agent_session else ''}` 开始执行。\n(审计专员已挂载监控，随后将在公共频道输出简报)")
                requests.post(f"{HUB_URL}/api/v2/commands", json=payload, timeout=5)
            except Exception as e:
                await message.channel.send(f"❌ 任务提交 Hub 失败: {e}")
        else:
            # 如果是在普通频道 @，则触发完整的 AI 审计与自动调度流程
            await message.channel.send("🤖 审计专员正在分析您的意图，并自动寻找最适合的子代理...")
            task_id = str(uuid.uuid4())
            # 直接挂入异步任务，让 Supervisor 分析并分发
            asyncio.create_task(handle_execute_command(content, task_id))

# ========== System Prompt 热加载 ==========
def load_system_prompt() -> str:
    """每次调用时从文件重新读取，支持热更新"""
    try:
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"⚠️ Failed to load prompt: {e}")
        return "你是 HyperTask Hub 审计专员，负责任务分派。"


# ========== Gemini CLI 调用 ==========
async def ask_gemini(user_input: str) -> str:
    """调用本地 Gemini CLI 获取审计专员的分析结果"""
    system_prompt = load_system_prompt()

    # 获取动态技能归属
    skills_context = "\n## 动态技能归属情况\n当前系统内的可用技能及其归属特工如下（必须严格按照此归属进行调度）：\n"
    try:
        import requests
        res = requests.get("http://localhost:8000/api/skills", timeout=3)
        if res.status_code == 200:
            skills_data = res.json()
            for cat, skills in skills_data.items():
                skills_context += f"### {cat}\n"
                for s in skills:
                    agents = ", ".join(s.get("agents", []))
                    skills_context += f"- **{s['name']}**: 仅限特工 [{agents}] 执行。\n"
    except Exception as e:
        skills_context += "(无法获取最新技能列表)\n"

    full_prompt = f"{system_prompt}\n{skills_context}\n---\n\n用户指令: {user_input}\n\n请分析并输出调度决策："

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.md', encoding='utf-8') as tmp:
        tmp.write(full_prompt)
        tmp_path = tmp.name    
    try:
        cmd = f"cat '{tmp_path}' | {GEMINI_CLI} -p - --model gemini-3-flash-preview --approval-mode yolo"
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        result = stdout.decode('utf-8', errors='ignore').strip()
        
        if not result:
            err = stderr.decode('utf-8', errors='ignore').strip()
            print(f"⚠️ Gemini CLI stderr: {err[:200]}")
            return ""
        
        return result
    except Exception as e:
        print(f"❌ Gemini CLI error: {e}")
        return ""
    finally:
        try:
            os.remove(tmp_path)
        except:
            pass


# ========== Hub API 交互 ==========
def hub_create_task(title: str, assignee: str, priority: str = "MEDIUM", context: dict = None) -> str:
    """在 Hub 看板上创建任务，返回 task_id"""
    task_id = str(uuid.uuid4())
    try:
        payload = {
            "id": task_id,
            "title": title,
            "assignee": assignee,
            "context": context or {}
        }
        resp = requests.post(f"{HUB_URL}/api/v2/tasks", json=payload, timeout=5)
        data = resp.json()
        return data.get("task_id", task_id)
    except Exception as e:
        print(f"⚠️ Failed to create task: {e}")
        return task_id


def hub_dispatch_to_agent(target: str, command: str, task_id: str, agent_session: str = None, task_title: str = ""):
    """通过 Hub 的统一命令端点将任务分派给目标特工，任务 ID 同时写入 prompt header"""
    try:
        # ✅ Nexus Protocol V2.1: 在 prompt 头部注入任务锚点信息
        # 特工必须提取 task_id 并在整个执行过程中持续上报进度
        nexus_header = (
            f"[NEXUS_TASK_BINDING]\n"
            f"task_id: {task_id}\n"
            f"assignee: {target}{('/' + agent_session) if agent_session else ''}\n"
            f"title: {task_title or command[:60]}\n"
            f"protocol: 接到此任务后立即调用 POST /api/v2/tasks/{{task_id}}/progress 上报 RUNNING，\n"
            f"         执行完毕后上报 DONE，失败时上报 FAILED。\n"
            f"[/NEXUS_TASK_BINDING]\n\n"
        )
        full_prompt = nexus_header + command

        payload = {
            "target": target,
            "prompt": full_prompt,
            "task_id": task_id,
            "source": "supervisor"
        }
        if agent_session:
            payload["agent_session"] = agent_session
        resp = requests.post(f"{HUB_URL}/api/v2/commands", json=payload, timeout=10)
        print(f"📡 Dispatched to [{target}" + (f"/{agent_session}" if agent_session else "") + f"] task={task_id[:8]}: {resp.json()}")
    except Exception as e:
        print(f"❌ Dispatch failed: {e}")


async def hub_broadcast(message: str):
    """通过 Hub 广播系统消息"""
    try:
        await asyncio.to_thread(
            requests.post,
            f"{HUB_URL}/api/v2/messages",
            json={"agent_id": AGENT_ID, "content": message},
            timeout=3
        )
    except:
        pass

async def talk_to_openclaw(prompt: str, session: str = "main") -> str:
    """直接调用本地 OpenClaw CLI 进行盘询/传达指令，支持指定子会话"""
    try:
        cmd = f'{OPENCLAW_CLI} agent --agent {session} --message "{prompt}" --json'
        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        out_str = stdout.decode("utf-8", errors="ignore").strip()
        
        try:
            data = json.loads(out_str)
            if 'result' in data and 'payloads' in data['result']:
                return data['result']['payloads'][0]['text']
        except: pass
        
        err_str = stderr.decode("utf-8", errors="ignore").strip()
        if out_str: return out_str
        if err_str: return err_str
        return "指令已强制下达 OpenClaw 后台。"
    except Exception as e:
        return f"命令调用失败: {e}"


# ========== 心跳 ==========
def start_heartbeat():
    """定时向 Hub 发送心跳"""
    def ping():
        while True:
            try:
                requests.post(
                    f"{HUB_URL}/api/v2/agents/heartbeat",
                    json={
                        "agent_id": AGENT_ID,
                        "name": "supervisor",
                        "platform_info": "HyperTask Supervisor (Gemini CLI)"
                    },
                    timeout=3
                )
            except:
                pass
            time.sleep(15)
    
    t = threading.Thread(target=ping, daemon=True)
    t.start()


# ========== 核心命令处理 ==========
async def handle_execute_command(command: str, original_task_id: str = None):
    """
    核心流程：
    1. 调 Gemini CLI 分析用户意图
    2. 解析结构化标签
    3. 创建看板任务（如果 Hub 没预创建的话）
    4. 路由到目标特工执行
    """
    print(f"\n{'='*60}")
    print(f"📥 收到指令: {command[:80]}")
    print(f"{'='*60}")
    
    # 1. 调用审计专员 AI
    print("🤔 审计专员正在分析...")
    ai_response = await ask_gemini(command)
    
    if not ai_response:
        print("❌ AI 未返回有效回复")
        if original_task_id:
            try:
                requests.post(
                    f"{HUB_URL}/api/v2/tasks/{original_task_id}/progress",
                    json={"progress": 0, "status": "FAILED"},
                    timeout=3
                )
            except:
                pass
        return
    
    # 2. 解析结构化标签
    directive = parse_ai_response(ai_response)
    print(f"📋 解析结果: {directive}")
    print(f"💬 简报: {directive.briefing}")
    
    # 3. 处理升级（需要人工介入）
    if directive.is_escalation:
        print("🆘 审计专员判定需要人工介入！")
        await hub_broadcast(f"🆘 [审计专员] 需要人工介入: {directive.briefing}")
        if original_task_id:
            try:
                requests.post(
                    f"{HUB_URL}/api/v2/tasks/{original_task_id}/steps",
                    json={"name": f"🆘 审计专员: {directive.briefing[:50]}"},
                    timeout=3
                )
            except:
                pass
        return
    
    # 4. 校验指令有效性
    if not directive.is_valid:
        print(f"⚠️ 指令无效: target={directive.target}, task={directive.task}")
        await hub_broadcast(f"⚠️ [审计专员] 无法解析有效指令: {directive.briefing}")
        return
    
    # 5. 特殊处理：人工任务管理指令 (COMPLETE / CANCEL)
    if directive.action in ['COMPLETE', 'CANCEL']:
        target_id = directive.task_id
        if target_id:
            status_val = 'DONE' if directive.action == 'COMPLETE' else 'CANCELLED'
            log_msg = '✅ 审计专员：受理主理人指令，标记为已完成' if directive.action == 'COMPLETE' else '❌ 审计专员：受理主理人指令，作废该任务'
            print(f"🔧 处理任务管理指令: {target_id} -> {status_val}")
            
            try:
                # 更新目标任务
                requests.post(f"{HUB_URL}/api/v2/tasks/{target_id}/progress", json={"progress": 100, "status": status_val}, timeout=3)
                requests.post(f"{HUB_URL}/api/v2/tasks/{target_id}/steps", json={"name": log_msg}, timeout=3)
                
                # 更新当前这句命令的载体任务
                if original_task_id:
                    requests.post(f"{HUB_URL}/api/v2/tasks/{original_task_id}/progress", json={"progress": 100, "status": "DONE"}, timeout=3)
                    requests.post(f"{HUB_URL}/api/v2/tasks/{original_task_id}/steps", json={"name": "✅ 管理指令已执行"}, timeout=3)
            except Exception as e:
                print(f"⚠️ 更新任务状态失败: {e}")
                
            await hub_broadcast(f"📝 [审计专员] 已将任务 {target_id} 标记为 {status_val}。")
        else:
            print("⚠️ 指令中缺失目标 TASK_ID")
            await hub_broadcast("⚠️ [审计专员] 接收到任务管理指令，但未能解析到目标任务 ID。")
            if original_task_id:
                try: requests.post(f"{HUB_URL}/api/v2/tasks/{original_task_id}/progress", json={"progress": 100, "status": "FAILED"}, timeout=3)
                except: pass
        return

    # 6. 使用 Hub 预创建的 task_id，或者让 dispatch 端点创建
    task_id = original_task_id
    
    # 6. 更新看板上的任务标题（用 AI 优化后的标题替换原始输入）
    agent_label = f" → {directive.agent}" if directive.agent else ""
    if task_id and directive.task:
        try:
            # 采用实际 PUT Endpoint 对任务标题进行规范化刷新
            requests.put(
                f"{HUB_URL}/api/v2/tasks/{task_id}",
                json={"title": directive.task},
                timeout=3
            )
            requests.post(
                f"{HUB_URL}/api/v2/tasks/{task_id}/steps",
                json={"name": f"🎯 审计决策: {directive.target}{agent_label} | {directive.priority}"},
                timeout=3
            )
            requests.post(
                f"{HUB_URL}/api/v2/tasks/{task_id}/progress",
                json={"progress": 10, "status": "RUNNING"},
                timeout=3
            )
        except:
            pass
    
    # 7. 路由到目标特工（含子会话）
    print(f"🚀 正在分派到 [{directive.target}{agent_label}]...")
    hub_dispatch_to_agent(directive.target, command, task_id, directive.agent, task_title=directive.task)
    
    # 8. 广播审计简报
    await hub_broadcast(
        f"📋 [审计专员] {directive.briefing}\n"
        f"🎯 目标: {directive.target} | 优先级: {directive.priority} | 动作: {directive.action}"
    )
    
    # 9. 社交媒体通知
    await notify_discord(f"**🎯 审计决策**\n**任务**: {directive.task[:60]}\n**派分**: {directive.target}{agent_label}\n\n**分析**:\n{directive.briefing}")
    
    print(f"✅ 任务已分派完成\n")


# ========== WebSocket 主循环 ==========
async def ws_main_loop():
    """连接 Hub WebSocket，监听和处理消息"""
    print(f"🛰️ 审计专员启动，连接 {HUB_WS}...")
    
    while True:
        try:
            async with websockets.connect(HUB_WS) as ws:
                print("🔗 已连接 Hub WebSocket")
                
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    msg_type = data.get("type")
                    
                    if msg_type == "execute":
                        command = data.get("command", "")
                        task_id = data.get("task_id")
                        # 立即将任务推进为 RUNNING，表明审计专员已介入
                        # 避免任务永远停在 PENDING，误触停滞告警
                        if task_id:
                            try:
                                requests.post(
                                    f"{HUB_URL}/api/v2/tasks/{task_id}/progress",
                                    json={"progress": 5, "status": "RUNNING"},
                                    timeout=2
                                )
                                requests.post(
                                    f"{HUB_URL}/api/v2/tasks/{task_id}/steps",
                                    json={"name": "🛡️ 审计专员已接单，正在分析意图并分派..."},
                                    timeout=2
                                )
                            except Exception as e:
                                print(f"⚠️ 无法推进任务状态: {e}")
                        # 异步处理，不阻塞 WS 接收
                        asyncio.create_task(handle_execute_command(command, task_id))
                    
                    elif msg_type == "audit_watch":
                        # 审计监控模式：只监督，不路由
                        task_id = data.get("task_id")
                        target_agent = data.get("target_agent", "unknown")
                        prompt = data.get("prompt", "")
                        print(f"👁️ [审计监控] 任务 {task_id[:8] if task_id else '?'}... → {target_agent}")
                        # 在看板上标记审计专员已介入监控
                        if task_id:
                            try:
                                requests.post(
                                    f"{HUB_URL}/api/v2/tasks/{task_id}/steps",
                                    json={"name": f"👁️ 审计专员已挂载监控 → {target_agent}"},
                                    timeout=2
                                )
                            except:
                                pass
                        
                         # 社交媒体通知
                        asyncio.create_task(notify_discord(f"**👁️ 审计专员挂载监控**\n**监控特工**: {target_agent}\n**提示词**: {prompt[:100]}..."))
                    
                    elif msg_type == "CONTROL_SIGNAL":
                        action = data.get("action")
                        if action == "STOP_ALL":
                            print("🛑 [STOP_ALL] 收到急停信号，审计专员退出")
                            return
                        elif action == "DIAGNOSE_AGENT":
                            stalled_agent = data.get('stalled_agent')
                            stall_minutes = data.get('stall_minutes', 0)
                            task_id = data.get('task_id')
                            context_logs = data.get('context_logs', '无日志')
                            task_title = data.get('task_title', '未知任务')
                            detected_errors = data.get('detected_errors', [])
                            agent_health = data.get('agent_health', 'degraded')
                            repair_attempts = data.get('repair_attempts', 0)
                            repair_exhausted = data.get('repair_exhausted', False)
                            
                            errors_str = ", ".join(detected_errors)
                            
                            step_id = None
                            if task_id:
                                try:
                                    idx_desc = "" if repair_exhausted else f"(第 {repair_attempts+1} 次挽救)"
                                    res = requests.post(f"{HUB_URL}/api/v2/tasks/{task_id}/steps", json={"name": f"🔬 审计诊断: 阻断故障 {errors_str} {idx_desc}..."}, timeout=2).json()
                                    step_id = res.get("step_id")
                                except: pass

                            if repair_exhausted:
                                limit_msg = f"⛔ **[熔断保护触发]**\n@WatcherBot 特工 **{stalled_agent}** 因 **{errors_str}** 多次尝试修复失败。\n💥 任务【{task_title}】修复超时或次数达标！系统已切断该特工自愈授权，必须由人类指挥官接手！"
                                await notify_discord(limit_msg)
                                print(f"⛔ [Circuit Breaker] Agent {stalled_agent} repair exhausted.")
                                if step_id:
                                    try:
                                        requests.put(f"{HUB_URL}/api/v2/steps/{step_id}", json={"status": "FAILED", "logs": "修复指标耗尽，触发熔断，交由人工处理。"}, timeout=2)
                                        # 可以把任务转回人工审计
                                    except: pass
                                continue
                                
                            initial_msg = f"🔬 **[故障诊断开始 - {errors_str}]**\n@WatcherBot 审计专员截获特工 **{stalled_agent}** 的关键崩溃报错！\n📜 **上下文:**\n`{context_logs}`\n💡 正在启动自动化【链路自检与备用降级】策略（第 {repair_attempts+1} 次挽救）..."
                            await notify_discord(initial_msg)
                            
                            is_openclaw = stalled_agent and ('openclaw' in stalled_agent.lower())
                            
                            if is_openclaw:
                                # 从 assignee 中提取子会话（如 openclaw/growth-expert → growth-expert）
                                diag_session = stalled_agent.split('/')[-1] if '/' in stalled_agent else 'main'
                                prompt = (
                                    f"任务严重故障阻断报告：系统拦截到你在处理任务时遇到了 {errors_str} 问题。\n\n"
                                    f"[修复指令 - 强制执行]:\n"
                                    f"不要再次无脑请求相同 URL！请立刻进行自检排查：\n"
                                    f"1. 动用 bash 工具执行 curl/ping 检查网络节点连通性及代理状态；\n"
                                    f"2. 如果发现是目标封禁或拦截，立刻更换备用访问源或改变访问层级模型（如降低并发）。\n"
                                    f"3. 立即出具修复报告并恢复作业。"
                                )
                                reply = await talk_to_openclaw(prompt, session=diag_session)
                                if reply:
                                    eval_prompt = f"你是 HyperTask 指挥官，这是特工 {stalled_agent} 针对 {errors_str} 故障进行的排查与修复汇报：\n\n{reply}\n\n请用强硬军事口吻进行评审：1.这修复可行吗？2.命令他带着此方案回归本源任务。"
                                    audit_reply = await ask_gemini(eval_prompt)

                                    await notify_discord(f"🤖 **[{stalled_agent} 排查汇报]**\n{reply[:500]}\n\n⚖️ **[指挥官判决]**\n{audit_reply}")

                                    await talk_to_openclaw(f"最终指令，禁止辩驳：\n\n{audit_reply}\n\n带着以上方案，马上回去把中断的任务【{task_title}】给我做完！", session=diag_session)
                                    
                                    if step_id:
                                        try:
                                            requests.put(f"{HUB_URL}/api/v2/steps/{step_id}", json={"status": "DONE", "logs": f"故障汇报:\n{reply}\n\n指挥决断:\n{audit_reply}"}, timeout=2)
                                        except: pass
                                else:
                                    await notify_discord(f"❌ 特工 {stalled_agent} 处于深度昏迷，修复指令未获响应。")
                                    if step_id:
                                        try:
                                            requests.put(f"{HUB_URL}/api/v2/steps/{step_id}", json={"status": "FAILED", "logs": "特工无响应"}, timeout=2)
                                        except: pass
                            else:
                                # 非 openclaw 的轻量 API Bot
                                nudge_payload = (
                                    f"🚨 [严重阻断告警 - {errors_str}]\n"
                                    f"任务【{task_title}】崩溃！日志: {context_logs}\n"
                                    f"要求：停止重试错误API，启动降级容错机制绕过，并继续汇报任务。这是系统级强制命令！"
                                )
                                try:
                                    requests.post(
                                        f"{HUB_URL}/api/v2/nudge",
                                        json={"target": stalled_agent, "message": nudge_payload, "task_id": task_id},
                                        timeout=5
                                    )
                                except: pass
                                await notify_discord(f"🚨 **[系统异步抢修]** 已向节点 `{stalled_agent}` 下发自动应急隔离与复苏指令。")
                                if step_id:
                                    try:
                                        requests.put(f"{HUB_URL}/api/v2/steps/{step_id}", json={"status": "DONE", "logs": "已通过 Nudge 广播应急恢复指令"}, timeout=2)
                                    except: pass
                            continue
                        elif action == "SUPERVISE_STALL":
                            stalled_agent = data.get('stalled_agent')
                            stall_minutes = data.get('stall_minutes', 0)
                            task_id = data.get('task_id')
                            context_logs = data.get('context_logs', '无日志')
                            task_title = data.get('task_title', '未知任务')
                            
                            initial_msg = f"🕵️ **[进度审计 - 任务停滞]**\n@WatcherBot 特工 **{stalled_agent}**，任务【{task_title}】停滞 {stall_minutes} 分钟！\n📜 **最近作业:**\n`{context_logs}`\n💡 系统正在进行自动化交叉盘询..."
                            await notify_discord(initial_msg)
                            
                            step_id = None
                            if task_id:
                                try:
                                    res = requests.post(f"{HUB_URL}/api/v2/tasks/{task_id}/steps", json={"name": "🚨 督察官发起了联合排查会商..."}, timeout=2).json()
                                    step_id = res.get("step_id")
                                except: pass
                            
                            is_openclaw = stalled_agent and ('openclaw' in stalled_agent.lower())

                            # ✅ 统一催办格式：所有特工收到相同的详细催办消息
                            nudge_prompt = (
                                f"⚠️ [系统审计介入 - 任务停滞盘询]\n"
                                f"特工 {stalled_agent}，你负责的任务【{task_title}】\n"
                                f"（task_id: {task_id}）已停滞 {stall_minutes} 分钟。\n"
                                f"请立即确认:\n"
                                f"1. 你是否仍在执行该任务？\n"
                                f"2. 遇到了什么障碍？\n"
                                f"3. 调用 POST /api/v2/tasks/{task_id}/progress 上报当前进度。\n"
                                f"不响应将触发人工介入。"
                            )

                            if is_openclaw:
                                # 从 assignee 中提取子会话（如 openclaw/growth-expert → growth-expert）
                                openclaw_session = stalled_agent.split('/')[-1] if '/' in stalled_agent else 'main'
                                reply = await talk_to_openclaw(nudge_prompt, session=openclaw_session)
                                if reply:
                                    eval_prompt = f"你是 HyperTask 审计专员指挥官。你的特工 {stalled_agent} 刚刚在任务卡住时汇报：\n\n{reply}\n\n请针对他的汇报给出一小段精炼的审计意见和战术指导。语气专业、威严。不用任何自我介绍，直接给出指导意见。"
                                    audit_reply = await ask_gemini(eval_prompt)
                                    
                                    await notify_discord(f"🤖 **[{stalled_agent} 汇报]**\n{reply[:500]}\n\n💡 **[审计意见]**\n{audit_reply}")
                                    
                                    await talk_to_openclaw(f"这是针对你刚才汇报的审计意见和战术指导，请立即按此执行：\n\n{audit_reply}", session=openclaw_session)
                                    
                                    if step_id:
                                        try:
                                            requests.put(f"{HUB_URL}/api/v2/steps/{step_id}", json={"status": "DONE", "logs": f"{stalled_agent} 汇报:\n{reply}\n\n审计意见:\n{audit_reply}"}, timeout=2)
                                            requests.post(f"{HUB_URL}/api/v2/tasks/{task_id}/progress", json={"progress": 10, "status": "RUNNING"}, timeout=2)
                                        except: pass
                                else:
                                    await notify_discord(f"❌ {stalled_agent} 保持沉默，未收到有效回复。")
                                    if step_id:
                                        try:
                                            requests.put(f"{HUB_URL}/api/v2/steps/{step_id}", json={"status": "FAILED", "logs": f"未收到 {stalled_agent} 有效回复"}, timeout=2)
                                        except: pass
                            else:
                                # 对监听型特工 (gemini-bot/deepseek) 使用专用催办接口
                                # 不走 /api/v2/commands（会创建新任务），改用 /api/v2/nudge
                                try:
                                    requests.post(
                                        f"{HUB_URL}/api/v2/nudge",
                                        json={"target": stalled_agent, "message": nudge_prompt, "task_id": task_id},
                                        timeout=5
                                    )
                                    print(f"📣 [Nudge via /nudge] → [{stalled_agent}] task={task_id[:8]}")
                                except Exception as nudge_err:
                                    print(f"⚠️ Nudge failed: {nudge_err}")
                                
                                await notify_discord(f"🚨 **[系统异步介入]** 已向节点 `{stalled_agent}` 下发关于任务【{task_title}】的防挂起查询信号（未创建新任务）。")
                                if step_id:
                                    try:
                                        requests.put(f"{HUB_URL}/api/v2/steps/{step_id}", json={"status": "DONE", "logs": f"由于 {stalled_agent} 为监听型特工，已通过 /nudge 端点下发异步盘询指令（无新任务创建）。"}, timeout=2)
                                    except: pass
                            continue
                        print(f"📡 控制信号: {action}")
                    
                    elif msg_type == "TASK_CREATED":
                        # 任务看板更新，记录但不处理
                        task = data.get("task", {})
                        assignee = task.get("assignee", "")
                        if assignee != AGENT_ID:
                            print(f"📋 看板更新: [{assignee}] {task.get('title', '')[:40]}")
        
        except websockets.exceptions.ConnectionClosed:
            print("⚠️ Hub 连接断开，5秒后重连...")
            await asyncio.sleep(5)
        except ConnectionRefusedError:
            print("⚠️ Hub 未启动，10秒后重试...")
            await asyncio.sleep(10)
        except Exception as e:
            print(f"❌ WS 异常: {e}")
            await asyncio.sleep(5)


# ========== 启动入口 ==========
async def main():
    print("=" * 60)
    print("  HyperTask Hub 审计专员 (Supervisor Agent)")
    print("  AI: Gemini CLI (gemini-3-flash-preview)")
    print("  职责: 任务调度 + 审计评审 (不执行代码)")
    print("=" * 60)
    
    # 启动心跳线程
    start_heartbeat()
    
    # 启动 Discord Bot
    asyncio.create_task(discord_client.start(DISCORD_TOKEN))
    
    # 进入 WebSocket 主循环
    await ws_main_loop()



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 审计专员已退出")
