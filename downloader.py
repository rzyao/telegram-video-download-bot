"""
Telethon 下载引擎 (完整版)
实现并发下载、断点续传（跨重启）、任务管理
"""
import asyncio
import os
import time
import math
import json
from collections import deque
from datetime import datetime
from typing import Optional, Deque, List, Dict
from dataclasses import dataclass, asdict, field
from telethon import TelegramClient, types
from telethon.sessions import StringSession
from config import Config
import logging

# 配置日志
logging.basicConfig(
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=Config.LOG_LEVEL,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Config.LOG_FILE, encoding='utf-8')
    ]
)
logger = logging.getLogger("TelethonEngine")

# ==================== 数据结构 ====================

@dataclass
class FilePart:
    """文件分片信息"""
    index: int                # 分片序号
    start_offset: int         # 起始字节 (包含)
    end_offset: int           # 结束字节 (包含)
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
    created_at: str = ""
    updated_at: str = ""
    
    # 运行时引用 (不保存到 JSON)
    message: object = field(default=None, repr=False)
    
    @property
    def progress_percent(self) -> float:
        if self.file_size == 0: return 0.0
        return self.downloaded_bytes * 100 / self.file_size

    def to_dict(self):
        # 手动构建字典，避免 asdict 深度递归导致序列化 message 出错
        return {
            "message_id": self.message_id,
            "chat_id": self.chat_id,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "downloaded_bytes": self.downloaded_bytes,
            "status": self.status,
            "parts": self.parts,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }
    
    @classmethod
    def from_dict(cls, data):
        return cls(**data)


# ==================== 下载管理器 ====================

