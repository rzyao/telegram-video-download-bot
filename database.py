import aiosqlite
import sqlite3
import os
import logging
import json
from datetime import datetime

logger = logging.getLogger("Database")

# 硬编码数据库名，它是唯一的数据源
DB_PATH = "bot_data.db"

async def init_db():
    """初始化数据库"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # 1. 历史记录表
            await db.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    size INTEGER DEFAULT 0,
                    duration TEXT,
                    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 2. 活动任务表 (断点续传)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS active_tasks (
                    message_id INTEGER,
                    chat_id INTEGER,
                    file_name TEXT,
                    file_size INTEGER,
                    status TEXT,
                    data TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (message_id, chat_id)
                )
            """)

            # 3. 系统配置表 (替代 config.yaml)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    description TEXT
                )
            """)
            
            await db.commit()
            logger.info(f"✅ 数据库已连接: {DB_PATH}")
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")

# ==================== 配置管理 (Settings) ====================

def load_settings_sync():
    """同步加载所有配置 (用于程序启动时初始化 Config)"""
    if not os.path.exists(DB_PATH):
        return {}
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # 检查表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
        if not cursor.fetchone():
            return {}
            
        cursor.execute("SELECT key, value FROM settings")
        rows = cursor.fetchall()
        conn.close()
        
        settings = {}
        for key, val_json in rows:
            try:
                settings[key] = json.loads(val_json)
            except:
                settings[key] = val_json
        return settings
    except Exception as e:
        print(f"❌ 同步加载配置失败: {e}")
        return {}

async def update_setting(key: str, value: any, description: str = ""):
    """更新配置项"""
    try:
        val_json = json.dumps(value, ensure_ascii=False)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value, description) VALUES (?, ?, ?)",
                (key, val_json, description)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"❌ 更新配置失败 [{key}]: {e}")

async def get_setting(key: str, default=None):
    """获取单个配置"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = await cursor.fetchone()
            if row:
                return json.loads(row[0])
            return default
    except Exception as e:
        logger.error(f"❌ 获取配置失败 [{key}]: {e}")
        return default

# ==================== 历史记录 (History) ====================

async def add_history(filename: str, size: int, duration: str = ""):
    """添加历史记录"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO history (filename, size, duration, completed_at) VALUES (?, ?, ?, ?)",
                (filename, size, duration, datetime.now())
            )
            await db.commit()
            logger.info(f"💾 已保存历史记录: {filename}")
    except Exception as e:
        logger.error(f"❌ 保存历史记录失败: {e}")

async def get_recent_history(limit=50):
    """获取最近历史记录"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM history ORDER BY completed_at DESC LIMIT ?",
                (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"❌ 读取历史记录失败: {e}")
        return []

async def clear_history():
    """清空历史记录"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM history")
            await db.commit()
            logger.info("🗑️ 历史记录已清空")
            return True
    except Exception as e:
        logger.error(f"❌ 清空历史记录失败: {e}")
        return False

# ==================== 活动任务管理 (断点续传) ====================

async def save_active_task(task_data: dict):
    """保存或更新活动任务进度"""
    try:
        message_id = task_data['message_id']
        chat_id = task_data['chat_id']
        file_name = task_data['file_name']
        file_size = task_data['file_size']
        status = task_data['status']
        json_data = json.dumps(task_data, ensure_ascii=False)
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT OR REPLACE INTO active_tasks 
                (message_id, chat_id, file_name, file_size, status, data, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (message_id, chat_id, file_name, file_size, status, json_data, datetime.now()))
            await db.commit()
    except Exception as e:
        logger.error(f"❌ 保存任务进度失败 [{task_data.get('file_name')}]: {e}")

async def load_active_task(message_id: int, chat_id: int):
    """加载活动任务进度"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT data FROM active_tasks WHERE message_id = ? AND chat_id = ?",
                (message_id, chat_id)
            )
            row = await cursor.fetchone()
            if row:
                return json.loads(row['data'])
            return None
    except Exception as e:
        logger.error(f"❌ 读取任务进度失败: {e}")
        return None

async def delete_active_task(message_id: int, chat_id: int):
    """删除活动任务进度 (任务完成或取消后)"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM active_tasks WHERE message_id = ? AND chat_id = ?",
                (message_id, chat_id)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"❌ 删除任务进度失败: {e}")
