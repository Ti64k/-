import asyncio
import os
import re
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message
from aiogram.utils.media_group import MediaGroupBuilder
from dotenv import load_dotenv

from downloader import DownloadError, MAX_TELEGRAM_FILE_SIZE, MediaDownloader, cleanup_downloads

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

TOKEN = os.getenv("TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "TOKEN is not set. Configure .env next to main.py or set TOKEN/BOT_TOKEN/TELEGRAM_TOKEN."
    )

bot = Bot(token=TOKEN)
dp = Dispatcher()

URL_REGEX = re.compile(r"https?://\S+")
downloader = MediaDownloader(cookies_path=Path.cwd() / "cookies.txt")


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        "مرحباً! 👋\n"
        "أرسل لي رابطاً من Instagram أو TikTok أو YouTube أو Facebook أو Twitter/X، "
        "وسأقوم بتحميل الفيديو أو الصورة بأعلى جودة ممكنة وإرسالها لك."
    )


async def _extract_url(message: Message) -> str | None:
    if not message.text:
        return None
    match = URL_REGEX.search(message.text)
    return match.group(0) if match else None


@dp.message(F.text)
async def download_handler(message: Message) -> None:
    url = await _extract_url(message)
    if not url:
        return

    status_msg = await message.answer("جاري المعالجة ⏳...")
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    want_audio = bool(re.search(r"\b(audio|mp3)\b", message.text, re.IGNORECASE)) or "صوت" in message.text
    base_dir = Path(tempfile.mkdtemp(prefix="download_", dir=Path.cwd()))
    try:
        result = await downloader.download(url, base_dir, want_audio=want_audio)
        if result.total_size > MAX_TELEGRAM_FILE_SIZE:
            await status_msg.edit_text(
                "عذراً، حجم الملف كبير جداً لإرساله عبر البوت (أكثر من 50MB)."
            )
            return

        await status_msg.edit_text("جاري الإرسال ⬆️...")
        await bot.send_chat_action(
            chat_id=message.chat.id,
            action=ChatAction.UPLOAD_VIDEO if result.is_video else ChatAction.UPLOAD_PHOTO,
        )

        caption = result.caption or ""
        if len(result.media_files) > 1 and not result.is_video:
            builder = MediaGroupBuilder()
            for index, file_path in enumerate(result.media_files):
                input_file = FSInputFile(file_path)
                builder.add_photo(media=input_file, caption=caption if index == 0 else None)
            await message.answer_media_group(builder.build())
        else:
            media = FSInputFile(result.media_files[0])
            if result.is_video:
                await message.answer_video(media, caption=caption)
            else:
                await message.answer_photo(media, caption=caption)

        if result.audio_file:
            await message.answer_audio(FSInputFile(result.audio_file))
        await status_msg.delete()
    except DownloadError as exc:
        await status_msg.edit_text(exc.reason)
    except Exception:
        await status_msg.edit_text("حدث خطأ أثناء التحميل. حاول مرة أخرى لاحقاً.")
    finally:
        cleanup_downloads(base_dir)


async def main() -> None:
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
