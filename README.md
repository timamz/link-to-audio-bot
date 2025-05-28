# YouTube Audio Downloader Bot

A simple Telegram bot built with [Telethon](https://docs.telethon.dev/) and [yt-dlp](https://github.com/yt-dlp/yt-dlp) that listens for YouTube links in chats, downloads the audio track as an MP3, and sends it back to the user.

## Prerequisites

* Python 3.8+
* A Telegram bot token (from [@BotFather](https://t.me/BotFather))
* `API_ID` and `API_HASH` (from [https://my.telegram.org](https://my.telegram.org))
* A valid `cookies.txt` file exported from your browser (optional, but required for some videos)

## Installation

1. Clone this repo:

   ```bash
   git clone the repo
   cd yt-audio-bot
   ```
2. Create and activate a virtual environment:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Configuration

### Extracting cookies.txt

1. Open an incognito/private Chrome window.
2. Log into YouTube.
3. Visit `https://www.youtube.com/robots.txt` to isolate the YouTube session.
4. Export **only** the `youtube.com` cookies (e.g., using the "Get cookies.txt LOCALLY" extension).

Create a `.env` file in the project root (or set environment variables directly):

```ini
API_ID=1234567
API_HASH=your_api_hash_here
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
COOKIE_FILE=path/to/cookies.txt
```

* **API\_ID** and **API\_HASH**: Obtain from [https://my.telegram.org](https://my.telegram.org)
* **BOT\_TOKEN**: Provided by @BotFather
* **COOKIE\_FILE**: Path to your exported `cookies.txt` (defaults to `cookies.txt` in project root)

## Usage

```bash
python bot.py
```

Once running, send a YouTube link into any chat with the bot. It will reply “⏳ Downloading audio...” and then send you the MP3 file.