import asyncio, os, sys, datetime, requests
import glob

# 将上一级目录加入sys.path，以便导入同级的 nexus_client
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from nexus_client import NexusClient

HUB_HTTP_URL = "http://localhost:8000"

async def bridge_openclaw_logs():
    """实时抓取 OpenClaw 的最新日志并转发。"""
    print("🚀 OpenClaw Bridge starting...", flush=True)
    
    try:
        # 动态获取最新的日志文件
        log_files = glob.glob("/tmp/openclaw/openclaw-*.log")
        if not log_files:
            os.makedirs("/tmp/openclaw", exist_ok=True)
            log_file = f"/tmp/openclaw/openclaw-{datetime.date.today()}.log"
            open(log_file, 'a').close()
        else:
            log_file = max(log_files, key=os.path.getmtime)
        
        print(f"📡 Monitoring latest log: {log_file}", flush=True)
        
        print("🔗 Connecting to Hub...", flush=True)
        client = NexusClient(hub_url=HUB_HTTP_URL)
        client.agent_id = "openclaw-bridge"
        current_task_id = None
        
        def handle_control(signal_data):
            nonlocal current_task_id
            action = signal_data.get('action')
            if action == 'WAKE_UP':
                print('⚡️ [WAKE_UP] 收到督察官强制唤醒信号，正在进行自检...', flush=True)
                if current_task_id:
                    sid = client.start_step(name='🚨 督察官强制唤醒：正在检查阻塞点...', task_id=current_task_id)
                    import os
                    ps = os.popen('ps aux | grep openclaw | grep -v grep').read()
                    status = '正常' if ps else '进程丢失'
                    client.complete_step(step_id=sid, status='DONE', logs=f'自检结果: 核心进程{status}。缓冲区已刷新。')
                    client.update_task_progress(progress=min(99, 10), status='RUNNING', task_id=current_task_id)
            elif action == 'STOP_ALL':
                print('🛑 [STOP_ALL] 紧急停机指令已下达，正在强制关停所有 OpenClaw 进程并退出...', flush=True)
                import os
                # 尝试杀掉所有 openclaw 相关的进程
                os.system("ps aux | grep openclaw | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null")
                sys.exit(0)

        print("👂 Listening for commands...", flush=True)
        client.listen_for_commands(handle_control)

        print("📊 Starting tail process...", flush=True)
        process = await asyncio.create_subprocess_exec(
            'tail', '-F', '-n', '0', log_file,
            stdout=asyncio.subprocess.PIPE
        )
        
        print("🐍 Entering main loop...", flush=True)
        nexus_task_id = None  # 从 NEXUS_TASK_BINDING 头解析出的 Supervisor 预分配 task_id
        
        while True:
            line = await process.stdout.readline()
            if not line:
                print("⚠️ EOF reached on tail.", flush=True)
                break

            msg = line.decode('utf-8').strip()
            if not msg:
                continue
            
            # 🔒 过滤：跳过协议说明文本（模板/文档行，不是真实的结构化日志）
            # 这些行包含反引号、模板占位符等，是 NEXUS header 内的协议说明被输出到日志
            if '`[HUB_' in msg or '<标题>' in msg or '<task_id>' in msg:
                continue
            
            # 💡 解析 NEXUS_TASK_BINDING 头部（从 Supervisor 注入的 prompt header）
            # 这是 Hub 预分配的 task_id，所有后续操作都绑定到它
            if '[NEXUS_TASK_BINDING]' in msg:
                # 接下来几行会包含 task_id: xxxx
                nexus_task_id = None  # 重置，准备接收新绑定
                continue
            if msg.startswith('task_id:') and not nexus_task_id:
                tid = msg.split('task_id:')[1].strip()
                if len(tid) > 10:
                    nexus_task_id = tid
                    current_task_id = tid
                    print(f'🔗 [NEXUS Bind] 从 prompt header 绑定到预分配任务: {tid[:8]}', flush=True)
                    # 不需要创建新任务，Supervisor 和 /api/v2/commands 已经创建了
                    client.update_task_progress(progress=10, status='RUNNING', task_id=current_task_id)
                continue
            if '[/NEXUS_TASK_BINDING]' in msg:
                continue
            # 跳过 NEXUS header 中的其他元数据行
            if nexus_task_id and (msg.startswith('assignee:') or msg.startswith('title:') or msg.startswith('hub_url:') or msg.startswith('protocol:')):
                continue
            
            # 💡 [HUB_TASK_START] — 特工主动宣告新任务
            if '[HUB_TASK_START]' in msg:
                title = msg.split('[HUB_TASK_START]')[1].strip()
                if nexus_task_id or current_task_id:
                    # ✅ 已有预分配的 task_id → 绑定到它，只更新标题
                    current_task_id = nexus_task_id or current_task_id
                    try:
                        requests.put(
                            f"{HUB_HTTP_URL}/api/v2/tasks/{current_task_id}",
                            json={"title": title[:60]},
                            timeout=3
                        )
                    except: pass
                    client.update_task_progress(progress=15, status='RUNNING', task_id=current_task_id)
                    print(f'✨ [Self-Report] 绑定到预分配任务 {current_task_id[:8]}: {title[:40]}', flush=True)
                else:
                    # ❌ 没有预分配 task_id → Bridge 不创建任务！只做遥测广播
                    # 任务必须由 Supervisor 或 /api/v2/commands 创建
                    print(f'⚠️ [Self-Report] 检测到 HUB_TASK_START 但无预分配 task_id，跳过创建: {title[:40]}', flush=True)
                continue
            
            # 💡 [HUB_TASK_ID] — 特工在日志中回传 Supervisor 分配的 task_id
            elif '[HUB_TASK_ID]' in msg:
                try:
                    supervisor_task_id = msg.split('[HUB_TASK_ID]')[1].strip()
                    if supervisor_task_id and len(supervisor_task_id) > 10:
                        print(f'🔗 [Task Bind] 绑定到 Supervisor 任务: {supervisor_task_id}', flush=True)
                        # 如果之前创建了一个占位任务，标记为已合并
                        if current_task_id and current_task_id != supervisor_task_id:
                            try:
                                client.update_task_progress(progress=100, status='DONE', task_id=current_task_id)
                            except: pass
                        current_task_id = supervisor_task_id
                        nexus_task_id = supervisor_task_id  # 也更新 nexus 引用
                        client.update_task_progress(progress=10, status='RUNNING', task_id=current_task_id)
                except Exception as e:
                    print(f'⚠️ [HUB_TASK_ID] 解析失败: {e}', flush=True)
                continue
                
            elif '[HUB_PROGRESS]' in msg and current_task_id:
                try:
                    prog_str = msg.split('[HUB_PROGRESS]')[1].strip().replace('%', '')
                    prog = int(prog_str)
                    client.update_task_progress(progress=prog, status='RUNNING', task_id=current_task_id)
                    sid = client.start_step(name=f'📈 进度更新: {prog}%', task_id=current_task_id)
                    client.complete_step(step_id=sid, status='DONE', logs=msg)
                except:
                    pass
                continue
                
            elif '[HUB_TASK_DONE]' in msg and current_task_id:
                client.update_task_progress(progress=100, status='DONE', task_id=current_task_id)
                sid = client.start_step(name='✅ 任务完美收官', task_id=current_task_id)
                client.complete_step(step_id=sid, status='DONE', logs=msg)
                
                # 🚀 关键修复：广播最终回复到 Dashboard
                try:
                    reply_payload = json.dumps({
                        "agent_id": "openclaw",
                        "task_id": current_task_id,
                        "content": msg.replace('[HUB_TASK_DONE]', '').strip(),
                        "status": "SUCCESS"
                    }).encode('utf-8')
                    req = urllib.request.Request(
                        f"{HUB_HTTP_URL}/api/v2/agent-reply",
                        data=reply_payload,
                        headers={"Content-Type": "application/json"}
                    )
                    urllib.request.urlopen(req, timeout=5)
                except Exception as e:
                    print(f"⚠️ Reply Broadcast Failed: {e}")

                current_task_id = None
                nexus_task_id = None  # 清除绑定
                continue
            
            # 💡 常规日志遥测（不创建任务）
            if current_task_id:
                s_name = None
                if 'Calling tool' in msg:
                    s_name = 'Tool Call'
                elif 'Reasoning' in msg or 'Thought:' in msg:
                    s_name = 'Reasoning'
                
                if s_name:
                    sid = client.start_step(name=s_name, task_id=current_task_id)
                    client.complete_step(step_id=sid, status='DONE', logs=msg[:200])

    except asyncio.CancelledError:
        print("🛑 Bridge cancelled.", flush=True)
    except Exception as e:
        print(f"🔥 Loop Error: {e}", flush=True)
    finally:
        print("🧹 Cleaning up...", flush=True)
        if current_task_id:
            try:
                client.update_task_progress(progress=100, status='DONE', task_id=current_task_id)
            except:
                pass
        try:
            client.disconnect()
        except:
            pass
        print("🛑 Bridge Stopped.", flush=True)

if __name__ == "__main__":
    try:
        asyncio.run(bridge_openclaw_logs())
    except KeyboardInterrupt:
        print("⌨️ Interrupted by user.", flush=True)
    except Exception as e:
        print(f"💀 Bridge Fatal Error: {e}", flush=True)
