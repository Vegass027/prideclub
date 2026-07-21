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

# security.py кладём напрямую в /app/, чтобы `import security` находил его через PYTHONPATH=/app
COPY packages/shared/security.py /app/security.py

COPY apps/backend/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

ENV PYTHONPATH=/app:/app/apps/backend

# Копируем alembic.ini и директорию миграций на верхний уровень /app,
# чтобы `alembic upgrade head` находил конфиг в /app без --config.
COPY apps/backend/alembic.ini /app/alembic.ini
COPY apps/backend/alembic /app/alembic

COPY apps/backend /app/apps/backend

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
