from fastapi.testclient import TestClient
from backend.main import app
import json

client = TestClient(app)

def test_websocket_connection():
    with client.websocket_connect("/ws/agent1") as websocket:
        data = websocket.receive_json()
        assert data == {"type": "system", "content": "Agent agent1 is ONLINE"}
        
        websocket.send_text("Hello World")
        data = websocket.receive_json()
        assert data == {"type": "message", "from": "agent1", "content": "Hello World"}

def test_websocket_broadcast():
    with client.websocket_connect("/ws/agentA") as ws_a:
        # A connects -> gets welcome
        msg_a = ws_a.receive_json()
        assert msg_a["type"] == "system" and "ONLINE" in msg_a["content"]
        
        with client.websocket_connect("/ws/agentB") as ws_b:
            # B connects -> gets welcome
            msg_b = ws_b.receive_json()
            assert msg_b["type"] == "system" and "ONLINE" in msg_b["content"]
            
            # A also receives "Agent agentB is ONLINE" because broadcast happens on connect
            msg_a_about_b = ws_a.receive_json()
            assert msg_a_about_b["type"] == "system" and "agentB is ONLINE" in msg_a_about_b["content"]

            ws_a.send_text("Message from A")
            
            # A receives its own message (echo)
            data_a = ws_a.receive_json()
            assert data_a["content"] == "Message from A"
            
            # B receives A's message
            data_b = ws_b.receive_json()
            assert data_b["content"] == "Message from A"
