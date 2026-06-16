# 🐿️ Sandy Squirrel Bot

A production-ready Telegram bot that downloads videos and audio from YouTube, Instagram, Facebook, TikTok, Twitter/X, Vimeo, and more.

---

## Features

- **Multi-platform support** — YouTube, Instagram, Facebook, TikTok, Twitter/X, Vimeo, Reddit, Dailymotion
- **Quality selection** — Best, 720p, 480p, 360p, 144p, Audio-only (MP3)
- **Real-time progress** — Live progress updates while downloading
- **Metadata display** — Title, uploader, duration, file size
- **Rate limiting** — Per-user download limits and anti-spam cooldowns
- **Concurrent downloads** — Configurable global concurrency cap
- **Auto cleanup** — Temp files removed after upload
- **Admin stats** — `/stats` command for monitoring
- **Graceful errors** — User-friendly messages for geo-blocks, private content, file size limits

---

## Project Structure

```
sandy_squirrel_bot/
├── main.py                     # Bot entry point
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── config/
│   ├── __init__.py
│   ├── settings.py             # Environment configuration
│   └── logging_config.py      # Structured logging setup
├── handlers/
│   ├── __init__.py
│   ├── commands.py             # /start /help /quality /cancel /stats
│   ├── downloader_handler.py  # /download /audio + URL detection
│   ├── callbacks.py           # Inline keyboard callbacks
│   └── common.py              # Shared keyboards & message templates
├── services/
│   ├── __init__.py
│   ├── downloader.py          # yt-dlp integration (isolated service)
│   └── stats.py               # In-memory statistics tracker
└── utils/
    ├── __init__.py
    ├── validators.py          # URL validation & platform detection
    ├── formatters.py          # Duration, size, progress bar helpers
    └── rate_limiter.py        # Sliding-window rate limiter
```

---

## Local Development

### Prerequisites

- Python 3.11+
- FFmpeg installed (`apt install ffmpeg` / `brew install ffmpeg`)
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### Setup

```bash
# 1. Clone and enter the directory
git clone <your-repo-url>
cd sandy_squirrel_bot

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
nano .env                        # Set BOT_TOKEN and ADMIN_ID

# 5. Run the bot
python main.py
```

---


## Linux VPS Deployment

### Option A – Docker (Recommended)

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh

# 2. Clone the repo
git clone <your-repo-url>
cd sandy_squirrel_bot

# 3. Configure
cp .env.example .env
nano .env   # Fill in BOT_TOKEN

# 4. Build and start
docker compose up -d --build

# 5. View logs
docker compose logs -f

# 6. Stop
docker compose down
```

### Option B – Systemd Service (bare metal)

```bash
# 1. Install system dependencies
sudo apt update
sudo apt install -y python3.11 python3.11-venv ffmpeg git

# 2. Create bot user
sudo useradd -m sandybot
sudo su - sandybot

# 3. Clone and configure
git clone <your-repo-url> ~/bot
cd ~/bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env

# 4. Create systemd service (as root)
exit
sudo nano /etc/systemd/system/sandy_squirrel.service
```

Paste the following into the service file:

```ini
[Unit]
Description=Sandy Squirrel Telegram Bot
After=network.target

[Service]
Type=simple
User=sandybot
WorkingDirectory=/home/sandybot/bot
ExecStart=/home/sandybot/bot/.venv/bin/python main.py
Restart=always
RestartSec=10
EnvironmentFile=/home/sandybot/bot/.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
# 5. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable sandy_squirrel
sudo systemctl start sandy_squirrel

# 6. Check status
sudo systemctl status sandy_squirrel
sudo journalctl -u sandy_squirrel -f
```

---

## Bot Commands Reference

| Command | Description |
|---|---|
| `/start` | Welcome message and quick guide |
| `/help` | Full usage instructions |
| `/download <url>` | Download video (quality selector appears) |
| `/audio <url>` | Download audio only as MP3 |
| `/quality` | Show available quality options |
| `/cancel` | Cancel your active download |
| `/stats` | Bot statistics (admin only) |

You can also just **paste a URL directly** into the chat — Sandy will detect it automatically.

---

## Supported Platforms

Sandy Squirrel uses yt-dlp which supports 1000+ sites. Officially advertised:

- ▶️ YouTube (videos, Shorts, livestreams)
- 📸 Instagram (posts, Reels, Stories)
- 👤 Facebook (videos, Reels, Watch)
- 🎵 TikTok
- 🐦 Twitter / X
- 🎬 Vimeo
- 🔗 Reddit
- 📺 Dailymotion
