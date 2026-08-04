---
name: habit-club-dev
description: ALWAYS load at the start of ANY chat about the Habit Club (Privichki) project — a Telegram Mini App for habit accountability with monetary penalties. The canonical entry point is `docs/AGENT_BOOTSTRAP.md` (server layout, SSH access, git workflow with required author Vegass <dmitriy@vegass.dev>, deploy procedure, "Всё работает → доки обновлены" ritual). Also load for ANY task touching backend (FastAPI), bot (aiogram), worker (Celery), frontend (React/TS Mini App, including Admin Mini App at admin.prideclub.fun), database migrations, schema changes, security, deployment, or updates to docs/ in this repository. The repo's authoritative docs are docs/01..10 + AGENT_BOOTSTRAP.md + AGENTS.md + apps/frontend/docs/STATUS.md.
---

# Skill: Habit Club (Privichki) Development

## Role

Senior engineer on the Habit Club project — a paid marketplace of closed habit clubs
(planks, early rising, reading) where members submit a daily video-note proof, and
missing a check-in triggers a monetary penalty into the club's prize pool. Discipline
is enforced through money at risk + social visibility ("catch" a violator), not
willpower alone.

Before writing or modifying any code, schema, infra config, or document in this repo,
read the relevant file(s) in `docs/` (01–10), `docs/AGENT_BOOTSTRAP.md` (canonical
entry point — server layout, deploy procedure, doc-maintenance ritual), `AGENTS.md`
(root rules), and `docs/archive/` for historical rationale. Never contradict decisions
already made in those documents — and **if a task genuinely appears to require a
contradiction, surface the conflict to the user explicitly before proceeding**. Do
not silently override.

**Maintenance ritual:** after every completed task where the user confirms "всё
работает", the agent MUST cross-check the affected docs against the actual code/server
state and update any drift. See `docs/AGENT_BOOTSTRAP.md` §12 for the full procedure.
This is not optional — leaving docs stale is a DoD violation for every change.

---

## Non-Negotiable Invariants

These rules must never be violated, regardless of what a specific task seems to ask for.

### Research-First Protocol (Context7) — strictly mandatory

This rule has priority over speed, convenience, and prior assumptions. Violations
are not acceptable even when the issue "looks obvious" from stack traces or memory
layouts.

- **Never guess. Never invent API surface, config keys, behavioral contracts,
  default values, or bug root causes from intuition.** If the answer is not already
  in this repository (`docs/`, `AGENTS.md`, code comments, or existing tests
  asserting it), the next step is documentation — not reasoning.
- **Before modifying code in any of these areas, read the official docs via the
  `context7_*` tools and cite what you learned:**
  - aiogram (any version) — `Dispatcher`, `Bot`, `SimpleRequestHandler`,
    `setup_application`, `dp.startup.register`, webhook lifecycle, FSM,
    middleware, polling vs webhook semantics. Library ID:
    `/websites/aiogram_dev_en_v3_27_0` or `/mastergroosha/aiogram-3-guide`.
  - aiohttp — `web.Application`, `web.run_app`, `AppRunner`, `TCPSite`, access
    logs, shutdown semantics. Library ID: `/aio-libs/aiohttp`.
  - pydantic / pydantic-settings — `BaseSettings`, `SettingsConfigDict`,
    `env_file` lookup semantics, `model_validator`, `model_rebuild`, cached
    properties. Library IDs: `/pydantic/pydantic` and
    `/pydantic/pydantic-settings`.
  - FastAPI / SQLAlchemy 2.x async / asyncpg / Alembic / Celery / Redis /
    aiogram / React / TanStack Query / Zustand / Vite — resolve a library ID
    and query before writing non-trivial code against any of them.
  - Any other unfamiliar library — resolve a library ID first.
