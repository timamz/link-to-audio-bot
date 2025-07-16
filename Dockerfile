FROM python:3.12.2-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

ARG API_ID
ARG API_HASH
ARG BOT_TOKEN

ENV API_ID=${API_ID} \
    API_HASH=${API_HASH} \
    BOT_TOKEN=${BOT_TOKEN}

WORKDIR /app
RUN pip install --no-cache-dir telethon==1.40.0 yt_dlp==2025.5.22 
COPY bot.py bot.py
COPY cookies.txt cookies.txt

CMD ["python", "bot.py"]
