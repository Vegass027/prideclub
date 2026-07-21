#!/usr/bin/env bash
# Ежедневный бэкап PostgreSQL → S3 (Selectel) с шифрованием age.
# Использование: ./backup_cron.sh
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${S3_ENDPOINT_URL:?S3_ENDPOINT_URL is required}"
: "${S3_BACKUP_BUCKET:?S3_BACKUP_BUCKET is required}"
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"
: "${BACKUP_PUBLIC_KEY:?BACKUP_PUBLIC_KEY is required}"

TS="$(date -u +%Y%m%d_%H%M%S)"
DUMP_FILE="/tmp/backup_${TS}.sql.gz"
ENC_FILE="/tmp/backup_${TS}.sql.gz.age"

trap 'rm -f "$DUMP_FILE" "$ENC_FILE"' EXIT

pg_dump --no-owner --no-privileges "$DATABASE_URL" | gzip > "$DUMP_FILE"

if [ ! -s "$DUMP_FILE" ]; then
    echo "ERROR: backup file is empty" >&2
    exit 1
fi

age -e -r "$BACKUP_PUBLIC_KEY" -o "$ENC_FILE" "$DUMP_FILE"

export AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY"

aws --endpoint-url="$S3_ENDPOINT_URL" \
    s3 cp "$ENC_FILE" "s3://$S3_BACKUP_BUCKET/daily/$(basename "$ENC_FILE")"

aws --endpoint-url="$S3_ENDPOINT_URL" \
    s3api put-object \
    --bucket "$S3_BACKUP_BUCKET" \
    --key "heartbeat/last_success.txt" \
    --body <(date -u +%Y-%m-%dT%H:%M:%SZ)

python3 "$(dirname "$0")/rotate_backups.py" \
    --bucket "$S3_BACKUP_BUCKET" \
    --keep-daily 7 --keep-weekly 4 --keep-monthly 12

echo "Backup ${TS} completed"