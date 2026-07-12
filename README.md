# 🐿 Sandy Squirrel — Telegram Media Downloader & File Converter

Sandy Squirrel is a Telegram bot built with Python for downloading media from multiple social platforms and converting files directly inside Telegram.

Fast, simple, and lightweight.

---

## ✨ Features

* 🎥 Download YouTube videos
* 📸 Download Instagram reels/posts
* 📘 Download Facebook videos
* 🎵 Extract audio from supported videos
* 🔄 Convert file types directly in Telegram
* ⚡ Fast processing
* 🔗 Automatic platform detection
* 📂 Send media and converted files directly inside Telegram
* ❌ Error handling for invalid links and unsupported files

---

## 🚀 Supported Platforms

* YouTube
* Instagram
* Facebook

More platforms will be added in future updates.

---

## 🔄 File Conversion

Sandy Squirrel can also convert files between supported formats.

### Supported conversions

* 🎵 Audio / Video → MP3
* 📄 DOCX, XLSX, MD, PPTX → PDF
* 🖼 PNG ↔ JPG ↔ WebP

### How to use converting mode

1. Open the bot menu.
2. Change the bot mode to **Converting Mode**.
3. Send the file you want to convert as a **document**.
4. Choose the target format.
5. The bot converts the file and sends it back to you.

> Important: Files must be sent as documents, not as compressed photos or normal media messages.

---

## 🛠 Tech Stack

Backend:

* Python 3
* aiogram

Media Tools:

* yt-dlp
* FFmpeg

Conversion Tools:

* FFmpeg
* LibreOffice
* Pillow
* Pandoc

---

## 📂 Project Structure

```bash
bot/
├── handlers/
├── services/
├── utils/
├── downloads/
├── temp_downloads/
├── config/
├── data/
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

## ▶ Running the Bot

```bash
python main.py
```

---

## 📥 How It Works

### Media downloading

1. Send a supported media URL.
2. The bot detects the platform automatically.
3. Downloads the media.
4. Sends the file back to you.

### File converting

1. Switch to **Converting Mode** from the bot menu.
2. Send a supported file as a document.
3. Choose the target format.
4. The bot converts the file.
5. The converted file is sent back to you.

Simple as that.

---

## ⚠ Requirements

Make sure these are installed:

* Python 3.10+
* FFmpeg
* LibreOffice
* Pandoc

Install FFmpeg:

[FFmpeg Official Website](https://ffmpeg.org)

Install LibreOffice:

[LibreOffice Official Website](https://www.libreoffice.org)

Install Pandoc:

[Pandoc Official Website](https://pandoc.org)

---

## 🔐 Notes

* Private content may not be downloadable.
* Some platforms may rate-limit requests.
* Large files may take longer.
* Some file types may not be supported for conversion yet.
* Files should be sent as documents for conversion mode to work correctly.
* Conversion quality may depend on the original file format and structure.

---

## 📈 Roadmap

Planned features:

* Premium subscriptions
* Database integration
* User statistics
* Payment system
* TikTok support
* VK support
* Download history
* Admin panel
* More file conversion formats
* Multi-language support improvements

---

## 📜 License

MIT License

---

## ⭐ Support

If you find this project useful, leave a star on the repository.

Built for speed, simplicity, and convenience.
