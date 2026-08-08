from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.constants import PROOF_TYPE_VALUES


class MarketplaceResponse(BaseModel):
    items: list[HabitOut]


class WalletClubOut(BaseModel):
    """Один клуб в `GET /me/wallet`.

    Pravki-deposit-sse.md §Z-4.1 + Pravki-subscribe-and-join.md §Z-17 (substep 1):
    содержит всё необходимое для UI-кнопки «Открыть клуб» без дополнительных
    запросов — penalty_amount, can_checkin (deposit >= penalty), статус
    последнего recompute, и `subscription_until` для pre-check режима
    модалки оплаты («full» vs «deposit-only», см. §Z-13.1 матрица).
    """

    model_config = ConfigDict(from_attributes=True)

    habit_id: str
    title: str
    penalty_amount: int
    can_checkin: bool  # user.deposit_balance >= habit.penalty_amount
    status: str  # "active" | "paused" — последний результат recompute_pause_status
    # Pravki-subscribe-and-join.md §Z-17 substep 1: добавлено для pre-check на фронте.
    # None если юзер ещё ни разу не платил подписку (или membership LEFT/свежая).
    # Фронт сравнивает с date.today() чтобы выбрать режим модалки оплаты.
    subscription_until: date | None = None


class WalletOut(BaseModel):
    """Pravki-deposit-sse.md §Z-4.1: глобальный депозит юзера + список клубов.

    `can_checkin` дублирует результат `MembershipService.recompute_pause_status` —
    UI Today page использует его для блокировки кнопки «Открыть клуб» без
    дополнительных запросов.
    """

    deposit_balance: int  # копейки
    active_clubs: list[WalletClubOut]


class HabitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str | None = None
    chat_id: int
    checkin_window_start: str
    checkin_window_end: str
    timezone: str
    penalty_amount: int
    price_month: int
    proof_type: str
    proof_types: list[str] = Field(default_factory=list)
    prize_pool: int
    members_count: int = 0
    is_active: bool
    photo_url: str | None = None
    telegram_invite_link: str | None = None
    checkin_topic_thread_id: int | None = None
    notifications_topic_thread_id: int | None = None
    chat_topic_thread_id: int | None = None
    checkin_topic_link: str | None = None
    notifications_topic_link: str | None = None
    chat_topic_link: str | None = None
    chat_link: str | None = None


class MembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: int
    habit_id: str
    status: str
    subscription_until: date | None
    auto_renew_enabled: bool
    joined_at: datetime


class CheckinStatusOut(BaseModel):
    """Статус чек-ина пользователя в клубе + сводная статистика.

    Pravki.md 2026-07-24: "сколько отчекинился раз столько и на счетчике".
    checkin_count — общее число done-чекинов за всё время (total).
    streak_days — consecutive (текущая серия подряд, обнуляется если
    сегодня не отмечен). penalties_count/total — антифрод-метрики.
    """

    status: str  # done | missed | pending | not_started
    checkin_count: int
    streak_days: int
    penalties_count: int
    penalties_total: int  # в копейках
    deadline_at: datetime | None


class TodayResponse(BaseModel):
    habit: HabitOut
    membership: MembershipOut
    checkin: CheckinStatusOut


class CheckinIngestPayload(BaseModel):
    """Payload из Bot Gateway → Worker → Backend (через внутреннее API)."""

    user_id: int
    chat_id: int
    message_thread_id: int | None = None
    message_id: int
    proof_type: str  # video_note | photo | text
    message_sent_at: datetime
    text: str | None = None
    duration_seconds: int | None = None


class InternalCheckinResult(BaseModel):
    checkin_id: str | None
    accepted: bool
    # ok | checkin_already_exists | checkin_window_closed |
    # membership_not_active | invalid_proof | habit_not_found
    code: str