class TelethonDownloader:
    def __init__(self, client: TelegramClient):
        self.client = client
        self.tasks: Deque[DownloadTask] = deque()
        self.current_task: Optional[DownloadTask] = None
        self.is_running = False
        
        # Worker Pool
        self.workers: List[TelegramClient] = []
        self.worker_lock = asyncio.Lock()
        self.worker_queue = asyncio.Queue()
        self._session_str = None  # 延迟保存 Session 字符串
        
        # 确保目录存在
        Config.ensure_directories()

    async def _ensure_workers_ready(self):
        """确保 Worker 客户端池就绪（延迟初始化）"""
        async with self.worker_lock:
            # 如果队列为空但有 workers，检查连接状态
            if self.workers:
                # 检查第一个 worker 的连接状态
                sample_worker = self.workers[0]
                if not sample_worker.is_connected():
                    logger.info("🔄 检测到 Worker 连接断开，正在重新连接...")
                    await self._reconnect_all_workers()
                return
            
            # 首次初始化
            await self._initialize_workers_internal()
    
    async def _initialize_workers_internal(self):
        """内部初始化方法"""
        logger.info(f"🔧 正在初始化 {Config.WORKER_COUNT} 个 Worker 客户端...")
        
        # 导出主客户端 Session
        self._session_str = StringSession.save(self.client.session)
        
        for i in range(Config.WORKER_COUNT):
            try:
                worker = TelegramClient(
                    StringSession(self._session_str),
                    Config.API_ID,
                    Config.API_HASH,
                    proxy=self.client._proxy,
                    device_model="Desktop",
                    system_version="Windows 10",
                    app_version="4.16.8 x64",
                    lang_code="en"
                )
                await worker.connect()
                self.workers.append(worker)
                self.worker_queue.put_nowait(worker)
                logger.info(f"  ✅ Worker {i+1} 就绪")
            except Exception as e:
                logger.error(f"  ❌ Worker {i+1} 初始化失败: {e}")
        
        logger.info(f"✨ Worker 初始化完成，可用: {len(self.workers)}")

    async def _reconnect_all_workers(self):
        """重新连接所有 Worker"""
        # 清空队列
        while not self.worker_queue.empty():
            try:
                self.worker_queue.get_nowait()
            except:
                break
        
        # 重新连接每个 worker
        for i, worker in enumerate(self.workers):
            try:
                if not worker.is_connected():
                    await worker.connect()
                self.worker_queue.put_nowait(worker)
                logger.info(f"  ✅ Worker {i+1} 重连成功")
            except Exception as e:
                logger.error(f"  ❌ Worker {i+1} 重连失败: {e}")

    async def initialize_workers(self):
        """公开的初始化方法（兼容旧调用，但现在是可选的）"""
        # 保留此方法以兼容 main.py 中的调用，但实际初始化延迟到首次使用
        logger.info("💡 Worker 将在首次下载时初始化")
        
    async def add_task(self, message):
        """添加下载任务 (支持断点续传)"""
        task = self._init_or_load_task(message)
        
        # 如果任务已经完成（且文件完整），则跳过
        file_path = os.path.join(Config.DOWNLOAD_DIR, task.file_name)
        if task.status == 'completed' and os.path.exists(file_path) and os.path.getsize(file_path) == task.file_size:
            logger.info(f"✅ 文件已存在，跳过: {task.file_name}")
            return

        # 加入队列
        # 避免队列中重复添加
        for t in self.tasks:
            if t.message_id == task.message_id and t.chat_id == task.chat_id:
                logger.info(f"⚠️ 任务已在队列中: {task.file_name}")
                return
        
        self.tasks.append(task)
        logger.info(f"➕ 已添加任务: {task.file_name} ({task.file_size/1024/1024:.2f} MB) [队列: {len(self.tasks)}]")
        
        # 启动处理
        if not self.is_running:
            asyncio.create_task(self.process_queue())

    def _init_or_load_task(self, message) -> DownloadTask:
        """加载或初始化任务"""
        progress_file = Config.get_progress_file_path(message.id, message.chat_id)
        
        # 1. 尝试加载现有任务
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    task = DownloadTask.from_dict(data)
                    task.message = message # 重新关联 message 对象
                    logger.info(f"🔄 恢复任务: {task.file_name} (进度: {task.progress_percent:.1f}%)")
                    return task
            except Exception as e:
                logger.warning(f"⚠️ 进度文件损坏，重新创建: {e}")
        
        # 2. 创建新任务
        # 获取文件名
        file_name = "unknown"
        if message.file:
            file_name = message.file.name or f"file_{message.id}{message.file.ext}"
        
        file_size = message.file.size if message.file else 0
        
        # 计算分片
        part_size = Config.PART_SIZE
        num_parts = math.ceil(file_size / part_size) if file_size > 0 else 1
        
        parts = []
        for i in range(num_parts):
            start = i * part_size
            end = min((i + 1) * part_size - 1, file_size - 1) if file_size > 0 else 0
            parts.append(asdict(FilePart(index=i, start_offset=start, end_offset=end)))
            
        task = DownloadTask(
            message_id=message.id,
            chat_id=message.chat_id,
            file_name=file_name,
            file_size=file_size,
            status="pending",
            parts=parts,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            message=message
        )
        self._save_task(task)
        return task

    def _save_task(self, task: DownloadTask):
        """保存任务进度到 JSON"""
        task.updated_at = datetime.now().isoformat()
        progress_file = Config.get_progress_file_path(task.message_id, task.chat_id)
        
        # 计算已下载量
        total = 0
        # 简单估算：完成的分片 + 正在下载分片的已下载量
        # 这里为了简化，只统计 'completed' 的分片。更精确的需要读取 .part 文件大小
        
        try:
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ 保存进度失败: {e}")

    async def process_queue(self):
        """处理任务队列"""
        if self.is_running: return
        self.is_running = True
        
        while self.tasks:
            self.current_task = self.tasks.popleft()
            task = self.current_task
            
            logger.info(f"\n{'='*50}")
            logger.info(f"🚀 开始任务: {task.file_name}")
            logger.info(f"📊 文件大小: {task.file_size/1024/1024:.2f} MB")
            logger.info(f"{'='*50}")
            
            try:
                # 确保 Worker 就绪
                await self._ensure_workers_ready()
                
                task.status = "downloading"
                self._save_task(task)
                
                # 最终文件路径
                file_path = os.path.join(Config.DOWNLOAD_DIR, task.file_name)
                
                # 1. 扫描分片状态
                # 检查哪些分片还没完成
                pending_parts = []
                self.active_parts = {} # Map: part_index -> downloaded
                self.part_status = {}  # Map: part_index -> status
                
                for p_data in task.parts:
                    p = FilePart(**p_data)
                    part_path = f"{file_path}.part{p.index}"
                    
                    # 检查分片文件真实状态
                    if p.status == 'completed':
                        # 如果标记完成但文件不存在，重置
                        if not os.path.exists(part_path) and not os.path.exists(file_path):
                            p.status = 'pending'
                            
                    if p.status != 'completed':
                        pending_parts.append(p)
                        self.active_parts[p.index] = 0
                        self.part_status[p.index] = 'pending'
                    else:
                        # 已完成的分片
                        self.active_parts[p.index] = p.size
                        self.part_status[p.index] = 'completed'
                
                if not pending_parts:
                    logger.info("🎉 所有分片已完成，准备合并...")
                else:
                    logger.info(f"⚡ 需要下载 {len(pending_parts)}/{len(task.parts)} 个分片")
                    
                    # 并发控制
                    semaphore = asyncio.Semaphore(Config.MAX_WORKERS)
                    download_tasks = []
                    
                    # 初始化进度追踪
                    self._total_parts = len(task.parts)
                    self._start_time = time.time()
                    
                    # 启动监控面板
                    monitor_stop = asyncio.Event()
                    monitor_task = asyncio.create_task(
                        self.monitor_progress(task, len(task.parts), monitor_stop)
                    )
                    
                    for part in pending_parts:
                        part_path = f"{file_path}.part{part.index}"
                        download_tasks.append(
                            self.download_part_worker(semaphore, task, part, part_path)
                        )
                    
                    # 等待下载完成
                    await asyncio.gather(*download_tasks)
                    
                    # 停止监控
                    monitor_stop.set()
                    await asyncio.sleep(0.1)  # 让监控有机会最后刷新一次
                
                # 再次检查是否全部完成
                all_done = True
                for p_data in task.parts:
                    if p_data['status'] != 'completed':
                        all_done = False
                        break
                
                if all_done:
                    # 合并这一步
                    await self.merge_parts(task, file_path)
                else:
                    task.status = "error"
                    logger.error("❌ 部分分片下载失败")
                    
            except Exception as e:
                task.status = "error"
                logger.error(f"❌ 任务出错: {e}")
                import traceback
                traceback.print_exc()
            finally:
                self._save_task(task)
                self.current_task = None
                
        self.is_running = False
        logger.info("💤 队列已空")

    async def download_part_worker(self, semaphore, task, part: FilePart, part_path):
        """Worker - 下载单个分片"""
        async with semaphore:
            self.part_status[part.index] = "waiting"
            worker_client = await self.worker_queue.get()
            
            try:
                self.part_status[part.index] = "downloading"
                task.parts[part.index]['status'] = 'downloading'
                # 移除 INFO 日志以避免干扰监控面板
                # logger.info(f"▶️  Worker 开始下载分片 P{part.index} ({part.size/1024/1024:.1f} MB)")
                
                try:
                    await self.download_part_telethon(worker_client, task.message, part_path, part.index, part.start_offset, part.end_offset)
                    
                    task.parts[part.index]['status'] = 'completed'
                    self.part_status[part.index] = "completed"
                    # 移除 INFO 日志以避免干扰监控面板
                    # logger.info(f"✅ 分片 P{part.index} 完成")
                    
                except Exception as e:
                    self.part_status[part.index] = "error"
                    task.parts[part.index]['status'] = 'error'
                    logger.error(f"❌ P{part.index} 失败: {e}")
                    raise e
            finally:
                self.worker_queue.put_nowait(worker_client)

    async def download_part_telethon(self, client: TelegramClient, message, part_path, part_index, start_byte, end_byte):
        """底层的 Telethon 分片下载"""
        current_offset = 0
        expected_size = end_byte - start_byte + 1
        
        # 断点续传检查
        if os.path.exists(part_path):
            current = os.path.getsize(part_path)
            if current >= expected_size:
                self.active_parts[part_index] = expected_size
                return
            current_offset = current
            self.active_parts[part_index] = current_offset
        
        request_offset = start_byte + current_offset
        bytes_to_download = expected_size - current_offset
        
        if bytes_to_download <= 0: return
        
        mode = 'ab' if current_offset > 0 else 'wb'
        
        with open(part_path, mode) as f:
            async for chunk in client.iter_download(
                message,
                offset=request_offset,
                limit=bytes_to_download,
                chunk_size=512 * 1024, # 512KB
                request_size=512 * 1024,
            ):
                # 计算剩余需要写入的字节数，防止溢出
                remaining = expected_size - current_offset
                if remaining <= 0:
                    break
                
                # 如果 chunk 超出剩余空间，截断
                if len(chunk) > remaining:
                    chunk = chunk[:remaining]
                
                f.write(chunk)
                current_offset += len(chunk)
                
                # 更新实时进度 (用于监控面板)，限制不超过预期大小
                self.active_parts[part_index] = min(current_offset, expected_size)

    async def merge_parts(self, task, file_path):
        """合并分片"""
        logger.info(f"\n🔄 正在合并 {len(task.parts)} 个分片...")
        
        # 简单检查所有分片是否都在
        for p in task.parts:
            part_path = f"{file_path}.part{p['index']}"
            if not os.path.exists(part_path):
                logger.error(f"❌ 缺失分片文件: {part_path}")
                return

        with open(file_path, 'wb') as outfile:
            for p in task.parts:
                part_path = f"{file_path}.part{p['index']}"
                with open(part_path, 'rb') as infile:
                    while True:
                        chunk = infile.read(4 * 1024 * 1024) # 4MB Buffer
                        if not chunk: break
                        outfile.write(chunk)
                
                # 删除临时分片
                try: os.remove(part_path)
                except: pass
        
        task.status = "completed"
        self._save_task(task)
        
        # 删除进度文件
        try:
            os.remove(Config.get_progress_file_path(task.message_id, task.chat_id))
        except: pass
        
        logger.info(f"✅ 下载完成: {task.file_name}")
        logger.info(f"📂 {file_path}")

    async def monitor_progress(self, task, num_parts, stop_event):
        """进度监控面板 - 实时刷新显示"""
        total_size = task.file_size
        last_bytes = 0
        last_time = time.time()
        speed = 0
        first_print = True
        
        # 预留行数
        LINES_COUNT = 5
        
        while not stop_event.is_set():
            # 统计各状态 - 直接从 part_status 读取
            completed = 0
            downloading = 0
            waiting = 0
            pending = 0
            downloading_list = []
            
            for i in range(num_parts):
                status = self.part_status.get(i, 'pending')
                if status == 'completed':
                    completed += 1
                elif status == 'downloading':
                    downloading += 1
                    # 获取该分片的进度
                    current = self.active_parts.get(i, 0)
                    p_data = task.parts[i]
                    expected = p_data['end_offset'] - p_data['start_offset'] + 1
                    pct = min(100, (current / expected * 100)) if expected > 0 else 0
                    downloading_list.append(f"P{i}:{pct:.0f}%")
                elif status == 'waiting':
                    waiting += 1
                    downloading_list.append(f"P{i}:⏳")
                else:
                    pending += 1
            
            # 计算进度和速度
            total_downloaded = sum(self.active_parts.values())
            now = time.time()
            elapsed = now - last_time
            if elapsed >= 0.5:
                speed = (total_downloaded - last_bytes) / elapsed
                last_bytes = total_downloaded
                last_time = now
            
            percent = min(100, (total_downloaded / total_size * 100)) if total_size > 0 else 0
            
            # 进度条
            bar_len = 30
            filled = int(bar_len * percent / 100)
            bar = '█' * filled + '░' * (bar_len - filled)
            
            # ETA
            eta = "--:--"
            if speed > 0:
                remaining = (total_size - total_downloaded) / speed
                if remaining < 3600:
                    eta = f"{int(remaining//60):02d}:{int(remaining%60):02d}"
                else:
                    eta = f"{int(remaining//3600)}h{int((remaining%3600)//60):02d}m"
            
            # 活跃分片（最多6个）
            active_str = ' '.join(downloading_list[:6])
            if len(downloading_list) > 6:
                active_str += '...'
            
            # 显示内容
            # 使用 ANSI 转义序列 \033[K 清除当前行
            lines = [
                f"{'═'*60}",
                f"  [{bar}] {percent:5.1f}%",
                f"  📥 {total_downloaded/1024/1024:.1f}/{total_size/1024/1024:.1f} MB | ⚡ {speed/1024/1024:.2f} MB/s | ETA: {eta}",
                f"  ✅{completed} ⬇️{downloading} ⏳{waiting} 📋{pending}  |  {active_str}",
                f"{'═'*60}"
            ]
            
            # 刷新显示
            if not first_print:
                # 移动光标上移 N 行
                print(f"\033[{LINES_COUNT}A", end="", flush=True)
            
            for line in lines:
                # \033[2K 清除整行, \r 回到行首
                print(f"\033[2K\r{line}", flush=True)
                
            first_print = False
            
            await asyncio.sleep(0.2)

