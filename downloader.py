import os
import re
import glob
import shutil
import tempfile
from dataclasses import dataclass
from typing import List

import yt_dlp
import instaloader


MAX_TELEGRAM_FILE_SIZE_MB = 50


@dataclass
class DownloadResult:
    files: List[str]
    temp_dir: str


def _list_media_files(folder: str) -> List[str]:
    patterns = ["*.mp4", "*.mkv", "*.webm", "*.mp3", "*.jpg", "*.jpeg", "*.png", "*.gif"]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(os.path.join(folder, pattern)))
    return sorted(files)


def _yt_dlp_download(url: str, temp_dir: str) -> List[str]:
    ydl_opts = {
        "outtmpl": os.path.join(temp_dir, "%(title).80s.%(ext)s"),
        "format": "bv*+ba/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {
            "tiktok": {
                "download_without_watermark": True,
            }
        },
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    return _list_media_files(temp_dir)


def _instagram_shortcode(url: str) -> str | None:
    match = re.search(r"/(p|reel|tv)/([^/?#&]+)", url)
    return match.group(2) if match else None


def _instaloader_download(url: str, temp_dir: str) -> List[str]:
    shortcode = _instagram_shortcode(url)
    if not shortcode:
        return []

    loader = instaloader.Instaloader(
        download_pictures=True,
        download_videos=True,
        save_metadata=False,
        compress_json=False,
        download_geotags=False,
        quiet=True,
    )
    post = instaloader.Post.from_shortcode(loader.context, shortcode)
    loader.download_post(post, target=temp_dir)
    return _list_media_files(temp_dir)


def download_media(url: str) -> DownloadResult:
    temp_dir = tempfile.mkdtemp(prefix="tg_dl_")
    files = []
    try:
        files = _yt_dlp_download(url, temp_dir)
        if not files and "instagram.com" in url:
            files = _instaloader_download(url, temp_dir)
    except Exception:
        if "instagram.com" in url:
            try:
                files = _instaloader_download(url, temp_dir)
            except Exception:
                files = []

    return DownloadResult(files=files, temp_dir=temp_dir)


def cleanup_download(result: DownloadResult) -> None:
    shutil.rmtree(result.temp_dir, ignore_errors=True)


def is_oversized(path: str) -> bool:
    size_mb = os.path.getsize(path) / (1024 * 1024)
    return size_mb > MAX_TELEGRAM_FILE_SIZE_MB
