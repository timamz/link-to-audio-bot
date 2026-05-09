import asyncio
import logging
import os
import re
import secrets
import math
import shutil
import subprocess
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TypedDict

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

YOUTUBE_RE = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/\S+", re.IGNORECASE)
HEALTH_STATE = {"ready": False}
PENDING_LINKS: dict[str, tuple[str, float]] = {}
PENDING_LINK_TTL_SECONDS = 60 * 60


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def optional_cookie_file() -> str | None:
    cookie_path = os.getenv("COOKIE_FILE", "/app/cookies.txt")
    return cookie_path if os.path.isfile(cookie_path) else None


def prepare_cookie_copy(download_dir: str) -> str | None:
    cookie_path = optional_cookie_file()
    if not cookie_path:
        return None

    copied_cookie_path = os.path.join(download_dir, "cookies.txt")
    shutil.copyfile(cookie_path, copied_cookie_path)
    return copied_cookie_path


def sanitize_title(title: str) -> str:
    safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_")).strip()
    return safe_title or "youtube"


class DownloadResult(TypedDict):
    path: str
    title: str
    duration: int
    width: int | None
    height: int | None


bot_token = require_env("BOT_TOKEN")
health_port = int(os.getenv("PORT", "8080"))
proxy_url = os.getenv("PROXY_URL")
default_bitrate = os.getenv("AUDIO_BITRATE", "96")
bot_api_server = os.getenv("BOT_API_SERVER")
max_bot_upload_bytes = int(os.getenv("MAX_BOT_UPLOAD_BYTES", str(1900 * 1024 * 1024)))
upload_timeout = int(os.getenv("UPLOAD_TIMEOUT", "3600"))

# PROXY_URL is used for yt-dlp/YouTube requests and for Telegram Bot API only when
# BOT_API_SERVER is not set. With local telegram-bot-api, the bot talks to it directly.
if bot_api_server:
    logger.info("Using local Telegram Bot API server: %s", bot_api_server)
elif proxy_url:
    logger.info("Using proxy for Telegram and yt-dlp: %s", proxy_url.split("@")[-1])
else:
    logger.info("No proxy configured")


class HealthcheckHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/healthz":
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return

        status = HTTPStatus.OK if HEALTH_STATE["ready"] else HTTPStatus.SERVICE_UNAVAILABLE
        body = b"ok\n" if HEALTH_STATE["ready"] else b"starting\n"
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        logger.debug("healthcheck: " + format, *args)


def start_healthcheck_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", health_port), HealthcheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Healthcheck server listening on port %s", health_port)


def cleanup_pending_links() -> None:
    now = time.monotonic()
    expired_tokens = [
        token for token, (_, created_at) in PENDING_LINKS.items()
        if now - created_at > PENDING_LINK_TTL_SECONDS
    ]
    for token in expired_tokens:
        PENDING_LINKS.pop(token, None)


def make_download_keyboard(url: str) -> InlineKeyboardMarkup:
    cleanup_pending_links()
    token = secrets.token_urlsafe(8)
    PENDING_LINKS[token] = (url, time.monotonic())
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎧 Audio MP3", callback_data=f"dl:audio:{token}")],
            [InlineKeyboardButton(text="🎬 Video 720p", callback_data=f"dl:video_720:{token}")],
            [InlineKeyboardButton(text="🎬 Video 1080p", callback_data=f"dl:video_1080:{token}")],
        ]
    )


def build_common_ydl_opts(outtmpl: str, cookie_file: str | None) -> dict:
    ydl_opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
    }
    if proxy_url:
        ydl_opts["proxy"] = proxy_url
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    return ydl_opts


def final_download_path(info: dict, ydl: yt_dlp.YoutubeDL) -> str:
    requested_downloads = info.get("requested_downloads") or []
    if requested_downloads:
        filepath = requested_downloads[0].get("filepath")
        if filepath:
            return filepath
    return ydl.prepare_filename(info)


