from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field


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


class MarketplaceResponse(BaseModel):
    items: list[HabitOut]


class MembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: int
    habit_id: str
    status: str
    deposit_balance: int
    subscription_until: date | None
    auto_renew_enabled: bool
    joined_at: datetime


class CheckinStatusOut(BaseModel):
    status: str  # done | missed | pending | not_started
    streak_days: int
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
    code: str  # ok | checkin_already_exists | checkin_window_closed | membership_not_active | invalid_proof | habit_not_found


class AdminHabitCreateRequest(BaseModel):
    """POST /admin/v1/habits — создание клуба (TZ §3.6.4).

    `is_active` намеренно отсутствует: при создании всегда False.

    Topic-scoped (migration 010):
    - `checkin_topic_link` и `notifications_topic_link` обязательны — клуб
      нельзя создать без привязки к топикам форума. Формат
      `https://t.me/c/<chat_id>/<thread_id>`.
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
    proof_type: str = Field(pattern="^(video_note|photo|text)$")
    price_month: int = Field(gt=0)
    penalty_amount: int = Field(gt=0)
    stat_gain_per_checkin: int = Field(default=2, gt=0)
    stat_loss_per_miss: int = Field(default=1, gt=0)
    member_limit: int | None = Field(default=None, gt=0)
    curator_id: int | None = None
    checkin_topic_link: str = Field(min_length=1, max_length=512)
    notifications_topic_link: str = Field(min_length=1, max_length=512)
    chat_topic_link: str | None = Field(default=None, min_length=1, max_length=512)


class AdminHabitUpdateRequest(BaseModel):
    """PATCH /admin/v1/habits/{id} — частичное обновление.

    Любые поля опциональны. Финансовые поля замораживаются после первого вступления
    (TZ §3.6.7) — сервис бросит HabitValidationError если в клубе уже есть участники.
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
    price_month: int | None = Field(default=None, gt=0)
    penalty_amount: int | None = Field(default=None, gt=0)
    stat_gain_per_checkin: int | None = Field(default=None, gt=0)
    stat_loss_per_miss: int | None = Field(default=None, gt=0)
    member_limit: int | None = Field(default=None, gt=0)
    curator_id: int | None = None
    checkin_topic_link: str | None = Field(default=None, min_length=1, max_length=512)
    notifications_topic_link: str | None = Field(default=None, min_length=1, max_length=512)
    chat_topic_link: str | None = Field(default=None, min_length=1, max_length=512)


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