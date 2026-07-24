"""Pravki.md §7.1 v3: утилита для вставки avatar location в прод-nginx конфиг.

Продовый /etc/nginx/sites-enabled/habit-club отличается от репо
(infra/nginx/nginx.prideclub.conf) — это конфиг со множественными
server{} блоками для каждого домена. Прямое scp не подходит.

Скрипт добавляет location для /api/v1/users/N/photo$ перед
location /api/ в server{} блоке app.prideclub.fun (Pravki §7.1 v3).

Использование:
    python3 insert_avatar_loc.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys


AVATAR_LOCATION = """  # Pravki.md §7.1 v3 (подход D): аватарки через nginx alias + error_page fallback.
  # 1. Сначала проксируем на habit_frontend /avatars/N.jpg (внутренний путь).
  #    Frontend nginx отдаёт файл из volume club_uploads
  #    (/usr/share/nginx/html/static/avatars/).
  # 2. На 404 (frontend вернул error_page) → @avatar_backend_fallback
  #    → habit_backend (cold cache: скачает с TG, сохранит, frontend будет hit).
  # После первого hit backend не участвует, nginx отдаёт 0-cost.
  # БЕЗ try_files в alias location — конфликтует (try_files $uri =404
  # резолвит $uri в root, не в alias, и даёт false 404).
  location ~ ^/api/v1/users/([0-9]+)/photo$ {
    set $avatar_user_id $1;
    proxy_pass http://habit_frontend/avatars/$avatar_user_id.jpg;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_intercept_errors on;
    error_page 404 = @avatar_backend_fallback;
  }

  location @avatar_backend_fallback {
    proxy_pass http://habit_backend/api/v1/users/$avatar_user_id/photo;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

"""

# Маркер начала server{} блока app.prideclub.fun
BLOCK_MARKER = "  server_name app.prideclub.fun;"

# Маркер location /api/ ВНУТРИ блока app.prideclub.fun (для точного поиска).
API_MARKER = """  # /api/v1/* и /internal/* → backend (FastAPI CORSMiddleware обработает OPTIONS)
  location /api/ {"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="/etc/nginx/sites-enabled/habit-club",
        help="Path to nginx config",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print diff without modifying",
    )
    args = parser.parse_args()

    with open(args.config) as f:
        content = f.read()

    # Проверяем что location уже не вставлен
    if "proxy_pass http://habit_frontend/avatars/$avatar_user_id.jpg" in content:
        print("Avatar location already present, nothing to do")
        return

    block_idx = content.find(BLOCK_MARKER)
    if block_idx < 0:
        print(f"ERROR: BLOCK_MARKER not found in {args.config}")
        sys.exit(1)

    api_idx = content.find(API_MARKER, block_idx)
    if api_idx < 0:
        print(f"ERROR: API_MARKER not found after BLOCK_MARKER")
        sys.exit(1)

    new_content = content[:api_idx] + AVATAR_LOCATION + content[api_idx:]

    if args.dry_run:
        print("=== DRY RUN ===")
        print("Would insert at offset", api_idx)
        print(AVATAR_LOCATION[:200] + "...")
        return

    # Backup
    import shutil
    backup = args.config + f".bak.{__import__('time').time()}"
    shutil.copy(args.config, backup)
    print(f"Backup: {backup}")

    with open(args.config, "w") as f:
        f.write(new_content)
    print(f"OK: inserted {len(AVATAR_LOCATION)} chars at offset {api_idx}")


if __name__ == "__main__":
    main()
