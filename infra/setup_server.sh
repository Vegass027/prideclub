#!/usr/bin/env bash
# Подготовка Ubuntu 24.04 VPS под Habit Club.
# Запускать через `ssh user@host "bash setup_server.sh"` или скопировать и выполнить на сервере.
#
# Требования:
#   - root или sudo
#   - 2 ядра / 4 ГБ RAM / 40 ГБ NVMe / 1 Гбит (Selectel)
#   - Минимум 1 ГБ свободного места ДО запуска
#   - Сетевой доступ к apt/docker/Selectel S3

set -euo pipefail
IFS=$'\n\t'

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "ERROR: запускай от root или через sudo."
        exit 1
    fi
}

log() {
    printf '\033[1;34m[setup]\033[0m %s\n' "$*"
}

ok() {
    printf '\033[1;32m[ ok ]\033[0m %s\n' "$*"
}

# -- 1. Системные пакеты ----------------------------------------------------

install_packages() {
    log "Обновляю apt index и ставлю базовые пакеты"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq \
        curl wget ca-certificates gnupg lsb-release \
        ufw fail2ban chrony htop git jq \
        age \
        build-essential libpq-dev python3-dev
    ok "Базовые пакеты установлены"
}

# -- 2. Docker CE 27+ -------------------------------------------------------

install_docker() {
    if command -v docker >/dev/null 2>&1; then
        log "Docker уже установлен: $(docker --version)"
        return
    fi
    log "Ставлю Docker CE"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable --now docker
    ok "Docker установлен: $(docker --version)"
}

# -- 3. Swap 1 ГБ (страховка для пиковых бэкграунд-задач) ------------------

setup_swap() {
    if swapon --show | grep -q '/swapfile'; then
        log "Swap уже существует, пропускаю"
        return
    fi
    log "Создаю swapfile 1 ГБ (страховка под 4 ГБ RAM)"
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    # Снижаем swappiness, чтобы не уходить в swap без нужды
    sysctl vm.swappiness=10
    echo 'vm.swappiness=10' > /etc/sysctl.d/99-swap.conf
    ok "Swap 1 ГБ активен"
}

# -- 4. Firewall ------------------------------------------------------------

setup_firewall() {
    log "Настраиваю ufw: разрешаю SSH/HTTP/HTTPS, остальное закрыто"
    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp comment 'SSH'
    ufw allow 80/tcp comment 'HTTP (Let''s Encrypt + редирект)'
    ufw allow 443/tcp comment 'HTTPS'
    # Закрываем прямые порты бэкенда/постгреса — они доступны только через nginx на 127.0.0.1
    ufw --force enable
    ok "Firewall активен"
}

# -- 5. Fail2ban ------------------------------------------------------------

setup_fail2ban() {
    log "Включаю fail2ban для sshd"
    cat >/etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
port = 22
EOF
    systemctl enable --now fail2ban
    ok "fail2ban активен"
}

# -- 6. NTP + chrony --------------------------------------------------------

setup_chrony() {
    log "Проверяю chrony"
    systemctl enable --now chrony
    if ! chronyc tracking >/dev/null 2>&1; then
        log "WARN: chrony не синхронизировался; оставляю как есть"
    else
        ok "NTP синхронизирован"
    fi
}

# -- 7. Подготовка app-директории -------------------------------------------

prepare_app_dir() {
    log "Создаю /app и docker volume директории"
    mkdir -p /app/{logs,backups,deploy-keys}
    chmod 750 /app/deploy-keys
    # Большие файлы не сохраняем в git
    cat >/app/.gitignore <<'EOF'
logs/
backups/
deploy-keys/
.env
*.pid
EOF
    ok "/app готов"
}

# -- 8. Docker log rotation -------------------------------------------------

setup_docker_logrotate() {
    log "Настраиваю ротацию docker логов (10 МБ × 5 файлов)"
    cat >/etc/docker/daemon.json <<'EOF'
{
    "log-driver": "json-file",
    "log-opts": {
        "max-size": "10m",
        "max-file": "5"
    },
    "storage-driver": "overlay2"
}
EOF
    systemctl restart docker
    ok "Docker log rotation настроена"
}

# -- 9. Linux kernel tuning (sysctl) ----------------------------------------

tune_kernel() {
    log "Применяю sysctl-тюнинг"
    cat >/etc/sysctl.d/99-habit-club.conf <<'EOF'
# Сетевые буферы для 1 Гбит/с
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216

# Защита от SYN flood
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = 4096

# Swap + cache behavior
vm.swappiness = 10
vm.vfs_cache_pressure = 50

# File handles для Postgres + Redis
fs.file-max = 2097152
fs.nr_open = 1048576
EOF
    sysctl --system >/dev/null
    ok "kernel tunables применены"
}

# -- main -------------------------------------------------------------------

main() {
    require_root
    install_packages
    install_docker
    setup_swap
    setup_chrony
    setup_firewall
    setup_fail2ban
    tune_kernel
    setup_docker_logrotate
    prepare_app_dir
    ok "VPS готов к деплою Habit Club"
    echo
    echo "Дальше:"
    echo "  1. Скопируй SSH-ключ для деплоя: ssh-copy-id user@<this-host>"
    echo "  2. Залей проект:                scp -r . user@host:/app/"
    echo "  3. Заполони /app/.env на сервере"
    echo "  4. Запусти:                      ssh user@host 'cd /app && make deploy'"
}

main "$@"