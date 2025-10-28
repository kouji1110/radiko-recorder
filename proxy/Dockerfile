FROM python:3.11-slim

# 必要なパッケージをインストール
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    cron \
    at \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY db.py .
COPY fetch_programs.py .
COPY img ./img

# cronとatdサービスを起動するスクリプトを作成
RUN echo '#!/bin/bash\n\
service cron start\n\
service atd start\n\
gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 8 --timeout 600 --worker-class gthread --access-logfile - --error-logfile - app:app' > /start.sh && chmod +x /start.sh

EXPOSE 8080

CMD ["/start.sh"]
