import asyncio
import os
import sys
import json
import urllib.request
import urllib.error
import time
import threading

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

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from nexus_client import NexusClient

HUB_HTTP_URL = f"http://{os.environ.get('HUB_HOST', 'localhost')}:{os.environ.get('HUB_PORT', '8000')}"
DEEPSEEK_API_URL = os.environ.get("DEEPSEEK_API_URL", "http://localhost:6799/v1/chat/completions")
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

class DeepSeekAgent:
    def __init__(self):
        self.client = NexusClient(hub_url=HUB_HTTP_URL)
        self.client.agent_id = "deepseek-nas"  # Fixed ID for UI matching
        self.running = True
        
        # 覆写心跳：DeepSeek 是远端 Docker 容器, 不采集本地 Mac 指标
        original_heartbeat = self.client._start_heartbeat
        def remote_heartbeat():
            import requests as _req
            def ping():
                while self.client.running:
                    try:
                        _req.post(
                            f"{self.client.hub_url}/api/v2/agents/heartbeat",
                            json={
                                'agent_id': 'deepseek-nas',
                                'name': 'deepseek-nas',
                                'platform_info': 'DeepSeek Remote Agent',
                                'cpu_percent': 0,
                                'memory_mb': 0,
                                'disk_percent': 0
                            },
                            timeout=5
                        )
                    except:
                        pass
                    time.sleep(15)
            t = threading.Thread(target=ping, daemon=True)
            t.start()
        self.client._start_heartbeat = remote_heartbeat

    def call_deepseek_api(self, prompt, model="deepseek-reasoner"):
        try:
            import subprocess
            import json
            
            data = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False
            }
            
            # 使用最原生的 curl 命令，并显式禁用代理（--noproxy "*"）
            # 彻底绕过系统代理污染，确保直接与本地 relay 或 NAS 通信
            cmd = [
                "curl", "-s", "--noproxy", "*", "-X", "POST", DEEPSEEK_API_URL,
                "-H", "Content-Type: application/json",
                "-H", f"Authorization: Bearer {API_KEY}",
                "-d", json.dumps(data)
            ]
            
            result_process = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            
            if result_process.returncode != 0:
                return f"API Call Failed: curl return code {result_process.returncode}, stderr: {result_process.stderr}"
                
            result = json.loads(result_process.stdout)
            return result['choices'][0]['message']['content']
        except Exception as e:
            return f"API Call Failed: {str(e)}"

    def execute_prompt(self, command, hub_task_id=None, agent_session=None):
        print(f"🚀 Starting task for prompt: {command}")
        
        # 优先使用 Hub 预创建的 task_id，避免重复挂载
        if hub_task_id:
            task_id = hub_task_id
            # 只需更新状态为 RUNNING
            self.client.update_task_progress(progress=5, status="RUNNING", task_id=task_id)
        else:
            task_id = self.client.create_task(
                title=command[:30] + "..." if len(command) > 30 else command,
                assignee="deepseek-nas",
                context={"prompt": command}
            )

        try:
            # 1. 解析意图
            step1 = self.client.start_step(name="解析意图与准备上下文", task_id=task_id)
            self.client.update_task_progress(progress=10, status="RUNNING", task_id=task_id)
            time.sleep(1) # Fake processing time
            self.client.complete_step(step_id=step1, status="DONE", logs="分析完成: 用户请求生成内容并需要逻辑推理。")

            # 2. 调用 DeepSeek API
            model = agent_session if agent_session else "deepseek-reasoner"
            step2 = self.client.start_step(name=f"DeepSeek [{model}] 深度计算", task_id=task_id)
            self.client.update_task_progress(progress=40, status="RUNNING", task_id=task_id)
            result = self.call_deepseek_api(command, model)
            
            if "API Call Failed" in result:
                self.client.complete_step(step_id=step2, status="FAILED", logs=result)
                self.client.update_task_progress(progress=40, status="FAILED", task_id=task_id)
                # 将错误也广播到仪表盘
                try:
                    import urllib.request
                    reply_payload = json.dumps({
                        "agent_id": "deepseek-nas",
                        "task_id": task_id,
                        "content": f"🚨 DeepSeek 接口请求失败\n\n原因: {result}",
                        "status": "ERROR"
                    }).encode('utf-8')
                    req = urllib.request.Request(
                        f"{HUB_HTTP_URL}/api/v2/agent-reply",
                        data=reply_payload,
                        headers={"Content-Type": "application/json"}
                    )
                    urllib.request.urlopen(req, timeout=5)
                except Exception:
                    pass
                return
            else:
                self.client.complete_step(step_id=step2, status="DONE", logs=f"推理完成。输出片段: {result[:100]}...")

            # 3. 结果汇总
            step3 = self.client.start_step(name="汇总并保存结果", task_id=task_id)
            self.client.update_task_progress(progress=80, status="RUNNING", task_id=task_id)
            
            # Save to a generic output folder
            out_dir = "/tmp/deepseek_output"
            os.makedirs(out_dir, exist_ok=True)
            out_file = os.path.join(out_dir, f"result_{task_id}.md")
            with open(out_file, "w") as f:
                f.write(result)
                
            self.client.complete_step(step_id=step3, status="DONE", logs=f"已保存到 {out_file}")

            # 4. 广播结果到 Dashboard
            try:
                import urllib.request
                reply_payload = json.dumps({
                    "agent_id": "deepseek-nas",
                    "task_id": task_id,
                    "content": result,
                    "status": "SUCCESS"
                }).encode('utf-8')
                req = urllib.request.Request(
                    f"{HUB_HTTP_URL}/api/v2/agent-reply",
                    data=reply_payload,
                    headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception as broadcast_err:
                print(f"⚠️ Broadcast failed: {broadcast_err}")

            # 5. 完成任务
            self.client.update_task_progress(progress=100, status="DONE", task_id=task_id)
            print("✅ Task completed.")

        except Exception as e:
            self.client.update_task_progress(progress=0, status="FAILED", task_id=task_id)
            print(f"❌ Task failed: {e}")

    def on_command(self, signal_data):
        msg_type = signal_data.get("type")
        if msg_type == "execute":
            command = signal_data.get("command")
            hub_task_id = signal_data.get("task_id")  # Hub 预创建的任务 ID
            agent_session = signal_data.get("agent_session", "deepseek-reasoner")
            threading.Thread(target=self.execute_prompt, args=(command, hub_task_id, agent_session), daemon=True).start()
        else:
            action = signal_data.get("action")
            task_id = signal_data.get("task_id")
            if action == 'STOP_ALL':
                print("🛑 [STOP_ALL] 紧急停机指令下达。正在强制退出...")
                os._exit(0)
            print(f"⚠️ 收到控制中心指令: {action} 对于任务 {task_id}")
        
    def start(self):
        print("🔗 Connecting DeepSeek NAS Agent to HyperTask Hub...")
        self.client.listen_for_commands(self.on_command)
        
        while self.running:
            time.sleep(1)

if __name__ == "__main__":
    agent = DeepSeekAgent()
    try:
        agent.start()
    except KeyboardInterrupt:
        print("Exiting...")
        agent.client.disconnect()
