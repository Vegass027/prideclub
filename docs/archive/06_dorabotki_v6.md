# Финальные доработки v6 — последние штрихи + README для разработчика

Закрывает 4 критичных пункта последнего ревью перед коммитом и наиболее важный
пробел из "критически отсутствует" — README для старта dev-окружения. Это последний
документ цикла спецификаций.

---

## 1. request.client может быть None (5 мин)

```python
def get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

logger.warning("auth_failed", extra={
    "path": request.url.path,
    "ip": get_client_ip(request),
    "reason": "invalid_init_data",
})
```

Вынесено в хелпер, используется во всех местах middleware, где логируется IP —
устраняет риск `AttributeError` → 500 за reverse proxy без корректного forwarding.

## 2. Sanity-check на отрицательные bonus_points перед миграцией (10 мин)

```sql
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM memberships WHERE bonus_points < 0) THEN
        RAISE EXCEPTION 'negative bonus_points found in memberships — manual cleanup required before migration';
    END IF;
    IF EXISTS (SELECT 1 FROM users WHERE bonus_points > 0) THEN
        RAISE EXCEPTION 'users.bonus_points already populated — manual review required';
    END IF;
END $$;

UPDATE users u SET bonus_points = COALESCE((
    SELECT SUM(m.bonus_points) FROM memberships m WHERE m.user_id = u.id
), 0)
WHERE EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = u.id AND m.bonus_points > 0);

UPDATE memberships SET bonus_points = 0 WHERE bonus_points > 0;
```

Обе защитные проверки объединены в один блок — миграция останавливается либо на
мусорных отрицательных значениях, либо на признаках повторного запуска, вместо
молчаливого переноса баги в новую схему.

## 3. CREATE EXTENSION pgcrypto явно в миграции (5 мин)

```sql
-- 000_extensions.sql — применяется первой, до всех остальных миграций
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;  -- полезно для мониторинга медленных запросов
```

Минимальная версия PostgreSQL зафиксирована в README как **≥ 14** (запас
относительно требования 13+ для `gen_random_uuid()` без расширения — на 14+ функция
доступна из коробки, но `CREATE EXTENSION IF NOT EXISTS` оставлен для совместимости
со средами на более старых версиях).

## 4. aws-cli вместо s3cmd для бэкапов (явное решение)

**Решение: aws-cli с кастомным endpoint на Selectel S3**, а не устаревший `s3cmd`.

```bash
aws --endpoint-url=https://s3.ru-1.storage.selcloud.ru \
    s3 cp "${DUMP_FILE}.age" "s3://habits-backups/daily/$(basename ${DUMP_FILE}).age"

aws --endpoint-url=https://s3.ru-1.storage.selcloud.ru \
    s3api put-object --bucket habits-backups --key "heartbeat/last_success.txt" \
    --body <(date -u +%Y-%m-%dT%H:%M:%SZ)
```

Обоснование: `aws-cli` активно поддерживается, совместим с S3 API Selectel через
`--endpoint-url`, и уже используется большинством современных инфраструктурных
скриптов — не нужно поддерживать отдельный legacy-инструмент.

Heartbeat-файл `heartbeat/last_success.txt` перезаписывается при каждом успешном
бэкапе — отдельный внешний monitoring-скрипт (cron на другом узле или Prometheus
blackbox exporter) проверяет его свежесть раз в час и алертит, если бэкап не
обновлялся дольше 26 часов.

---

## README.md для старта dev-окружения

```markdown
# Habit Club — dev setup

## Требования
- Python 3.11+
- Docker + Docker Compose
- PostgreSQL 14+ (через Docker, локально не нужен)

## Быстрый старт
1. Скопировать `.env.example` → `.env`, заполнить `BOT_TOKEN`, `SERVICE_SECRET`.
2. `make dev` — поднимает postgres, redis, backend, bot, worker в Docker Compose.
3. `make migrate` — применяет все миграции (включая `000_extensions.sql`).
4. Backend доступен на `http://localhost:8000`, `/health` и `/ready` — для проверки.

