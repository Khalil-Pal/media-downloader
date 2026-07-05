# Deno is required by current yt-dlp YouTube extraction for JavaScript challenges.
FROM denoland/deno:bin-2.6.8 AS deno_bin

# Stage 1: Python dependencies
FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: application image
FROM python:3.11-slim
LABEL maintainer="sandy-squirrel-bot"
LABEL description="Sandy Squirrel – Telegram media downloader bot"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Deno before switching to the non-root user. This is the official
# single-binary image pattern from Deno's Docker documentation.
COPY --from=deno_bin /deno /usr/local/bin/deno
RUN deno --version && ffmpeg -version

RUN useradd -m -u 1000 sandybot
USER sandybot
WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=sandybot:sandybot . .
RUN mkdir -p temp_downloads logs data
RUN python setup_multilang.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOWNLOAD_PATH=/app/temp_downloads \
    DENO_DIR=/home/sandybot/.cache/deno \
    DENO_NO_UPDATE_CHECK=1

CMD ["python", "main.py"]
