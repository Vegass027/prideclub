"""Тесты для _resize_jpeg (Pravki §7.1 v3.1).

Pillow-based server-side resize аватарок с 640x640 до 160x160.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from worker.tasks.update_user_photos import AVATAR_SIZE_PX, _resize_jpeg


def _make_test_jpeg(width: int, height: int, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """Создаёт JPEG указанного размера в памяти."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_resize_640x640_to_160x160() -> None:
    """Источник 640x640 → результат 160x160, ~2-3 KB."""
    raw = _make_test_jpeg(640, 640)
    resized = _resize_jpeg(raw)
    assert resized is not None
    img = Image.open(io.BytesIO(resized))
    assert img.size == (160, 160)
    assert img.format == "JPEG"
    # 160x160 JPEG @ quality 85 должен быть < 10 KB.
    assert len(resized) < 10_000


def test_resize_preserves_aspect_ratio_with_center_crop() -> None:
    """Не квадратный источник (640x320) → crop до квадрата 160x160."""
    raw = _make_test_jpeg(640, 320)
    resized = _resize_jpeg(raw)
    assert resized is not None
    img = Image.open(io.BytesIO(resized))
    assert img.size == (160, 160)


def test_resize_rgba_to_rgb() -> None:
    """RGBA источник → конвертация в RGB (JPEG не поддерживает alpha)."""
    img = Image.new("RGBA", (640, 640), (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()

    resized = _resize_jpeg(raw)
    assert resized is not None
    out = Image.open(io.BytesIO(resized))
    assert out.mode == "RGB"
    assert out.size == (160, 160)


def test_resize_custom_target_size() -> None:
    """Можно передать target_px отличный от default."""
    raw = _make_test_jpeg(640, 640)
    resized = _resize_jpeg(raw, target_px=80)
    assert resized is not None
    img = Image.open(io.BytesIO(resized))
    assert img.size == (80, 80)


def test_resize_returns_none_on_garbage() -> None:
    """Битый JPEG → return None, не raise."""
    assert _resize_jpeg(b"not a jpeg") is None
    assert _resize_jpeg(b"") is None
    assert _resize_jpeg(b"\xff\xd8\xff") is None  # JPEG magic без данных


def test_resize_preserves_color() -> None:
    """Resize не искажает цвета (sample одного пикселя после resize)."""
    red_jpeg = _make_test_jpeg(640, 640, color=(255, 0, 0))
    resized = _resize_jpeg(red_jpeg)
    assert resized is not None
    img = Image.open(io.BytesIO(resized))
    pixel = img.getpixel((80, 80))  # центр
    # Допускаем небольшое отклонение из-за JPEG-сжатия.
    assert pixel[0] > 200  # red component доминирует
    assert pixel[1] < 50
    assert pixel[2] < 50


def test_avatar_size_px_constant() -> None:
    """Sanity-check: AVATAR_SIZE_PX = 160 (Pravki §7.1 v3.1)."""
    assert AVATAR_SIZE_PX == 160
