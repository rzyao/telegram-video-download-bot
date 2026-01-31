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

import database

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
        
        # 取消信号
        self.cancel_event = asyncio.Event()
        
        # 确保目录存在
        Config.ensure_directories()
        
        # 实时状态 (用于外部查询)
        self.current_speed = 0.0
        self.current_percent = 0.0
        self.current_eta = "N/A"

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
        await database.init_db()
        
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

                logger.error(f"  ❌ Worker {i+1} 重连失败: {e}")

    async def _cleanup_workers(self):
        """强制清理所有 Workers (用于取消任务时的硬重置)"""
        logger.info("🧹 正在强制清理 Worker 连接...")
        async with self.worker_lock:
            for w in self.workers:
                try:
                    if w.is_connected():
                        await w.disconnect()
                except:
                    pass
            self.workers = []
            
            # 清空队列
            while not self.worker_queue.empty():
                try:
                    self.worker_queue.get_nowait()
                except:
                    pass
            logger.info("✅ Worker 连接已清理")

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

    async def stop(self):
        """完全停止下载器"""
        self.is_running = False
        self.cancel_event.set()
        
        # 保存当前任务状态
        if self.current_task:
            logger.info("🛑 正在保存当前任务状态...")
            self._save_task(self.current_task)
            
        # 强制清理 worker 以中断网络连接
        await self._cleanup_workers()
            
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
                
                # 重置取消信号
                self.cancel_event.clear()
                
                # 最终文件路径
                file_path = os.path.join(Config.DOWNLOAD_DIR, task.file_name)
                
                # 使用临时目录存放分片
                temp_base_path = os.path.join(Config.TEMP_DIR, task.file_name)
                
                # 1. 扫描分片状态
                # 检查哪些分片还没完成
                pending_parts = []
                self.active_parts = {} # Map: part_index -> downloaded
                self.part_status = {}  # Map: part_index -> status
                
                for p_data in task.parts:
                    p = FilePart(**p_data)
                    part_path = f"{temp_base_path}.part{p.index}"
                    
                    # 检查分片文件真实状态
                    if p.status == 'completed':
                        # 如果标记完成但文件不存在
                        # 注意：如果最终文件存在，可能分片已经被合并删除了
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
                        part_path = f"{temp_base_path}.part{part.index}"
                        download_tasks.append(
                            asyncio.create_task(self.download_part_worker(semaphore, task, part, part_path))
                        )
                    
                    # 监控取消事件
                    cancel_waiter = asyncio.create_task(self.cancel_event.wait())
                    
                    # 核心逻辑：等待 "所有下载完成" 或者 "取消信号触发"
                    # 我们把所有下载任务打包成一个 awaitable
                    main_download_group = asyncio.gather(*download_tasks)
                    
                    try:
                        done, pending = await asyncio.wait(
                            [main_download_group, cancel_waiter], 
                            return_when=asyncio.FIRST_COMPLETED
                        )
                    except Exception as e:
                        # 异常处理：取消所有任务
                        main_download_group.cancel()
                        cancel_waiter.cancel()
                        raise e

                    # Case 1: 取消触发
                    # Case 1: 取消触发
                    if self.cancel_event.is_set():
                        monitor_stop.set() # 立即停止监控
                        logger.warning(f"⛔ 任务被取消: {task.file_name}")
                        
                        # 架构优化：立即物理断开网络连接，强制中断 Telethon IO
                        await self._cleanup_workers()
                        
                        main_download_group.cancel() # 取消正在进行的下载
                        monitor_task.cancel() # 取消监控任务
                        try:
                            await main_download_group # 等待取消完成
                        except asyncio.CancelledError:
                            pass
                        except Exception as e:
                            logger.error(f"取消过程中发生错误: {e}")
                        
                        task.status = "cancelled"
                        self._save_task(task)
                        continue

                    # Case 2: 下载任务组完成 (可能是成功，也可能是异常)
                    if main_download_group in done:
                        cancel_waiter.cancel() # 不需要再等取消了
                        
                        # 检查 gather 的结果是否有异常
                        # gather 默认会把异常抛出来，或者包含在结果里
                        try:
                            await main_download_group
                        except Exception as e:
                            # 真正的下载错误
                            raise e 


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
                # 只有在非主动取消的情况下才打印错误日志
                if not self.cancel_event.is_set():
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
                    
                except asyncio.CancelledError:
                    task.parts[part.index]['status'] = 'pending' # 重置为 pending 以便下次恢复
                    self.part_status[part.index] = "cancelled"
                    raise
                except Exception as e:
                    self.part_status[part.index] = "error"
                    task.parts[part.index]['status'] = 'error'
                    
                    # 只有在非主动取消的情况下才打印错误日志
                    # "Cannot send requests while disconnected" 是物理中断连接后的正常现象
                    if not self.cancel_event.is_set():
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
                if self.cancel_event.is_set():
                    raise asyncio.CancelledError("Task Cancelled")
                    
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
        
        temp_base_path = os.path.join(Config.TEMP_DIR, task.file_name)
        
        # 简单检查所有分片是否都在
        for p in task.parts:
            part_path = f"{temp_base_path}.part{p['index']}"
            if not os.path.exists(part_path):
                logger.error(f"❌ 缺失分片文件: {part_path}")
                return

        with open(file_path, 'wb') as outfile:
            for p in task.parts:
                part_path = f"{temp_base_path}.part{p['index']}"
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
        
        # 发送完成通知 (仅当 task.message 存在且有效时)
        if task.message:
            try:
                # 计算耗时
                start_time = datetime.fromisoformat(task.created_at) if task.created_at else datetime.now()
                # 简单计算耗时 (不精确，仅供参考)
                duration = datetime.now() - start_time
                duration_str = str(duration).split('.')[0]
                
                msg = (
                    f"✅ **下载完成**\n\n"
                    f"📄 `{task.file_name}`\n"
                    f"📂大小: {task.file_size/1024/1024:.2f} MB\n"
                    f"⏱耗时: {duration_str}"
                )
                await task.message.reply(msg)
            except Exception as e:
                logger.error(f"❌ 发送通知失败: {e}")

            except Exception as e:
                logger.error(f"❌ 发送通知失败: {e}")

            # 添加到数据库历史记录
            await database.add_history(task.file_name, task.file_size, duration_str)
            await asyncio.sleep(0.2)

    async def monitor_progress(self, task, num_parts, stop_event):
        """进度监控面板 (支持 Headless 模式)"""
        total_size = task.file_size
        last_bytes = 0
        last_time = time.time()
        speed = 0
        
        # Headless 模式下，最后一次日志的时间
        last_log_time = 0
        
        while not stop_event.is_set():
            # 1. 计算通用统计数据
            total_downloaded = sum(self.active_parts.values())
            now = time.time()
            elapsed = now - last_time
            
            # 计算瞬时速度
            if elapsed >= 0.5:
                speed = (total_downloaded - last_bytes) / elapsed
                self.current_speed = speed # Update global state
                last_bytes = total_downloaded
                last_time = now
            
            percent = min(100, (total_downloaded / total_size * 100)) if total_size > 0 else 0
            self.current_percent = percent
            
            # ETA
            eta = "--:--"
            if speed > 0:
                remaining = (total_size - total_downloaded) / speed
                if remaining < 3600:
                    eta = f"{int(remaining//60):02d}:{int(remaining%60):02d}"
                else:
                    eta = f"{int(remaining//3600)}h{int((remaining%3600)//60):02d}m"
            self.current_eta = eta
            
            # 2. 分支处理：Headless vs Interactive
            if Config.HEADLESS:
                # 定时日志 (避免刷屏)
                if now - last_log_time >= Config.LOG_INTERVAL:
                    # 统计分片状态
                    completed_count = sum(1 for s in self.part_status.values() if s == 'completed')
                    downloading_count = sum(1 for s in self.part_status.values() if s == 'downloading')
                    
                    log_msg = (
                        f"📈 进度: {percent:5.1f}% | "
                        f"📥 {total_downloaded/1024/1024:.1f}/{total_size/1024/1024:.1f} MB | "
                        f"⚡ {speed/1024/1024:.2f} MB/s | "
                        f"分片: ✅{completed_count} ⬇️{downloading_count} | ETA: {eta}"
                    )
                    logger.info(log_msg)
                    last_log_time = now
                
                # Check less frequently in headless mode
                await asyncio.sleep(1)
                
            else:
                # === 原有的 ANSI 进度条逻辑 ===
                # 统计各状态
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
                
                # 进度条
                bar_len = 30
                filled = int(bar_len * percent / 100)
                bar = '█' * filled + '░' * (bar_len - filled)
                
                # 活跃分片（最多6个）
                active_str = ' '.join(downloading_list[:6])
                if len(downloading_list) > 6:
                    active_str += '...'
                
                lines = [
                    f"{'═'*60}",
                    f"  [{bar}] {percent:5.1f}%",
                    f"  📥 {total_downloaded/1024/1024:.1f}/{total_size/1024/1024:.1f} MB | ⚡ {speed/1024/1024:.2f} MB/s | ETA: {eta}",
                    f"  ✅{completed} ⬇️{downloading} ⏳{waiting} 📋{pending}  |  {active_str}",
                    f"{'═'*60}"
                ]
                
                # 刷新显示 (移动光标逻辑)
                LINES_COUNT = 5
                monitor_attr_name = '_monitor_initialized'
                
                # 首次打印不移动光标
                if not getattr(self, monitor_attr_name, False):
                    setattr(self, monitor_attr_name, True)
                else:
                    print(f"\033[{LINES_COUNT}A", end="", flush=True)
                
                for line in lines:
                    print(f"\033[2K\r{line}", flush=True)
                    
                await asyncio.sleep(0.2)

    def get_status_text(self):
        """获取当前状态文本 (供 Bot 命令使用)"""
        status_lines = []
        
        # 1. 运行状态
        status_lines.append(f"🟢 服务状态: {'运行中' if self.is_running else '空闲中'}")
        status_lines.append(f"📋 等待队列: {len(self.tasks)} 个任务")
        
        # 2. 当前任务
        if self.current_task:
            t = self.current_task
            status_lines.append(f"\n🚀 正在下载:")
            status_lines.append(f"📄 {t.file_name}")
            status_lines.append(f"📊 进度: {self.current_percent:.1f}%")
            status_lines.append(f"📥 大小: {t.file_size/1024/1024:.2f} MB")
            status_lines.append(f"⚡ 速度: {self.current_speed/1024/1024:.2f} MB/s")
            status_lines.append(f"⏱ 剩余: {self.current_eta}")
        else:
            status_lines.append("\n💤 当前无下载任务")
            
        return "\n".join(status_lines)

    async def cancel_current_task(self):
        """取消当前正在运行的任务"""
        if self.is_running and self.current_task:
            logger.info(f"👋收到取消指令: {self.current_task.file_name}")
            self.cancel_event.set()
            return True
        return False

    async def restore_tasks(self):
        """从临时文件恢复未完成的任务"""
        if not Config.TEMP_DIR or not os.path.exists(Config.TEMP_DIR):
            return

        logger.info("🔍 正在扫描未完成任务...")
        count = 0
        import glob
        
        files = glob.glob(os.path.join(Config.TEMP_DIR, "task_*.json"))
        for file in files:
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 过滤已完成或已取消的任务
                if data.get('status') in ['completed', 'cancelled']:
                    continue
                
                # 恢复任务对象
                task = DownloadTask.from_dict(data)
                
                # 获取原始消息对象 (必须，否则无法下载)
                try:
                    message = await self.client.get_messages(task.chat_id, ids=task.message_id)
                    if not message or not message.media:
                        logger.warning(f"⚠️ 无法恢复任务 {task.file_name}: 消息已失效")
                        continue
                    task.message = message
                except Exception as e:
                    logger.warning(f"⚠️ 无法获取消息 {task.message_id}: {e}")
                    continue

                # 添加到队列
                # 避免重复
                if not any(t.message_id == task.message_id for t in self.tasks) and \
                   (not self.current_task or self.current_task.message_id != task.message_id):
                    self.tasks.append(task)
                    count += 1
                    logger.info(f"♻️ 已恢复任务: {task.file_name} ({task.status})")
                    
            except Exception as e:
                logger.error(f"❌ 恢复任务失败 {file}: {e}")
        
        if count > 0:
            logger.info(f"✅ 成功恢复 {count} 个任务")
            # 触发队列处理
            if not self.is_running:
                asyncio.create_task(self.process_queue())

    async def get_cancelled_tasks(self):
        """获取所有已取消的任务列表"""
        if not Config.TEMP_DIR or not os.path.exists(Config.TEMP_DIR):
            return []

        cancelled_tasks = []
        import glob
        files = glob.glob(os.path.join(Config.TEMP_DIR, "task_*.json"))
        
        for file in files:
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                if data.get('status') == 'cancelled':
                    # 避免加载太多详细信息，只返回基本信息
                    task_info = {
                        "message_id": data['message_id'],
                        "filename": data['file_name'],
                        "size": data['file_size'],
                        "updated_at": data.get('updated_at', ''),
                        "progress": data.get('downloaded_bytes', 0) / data.get('file_size', 1) * 100
                    }
                    cancelled_tasks.append(task_info)
            except:
                pass
        
        # 按时间倒序
        cancelled_tasks.sort(key=lambda x: x['updated_at'], reverse=True)
        return cancelled_tasks

    async def delete_task(self, message_id):
        """彻底清除已取消任务及其临时文件"""
        if not Config.TEMP_DIR or not os.path.exists(Config.TEMP_DIR):
            return False

        import glob
        # 1. 找到对应的元数据文件
        # 因为我们不知道 chat_id，所以需要扫描
        target_file = None
        task_data = None
        
        files = glob.glob(os.path.join(Config.TEMP_DIR, "task_*.json"))
        for file in files:
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if str(data.get('message_id')) == str(message_id):
                    target_file = file
                    task_data = data
                    break
            except:
                continue
                
        if not target_file:
            logger.warning(f"❌ 找不到需要删除的任务 ID: {message_id}")
            return False
            
        # 2. 删除分片文件
        try:
            file_name = task_data.get('file_name')
            if file_name:
                # 构造分片的基础路径
                # 注意：这里需要与 download_part_worker 中的路径生成逻辑一致
                # part_path = f"{temp_base_path}.part{p.index}"
                # temp_base_path = os.path.join(Config.TEMP_DIR, task.file_name)
                
                # 使用 glob 匹配所有分片
                # 注意转义文件名中的特殊字符用于 glob
                escaped_name = glob.escape(file_name)
                part_pattern = os.path.join(Config.TEMP_DIR, f"{escaped_name}.part*")
                part_files = glob.glob(part_pattern)
                
                for pf in part_files:
                    try:
                        os.remove(pf)
                    except OSError as e:
                        logger.error(f"删除分片失败 {pf}: {e}")
                        
            logger.info(f"🗑️ 已清理任务文件: {file_name}")
        except Exception as e:
            logger.error(f"清理分片过程出错: {e}")
            
        # 3. 删除元数据文件
        try:
            os.remove(target_file)
            logger.info(f"✅ 任务记录已移除: {target_file}")
            return True
        except Exception as e:
            logger.error(f"删除任务记录失败: {e}")
            return False
            return False

    async def resume_task(self, message_id):
        """恢复已取消的任务"""
        logger.info(f"♻️ 正在恢复任务 ID: {message_id}")
        import glob
        
        # 查找对应的任务文件 (因为不知道 chat_id，只能遍历)
        # 或者假如我们知道 message_id 是唯一的
        files = glob.glob(os.path.join(Config.TEMP_DIR, f"task_*_{message_id}.json"))
        if not files:
            logger.warning("❌ 找不到任务文件")
            return False
            
        file_path = files[0]
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            task = DownloadTask.from_dict(data)
            
            # 获取原始消息
            try:
                message = await self.client.get_messages(task.chat_id, ids=task.message_id)
                if not message or not message.media:
                    logger.warning("❌ 消息已失效，无法恢复")
                    return False
                task.message = message
            except Exception as e:
                logger.warning(f"❌ 获取消息失败: {e}")
                return False
                
            # 重置状态
            task.status = "pending"
            self._save_task(task)
            
            # 加入队列
            for t in self.tasks:
                if t.message_id == task.message_id:
                    logger.info("⚠️ 任务已在队列中")
                    return True
                    
            if self.current_task and self.current_task.message_id == task.message_id:
                 logger.info("⚠️ 任务正在运行")
                 return True

            self.tasks.append(task)
            logger.info(f"✅ 任务已恢复并加入队列: {task.file_name}")
            
            if not self.is_running:
                asyncio.create_task(self.process_queue())
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 恢复任务出错: {e}")
            return False