- **Mandatory pre-write sequence for any non-trivial change:**
  1. Read the relevant `docs/0X-*.md` sections and `AGENTS.md`.
  2. Search this codebase for prior art (`grep`, `glob`).
  3. `context7_resolve-library-id` for every library you'll touch → then
     `context7_query-docs` with a focused, single-concept question.
  4. Only then start editing code. The first commit message or chat reply for
     the task must reference which doc/section informed each non-obvious
     decision (e.g. "per aiogram v3.27 `setup_application` source: …").
- **Forbidden heuristics that look like shortcuts but are actually violations:**
  - "It probably hangs because of memory" without `dmesg | grep -i oom` and a
    `py-spy dump` showing the actual Python frame. (Real example from this
    repo: the bot hang was OOM during pydantic schema generation — only
    `py-spy` revealed the exact frame.)
  - "It probably needs `-u` / unbuffered stdout" without confirming the
    observed buffering root cause.
  - "The compose config is probably wrong" without running
    `docker compose config` and comparing against the running container's
    actual `docker inspect` output.
  - Patching examples from blog posts or older aiogram versions without
    cross-checking against the project's pinned `requirements.txt`.
- **When context7 returns no useful result** (rare — usually means the
  question is too broad), narrow the question to a single concept and re-query,
  up to 3 attempts. After 3 failed attempts, **stop and tell the user** — do
  not start inventing.
- **When the documentation contradicts what you observe in production**, log
  the conflict (raw `docker logs`, `py-spy dump`, `strace` excerpt, exact
  version numbers) and surface it to the user before "fixing" the docs away.
- **Stack-trace first, hypotheses last.** For bug investigation the order is:
  reproduce → `py-spy dump` / `strace` / `dmesg` / `docker inspect`
  → context7 for the library mentioned in the trace → only then write a fix.

### Money and accounting

- **Money is always `int` (kopecks).** Never use `float` or `Decimal` for any
  monetary or countable field (deposits, penalties, `bonus_points`, prize percentages
  are stored as `NUMERIC` only when they represent a fraction, e.g. `40.00` meaning
  40%). Arithmetic on money is integer arithmetic only. Always grep your diff for
  `Decimal(` and `float(` near money-related identifiers before declaring a change done.
- **One transaction = one handler.** Services never call `session.commit()` — commit
  happens once at the API/handler boundary. Multi-step financial operations (penalty
  charge + `prize_pool` credit + `penalties` row + `transactions` row) happen inside a
  **single** DB transaction with `SELECT ... FOR UPDATE` on the mutated rows.
- **All idempotency is event-scoped, not counter-scoped.** Bonus grants use
  `bonus_applied` on the specific `penalties` row, not a running tally on `users` — this
  survives crash-and-retry without double-crediting. Idempotency keys:
  `penalty:{membership_id}:{date}` for penalties, `telegram_payment_charge_id` for
  payments.

### Identity and auth

- **`user_id` comes only from `request.state.telegram_user`**, set by auth middleware
  after signature validation. Never accept `user_id` as a request parameter/body field
  on `/api/v1/*` endpoints.
- **Two separate auth contours, never mixed:**
  - `/api/v1/*` → validated via `X-Telegram-Init-Data` (HMAC-SHA256 with `WebAppData`
    secret, `hmac.compare_digest`, `auth_date` freshness check, configurable
    `max_age_seconds` per endpoint class).
  - `/internal/*` → validated via `X-Service-Token` (JWT HS256, requires `aud`, `iss`,
    `exp`, `iat`, `leeway=30`, TTL 60s). Bot Gateway/Worker never forward or reuse a
    user's initData to call the backend on their behalf — they authenticate as
    themselves, passing the user identifier explicitly only when the operation is
    initiated by a known authenticated event.
- **CORS allowed origins: `https://web.telegram.org` only.** No wildcard, no localhost
  in production.

### Privacy (ФЗ-152)

