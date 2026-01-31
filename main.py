"""
Telegram Downloader (Telethon Version)
基于 Telethon 的下载器，支持受限频道并发下载
架构：延迟初始化 Client，支持动态销毁和重建
"""
import asyncio
import logging
import os
from telethon import TelegramClient, events
from config import Config
from downloader import TelethonDownloader

# 配置日志（简化格式避免错误）
logging.basicConfig(
    level=Config.LOG_LEVEL,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("Main")
logging.getLogger('telethon').setLevel(logging.WARNING)

# ========== 全局变量（延迟初始化）==========
client = None
downloader = None
client_connected = False
event_handlers_registered = False

# ========== Getter 函数供 Dashboard 调用 ==========
def get_client():
    return client

def get_downloader():
    return downloader

def get_client_connected():
    return client_connected

def get_telethon_proxy():
    """获取 Telethon 格式的代理配置"""
    if not Config.PROXY:
        return None
    
    import python_socks
    scheme = Config.PROXY.get('scheme')
    proxy_type = python_socks.ProxyType.SOCKS5 if scheme == 'socks5' else python_socks.ProxyType.HTTP
    return (proxy_type, Config.PROXY['hostname'], Config.PROXY['port'])

def create_client():
    """创建新的 Telegram Client（延迟初始化）"""
    global client, downloader
    
    logger.info("🔧 正在创建 Telegram Client...")
    
    proxy = get_telethon_proxy()
    
    client = TelegramClient(
        Config.SESSION_NAME,
        Config.API_ID,
        Config.API_HASH,
        proxy=proxy,
        device_model="Desktop",
        system_version="Windows 10",
        app_version="4.16.8 x64",
        lang_code="en"
    )
    
    downloader = TelethonDownloader(client)
    
    logger.info("✅ Client 已创建")
    return client

async def destroy_client():
    """销毁 Client 并释放所有资源"""
    global client, downloader, client_connected, event_handlers_registered
    
    logger.info("🔌 正在销毁 Telegram Client...")
    
    try:
        if client:
            # 移除事件处理器
            if event_handlers_registered:
                client.remove_event_handler(message_handler)
                event_handlers_registered = False
            
            # 停止下载器
            if downloader:
                await downloader.stop()
            
            # 断开连接
            
            # 断开连接
            if client.is_connected():
                await client.disconnect()
            
            # 清空引用
            client = None
            downloader = None
            client_connected = False
        
        # 等待文件句柄释放
        await asyncio.sleep(1)
        
        logger.info("✅ Client 已销毁")
        
    except Exception as e:
        logger.error(f"❌ 销毁 Client 失败: {e}")

def ensure_client():
    """确保 Client 存在，不存在则创建"""
    global client
    if client is None:
        create_client()
    return client

async def message_handler(event):
    """消息处理器（收藏夹新消息）"""
    message = event.message
    logger.info(f"📨 收到消息 ID: {message.id}")
    
    # 处理指令
    if message.text and message.text.startswith('/'):
        cmd = message.text.strip().split()[0].lower()
        logger.info(f"🤖 收到指令: {cmd}")
        
        if cmd == '/ping':
            await event.reply("🏓 Pong! Bot 运行正常")
            return
        elif cmd == '/status':
            status_info = downloader.get_status_summary()
            await event.reply(f"📊 下载状态:\n{status_info}")
            return
        else:
            await event.reply(f"❓ 未知指令: {cmd}")
            return

    # 处理转发的媒体
    if message.fwd_from and message.media:
        await downloader.add_task(message)
        return

    # 处理媒体消息
    if message.media:
        await downloader.add_task(message)
        return

    # 处理链接
    if message.text and ('t.me/' in message.text or 'telegram.me/' in message.text):
        logger.info("🔗 检测到频道/消息链接")
        urls = [word for word in message.text.split() if 't.me/' in word or 'telegram.me/' in word]
        
        for url in urls:
            try:
                parts = url.split('/')
                if len(parts) >= 2:
                    channel_username = parts[-2]
                    msg_id = int(parts[-1]) if parts[-1].isdigit() else None
                    
                    if msg_id:
                        logger.info(f"📡 正在从 @{channel_username} 获取消息 {msg_id}")
                        remote_msg = await client.get_messages(channel_username, ids=msg_id)
                        
                        if remote_msg and remote_msg.media:
                            await downloader.add_task(remote_msg)
                        else:
                            logger.warning(f"⚠️ 消息 {msg_id} 无媒体内容")
            except Exception as e:
                logger.error(f"❌ 链接解析失败: {e}")
        return
    else:
        logger.info("ℹ️ 消息无媒体且无链接")

async def start_telegram_bot():
    """启动 Telegram Bot（非阻塞）"""
    global client_connected, event_handlers_registered
    
    try:
        logger.info("🔌 正在连接 Telegram...")
        
        # 确保 Client 存在
        ensure_client()
        
        # 连接
        await client.connect()
        
        # 检查认证
        if not await client.is_user_authorized():
            logger.warning("⚠️ Telegram Session 未认证或已过期")
            logger.info("💡 请访问 Dashboard 完成登录")
            await client.disconnect()
            client_connected = False
            return
        
        # 已认证，获取用户信息
        me = await client.get_me()
        logger.info(f"✅ 已登录: {me.first_name} (@{me.username})")
        logger.info(f"📂 下载目录: {Config.DOWNLOAD_DIR}")
        logger.info("💡 请转发视频到 '收藏夹' (Saved Messages) 开始下载")
        
        # 注册事件处理器
        if not event_handlers_registered:
            client.add_event_handler(message_handler, events.NewMessage(chats='me'))
            event_handlers_registered = True
        
        # 初始化下载器
        await downloader.initialize_workers()
        
        # 恢复未完成的任务
        await downloader.restore_tasks()
        
        client_connected = True
        
        # 保持连接
        await client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"❌ Telegram Client 启动失败: {e}")
        logger.warning("⚠️ Telegram 功能不可用，但 Dashboard 仍在运行")
        logger.info("💡 请访问 Dashboard 检查代理配置或网络设置")
        client_connected = False
        if client and client.is_connected():
            await client.disconnect()

