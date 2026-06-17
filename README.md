# 🐿 Sandy Squirrel — Telegram Media Downloader

Sandy Squirrel is a Telegram bot built with Python for downloading media from multiple social platforms directly inside Telegram.

Fast, simple, and lightweight.

---

## ✨ Features

* 🎥 Download YouTube videos
* 📸 Download Instagram reels/posts
* 📘 Download Facebook videos
* 🎵 Extract audio from supported videos
* ⚡ Fast processing
* 🔗 Automatic platform detection
* 📂 Send media directly inside Telegram
* ❌ Error handling for invalid links

---

## 🚀 Supported Platforms

* YouTube
* Instagram
* Facebook

More platforms will be added in future updates.

---

## 🛠 Tech Stack

Backend:

* Python 3
* aiogram

Media Tools:

* yt-dlp
* FFmpeg

---

## 📂 Project Structure

```bash
bot/
├── handlers/
├── utils/
├── downloads/
├── config/
├── main.py
└── requirements.txt
```

---

## ⚙ Installation

### Clone the repository

```bash
git clone https://github.com/yourusername/sandy-squirrel.git
cd sandy-squirrel
```

---

### Create virtual environment

```bash
python -m venv venv
```

Linux/macOS:

```bash
source venv/bin/activate
```

Windows:

```bash
venv\Scripts\activate
```

---

### Install dependencies

```bash
pip install -r requirements.txt
```

---

## 🔑 Configuration

Create a `.env` file:

```env
BOT_TOKEN=your_telegram_bot_token
```

---

## ▶ Running the Bot

```bash
python main.py
```

---

## 📥 How It Works

1. Send a supported media URL.
2. The bot detects the platform automatically.
3. Downloads the media.
4. Sends the file back to you.

Simple as that.

---

## ⚠ Requirements

Make sure these are installed:

* Python 3.10+
* FFmpeg

Install FFmpeg:

[FFmpeg Official Website](https://ffmpeg.org?utm_source=chatgpt.com)

---

## 🔐 Notes

* Private content may not be downloadable.
* Some platforms may rate-limit requests.
* Large files may take longer.

---

## 📈 Roadmap

Planned features:

* Premium subscriptions
* Database integration
* User statistics
* Payment system
* TikTok support
* Download history
* Admin panel

---

## 🤝 Contributing

Pull requests are welcome.

---

## 📜 License

MIT License

---

## ⭐ Support

If you find this project useful, leave a star on the repository.

Built for speed, simplicity, and convenience.