- **PII is never logged.** Only the numeric `user_id` may appear in logs/metrics.
  `first_name`, `username`, full `telegram_user` objects are forbidden in log
  statements. Always access `request.client` through a `get_client_ip()` helper that
  handles `None` safely — never `request.client.host` directly.
- **All personal data of Russian users must be stored on infrastructure physically
  located in the Russian Federation** (Selectel VPS, Selectel Object Storage). No
  portion of the system that holds `users`, `memberships`, or `transactions` may run on
  foreign infrastructure, even temporarily.
- **Versioned offer consent is mandatory before any charge.** Consent must be recorded
  in `user_consents(user_id, offer_version_id, accepted_at, ip_address)` referencing a
  row in `offer_versions` — not a single boolean flag.

> ⚠️ **Snapshot 2026-07-22 — расхождение с реальностью.** Прод-сервер —
> **Contabo Cloud VPS 4** (`169.58.52.78`, Германия), не Selectel (РФ). Это нарушает
> правило выше. Миграция на Selectel managed / Yandex Cloud — в плане, не выполнена.
> Текущее состояние БД: 10 тест-юзеров, 0 habits, 0 transactions — реальных ПДн
> клиентов нет. **При любом прод-деплое с реальными ПДн** требуется сначала
> мигрировать инфраструктуру в РФ. Не оставляй без внимания.

### Time

- **Club timezone, not user timezone, drives all scheduling logic.** `habits.timezone`
  is authoritative for check-in windows, catch windows, season boundaries, and
  per-habit cron schedules. `users.timezone` is display-only and must never leak into
  business logic (`Habit.today_in_club_tz()` is the only correct way to compute
  "today"). Per-habit cron jobs (e.g. `close_catch_window`) must be scheduled
  per-habit in `habit.timezone`, not via a single global beat time.

### Data and migrations

- **Migrations are append-only.** Once a migration has been applied to production, it
  is never rewritten, edited, or deleted — only new migrations are added. `make
  migrate-test` (upgrade head → downgrade base → upgrade head) must pass for every
  schema change before merge.
- **Alembic autogenerate is a starting point, not a final answer.** Always review the
  generated diff for accidentally-dropped constraints, lost `server_default`s, and
  wrong index types.
- **All `BIGINT` for any monotonically-growing counter** (`bonus_points`,
  `deposit_balance`, `streak_days`). `INT` overflows under multi-year single-user load.
- **`requirements.txt` uses exact versions (`==`), never ranges.** Updates flow through
  Dependabot PRs that must pass the full test plan before merge — never via
  `pip install -U`.

### Anti-fraud and abuse prevention

- **One check-in per day** is enforced at the DB level via unique index
  `(membership_id, date)`, not at the application level.
- **One penalty per day per reason** is enforced at the DB level via unique index
  `(membership_id, date, reason)`, written with `INSERT ... ON CONFLICT DO NOTHING`.
- **Forwarded messages are rejected** in check-in validation with an explicit reason
  code (`forwarded`) — not a silent `False` that looks like an error to the user.

---

## Architecture Rules

- **Layered architecture**: `api → services → repositories → models`. Routes never
  touch the DB directly; repositories never contain business logic; services never
  call `commit()`.
- **Constructor-based DI everywhere.** No global mutable state, no module-level
  singletons holding request-scoped data. Engine, Redis client, and config objects are
  created once at startup and injected through `__init__`.
- **Async I/O only.** No `time.sleep`, no `requests` library, no synchronous file I/O
  inside request handlers or Celery async tasks. CPU-heavy work goes through
  `asyncio.to_thread`.
- **Domain exceptions + global exception handler.** Business errors are raised as
  typed exceptions (e.g. `CannotCatchSelfError`, `PenaltyAlreadyProcessedError`,
  `CheckinWindowClosedError`, `MembershipNotActiveError`) and mapped centrally to HTTP
  status + `code` field — never raw `try/except` scattered in route handlers, and
  never bare 500s leaking stack traces to clients.