class AdminHabitCreateRequest(BaseModel):
    """POST /admin/v1/habits — создание клуба (TZ §3.6.4).

    `is_active` намеренно отсутствует: при создании всегда False.

    Topic-scoped (migration 010):
    - `checkin_topic_link` и `notifications_topic_link` обязательны — клуб
      нельзя создать без привязки к топикам форума. Формат
      `https://t.me/c/<chat_id>/<thread_id>`.

    Multi-proof (migration 012):
    - `proof_types: list[str]` — массив 1..3 значений ∈ {video_note, photo, text}.
    - `proof_type` опционален — если указан, конвертируется в `[proof_type]`
      для обратной совместимости. Если переданы оба — валидируется, что
      `proof_type ∈ proof_types`.
    """

    title: str = Field(min_length=3, max_length=128)
    description: str | None = None
    photo_url: str | None = Field(default=None, max_length=512)
    telegram_invite_link: str | None = Field(default=None, max_length=512)
    stat_name: str = Field(min_length=1, max_length=64)
    stat_icon: str | None = Field(default=None, max_length=16)
    chat_id: int | None = Field(default=0)
    checkin_window_start: time
    checkin_window_end: time
    timezone: str = Field(min_length=1, max_length=64)
    proof_type: str | None = Field(default=None, pattern="^(video_note|photo|text)$")
    proof_types: list[str] | None = Field(default=None, min_length=1, max_length=3)
    price_month: int = Field(gt=0)
    penalty_amount: int = Field(gt=0)
    stat_gain_per_checkin: int = Field(default=2, gt=0)
    stat_loss_per_miss: int = Field(default=1, gt=0)
    member_limit: int | None = Field(default=None, gt=0)
    curator_id: int | None = None
    checkin_topic_link: str = Field(min_length=1, max_length=512)
    notifications_topic_link: str = Field(min_length=1, max_length=512)
    chat_topic_link: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _resolve_proof_types(self) -> AdminHabitCreateRequest:
        """Привести proof_types к каноническому виду:
        - ровно 1..3 уникальных значений из PROOF_TYPE_VALUES;
        - proof_type (если указан) обязан входить в proof_types;
        - если proof_types не передан, берём [proof_type] или ['video_note'].
        """
        valid = PROOF_TYPE_VALUES
        pt = self.proof_type
        pts = self.proof_types

        if pts is None and pt is None:
            pts = ["video_note"]
        elif pts is None:
            pts = [pt]

        # Валидация значений.
        unknown = [p for p in pts if p not in valid]
        if unknown:
            raise ValueError(
                f"proof_types содержит недопустимые значения: {unknown}. "
                f"Допустимо: {valid}"
            )
        # Уникальность + 1..3.
        if len(set(pts)) != len(pts):
            raise ValueError("proof_types не должны содержать дубликатов")
        if not (1 <= len(pts) <= 3):
            raise ValueError("proof_types должны содержать от 1 до 3 значений")

        # Если задан proof_type — он должен быть в proof_types.
        if pt is not None and pt not in pts:
            raise ValueError(
                f"proof_type={pt} должен входить в proof_types={pts}"
            )

        self.proof_types = pts
        self.proof_type = pts[0]
        return self


