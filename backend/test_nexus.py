import time
import threading
from nexus_client import NexusClient

def run_dummy_task():
    print("Starting NexusClient dummy test...")
    # Make sure to point to localhost:8000 where our backend presumably runs
    client = NexusClient(hub_url="http://localhost:8000")
    
    task_id = client.create_task("Integration Test Pipeline", assignee="OpenClaw", context={"mode": "test"})
    print(f"Created Task: {task_id}")
    
    # Listen for control signals in the background
    def on_control(signal):
        print(f"Received Control Signal: {signal}")
        
    client.listen_for_commands(on_control)
    
    # Let connection establish
    time.sleep(1)
    
    steps = ["Initialize", "Load Data", "Process", "Cleanup"]
    
    for i, step_name in enumerate(steps):
        print(f"Starting step: {step_name}")
        step_id = client.start_step(step_name)
        
        # Simulate work
        time.sleep(2)
        
        progress = int(((i + 1) / len(steps)) * 100)
        client.update_task_progress(progress, status="RUNNING")
        
        client.complete_step(step_id, status="DONE", logs=f"{step_name} executed successfully.")
        
    client.update_task_progress(100, status="DONE")
    print("Task completed.")
    
    client.disconnect()

if __name__ == "__main__":
    run_dummy_task()
