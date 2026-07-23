"""Admin uploads: загрузка медиа для клубов (фото/GIF обложки).

POST /admin/v1/habits/upload_photo — принимает multipart/form-data с полем
`file`, валидирует тип и размер, сохраняет в /app/static/uploads/club_photos/
и возвращает URL для подстановки в habits.photo_url.

Хранилище: локальная папка, прокинутая через volume `club_uploads` на бэкенд
и nginx фронтенда (чтобы статика отдавалась с того же origin).
"""
from __future__ import annotations

import asyncio
import os
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.v1.users import TelegramUserDep
from app.core.logging import get_logger

router = APIRouter()
logger = get_logger("admin.uploads")


ALLOWED_CONTENT_TYPES: set[str] = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}
ALLOWED_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB
STORAGE_DIR = Path("/app/static/uploads/club_photos")


@router.post("/habits/upload_photo")
async def upload_club_photo(
    user: TelegramUserDep,
    file: UploadFile = File(...),  # noqa: B008 — File is FastAPI multipart param
) -> dict:
    """Загрузка фото/GIF для обложки клуба."""
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        logger.warning(
            "upload_photo.invalid_content_type",
            extra={
                "content_type": content_type,
                "user_id": user.id,
                "filename": file.filename,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "unsupported_media_type",
                "message": (
                    "Поддерживаются только JPEG, PNG, GIF, WebP. "
                    f"Получено: {content_type or 'unknown'}"
                ),
            },
        )

    ext = ALLOWED_EXTENSIONS[content_type]
    await asyncio.to_thread(STORAGE_DIR.mkdir, parents=True, exist_ok=True)

    random_part = secrets.token_hex(8)
    storage_filename = f"{int(time.time())}_{random_part}.{ext}"
    storage_path = STORAGE_DIR / storage_filename

    total = 0
    chunks: list[bytes] = []
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "code": "file_too_large",
                    "message": f"Максимальный размер файла — {MAX_FILE_BYTES // (1024*1024)} MB",
                },
            )
        chunks.append(chunk)

    await asyncio.to_thread(storage_path.write_bytes, b"".join(chunks))
    await asyncio.to_thread(os.chmod, storage_path, 0o644)

    public_url = f"/static/uploads/club_photos/{storage_filename}"

    logger.info(
        "upload_photo.stored",
        extra={
            "user_id": user.id,
            "filename": storage_filename,
            "size": total,
            "content_type": content_type,
        },
    )

    return {
        "ok": True,
        "url": public_url,
        "filename": storage_filename,
        "size": total,
        "content_type": content_type,
    }


__all__ = ["router"]
