import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yt_dlp

try:
    import instaloader
except Exception:  # pragma: no cover - optional dependency
    instaloader = None

MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024


@dataclass
class DownloadResult:
    file_path: Path
    file_size: int
    is_video: bool


SUPPORTED_DOMAINS = (
    "instagram.com",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "fb.watch",
    "twitter.com",
    "x.com",
)


def is_supported_url(url: str) -> bool:
    return any(domain in url for domain in SUPPORTED_DOMAINS)


def _pick_media_file(directory: Path) -> Optional[Path]:
    candidates = sorted(
        directory.rglob("*"),
        key=lambda p: p.stat().st_size if p.is_file() else 0,
        reverse=True,
    )
    for file in candidates:
        if file.is_file() and file.suffix.lower() in {".mp4", ".mkv", ".webm", ".jpg", ".jpeg", ".png"}:
            return file
    return None


def _build_ydl_opts(output_dir: Path) -> dict:
    return {
        "outtmpl": str(output_dir / "%(title).150s.%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "merge_output_format": "mp4",
        "extractor_args": {"tiktok": {"format": "no_watermark"}},
        "postprocessors": [
            {
                "key": "FFmpegVideoRemuxer",
                "preferedformat": "mp4",
            }
        ],
    }


def _download_with_yt_dlp(url: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = _build_ydl_opts(output_dir)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    media = _pick_media_file(output_dir)
    if not media:
        raise RuntimeError("No media downloaded by yt-dlp.")
    return media


def _download_with_instaloader(url: str, output_dir: Path) -> Path:
    if instaloader is None:
        raise RuntimeError("Instaloader not available.")
    output_dir.mkdir(parents=True, exist_ok=True)
    loader = instaloader.Instaloader(
        download_pictures=True,
        download_videos=True,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        quiet=True,
    )
    shortcode_match = re.search(r"/(p|reel|tv)/([^/?#&]+)", url)
    if not shortcode_match:
        raise RuntimeError("Invalid Instagram URL.")
    shortcode = shortcode_match.group(2)
    post = instaloader.Post.from_shortcode(loader.context, shortcode)
    loader.download_post(post, target=output_dir.name)
    media = _pick_media_file(Path.cwd() / output_dir.name)
    if not media:
        raise RuntimeError("No media downloaded by Instaloader.")
    return media


async def download_media(url: str, base_dir: Path) -> DownloadResult:
    output_dir = base_dir / "download"
    try:
        media_path = await asyncio.to_thread(_download_with_yt_dlp, url, output_dir)
    except Exception:
        if "instagram.com" in url:
            media_path = await asyncio.to_thread(_download_with_instaloader, url, base_dir)
        else:
            raise

    file_size = media_path.stat().st_size
    return DownloadResult(
        file_path=media_path,
        file_size=file_size,
        is_video=media_path.suffix.lower() in {".mp4", ".mkv", ".webm"},
    )


def cleanup_downloads(base_dir: Path) -> None:
    if base_dir.exists():
        shutil.rmtree(base_dir, ignore_errors=True)
