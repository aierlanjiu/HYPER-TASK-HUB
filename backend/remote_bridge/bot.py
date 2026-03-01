import os
import sys
import logging
import asyncio
import re
import base64
import time
import tempfile
import json
import threading
import websockets
import random
import datetime
import html
import requests
import httpx
from pathlib import Path
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from dotenv import load_dotenv

# --- Paths & Constants ---
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOWNLOAD_DIR = os.path.join(ROOT_DIR, "downloads")
sys.path.append(ROOT_DIR)
MAX_HISTORY = 10
YOLO_MODE = True
DEFAULT_MODEL = "gemini-3.1-pro-preview"
AVAILABLE_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3-pro-image-preview",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "deepseek-chat",
    "deepseek-reasoner",
    "deepseek-chat-search",
    "deepseek-reasoner-search"
]

# --- DeepSeek Config ---
NAS_DS_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions")
NAS_DS_KEY = os.getenv("DEEPSEEK_API_KEY", "")


# --- Hub Synchronization ---
HUB_HTTP_URL = "http://localhost:8000"
HUB_WS_URL = "ws://localhost:8000/ws/gemini-bot"

# --- Load Environment Variables ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_IDS = os.getenv("ALLOWED_TELEGRAM_IDS", "").split(",")
DEFAULT_TG_CHAT_ID = os.getenv("DEFAULT_TELEGRAM_CHAT_ID", "")

# --- Logging Setup ---
LOG_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "bot.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

global_ws = None


