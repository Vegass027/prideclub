FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY packages/shared/security.py /app/security.py

COPY apps/bot/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

ENV PYTHONPATH=/app:/app/apps/bot

COPY apps/bot /app/apps/bot

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8080

CMD ["python", "-m", "bot.main"]
