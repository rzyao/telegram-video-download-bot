"""
使用 Pyrogram 下载文件 - 直接使用 handle_download 内部方法
"""
import asyncio
import os
from pyrogram import Client
from pyrogram.raw import types
from config import Config

# ==================== 下载参数配置 ====================
DOWNLOAD_INFO = {
    "dc_id": 1,
    "document_id": 5174878942443603046,
    "access_hash": 1175872009448698152,
    "file_reference": bytes([2,107,41,180,154,0,0,3,160,105,123,96,30,9,35,71,201,0,13,27,186,161,27,126,124,128,131,25,213]),
    "file_size": 593571675,
    "file_name": "【#91唐哥】02舞蹈女孩 第一部.mp4"
}


def progress_callback(current, total):
    """进度回调"""
    pct = current / total * 100
    print(f"\r⬇️ {pct:.1f}% | {current/1024/1024:.0f}/{total/1024/1024:.0f} MB    ", end="", flush=True)


async def main():
    """主函数"""
    
    app = Client(
        Config.SESSION_NAME,
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        proxy=Config.PROXY,
        ipv6=False,
        device_model="Desktop",
        system_version="Windows 10",
        app_version="4.16.8 x64",
        lang_code="en"
    )
    
    async with app:
        print(f"✅ 客户端已连接")
        print(f"📁 文件: {DOWNLOAD_INFO['file_name']}")
        print(f"📊 大小: {DOWNLOAD_INFO['file_size'] / 1024 / 1024:.2f} MB")
        
        file_path = os.path.join(Config.DOWNLOAD_DIR, DOWNLOAD_INFO['file_name'])
        os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
        
        # 使用 Pyrogram 的 handle_download 方法
        # 这个方法会自动处理 DC 迁移
        try:
            result = await app.handle_download(
                (
                    types.InputDocumentFileLocation(
                        id=DOWNLOAD_INFO['document_id'],
                        access_hash=DOWNLOAD_INFO['access_hash'],
                        file_reference=DOWNLOAD_INFO['file_reference'],
                        thumb_size=""
                    ),
                    DOWNLOAD_INFO['dc_id'],
                    DOWNLOAD_INFO['file_size'],
                    None,  # progress
                    ()     # progress_args
                ),
                file_name=file_path,
                in_memory=False
            )
            
            print(f"\n✅ 下载完成: {result}")
            
        except AttributeError:
            # handle_download 可能不是公开方法，尝试其他方式
            print("⚠️ handle_download 不可用，尝试备用方法...")
            
            # 使用 get_file 方法（Pyrogram 2.0 内部方法）
            try:
                async for chunk in app.get_file(
                    file_id=types.InputDocumentFileLocation(
                        id=DOWNLOAD_INFO['document_id'],
                        access_hash=DOWNLOAD_INFO['access_hash'],
                        file_reference=DOWNLOAD_INFO['file_reference'],
                        thumb_size=""
                    ),
                    file_size=DOWNLOAD_INFO['file_size'],
                    dc_id=DOWNLOAD_INFO['dc_id'],
                    progress=progress_callback
                ):
                    # 写入文件
                    with open(file_path, 'ab') as f:
                        f.write(chunk)
                        
                print(f"\n✅ 下载完成: {file_path}")
                
            except Exception as e2:
                print(f"❌ 备用方法也失败: {e2}")
                import traceback
                traceback.print_exc()
                
        except Exception as e:
            print(f"❌ 下载失败: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
