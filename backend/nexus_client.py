import json
import uuid
from websocket import WebSocketApp
import threading
import requests

class NexusClient:
    def __init__(self, hub_url="http://localhost:8000"):
        self.hub_url = hub_url
        self.ws_url = hub_url.replace("http://", "ws://").replace("https://", "wss://")
        self.ws = None
        self.agent_id = "agent-" + uuid.uuid4().hex[:8]
        self.current_task_id = None
        self.running = False
        
    def _start_heartbeat(self):
        def ping():
            import time
            import platform
            try:
                import psutil
                import os as _os
                has_psutil = True
                proc = psutil.Process(_os.getpid())
            except ImportError:
                has_psutil = False
                proc = None
                
            platform_info = f"{platform.system()} {platform.machine()}"
            
            while self.running:
                try:
                    payload = {
                        'agent_id': self.agent_id, 
                        'name': self.agent_id,
                        'platform_info': platform_info
                    }
                    
                    if has_psutil and proc:
                        # 进程级别的 CPU 和内存 (不是全系统)
                        payload['cpu_percent'] = proc.cpu_percent(interval=1)
                        mem_info = proc.memory_info()
                        payload['memory_mb'] = round(mem_info.rss / (1024 * 1024), 1)
                        # 磁盘仍用系统级，因为所有进程共享
                        disk = psutil.disk_usage('/')
                        payload['disk_percent'] = disk.percent
                    
                    url = f"{self.hub_url}/api/v2/agents/heartbeat"
                    requests.post(url, json=payload, timeout=5)
                except:
                    pass
                time.sleep(15)
        t = threading.Thread(target=ping, daemon=True)
        t.start()

    def create_task(self, title, assignee="Agent", context=None):
        url = f"{self.hub_url}/api/v2/tasks"
        payload = {
            "title": title,
            "assignee": assignee,
            "context": context or {}
        }
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        self.current_task_id = resp.json().get("id")
        return self.current_task_id

    def update_task_progress(self, progress, status="RUNNING", task_id=None):
        t_id = task_id or self.current_task_id
        if not t_id:
            raise ValueError("No task_id available")
        url = f"{self.hub_url}/api/v2/tasks/{t_id}/progress"
        payload = {
            "progress": progress,
            "status": status
        }
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def start_step(self, name, task_id=None):
        t_id = task_id or self.current_task_id
        if not t_id:
            raise ValueError("No task_id available")
        url = f"{self.hub_url}/api/v2/tasks/{t_id}/steps"
        payload = {"name": name}
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        return resp.json().get("step_id")

    def complete_step(self, step_id, status="DONE", logs=""):
        url = f"{self.hub_url}/api/v2/steps/{step_id}"
        payload = {
            "status": status,
            "logs": logs
        }
        resp = requests.put(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def send_agent_message(self, target_agent, message_type, content, **kwargs):
        url = f"{self.hub_url}/api/v2/messages"
        payload = {
            "type": message_type,
            "target_agent": target_agent,
            "source_agent": self.agent_id,
            "content": content
        }
        payload.update(kwargs)
        try:
            resp = requests.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print("Failed to send agent message:", e)
            return None

    def listen_for_commands(self, callback):
        self.running = True
        self._start_heartbeat()
        ws_url = f"{self.ws_url}/ws/{self.agent_id}"
        
        def on_message(ws, message):
            data = json.loads(message)
            if data.get("type") in ["CONTROL_SIGNAL", "execute", "DIRECTIVE"]:
                callback(data)
                
        def on_error(ws, error):
            pass
            
        def on_close(ws, close_status_code, close_msg):
            pass
            
        def on_open(ws):
            pass
            
        self.ws = WebSocketApp(
            ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        
        def run_ws():
            import time
            while self.running:
                try:
                    self.ws.run_forever()
                except Exception:
                    pass
                if self.running:
                    time.sleep(3)
                    
        wst = threading.Thread(target=run_ws)
        wst.daemon = True
        wst.start()

    def disconnect(self):
        self.running = False
        if self.ws:
            self.ws.close()
