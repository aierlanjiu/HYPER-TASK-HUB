import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "hub_v2.db")


def get_db():
    """获取数据库连接，内置 WAL 模式 + busy timeout 防止并发锁冲突"""
    conn = sqlite3.connect(DB_PATH, timeout=10)  # 10s busy timeout
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")       # 读写分离，读不阻塞写
    conn.execute("PRAGMA busy_timeout=10000")      # 双保险：等待 10s 再报错
    conn.execute("PRAGMA synchronous=NORMAL")      # WAL 模式下用 NORMAL 即可保安全
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    
    conn.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        status TEXT DEFAULT 'PENDING',
        progress INTEGER DEFAULT 0,
        assignee TEXT,
        priority TEXT DEFAULT 'NORMAL',
        context JSON,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ended_at TIMESTAMP
    )
    """)
    
    conn.execute("""
    CREATE TABLE IF NOT EXISTS steps (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        name TEXT NOT NULL,
        status TEXT DEFAULT 'PENDING',
        logs TEXT,
        started_at TIMESTAMP,
        ended_at TIMESTAMP,
        FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
    )
    """)
    
    conn.execute("""
    CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        status TEXT DEFAULT 'OFFLINE',
        last_heartbeat TIMESTAMP,
        cpu_percent REAL DEFAULT 0,
        memory_mb REAL DEFAULT 0,
        disk_percent REAL DEFAULT 0,
        platform_info TEXT DEFAULT ''
    )
    """)
    
    conn.execute("""
    CREATE TABLE IF NOT EXISTS skill_usage (
        skill_name TEXT PRIMARY KEY,
        use_count INTEGER DEFAULT 0,
        last_used TIMESTAMP
    )
    """)
    
    # Migration: add new columns to existing DB if they don't exist
    try:
        conn.execute("SELECT cpu_percent FROM agents LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE agents ADD COLUMN cpu_percent REAL DEFAULT 0")
        conn.execute("ALTER TABLE agents ADD COLUMN memory_mb REAL DEFAULT 0")
        conn.execute("ALTER TABLE agents ADD COLUMN disk_percent REAL DEFAULT 0")
        conn.execute("ALTER TABLE agents ADD COLUMN platform_info TEXT DEFAULT ''")
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
