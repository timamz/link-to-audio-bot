import os
import tempfile
import logging
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeAudio
import yt_dlp

# Configure logging
logging.basicConfig(level=logging.INFO)

# Load your credentials from environment variables
api_id    = int(os.getenv("API_ID"))
api_hash  = os.getenv("API_HASH")
bot_token = os.getenv("BOT_TOKEN")
# Path to cookies.txt file exported from your browser
cookie_file = os.getenv("COOKIE_FILE", "cookies.txt")

# Initialize the Telethon client
client = TelegramClient('bot_session', api_id, api_hash)

async def download_audio(url: str, download_dir: str, bitrate: str = "128") -> tuple[str, str, int]:
    """
    Download audio from YouTube at the given bitrate (in kbps) using a cookies.txt file,
    and return a tuple of (file_path, title, duration_seconds).
    """
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
        'cookiefile': cookie_file,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # Build the .mp3 path
        base, _ = os.path.splitext(ydl.prepare_filename(info))
        file_path = f"{base}.mp3"
        # Get a filesystem-safe title
        title = info.get('title', os.path.basename(base))
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
        # Duration in seconds
        duration = int(info.get('duration', 0))
        return file_path, safe_title, duration

@client.on(events.NewMessage(pattern=r'https?://(www\.)?(youtube\.com|youtu\.be)/'))
async def handler(event):
    """
    Triggered when a message containing a YouTube URL is received.
    Downloads the audio using cookies.txt, then sends it back as a native audio message.
    """
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
            logging.exception("DownloadError occurred")
            await event.reply(
                f"❌ Download failed: {de}\n"
                "Make sure your cookies.txt is correct and COOKIE_FILE env var points to it."
            )
        except Exception as e:
            logging.exception("Failed to download or send audio")
            await event.reply(f"❌ Failed: {e}")
        finally:
            await status.delete()

async def main():
    await client.start(bot_token=bot_token)
    print("Bot is running...")
    await client.run_until_disconnected()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
