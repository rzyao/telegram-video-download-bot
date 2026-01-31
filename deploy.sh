cat << 'EOF' > deploy.sh
#!/bin/bash

# 定义代理地址
PROXY_ADDR="socks5://192.168.50.2:10088"

echo "Step 1: 正在停止并删除旧容器 tg-downloader..."
# 使用 docker rm -f 强制停止并删除容器
docker rm -f tg-downloader || echo "容器 tg-downloader 不存在，跳过此步。"

echo "Step 2: 正在通过代理拉取最新代码 (git pull)..."
ALL_PROXY=$PROXY_ADDR git pull origin main

echo "Step 3: 正在通过代理重新构建并启动容器..."
# 同时传递 HTTPS_PROXY 和 HTTP_PROXY 确保构建时能下载依赖
HTTPS_PROXY=$PROXY_ADDR HTTP_PROXY=$PROXY_ADDR docker compose up -d --build

echo "部署完成！"
EOF