def download_audio_sync(url: str, download_dir: str, bitrate: str = "128") -> DownloadResult:
    outtmpl = os.path.join(download_dir, "%(id)s.%(ext)s")

    def run_download(cookie_file: str | None) -> DownloadResult:
        ydl_opts = build_common_ydl_opts(outtmpl, cookie_file)
        ydl_opts.update(
            {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": bitrate,
                    }
                ],
                "keepvideo": False,
            }
        )

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            base, _ = os.path.splitext(ydl.prepare_filename(info))
            file_path = f"{base}.mp3"
            title = sanitize_title(info.get("title") or os.path.basename(base))
            duration = int(info.get("duration") or 0)
            return {"path": file_path, "title": title, "duration": duration, "width": None, "height": None}

    cookie_copy = prepare_cookie_copy(download_dir)
    try:
        return run_download(cookie_copy)
    except yt_dlp.utils.DownloadError:
        if cookie_copy:
            logger.warning("yt-dlp failed with cookies, retrying without cookies")
            return run_download(None)
        raise


def human_size(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / 1024 ** 3:.2f} GB"
    if num_bytes >= 1024 ** 2:
        return f"{num_bytes / 1024 ** 2:.0f} MB"
    return f"{num_bytes / 1024:.0f} KB"


def video_format_selector(height: int) -> str:
    # Prefer H.264/AVC for Telegram playback. YouTube's smallest MP4 at 720p/1080p
    # is often AV1 (av01), which Telegram may upload but render as a blank video.
    return (
        f"bestvideo[height={height}][ext=mp4][vcodec*=avc1]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}][ext=mp4][vcodec*=avc1]+bestaudio[ext=m4a]/"
        f"bestvideo[height={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height={height}]+bestaudio/"
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]/best"
    )


def selected_formats_size(info: dict) -> int | None:
    requested_formats = info.get("requested_formats") or []
    if requested_formats:
        sizes = [fmt.get("filesize") or fmt.get("filesize_approx") for fmt in requested_formats]
        return sum(int(size) for size in sizes if size) or None

    size = info.get("filesize") or info.get("filesize_approx")
    return int(size) if size else None


def split_video_sync(path: str, output_dir: str, max_part_bytes: int) -> list[str]:
    file_size = os.path.getsize(path)
    if file_size <= max_part_bytes:
        return [path]

    duration = get_video_duration_sync(path)
    if duration <= 0:
        raise RuntimeError("Can't split video: unknown duration")

    # Keep margin below Bot API limit because ffmpeg segment boundaries are keyframe-based.
    target_part_bytes = int(max_part_bytes * 0.88)
    estimated_parts = max(2, math.ceil(file_size / target_part_bytes))
    segment_time = max(1, math.floor(duration / estimated_parts))
    output_pattern = os.path.join(output_dir, "part_%03d.mp4")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        path,
        "-map",
        "0",
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        str(segment_time),
        "-reset_timestamps",
        "1",
        output_pattern,
    ]
    subprocess.run(command, check=True)
    parts = sorted(
        os.path.join(output_dir, name)
        for name in os.listdir(output_dir)
        if name.startswith("part_") and name.endswith(".mp4")
    )
    if not parts:
        raise RuntimeError("ffmpeg did not create video parts")

    oversized_parts = [part for part in parts if os.path.getsize(part) > max_part_bytes]
    if oversized_parts:
        raise RuntimeError(
            "Some split parts are still too large for Telegram Bot API: "
            + ", ".join(human_size(os.path.getsize(part)) for part in oversized_parts[:3])
        )
    return parts


def get_video_duration_sync(path: str) -> int:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(float(result.stdout.strip() or 0))


def probe_video_dimensions_sync(path: str) -> tuple[int | None, int | None]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if "x" not in value:
        return None, None
    width, height = value.split("x", 1)
    return int(width), int(height)


def remux_video_for_telegram_sync(path: str, output_dir: str) -> str:
    # Keep codecs untouched, but rewrite MP4 metadata for Telegram clients:
    # - moov atom at the beginning (+faststart)
    # - explicit avc1 tag for H.264
    output_path = os.path.join(output_dir, "telegram_compatible.mp4")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        path,
        "-map",
        "0",
        "-c",
        "copy",
        "-tag:v",
        "avc1",
        "-movflags",
        "+faststart",
        "-avoid_negative_ts",
        "make_zero",
        output_path,
    ]
    subprocess.run(command, check=True)
    return output_path


def estimate_video_size_sync(url: str, height: int) -> int | None:
    # Do not pass cookies here: mounted cookies.txt is read-only and yt-dlp may try to rewrite it.
    ydl_opts = build_common_ydl_opts(os.devnull, None)
    ydl_opts.update({"format": video_format_selector(height), "quiet": True})
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return selected_formats_size(info)