class AdminHabitUpdateRequest(BaseModel):
    """PATCH /admin/v1/habits/{id} — частичное обновление.

    Любые поля опциональны. Финансовые поля замораживаются после первого вступления
    (TZ §3.6.7) — сервис бросит HabitValidationError если в клубе уже есть участники.

    Multi-proof (migration 012):
    - `proof_types` и `proof_type` оба опциональны, но если переданы —
      валидируются так же, как в Create (1..3 уникальных, входят в
      PROOF_TYPE_VALUES, proof_type ∈ proof_types).
    """

    title: str | None = Field(default=None, min_length=3, max_length=128)
    description: str | None = None
    photo_url: str | None = Field(default=None, max_length=512)
    telegram_invite_link: str | None = Field(default=None, max_length=512)
    stat_name: str | None = Field(default=None, min_length=1, max_length=64)
    stat_icon: str | None = Field(default=None, max_length=16)
    chat_id: int | None = Field(default=None)
    checkin_window_start: time | None = None
    checkin_window_end: time | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    proof_type: str | None = Field(default=None, pattern="^(video_note|photo|text)$")
    proof_types: list[str] | None = Field(default=None, min_length=1, max_length=3)
    price_month: int | None = Field(default=None, gt=0)
    penalty_amount: int | None = Field(default=None, gt=0)
    stat_gain_per_checkin: int | None = Field(default=None, gt=0)
    stat_loss_per_miss: int | None = Field(default=None, gt=0)
    member_limit: int | None = Field(default=None, gt=0)
    curator_id: int | None = None
    checkin_topic_link: str | None = Field(default=None, min_length=1, max_length=512)
    notifications_topic_link: str | None = Field(default=None, min_length=1, max_length=512)
    chat_topic_link: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _resolve_proof_types(self) -> AdminHabitUpdateRequest:
        valid = PROOF_TYPE_VALUES
        pt = self.proof_type
        pts = self.proof_types

        # Если ничего не передано — оставляем как есть, сервис ничего не меняет.
        if pts is None and pt is None:
            return self
        if pts is None:
            pts = [pt]
        else:
            unknown = [p for p in pts if p not in valid]
            if unknown:
                raise ValueError(
                    f"proof_types содержит недопустимые значения: {unknown}"
                )
            if len(set(pts)) != len(pts):
                raise ValueError("proof_types не должны содержать дубликатов")
            if not (1 <= len(pts) <= 3):
                raise ValueError("proof_types должны содержать от 1 до 3 значений")

        if pt is not None and pt not in pts:
            raise ValueError(f"proof_type={pt} должен входить в proof_types={pts}")

        self.proof_types = pts
        self.proof_type = pts[0]
        return self


class AdminHabitToggleRequest(BaseModel):
    is_active: bool


class AdminHabitOut(BaseModel):
    """Полная карточка клуба для админки."""

    id: str
    title: str
    description: str | None
    chat_id: int
    checkin_window_start: str
    checkin_window_end: str
    timezone: str
    penalty_amount: int
    price_month: int
    proof_type: str
    proof_types: list[str] = Field(default_factory=list)
    prize_pool: int
    is_active: bool
    photo_url: str | None
    telegram_invite_link: str | None
    stat_name: str
    stat_icon: str | None
    stat_gain_per_checkin: int
    stat_loss_per_miss: int
    member_limit: int | None
    curator_id: int | None
    checkin_topic_thread_id: int | None
    notifications_topic_thread_id: int | None
    checkin_topic_link: str | None
    notifications_topic_link: str | None
    chat_topic_thread_id: int | None
    chat_topic_link: str | None
    archived_at: datetime | None
    created_at: datetime
    active_members_count: int = 0


class AdminHabitsListResponse(BaseModel):
    items: list[AdminHabitOut]


class AdminHabitForceFinancialsRequest(BaseModel):
    """Force-update price_month / penalty_amount вне заморозки.

    Доступно ТОЛЬКО owner'у (middleware /admin/v1/* уже гейтит доступ).
    `confirm=True` обязательно — защита от случайного клика.

    Семантика:
    - Меняет ТОЛЬКО habits.price_month и/или habits.penalty_amount.
    - НЕ трогает: users.deposit_balance, memberships.subscription_until,
      memberships.auto_renew_enabled, memberships.status — у участников
      ничего не меняется. Уже оплаченные подписки продолжают действовать
      до subscription_until по старой цене.
    """

    price_month: int | None = Field(default=None, gt=0)
    penalty_amount: int | None = Field(default=None, gt=0)
    confirm: bool = Field(default=False)