- **Frontend calls the API only through hooks over `shared/api`** — never raw
  `fetch()` calls inside components. Auth headers (initData) are attached once in an
  `axios` interceptor, not per-call.
- **Celery `send_task` by string name.** Backend (`apps/backend/app/services/celery_producer.py`)
  does **NOT** import worker modules (`include=[]` in the producer Celery app). It places
  tasks into the queue by string name (`"worker.tasks.process_checkin.run"`,
  `"worker.tasks.process_penalty.run"`, etc.). Worker registers them via
  `celery_app.include=[...]` in `apps/worker/worker/celery_app.py`. Adding a new task
  = three places: `worker/tasks/<name>.py`, `celery_app.include`, `celery_producer._TASK_NAMES`.
  **Never** import a worker module from backend code.
- **Admin Mini App** lives in `apps/frontend/src/admin/` with its own router, API client
  (`adminHabitsApi`), and hooks (`useAdminHabits`, `useActivateHabit`, `useDeleteHabit`,
  `useRestoreHabit`, `usePermanentDeleteHabit`). Hosted on `admin.prideclub.fun`,
  owner-gated in `apps/backend/app/core/middleware.py` via `OWNER_TELEGRAM_ID` —
  not the user's `telegram_user.id` from initData, but the env-configured owner ID.
  Admin endpoints are mounted under `/admin/v1/*` and require the same `X-Telegram-Init-Data`
  auth as user endpoints, plus the owner check.

---

## Anti-Fraud Rules (mandatory on any check-in / penalty / catch code path)

### Critical (blocks MVP)

- One check-in per day: enforced via unique index `(membership_id, date)`.
- One penalty per day per reason: enforced via unique index
  `(membership_id, date, reason)`, inserted with `INSERT ... ON CONFLICT DO NOTHING`.
- Proof media validated for type (`video_note`/`photo`/`text` per habit config),
  minimum duration (≥3s for video_note), and **must reject forwarded messages**
  (`forward_date is not None` → reject with explicit reason code, not a silent
  `False`).
- Rate limit on the "Catch" action: max 10 actions per 10 seconds per user. The number
  lives in config (`core/constants.py`), never hardcoded in route handlers.
- Catch window = check-in window + 1 hour. All violators are visible to all members —
  no random subsampling (that variant was rejected — see `archive/02_dorabotki_v2.md`).
- `catcher_membership_id != violator_membership_id` is checked as a service-level
  invariant **before** any DB call.
- When a violation window closes with nobody catching it, the penalty is still charged
  with `reason = 'window_closed_no_catch'` (distinct UI copy from `reason = 'caught'`),
  inserted idempotently via the same `ON CONFLICT DO NOTHING` pattern.
- **Two distinct UI copy paths** for the same financial outcome:
  - `reason = 'caught'` → "Тебя поймал участник {имя}, штраф {сумма} ушёл в фонд клуба."
  - `reason = 'window_closed_no_catch'` → "Ты не отметился, штраф {сумма} автоматически
    ушёл в фонд клуба (никто не поймал)."
  Both are identical financially; only the user-facing explanation differs.

### Anti-abuse (should-have, ships in MVP but won't block initial demo)

- `suspicious_pairs` mechanism: flagged pairs still pay penalties normally, but do not
  earn catch bonuses and are excluded from leaderboard credit for that catch. Only two
  admin actions exist on a flagged pair: clear or ban — never "manually investigate."
  Users are never notified they are flagged.
- Catching the same violator more than N times per season in one direction without
  reciprocal catches triggers automatic suspicion flagging (heuristic, not auto-ban).

---

## Financial Integrity Rules

- Bonus points live on `users.id`, not `membership_id` — they survive leaving a club,
  and expire after 90 days of inactivity via a dedicated cron
  (`expire_stale_bonus_points`) with a 7-day-prior notification
  (`notify_bonus_expiring`). The migration from old `memberships.bonus_points` to
  `users.bonus_points` is guarded by sanity checks (no negative values, no double
  populate) — never run it blindly.
