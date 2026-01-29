"""
Telegram 断点续传下载器 - 主程序入口
监听收藏夹中的视频/文档消息，自动下载并支持断点续传
"""
import signal
import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.raw import functions, types as raw_types

from config import Config
from downloader import TaskQueue

# ==================== 日志配置 ====================
def setup_logging():
    """配置日志系统"""
    log_format = '%(asctime)s | %(levelname)-7s | %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))
    
    # 文件处理器
    file_handler = logging.FileHandler(Config.LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    
    # 配置根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # 降低 pyrogram 日志级别
    logging.getLogger('pyrogram').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ==================== 全局变量 ====================
app: Client = None
task_queue: TaskQueue = None
stop_event: asyncio.Event = None


# ==================== 信号处理 ====================
def signal_handler(signum, frame):
    """处理 Ctrl+C 信号，优雅退出"""
    global stop_event
    logger.info("\n⏹️ 收到退出信号，正在保存进度...")
    
    if task_queue:
        task_queue.request_stop()
        
    if stop_event:
        # 通知主循环退出
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(stop_event.set)
        except RuntimeError:
            # 如果没有运行的 loop（极少情况），直接设置
            pass



# ==================== 消息处理器 ====================
async def download_handler(client: Client, message):
    """处理收到的视频/文档消息"""
    # [DEBUG] 先打印最原始的消息结构，确保能看到输入
    logger.info(f"[DEBUG] 收到消息 (ID: {message.id}):\n{message}")
    
    media = message.video or message.document or message.animation or message.video_note or message.voice or message.audio or message.photo
    
    # 检查 web_page 中的媒体
    if not media and message.web_page:
        media = message.web_page.video or message.web_page.document or message.web_page.audio or message.web_page.photo
    
    # 如果当前消息没有媒体，但是是转发的消息，尝试从源频道获取
    if not media and message.forward_from_chat and message.forward_from_message_id:
        source_chat = message.forward_from_chat
        source_msg_id = message.forward_from_message_id
        logger.info(f"🔍 检测到转发消息，正在从源频道获取: {source_chat.title or source_chat.id} / {source_msg_id}")
        
        try:
            # 从源频道获取原始消息
            original_message = await client.get_messages(source_chat.id, source_msg_id)
            if original_message:
                # 尝试从源消息获取媒体
                media = original_message.video or original_message.document or original_message.animation or original_message.video_note or original_message.voice or original_message.audio or original_message.photo
                
                if not media and original_message.web_page:
                    media = original_message.web_page.video or original_message.web_page.document or original_message.web_page.audio or original_message.web_page.photo
                
                if media:
                    message = original_message  # 使用原始消息进行下载
                    logger.info(f"✅ 成功获取源消息，媒体类型: {type(media).__name__}")
                else:
                    # 尝试打印原始 raw 数据
                    logger.warning(f"❌ 源消息没有可下载的媒体")
                    logger.info(f"[DEBUG] 源消息高层对象:\n{original_message}")
                    
                    # --- Raw API Debugging ---
                    try:
                        logger.info("� 尝试通过 Raw API 获取底层数据...")
                        peer = await client.resolve_peer(source_chat.id)
                        # 注意：对于频道，通常需要使用 channels.GetMessages
                        # 如果是频道/超级群组
                        if isinstance(peer, (raw_types.InputPeerChannel, raw_types.InputChannel)):
                            raw_msgs = await client.invoke(
                                functions.channels.GetMessages(
                                    channel=peer,
                                    id=[raw_types.InputMessageID(id=source_msg_id)]
                                )
                            )
                        else:
                            # 私聊或普通群组 logic (少见)
                            raw_msgs = await client.invoke(
                                functions.messages.GetMessages(
                                    id=[raw_types.InputMessageID(id=source_msg_id)]
                                )
                            )
                            
                        if raw_msgs and hasattr(raw_msgs, 'messages'):
                            for m in raw_msgs.messages:
                                logger.info(f"[DEBUG] Raw Message Data:\n{str(m)}")
                                if isinstance(m, raw_types.Message):
                                    if isinstance(m.media, raw_types.MessageMediaDocument):
                                        logger.info(f"📄 发现 Raw Document: {m.media.document}")
                                    elif isinstance(m.media, raw_types.MessageMediaUnsupported):
                                        logger.error("❌ 媒体内容被服务器拦截 (MessageMediaUnsupported)")
                                        # 打印限制详情
                                        if hasattr(m, 'restriction_reason') and m.restriction_reason:
                                            for r in m.restriction_reason:
                                                logger.info(f"   Configs: {r.platform} - {r.reason}")
                                        
                                        logger.warning(f"\n{'!'*60}")
                                        logger.warning("� 严重故障: 您的 Session 仍被识别为受限设备 (如旧的 Android)")
                                        logger.warning("👉 必须执行的操作 (由官方机制决定):")
                                        logger.warning("   1. 停止程序 (Ctrl+C)。")
                                        logger.warning(f"   2. 删除文件: {Config.SESSION_NAME}.session (位于程序同级目录)。")
                                        logger.warning("   3. 重新运行程序并扫码/输入手机号登录。")
                                        logger.warning("💡 原因: 该频道可能对所有非官方客户端（API ID）实施了屏蔽，")
                                        logger.warning("         或者您的账号在服务器端仍被标记为受限区域。")
                                        logger.warning("         尝试访问 Web 版 Telegram 确认该频道是否可见。")
                                        logger.warning(f"{'!'*60}\n")
                        else:
                            logger.warning("❌ Raw API 返回空")
                            
                    except Exception as raw_e:
                        logger.error(f"❌ Raw API 调试失败: {raw_e}")
                    # -------------------------

                    logger.info(f"💡 提示: 请确认源消息是视频/文档/照片/语音等文件")
                    pass
            else:
                logger.warning(f"❌ 无法获取源消息 (返回空)")
                return
        except Exception as e:
            logger.error(f"❌ 获取源消息失败: {e}")
            pass
    
    if not media:
        logger.warning(f"❌ 消息没有包含支持的媒体类型 (ID: {message.id})")
        logger.info(f"[DEBUG] 当前消息完整内容 (收藏夹):\n{message}")
        return
    
    # 尝试获取文件名，如果没有则自动生成
    file_name = getattr(media, 'file_name', None)
    if not file_name:
        # 根据媒体类型生成后缀
        ext = ""
        if getattr(message, 'video_note', None): ext = ".mp4"
        elif getattr(message, 'voice', None): ext = ".ogg"
        elif getattr(message, 'audio', None): ext = ".mp3"
        elif getattr(message, 'photo', None): ext = ".jpg"
        elif getattr(message, 'video', None): ext = ".mp4"
        else: ext = ".unknown"
        
        file_name = f"{type(media).__name__.lower()}_{message.id}{ext}"
    
    file_size_mb = getattr(media, 'file_size', 0) / 1024 / 1024
    
    logger.info(f"\n{'='*50}")
    logger.info(f"📹 发现新媒体: {file_name}")
    logger.info(f"📊 文件大小: {file_size_mb:.2f} MB")
    logger.info(f"{'='*50}")
    
    # 添加到队列并处理
    task_queue.add_task(message)
    await task_queue.process_queue()


async def show_pending_tasks():
    """显示未完成的任务"""
    pending = task_queue.get_pending_tasks()
    if pending:
        logger.info(f"\n📋 发现 {len(pending)} 个未完成的任务:")
        for task in pending:
            logger.info(f"   - {task.file_name}: {task.progress_percent:.1f}% ({task.status})")
        logger.info("💡 这些任务将在收到对应消息时自动恢复")


# ==================== 主函数 ====================
async def main():
    """主函数"""
    global app, task_queue, stop_event
    
    # 初始化全局事件
    stop_event = asyncio.Event()
    
    # 初始化日志等
    setup_logging()
    Config.ensure_directories()
    Config.print_config()
    
    # 注册信号处理 (注意：在 Windows 上，信号处理只能在主线程运行，且 asyncio loop 可能无法立即响应)
    # 我们只设置标志，并在 main loop 中响应
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 创建客户端
    # max_concurrent_transmissions 控制同时进行的上传/下载数，默认为1
    # 伪装成 Telegram Desktop 官方客户端，以访问受限内容
    app = Client(
        Config.SESSION_NAME,
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        proxy=Config.PROXY,
        # 终极伪装: Telegram Desktop (Windows) 5.9.1
        # 使用官方 tdesktop 的标准识别参数
        device_model="Desktop",
        system_version="Windows 10", 
        app_version="5.9.1 x64",
        lang_code="en"
    )
    
    # 创建任务队列
    task_queue = TaskQueue(app)
    
    # 添加消息处理器
    # 监听收藏夹中的所有消息，让 handler 内部判断是否需要处理
    app.add_handler(MessageHandler(
        download_handler,
        filters.chat('me')  # 只监听收藏夹，不限定媒体类型
    ))
    
    # [调试] 监听并打印所有消息（已关闭）
    # async def debug_handler(client, message):
    #     full_msg = await client.get_messages(message.chat.id, message.id)
    #     logger.info(f"[DEBUG] 完整消息: {str(full_msg)[:5000]}")
    # app.add_handler(MessageHandler(debug_handler), group=1)
    
    # 启动客户端
    logger.info("🚀 Telegram 断点续传下载器启动中...")
    
    await app.start()
    
    # --- 检测出口 IP 地址 ---
    try:
        import aiohttp
        from aiohttp_socks import ProxyConnector
        
        if Config.USE_PROXY and Config.PROXY:
            # 构建 SOCKS5 代理 URL
            proxy_url = f"socks5://{Config.PROXY['hostname']}:{Config.PROXY['port']}"
            connector = ProxyConnector.from_url(proxy_url)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get("https://httpbin.org/ip", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    logger.info(f"🌐 当前出口 IP (通过代理): {data.get('origin', '未知')}")
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://httpbin.org/ip", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    logger.info(f"🌐 当前出口 IP (直连): {data.get('origin', '未知')}")
    except ImportError:
        logger.warning("⚠️ 需要安装 aiohttp 和 aiohttp-socks 来检测 IP: pip install aiohttp aiohttp-socks")
    except Exception as e:
        logger.warning(f"⚠️ IP 检测失败: {e}")
    
    # --- 验证 Pyrogram 是否通过代理连接 ---
    try:
        from pyrogram.raw import functions as raw_functions
        # 调用 Telegram API 获取最近的数据中心（基于客户端 IP 判断）
        nearest_dc = await app.invoke(raw_functions.help.GetNearestDc())
        logger.info(f"🔌 Pyrogram 连接验证:")
        logger.info(f"   - 当前连接的 DC: DC{nearest_dc.this_dc}")
        logger.info(f"   - 服务端判断最近的 DC: DC{nearest_dc.nearest_dc} (基于出口 IP 位置)")
        logger.info(f"   - 国家代码: {nearest_dc.country}")
        
        # 如果国家代码与你本地不同，说明代理生效
        if Config.USE_PROXY:
            logger.info(f"   💡 如果国家代码与你本地不同，说明 Pyrogram 正在使用代理")
    except Exception as e:
        logger.warning(f"⚠️ Pyrogram 代理验证失败: {e}")
    
    # 尝试启用敏感内容设置（用于访问受限频道）
    # 尝试启用敏感内容设置（用于访问受限频道）
    try:
        from pyrogram.raw import functions
        # 检查当前设置
        settings = await app.invoke(functions.account.GetContentSettings())
        logger.info(f"📋 敏感内容设置状态: 已启用={settings.sensitive_enabled}, 可修改={settings.sensitive_can_change}")
        
        if not settings.sensitive_enabled:
            if settings.sensitive_can_change:
                try:
                    await app.invoke(functions.account.SetContentSettings(sensitive_enabled=True))
                    logger.info("✅ 已自动发送【启用敏感内容】请求")
                    # 再次检查确认
                    new_settings = await app.invoke(functions.account.GetContentSettings())
                    if new_settings.sensitive_enabled:
                        logger.info("🎉 敏感内容限制已成功解除！")
                    else:
                        logger.warning("⚠️ 请求已发送但似乎未立即生效，建议重启程序或稍后重试")
                except Exception as e:
                    logger.warning(f"⚠️ 尝试自动启用敏感内容失败: {e}")
            else:
                logger.warning(f"\n{'!'*60}")
                logger.warning("⛔ 无法通过 API 自动解除敏感内容限制 (权限受限)")
                logger.warning("👉 请手动操作: 访问 https://web.telegram.org -> Settings -> Privacy and Security -> Disable filtering")
                logger.warning(f"{'!'*60}\n")
        else:
            logger.info("✅ 敏感内容显示已开启 (无需操作)")

    except Exception as e:
        logger.warning(f"⚠️ 检查敏感内容设置时出错: {e}")
    
    logger.info("✅ 客户端已连接")
    logger.info(f"📂 下载目录: {Config.DOWNLOAD_DIR}")
    logger.info("💡 在 Telegram 中转发视频到'收藏夹'即可开始下载")
    logger.info("💡 按 Ctrl+C 可安全退出并保存进度")
    
    # 显示未完成任务
    await show_pending_tasks()
    
    # 等待退出信号
    await stop_event.wait()
    
    logger.info("⏳ 正在停止 Telegram 客户端...")
    await app.stop()
    logger.info("👋 程序已安全退出")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("程序已退出")
    except Exception as e:
        logger.error(f"程序异常退出: {e}")
