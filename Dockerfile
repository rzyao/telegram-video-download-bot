FROM python:3.9-slim

WORKDIR /app

# 安装系统依赖 (如有需)
RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 设置环境变量
ENV HEADLESS=true
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
