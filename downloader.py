import asyncio
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import ffmpeg
import mutagen
from mutagen.id3 import APIC, ID3, TIT2
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
    "pinterest.com",
    "pin.it",
)


class DownloadError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class DownloadResult:
    media_files: list[Path]
    audio_file: Optional[Path]
    caption: Optional[str]
    is_video: bool
    total_size: int
    error_reason: Optional[str] = None


class MediaDownloader:
    def __init__(self, cookies_path: Optional[Path] = None) -> None:
        self.cookies_path = cookies_path

    async def download(self, url: str, base_dir: Path, want_audio: bool) -> DownloadResult:
        output_dir = base_dir / "download"
        output_dir.mkdir(parents=True, exist_ok=True)
        if not self._is_supported(url):
            raise DownloadError("Unsupported URL.")

        domain = self._domain(url)
        is_tiktok = "tiktok.com" in domain
        is_image_first = any(key in domain for key in ("instagram.com", "twitter.com", "x.com", "pinterest.com"))

        if is_image_first:
            try:
                media_files, caption = await self._download_with_gallery_dl(url, output_dir)
                return await self._finalize(media_files, caption, want_audio)
            except DownloadError:
                pass

        try:
            media_files, caption, thumbnail = await self._download_with_yt_dlp(url, output_dir, is_tiktok)
            return await self._finalize(
                media_files,
                caption,
                want_audio,
                thumbnail=thumbnail,
            )
        except DownloadError as exc:
            if "instagram.com" in domain:
                media_files, caption = await self._download_with_instaloader(url, base_dir)
                return await self._finalize(media_files, caption, want_audio)
            raise exc

    async def _finalize(
        self,
        media_files: list[Path],
        caption: Optional[str],
        want_audio: bool,
        thumbnail: Optional[Path] = None,
    ) -> DownloadResult:
        is_video = any(path.suffix.lower() in {".mp4", ".mkv", ".webm"} for path in media_files)
        audio_file = None
        if want_audio and is_video:
            video_path = next(
                (path for path in media_files if path.suffix.lower() in {".mp4", ".mkv", ".webm"}),
                None,
            )
            if video_path:
                audio_file = await asyncio.to_thread(self._extract_audio, video_path, thumbnail, caption)

        total_size = sum(path.stat().st_size for path in media_files)
        if audio_file:
            total_size += audio_file.stat().st_size

        return DownloadResult(
            media_files=media_files,
            audio_file=audio_file,
            caption=caption,
            is_video=is_video,
            total_size=total_size,
        )

    def _is_supported(self, url: str) -> bool:
        return any(domain in url for domain in SUPPORTED_DOMAINS)

    def _domain(self, url: str) -> str:
        match = re.search(r"https?://([^/]+)/?", url)
        return match.group(1).lower() if match else ""

    async def _download_with_gallery_dl(self, url: str, output_dir: Path) -> tuple[list[Path], Optional[str]]:
        output_dir.mkdir(parents=True, exist_ok=True)
        args = [
            "gallery-dl",
            "-j",
            "-D",
            str(output_dir),
            "--filename",
            "{title}.{extension}",
            url,
        ]
        if self.cookies_path and self.cookies_path.exists():
            args.extend(["--cookies", str(self.cookies_path)])
        result = await self._run_subprocess(args, "gallery-dl failed")
        payload = self._parse_gallery_dl_json(result.stdout)
        media_files = self._media_files_in(output_dir)
        if not media_files:
            raise DownloadError("No images downloaded.")
        caption = payload.get("title") or payload.get("description")
        return media_files, caption

    async def _download_with_yt_dlp(
        self,
        url: str,
        output_dir: Path,
        is_tiktok: bool,
    ) -> tuple[list[Path], Optional[str], Optional[Path]]:
        ydl_opts = self._build_ydl_opts(output_dir, is_tiktok)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            reason = "Login required" if "login" in str(exc).lower() else "Network error"
            raise DownloadError(reason) from exc

        if not info:
            raise DownloadError("No info returned by yt-dlp.")

        entries = info.get("entries") if isinstance(info, dict) else None
        info_for_caption = info
        if entries:
            entries_list = [entry for entry in entries if entry]
            if entries_list:
                info_for_caption = entries_list[0]

        media_files = self._media_files_in(output_dir)
        if not media_files:
            raise DownloadError("No media downloaded.")

        thumbnail_path = await asyncio.to_thread(self._download_thumbnail, info_for_caption, output_dir)
        caption = self._pick_caption(info_for_caption)
        return media_files, caption, thumbnail_path

    async def _download_with_instaloader(self, url: str, output_dir: Path) -> tuple[list[Path], Optional[str]]:
        if instaloader is None:
            raise DownloadError("Instaloader unavailable.")
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
            raise DownloadError("Invalid Instagram URL.")
        shortcode = shortcode_match.group(2)
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
        loader.download_post(post, target=output_dir.name)
        media_files = self._media_files_in(Path.cwd() / output_dir.name)
        caption = post.caption if post.caption else None
        if not media_files:
            raise DownloadError("No media downloaded.")
        return media_files, caption

    async def _run_subprocess(self, args: list[str], error_message: str) -> "_SubprocessResult":
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise DownloadError(f"{error_message}: {stderr.decode().strip()}")
        return _SubprocessResult(stdout=stdout.decode(), stderr=stderr.decode())

    def _build_ydl_opts(self, output_dir: Path, is_tiktok: bool) -> dict:
        opts = {
            "outtmpl": str(output_dir / "%(title).150s.%(ext)s"),
            "format": "bestvideo+bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "ignoreerrors": True,
            "merge_output_format": "mp4",
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
        if is_tiktok:
            opts["extractor_args"] = {"tiktok": {"format": "no_watermark"}}
        if self.cookies_path and self.cookies_path.exists():
            opts["cookiefile"] = str(self.cookies_path)
        return opts

    def _media_files_in(self, directory: Path) -> list[Path]:
        media_exts = {".mp4", ".mkv", ".webm", ".jpg", ".jpeg", ".png"}
        files = [p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in media_exts]
        return sorted(files, key=lambda p: p.stat().st_size, reverse=True)

    def _pick_caption(self, info: dict) -> Optional[str]:
        for key in ("description", "title", "alt_title"):
            value = info.get(key)
            if value:
                return str(value).strip()
        return None

    def _download_thumbnail(self, info: dict, output_dir: Path) -> Optional[Path]:
        thumbnail_url = info.get("thumbnail") if info else None
        if not thumbnail_url:
            return None
        thumb_path = output_dir / "thumb.jpg"
        try:
            with yt_dlp.YoutubeDL(
                {"quiet": True, "outtmpl": str(thumb_path), "noplaylist": True}
            ) as ydl:
                ydl.download([thumbnail_url])
        except Exception:
            return None
        return thumb_path if thumb_path.exists() else None

    def _extract_audio(self, video_path: Path, thumbnail: Optional[Path], caption: Optional[str]) -> Optional[Path]:
        audio_path = video_path.with_suffix(".mp3")
        try:
            (
                ffmpeg.input(str(video_path))
                .output(str(audio_path), acodec="libmp3lame", qscale=2, vn=None)
                .overwrite_output()
                .run(quiet=True)
            )
        except ffmpeg.Error:
            return None

        if not audio_path.exists():
            return None

        try:
            tags = ID3()
            if caption:
                tags.add(TIT2(encoding=3, text=caption[:100]))
            if thumbnail and thumbnail.exists():
                with thumbnail.open("rb") as handle:
                    tags.add(
                        APIC(
                            encoding=3,
                            mime="image/jpeg",
                            type=3,
                            desc="Cover",
                            data=handle.read(),
                        )
                    )
            tags.save(audio_path)
        except mutagen.MutagenError:
            pass

        return audio_path

    def _parse_gallery_dl_json(self, payload: str) -> dict:
        try:
            lines = [line for line in payload.splitlines() if line.strip()]
            if not lines:
                return {}
            return json.loads(lines[-1])
        except json.JSONDecodeError:
            return {}


class _SubprocessResult:
    def __init__(self, stdout: str, stderr: str) -> None:
        self.stdout = stdout
        self.stderr = stderr


def cleanup_downloads(base_dir: Path) -> None:
    if base_dir.exists():
        shutil.rmtree(base_dir, ignore_errors=True)
