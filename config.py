"""
Telegram 断点续传下载器 - 配置管理
支持环境变量覆盖，便于在不同环境（本地/服务器）部署
"""
import os

class Config:
    """配置类，集中管理所有配置项"""
    
    # ==================== Telegram API ====================
    # 使用 Telegram Desktop 官方 API ID (配合 Windows 伪装，权限最高)
    API_ID = 2040
    API_HASH = "b18441a1ff607e10a989891a5462e627"
    SESSION_NAME = "telethon_session"
    
    # 旧配置备份
    # API_ID = int(os.getenv("TG_API_ID", "36348713"))
    # API_HASH = os.getenv("TG_API_HASH", "cfa5fdaedc3b34f934d8d4152e41811a")
    # SESSION_NAME = os.getenv("TG_SESSION_NAME", "ayao_account")
    
    # ==================== 下载目录 ====================
    # Windows 默认: D:/tg_downloads
    # Linux 默认: /mnt/downloads/telegram_videos
    if os.name == 'nt':
        DEFAULT_DOWNLOAD_DIR = "D:/tg_downloads"
    else:
        DEFAULT_DOWNLOAD_DIR = "/mnt/downloads/telegram_videos"
    
    DOWNLOAD_DIR = os.getenv("TG_DOWNLOAD_DIR", DEFAULT_DOWNLOAD_DIR)
    
    # 进度文件存储目录（相对于 DOWNLOAD_DIR）
    PROGRESS_DIR = ".progress"
    
    # ==================== 代理配置 ====================
    # 服务器上通常不需要代理，设置 USE_PROXY=false
    # 本地测试时设置 USE_PROXY=true
    USE_PROXY = os.getenv("USE_PROXY", "true" if os.name == 'nt' else "false").lower() == "true"
    
    if USE_PROXY:
        PROXY = {
            "scheme": "socks5",
            "hostname": os.getenv("PROXY_HOST", "192.168.50.2"),
            "port": int(os.getenv("PROXY_PORT", "10088"))
        }
    else:
        PROXY = None
    
    # ==================== 下载配置 ====================
    CHUNK_SIZE = 1024 * 1024  # 1MB 每请求块（Telegram API 限制）
    PART_SIZE = 10 * 1024 * 1024  # 10MB 每并发分片（每个 Worker 负责下载的大小）
    MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))  # 并发线程数
    WORKER_COUNT = int(os.getenv("WORKER_COUNT", "4")) # 独立客户端池大小 (建议与 MAX_WORKERS 一致或略小)
    MAX_RETRIES = 50          # 单块最大重试次数
    RETRY_DELAY_BASE = 5      # 基础重试间隔（秒）
    RETRY_DELAY_MAX = 60      # 最大重试间隔（秒）
    
    # ==================== 日志配置 ====================
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = os.getenv("LOG_FILE", "tg_downloader.log")
    
    @classmethod
    def ensure_directories(cls):
        """确保必要的目录存在"""
        os.makedirs(cls.DOWNLOAD_DIR, exist_ok=True)
        os.makedirs(os.path.join(cls.DOWNLOAD_DIR, cls.PROGRESS_DIR), exist_ok=True)
    
    @classmethod
    def get_progress_file_path(cls, message_id: int, chat_id: int) -> str:
        """获取进度文件路径"""
        return os.path.join(
            cls.DOWNLOAD_DIR, 
            cls.PROGRESS_DIR, 
            f"task_{chat_id}_{message_id}.json"
        )
    
    @classmethod
    def print_config(cls):
        """打印当前配置（调试用）"""
        print("=" * 50)
        print("当前配置:")
        print(f"  下载目录: {cls.DOWNLOAD_DIR}")
        print(f"  使用代理: {cls.USE_PROXY}")
        if cls.USE_PROXY and cls.PROXY:
            print(f"  代理地址: {cls.PROXY['hostname']}:{cls.PROXY['port']}")
        print(f"  最大重试: {cls.MAX_RETRIES} 次")
        print("=" * 50)