- If auto-renewal is active, catch-streak bonuses accumulate as `bonus_points` instead
  of extending `subscription_until` — this prevents double-charging on renewal.
- Prices live in `pricing_rules` table — never hardcoded in application code. The
  pricing function reads the active rule by `habit_rank`.
- Deposit and subscription are distinct payment purposes in the contract/offer — never
  merge them into a single undifferentiated charge. Each must produce its own
  `transactions` row with distinct `type` values.
- `season_prize_rules` percentages must sum to exactly 100% per `metric`, validated
  both on save (admin action) and again before `close_season` runs. `rank_from >=
  1` and `rank_from <= rank_to` must also be validated. Invalid configs raise
  `InvalidPrizeRulesError` and **never** silently produce partial payouts.
- A `prize_rules_snapshot` is taken at season start and used by `close_season` —
  mid-season edits to `season_prize_rules` never retroactively change an in-progress
  season's payout.
- Nightly integrity check: any `penalties` row with `bonus_applied = true` must have a
  matching `transactions` row of type `bonus_catch` — alert the owner via Telegram if
  not.
- Conversions (e.g. "1st check-in → 7 days streak") are computed from
  `daily_streak_snapshots`, not from a full scan of `checkins`. Always filter by date
  range and use the snapshot table for analytics queries.

---

## Security Checklist (apply to every new endpoint)

- [ ] Route is under `/api/v1/*` (initData) or `/internal/*` (service-token) — never
      unauthenticated, never mixing the two contours.
- [ ] `OPTIONS` requests pass through without auth (CORS preflight for Telegram Mini
      App origin `https://web.telegram.org` only, with `max_age=3600` on the CORS
      middleware).
- [ ] Auth failures return structured JSON with a `code` field and correct 401/404 —
      never an unhandled exception leaking a 500. Each exception type
      (`InvalidInitDataError`, `InitDataExpiredError`, `MissingInitDataError`,
      `MissingServiceTokenError`, `ServiceTokenExpiredError`, `InvalidServiceTokenError`)
      has its own distinct `code`.
- [ ] `request.client` is accessed only via `get_client_ip()` helper, never
      `request.client.host` directly.
- [ ] No monetary or user-identifying data is accepted from the client without
      passing through validated `request.state.telegram_user` or
      `request.state.caller`.
- [ ] New Celery tasks and cron jobs that depend on club-local "today" use per-habit
      timezone-aware scheduling (not a single global beat time).
- [ ] No secret value, token, initData fragment, or PII appears in any new log
      statement or metric label.
- [ ] Prometheus counters added for the new endpoint: requests total, errors total,
      duration histogram (labels at most: `path`, `status_class`).

---

## Operational Checklist (apply when changing schema, infra, or deploy)

- [ ] `make migrate-test` passes on a clean DB (upgrade head → downgrade base →
      upgrade head).
- [ ] New tables and indexes match the schemas documented in `docs/06-data-model.md` —
      if the implementation differs, update the doc in the same PR.
- [ ] New columns with `NOT NULL` have an explicit `DEFAULT` or are added in a
      multi-step migration (add nullable → backfill → set NOT NULL).
- [ ] `backup_cron.sh` continues to work: heartbeat file is written on success, and
      `pg_dump` output is encrypted (age/gpg) before upload to Selectel Object Storage.
      > ⚠️ **Snapshot 2026-07-22 — НЕ ВЫПОЛНЕНО на проде.** Скрипт `infra/backup/backup_cron.sh`
      > готов, но на сервере **нет** `aws` CLI, **нет** `S3_*` env-переменных,
      > **не зарегистрирована** cron-задача. До починки данные защищены только Docker
      > volume `habit-club_pgdata` на хосте — при потере VPS данные потеряны. Это
      > блокирует мягкий запуск для реальных пользователей.
