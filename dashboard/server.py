from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import os
import uvicorn
import asyncio
import psutil
from collections import deque
from config import Config
import logging
import database
import yaml
from telethon import TelegramClient, errors
from telethon.errors import SessionPasswordNeededError

# Pydantic 请求模型
class CodeRequest(BaseModel):
    phone: str

class SignInRequest(BaseModel):
    code: str
    password: str = None

# 获取 logger
logger = logging.getLogger("Dashboard")

# 内存日志 Handler
class MemoryLogHandler(logging.Handler):
    def __init__(self, capacity=50):
        super().__init__()
        self.logs = deque(maxlen=capacity)
        self.formatter = logging.Formatter('%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%H:%M:%S')

    def emit(self, record):
        try:
            msg = self.format(record)
            self.logs.append(msg)
        except Exception:
            self.handleError(record)

# 全局日志收集器
mem_handler = MemoryLogHandler()
logging.getLogger().addHandler(mem_handler)

# 模块自引用（用于访问模块级变量）
import sys
server = sys.modules[__name__]
# 注入的主模块引用
main_module = None 

def _get_main():
    """获取正确的主模块实例"""
    return getattr(server, 'main_module', None) or sys.modules.get('main')

# 全局 Telegram 登录状态（用于 Web 向导）
telegram_login_state = {
    "client": None,
    "phone": None,
    "phone_code_hash": None
}

# Main 模块的引用（由 main.py 设置）
main_module = None

# 定义应用
app = FastAPI(title="Telegram Downloader")

# 模板引擎
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(templates_dir, exist_ok=True)
templates = Jinja2Templates(directory=templates_dir)

# 全局 Downloader 引用
downloader_instance = None

def set_downloader(downloader):
    global downloader_instance
    downloader_instance = downloader

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """智能重定向：根据系统状态决定去向"""
    # 状态 1: 未完成初始化
    if not Config.SETUP_COMPLETED:
        return templates.TemplateResponse("setup.html", {"request": request, "defaults": Config.to_dict()})
    
    # 状态 2: 已初始化但未登录
    session_file = f"{Config.SESSION_NAME}.session"
    if not os.path.exists(session_file):
        return templates.TemplateResponse("login.html", {"request": request})
    
    # 状态 3: 正常进入主页
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/setup.html", response_class=HTMLResponse)
async def setup_page(request: Request):
    """系统配置页面"""
    return templates.TemplateResponse("setup.html", {"request": request, "defaults": Config.to_dict()})

@app.get("/login.html", response_class=HTMLResponse)
async def login_page(request: Request):
    """Telegram 登录页面"""
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/api/system")
async def get_system_stats():
    """获取系统状态"""
    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    
    try:
        if Config.DOWNLOAD_DIR and os.path.exists(Config.DOWNLOAD_DIR):
             disk = psutil.disk_usage(Config.DOWNLOAD_DIR)
             disk_percent = disk.percent
             disk_free = round(disk.free / (1024**3), 2)
        else:
             disk_percent = 0
             disk_free = 0
    except:
        disk_percent = 0
        disk_free = 0
    
    return {
        "cpu": cpu_percent,
        "memory": mem.percent,
        "disk": disk_percent,
        "disk_free_gb": disk_free
    }

@app.get("/api/logs")
async def get_logs():
    """获取最近日志"""
    return {"logs": list(mem_handler.logs)}

@app.get("/api/setup/status")
async def get_setup_status():
    return {"completed": Config.SETUP_COMPLETED}

@app.post("/api/setup")
async def complete_setup(request: Request):
    """完成初始化"""
    try:
        data = await request.json()
        for key, value in data.items():
            await database.update_setting(key, value)
        await database.update_setting("system.setup_completed", True)
        Config.reload()
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/config")
async def get_config():
    """获取当前配置"""
    # 返回 Config._settings（包含了默认值+数据库值）
    # 但 Config._settings 是私有的，我们在 Config 增加了 get 方法，但没有 get_all
    # 既然 Config._settings 是类属性，可以直接访问
    return Config._settings

@app.post("/api/config")
async def update_config_json(request: Request):
    """更新配置 (JSON)"""
    try:
        data = await request.json()
        for key, value in data.items():
            await database.update_setting(key, value)
        Config.reload()
        return {"status": "ok", "message": "Updated"}
    except Exception as e:
        logger.error(f"Config Update Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/task/cancel")
async def cancel_task():
    """取消当前任务"""
    downloader = None
    if hasattr(server, 'main_module'):
        downloader = server.main_module.get_downloader()
        
    if downloader:
        if await downloader.cancel_current_task():
            return {"status": "ok", "message": "Task cancellation requested"}
            
    return JSONResponse(status_code=400, content={"status": "error", "message": "No active task"})

