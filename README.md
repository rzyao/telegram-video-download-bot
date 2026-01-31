# Telegram Video Downloader Bot

这是一个基于 Python Telethon 的高性能 Telegram 视频下载机器人，配备了现代化的 Web 管理面板。

## 🌟 核心功能

- **Web 控制面板**: 实时监控下载进度、管理队列、查看历史记录。
- **高速下载**: 多线程分片并发下载，充分利用带宽。
- **断点续传**: 支持中断后自动恢复，智能扫描未完成分片。
- **任务取消**: 支持"物理断连"式秒停取消，并可彻底清理残留文件。
- **读写分离**: 智能临时目录策略，支持 SSD 高速写入 + HDD 归档存储。

## 🚀 Docker 部署指南 (推荐)

使用 Docker 是最简单且推荐的部署方式。

### 前置要求

- 安装 [Docker](https://docs.docker.com/get-docker/) 和 [Docker Compose](https://docs.docker.com/compose/install/)。
- 一个 Telegram 账号 (建议使用小号)。

### 1. 快速启动

在项目根目录下，直接运行：

```bash
docker-compose up -d
```

### 2. 访问控制面板

启动成功后，在浏览器访问：

> **http://localhost:8000**

### 3. 初始化配置 (Setup)

首次运行需要进行简单的配置：

1. 打开 Web 面板，系统会自动跳转到 **Setup Wizard**。
2. 输入您的 `API ID` 和 `API Hash` (可在 [my.telegram.org](https://my.telegram.org) 获取)。
3. 输入您的手机号并完成验证码登录。

### 4. 目录挂载说明

默认的 `docker-compose.yml` 已经配置好了数据持久化：

| 本地目录      | 容器内路径       | 说明                                       |
| :------------ | :--------------- | :----------------------------------------- |
| `./downloads` | `/app/downloads` | 最终下载文件的保存位置                     |
| `./data`      | `/app/data`      | 存放数据库 (`bot_data.db`) 和 Session 文件 |
| `./config.py` | `/app/config.py` | 配置文件映射                               |

如果您想修改下载路径到其他硬盘（例如 NAS 挂载路径），请修改 `docker-compose.yml` 中的 `volumes` 部分：

```yaml
    volumes:
      - /path/to/your/hdd/downloads:/app/downloads  # 修改这里
      - ./data:/app/data
      ...
```

---

## 🛠️ 常规部署 (Python 直接运行)

如果您不想使用 Docker：

1. **安装依赖**:
   ```bash
   pip install -r requirements.txt
   ```
2. **运行程序**:
   ```bash
   python main.py
   ```
3. **后台运行 (可选)**:
   建议使用 `screen` 或 `nohup` 保持后台运行。

## ⚙️ 高级配置

您可以在 Web 面板的 **"系统设置"** 中调整：

- **并发数**: 建议设置为 4-8 个 Worker。
- **临时目录**: 容器内默认使用根目录作为临时存储（已优化 IO）。
- **代理**: 支持 Socks5/HTTP 代理。

## 📝 问题排查

**Q: 点击取消后下载没有立即停止？**
A: 最新版已修复。系统采用"物理断连"机制，取消操作会强制切断网络连接，请确保您运行的是最新版本。

**Q: 频繁出现 420 FLOOD_WAIT 错误？**
A: 这是 Telegram 的反滥用机制。请尝试**减少并发 Worker 数量**（建议 4 个），或增大分片大小（在源码中 `PART_SIZE` 调整，建议 64MB）。