- [ ] If a new external service or host is added, it must be located in the Russian
      Federation (no exceptions for temporary staging either — staging may use anonymized
      data only).
- [ ] New env vars are added to `.env.example` (no real values, only variable names +
      brief inline comments).
- [ ] `/ready` endpoint still returns 200 within 4 seconds (2s DB + 2s Redis
      timeouts); any new external dependency check must respect that budget.

---

## Definition of Done (apply before proposing any change as complete)

- [ ] Unit tests cover the happy path + at least one edge case (race, idempotency
      replay, idempotency on retried Celery task, etc.).
- [ ] `make migrate-test` passes (upgrade head → downgrade base → upgrade head) for
      any schema change.
- [ ] No `float`/`Decimal` introduced for money — grep the diff for `Decimal(` and
      `float(` near money-related variables.
- [ ] Middleware auth is not bypassed by the new endpoint (search for the endpoint in
      a routing table review).
- [ ] No PII appears in any log statement added or modified.
- [ ] Structured events are logged for critical operations (penalties, payments,
      bonus grants) with `duration_ms` where applicable.
- [ ] New endpoints expose Prometheus counters for requests and errors.
- [ ] `make lint` and `make test` pass in CI.
- [ ] If a documented decision in `docs/` was changed, the corresponding doc section
      is updated in the same PR (and the rationale is captured in the archive).

---

## Reference Map (where to look before coding)

| Topic | File |
|---|---|
| **Entry point for new AI agent — server layout, SSH, deploy procedure, doc-maintenance ritual** | **`docs/AGENT_BOOTSTRAP.md`** |
| Product concept, economics, gamification | `docs/01-concept.md` |
| Stack, architecture, scaling (snapshot 2026-07-22) | `docs/02-architecture.md` |
| Repo layout, module boundaries | `docs/03-project-structure.md` |
| Coding patterns and examples | `docs/04-code-standards.md` |
| UI/UX components, palette, motion | `docs/05-ui-ux.md` |
| Full DB schema, migrations 000–009, anti-fraud tables | `docs/06-data-model.md` |
| Auth, ФЗ-152, backups, monitoring, alerts | `docs/07-security-and-ops.md` |
| Dev environment setup, DoD, commands | `docs/08-readme.md` |
| Backend prod snapshot, what's working / what's not | `docs/09-prod-readiness.md` |
| Deploy runbook (rsync → build → up) | `docs/10-deploy.md` |
| Historical decisions and rejected alternatives | `docs/archive/01–06_*.md` |
| AI-agent-facing instructions | `AGENTS.md` (root) |
| Frontend-specific status (screens, UI kit, build metrics) | `apps/frontend/docs/STATUS.md` |

If a task seems to require deviating from a rule in this skill, **state the conflict
explicitly and ask before proceeding** — do not silently override a documented
decision from `docs/` or `docs/archive/`.

---

---

## Current Reality (snapshot 2026-07-22) — what works and what doesn't

These are facts about the production server `169.58.52.78`, not aspirations.

### ✅ Works on prod (verified)

- 7 Docker containers healthy: `habit-{postgres,redis,backend,bot,worker,frontend,pgweb}`.
- TLS via Let's Encrypt, auto-renewed, per-domain certs.
- Telegram bot webhook: `https://api.prideclub.fun/bot/webhook` → 200.
- Health/ready endpoints return 200.
- Two-contour auth (`X-Telegram-Init-Data` for `/api/v1/*` + `X-Service-Token` JWT for
  `/internal/*` + owner-gate for `/admin/v1/*`).
- nginx on host proxies by domain to 127.0.0.1 → containers.
- Admin Mini App at `https://admin.prideclub.fun` (commit `c7f8d87`, owner-only).
- Celery Beat (`close_catch_window` at `:05` hourly, plus 3 daily crons).
- Volumes survive `docker compose down`: `pgdata`, `redisdata`, `club_uploads`.
- 161 backend tests + 34 worker tests pass locally.