@app.post("/api/task/resume/{message_id}")
async def resume_task(message_id: int):
    """恢复已取消的任务"""
    downloader = None
    if hasattr(server, 'main_module'):
        downloader = server.main_module.get_downloader()
        
    if downloader:
        if await downloader.resume_task(message_id):
            return {"status": "ok", "message": "Task resumed"}
            
    return JSONResponse(status_code=400, content={"status": "error", "message": "Failed to resume task"})

@app.delete("/api/task/cancelled/{message_id}")
async def delete_cancelled_task(message_id: int):
    """彻底删除已取消的任务及其文件"""
    downloader = None
    if hasattr(server, 'main_module'):
        downloader = server.main_module.get_downloader()
        
    if downloader:
        if await downloader.delete_task(message_id):
            return {"status": "ok", "message": "Task files cleaned up"}
            
    return JSONResponse(status_code=400, content={"status": "error", "message": "Failed to delete task or file not found"})

@app.post("/api/restart")
async def restart_bot():
    """重启 Bot (需要外部进程管理器)"""
    logger.warning("🔄 收到重启请求，即将退出...")
    import os
    import asyncio
    
    async def delayed_exit():
        await asyncio.sleep(0.5)  # 给响应时间返回
        os._exit(0)
    
    asyncio.create_task(delayed_exit())
    return {"status": "ok", "message": "Restarting..."}

@app.get("/api/telegram/status")
async def telegram_status():
    """检查 Telegram Session 状态"""
    main = _get_main()
    session_file = f"{Config.SESSION_NAME}.session"
    session_exists = os.path.exists(session_file)
    
    # 检查 Client 是否真正连接且授权
    logged_in = False
    connected = False
    if main and main.client and main.client.is_connected():
        connected = main.client_connected
        try:
            # 只有当 client 已经建立连接并成功授权时，才认为已登录
            logged_in = await main.client.is_user_authorized()
        except:
            logged_in = False
    elif session_exists:
        # 如果文件存在但 client 还没启动，先认为已登录（前端会显示加载或等待启动）
        logged_in = True
    
    return {
        "logged_in": logged_in,
        "connected": connected,
        "session_file": session_file
    }

@app.get("/api/bot/status")
async def bot_status():
    """获取 Bot 运行状态"""
    session_exists = os.path.exists(f"{Config.SESSION_NAME}.session")
    
    # 使用传递的 main 模块引用获取状态
    client_connected = False
    try:
        main = _get_main()
        if main:
            c = main.get_client()
            if c is not None and c.is_connected():
                client_connected = True
    except Exception as e:
        logger.debug(f"获取 client 状态失败: {e}")
    
    return {
        "setup_completed": Config.SETUP_COMPLETED,
        "session_exists": session_exists,
        "client_connected": client_connected
    }

