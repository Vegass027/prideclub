"""Pytest configuration for apps/bot.

Добавляет корень репо и packages/shared в sys.path. В Docker-образе
security.py копируется в /app/security.py (см. infra/docker/bot.Dockerfile),
поэтому все контейнерные импорты `from security import ...` работают.
"""
import sys
from pathlib import Path

# Корень репо → /app в контейнере бота.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# packages/shared — там лежит security.py, импортируется как `from security import ...`.
sys.path.insert(0, str(ROOT / "packages" / "shared"))