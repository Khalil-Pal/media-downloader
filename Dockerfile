# ─────────────────────────────────────────────────────────────
# Sandy Squirrel Bot – YouTube PO-token + FFmpeg Dockerfile
# ─────────────────────────────────────────────────────────────
# The provider image contains the Deno-ready bgutil generator. We copy it
# into the bot image so only one Railway service is required.
FROM brainicism/bgutil-ytdlp-pot-provider:1.3.1-deno AS pot_provider

# Build Python packages. Deno is available here because yt-dlp-ejs supports it.
FROM python:3.11-slim AS builder

WORKDIR /build

COPY --from=pot_provider /usr/bin/deno /usr/local/bin/deno

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN deno --version \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# Runtime image
FROM python:3.11-slim

LABEL maintainer="sandy-squirrel-bot"
LABEL description="Sandy Squirrel – Telegram media downloader bot"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libreoffice-core \
    libreoffice-common \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    fonts-dejavu-core \
    libcairo2 \
    libffi8 \
    libgdk-pixbuf-2.0-0 \
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    shared-mime-info \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deno and the preinstalled bgutil script provider for YouTube only.
COPY --from=pot_provider /usr/bin/deno /usr/local/bin/deno
COPY --from=pot_provider /app /opt/bgutil-ytdlp-pot-provider/server

# Copy Python dependencies and app source.
COPY --from=builder /install /usr/local

RUN useradd -m -u 1000 sandybot

WORKDIR /app
COPY --chown=sandybot:sandybot . .

RUN mkdir -p temp_downloads logs data \
    && chown -R sandybot:sandybot /app /opt/bgutil-ytdlp-pot-provider

USER sandybot

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOWNLOAD_PATH=/app/temp_downloads \
    DENO_PATH=/usr/local/bin/deno \
    DENO_DIR=/opt/bgutil-ytdlp-pot-provider/server/.cache/deno \
    DENO_NO_PROMPT=1 \
    DENO_NO_UPDATE_CHECK=1 \
    YOUTUBE_POT_SERVER_HOME=/opt/bgutil-ytdlp-pot-provider/server

CMD ["python", "main.py"]