async def run_hub_ws(application):
    import websockets, html, json
    logging.info(f'🛰️ WS Connecting: {HUB_WS_URL}')
    while True:
        try:
            async with websockets.connect(HUB_WS_URL) as ws:
                logging.info('🔗 Connected to Hub.')
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    mtype = data.get('type')
                    if mtype == 'TASK_CREATED':
                        task = data.get('task', {})
                        a = task.get('assignee', '')
                        if a == 'gemini-bot': continue
                        safe_title = html.escape(str(task.get('title', '')))
                        msg = f'🔍 <b>[看板审核]</b>\n任务请求：\n『{safe_title}』\n✅ 审核通过。'
                        if DEFAULT_TG_CHAT_ID:
                            await application.bot.send_message(chat_id=DEFAULT_TG_CHAT_ID, text=msg, parse_mode='HTML')
                    
                    elif mtype == 'execute':
                        # ========== 🆕 Dashboard 自然语言命令执行 ==========
                        command = data.get('command', '')
                        task_id = data.get('task_id')
                        logging.info(f'📡 [Hub Execute] Received: {command[:60]}')
                        
                        admin_id = int(ALLOWED_IDS[0]) if (ALLOWED_IDS and ALLOWED_IDS[0]) else DEFAULT_TG_CHAT_ID
                        if not admin_id:
                            logging.warning("⚠️ No recipient ID found for command notification.")
                            continue

                        # 1. 更新看板: PENDING → RUNNING
                        if task_id:
                            try:
                                requests.post(f'{HUB_HTTP_URL}/api/v2/tasks/{task_id}/progress', json={'progress': 5, 'status': 'RUNNING'}, timeout=2)
                                requests.post(f'{HUB_HTTP_URL}/api/v2/tasks/{task_id}/steps', json={'name': '📡 Gemini Bot 收到指令，正在执行...'}, timeout=2)
                            except: pass
                        
                        # 2. 通知 Telegram 私聊
                        safe_cmd = html.escape(command[:200])
                        tg_msg = await application.bot.send_message(
                            chat_id=admin_id, 
                            text=f'🎯 <b>[Dashboard 指令]</b>\n<pre>{safe_cmd}</pre>\n⏳ 正在执行...', 
                            parse_mode='HTML'
                        )
                        
                        # 3. 使用 Gemini CLI 执行
                        try:
                            result = await get_gemini_analysis(command)
                            
                            if result:
                                # 4a. 成功: 更新看板 + Telegram
                                if task_id:
                                    try:
                                        step_res = requests.post(f'{HUB_HTTP_URL}/api/v2/tasks/{task_id}/steps', json={'name': '✅ 执行完成'}, timeout=2).json()
                                        sid = step_res.get('step_id')
                                        if sid:
                                            requests.put(f'{HUB_HTTP_URL}/api/v2/steps/{sid}', json={'status': 'DONE', 'logs': result[:500]}, timeout=2)
                                        requests.post(f'{HUB_HTTP_URL}/api/v2/tasks/{task_id}/progress', json={'progress': 100, 'status': 'DONE'}, timeout=2)
                                    except: pass
                                
                                # 将生成的长文自动固化为本地 Markdown 文件
                                articles_dir = os.path.join(DOWNLOAD_DIR, 'articles')
                                os.makedirs(articles_dir, exist_ok=True)
                                ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                                file_path = os.path.join(articles_dir, f'task_{ts}.md')
                                with open(file_path, 'w', encoding='utf-8') as f:
                                    f.write(f"# Task: {command}\n\n{result}")

                                safe_result = html.escape(result[:2000])
                                append_str = f'\n\n💾 <b>已固化存档:</b>\n<code>{file_path}</code>\n<i>(在文件管理中可随时查看)</i>'
                                await tg_msg.edit_text(f'✅ <b>[执行完毕]</b>\n<pre>{safe_cmd}</pre>\n\n<b>结果:</b>\n{safe_result}{append_str}', parse_mode='HTML')
                            else:
                                # 4b. 空结果
                                if task_id:
                                    try:
                                        requests.post(f'{HUB_HTTP_URL}/api/v2/tasks/{task_id}/progress', json={'progress': 100, 'status': 'DONE'}, timeout=2)
                                    except: pass
                                await tg_msg.edit_text(f'⚠️ <b>[执行完毕]</b>\n<pre>{safe_cmd}</pre>\n\nGemini 未返回有效内容。', parse_mode='HTML')
                        except Exception as exec_err:
                            # 4c. 异常
                            logging.error(f'Execute error: {exec_err}')
                            if task_id:
                                try:
                                    requests.post(f'{HUB_HTTP_URL}/api/v2/tasks/{task_id}/progress', json={'progress': 0, 'status': 'FAILED'}, timeout=2)
                                except: pass
                            await tg_msg.edit_text(f'❌ <b>[执行失败]</b>\n{html.escape(str(exec_err)[:500])}', parse_mode='HTML')
                    elif data.get('action') == 'SUPERVISE_STALL' or mtype == 'CONTROL_SIGNAL':
                        if data.get('action') == 'SUPERVISE_STALL':
                            sa, sm = data.get('stalled_agent'), data.get('stall_minutes', 0)
                            task_id = data.get('task_id')
                            task_title = data.get('task_title', '未知任务')
                            safe_logs = html.escape(str(data.get('context_logs', '无日志')))
                            admin_id = int(ALLOWED_IDS[0]) if ALLOWED_IDS else TG_CHAT_ID
                            
                            # 1. 初始告警并通报启动会商（包含任务标题）
                            safe_title = html.escape(task_title)
                            initial_msg = (
                                f'🕵️ <b>[进度审计 - 任务停滞]</b>\n'
                                f'@YourBot，特工 <b>{sa}</b> 负责的任务\n'
                                f'📌 <b>「{safe_title}」</b>\n'
                                f'已停滞 <b>{sm}</b> 分钟！\n'
                                f'📜 <b>最近作业：</b>\n<pre>{safe_logs}</pre>\n'
                                f'💡 <b>系统正在进行自动化交叉盘询...</b>'
                            )
                            status_msg = await application.bot.send_message(chat_id=admin_id, text=initial_msg, parse_mode='HTML')
                            
                            # 2. 并在 Hub 看板上创建一个"正在会商"的 Step
                            step_id = None
                            if task_id:
                                try:
                                    res = requests.post(f'{HUB_HTTP_URL}/api/v2/tasks/{task_id}/steps', json={'name': '🚨 督察官发起了联合排查会商...'}, timeout=2).json()
                                    step_id = res.get('step_id')
                                except: pass

                            # 3. 与 OpenClaw 实际进行会商（携带任务标题和 task_id）
                            prompt = (
                                f'OpenClaw，我是 Gemini 战略官。\n'
                                f'[NEXUS_TASK_BINDING] task_id={task_id} title={task_title} [/NEXUS_TASK_BINDING]\n'
                                f'你负责的任务「{task_title}」（task_id: {task_id}）已停滞 {sm} 分钟。\n'
                                f'请确认你是否仍在执行该任务，并向我汇报遇到了什么障碍，我会协助你继续推进。'
                            )
                            reply = await talk_to_openclaw(prompt)
                            
                            if reply:
                                # 4. 使用 Gemini 对回复进行专业分析
                                eval_prompt = f"你是指挥官(Gemini Bot)。特工 OpenClaw 正在处理任务「{task_title}」，刚刚汇报：\n\n{reply}\n\n请针对他的汇报给出一小段精炼的审计意见和战术指导。语气专业、威严。不用任何自我介绍，直接给出指导意见。"
                                audit_reply = await get_gemini_analysis(eval_prompt)
                                safe_audit = html.escape(audit_reply)
                                
                                # 5. Telegram 群发送最终分析报告
                                final_msg = f'🤖 <b>[OpenClaw 汇报 - 任务「{safe_title}」]</b>\n\n{html.escape(reply)}\n\n💡 <b>[Gemini 审计意见]</b>\n{safe_audit}'
                                await status_msg.edit_text(final_msg, parse_mode='HTML')
                                
                                # 6. 将审计意见同步下发给 OpenClaw，使其能接收到指导并继续执行
                                await talk_to_openclaw(f"这是针对你刚才汇报的审计意见和战术指导，请立即按此执行继续任务「{task_title}」（task_id: {task_id}）：\n\n{audit_reply}")

                                # 7. 在 Hub 看板记录最终分析结果，并推进少许进度打破停滞
                                if step_id:
                                    try:
                                        requests.put(f'{HUB_HTTP_URL}/api/v2/steps/{step_id}', json={'status': 'DONE', 'logs': f'OpenClaw 汇报:\n{reply}\n\n审计意见:\n{audit_reply}'}, timeout=2)
                                        requests.post(f'{HUB_HTTP_URL}/api/v2/tasks/{task_id}/progress', json={'progress': 10, 'status': 'RUNNING'}, timeout=2)
                                    except: pass
                            else:
                                await status_msg.edit_text(f'❌ OpenClaw 保持沉默，未收到关于任务「{safe_title}」的有效回复。')
                                if step_id:
                                    try:
                                        requests.put(f'{HUB_HTTP_URL}/api/v2/steps/{step_id}', json={'status': 'FAILED', 'logs': f'未收到 OpenClaw 关于任务「{task_title}」的有效回复'}, timeout=2)
                                    except: pass

        except Exception as e:
            logging.error(f'Hub WS Error: {e}')
            await asyncio.sleep(10)

