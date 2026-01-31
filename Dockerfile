FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    iputils-ping \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create volume checkpoints
VOLUME ["/app/downloads", "/app/data"]

# Expose Dashboard port
EXPOSE 9595

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Shanghai

CMD ["python", "main.py"]
