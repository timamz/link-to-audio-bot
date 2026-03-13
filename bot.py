import logging
import os
import tempfile
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeAudio
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def optional_cookie_file() -> str | None:
    cookie_path = os.getenv("COOKIE_FILE", "/app/cookies.txt")
    return cookie_path if os.path.isfile(cookie_path) else None


api_id = int(require_env("API_ID"))
api_hash = require_env("API_HASH")
bot_token = require_env("BOT_TOKEN")
session_name = os.getenv("SESSION_NAME", "bot_session")
health_port = int(os.getenv("PORT", "8080"))
client = TelegramClient(session_name, api_id, api_hash)
HEALTH_STATE = {"ready": False}


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


async def download_audio(url: str, download_dir: str, bitrate: str = "128") -> tuple[str, str, int]:
    outtmpl = os.path.join(download_dir, '%(id)s.%(ext)s')
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': outtmpl,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': bitrate,
        }],
        'keepvideo': False,
        'noplaylist': True,
    }
    cookie_file = optional_cookie_file()
    if cookie_file:
        ydl_opts['cookiefile'] = cookie_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        base, _ = os.path.splitext(ydl.prepare_filename(info))
        file_path = f"{base}.mp3"
        title = info.get('title', os.path.basename(base))
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
        duration = int(info.get('duration', 0))
        return file_path, safe_title, duration


@client.on(events.NewMessage(pattern=r'https?://(www\.)?(youtube\.com|youtu\.be)/'))
async def handler(event):
    url = event.message.text.strip()
    status = await event.reply("⏳ Downloading audio...")

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            path, title, duration = await download_audio(url, tmpdir, bitrate="128")
            if not os.path.isfile(path):
                raise FileNotFoundError(f"Extracted file not found: {path}")

            # Send as an audio message, so Telegram uses its native player
            await client.send_file(
                event.chat_id,
                path,
                caption=f"🎶 {title}",
                filename=f"{title}.mp3",
                attributes=[
                    DocumentAttributeAudio(
                        duration=duration,
                        title=title,
                        performer=""
                    )
                ]
            )
        except yt_dlp.utils.DownloadError as de:
            logger.exception("DownloadError occurred")
            await event.reply(
                f"❌ Download failed: {de}\n"
                "If the video requires login, mount a valid cookies.txt file and set COOKIE_FILE."
            )
        except Exception as e:
            logger.exception("Failed to download or send audio")
            await event.reply(f"❌ Failed: {e}")
        finally:
            await status.delete()


async def main():
    start_healthcheck_server()
    await client.start(bot_token=bot_token)
    HEALTH_STATE["ready"] = True
    logger.info("Bot is running")
    await client.run_until_disconnected()


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