### ❌ Does NOT work / not deployed

- **Payments = mock on frontend.** `PaymentModal` (`setTimeout(1200)`, text "платёжный
  шлюз не подключён") + `TopUpModal` (`alert()`). Bot does NOT call `bot.send_invoice`
  or `bot.create_invoice_link`. `/internal/payments/confirm` and `process_payment`
  worker task are written but not invoked. `PROVIDER_TOKEN` is NOT in `.env`. DB
  `transactions` table has 0 rows.
- **Backups NOT deployed.** `backup_cron.sh` ready, but no `aws` CLI on server, no
  `S3_*` env, no cron job. Only protection = Docker volume `habit-club_pgdata`.
- **Sentry = no-op** (`SENTRY_DSN` empty). Grafana not deployed. No custom Prometheus
  metrics.
- **Hosting = Contabo (Германия), NOT Selectel (РФ)** — ФЗ-152 violation for real PII.
- **AI-комендант + "Delete account"** — not implemented (planned v2).
- **DB has 0 habits despite 9 files in `uploads/club_photos/`** — POST
  `/admin/v1/habits` not exercised, investigate.
- **`chat_id` vs `habit_id` contract broken** in
  `apps/backend/app/api/v1/internal_payments.py` (expects `chat_id: int`, bot sends
  `habit_id: str`) — 422 without fix.
- **Bot logs plain text** (`logging.basicConfig`), not structlog-JSON like backend/worker.

### 🟡 Known contract drift

- `docs/07-security-and-ops.md` and `docs/08-readme.md` were written assuming Selectel
  hosting — they describe the **target** configuration, not reality. Read with caution.
- `docs/06-data-model.md` §3 used to describe only migrations 000–004; current head is
  `009_chat_id_partial_unique` (migrations 005–008 not in the doc).
- `docs/09-prod-readiness.md` is itself a snapshot — re-check `git log` before relying.

### 🟡 Component-level reality

- **Worker** runs with `--pool=solo` (one process, async inside). Blocks horizontal
  scaling; switch to `prefork` at growth.
- **Frontend bundle** is built inside Docker via multi-stage `node:20-alpine` →
  `nginx:1.27-alpine`. The `dist/` directory on the host (`/app/apps/frontend/dist/`)
  is an artifact of local builds, NOT consumed by the running container.
- **`/app` on server is not a git repo** — it's an unpacked copy. All edits go through
  the deploy pipeline (rsync → `/tmp/privichki_new/` → `/app/apps/X/` → rebuild), never
  directly on the server.

---

## Git workflow — author is non-negotiable

The local `~/.gitconfig` has `Dim41g / ivanov1331d@gmail.com`, but the repo's commit
history uses `Vegass / dmitriy@vegass.dev` (commit `00884e8` through `c7f8d87` all
authored by Vegass). **Every commit MUST be authored by Vegass** to keep the history
consistent. Forgetting this flag means the next commit will be authored by Dim41g and
git history will be polluted.

**Mandatory pattern for ALL commits:**

```bash
git -c user.name=Vegass -c user.email=dmitriy@vegass.dev commit -m "..."
git -c user.name=Vegass -c user.email=dmitriy@vegass.dev push origin main
```

`git status` is checked before commit; dirty state from unrelated work-in-progress
files must be stashed or removed first.

**Push is never automatic.** Wait for explicit user confirmation.

---

## Deploy procedure (exact)

The standard pipeline is in `infra/deploy.sh` and documented in `docs/10-deploy.md`.
Core sequence:

1. **Local:** tests pass → commit with `Vegass` author → `git push origin main`.
2. **Stage to `/tmp/privichki_new/` on server** via `ssh privichki-prod` (ed25519 key) + `rsync`:
   ```bash
   rsync -az --delete apps/backend/ privichki-prod:/tmp/privichki_new/backend/
   rsync -az --delete apps/worker/  privichki-prod:/tmp/privichki_new/worker/
   ```
3. **Apply to `/app/apps/X/`** (in-place, atomic with `--delete`):
   ```bash
   ssh privichki-prod \
     'rsync -az --delete /tmp/privichki_new/backend/ /app/apps/backend/'
   ```
4. **Rebuild + recreate ONLY the affected service**:
   ```bash
   ssh privichki-prod \
     'cd /app/infra && docker compose build backend --no-cache \
      && docker compose up -d backend'
   ```
5. **Verify:** `curl -s http://127.0.0.1:8000/health` and `/ready`.

`privichki-prod` is an SSH alias from `~/.ssh/config` pointing to `root@169.58.52.78`
via ed25519 key `~/.ssh/id_ed25519_privichki`. Password is NOT used in normal work.
See `~/.config/kilo/privichki-bootstrap.md` for recovery (Contabo rescue-mode).

### Forbidden on the server

- ❌ `docker compose down` (kills the whole stack).
- ❌ `docker compose up -d --force-recreate` for the whole compose (only per-service).
- ❌ Editing `/app/apps/X/...` directly — overwritten by the next rsync.
- ❌ `docker cp` for code changes — lost on recreate.
- ❌ `rm -rf /app/**`, `docker system prune`, `.env` edits without explicit OK.
- ❌ SSH password in any commit, chat log, or `docs/*.md`. Local file only.

---

## Payments are mocked in MVP — what to do when wiring real payments

**Current state (mock):** User taps "Присоединиться" in `MarketplacePage` →
`setPayingHabit(habit)` opens `PaymentModal` which runs `setTimeout(1200)` and shows
text "Сейчас платёжный шлюз не подключён". On success → `joinMutation.mutate(habit.id)`
calls `POST /habits/:id/join` (which creates membership without any payment). DB
`transactions` table stays at 0 rows.

**Backend and bot code are written but inactive:** `PaymentService`,
`internal_payments.py`, `bot/handlers/payments.py`, `worker.tasks.process_payment`.
Contract is broken (see "Current Reality" section).

**To enable real Telegram Payments:**

1. **Obtain provider token** from @BotFather → Payments → SmartGlocal (or YooKassa,
   Telegram Stars). Add `PROVIDER_TOKEN=<token>` to `/app/.env` and `.env.example`.
2. **Fix contract in `internal_payments.py`** — replace `chat_id: int` field with
   `habit_id: str` (or add `habit_id` alongside), update bot's POST payload in
   `bot/handlers/payments.py`.
3. **Bot: call `bot.send_invoice(...)`** — add a new handler `bot/handlers/payments_send.py`
   (or extend `start.py`) that builds an invoice via `bot.create_invoice_link(...)`
   and returns the link to the frontend.
4. **Frontend: replace mock** — in `PaymentModal.tsx`, call backend endpoint that
   returns invoice link → `Telegram.WebApp.openInvoice(link)` instead of `setTimeout`.
5. **Test E2E:** small test charge (1 RUB), verify `transactions` row written,
   verify `idempotency_key = charge_id` prevents double-charge on webhook replay.

Until ALL five steps are done, leave `PaymentModal` as mock. Do NOT remove the mock
text — it's the only signal to users that payment is not yet real.

---

## Explicitly Deferred to v2 (do not implement unless asked)

AI-комендант (motivational messages, wall of shame, daily digest — use static
templates instead), chat moderation, full DR testing automation, ADR documentation,
middleware-level rate limiting beyond the "Catch" action limit, anomaly monitoring
dashboards, registration CAPTCHA, standalone web app outside Telegram, separate
admin-api service with its own JWT `aud` claim (current backend uses a single
`backend-api` audience).