@app.post("/api/bot/start")
async def start_bot_manually():
    """手动启动 Telegram Bot"""
    session_file = f"{Config.SESSION_NAME}.session"
    if not os.path.exists(session_file):
        return JSONResponse(status_code=400, content={"error": "请先完成 Telegram 登录"})
    
    try:
        main = _get_main()
        if not main:
             return JSONResponse(status_code=500, content={"error": "主模块未就绪"})
        # 启动 Bot 任务
        asyncio.create_task(main.start_telegram_bot())
        logger.info("🤖 手动启动 Bot 任务已创建")
        return {"status": "ok", "message": "Bot启动中..."}
    except Exception as e:
        logger.error(f"启动 Bot 失败: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.delete("/api/telegram/session")
async def delete_session():
    """删除当前 Telegram Session（用于重新登录）"""
    main = _get_main()
    if not main:
        return JSONResponse(status_code=500, content={"error": "主模块未就绪"})
    session_file = f"{Config.SESSION_NAME}.session"
    
    try:
        logger.info("🗑️ 开始删除 Session...")
        
        # 1. 彻底销毁 Client（释放文件句柄）
        if main.client is not None:
            await main.destroy_client()
        else:
            logger.info("ℹ️  Client 未初始化，直接删除文件")
        
        # 2. 删除 Session 文件
        if os.path.exists(session_file):
            try:
                os.remove(session_file)
                logger.info(f"✅ 已删除 Session 文件: {session_file}")
            except PermissionError:
                # 如果仍然被占用，等待后重试
                logger.warning("⚠️ 文件被占用，等待后重试...")
                await asyncio.sleep(2)
                os.remove(session_file)
                logger.info(f"✅ 重试成功，已删除: {session_file}")
            
            # 3. 删除 journal 文件
            journal_file = f"{session_file}-journal"
            if os.path.exists(journal_file):
                os.remove(journal_file)
                logger.info(f"✅ 已删除 Journal 文件")
            
            return {"status": "ok", "message": "Session 已删除"}
        else:
            logger.info("ℹ️ Session 文件不存在")
            return {"status": "ok", "message": "Session 文件不存在"}
            
    except Exception as e:
        logger.error(f"❌ 删除 Session 失败: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/telegram/send_code")
async def send_code(req: CodeRequest):
    """发送 Telegram 验证码"""
    main = _get_main()
    if not main:
        return JSONResponse(status_code=500, content={"error": "主模块未就绪"})
    
    try:
        # 确保 Client 存在
        client = main.ensure_client()
        
        # 连接
        if not client.is_connected():
            await client.connect()
        
        # 发送验证码
        result = await client.send_code_request(req.phone)
        
        # 保存状态
        telegram_login_state["phone"] = req.phone
        telegram_login_state["phone_code_hash"] = result.phone_code_hash
        telegram_login_state["client"] = client
        
        logger.info(f"📱 验证码已发送至: {req.phone}")
        return {"status": "ok", "message": "验证码已发送"}
        
    except Exception as e:
        logger.error(f"发送验证码失败: {e}")
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.post("/api/telegram/sign_in")
async def sign_in(req: SignInRequest):
    """Telegram 登录验证"""
    main = _get_main()
    if not main:
        return JSONResponse(status_code=500, content={"error": "主模块未就绪"})
    
    phone = telegram_login_state.get("phone")
    phone_code_hash = telegram_login_state.get("phone_code_hash")
    client = telegram_login_state.get("client") or main.client
    
    if not phone or not phone_code_hash or not client:
        return JSONResponse(status_code=400, content={"error": "请先发送验证码"})
    
    try:
        # 尝试登录
        await client.sign_in(phone, req.code, phone_code_hash=phone_code_hash)
        
        logger.info(f"✅ 登录成功: {phone}")
        
        # 清除临时状态
        telegram_login_state.clear()
        
        # 核心修复：登录成功后立即启动 Bot 任务
        asyncio.create_task(main.start_telegram_bot())
        logger.info("🤖 登录成功，已自动启动 Bot 任务")
        
        return {"status": "ok", "message": "登录成功"}
        
    except SessionPasswordNeededError:
        # 需要两步验证密码
        if req.password:
            try:
                await client.sign_in(password=req.password)
                logger.info(f"✅ 两步验证成功: {phone}")
                telegram_login_state.clear()
                
                # 核心修复：登录成功后立即启动 Bot 任务
                asyncio.create_task(main.start_telegram_bot())
                logger.info("🤖 两步验证成功，已自动启动 Bot 任务")
                
                return {"status": "ok", "message": "登录成功"}
            except Exception as e:
                logger.error(f"两步验证失败: {e}")
                return JSONResponse(status_code=400, content={"error": "密码错误"})
        else:
            return JSONResponse(status_code=400, content={"need_password": True, "error": "需要两步验证密码"})
    
    except Exception as e:
        logger.error(f"登录失败: {e}")
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/status")
async def get_status():
    """获取最新状态 API"""
    # 使用 main_module 获取 downloader
    downloader = None
    if hasattr(server, 'main_module'):
        downloader = server.main_module.get_downloader()
    
    if not downloader:
        return {
            "status": "ok",
            "running": False,
            "current_speed": 0,
            "queue_count": 0,
            "tasks": [],
            "history": []
        }
    
    # 构造响应数据
    tasks_data = []
    # 正在进行的任务
    if downloader.current_task:
        t = downloader.current_task
        tasks_data.append({
            "id": t.message_id,
            "filename": t.file_name,
            "percent": downloader.current_percent,
            "size": t.file_size,
            "speed": downloader.current_speed,
            "eta": downloader.current_eta,
            "status": "downloading" if t.status != 'cancelled' else 'cancelled'
        })
    
    # 等待中的任务
    for t in list(downloader.tasks):
        tasks_data.append({
            "id": t.message_id,
            "filename": t.file_name,
            "percent": t.progress_percent,
            "size": t.file_size,
            "speed": 0,
            "eta": 0,
            "status": "pending"
        })
    # 历史记录
    history_data = []
    # 已取消任务
    cancelled_data = []
    
    try:
        if downloader:
            cancelled_data = await downloader.get_cancelled_tasks()
            
        history = await database.get_recent_history(limit=10)
        # 数据库返回的是字典列表: {'id':..., 'filename':..., 'size':...}
        history_data = [{"filename": h['filename'], "size": h['size']} for h in history]
    except Exception as e:
        logger.error(f"读取状态失败: {e}")
    
    return {
        "status": "ok",
        "running": downloader.current_task is not None if downloader else False,
        "current_speed": downloader.current_speed if downloader and downloader.current_task else 0,
        "queue_count": len(downloader.tasks) if downloader else 0,
        "tasks": tasks_data,
        "history": history_data,
        "cancelled": cancelled_data
    }

async def run_server():
    """启动 uvicorn 服务"""
    config = uvicorn.Config(
        app, 
        host=Config.DASHBOARD_HOST, 
        port=Config.DASHBOARD_PORT, 
        log_level="warning"
    )
    server = uvicorn.Server(config)
    logger.info(f"🌐 Dashboard 启动: http://{Config.DASHBOARD_HOST}:{Config.DASHBOARD_PORT}")
    await server.serve()
