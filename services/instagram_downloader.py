"""
services/instagram_downloader.py – Instagram fallback using instaloader.

Used when yt-dlp gets an "empty media response" from Instagram.
Uses the same instagram_cookies.txt file as yt-dlp when it is available.
"""
from __future__ import annotations

import http.cookiejar
import logging
import re
import shutil
from pathlib import Path

import instaloader

from services.cookie_files import INSTAGRAM_COOKIES_FILE, get_instagram_cookies_file

logger = logging.getLogger(__name__)

_SHORTCODE_RE = re.compile(
    r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE
)


class InstagramFallbackError(Exception):
    """Known Instagram fallback failure safe to surface through downloader.py."""


class InstagramRateLimitedError(InstagramFallbackError):
    """Instagram temporarily rate-limited the fallback session."""


class InstagramPrivatePostError(InstagramFallbackError):
    """The post requires login/follow permission beyond the available cookies."""


def _exception_types(*names: str) -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = []
    for name in names:
        exc_type = getattr(instaloader.exceptions, name, None)
        if isinstance(exc_type, type) and issubclass(exc_type, BaseException):
            types.append(exc_type)
    return tuple(types)


RATE_LIMIT_EXCEPTIONS = _exception_types("TooManyRequestsException")
PRIVATE_EXCEPTIONS = _exception_types(
    "LoginRequiredException",
    "PrivateProfileNotFollowedException",
)


def _extract_shortcode(url: str) -> str | None:
    m = _SHORTCODE_RE.search(url)
    return m.group(1) if m else None


def _cookie_identity(cookie_jar: http.cookiejar.CookieJar) -> str | None:
    for cookie in cookie_jar:
        if cookie.name in {"ds_user_id", "sessionid"} and cookie.value:
            return str(cookie.value).split("%3A", 1)[0]
    return None


def _load_instagram_cookies() -> tuple[http.cookiejar.MozillaCookieJar | None, str | None]:
    cookies_file = get_instagram_cookies_file()
    if not cookies_file:
        logger.warning(
            "instaloader: %s not found; proceeding without Instagram authentication.",
            INSTAGRAM_COOKIES_FILE,
        )
        return None, None

    cookie_jar = http.cookiejar.MozillaCookieJar(cookies_file)
    try:
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError) as exc:
        logger.warning(
            "instaloader: could not load %s; proceeding unauthenticated: %s",
            cookies_file,
            exc,
        )
        return None, None

    instagram_cookies = [
        cookie
        for cookie in cookie_jar
        if "instagram.com" in cookie.domain.lstrip(".").lower()
    ]
    if not instagram_cookies:
        logger.warning(
            "instaloader: %s contains no usable Instagram cookies; proceeding unauthenticated.",
            cookies_file,
        )
        return None, None

    return cookie_jar, _cookie_identity(cookie_jar)


def _attach_cookie_file(L: instaloader.Instaloader) -> None:
    cookie_jar, cookie_identity = _load_instagram_cookies()
    if not cookie_jar:
        return

    for cookie in cookie_jar:
        L.context._session.cookies.set_cookie(cookie)

    if cookie_identity:
        L.context.username = cookie_identity

    verified_username = L.context.test_login()
    if verified_username:
        L.context.username = verified_username
        logger.info("instaloader: authenticated as %s", verified_username)
        return

    logger.info("instaloader: proceeding without a verified login")


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
    _attach_cookie_file(L)
    return L


def _log_instaloader_failure(action: str, shortcode: str, exc: Exception) -> None:
    logger.warning(
        "instaloader: %s failed (%s) [%s]: %s",
        action,
        shortcode,
        type(exc).__name__,
        exc,
    )


def _raise_known_instagram_error(exc: Exception) -> None:
    if RATE_LIMIT_EXCEPTIONS and isinstance(exc, RATE_LIMIT_EXCEPTIONS):
        raise InstagramRateLimitedError(
            "Instagram is temporarily rate-limiting requests. Try again later."
        ) from exc

    if PRIVATE_EXCEPTIONS and isinstance(exc, PRIVATE_EXCEPTIONS):
        raise InstagramPrivatePostError(
            "This Instagram post is private or requires login."
        ) from exc


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
        _log_instaloader_failure("fetch post", shortcode, exc)
        _raise_known_instagram_error(exc)
        return None

    tmp_dir = output_dir / f"ig_{shortcode}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        L.download_post(post, target=tmp_dir)
    except Exception as exc:
        _log_instaloader_failure("download", shortcode, exc)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _raise_known_instagram_error(exc)
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
        _log_instaloader_failure("info fetch", shortcode, exc)
        _raise_known_instagram_error(exc)
        return None

    return {
        "title": (post.caption or "")[:120].replace("\n", " ") or "Instagram video",
        "uploader": post.owner_username,
        "duration": post.video_duration or 0,
        "shortcode": shortcode,
        "is_video": post.is_video,
    }