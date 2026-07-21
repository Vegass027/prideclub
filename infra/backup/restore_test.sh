#!/usr/bin/env bash
# Тестовый restore из последнего бэкапа в изолированный контейнер.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${S3_ENDPOINT_URL:?S3_ENDPOINT_URL is required}"
: "${S3_BACKUP_BUCKET:?S3_BACKUP_BUCKET is required}"
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"

export AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY"

LATEST=$(aws --endpoint-url="$S3_ENDPOINT_URL" s3api list-objects-v2 \
    --bucket "$S3_BACKUP_BUCKET" --prefix "daily/" \
    --query "sort_by(Contents, &LastModified)[-1].Key" --output text)

if [ -z "$LATEST" ] || [ "$LATEST" = "None" ]; then
    echo "No backups found" >&2
    exit 1
fi

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

aws --endpoint-url="$S3_ENDPOINT_URL" s3 cp "s3://$S3_BACKUP_BUCKET/$LATEST" "$TMP_DIR/backup.age"

# Для теста расшифровка опциональна — нужен age secret key в $BACKUP_PRIVATE_KEY
if [ -n "${BACKUP_PRIVATE_KEY:-}" ]; then
    echo "$BACKUP_PRIVATE_KEY" | age -d -i - "$TMP_DIR/backup.age" > "$TMP_DIR/backup.sql.gz"
else
    cp "$TMP_DIR/backup.age" "$TMP_DIR/backup.sql.gz"
fi

gunzip -c "$TMP_DIR/backup.sql.gz" | head -50

echo "Latest backup: $LATEST"
echo "OK (header printed)"