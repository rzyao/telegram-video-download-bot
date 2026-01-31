#!/usr/bin/env python3
"""快速禁用代理配置"""
import sqlite3
import json

DB_PATH = "bot_data.db"

def disable_proxy():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 禁用代理
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("proxy.enable", json.dumps(False))
    )
    
    conn.commit()
    conn.close()
    print("✅ 代理已禁用！请重新运行 main.py")

if __name__ == "__main__":
    disable_proxy()