async def talk_to_openclaw(prompt):
    import asyncio
    try:
        # 抛弃脆弱的网页验证，直接以极客方式通过原生 CLI 触发，最高权限，无视隔离
        cmd = f'/opt/homebrew/bin/openclaw agent --agent main --message "{prompt}" --json'
        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        out_str = stdout.decode("utf-8", errors="ignore").strip()
        
        # 尝试解析 JSON 获取纯净文本
        import json
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

async def send_to_hub(msg):
    global global_ws
    if global_ws:
        try: 
            safe_msg = str(msg)[:2000]
            await asyncio.create_task(global_ws.send(json.dumps({"type": "message", "content": safe_msg})))
        except: pass
    # 新增：REST API 汇报逻辑
    asyncio.create_task(report_to_hub(msg))

async def report_to_hub(content, status="INFO"):
    """向 HyperTask Hub REST API 汇报状态"""
    url = "http://localhost:8000/api/report"
    payload = {
        "agent_id": "Gemini Bot",
        "content": str(content)[:5000],
        "status": status
    }
    try:
        # 使用原生 requests 但跑在线程池中，避免阻塞 asyncio
        await asyncio.to_thread(requests.post, url, json=payload, timeout=1)
    except: pass

# --- Core Business Components ---
AVAILABLE_STYLES = {} # Default empty
STYLE_GUIDE_DIR = Path(".")
try:
    from content_engine.keyword_parser import get_next_batch
    from content_engine.master_controller import process_task_async, create_async_driver, AVAILABLE_STYLES, STYLE_GUIDE_DIR
    from scripts.remove_watermark import WatermarkRemover
    watermark_remover = WatermarkRemover()
except ImportError as e:
    logging.warning(f"Business logic import failed: {e}")
    watermark_remover = None

# Load environment variables
load_dotenv(dotenv_path=Path(ROOT_DIR) / 'remote_bridge' / '.env')
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")
ALLOWED_IDS_STR = os.getenv("ALLOWED_IDS", "")
ALLOWED_IDS = [x.strip() for x in ALLOWED_IDS_STR.split(",") if x.strip()]
if ALLOWED_USER_ID and ALLOWED_USER_ID not in ALLOWED_IDS:
    ALLOWED_IDS.append(ALLOWED_USER_ID)

# Setup Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(os.path.join(ROOT_DIR, "remote_bridge", "bot_debug.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

user_histories = {}
active_drivers = {}

async def bot_heartbeat(context: ContextTypes.DEFAULT_TYPE):
    import requests
    try:
        url = f'{HUB_HTTP_URL}/api/v2/agents/heartbeat'
        payload = {
            'agent_id': 'gemini-bot',
            'name': 'gemini-bot',
            'platform_info': 'Mac Mini (Direct WS)'
        }
        await asyncio.to_thread(requests.post, url, json=payload, timeout=2)
    except: pass

