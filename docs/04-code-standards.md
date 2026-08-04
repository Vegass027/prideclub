# 04 — Стандарты кода и архитектурные паттерны

> Snapshot от 2026-07-22. Правила и паттерны актуальны; примеры кода иллюстрируют
> **текущий** стиль (с DI через репозитории, без `commit()` в сервисах, с `send_task`
> через Celery producer).

Принципы и правила, единые для всех сервисов проекта. Цель — масштабируемая кодовая
база без дублирования логики, удобная для добавления новых привычек и фич.

---

## 1. Слоистая архитектура (Layered Architecture)

Каждый сервис строится по принципу разделения ответственности. Каждый слой знает только
о слое ниже и никогда не перепрыгивает через уровень.

```
Роут / Хендлер  →  Сервис (бизнес-логика)  →  Репозиторий (доступ к данным)  →  Модель/БД
```

### ❌ Плохо — вся логика в роуте

```python
# api/checkins.py
@router.post("/checkins")
async def create_checkin(data: CheckinIn, db: AsyncSession = Depends(get_db)):
    membership = await db.execute(
        select(Membership).where(Membership.user_id == data.user_id)
    )
    membership = membership.scalar_one_or_none()
    if not membership:
        raise HTTPException(404, "Membership not found")
    if membership.status != "active":
        raise HTTPException(400, "Membership not active")

    now = datetime.now(tz=pytz.timezone(membership.timezone))
    habit = await db.execute(select(Habit).where(Habit.id == data.habit_id))
    habit = habit.scalar_one_or_none()
    if now.time() < habit.checkin_window_start or now.time() > habit.checkin_window_end:
        raise HTTPException(400, "Outside checkin window")

    checkin = Checkin(membership_id=membership.id, date=now.date(), status="done")
    db.add(checkin)
    await db.commit()
    return checkin
```

**Проблема:** роут отвечает за валидацию, бизнес-правила, доступ к БД. Логику невозможно
переиспользовать из бота или из Celery-задачи — придётся копировать.

### ✅ Хорошо — логика в сервисе, роут — только диспетчер

```python
# services/checkin_service.py
class CheckinService:
    def __init__(self, session: AsyncSession, membership_repo, habit_repo, checkin_repo):
        self._session = session
        self._membership_repo = membership_repo
        self._habit_repo = habit_repo
        self._checkin_repo = checkin_repo

    async def create_checkin(self, user_id: int, habit_id: UUID) -> Checkin:
        membership = await self._membership_repo.get_active(user_id, habit_id)
        if not membership:
            raise MembershipNotActiveError()

        habit = await self._habit_repo.get(habit_id)
        if not habit.is_within_checkin_window(membership.timezone):
            raise CheckinWindowClosedError()

        return await self._checkin_repo.create(membership.id, status="done")


# api/checkins.py
@router.post("/checkins")
async def create_checkin(
    data: CheckinIn,
    service: CheckinServiceDep,  # Annotated[CheckinService, Depends(get_checkin_service)]
):
    checkin = await service.create_checkin(data.user_id, data.habit_id)
    return CheckinOut.model_validate(checkin)
```

