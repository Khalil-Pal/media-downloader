"""
services/instagram_downloader.py – Instagram fallback using instaloader.

Used when yt-dlp gets an "empty media response" from Instagram.
Works for public posts/reels without requiring login.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

import instaloader

logger = logging.getLogger(__name__)

_SHORTCODE_RE = re.compile(
    r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE
)


def _extract_shortcode(url: str) -> str | None:
    m = _SHORTCODE_RE.search(url)
    return m.group(1) if m else None


def _make_loader() -> instaloader.Instaloader:
    L = instaloader.Instaloader(
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
        request_timeout=30,
    )
    session_file = os.getenv("INSTAGRAM_SESSION_FILE", "")
    ig_user = os.getenv("INSTAGRAM_USERNAME", "")
    ig_pass = os.getenv("INSTAGRAM_PASSWORD", "")
    if session_file and Path(session_file).exists():
        try:
            L.load_session_from_file(ig_user or "anonymous", session_file)
            logger.info("instaloader: loaded session from %s", session_file)
        except Exception as exc:
            logger.warning("instaloader: session load failed: %s", exc)
    elif ig_user and ig_pass:
        try:
            L.login(ig_user, ig_pass)
            logger.info("instaloader: logged in as %s", ig_user)
        except Exception as exc:
            logger.warning("instaloader: login failed: %s", exc)
    return L


def download_instagram(url: str, output_dir: Path) -> Path | None:
    """
    Download an Instagram post/reel into *output_dir*.
    Returns the Path of the downloaded video file, or None on failure.
    """
    shortcode = _extract_shortcode(url)
    if not shortcode:
        logger.warning("instaloader: could not extract shortcode from %s", url)
        return None

    L = _make_loader()
    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
    except Exception as exc:
        logger.warning("instaloader: fetch post failed (%s): %s", shortcode, exc)
        return None

    tmp_dir = output_dir / f"ig_{shortcode}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        L.download_post(post, target=tmp_dir)
    except Exception as exc:
        logger.warning("instaloader: download failed (%s): %s", shortcode, exc)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    video_file: Path | None = None
    largest = -1
    for f in tmp_dir.rglob("*.mp4"):
        sz = f.stat().st_size
        if sz > largest:
            largest = sz
            video_file = f

    if not video_file:
        logger.warning("instaloader: no mp4 found after download (%s)", shortcode)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    dest = output_dir / f"{shortcode}.mp4"
    shutil.move(str(video_file), dest)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.info("instaloader: saved %s → %s", shortcode, dest)
    return dest


def fetch_instagram_info(url: str) -> dict | None:
    """
    Return basic metadata for an Instagram post without downloading it.
    Returns None if the post is inaccessible.
    """
    shortcode = _extract_shortcode(url)
    if not shortcode:
        return None

    L = _make_loader()
    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
    except Exception as exc:
        logger.warning("instaloader: info fetch failed (%s): %s", shortcode, exc)
        return None

    return {
        "title": (post.caption or "")[:120].replace("\n", " ") or "Instagram video",
        "uploader": post.owner_username,
        "duration": post.video_duration or 0,
        "shortcode": shortcode,
        "is_video": post.is_video,
    }