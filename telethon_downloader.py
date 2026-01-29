"""
使用 Telethon 下载 Telegram 视频
Telethon 通常比 Pyrogram 更能处理受限频道和 DC 迁移
"""
import asyncio
import os
from telethon import TelegramClient, events, errors
from telethon.tl.types import InputDocument, InputFileLocation
from config import Config

# ==================== 配置 ====================
# 使用相同的 API ID/HASH
SESSION_NAME = "telethon_session"  # 新的 session 名称，避免冲突

# 目标视频信息 (从你的抓包或日志中获取)
# 也可以直接填入消息 ID 和频道用户名
TARGET_CHANNEL = "fangsongya"  # 频道用户名
MESSAGE_ID = 447               # 消息 ID

# 或者手动填写 File ID 信息 (如果消息无法获取)
USE_MANUAL_FILE_INFO = False
MANUAL_INFO = {
    "id": 5174878942443603046,
    "access_hash": 1175872009448698152,
    "file_reference": bytes([2,107,41,180,154,0,0,3,160,105,123,96,30,9,35,71,201,0,13,27,186,161,27,126,124,128,131,25,213])
}

async def progress_callback(current, total):
    """下载进度回调"""
    pct = current / total * 100
    print(f"\r⬇️ {pct:.1f}% | {current/1024/1024:.0f}/{total/1024/1024:.0f} MB    ", end="", flush=True)

async def main():
    print(f"🚀 启动 Telethon 客户端...")
    
    # 适配 Telethon 代理格式
    telethon_proxy = None
    if Config.PROXY:
        # Telethon proxy format: (python_socks.ProxyType.SOCKS5, 'host', port)
        # 或者简单的字典，但需要 key 匹配
        import python_socks
        scheme = Config.PROXY.get('scheme')
        proxy_type = python_socks.ProxyType.SOCKS5 if scheme == 'socks5' else python_socks.ProxyType.HTTP
        telethon_proxy = (proxy_type, Config.PROXY['hostname'], Config.PROXY['port'])

    # 初始化客户端
    client = TelegramClient(
        SESSION_NAME, 
        Config.API_ID, 
        Config.API_HASH,
        proxy=telethon_proxy,
        # 伪装
        device_model="Desktop",
        system_version="Windows 10",
        app_version="4.16.8 x64",
        lang_code="en"
    )
    
    await client.start()
    print("✅ 登录成功!")
    
    # 尝试访问目标
    try:
        if USE_MANUAL_FILE_INFO:
            print("🔧 使用手动文件信息下载...")
            # 构造 InputDocument
            input_doc = InputDocument(
                id=MANUAL_INFO['id'],
                access_hash=MANUAL_INFO['access_hash'],
                file_reference=MANUAL_INFO['file_reference']
            )
            file_location = input_doc
            file_name = "downloaded_video.mp4"
            file_size = 0 # 未知
        else:
            print(f"🔍 获取消息: {TARGET_CHANNEL}/{MESSAGE_ID}")
            # 获取消息
            message = await client.get_messages(TARGET_CHANNEL, ids=MESSAGE_ID)
            
            if not message:
                print("❌ 未找到消息")
                return
            
            if not message.media:
                print("❌ 消息没有媒体内容")
                # 尝试打印详细信息
                print(f"Content: {message.text}")
                # 即使没有高层 media，也许有 raw attachment
                if hasattr(message, 'restriction_reason'):
                    print(f"⚠️ 受限原因: {message.restriction_reason}")
                return
            
            print(f"📹 找到媒体: {message.file.name if message.file else '未知'}")
            print(f"📊 大小: {message.file.size / 1024 / 1024:.2f} MB")
            
            file_location = message.media
            file_name = message.file.name or f"video_{MESSAGE_ID}.mp4"
            file_size = message.file.size
            
        # 下载
        save_path = os.path.join(Config.DOWNLOAD_DIR, file_name)
        os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
        
        print(f"📥 开始下载到: {save_path}")
        
        # 检查断点
        # Telethon 原生支持断点吗？通常支持，但这里我们用简单的 download_media
        # 对于大文件，建议用 smart_downloader
        
        path = await client.download_media(
            file_location,
            file=save_path,
            progress_callback=progress_callback
        )
        
        print(f"\n✅ 下载完成: {path}")
        
    except errors.RPCError as e:
        print(f"\n❌ Telegram API 错误: {e}")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
