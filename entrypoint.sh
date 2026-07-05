#!/bin/sh
set -eu

: "${API_ID:?API_ID is required by the Local Bot API server}"
: "${API_HASH:?API_HASH is required by the Local Bot API server}"
: "${BOT_TOKEN:?BOT_TOKEN is required}"

# Enable this for the first deployment only. It deregisters the bot from
# api.telegram.org before the local Bot API server starts.
if [ "${MIGRATE_BOT_TO_LOCAL_API:-false}" = "true" ]; then
  python /app/scripts/logout_cloud.py
fi

mkdir -p "$LOCAL_BOT_API_DIR" /tmp/telegram-bot-api

telegram-bot-api \
  --api-id="$API_ID" \
  --api-hash="$API_HASH" \
  --local \
  --http-ip-address=127.0.0.1 \
  --http-port="${LOCAL_BOT_API_PORT:-8081}" \
  --dir="$LOCAL_BOT_API_DIR" \
  --temp-dir=/tmp/telegram-bot-api &
BOT_API_PID=$!

sleep 2
if ! kill -0 "$BOT_API_PID" 2>/dev/null; then
  echo "Local Telegram Bot API server failed to start." >&2
  exit 1
fi

exec python main.py
