# Sandy Squirrel — YouTube PO-token + Local Telegram Bot API
# The local Bot API is what lets the BOT itself send files up to 2 GB.

# YouTube PO-token provider (unchanged from the YouTube fix)
FROM brainicism/bgutil-ytdlp-pot-provider:1.3.1-deno AS pot_provider

# Build Telegram's official local Bot API server from source.
FROM python:3.11-slim AS telegram_bot_api_builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git cmake make g++ gperf zlib1g-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --recursive --depth 1 https://github.com/tdlib/telegram-bot-api.git . \
    && cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/telegram-bot-api \
    && cmake --build build --target install -j2

# Python dependencies
FROM python:3.11-slim AS python_builder
WORKDIR /build
COPY --from=pot_provider /usr/bin/deno /usr/local/bin/deno
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN deno --version \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# Runtime image
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates libstdc++6 libssl3 zlib1g libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# YouTube PO-token runtime (unchanged)
COPY --from=pot_provider /usr/bin/deno /usr/local/bin/deno
COPY --from=pot_provider /app /opt/bgutil-ytdlp-pot-provider/server

# Local Telegram Bot API binary
COPY --from=telegram_bot_api_builder /opt/telegram-bot-api/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

# Python packages
COPY --from=python_builder /install /usr/local

RUN useradd -m -u 1000 sandybot \
    && mkdir -p /app /var/lib/telegram-bot-api /tmp/telegram-bot-api \
    && chown -R sandybot:sandybot /app /var/lib/telegram-bot-api /tmp/telegram-bot-api /opt/bgutil-ytdlp-pot-provider

WORKDIR /app
COPY --chown=sandybot:sandybot . .
COPY --chown=sandybot:sandybot entrypoint.sh /usr/local/bin/start-sandy
RUN chmod +x /usr/local/bin/start-sandy \
    && mkdir -p temp_downloads logs data \
    && python setup_multilang.py

USER sandybot

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOWNLOAD_PATH=/app/temp_downloads \
    DENO_PATH=/usr/local/bin/deno \
    DENO_DIR=/opt/bgutil-ytdlp-pot-provider/server/.cache/deno \
    DENO_NO_PROMPT=1 \
    DENO_NO_UPDATE_CHECK=1 \
    YOUTUBE_POT_SERVER_HOME=/opt/bgutil-ytdlp-pot-provider/server \
    LOCAL_BOT_API_URL=http://127.0.0.1:8081 \
    LOCAL_BOT_API_PORT=8081 \
    LOCAL_BOT_API_DIR=/var/lib/telegram-bot-api

ENTRYPOINT ["/usr/local/bin/start-sandy"]
