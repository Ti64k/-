import asyncio
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import yt_dlp

try:
    import instaloader
except Exception:  # pragma: no cover - optional dependency
    instaloader = None

MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024

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


@dataclass
class DownloadResult:
    media_files: list[Path]
    audio_file: Optional[Path]
    caption: Optional[str]
    is_video: bool
    total_size: int


def is_supported_url(url: str) -> bool:
    return any(domain in url for domain in SUPPORTED_DOMAINS)


def _build_ydl_opts(output_dir: Path) -> dict:
    return {
        "outtmpl": str(output_dir / "%(title).150s.%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "ignoreerrors": True,
        "merge_output_format": "mp4",
        "extractor_args": {"tiktok": {"format": "no_watermark"}},
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/",
        },
        "geo_bypass": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 20,
        "postprocessors": [
            {
                "key": "FFmpegVideoRemuxer",
                "preferedformat": "mp4",
            }
        ],
    }


def _media_files_in(directory: Path) -> list[Path]:
    media_exts = {".mp4", ".mkv", ".webm", ".jpg", ".jpeg", ".png"}
    files = [p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in media_exts]
    return sorted(files, key=lambda p: p.stat().st_size, reverse=True)


def _pick_caption(info: dict) -> Optional[str]:
    for key in ("description", "title", "alt_title"):
        value = info.get(key)
        if value:
            return str(value).strip()
    return None


def _download_with_yt_dlp(url: str, output_dir: Path) -> tuple[list[Path], Optional[str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = _build_ydl_opts(output_dir)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if not info:
        raise RuntimeError("No info returned by yt-dlp.")

    entries = info.get("entries") if isinstance(info, dict) else None
    info_for_caption = info
    if entries:
        entries_list = [entry for entry in entries if entry]
        if entries_list:
            info_for_caption = entries_list[0]

    media_files = _media_files_in(output_dir)
    if not media_files:
        raise RuntimeError("No media downloaded by yt-dlp.")

    return media_files, _pick_caption(info_for_caption)


def _download_with_instaloader(url: str, output_dir: Path) -> tuple[list[Path], Optional[str]]:
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
    media_files = _media_files_in(Path.cwd() / output_dir.name)
    caption = post.caption if post.caption else None
    if not media_files:
        raise RuntimeError("No media downloaded by Instaloader.")
    return media_files, caption


def _extract_audio(video_path: Path) -> Optional[Path]:
    if not video_path.exists():
        return None
    audio_path = video_path.with_suffix(".mp3")
    command = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "2",
        str(audio_path),
        "-y",
        "-loglevel",
        "error",
    ]
    result = subprocess.run(command, check=False)
    if result.returncode != 0 or not audio_path.exists():
        return None
    return audio_path


def _total_size(paths: Iterable[Path]) -> int:
    return sum(path.stat().st_size for path in paths if path.exists())


async def download_media(url: str, base_dir: Path) -> DownloadResult:
    output_dir = base_dir / "download"
    try:
        media_files, caption = await asyncio.to_thread(_download_with_yt_dlp, url, output_dir)
    except Exception:
        if "instagram.com" in url:
            media_files, caption = await asyncio.to_thread(_download_with_instaloader, url, base_dir)
        else:
            raise

    is_video = any(path.suffix.lower() in {".mp4", ".mkv", ".webm"} for path in media_files)
    audio_file = None
    if is_video:
        video_path = next(
            (path for path in media_files if path.suffix.lower() in {".mp4", ".mkv", ".webm"}),
            None,
        )
        if video_path:
            audio_file = await asyncio.to_thread(_extract_audio, video_path)

    total_size = _total_size(media_files)
    if audio_file:
        total_size += audio_file.stat().st_size

    return DownloadResult(
        media_files=media_files,
        audio_file=audio_file,
        caption=caption,
        is_video=is_video,
        total_size=total_size,
    )


def cleanup_downloads(base_dir: Path) -> None:
    if base_dir.exists():
        shutil.rmtree(base_dir, ignore_errors=True)