class AdminHabitForceFinancialsResponse(BaseModel):
    ok: bool
    habit_id: str
    old_price_month: int
    new_price_month: int
    old_penalty_amount: int
    new_penalty_amount: int
    updated_at: datetime


class AdminHabitActionResponse(BaseModel):
    ok: bool
    habit_id: str
    is_active: bool | None = None
    archived_at: datetime | None = None


class AdminHabitChatStatusResponse(BaseModel):
    """Текущее состояние chat_id для клуба.

    chat_id == 0 — бот ещё не добавлен в Telegram-группу.
    chat_id != 0 — бот уже в группе, значение получено из my_chat_member.
    """
    ok: bool
    habit_id: str
    chat_id: int
    bound: bool
    code: str | None = None


class AdminHabitPreviewChatRequest(BaseModel):
    """POST /admin/v1/habits/preview_chat_by_invite — резолв invite-ссылки.

    Используется в форме создания клуба ДО сохранения в БД.
    Админ уже добавил @join_prideclub_bot в Telegram-группу и вставил
    инвайт-ссылку — мы пробрасываем её в Telegram Bot API и получаем
    chat_id, title, type.
    """

    invite_link: str = Field(min_length=1, max_length=512)


class AdminHabitPreviewChatResponse(BaseModel):
    """Результат резолва инвайт-ссылки.

    ok=True, chat_id>0  — ссылка валидна и бот имеет доступ к чату.
    ok=False            — ошибка (см. code и message).
    """

    ok: bool
    chat_id: int | None = None
    title: str | None = None
    type: str | None = None
    invite_link: str
    already_used_by_habit_id: str | None = None
    code: str | None = None
    message: str | None = None


class AdminHabitAvailableChat(BaseModel):
    """Один чат, куда бот @join_prideclub_bot добавлен (по данным my_chat_member)."""

    chat_id: int
    chat_title: str | None = None
    chat_type: str | None = None
    invite_link: str | None = None
    added_at: float  # unix timestamp
    bound_to_habit_id: str | None = None
    bound_to_habit_title: str | None = None


class AdminHabitRefreshChatResponse(BaseModel):
    """Результат ручного обновления чата из Telegram."""

    ok: bool
    chat_id: int
    chat_title: str | None = None
    chat_type: str | None = None
    invite_link: str | None = None
    code: str | None = None
    message: str | None = None


class AdminHabitAvailableChatsResponse(BaseModel):
    items: list[AdminHabitAvailableChat]


# ---------------------------------------------------------------------------
# Pravki-subscribe-and-join.md §Z-12.1: POST /api/v1/payments/subscribe
# ---------------------------------------------------------------------------


class SubscribeRequest(BaseModel):
    """Request для объединённой оплаты «подписка + депозит + создание ACTIVE membership».

    Pravki-subscribe-and-join.md §Z-12.1:
    - `subscription_accepted` — server-side gate (см. §Z-13.1 матрица). Допустимо
      True и False если у юзера есть активная подписка (existing.subscription_until >= today).
      Если подписки нет — должно быть True, иначе 422.
    - `idempotency_key` — client-generated UUID4 (uuid4 из фронта), позволяет safe-retry
      без двойного списания. Префикс «subscribe:» добавляется на backend для отделения
      от idempotency_key обычных topup'ов.
    """

    habit_id: str
    deposit_amount_kopecks: int = Field(gt=0, le=10_000_000)
    subscription_accepted: bool
    idempotency_key: str = Field(min_length=8, max_length=128)


class SubscribeResponse(BaseModel):
    """Response для объединённой оплаты.

    Pravki-subscribe-and-join.md §Z-13.3: `charged_subscription` показывает,
    списали ли price_month (True) или только депозит (False — была активная
    подписка, не трогаем). UI использует это для adaptive alert после успеха.
    """

    ok: bool = True
    transaction_id: str
    membership_id: str
    new_deposit_balance: int
    subscription_until: date
    total_charged_kopecks: int
    charged_subscription: bool