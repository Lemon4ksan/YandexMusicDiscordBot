FROM python:3.13-slim

RUN apt-get update && apt-get install -y ffmpeg && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY MusicBot /app/MusicBot

ENV PYTHONPATH=/app

# Команда для запуска бота
CMD ["python", "./MusicBot/main.py"]