def estimate_audio_size_sync(url: str, bitrate: str) -> int | None:
    # Do not pass cookies here: mounted cookies.txt is read-only and yt-dlp may try to rewrite it.
    ydl_opts = build_common_ydl_opts(os.devnull, None)
    ydl_opts.update({"format": "bestaudio/best", "quiet": True})
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    duration = int(info.get("duration") or 0)
    if duration:
        try:
            return int(duration * int(bitrate) * 1000 / 8)
        except ValueError:
            pass
    return selected_formats_size(info)


def download_video_sync(url: str, download_dir: str, height: int) -> DownloadResult:
    outtmpl = os.path.join(download_dir, "%(id)s.%(ext)s")

    def run_download(cookie_file: str | None) -> DownloadResult:
        ydl_opts = build_common_ydl_opts(outtmpl, cookie_file)
        ydl_opts.update(
            {
                "format": video_format_selector(height),
                "merge_output_format": "mp4",
            }
        )

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = final_download_path(info, ydl)
            title = sanitize_title(info.get("title") or os.path.basename(file_path))
            duration = int(info.get("duration") or 0)
            actual_width = info.get("width")
            actual_height = info.get("height")
            if info.get("requested_formats"):
                video_formats = [fmt for fmt in info["requested_formats"] if fmt.get("vcodec") != "none"]
                if not actual_width:
                    actual_width = max((fmt.get("width") or 0 for fmt in video_formats), default=0)
                if not actual_height:
                    actual_height = max((fmt.get("height") or 0 for fmt in video_formats), default=0)
            if not actual_width or not actual_height:
                probed_width, probed_height = probe_video_dimensions_sync(file_path)
                actual_width = actual_width or probed_width
                actual_height = actual_height or probed_height
            return {
                "path": file_path,
                "title": title,
                "duration": duration,
                "width": int(actual_width or 0) or None,
                "height": int(actual_height or height),
            }

    cookie_copy = prepare_cookie_copy(download_dir)
    try:
        return run_download(cookie_copy)
    except yt_dlp.utils.DownloadError:
        if cookie_copy:
            logger.warning("yt-dlp failed with cookies, retrying without cookies")
            return run_download(None)
        raise


async def download_audio(url: str, download_dir: str, bitrate: str = "128") -> DownloadResult:
    return await asyncio.to_thread(download_audio_sync, url, download_dir, bitrate)


async def download_video(url: str, download_dir: str, height: int) -> DownloadResult:
    return await asyncio.to_thread(download_video_sync, url, download_dir, height)


async def remux_video_for_telegram(path: str, output_dir: str) -> str:
    return await asyncio.to_thread(remux_video_for_telegram_sync, path, output_dir)


async def estimate_audio_size(url: str, bitrate: str) -> int | None:
    return await asyncio.to_thread(estimate_audio_size_sync, url, bitrate)


async def estimate_video_size(url: str, height: int) -> int | None:
    return await asyncio.to_thread(estimate_video_size_sync, url, height)


async def split_video(path: str, output_dir: str, max_part_bytes: int) -> list[str]:
    return await asyncio.to_thread(split_video_sync, path, output_dir, max_part_bytes)


async def handle_youtube_link(message: Message) -> None:
    text = message.text or message.caption or ""
    match = YOUTUBE_RE.search(text)
    if not match:
        return

    url = match.group(0)
    await message.answer(
        "Что скачать?",
        reply_markup=make_download_keyboard(url),
    )


