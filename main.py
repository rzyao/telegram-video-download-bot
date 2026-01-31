"""
Telegram Downloader (Telethon Version)
基于 Telethon 的下载器，支持受限频道和并发下载
"""
import asyncio
import logging
from telethon import TelegramClient, events
from config import Config
from config import Config
from downloader import TelethonDownloader

# 配置日志
logging.basicConfig(
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=Config.LOG_LEVEL
)
logger = logging.getLogger("Main")

# 降低 Telethon 日志级别，避免干扰进度显示
logging.getLogger('telethon').setLevel(logging.WARNING)

# 适配 Telethon 代理格式
telethon_proxy = None
if Config.PROXY:
    import python_socks
    scheme = Config.PROXY.get('scheme')
    proxy_type = python_socks.ProxyType.SOCKS5 if scheme == 'socks5' else python_socks.ProxyType.HTTP
    telethon_proxy = (proxy_type, Config.PROXY['hostname'], Config.PROXY['port'])

# 初始化客户端
client = TelegramClient(
    Config.SESSION_NAME,  # 使用配置中的 Session 名称
    Config.API_ID,
    Config.API_HASH,
    proxy=telethon_proxy,
    device_model="Desktop",
    system_version="Windows 10",
    app_version="4.16.8 x64",
    lang_code="en"
)

# 初始化下载器
downloader = TelethonDownloader(client)

@client.on(events.NewMessage(chats='me'))
async def handler(event):
    """监听收藏夹 (Saved Messages) 的新消息"""
    message = event.message
    
    # 打印消息基本信息
    logger.info(f"📨 收到消息 ID: {message.id}")
    
    target_msg = message
    
    # 检查是否包含媒体
    if not message.media:
        # 可能是转发的消息，尝试访问源消息
        if message.fwd_from:
            try:
                # 获取源频道 ID 和消息 ID
                # Telethon 会自动处理很多细节，但如果是受限频道，我们还是需要尝试获取
                if message.fwd_from.from_id:
                    chat_id = message.fwd_from.from_id
                    msg_id = message.fwd_from.channel_post
                    
                    logger.info(f"🔍 检测到转发消息，尝试获取源消息: {chat_id}/{msg_id}")
                    
                    # 获取源消息
                    # Telethon 的 get_messages 处理受限内容比 Pyrogram 强
                    source_msgs = await client.get_messages(chat_id, ids=msg_id)
                    if source_msgs and source_msgs.media:
                        target_msg = source_msgs
                        logger.info(f"✅ 成功获取源消息媒体: {target_msg.file.mime_type}")
                    else:
                        logger.warning("❌ 源消息也没有媒体或无法访问")
            except Exception as e:
                logger.error(f"❌ 获取源消息失败: {e}")
    
    # 再次检查是否有媒体
    if target_msg.media:
        # 过滤类型：只下载视频和文件
        if target_msg.video or target_msg.document or target_msg.gif:
            await downloader.add_task(target_msg)
        else:
            logger.info(f"ℹ️ 忽略非视频/文件媒体: {type(target_msg.media)}")
            
    # 如果没有媒体，检查是否包含 t.me 链接
    elif message.text:
        import re
        # 匹配两种格式：
        # 1. 私有频道: https://t.me/c/12345/678
        # 2. 公开频道: https://t.me/username/678
        url_pattern = re.compile(r"https?://t\.me/(?:c/(\d+)|([a-zA-Z0-9_]+))/(\d+)")
        match = url_pattern.search(message.text)
        
        if match:
            private_id, username, msg_id = match.groups()
            msg_id = int(msg_id)
            
            chat_identifier = None
            if private_id:
                # 私有频道 ID 通常需要 -100 前缀
                chat_identifier = int(f"-100{private_id}")
            else:
                chat_identifier = username
                
            logger.info(f"🔗 检测到链接，尝试从 {chat_identifier} 获取消息 ID: {msg_id}")
            
            try:
                # 获取原消息
                source_msg = await client.get_messages(chat_identifier, ids=msg_id)
                
                if source_msg and source_msg.media:
                    logger.info(f"✅ 成功通过链接获取媒体: {source_msg.file.mime_type}")
                    # 递归检查（防止获取到的还是链接？通常就是媒体了）
                    if source_msg.video or source_msg.document or source_msg.gif:
                        await downloader.add_task(source_msg)
                    else:
                        logger.warning("❌ 链接指向的消息不是视频/文件")
                else:
                    logger.warning("❌ 链接指向的消息无法访问或无媒体")
            except Exception as e:
                logger.error(f"❌ 通过链接获取消息失败: {e}")
                
    else:
        logger.info("ℹ️ 消息无媒体且无链接")

async def main():
    logger.info("🚀 Telegram 下载器 (Telethon版) 启动中...")
    
    await client.start()
    
    me = await client.get_me()
    logger.info(f"✅ 已登录: {me.first_name} (@{me.username})")
    logger.info(f"📂 下载目录: {Config.DOWNLOAD_DIR}")
    logger.info("💡 请转发视频到 '收藏夹' (Saved Messages) 开始下载")
    
    # 初始化下载 Worker 池
    await downloader.initialize_workers()
    
    # 保持运行
    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 程序已停止")
