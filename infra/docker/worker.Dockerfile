FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Shared-пакет (security.py с init_data + JWT)
COPY packages/shared/security.py /app/security.py

# Backend нужен для импорта app.* (logging, exceptions, модели)
COPY apps/backend /app/apps/backend
COPY apps/worker /app/apps/worker

COPY apps/worker/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

ENV PYTHONPATH=/app:/app/apps/backend:/app/apps/worker

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=INFO", "--concurrency=2"]
