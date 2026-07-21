#!/usr/bin/env bash
# SSH-wrapper для подключения к VPS с паролем через sshpass.
# Использует sshpass -e (чтение пароля из SSH_ASKPASS/переменной) и env_pass для безопасности.
# Пароль берётся из env переменной HABIT_VPS_PASSWORD.

set -euo pipefail

if [[ -z "${HABIT_VPS_PASSWORD:-}" ]]; then
    echo "ERROR: HABIT_VPS_PASSWORD env not set" >&2
    exit 1
fi

export SSHPASS="${HABIT_VPS_PASSWORD}"
exec /opt/homebrew/bin/sshpass -e /usr/bin/ssh \
    -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=15 \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=5 \
    -p 22 \
    root@155.212.211.44 \
    "$@"