async def handle_download_choice(callback: CallbackQuery) -> None:
    if not callback.data:
        await callback.answer("Unknown action", show_alert=True)
        return

    _, kind, token = callback.data.split(":", 2)
    pending = PENDING_LINKS.pop(token, None)
    if not pending:
        await callback.answer("Ссылка устарела, отправь её заново", show_alert=True)
        return

    url, _ = pending
    await callback.answer()

    status_text = {
        "audio": "⏳ Checking audio size...",
        "video_720": "⏳ Checking 720p video size...",
        "video_1080": "⏳ Checking 1080p video size...",
    }.get(kind, "⏳ Checking...")
    status = await callback.message.answer(status_text) if callback.message else None

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            if kind == "audio":
                estimated_size = await estimate_audio_size(url, default_bitrate)
                if status:
                    await status.edit_text("⏳ Downloading audio...")
                result = await download_audio(url, tmpdir, bitrate=default_bitrate)
                path = result["path"]
                if not os.path.isfile(path):
                    raise FileNotFoundError(f"Extracted file not found: {path}")

                audio = FSInputFile(path, filename=f"{result['title']}.mp3")
                await callback.message.answer_audio(
                    audio=audio,
                    caption=f"🎶 {result['title']}",
                    duration=result["duration"] or None,
                    performer="",
                    title=result["title"],
                )
            elif kind in {"video_720", "video_1080"}:
                requested_height = 720 if kind == "video_720" else 1080
                estimated_size = await estimate_video_size(url, requested_height)
                if status:
                    if estimated_size and estimated_size > max_bot_upload_bytes:
                        estimated_parts = math.ceil(estimated_size / int(max_bot_upload_bytes * 0.88))
                        await status.edit_text(
                            f"⏳ Downloading {requested_height}p video (~{human_size(estimated_size)}). "
                            f"It will be sent in about {estimated_parts} parts."
                        )
                    else:
                        await status.edit_text(f"⏳ Downloading {requested_height}p video...")
                result = await download_video(url, tmpdir, requested_height)
                path = result["path"]
                if not os.path.isfile(path):
                    raise FileNotFoundError(f"Video file not found: {path}")

                actual_width = result["width"]
                actual_height = result["height"] or requested_height
                if status:
                    await status.edit_text("🔧 Preparing Telegram-compatible MP4...")
                path = await remux_video_for_telegram(path, tmpdir)
                if os.path.getsize(path) <= max_bot_upload_bytes:
                    video = FSInputFile(path, filename=f"{result['title']}-{actual_height}p.mp4")
                    await callback.message.answer_video(
                        video=video,
                        caption=f"🎬 {result['title']} ({actual_height}p)",
                        duration=result["duration"] or None,
                        width=actual_width,
                        height=actual_height,
                        supports_streaming=True,
                    )
                else:
                    if status:
                        await status.edit_text("✂️ Splitting video into Telegram-sized parts...")
                    parts_dir = os.path.join(tmpdir, "parts")
                    os.makedirs(parts_dir, exist_ok=True)
                    parts = await split_video(path, parts_dir, max_bot_upload_bytes)
                    total_parts = len(parts)
                    for index, part_path in enumerate(parts, start=1):
                        part_video = FSInputFile(
                            part_path,
                            filename=f"{result['title']}-{actual_height}p-part-{index:02d}-of-{total_parts:02d}.mp4",
                        )
                        await callback.message.answer_video(
                            video=part_video,
                            caption=f"🎬 {result['title']} ({actual_height}p) — part {index}/{total_parts}",
                            width=actual_width,
                            height=actual_height,
                            supports_streaming=True,
                        )
            else:
                await callback.message.answer("❌ Unknown download type")
        except yt_dlp.utils.DownloadError as de:
            logger.exception("DownloadError occurred")
            await callback.message.answer(
                f"❌ Download failed: {de}\n"
                "If the video requires login, mount a valid cookies.txt file and set COOKIE_FILE."
            )
        except TelegramNetworkError as e:
            logger.exception("Telegram network error while sending file")
            if "ServerDisconnectedError" in str(e):
                await callback.message.answer(
                    "⚠️ Telegram closed the upload connection after a large file transfer. "
                    "If the video appeared above, everything is fine; if not, send the link again."
                )
            else:
                await callback.message.answer(f"❌ Telegram upload failed: {e}")
        except Exception as e:
            logger.exception("Failed to download or send file")
            await callback.message.answer(f"❌ Failed: {e}")
        finally:
            if status:
                await status.delete()


async def main() -> None:
    start_healthcheck_server()

    if bot_api_server:
        api_server = TelegramAPIServer.from_base(bot_api_server)
        session = AiohttpSession(api=api_server, timeout=upload_timeout)
    else:
        session = AiohttpSession(proxy=proxy_url, timeout=upload_timeout) if proxy_url else AiohttpSession(timeout=upload_timeout)
    bot = Bot(token=bot_token, session=session)
    dispatcher = Dispatcher()
    dispatcher.message.register(handle_youtube_link, F.text.regexp(YOUTUBE_RE))
    dispatcher.callback_query.register(handle_download_choice, F.data.startswith("dl:"))

    me = await bot.get_me()
    HEALTH_STATE["ready"] = True
    logger.info("Bot is running as @%s", me.username)

    try:
        await dispatcher.start_polling(bot)
    finally:
        HEALTH_STATE["ready"] = False
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
