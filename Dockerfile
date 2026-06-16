# ─────────────────────────────────────────────
#  Sandy Squirrel Bot – Production Dockerfile
# ─────────────────────────────────────────────

# Stage 1: builder
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# Stage 2: runtime
FROM python:3.11-slim

LABEL maintainer="sandy-squirrel-bot"
LABEL description="Sandy Squirrel – Telegram media downloader bot"

# Install runtime dependencies: FFmpeg + yt-dlp native deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN useradd -m -u 1000 sandybot
USER sandybot

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=sandybot:sandybot . .

# Create directories the bot needs
RUN mkdir -p temp_downloads logs

# Default environment (override via .env or docker-compose)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOWNLOAD_PATH=/app/temp_downloads

CMD ["python", "main.py"]