> **Зачем `Annotated[X, Depends(...)]` вместо `x: X = Depends(get_x)`?**
>
> С FastAPI 0.95+ рекомендован именно `Annotated`-синтаксис
> ([fastapi.tiangolo.com/tutorial/sql-databases](https://fastapi.tiangolo.com/tutorial/sql-databases)).
> Переиспользуемые alias'ы живут в `apps/backend/app/core/deps.py`:
>
> - `SessionDep = Annotated[AsyncSession, Depends(get_session)]`
> - `RedisDep = Annotated[Redis, Depends(get_redis)]`
>
> Auth alias'ы (`TelegramUserDep`, `TelegramUserDbDep`, `ServiceCallerDep`) — в
> `apps/backend/app/api/v1/users.py`, чтобы избежать циклического импорта с
> `app.core.deps` (auth-helper'ы уже зависят от `app.core.security`).
> Handler'ы импортируют их так:
> ```python
> from app.api.v1.users import TelegramUserDbDep
> from app.core.deps import SessionDep
> ```
>
> **Плюсы:** нет `B008` (function-call-in-default-argument), явная типизация
> зависимости, лучший IDE-рефакторинг, шаринг сигнатуры между handler'ами.
> ruff в `pyproject.toml` имеет `B008` в селекте без per-file-ignore — все
> handler'ы должны использовать `Annotated`.

**Плюсы:** `CheckinService` можно вызвать откуда угодно — из API, из бота, из Celery.
Правило "время должно быть в окне" инкапсулировано в `habit.is_within_checkin_window()`.

---

## 2. Бизнес-правила — в domain-объектах

Если бизнес-условие используется больше одного раза — оно **обязательно** выносится
в метод модели или в отдельный сервис, а не копируется.

### ❌ Плохо — правило продублировано в трёх местах

```python
# в API, в боте, в Celery-задаче — везде одно и то же
if now.time() < habit.checkin_window_start or now.time() > habit.checkin_window_end:
    raise ...
```

### ✅ Хорошо — правило живёт один раз, в модели

```python
# models/habit.py
class Habit:
    def is_within_checkin_window(self, message_sent_at_utc: datetime) -> bool:
        local_dt = message_sent_at_utc.astimezone(ZoneInfo(self.timezone))
        return self.checkin_window_start <= local_dt.time() <= self.checkin_window_end
```

Теперь везде `habit.is_within_checkin_window(now)` — изменение правила применяется
во всех местах сразу.

---

## 3. Репозитории — единая точка доступа к данным

### ❌ Плохо — сырые запросы разбросаны по сервисам

```python
class PenaltyService:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def apply_penalty(self, membership_id, amount):
        result = await self._db.execute(
            select(Membership).where(Membership.id == membership_id)
        )
        membership = result.scalar_one()
        membership.deposit_balance -= amount
        await self._db.commit()  # ❌ commit() в сервисе нарушает "одна транзакция = один handler"
```

Сервис знает про SQLAlchemy, про то, как строится запрос, и **сам коммитит транзакцию** —
это нарушает инвариант "коммит делает middleware или framework-слой". При добавлении
кэша в Redis нужно менять каждый сервис.

### ✅ Хорошо — доступ через репозиторий, без `commit()` в сервисе

```python
# repositories/membership_repository.py
class MembershipRepository:
    def __init__(self, db: AsyncSession, cache: Redis):
        self._db = db
        self._cache = cache

    async def lock_for_update(self, membership_id: UUID) -> Membership:
        """SELECT ... FOR UPDATE — атомарный лок строки для транзакции штрафа."""
        result = await self._db.execute(
            select(Membership).where(Membership.id == membership_id).with_for_update()
        )
        return result.scalar_one()

    async def decrease_deposit(self, membership_id: UUID, amount: int) -> Membership:
        membership = await self.lock_for_update(membership_id)
        membership.deposit_balance -= amount
        await self._db.flush()  # flush, не commit — коммит в handler
        return membership


# services/penalty_service.py
class PenaltyService:
    def __init__(self, session: AsyncSession, membership_repo: MembershipRepository):
        self._session = session
        self._membership_repo = membership_repo

    async def apply_penalty(self, membership_id: UUID, amount: int) -> Membership:
        return await self._membership_repo.decrease_deposit(membership_id, amount)


# api/penalties.py — коммит здесь (или в middleware / Celery-задаче)
@router.post("/penalties")
async def create_penalty(
    payload: PenaltyIn,
    service: PenaltyService = Depends(get_penalty_service),
):
    m = await service.apply_penalty(payload.membership_id, payload.amount)
    await service._session.commit()  # коммит на границе handler
    return PenaltyOut.model_validate(m)
```

Сервис не знает деталей хранения. Изменения — только в репозитории. **Исключение:**
в admin-эндпоинтах допускается `await service._session.commit()` в самом handler
(`/admin/v1/habits` использует это после `service.create(...)` — единственный
публичный кейс, помечен комментарием в коде).

---

## 4. Конфигурация и enum'ы — без магических чисел

### ❌ Плохо

```python
if membership.deposit_balance < 100:
    membership.status = "paused"

reward = amount * 0.7
fund_share = amount * 0.3
```

### ✅ Хорошо

```python
# core/constants.py
class PenaltyConfig:
    CATCHER_SHARE = 0.7
    FUND_SHARE = 0.3

class MembershipStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    LEFT = "left"


# services/penalty_service.py
fund_share = amount * PenaltyConfig.FUND_SHARE

if membership.deposit_balance < habit.penalty_amount:
    membership.status = MembershipStatus.PAUSED
```

**Все правила** видно в одном месте, меньше риска опечатки в строке-статусе.

---

## 5. Переиспользование UI-компонентов

Если один и тот же визуальный паттерн встречается 2+ раза — он выносится в `shared/ui`
как переиспользуемый компонент.

### ❌ Плохо — своя кнопка в каждом экране

```tsx
// Marketplace.tsx
<button style={{background: '#7C5CFC', borderRadius: 12, padding: '12px 20px'}}>
  Присоединиться
</button>

// Today.tsx
<button style={{background: '#7C5CFC', borderRadius: 10, padding: '10px 18px'}}>
  Отправить кружок
</button>
```

### ✅ Хорошо — единый компонент с вариантами

```tsx
// shared/ui/Button/Button.tsx
type ButtonVariant = "primary" | "secondary" | "danger";

interface ButtonProps {
  variant?: ButtonVariant;
  onClick: () => void;
  children: React.ReactNode;
  loading?: boolean;
}

export function Button({ variant = "primary", onClick, children, loading }: ButtonProps) {
  return (
    <button className={cn(styles.base, styles[variant])} onClick={onClick} disabled={loading}>
      {loading ? <Spinner size="sm" /> : children}
    </button>
  );
}
```

---

## 6. Запросы к API — только через хуки

Компоненты **никогда** не вызывают `fetch`/`axios` напрямую — только через хуки
из `shared/hooks`, которые оборачивают `shared/api`.

### ❌ Плохо

```tsx
function Today() {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    fetch(`/api/checkins/today?user_id=${userId}`)
      .then(res => res.json())
      .then(data => setStatus(data));
  }, []);
  ...
}
```

Нет кэширования, нет обработки ошибок, нет retry при провале сети.

### ✅ Хорошо

```tsx
// shared/api/client.ts
export const apiClient = axios.create({ baseURL: import.meta.env.VITE_API_URL });

apiClient.interceptors.request.use((config) => {
  config.headers["X-Telegram-Init-Data"] = getTelegramInitData();
  return config;
});

// shared/api/checkins.ts
export const checkinsApi = {
  getToday: (habitId: string) =>
    apiClient.get<CheckinStatus>(`/checkins/today`, { params: { habit_id: habitId } }),
};

// shared/hooks/useTodayCheckin.ts
export function useTodayCheckin(habitId: string) {
  return useQuery({
    queryKey: ["checkin", "today", habitId],
    queryFn: () => checkinsApi.getToday(habitId).then(res => res.data),
    staleTime: 30_000,
  });
}

// pages/Today/Today.tsx
function Today({ habitId }: { habitId: string }) {
  const { data: status, isLoading } = useTodayCheckin(habitId);
  if (isLoading) return <Skeleton />;
  return <CheckinStatusView status={status} />;
}
```

Авторизация (initData) подключается один раз в interceptor, кэширование и retry —
автоматически через React Query.

---

## 7. Обработка ошибок — доменные исключения + глобальный обработчик

Роуты **не содержат** try/except. Исключения из сервисов сами долетают до глобального
обработчика. Frontend получает предсказуемый `code`, по которому показывает нужное
сообщение.

```python
# core/exceptions.py
class DomainError(Exception):
    status_code = 400
    code = "domain_error"

class MembershipNotActiveError(DomainError):
    status_code = 404
    code = "membership_not_active"

class CheckinWindowClosedError(DomainError):
    status_code = 400
    code = "checkin_window_closed"

class PenaltyAlreadyProcessedError(DomainError):
    status_code = 409
    code = "penalty_already_processed"


# main.py
@app.exception_handler(DomainError)
async def handle_domain_error(request: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code})
```

---

## 8. Новые привычки — только данными, без изменения кода

```python
class Habit(Base):
    id: UUID
    title: str
    proof_type: ProofType         # enum: video_note / photo / text
    checkin_window_start: time
    checkin_window_end: time
    penalty_amount: int
    price_month: int


async def validate_proof(habit: Habit, message: Message) -> bool:
    if habit.proof_type == ProofType.VIDEO_NOTE:
        return message.video_note is not None
    if habit.proof_type == ProofType.PHOTO:
        return message.photo is not None
    if habit.proof_type == ProofType.TEXT:
        return message.text is not None
    return False
```

Добавление привычки "Медитация" — это новая строка в БД, без изменения кода.

## 9. Celery `send_task` — backend НЕ импортирует worker-модули

В этом проекте backend и worker — **отдельные контейнеры**, и backend **не должен**
импортировать worker-таски напрямую (иначе при старте API подтягивается всё дерево
зависимостей worker'а, конфликты версий, лишний startup cost).

Правильный паттерн: backend кладёт задачи в очередь по **строковому имени**.

```python
# apps/backend/app/services/celery_producer.py
_TASK_NAMES: dict[str, str] = {
    "checkin": "worker.tasks.process_checkin.run",
    "penalty": "worker.tasks.process_penalty.run",
    "payment": "worker.tasks.process_payment.run",
}


def send_task(task_kind: str, payload: dict) -> str:
    if task_kind not in _TASK_NAMES:
        raise ValueError(f"Unknown task kind: {task_kind!r}")
    task_name = _TASK_NAMES[task_kind]
    result = _get_app().send_task(task_name, kwargs={"payload": payload})
    return result.id


# Celery-инстанс на стороне backend:
_app = Celery(
    "habit_club_backend_producer",
    broker=broker,
    backend=result_backend,
    include=[],   # НИКАКИХ автоимпортов тасок в backend
)
```

```python
# apps/worker/worker/celery_app.py — здесь worker РЕГИСТРИРУЕТ таски
celery_app = Celery(
    "habit_club_worker",
    broker=broker,
    include=[
        "worker.tasks.process_checkin",
        "worker.tasks.process_penalty",
        "worker.tasks.process_payment",
        "worker.tasks.apply_catch_bonus",
        "worker.tasks.close_catch_window",
        "worker.tasks.expire_bonus_points",
    ],
)
```

**Что важно:**
- Backend может вызвать `send_task("checkin", {...})`, **не зная**, что код живёт в
  worker-контейнере.
- Worker-таски не имеют обратной связи с backend через импорты — могут развиваться
  независимо.
- Добавление новой таски: 1) написать `worker/tasks/<name>.py`, 2) добавить в
  `celery_app.include`, 3) добавить имя в `_TASK_NAMES` в backend. Три точки
  изменения, не одна.
- Для cron-тасок имя указывается прямо в `beat_schedule`, без producer'а.

```python
# tests/test_checkin_service.py
async def test_checkin_rejected_outside_window():
    membership_repo = FakeMembershipRepository(active=True)
    habit_repo = FakeHabitRepository(window_closed=True)
    service = CheckinService(membership_repo, habit_repo, FakeCheckinRepository())

    with pytest.raises(CheckinWindowClosedError):
        await service.create_checkin(user_id=1, habit_id=uuid4())
```

Сервисы получают репозитории через DI (конструктор), в тестах подставляются фейки —
тесты быстрые, не зависят от инфраструктуры.

**Текущее покрытие (2026-07-22):** 161 backend-тест + 34 worker-теста (2 legacy
fail в `test_close_catch_window.py`, не связано с текущей разработкой). Прогоняются
локально + в CI (GitHub Actions), **не** на проде.

---

## 11. Правила проекта (сводка)
| Доступ к данным через репозитории | SQL/ORM-запросы в сервисах |
| Константы и enum'ы в `core/constants.py` | Магические числа в коде |
| Переиспользуемые UI в `shared/ui` | Копипаста JSX/стилей |
| Запросы через хуки над `shared/api` | `fetch`/`axios` в компонентах |
| Доменные исключения + глобальный обработчик | `try/except Exception` в каждом роуте |
| Новые привычки — данными в `habits` | Хардкод условий по названию |
| DI через конструктор | Импорт БД/Redis в функции сервиса |
| Юнит-тесты с фейковыми репозиториями | Тесты с поднятием всей инфраструктуры |
| **Async I/O везде** (`asyncio.sleep`, `aiohttp`, async DB) | `time.sleep`, `requests`, sync file I/O |
| **Одна транзакция = один handler** | `commit()` внутри сервисов |
| **Single engine/session pool** | Создание engine в handler'ах |
| **Все суммы — `int` копейки** | `float`/`Decimal` для денег |
| **Structured logging с контекстом** | `print`/`console.log` без контекста |
| **Retry с backoff для критических операций** | Бесконечный цикл retry |
| **Graceful shutdown для всех сервисов** | Просто `process.exit(0)` |

---

## 12. Python-specific правила (для backend/bot/worker)

### Async-only I/O
- Никаких `time.sleep` в async-коде — только `asyncio.sleep`.
- Никаких `requests` — только `aiohttp` с настроенными таймаутами.
- CPU-heavy операции — в `asyncio.to_thread`.

### Транзакции и сессии
- Одна транзакция = один handler/middleware.
- Сервисы принимают `AsyncSession` как аргумент, **не управляют commit/rollback**.
- `session.add()` / `session.execute()` / `session.flush()` — внутри сервисов;
  commit/rollback — в middleware или framework-слое.

### Engine и пулы
- Один глобальный `create_async_engine` на приложение.
- `pool_size`, `max_overflow`, `pool_pre_ping=True`, `pool_recycle`, `pool_timeout`.
- Никогда не создавать engine внутри handler'ов.

### HTTP клиенты
- Один общий `aiohttp.ClientSession` для внешних API.
- Все запросы обёрнуты в `try/except`, ошибки логируются, пользователю — дружелюбное сообщение.
- Таймауты обязательны.

### Логирование
- Структурированные логи с `user_id`, `request_id`, ключевыми параметрами.
- Медленные операции (> 500 ms) помечаются как "slow" и логируются отдельно.
- Внутренние ошибки не утекают в пользовательские сообщения.
