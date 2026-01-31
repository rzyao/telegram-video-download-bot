import os
import database
import logging

class Config:
    """配置类，数据库驱动"""
    
    # 默认出厂配置
    DEFAULTS = {
        "telegram.api_id": 2040,
        "telegram.api_hash": "b18441a1ff607e10a989891a5462e627",
        "telegram.session_name": "telethon_session",
        
        "directories.download_dir": "D:/tg_downloads" if os.name == 'nt' else "/app/downloads",
        "directories.data_dir": "data" if os.name != 'nt' else "data", 
        "directories.temp_dir": "", # Will be derived
        
        "proxy.enable": False,
        "proxy.scheme": "socks5",
        "proxy.host": "127.0.0.1",
        "proxy.port": 1080,
        
        "download.max_workers": 4,
        "download.worker_count": 4,
        
        "dashboard.enable": True,
        "dashboard.host": "0.0.0.0",
        "dashboard.port": 9595,
        
        "logging.file": "tg_downloader.log",
        "logging.headless": False,
        "logging.log_interval": 30,
        
        "system.setup_completed": False
    }
    
    # 当前内存配置
    _settings = {}
    
    # 类属性占位符 (防止 IDE 报错)
    API_ID = 2040
    API_HASH = ""
    SESSION_NAME = ""
    DOWNLOAD_DIR = ""
    TEMP_DIR = ""
    USE_PROXY = False
    PROXY = None
    MAX_WORKERS = 4
    WORKER_COUNT = 4
    DASHBOARD_HOST = "0.0.0.0"
    DASHBOARD_PORT = 9595
    ENABLE_DASHBOARD = True
    SETUP_COMPLETED = False
    
    # 日志相关
    LOG_FILE = "tg_downloader.log"
    HEADLESS = False
    LOG_INTERVAL = 30

    # 静态常量
    CHUNK_SIZE = 1024 * 1024
    PART_SIZE = 10 * 1024 * 1024
    MAX_RETRIES = 50
    RETRY_DELAY_BASE = 5
    RETRY_DELAY_MAX = 60
    LOG_LEVEL = "INFO"

    @classmethod
    def load(cls):
        """从数据库加载配置"""
        db_settings = database.load_settings_sync()
        cls._settings = cls.DEFAULTS.copy()
        # Merge DB settings
        cls._settings.update(db_settings)
        
        # 刷新属性
        cls.API_ID = int(cls.get("telegram.api_id"))
        cls.API_HASH = cls.get("telegram.api_hash")
        
        # 确保 Session 文件保存在 data 目录
        data_dir = cls.get("directories.data_dir", "data")
        session_base = cls.get("telegram.session_name", "telethon_session")
        if not os.path.isabs(session_base):
            cls.SESSION_NAME = os.path.join(data_dir, session_base)
        else:
            cls.SESSION_NAME = session_base
        
        cls.DOWNLOAD_DIR = cls.get("directories.download_dir")
        
        # 优先使用用户配置的临时目录，提升 IO 性能
        custom_temp = cls.get("directories.temp_dir")
        if custom_temp and custom_temp.strip():
            cls.TEMP_DIR = custom_temp
        else:
            # 用户要求直接使用项目根目录作为默认临时目录
            cls.TEMP_DIR = "." # Current directory
        
        cls.USE_PROXY = cls.get("proxy.enable")
        if cls.USE_PROXY:
            cls.PROXY = {
                "scheme": cls.get("proxy.scheme"),
                "hostname": cls.get("proxy.host"),
                "port": int(cls.get("proxy.port"))
            }
        else:
            cls.PROXY = None

        cls.MAX_WORKERS = int(cls.get("download.max_workers"))
        cls.WORKER_COUNT = int(cls.get("download.worker_count"))
        
        cls.DASHBOARD_HOST = cls.get("dashboard.host")
        cls.DASHBOARD_PORT = int(cls.get("dashboard.port"))
        cls.ENABLE_DASHBOARD = cls.get("dashboard.enable", True)
        
        cls.LOG_FILE = cls.get("logging.file", "tg_downloader.log")
        cls.HEADLESS = cls.get("logging.headless", False)
        cls.LOG_INTERVAL = int(cls.get("logging.log_interval", 30))
        
        cls.SETUP_COMPLETED = cls.get("system.setup_completed", False)
        
        # 确保目录存在 (只在 Setup 完成后或目录已配置时尝试)
        if cls.SETUP_COMPLETED or os.path.isabs(cls.DOWNLOAD_DIR):
             cls.ensure_directories()
             
        print(f"✅ 配置已加载 (已初始化: {cls.SETUP_COMPLETED})")

    @classmethod
    def get(cls, key, default=None):
        return cls._settings.get(key, default)

    @classmethod
    def reload(cls):
        cls.load()

    @classmethod
    def get_progress_file_path(cls, message_id, chat_id):
        """获取任务进度文件路径"""
        filename = f"task_{chat_id}_{message_id}.json"
        return os.path.join(cls.TEMP_DIR, filename)

    @classmethod
    def ensure_directories(cls):
        """确保必要的目录存在"""
        if cls.DOWNLOAD_DIR and not os.path.exists(cls.DOWNLOAD_DIR):
            try:
                os.makedirs(cls.DOWNLOAD_DIR, exist_ok=True)
            except Exception as e:
                print(f"创建下载目录失败: {e}")
                
        if cls.TEMP_DIR and not os.path.exists(cls.TEMP_DIR):
            try:
                os.makedirs(cls.TEMP_DIR, exist_ok=True)
            except Exception as e:
                print(f"创建临时目录失败: {e}")

        # 确保数据目录存在
        data_dir = cls.get("directories.data_dir", "data")
        if data_dir and not os.path.exists(data_dir):
            try:
                os.makedirs(data_dir, exist_ok=True)
            except Exception as e:
                print(f"创建数据目录失败: {e}")

# 模块加载时自动执行一次配置加载
Config.load()
