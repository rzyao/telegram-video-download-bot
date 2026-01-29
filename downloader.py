"""
Telegram 并发断点续传下载器
支持多线程分片下载 + 断点续传 + 自动合并
"""
import os
import json
import asyncio
import logging
import math
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict
from pyrogram import Client
from pyrogram.errors import FloodWait
from pyrogram.types import Message

from config import Config

logger = logging.getLogger(__name__)

@dataclass
class FilePart:
    """文件分片信息"""
    index: int                # 分片序号 (0, 1, 2...)
    start_offset: int         # 起始字节偏移
    end_offset: int           # 结束字节偏移 (包含)
    status: str = "pending"   # pending/downloading/completed
    
    @property
    def size(self) -> int:
        return self.end_offset - self.start_offset + 1

@dataclass
class DownloadTask:
    """下载任务数据结构"""
    message_id: int
    chat_id: int
    file_name: str
    file_size: int
    downloaded_bytes: int = 0
    status: str = "pending"
    parts: List[Dict] = field(default_factory=list) 
    error_message: str = ""
    created_at: str = ""
    updated_at: str = ""
    
    @property
    def progress_percent(self) -> float:
        if self.file_size == 0: return 0.0
        return self.downloaded_bytes * 100 / self.file_size

    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data):
        return cls(**data)


