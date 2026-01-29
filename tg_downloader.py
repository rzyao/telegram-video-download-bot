import os
import time
import asyncio
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

# --- 配置区 ---
API_ID = 36348713          # 换成你的 API ID
API_HASH = "cfa5fdaedc3b34f934d8d4152e41811a"      # 换成你的 API HASH
# 自动识别系统路径：如果是 Windows 测试则用第一个，Linux 生产环境用第二个
DOWNLOAD_DIR = "D:/tg_downloads" if os.name == 'nt' else "/mnt/downloads/telegram_videos"

# 你的 SOCKS5 代理
PROXY = {
    "scheme": "socks5",
    "hostname": "192.168.50.2",
    "port": 10088
}

# 确保目录存在
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# 禁用 IPv6 可能提高某些网络环境下的连接速度
app = Client("ayao_account", api_id=API_ID, api_hash=API_HASH, proxy=PROXY, ipv6=False)

# 增强版进度回调
def progress(current, total, start_time, file_name):
    elapsed_time = time.time() - start_time
    speed = current / elapsed_time if elapsed_time > 0 else 0
    percent = current * 100 / total
    # 每秒更新一次控制台，显示已下载、总大小、进度和速度
    print(f"\r[下载中] {file_name} | 进度: {percent:.1f}% | 速度: {speed/1024/1024:.2f} MB/s", end="")

@app.on_message(filters.me & (filters.video or filters.document))
async def download_handler(client, message):
    # 提取媒体对象
    media = message.video or message.document
    if not media:
        return

    # 确定文件名
    file_name = getattr(media, 'file_name', f"video_{message.id}.mp4") or f"video_{message.id}.mp4"
    full_path = os.path.join(DOWNLOAD_DIR, file_name)

    print(f"\n\n[新任务] 发现视频: {file_name}")
    print(f"[信息] 文件大小: {media.file_size / 1024 / 1024:.2f} MB")
    print(f"[存储] 目标路径: {full_path}")

    start_time = time.time()
    
    # 根据文件大小动态调整重试次数
    file_size_mb = media.file_size / 1024 / 1024
    if file_size_mb < 100:
        max_retries = 5
    elif file_size_mb < 1024:
        max_retries = 15
    else:
        max_retries = 50  # 超大文件需要更多重试
    
    print(f"[策略] 文件较大，设置最大重试次数: {max_retries}")
    
    for attempt in range(1, max_retries + 1):
        try:
            # 检查是否有已下载的部分
            if os.path.exists(full_path):
                existing_size = os.path.getsize(full_path)
                existing_percent = existing_size * 100 / media.file_size
                print(f"[续传] 发现已下载 {existing_percent:.1f}% ({existing_size/1024/1024:.2f} MB)")
            
            # 开始下载
            print(f"[连接] 尝试第 {attempt}/{max_retries} 次下载...")
            downloaded_path = await client.download_media(
                message,
                file_name=full_path,
                progress=progress,
                progress_args=(start_time, file_name)
            )
            
            # 校验文件完整性
            if downloaded_path and os.path.exists(downloaded_path):
                actual_size = os.path.getsize(downloaded_path)
                if actual_size == media.file_size:
                    elapsed = int(time.time() - start_time)
                    print(f"\n✅ 下载成功！耗时: {elapsed//60}分{elapsed%60}秒 | 大小: {actual_size/1024/1024:.2f} MB")
                    return  # 成功，退出函数
                else:
                    # 保留部分文件，显示当前进度
                    progress_percent = actual_size * 100 / media.file_size
                    print(f"\n⚠️ 第 {attempt} 次下载中断，当前进度: {progress_percent:.1f}%")
                    print(f"   已下载: {actual_size/1024/1024:.2f} MB / {media.file_size/1024/1024:.2f} MB")
                    # 注意：不删除文件，pyrogram 不支持真正的断点续传，但保留文件可以追踪进度
            else:
                print(f"\n⚠️ 第 {attempt} 次下载失败: 未收到有效文件")
            
        except FloodWait as e:
            print(f"\n⏳ 触发限制：需等待 {e.value} 秒后自动继续...")
            await asyncio.sleep(e.value)
            continue
        except Exception as e:
            print(f"\n⚠️ 第 {attempt} 次下载异常: {str(e)}")
        
        # 如果还有重试机会，等待后继续
        if attempt < max_retries:
            wait_time = min(5 + attempt * 2, 30)  # 逐渐增加等待时间, 最多30秒
            print(f"[重试] 等待 {wait_time} 秒后进行第 {attempt + 1} 次尝试...")
            await asyncio.sleep(wait_time)
    
    # 所有重试都失败了
    print(f"\n❌ 下载彻底失败: 已尝试 {max_retries} 次均未成功")
    print(f"💡 提示: 建议检查网络/代理稳定性，或稍后重试")

if __name__ == "__main__":
    print("🚀 Telegram Userbot 已启动...")
    print(f"📂 当前下载目录: {DOWNLOAD_DIR}")
    print("💡 提示：在手机上转发视频到'收藏夹'即可开始下载。")
    try:
        app.run()
    except Exception as e:
        print(f"程序启动失败: {e}")