// PM2 Ecosystem Configuration for HyperTask Hub
// Adjust paths to match your local environment before use.
// See .env.example for required environment variables.

const path = require('path');
const PROJECT_ROOT = __dirname;  // Auto-detect project root

module.exports = {
  apps: [
    {
      name: "hypertask-hub",
      script: path.join(PROJECT_ROOT, "venv/bin/uvicorn"),
      args: "backend.main:app --host 0.0.0.0 --port 8000",
      cwd: PROJECT_ROOT,
      interpreter: "none",
      watch: false,
      env: {
        PLAN_DIRS: ""
      }
    },
    {
      name: "openclaw-bridge",
      script: path.join(PROJECT_ROOT, "venv/bin/python3"),
      args: "backend/openclaw_bridge.py",
      cwd: PROJECT_ROOT,
      interpreter: "none",
      watch: false
    },
    {
      name: "deepseek-agent",
      script: path.join(PROJECT_ROOT, "venv/bin/python3"),
      args: "backend/deepseek_agent.py",
      cwd: PROJECT_ROOT,
      interpreter: "none",
      watch: false
    },
    {
      name: "supervisor-agent",
      script: path.join(PROJECT_ROOT, "venv/bin/python3"),
      args: "backend/supervisor_agent.py",
      cwd: PROJECT_ROOT,
      interpreter: "none",
      watch: false
    }
  ]
};
