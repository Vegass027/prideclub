# e2e-simulation для Habit Club

Переиспользуемые скрипты для прогона полного пользовательского пути
без ручных кликов. Заточено под прогон на проде (в БД — только
тестовые аккаунты, см. `docs/AGENT_BOOTSTRAP.md` §9).

## Файлы

| Файл | Назначение |
|---|---|
| `auth.py` | `FakeUser`, `generate_init_data` (инверсия `validate_init_data`), `generate_service_token` |
| `core.py` | `E2EHttp` (initData / service / webhook headers), `E2EDatabase` (read-only SQL через asyncpg), `load_secrets` |
| `webhook.py` | Фабрики фейковых Telegram Update JSON (`make_video_note_update`, `make_text_update`, `make_photo_update`, `make_start_update`) |
| `scenario_happy_path.py` | Основной сценарий: create-habit → topup → join → checkin (через бота) → catch → reject |
| `cleanup.py` | Удаление e2e-артефактов из БД + Redis. DRY-RUN по умолчанию, `--apply` для реального удаления |

Все скрипты — pure async, без глобального состояния. Секреты
передаются через `os.environ` или читаются из `/app/.env` + `/app/infra/.env`
на проде (paths overridable в `core.load_secrets`).

## Безопасность

- `BOT_TOKEN`, `SERVICE_SECRET`, `WEBHOOK_SECRET`, `OWNER_TELEGRAM_ID`
  подтягиваются из окружения. **Никогда не логируются, не пишутся в
  stdout, не коммитятся.**
- Synthetic user_id берутся из диапазона 99xxx, чтобы не пересекаться
  ни с seed-юзерами (10xxx), ни с реальными прод-юзерами.
- Synthetic chat_id (-100xxx) специально НЕ соответствует реальной
  Telegram-группе. Бот попытается отправить ответ (`bot.send_message`)
  и получит `ChatNotFound` — но наш сценарий это не ломает:
  чек-ин enqueue'ится ДО `send_message`, а worker пишет Checkin в БД
  независимо от того, смог ли бот доставить текстовый ответ.

## Запуск

### На проде через docker exec

Самый простой способ — запустить из контейнера backend, у которого
уже есть `DATABASE_URL`, PYTHONPATH, и установленные зависимости.

```bash
ssh privichki-prod \
  'cd /app/apps/backend && python -m scripts.e2e.scenario_happy_path'
```

`core.load_secrets` подхватит secrets из `/app/.env` (BOT_TOKEN,
SERVICE_SECRET, OWNER_TEGRAM_ID, DATABASE_URL) и `/app/infra/.env`
(WEBHOOK_SECRET). Никакие секреты в чат и stdout не выводятся.

### На локальной машине (против удалённого API)

```bash
E2E_BACKEND_URL=https://api.prideclub.fun \
BOT_TOKEN=... \
SERVICE_SECRET=... \
WEBHOOK_SECRET=... \
OWNER_TELEGRAM_ID=... \
DATABASE_URL='postgresql://habits:...@localhost:5432/habits' \
  python -m scripts.e2e.scenario_happy_path
```

Для DATABASE_URL нужен SSH tunnel либо локальный PostgreSQL с
replica'ой прод-БД (сценарий read-only кроме одной записи
Checkin от воркера).

## Сценарий: `scenario_happy_path`

Двухфазный прогон. Каждый шаг ассертится — падение на любом = exit 1.

### PHASE A — always-open клуб (00:00-23:59 Europe/Moscow)

1. owner создаёт клуб через `POST /admin/v1/habits`
2. owner активирует (`POST .../activate`)
3. 3 synthetic-юзера пополняют deposit (`POST /api/v1/payments/topup`, +200₽)
4. каждый join'ит (`POST /api/v1/habits/{id}/join` → ACTIVE membership)
5. verify: SQL — `memberships.status = 'active'`
6. каждый шлёт video_note (`POST /bot/webhook`)
7. verify: SQL — `checkins.status = 'done'`, ждём worker с timeout 15s

### PHASE B — closed-window клуб (00:00-00:01 Europe/Moscow)

8. owner создаёт клуб с уже закрытым окном (для дневного прогона)
9. 3 юзера join'ят (deposit сохраняется)
10. user₁ ловит user₂ (`POST /api/v1/habits/{id}/catch`)
11. verify: SQL — `penalties(reason='caught')`, `checkins(status='caught')`
12. user₂ шлёт video_note — bot prefilter отвергает `caught_today`
13. verify: SQL — id Checkin строки не изменился (worker не получил задачу)

### Финальный SQL-снэпшот

В конце печатаются таблицы `habits`, `memberships`, `checkins`,
`penalties` (агрегаты за today) для обоих клубов и всех 3 юзеров —
для ручной проверки оператором или вставки в отчёт.

### Cleanup после прогона

Каждый прогон оставляет 2 клуба + 3 memberships + 3-4 checkin +
0-1 penalty + 3 users + 3 transactions. Если оставлять их — через
несколько прогонов в БД будет каша из E2E-prefixed записей.
Решение: `cleanup.py` удаляет всё с префиксом `E2E-`.

```bash
# Посмотреть, что удалится (DRY-RUN по умолчанию)
docker exec habit-backend python -m scripts.e2e.cleanup

# Реально удалить все E2E-артефакты
docker exec -it habit-backend python -m scripts.e2e.cleanup --apply

# Только текущий run (по подстроке run_tag в title)
docker exec -it habit-backend python -m scripts.e2e.cleanup --apply \
  --run-tag 20260813-103343

# Сразу после сценария (флаг --cleanup)
docker exec -e WEBHOOK_SECRET=... habit-backend \
  python -m scripts.e2e.scenario_happy_path --cleanup

# Без интерактивного подтверждения (CI)
docker exec habit-backend python -m scripts.e2e.cleanup --apply --yes
```

Что чистится:
- `habits WHERE title LIKE 'E2E-%'` (cascades → memberships, checkins, penalties)
- `users WHERE id IN (99001, 99002, 99003)` (cascades → transactions)
- Redis `sse:user:*` / `sse:habit:*` / `sse_published:*` для тех же id
- Redis `today:*` (если клали туда) для тех же user_id

После cascade delete остаётся
ровно одна транзакция в БД, освобождённая от e2e-юзеров, — никакие
«забытые» FK не остаются.

## Расширение

Когда happy_path стабилен, можно добавить:
- `scenario_rejects.py` — матрица REJECT-кодов (forwarded / too_short / wrong_topic / out_of_window / not_checkin_topic / already_checked_in)
- `scenario_payment_flow.py` — subscribe + bonus-points + season-close (отдельная ветка для платёжных сценариев)
- параметризация через CLI (например `--users=5 --penalty=100₽`)
- pytest fixtures + параметризация — для встраивания в CI

Все они используют те же `core.py` / `auth.py` / `webhook.py`.
