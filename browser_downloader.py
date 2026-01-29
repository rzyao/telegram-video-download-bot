"""
使用 Playwright 控制浏览器下载 Telegram 视频
利用浏览器已登录的 Telegram Web 会话
"""
import asyncio
import os
from playwright.async_api import async_playwright
from config import Config

# 下载配置
TELEGRAM_CHANNEL = "fangsongya"  # 频道用户名
MESSAGE_ID = 447  # 消息 ID

# 或者直接使用消息链接
MESSAGE_LINK = f"https://t.me/{TELEGRAM_CHANNEL}/{MESSAGE_ID}"

# 下载目录
DOWNLOAD_DIR = Config.DOWNLOAD_DIR


async def download_with_browser():
    """使用浏览器下载"""
    
    print(f"🌐 启动浏览器...")
    print(f"📎 目标消息: {MESSAGE_LINK}")
    
    async with async_playwright() as p:
        # 使用持久化上下文保持登录状态
        user_data_dir = os.path.join(os.path.dirname(__file__), "browser_data")
        
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,  # 显示浏览器窗口
            downloads_path=DOWNLOAD_DIR,
            accept_downloads=True
        )
        
        page = await browser.new_page()
        
        # 打开 Telegram Web
        print(f"📱 打开 Telegram Web...")
        await page.goto("https://web.telegram.org/k/")
        
        # 等待加载
        await page.wait_for_load_state("networkidle")
        
        # 检查是否需要登录
        try:
            # 等待主界面出现（已登录）
            await page.wait_for_selector(".chats-container", timeout=10000)
            print(f"✅ 已登录 Telegram Web")
        except:
            print(f"⚠️ 请在浏览器中登录 Telegram...")
            print(f"💡 登录后程序会自动继续")
            # 等待用户登录
            await page.wait_for_selector(".chats-container", timeout=300000)  # 5分钟
            print(f"✅ 登录成功!")
        
        # 导航到目标消息
        print(f"🔍 导航到消息...")
        await page.goto(MESSAGE_LINK)
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)  # 等待消息加载
        
        # 查找视频元素并点击下载
        print(f"📥 查找下载按钮...")
        
        # 尝试右键点击视频获取下载选项
        video = await page.query_selector("video")
        if video:
            # 点击视频播放
            await video.click()
            await asyncio.sleep(1)
            
            # 查找下载按钮（通常在控制栏中）
            download_btn = await page.query_selector('[class*="download"]')
            if download_btn:
                print(f"⬇️ 点击下载...")
                
                # 监听下载事件
                async with page.expect_download() as download_info:
                    await download_btn.click()
                
                download = await download_info.value
                save_path = os.path.join(DOWNLOAD_DIR, download.suggested_filename)
                await download.save_as(save_path)
                print(f"✅ 下载完成: {save_path}")
            else:
                print(f"❌ 未找到下载按钮")
                print(f"💡 请手动在浏览器中下载")
                # 保持浏览器打开让用户操作
                await asyncio.sleep(300)
        else:
            print(f"❌ 未找到视频元素")
            # 打印页面内容用于调试
            content = await page.content()
            print(f"页面长度: {len(content)}")
        
        await browser.close()


async def main():
    # 检查 playwright 是否安装
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ 请先安装 playwright:")
        print("   pip install playwright")
        print("   playwright install chromium")
        return
    
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    await download_with_browser()


if __name__ == "__main__":
    asyncio.run(main())