class ResumeDownloader:
    def __init__(self, client: Client):
        self.client = client
        self.current_task = None
        self._stop_requested = False
        self._semaphore = asyncio.Semaphore(Config.MAX_WORKERS)
        self._write_lock = asyncio.Lock()
        self._monitor_task = None
        self._monitor_stop_event = asyncio.Event()
    
    async def _monitor_progress(self, task: DownloadTask):
        """定期监控并打印进度（多行格式）"""
        start_time = datetime.now()
        last_bytes = task.downloaded_bytes
        first_print = True
        
        while not self._stop_requested:
            total_mb = task.file_size / 1024 / 1024
            
            # 收集分片状态
            current_bytes = 0
            completed_parts = []
            active_parts_str = []
            
            for part in task.parts:
                part_size = part['end_offset'] - part['start_offset'] + 1
                
                if part['status'] == 'completed':
                    current_bytes += part_size
                    completed_parts.append(f"P{part['index']}")
                elif part['status'] == 'downloading':
                    part_path = self._get_part_path(task, part['index'])
                    part_downloaded = 0
                    if os.path.exists(part_path):
                        part_downloaded = os.path.getsize(part_path)
                    
                    current_bytes += part_downloaded
                    p_percent = (part_downloaded / part_size) * 100 if part_size > 0 else 0
                    active_parts_str.append(f"P{part['index']}:{p_percent:.0f}%")
            
            downloaded_mb = current_bytes / 1024 / 1024
            percent = current_bytes * 100 / task.file_size if task.file_size > 0 else 0
            
            # 计算速度
            now = datetime.now()
            duration = (now - start_time).total_seconds()
            speed = (current_bytes - last_bytes) / duration if duration > 1 else 0
            if duration > 5:
                start_time = now
                last_bytes = current_bytes
            
            # 格式化输出
            completed_str = " ".join(completed_parts[-12:])  # 最多显示最近12个
            if len(completed_parts) > 12:
                completed_str = "... " + completed_str
            
            active_str = " ".join(active_parts_str[:8])
            if len(active_parts_str) > 8:
                active_str += " ..."
            
            # 构建多行输出
            line1 = f"{'═'*50}"
            line2 = f"📊 总进度: {percent:.1f}% | {downloaded_mb:.0f}/{total_mb:.0f} MB | {speed/1024/1024:.2f} MB/s"
            line3 = f"✅ 已完成 ({len(completed_parts)}): {completed_str if completed_str else '无'}"
            line4 = f"⬇️ 下载中 ({len(active_parts_str)}): {active_str if active_str else '无'}"
            line5 = f"{'═'*50}"
            
            # 使用 ANSI 转义码刷新多行
            # 如果不是第一次打印，先向上移动5行
            if not first_print:
                print("\033[5A", end="")  # 向上移动5行
            
            # 打印5行（每行先清除再打印）
            for line in [line1, line2, line3, line4, line5]:
                print(f"\033[2K{line}")
            
            first_print = False
            
            if percent >= 100:
                break
            
            # 如果收到了停止信号，且刚刚已经打印了最后一次（即 percent 可能是 100 或被中断），则退出
            if self._monitor_stop_event.is_set():
                break

            try:
                # 等待1秒，或者收到完成信号
                await asyncio.wait_for(self._monitor_stop_event.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
    
    def request_stop(self):
        self._stop_requested = True
        logger.info("⏹️ 正在停止所有任务...")

    def _get_part_path(self, task: DownloadTask, index: int) -> str:
        """获取分片临时文件路径: .download_dir/.progress/filename.partN"""
        return os.path.join(Config.DOWNLOAD_DIR, Config.PROGRESS_DIR, f"{task.file_name}.part{index}")
    
    def _get_media_from_message(self, message: Message):
        """从消息中提取媒体对象，支持 web_page"""
        media = message.video or message.document or message.video_note or message.voice or message.audio or message.photo or message.animation
        if not media and message.web_page:
            media = message.web_page.video or message.web_page.document or message.web_page.audio or message.web_page.photo
        return media

    def _init_or_load_task(self, message: Message) -> DownloadTask:
        """加载或初始化任务，并进行切片"""
        progress_file = Config.get_progress_file_path(message.id, message.chat.id)
        
        # 1. 尝试加载现有任务
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    task = DownloadTask.from_dict(data)
                    logger.info(f"🔄 恢复任务: {task.file_name} (进度: {task.progress_percent:.1f}%)")
                    return task
            except Exception as e:
                logger.warning(f"⚠️ 进度文件损坏，重新创建: {e}")
        
        # 2. 创建新任务
        media = self._get_media_from_message(message)
        
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
            # web_page 里的
            elif isinstance(media, type(message.web_page.video)) if message.web_page else False: ext = ".mp4" 
            else: ext = ".unknown"
            
            # 简单处理：如果来自 web_page，id 可能不唯一？用消息ID更安全
            file_name = f"{type(media).__name__.lower()}_{message.id}{ext}"
            
        file_size = getattr(media, 'file_size', 0)
        
        # 如果获取不到文件大小（极少情况），尝试从其他属性获取或报错
        if file_size == 0:
            logger.warning(f"⚠️ 无法获取文件大小，可能不支持断点续传: {file_name}")
            pass
        
        # 3. 计算切片
        part_size = Config.PART_SIZE
        # 防止 file_size 为 0 导致除零错误
        if file_size > 0:
            num_parts = math.ceil(file_size / part_size)
        else:
            num_parts = 1 # 兜底
            
        parts = []
        
        for i in range(num_parts):
            start = i * part_size
            end = min((i + 1) * part_size - 1, file_size - 1) if file_size > 0 else 0
            parts.append(asdict(FilePart(index=i, start_offset=start, end_offset=end)))
            
        task = DownloadTask(
            message_id=message.id,
            chat_id=message.chat.id,
            file_name=file_name,
            file_size=file_size,
            status="downloading",
            parts=parts,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )
        self._save_task(task)
        logger.info(f"🔪 文件已切分为 {num_parts} 个分片 (每片 {part_size/1024/1024:.0f}MB)")
        return task

    def _save_task(self, task: DownloadTask):
        """保存进度 (线程安全)"""
        task.updated_at = datetime.now().isoformat()
        progress_file = Config.get_progress_file_path(task.message_id, task.chat_id)
        
        # 计算已下载总字节
        total_downloaded = 0
        for p in task.parts:
            if p['status'] == 'completed':
                total_downloaded += (p['end_offset'] - p['start_offset'] + 1)
            elif p['status'] == 'downloading':
                # 简单的进度估算（可选：读取临时文件大小）
                part_path = self._get_part_path(task, p['index'])
                if os.path.exists(part_path):
                     total_downloaded += os.path.getsize(part_path)
        task.downloaded_bytes = total_downloaded

        try:
            temp_file = progress_file + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(temp_file, progress_file)
        except Exception as e:
            logger.error(f"保存进度失败: {e}")

    def _delete_file_parts(self, task: DownloadTask):
         for part in task.parts:
            path = self._get_part_path(task, part['index'])
            if os.path.exists(path):
                os.remove(path)

    async def download(self, message: Message) -> bool:
        self._stop_requested = False
        task = self._init_or_load_task(message)
        self.current_task = task
        
        # 筛选未完成的分片
        pending_parts = []
        for p_data in task.parts:
            part = FilePart(**p_data)
            # 简单检查：如果分片状态是 completed 且文件存在（或已合并），则跳过
            part_path = self._get_part_path(task, part.index)
            
            # 如果标记完成但临时文件不存在，且主文件也不存在，说明可能需要重下
            if part.status == "completed":
                if not os.path.exists(part_path) and not os.path.exists(os.path.join(Config.DOWNLOAD_DIR, task.file_name)):
                     part.status = "pending" # 重置
            
            if part.status != "completed":
                pending_parts.append(part)
        
        if not pending_parts:
            logger.info("🎉 检测到所有分片已完成，直接合并...")
            return await self._merge_parts(task)
            
        logger.info(f"🚀 开始并发下载: 需下载 {len(pending_parts)}/{len(task.parts)} 个分片")
        
        # 创建并发任务
        tasks = []
        for part in pending_parts:
            tasks.append(self._download_worker(message, task, part))
        
        # 启动监控
        self._monitor_stop_event.clear()
        self._monitor_task = asyncio.create_task(self._monitor_progress(task))
        
        # 等待所有任务完成
        try:
            await asyncio.gather(*tasks)
        finally:
            if self._monitor_task:
                # 发送停止信号，让 monitor 再刷新一次最后状态
                self._monitor_stop_event.set()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass
                print() # 换行
        
        if self._stop_requested:
            logger.info("⏸️ 下载已暂停")
            return False
            
        # 检查是否全部完成
        all_done = True
        for p in task.parts:
            if p['status'] != 'completed':
                all_done = False
                break
        
        if all_done:
            return await self._merge_parts(task)
        else:
            logger.warning("⚠️ 部分分片下载失败")
            return False

    async def _download_worker(self, message: Message, task: DownloadTask, part: FilePart):
        """单个 Worker 下载逻辑"""
        if self._stop_requested: return
        
        # 获取媒体的 file_id，以防止 stream_media 无法从 complex message (如 web_page) 中找到
        media = self._get_media_from_message(message)
        file_id = getattr(media, "file_id", None)
        # 如果获取不到 file_id (不应发生)，则回退到 message
        download_target = file_id if file_id else message
        
        # 获取信号量
        async with self._semaphore:
            if self._stop_requested: return
            
            # 更新状态
            part_dict = task.parts[part.index]
            part_dict['status'] = 'downloading'
            self._save_task(task)
            
            part_path = self._get_part_path(task, part.index)
            
            # 断点续传逻辑（分片内续传）
            current_offset = part.start_offset
            if os.path.exists(part_path):
                file_size = os.path.getsize(part_path)
                # 如果文件过大（异常），重置
                if file_size > part.size:
                    os.remove(part_path)
                else:
                    current_offset += file_size
            
            # 已经下载完了？
            if current_offset > part.end_offset:
                part_dict['status'] = 'completed'
                self._save_task(task)
                return

            # logger.info(f"⬇️ [Part {part.index}] 开始下载 ({part.size/1024/1024:.1f}MB)")
            
            # 计算 pyrogram stream 的 chunk 参数
            # stream_media 是以 1MB 为单位
            # 我们需要计算 jump 到第几个 1MB 块
            chunk_size = Config.CHUNK_SIZE
            start_chunk_idx = current_offset // chunk_size
            
            # 剩余需要下载的字节数
            bytes_needed = part.end_offset - current_offset + 1
            # 转换成需要下载多少个 1MB chunk
            chunks_needed = math.ceil(bytes_needed / chunk_size)
            
            retries = 0
            while retries < Config.MAX_RETRIES:
                try:
                    if self._stop_requested: break
                    
                    # 使用 limit 限制只下载该 part 需要的 chunk 数量
                    async for chunk in self.client.stream_media(
                        download_target,
                        offset=start_chunk_idx,
                        limit=chunks_needed
                    ):
                        if self._stop_requested: break
                        
                        # 写入文件
                        with open(part_path, 'ab') as f:
                            f.write(chunk)
                        
                        # 重要：stream_media 返回的 chunk 可能小于 1MB (最后一块)，或者 1MB
                        # 我们需要精确控制字节范围
                        current_offset += len(chunk)
                        
                        # 如果超出了该 part 的范围（通常因为 limit 是按 1MB 算的），截断
                        if current_offset > part.end_offset + 1:
                            # 这种情况理论上 limit 控制好了不会发生太多
                            pass

                        # 更新进度（减少IO频率，这里可以优化）
                        # self._save_task(task) 
                    
                    # 循环结束，检查是否下载够了
                    final_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
                    expected_size = part.size
                    
                    if final_size >= expected_size:
                         # 可能会多下载一点点（因为 chunk 是 1MB 对齐），截断到正确大小
                        if final_size > expected_size:
                            with open(part_path, 'r+b') as f:
                                f.truncate(expected_size)
                        
                        part_dict['status'] = 'completed'
                        self._save_task(task)
                        # logger.info(f"✅ [Part {part.index}] 完成")
                        return
                    else:
                        raise Exception(f"分片大小不匹配: {final_size} / {expected_size}")

                except FloodWait as e:
                    logger.warning(f"⏳ [Part {part.index}] 触发限流:等待 {e.value}s")
                    await asyncio.sleep(e.value)
                except Exception as e:
                    retries += 1
                    logger.warning(f"⚠️ [Part {part.index}] 异常: {e} (重试 {retries})")
                    await asyncio.sleep(Config.RETRY_DELAY_BASE)
            
            # 失败
            part_dict['status'] = 'failed'
            self._save_task(task)
            logger.error(f"❌ [Part {part.index}] 最终失败")

    async def _merge_parts(self, task: DownloadTask) -> bool:
        """合并所有分片"""
        logger.info("🧩 开始合并分片...")
        final_path = os.path.join(Config.DOWNLOAD_DIR, task.file_name)
        
        try:
            with open(final_path, 'wb') as outfile:
                for i in range(len(task.parts)):
                    part_path = self._get_part_path(task, i)
                    if not os.path.exists(part_path):
                        logger.error(f"❌ 缺失分片文件: {part_path}")
                        return False
                    
                    # 读写流合并
                    with open(part_path, 'rb') as infile:
                        while True:
                            chunk = infile.read(8 * 1024 * 1024) # 8MB buffer
                            if not chunk: break
                            outfile.write(chunk)
            
            logger.info(f"✅ 合并完成: {task.file_name}")
            
            # 校验大小
            if os.path.getsize(final_path) == task.file_size:
                task.status = "completed"
                task.downloaded_bytes = task.file_size
                self._save_task(task)
                # 清理分片
                self._delete_file_parts(task)
                os.remove(Config.get_progress_file_path(task.message_id, task.chat_id)) # 完成任务删记录
                return True
            else:
                logger.error("❌ 合并后文件大小不匹配")
                return False
                
        except Exception as e:
            logger.error(f"❌ 合并失败: {e}")
            return False


class TaskQueue:
    """任务队列（适配并发版）"""
    def __init__(self, client):
        self.downloader = ResumeDownloader(client)
        self.pending_messages = []
        self.is_running = False

    def add_task(self, message):
        self.pending_messages.append(message)
    
    def request_stop(self):
        self.downloader.request_stop()
        
    def get_pending_tasks(self):
        # 简单实现，读取 output 目录的 json
        return []

    async def process_queue(self):
        if self.is_running: return
        self.is_running = True
        
        while self.pending_messages:
            msg = self.pending_messages.pop(0)
            await self.downloader.download(msg)
            
        self.is_running = False
