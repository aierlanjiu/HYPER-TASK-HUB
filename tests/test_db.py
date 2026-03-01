import os
import sys
import pytest
from backend.database import init_db, DB_PATH

def test_db_init():
    # 确保测试前文件不存在
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    
    init_db()
    assert os.path.exists(DB_PATH)