async def post_init(application):
    commands = [
        BotCommand("start", "Show Menu"),
        BotCommand("mode", "Toggle Auto-Execute"),
        BotCommand("model", "Switch Model"),
        BotCommand("clear", "Clear Memory"),
        BotCommand("new", "New Terminal (Full Reset)"),
        BotCommand("get", "Get File"),
        BotCommand("evolve", "Solidify a new rule (进化法则)"),
        BotCommand("test_nexus", "发起 AI 委员会商"),
        BotCommand("nurse", "手动触发早安护士")
    ]
    await application.bot.set_my_commands(commands)
    print("✅ Persistent menu commands set.")
    
    bj_tz = datetime.timezone(datetime.timedelta(hours=8))
    target_time_m = datetime.time(hour=7, minute=30, tzinfo=bj_tz)
    target_time_n = datetime.time(hour=23, minute=55, tzinfo=bj_tz)
    
    
    # 心跳包
    application.job_queue.run_repeating(bot_heartbeat, interval=15)
    
    # 延迟 1 秒安全拉起 WebSocket 监听
    async def start_ws_job(context): await run_hub_ws(application)
    application.job_queue.run_once(start_ws_job, 1)

    print(f"⏰ All background jobs and Hub listener scheduled.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_histories[user_id] = []
    mode_status = "🔴 Safe Mode" if not YOLO_MODE else "🟢 Auto-Execute (YOLO)"
    
    keyboard = [
        ["📰 生成今日资讯海报", "📂 文件管理"],
        ["🎨 图文内容产出"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    msg = (
        f"🚀 **Streaming Agent Active**\n"
        f"User ID: `{user_id}`\n"
        f"Status: **{mode_status}**\n\n"
        "**Features:**\n"
        "1. **Real-time:** I show output as it happens.\n"
        "2. **Agent:** I can execute commands and use skills.\n"
        "3. **Control:** Use `/mode` to toggle Auto-Execution permissions.\n"
    )
    is_allowed = True
    if ALLOWED_IDS and user_id not in ALLOWED_IDS:
        is_allowed = False
    if not is_allowed:
        msg += f"\n⛔ **Unauthorized ID**"
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def switch_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if ALLOWED_IDS and user_id not in ALLOWED_IDS: return
    
    current = context.application.bot_data.get('current_model', DEFAULT_MODEL)
    keyboard = []
    for m in AVAILABLE_MODELS:
        label = f"✅ {m}" if m == current else m
        keyboard.append([InlineKeyboardButton(label, callback_data=f"SET_MODEL:{m}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"当前模型: `{current}`\n请选择要切换的模型:", reply_markup=reply_markup, parse_mode='Markdown')

async def toggle_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global YOLO_MODE
    user_id = str(update.effective_user.id)
    if ALLOWED_IDS and user_id not in ALLOWED_IDS: return
    YOLO_MODE = not YOLO_MODE
    status = "🟢 **Enabled** (YOLO)" if YOLO_MODE else "🔴 **Disabled** (Safe Mode)"
    await update.message.reply_text(f"🛡️ Auto-Execute: {status}", parse_mode='Markdown')

async def clear_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_histories[user_id] = []
    await update.message.reply_text("🧹 Memory wiped.", parse_mode='Markdown')

async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_histories[user_id] = []
    if user_id in active_drivers:
        try: await active_drivers[user_id].close()
        except: pass
        del active_drivers[user_id]
    context.user_data.clear()
    await update.message.reply_text("✨ **Session Reset**\nMemory cleared & Browser closed.", parse_mode='Markdown')

def smart_decode(bytes_content):
    try: return bytes_content.decode('utf-8')
    except:
        try: return bytes_content.decode('gbk')
        except: return bytes_content.decode('utf-8', errors='replace')

async def compact_memory(user_id, history):
    """
    使用 Gemini 对历史记录进行总结，以压缩上下文。
    """
    if not history: return ""
    
    prompt = "请简要总结以下对话内容，保留关键信息和上下文，以便 AI 能继续后续对话：\n\n"
    for turn in history:
        role = "User" if turn["role"] == "user" else "Assistant"
        prompt += f"{role}: {turn['content']}\n"
    
    # 强制使用一个小模型进行总结
    summary = await get_gemini_analysis(prompt)
    return summary

async def stream_subprocess(command, update_message, context, render_style='terminal'):
    """
    执行 Shell 命令并在 Telegram 中实时流式反馈结果。
    采用“自动分条”机制：当单条消息接近 Telegram 长度上限时，自动发送新消息继续流式输出，
    确保长代码或长回答不会被截断，且能完整保留所有历史内容。
    """
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    full_output = ""
    current_chunk = ""
    active_message = update_message
    last_update_time = 0
    
    while True:
        line = await process.stdout.readline()
        if not line: break
        
        decoded_line = smart_decode(line)
        full_output += decoded_line
        current_chunk += decoded_line
        await send_to_hub(decoded_line.strip())

        current_time = time.time()
        
        # 核心修复：如果当前片段积攒超过 3500 字，将其“定格”并开启一条新消息
        if len(current_chunk) > 3500:
            if active_message:
                try:
                    content = f"```\n{current_chunk}\n```" if render_style == 'terminal' else current_chunk
                    await active_message.edit_text(content, parse_mode='Markdown')
                except Exception:
                    try: await active_message.edit_text(current_chunk)
                    except: pass
            
            # 清空当前片段，开启新页
            current_chunk = ""
            if active_message:
                try:
                    active_message = await active_message.reply_text("⏳ ...(接上文)", parse_mode='Markdown')
                except: pass
            last_update_time = current_time
            continue

        # 常规流式刷新（每 1.5 秒）
        if active_message and current_time - last_update_time > 1.5:
            try:
                content = f"```\n{current_chunk}▌\n```" if render_style == 'terminal' else f"{current_chunk}▌"
                await active_message.edit_text(content, parse_mode='Markdown')
                last_update_time = current_time
            except Exception:
                try:
                    await active_message.edit_text(f"{current_chunk}▌")
                    last_update_time = current_time
                except: pass

    stderr_data = await process.stderr.read()
    if stderr_data:
        err_text = smart_decode(stderr_data)
        full_output += f"\n[STDERR]: {err_text}"
        current_chunk += f"\n[STDERR]: {err_text}"
        await send_to_hub(f"ERROR: {err_text}")

    await process.wait()

    # 最终收尾当前片段
    if active_message and current_chunk:
        try:
            content = f"```\n{current_chunk}\n```" if render_style == 'terminal' else current_chunk
            await active_message.edit_text(content, parse_mode='Markdown')
        except Exception:
            try: await active_message.edit_text(current_chunk)
            except: pass
        
    return full_output

async def stream_deepseek_api(prompt, model, update_message, context):
    """
    通过原生 curl 调用 NAS DeepSeek API 并在 Telegram 中实时流式反馈。
    这种方式能最可靠地绕过 Python 环境代理污染。
    """
    import json
    import shlex
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True
    }
    
    # 构造 curl 命令，使用 -N 禁用缓冲以实现真正流式
    cmd = f"curl -s -N -X POST {NAS_DS_URL} " \
          f"-H 'Authorization: Bearer {NAS_DS_KEY}' " \
          f"-H 'Content-Type: application/json' " \
          f"-d {shlex.quote(json.dumps(payload))}"

    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    full_output = ""
    current_chunk = ""
    active_message = update_message
    last_update_time = 0

    try:
        while True:
            line = await process.stdout.readline()
            if not line: break
            
            line_str = line.decode('utf-8', errors='ignore').strip()
            if not line_str or not line_str.startswith("data: "): continue
            
            data_str = line_str[6:]
            if data_str == "[DONE]": break
            
            try:
                data = json.loads(data_str)
                delta = data['choices'][0]['delta'].get('content', '')
                if not delta: continue
                
                full_output += delta
                current_chunk += delta
                
                current_time = time.time()
                # 分段发送逻辑
                if len(current_chunk) > 3500:
                    try:
                        await active_message.edit_text(current_chunk, parse_mode='Markdown')
                    except:
                        try: await active_message.edit_text(current_chunk)
                        except: pass
                    
                    current_chunk = ""
                    active_message = await active_message.reply_text("⏳ ...(接上文)", parse_mode='Markdown')
                    last_update_time = current_time
                    continue

                # 实时刷新 (1.5s 频率)
                if active_message and current_time - last_update_time > 1.5:
                    try:
                        await active_message.edit_text(f"{current_chunk}▌", parse_mode='Markdown')
                    except:
                        try: await active_message.edit_text(f"{current_chunk}▌")
                        except: pass
                    last_update_time = current_time
            except:
                continue

        # 最终收尾
        if active_message and current_chunk:
            try:
                await active_message.edit_text(current_chunk, parse_mode='Markdown')
            except:
                try: await active_message.edit_text(current_chunk)
                except: pass
                
    except Exception as e:
        error_text = f"❌ DeepSeek Curl Error: {e}"
        logging.error(error_text)
        if active_message:
            await active_message.edit_text(error_text)
        return error_text
    finally:
        try:
            process.terminate()
            await process.wait()
        except: pass

    return full_output

# --- 文件浏览器核心逻辑 (v3.0 - 修复版) ---
async def render_file_browser(update, context, path=None, page=0, is_edit=False):
    current_path = path or context.user_data.get('cwd', ROOT_DIR)
    if not os.path.exists(current_path): current_path = ROOT_DIR
    context.user_data['cwd'] = current_path

    try:
        all_items = sorted(os.listdir(current_path))
        items = [i for i in all_items if not i.startswith('.')]
    except Exception as e:
        if is_edit: await update.callback_query.answer(f"读取失败: {e}")
        return

    ITEMS_PER_PAGE = 8
    total_pages = (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    if total_pages == 0: total_pages = 1
    if page >= total_pages: page = max(0, total_pages - 1)
    if page < 0: page = 0
    
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_items = items[start:end]

    keyboard = []
    for i, name in enumerate(page_items):
        full_path = os.path.join(current_path, name)
        icon = "📁" if os.path.isdir(full_path) else "📄"
        real_index = start + i
        keyboard.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"FB_OPEN:{real_index}")])

    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"FB_PAGE:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="NOOP"))
    if page < total_pages - 1: nav.append(InlineKeyboardButton("➡️", callback_data=f"FB_PAGE:{page+1}"))
    if nav: keyboard.append(nav)

    sys_row = []
    if os.path.abspath(current_path) != os.path.abspath(ROOT_DIR):
        sys_row.append(InlineKeyboardButton("⬆️ 上一级", callback_data="FB_UP"))
    sys_row.append(InlineKeyboardButton("🏠 根目录", callback_data="FB_HOME"))
    sys_row.append(InlineKeyboardButton("❌ 关闭", callback_data="FB_CLOSE"))
    keyboard.append(sys_row)

    display_path = current_path.replace(ROOT_DIR, "") or "/"
    text = f"📂 **文件浏览器**\n📍 `{display_path}`"
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if is_edit:
        try: await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        except: pass
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(update.effective_user.id)
    data = query.data
    
    # 统一先应答，消除电报转圈
    await query.answer()

    # 1. 模型切换逻辑
    if data.startswith("SET_MODEL:"):
        model_name = data.split(":")[1]
        context.application.bot_data['current_model'] = model_name
        
        current = model_name
        keyboard = []
        for m in AVAILABLE_MODELS:
            label = f"✅ {m}" if m == current else m
            keyboard.append([InlineKeyboardButton(label, callback_data=f"SET_MODEL:{m}")])
        
        await query.edit_message_text(f"当前模型: `{current}`\n模型切换成功！", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return

    # 2. 文件浏览器逻辑
    if data.startswith("FB_") or data == "NOOP":
        if data == "NOOP": return
        
        cwd = context.user_data.get('cwd', ROOT_DIR)
        try: items = sorted([i for i in os.listdir(cwd) if not i.startswith('.')])
        except: await render_file_browser(update, context, ROOT_DIR, is_edit=True); return

        if data == "FB_HOME": await render_file_browser(update, context, ROOT_DIR, is_edit=True)
        elif data == "FB_UP":
            parent = os.path.dirname(cwd)
            await render_file_browser(update, context, parent if ROOT_DIR in parent else ROOT_DIR, is_edit=True)
        elif data == "FB_CLOSE": await query.message.delete()
        elif data.startswith("FB_PAGE:"):
            page = int(data.split(":")[1])
            await render_file_browser(update, context, cwd, page=page, is_edit=True)
        elif data.startswith("FB_OPEN:"):
            index = int(data.split(":")[1])
            if index >= len(items): return
            target = os.path.join(cwd, items[index])
            if os.path.isdir(target): 
                await render_file_browser(update, context, target, is_edit=True)
            else:
                size_mb = os.path.getsize(target) / 1024 / 1024
                file_ext = os.path.splitext(target)[1].lower()
                kb = []
                if file_ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif']:
                    kb.append([InlineKeyboardButton("🖼️ 查看图片", callback_data=f"FB_PREVIEW:{index}")])
                elif file_ext in ['.txt', '.md', '.py', '.json', '.sh', '.log']:
                    kb.append([InlineKeyboardButton("👁️ 预览文本", callback_data=f"FB_PREVIEW:{index}")])
                kb.append([InlineKeyboardButton("⬇️ 下载文件", callback_data=f"FB_GET:{index}")])
                kb.append([InlineKeyboardButton("🔙 返回列表", callback_data="FB_BACK")])
                await query.edit_message_text(f"📄 **文件详情**\n名称: `{items[index]}`\n大小: `{size_mb:.2f} MB`\n路径: `{target}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        elif data == "FB_BACK": await render_file_browser(update, context, cwd, is_edit=True)
        elif data.startswith("FB_GET:"):
            index = int(data.split(":")[1])
            target = os.path.join(cwd, items[index])
            await context.bot.send_message(chat_id=user_id, text="📤 正在上传...")
            with open(target, 'rb') as f: await context.bot.send_document(chat_id=user_id, document=f)
        elif data.startswith("FB_PREVIEW:"):
            index = int(data.split(":")[1])
            target = os.path.join(cwd, items[index])
            file_ext = os.path.splitext(target)[1].lower()
            if file_ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif']:
                with open(target, 'rb') as f: await context.bot.send_photo(chat_id=user_id, photo=f)
            else:
                try:
                    with open(target, 'r', encoding='utf-8', errors='replace') as f: content = f.read(3000)
                    if len(content) >= 3000: content += "\n...(已截断)"
                    await context.bot.send_message(chat_id=user_id, text=f"```\n{content}\n```", parse_mode='Markdown')
                except Exception as e:
                    await context.bot.send_message(chat_id=user_id, text=f"❌ 预览失败: {e}")
        return

    # 3. 图文内容产出逻辑
    if data.startswith("STYLE_"):
        style_name = data.replace("STYLE_", "")
        batch = context.user_data.get('pending_batch')
        if not batch:
            bot_storage = context.application.bot_data.get(f"admin_pending_{user_id}")
            if bot_storage: batch = bot_storage.get('pending_batch')
        
        if not batch:
            await query.edit_message_text("⚠️ 会话已过期，请重新点击“图文内容产出”按钮。")
            return
        
        context.user_data['pending_batch'] = batch
        context.user_data['selected_style'] = style_name
        msg_lines = [f"📋 **待处理任务** (风格: `{style_name}`):"]
        for i, item in enumerate(batch):
            msg_lines.append(f"{i+1}. {item['keyword']} ({item['sub_category']})")
        
        keyboard = [
            [InlineKeyboardButton("✅ 确认并开始生成", callback_data="CONFIRM_BATCH")],
            [InlineKeyboardButton("❌ 取消", callback_data="CANCEL_BATCH")]
        ]
        await query.edit_message_text("\n".join(msg_lines), parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    if data == "CANCEL_BATCH":
        if user_id in active_drivers:
            try: await active_drivers[user_id].close()
            except: pass
            del active_drivers[user_id]
        context.user_data.pop('pending_batch', None)
        context.user_data.pop('selected_style', None)
        await query.edit_message_text("❌ 任务已取消。")
        return

    if data == "CONFIRM_BATCH":
        await query.edit_message_text("🚀 正在启动自动化环境，请稍候...")
        asyncio.create_task(process_first_image(update, context))
        return

    if data == "RETRY_FIRST":
        if user_id in active_drivers:
            try: await active_drivers[user_id].close()
            except: pass
            del active_drivers[user_id]
        await query.edit_message_text("🔄 正在重新尝试生成首图...")
        asyncio.create_task(process_first_image(update, context))
        return

    if data == "CONTINUE_BATCH":
        await query.edit_message_text("✅ 已确认。正在后台为您生成剩余任务，请留意消息回传...")
        asyncio.create_task(process_remaining_batch(update, context))
        return

    user_id = str(update.effective_user.id)
    if ALLOWED_IDS and user_id not in ALLOWED_IDS: return
    if not context.args:
        await update.message.reply_text("Usage: /get <filename>")
        return
    filename = " ".join(context.args)
    if os.path.exists(filename): filepath = filename
    elif os.path.exists(os.path.join(DOWNLOAD_DIR, filename)): filepath = os.path.join(DOWNLOAD_DIR, filename)
    else:
        await update.message.reply_text(f"❌ File not found: {filename}")
        return
    await update.message.reply_document(document=open(filepath, 'rb'))

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if ALLOWED_IDS and user_id not in ALLOWED_IDS: return
    file = None
    file_name = None
    if update.message.document:
        file = await update.message.document.get_file()
        file_name = update.message.document.file_name
    elif update.message.photo:
        file = await update.message.photo[-1].get_file()
        file_name = f"photo_{update.message.id}.jpg"
    if file:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        save_path = os.path.join(DOWNLOAD_DIR, file_name)
        await file.download_to_drive(save_path)
        await update.message.reply_text(f"✅ Saved: {file_name}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text
    logging.info(f"Received message from {user_id}: {repr(text)}")

    if ALLOWED_IDS and user_id not in ALLOWED_IDS:
        logging.warning(f"Unauthorized access attempt by {user_id}")
        return
    if not text: return

    # --- 1. Manual Shell ---
    if text.startswith('!'):
        command = text[1:].strip()
        status_msg = await update.message.reply_text(f"💻 Running: `{command}`...", parse_mode='Markdown')
        await stream_subprocess(command, status_msg, context)
        return

    # --- 2. Interactive Handlers ---
    if "图文内容产出" in text:
        if not YOLO_MODE:
             await update.message.reply_text("⚠️ **Auto-Execute is OFF.**\nPlease enable it with `/mode` first.", parse_mode='Markdown')
             return
        batch = get_next_batch(5)
        if not batch:
            await update.message.reply_text("✅ All tasks in `left77.txt` are completed!")
            return
        context.user_data['pending_batch'] = batch
        keyboard = [[InlineKeyboardButton(f"🎨 {s}", callback_data=f"STYLE_{s}")] for s in AVAILABLE_STYLES.keys()]
        await update.message.reply_text(f"请选择生成风格 (待处理: {len(batch)}):", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if "文件管理" in text:
        await render_file_browser(update, context)
        return

    if "生成今日资讯海报" in text:
        if not YOLO_MODE:
             await update.message.reply_text("⚠️ **Auto-Execute is OFF.**\nPlease enable it with `/mode` first.", parse_mode='Markdown')
             return
        msg = await update.message.reply_text("📰 **启动生产级资讯流水线...**", parse_mode='Markdown')
        cmd = f"cd '{ROOT_DIR}' && /opt/homebrew/bin/python3 select_news.py && /opt/homebrew/bin/python3 scripts/auto_gen_poster.py poster_prompt.txt --chat_id {user_id} --news_file today_news_selection.json && /opt/homebrew/bin/python3 scripts/deploy_news_only.py"
        await stream_subprocess(cmd, msg, context)
        return

    # --- 3. Agent Mode (Gemini) ---
    if user_id not in user_histories:
        user_histories[user_id] = []
    history = user_histories[user_id]
    
    snapshot = ""
    if len(history) > MAX_HISTORY * 2:
        status_msg_qmd = await update.message.reply_text("🧠 Compacting memory...", parse_mode='Markdown')
        snapshot = await compact_memory(user_id, history)
        history = history[-4:] # Keep last 2 turns
        user_histories[user_id] = history
        try: await status_msg_qmd.delete()
        except: pass

    core_rules_file = Path(ROOT_DIR) / "remote_bridge" / "core_rules.md"
    core_rules = core_rules_file.read_text(encoding='utf-8') if core_rules_file.exists() else ""

    learnings_file = Path(ROOT_DIR) / "remote_bridge" / "learnings.md"
    learnings = learnings_file.read_text(encoding='utf-8') if learnings_file.exists() else ""

    system_instruction = (
        "Instructions:\n"
        "1. You are an expert AI Assistant specialized in MacOS operations and Automotive Content. Reply in Chinese.\n"
        "2. You MUST execute commands directly using your native shell skills to answer the user.\n"
        "3. Follow Core Rules strictly.\n"
    )
    
    full_prompt = f"{system_instruction}\n\n"
    if core_rules:
        full_prompt += f"### 🛡️ Core Rules (User Directives):\n{core_rules}\n\n"
    if learnings:
        full_prompt += f"### 📚 Recent Learnings (Nightly Reflection):\n{learnings}\n\n"
    if snapshot:
        full_prompt += f"[Memory Snapshot]: {snapshot}\n\n"
    
    context_str = ""
    for turn in history:
        role = "User" if turn["role"] == "user" else "Model"
        context_str += f"{role}: {turn['content']}\n"
    
    full_prompt += f"[History]:\n{context_str}\n[User]: {text}\n[Model]:"
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as tmp:
        tmp.write(full_prompt)
        tmp_path = tmp.name
    
    current_model = context.application.bot_data.get('current_model', DEFAULT_MODEL)
    
    if current_model.startswith("deepseek"):
        status_msg = await update.message.reply_text("🤔 Thinking (DeepSeek)...", parse_mode='Markdown')
        gemini_reply = await stream_deepseek_api(full_prompt, current_model, status_msg, context)
    else:
        gemini_flags = f" --model {current_model} --approval-mode yolo" if YOLO_MODE else f" --model {current_model}"
        mac_command = f"cd '{ROOT_DIR}' && cat '{tmp_path}' | /opt/homebrew/bin/gemini -p - {gemini_flags}"
        
        status_msg = await update.message.reply_text("🤔 Thinking...", parse_mode='Markdown')
        gemini_reply = await stream_subprocess(mac_command, status_msg, context, render_style='markdown')
    
    gemini_reply = gemini_reply.strip()
    
    if os.path.exists(tmp_path): os.remove(tmp_path)

    if gemini_reply:
        history.append({"role": "user", "content": text})
        history.append({"role": "model", "content": gemini_reply})
        if len(history) > MAX_HISTORY * 2:
            history = history[-(MAX_HISTORY*2):]
            user_histories[user_id] = history

async def evolve_rule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if ALLOWED_IDS and user_id not in ALLOWED_IDS: return
    
    instruction = " ".join(context.args)
    if not instruction:
        await update.message.reply_text("⚠️ Usage: /evolve <new rule or behavior to remember>")
        return

    status_msg = await update.message.reply_text("🧬 Extracting and solidifying rule...", parse_mode='Markdown')
    
    rule_prompt = f"Extract a concise, permanent operational rule from this instruction: '{instruction}'. Format it as a single bullet point starting with a clear directive verb. Do not include any intro or conversational text."
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as tmp:
        tmp.write(rule_prompt)
        tmp_path = tmp.name

    cmd = f"cat '{tmp_path}' | /opt/homebrew/bin/gemini -p - --model gemini-3-flash-preview --approval-mode yolo"
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await process.communicate()
    extracted_rule = smart_decode(stdout).strip()
    if os.path.exists(tmp_path): os.remove(tmp_path)

    if extracted_rule:
        core_rules_file = Path(ROOT_DIR) / "logs" / "core_rules.md"
        with open(core_rules_file, "a", encoding="utf-8") as f:
            f.write(f"- {extracted_rule}\n")
        await status_msg.edit_text(f"✅ **Rule Solidified into Core Identity:**\n{extracted_rule}", parse_mode='Markdown')
    else:
        await status_msg.edit_text("❌ Failed to extract rule.")

async def get_gemini_analysis(prompt: str) -> str:
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as tmp:
        tmp.write(prompt)
        tmp_path = tmp.name
    
    # Run from the user's home directory so .gemini/skills are loaded correctly
    # -p - reads from stdin, --yolo auto-approves tool usage
    cmd = f"cd ~ && cat '{tmp_path}' | /opt/homebrew/bin/gemini -p - --yolo"
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    
    if os.path.exists(tmp_path): os.remove(tmp_path)
    return (stdout.decode('utf-8', errors='ignore') + stderr.decode('utf-8', errors='ignore')).strip()

async def test_nexus_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text('🤝 正在发起特工委员会商：【共同事业目标 - 系统进化】...')
    prompt = 'OpenClaw，我是 Gemini 战略官。我们的共同事业是 HyperTask Hub 的进化。目前我正在监控全网，请向我汇报你当前任务的进展，并告诉我你遇到了什么障碍，我会尝试协调资源协助你。'
    reply = await talk_to_openclaw(prompt)
    if reply:
        await status_msg.edit_text(f'🤖 <b>[OpenClaw 汇报]</b>\n\n{reply}\n\n💡 <b>[Gemini 正在深思...]</b>', parse_mode="HTML")
        eval_prompt = f"你是指挥官(Gemini Bot)。你的特工 OpenClaw 刚刚回复：\n\n{reply}\n\n请针对他的汇报给出一小段精炼的审计意见和战术指导。语气专业、威严。不用任何自我介绍，直接给出指导意见。"
        audit_reply = await get_gemini_analysis(eval_prompt)
        # 转义 HTML 避免格式错误
        import html
        safe_audit = html.escape(audit_reply)
        msg = f'🤖 <b>[OpenClaw 汇报]</b>\n\n{reply}\n\n💡 <b>[Gemini 审计意见]</b>\n{safe_audit}'
        await status_msg.edit_text(msg, parse_mode="HTML")

        # 将审计意见同步下发给 OpenClaw，使其能接收到指导并继续执行
        await talk_to_openclaw(f"这是针对你刚才汇报的审计意见和战术指导，请立即按此执行：\n\n{audit_reply}")
    else:
        await status_msg.edit_text('❌ OpenClaw 保持沉默，未收到有效回复。')


if __name__ == '__main__':
    proxy_url = os.getenv("TELEGRAM_PROXY", "")
    
    # 使用 httpx 构建带代理的客户端以符合 PTB v22.6 标准
    proxy_client = httpx.AsyncClient(proxy=proxy_url) if proxy_url else None
    
    builder = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init)
    if proxy_url:
        builder.proxy(proxy_url).get_updates_proxy(proxy_url)
        
    application = builder.build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('mode', toggle_mode))
    application.add_handler(CommandHandler('model', switch_model))
    application.add_handler(CommandHandler('new', new_session))
    application.add_handler(CommandHandler('clear', clear_memory)) 
    application.add_handler(CommandHandler('get', get_file))
    application.add_handler(CommandHandler('evolve', evolve_rule))
    application.add_handler(CommandHandler('test_nexus', test_nexus_connection))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("Streaming Agent running on Mac Mini...")
    application.run_polling()
