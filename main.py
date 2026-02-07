import asyncio
import logging
import os
import re
from typing import List

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ChatAction
from aiogram.filters import CommandStart
from aiogram.types import Message, InputMediaPhoto, InputMediaVideo
from aiogram.utils.media_group import MediaGroupBuilder
from aiogram.types import FSInputFile
from dotenv import load_dotenv

from downloader import download_media, cleanup_download, is_oversized


URL_REGEX = re.compile(r"https?://\S+")

router = Router()


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "مرحبًا! 👋\n"
        "أرسل رابطًا من Instagram أو TikTok أو YouTube أو Facebook أو Twitter/X وسأقوم بتحميله بأفضل جودة ممكنة."
    )


async def _send_media_group(message: Message, files: List[str]) -> None:
    chunks = [files[i : i + 10] for i in range(0, len(files), 10)]
    for chunk in chunks:
        media_group = MediaGroupBuilder()
        for path in chunk:
            file = FSInputFile(path)
            if path.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                media_group.add_photo(media=file)
            else:
                media_group.add_video(media=file)
        await message.answer_media_group(media_group.build())


async def _send_files(message: Message, files: List[str]) -> None:
    oversized_files = [path for path in files if is_oversized(path)]
    sendable_files = [path for path in files if path not in oversized_files]

    if oversized_files and not sendable_files:
        await message.answer("عذرًا، حجم الملف أكبر من الحد المسموح به في تيليجرام (50MB).")
        return

    if oversized_files:
        await message.answer("تم تجاهل بعض الملفات لأن حجمها يتجاوز 50MB.")

    if len(sendable_files) == 1:
        path = sendable_files[0]
        file = FSInputFile(path)
        if path.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
            await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
            await message.answer_photo(file)
        else:
            await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_VIDEO)
            await message.answer_video(file)
        return

    await _send_media_group(message, sendable_files)


@router.message(F.text)
async def handle_text(message: Message) -> None:
    urls = URL_REGEX.findall(message.text or "")
    if not urls:
        await message.answer("من فضلك أرسل رابطًا صالحًا من المنصات المدعومة.")
        return

    url = urls[0]
    status = await message.answer("جاري المعالجة ⏳...")
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    try:
        result = await asyncio.to_thread(download_media, url)
        if not result.files:
            await status.edit_text("تعذر تحميل هذا الرابط. تأكد من أنه مدعوم.")
            cleanup_download(result)
            return

        await status.edit_text("جاري الإرسال 📤...")
        await _send_files(message, result.files)
    except Exception:
        await status.edit_text("عذرًا، حدث خطأ أثناء التحميل. حاول مرة أخرى لاحقًا.")
    finally:
        if 'result' in locals():
            cleanup_download(result)


async def main() -> None:
    load_dotenv()
    token = os.getenv("TOKEN")
    if not token:
        raise RuntimeError("Missing TOKEN in environment variables.")

    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