async def main():
    logger.info("🚀 Telegram 下载器 (Telethon版) 启动中...")
    
    # 初始化数据库
    import database
    await database.init_db()

    # 无条件启动 Dashboard
    if Config.ENABLE_DASHBOARD:
        try:
            from dashboard import server
            # 设置全局引用，让 server 可以访问 client
            import __main__
            server.main_module = __main__
            
            asyncio.create_task(server.run_server())
            logger.info("🌐 Dashboard 服务已启动")
            logger.info(f"🌐 访问地址: http://{Config.DASHBOARD_HOST}:{Config.DASHBOARD_PORT}")
        except ImportError as e:
            logger.error(f"❌ Dashboard 启动失败 (依赖缺失?): {e}")
        except Exception as e:
            logger.error(f"❌ Dashboard 启动出错: {e}")
    
    # 检查应用状态
    session_exists = os.path.exists(f"{Config.SESSION_NAME}.session")
    
    if not Config.SETUP_COMPLETED:
        logger.info("📋 首次启动检测到，请访问 Dashboard 完成初始化")
        logger.info("💡 初始化向导: http://localhost:9595")
    elif not session_exists:
        logger.info("🔐 配置已完成，但未检测到 Telegram Session")
        logger.info("💡 请访问 Dashboard 完成 Telegram 登录")
    else:
        # 自动启动 Telegram Bot
        logger.info("🤖 检测到 Session，正在启动 Telegram Bot...")
        asyncio.create_task(start_telegram_bot())
    
    # 保持事件循环运行
    logger.info("⏳ 主程序运行中，按 Ctrl+C 退出")
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 收到退出信号，正在关闭...")
    except Exception as e:
        logger.error(f"❌ 程序异常退出: {e}")
        import traceback
        traceback.print_exc()
