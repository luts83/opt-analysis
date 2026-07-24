FROM python:3.11-slim

WORKDIR /app

# 시스템 의존성 (yfinance/certifi SSL 등)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 기본 스냅샷 경로 (Railway Volume 마운트 시 SNAPSHOTS_DIR 로 덮어씀)
ENV PYTHONUNBUFFERED=1 \
    SNAPSHOTS_DIR=/data/snapshots

RUN mkdir -p /data/snapshots

# cron 서비스가 아니라 상시 봇(+내장 스케줄)
CMD ["python", "bot.py"]