## Команды
- `make dev` — поднять окружение
- `make test` — прогнать pytest (unit + integration)
- `make migrate` — применить миграции
- `make migrate-test` — тест миграций: upgrade head → downgrade base → upgrade head
- `make backup-test` — тестовый restore из последнего бэкапа в изолированный контейнер
- `make lint` — ruff + mypy

## Definition of Done для user story
- Код покрыт unit-тестами (happy path + минимум 1 edge case)
- Миграции применяются и откатываются без ошибок (`make migrate-test`)
- Нет прямого использования `float`/`Decimal` для денежных полей — только `int`
- Middleware-проверки (initData / service-token) не обойдены в новых эндпоинтах
- PII не попадает в логи (проверка вручную по `git diff`)
- Прошёл `make lint` и `make test` в CI
```

## .env.example

```
BOT_TOKEN=
SERVICE_SECRET=
POSTGRES_PASSWORD=
POSTGRES_DB=habits
POSTGRES_USER=habits
DATABASE_URL=postgresql+asyncpg://habits:${POSTGRES_PASSWORD}@postgres:5432/habits
REDIS_URL=redis://redis:6379/0
WEBHOOK_SECRET=
S3_ENDPOINT_URL=https://s3.ru-1.storage.selcloud.ru
S3_BACKUP_BUCKET=habits-backups
```

---

## Дополнительно закрыто (важное, малозатратное)

**Issuer claim в service token** — добавлен `iss` рядом с `aud` для аудита источника
токена:

```python
payload = {"service": service_name, "iss": service_name, "aud": target_audience, "iat": now, "exp": now + ttl_seconds}
```

**Явная проверка `if not init_data`** в ветке `/api/v1/` middleware, зеркально
проверке `if not token` в ветке `/internal/` — до вызова `validate_init_data`,
возвращает `missing_init_data` вместо неточного `invalid_init_data`.

**Фиксация версий зависимостей** — правило проекта: `requirements.txt` использует
точные версии (`==`), а не диапазоны; обновления проходят через отдельный PR с
прогоном полного тест-плана, не через автоматический `pip install -U`.

**Dependabot** — минимальная конфигурация `.github/dependabot.yml` с еженедельной
проверкой Python-зависимостей и Docker-образов, PR создаются автоматически, но
мержатся только после прохождения CI и ручного review.

---

## Итоговый чек-лист "готово к первому коммиту" (финальная версия)

- [ ] `000_extensions.sql` (pgcrypto) применяется первой миграцией
- [ ] Sanity-check на отрицательные bonus_points + защита от повторного запуска в миграции
- [ ] `get_client_ip()` хелпер используется везде в middleware вместо прямого `request.client.host`
- [ ] `aws-cli` с Selectel endpoint в `backup_cron.sh`, heartbeat-файл пишется при каждом успехе
- [ ] `README.md` и `.env.example` в корне репозитория
- [ ] `iss`/`aud` claims в service-token, явная проверка `missing_init_data`
- [ ] `requirements.txt` с зафиксированными версиями, `dependabot.yml` настроен
- [ ] Definition of Done зафиксирован в README и применяется к каждой user story

## Перенесено в "желательно до релиза MVP" (не блокирует старт)

Prometheus-метрики `auth_failures_total`/`auth_success_total`, heartbeat-мониторинг
через внешний blackbox-экспортер, `rotate_backups.py` (или готовый пакет из PyPI),
smoke-test после деплоя, `RUNBOOK.md` для оператора.

---

## Итог цикла спецификаций

Шесть итераций доработок (v1–v6) последовательно закрыли: продуктовую механику и
юридическую модель, схему БД с антифродом и идемпотентностью, инфраструктурные
блокеры (хостинг с учётом ФЗ-152, service-to-service аутентификация, race
conditions), операционную готовность (бэкапы, секреты, health-checks) и финальные
штрихи безопасности и старта разработки (README, миграции, claims в токенах).
Пакет специфийкаций — от концепции до v6 — образует связное техническое задание,
готовое к передаче команде разработки для старта первой итерации MVP.
