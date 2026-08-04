#!/usr/bin/env bash
# Деплой Habit Club на удалённый Ubuntu 24.04 VPS.
# Использование:
#   SSH_KEY=~/.ssh/id_ed25519 SERVER=user@host make deploy
#   или
#   ./infra/deploy.sh user@host ~/.ssh/id_ed25519
#
# Предполагает что на сервере уже выполнен setup_server.sh.

set -euo pipefail
IFS=$'\n\t'

SERVER="${1:-${SERVER:-}}"
SSH_KEY="${2:-${SSH_KEY:-}}"

if [[ -z "$SERVER" ]]; then
    echo "Usage: $0 user@host [~/.ssh/id_ed25519]" >&2
    echo "  или SSH_KEY=~/.ssh/id_ed25519 SERVER=user@host make deploy" >&2
    exit 1
fi

if [[ -n "$SSH_KEY" && ! -f "$SSH_KEY" ]]; then
    echo "ERROR: SSH key not found: $SSH_KEY" >&2
    exit 2
fi

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
[[ -n "$SSH_KEY" ]] && SSH_OPTS=(-i "$SSH_KEY" "${SSH_OPTS[@]}")

REMOTE="ssh"
[[ -n "$SSH_KEY" ]] && REMOTE="ssh -i $SSH_KEY"
REMOTE="${REMOTE} ${SSH_OPTS[*]} ${SERVER}"

REMOTE_CMD() {
    $REMOTE "$@"
}

log() {
    printf '\033[1;34m[deploy]\033[0m %s\n' "$*"
}

ok() {
    printf '\033[1;32m[ ok ]\033[0m %s\n' "$*"
}

# -- 1. Проверить окружение -------------------------------------------------

check_remote() {
    log "Проверяю ${SERVER}"
    REMOTE_CMD 'bash -s' <<'EOF'
set -e
command -v docker >/dev/null || { echo "NO docker"; exit 1; }
docker compose version >/dev/null || { echo "NO compose"; exit 1; }
test -f /app/.env || { echo "NO /app/.env"; exit 1; }
echo "host: $(hostname)"
echo "docker: $(docker --version | head -1)"
echo "mem: $(free -h | awk 'NR==2 {print $7}') free"
echo "disk: $(df -h /app | awk 'NR==2 {print $4}') free"
EOF
}

# -- 2. Синхронизировать код (rsync, exclude .git/venv/__pycache__) ---------

sync_code() {
    log "Синхронизирую код"
    local RSYNC_RSH="ssh"
    [[ -n "$SSH_KEY" ]] && RSYNC_RSH="ssh -i ${SSH_KEY}"
    rsync -az --delete \
        --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='.venv' \
        --exclude='node_modules' \
        --exclude='dist' \
        --exclude='*.pyc' \
        --exclude='.pytest_cache' \
        --exclude='.mypy_cache' \
        --exclude='.ruff_cache' \
        -e "$RSYNC_RSH ${SSH_OPTS[*]}" \
        ./ "${SERVER}:/app/"
    ok "Код синхронизирован"
}

# -- 3. Build образов -------------------------------------------------------

build_images() {
    log "Собираю Docker-образы (это займёт 3–5 минут)"
    REMOTE_CMD 'cd /app && docker compose build --pull --parallel'
    ok "Образы собраны"
}

# -- 4. Чистка старого build cache и dangling образов -----------------------
#
# Без этого шага build cache накапливается до десятков GB после каждого
# --no-cache rebuild (Pravki.md §10 — пост-мортем от 2026-08-04).
# - docker image prune -f: dangling (без тегов). На нашем флоу 0, но
#   подчищаем на всякий случай после свежего build.
# - docker builder prune -f --filter "until=72h": build cache старше 72ч.
#   Активные слои текущих 9 образов (4 наших + 5 базовых) НЕ удаляются —
#   они нужны для следующего rebuild.
# Пропускная способность: 5–30 сек на типичном VDS.

prune_images() {
    log "Чищу dangling образы и build cache старше 72ч"
    REMOTE_CMD 'docker image prune -f' || true
    REMOTE_CMD 'docker builder prune -f --filter "until=72h"' || true
    REMOTE_CMD 'df -h / --output=used,avail,pcent'
    ok "Образы и cache почищены"
}

# -- 5. Миграции -----------------------------------------------------------

run_migrations() {
    log "Применяю миграции БД"
    REMOTE_CMD 'cd /app && docker compose run --rm backend alembic upgrade head'
    ok "Миграции примены"
}

# -- 6. Поднять стек -------------------------------------------------------

up_stack() {
    log "Поднимаю стек"
    REMOTE_CMD 'cd /app && docker compose up -d --remove-orphans'
    ok "Стек работает"
}

# -- 7. Ждать готовности ----------------------------------------------------

wait_ready() {
    log "Жду 60с прогрева (backend стартует ~20с + apply)"
    sleep 30
    log "Проверяю /health"
    if REMOTE_CMD 'curl -fsS http://localhost:8000/health'; then
        ok "backend /health отвечает"
    else
        log "WARN: /health недоступен, проверяю docker logs"
        REMOTE_CMD 'cd /app && docker compose logs --tail=50 backend'
        exit 3
    fi
}

# -- 8. Регистрация webhook Telegram бота ----------------------------------

register_webhook() {
    log "Регистрирую webhook бота в Telegram"
    if ! REMOTE_CMD 'cd /app && docker compose exec -T backend python -m scripts.register_webhook'; then
        log "WARN: webhook не зарегистрирован (настрой BOT_TOKEN и WEBHOOK_BASE_URL позже)"
    fi
}

# -- main -------------------------------------------------------------------

main() {
    check_remote
    sync_code
    build_images
    prune_images
    run_migrations
    up_stack
    wait_ready
    register_webhook
    ok "Деплой завершён: https://${SERVER}/habits (frontend) + /bot/webhook (Telegram API)"
}

main "$@"