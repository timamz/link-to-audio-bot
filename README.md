# YouTube Audio Downloader Bot

A simple Telegram bot built with [Telethon](https://docs.telethon.dev/) and [yt-dlp](https://github.com/yt-dlp/yt-dlp) that listens for YouTube links in chats, downloads the audio track as an MP3, and sends it back to the user.

## Prerequisites

* A Telegram bot token (from [@BotFather](https://t.me/BotFather))
* `API_ID` and `API_HASH` (from [https://my.telegram.org](https://my.telegram.org))
* A valid `cookies.txt` file exported from your browser (optional, but required for some videos)

## Installation

1. Clone this repo:

   ```bash
   git clone https://github.com/timamz/link-to-audio-bot
   cd link-to-audio-bot
   `````

## Configuration

### Extracting cookies.txt

1. Open an incognito/private Chrome window.
2. Log into YouTube.
3. Visit `https://www.youtube.com/robots.txt` to isolate the YouTube session.
4. Export **only** the `youtube.com` cookies (e.g., using the "Get cookies.txt LOCALLY" extension).

Create a `.env` file in the project root (or set environment variables directly):

```ini
API_ID=1234567
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
COOKIE_FILE=/app/cookies.txt
PORT=8080
```

* **API\_ID** and **API\_HASH**: Obtain from [https://my.telegram.org](https://my.telegram.org)
* **BOT\_TOKEN**: Provided by @BotFather
* **COOKIE\_FILE**: Optional path to a mounted `cookies.txt`
* **PORT**: Optional healthcheck port exposed by the container

## Usage

Build and run in one command with Docker Compose:

```bash
docker compose up --build -d
```

Stop the bot:

```bash
docker compose down
```

If you do not need YouTube cookies, remove the `volumes` section from `compose.yaml` and omit `COOKIE_FILE` from `.env`.

Check that the bot is healthy:

```bash
docker compose logs
curl http://localhost:8080/healthz
```

Once running, send a YouTube link into any chat with the bot. It will reply “⏳ Downloading audio...” and then send you the MP3 file.
