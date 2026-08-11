from __future__ import annotations

from enum import StrEnum


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    LEFT = "left"


class CheckinStatus(StrEnum):
    DONE = "done"
    MISSED = "missed"
    # Pravki-bug-fixes §Z-19 (joiner-late protection):
    # юзер вступил в клуб сегодня ПОСЛЕ checkin_window_end.
    # Защита от ловли в день вступления: can_catch=False в /members
    # (status != 'missed').
    JOINED_LATE = "joined_late"
    # Pravki-bug-fixes §Z-21 (caught status badge):
    # юзер пойман в этот день. PenaltyService.apply_catch пишет
    # Checkin(status='caught') при успешной поимке. can_catch=False.
    CAUGHT = "caught"


class PenaltyReason(StrEnum):
    CAUGHT = "caught"
    WINDOW_CLOSED_NO_CATCH = "window_closed_no_catch"


class ProofType(StrEnum):
    VIDEO_NOTE = "video_note"
    PHOTO = "photo"
    TEXT = "text"


# Список всех значений ProofType для валидации входящих payload'ов
# и для админских чекбоксов.
PROOF_TYPE_VALUES: tuple[str, ...] = tuple(pt.value for pt in ProofType)


class TransactionType(StrEnum):
    SUBSCRIPTION = "subscription"
    DEPOSIT_TOPUP = "deposit_topup"
    DEPOSIT_WITHDRAW = "deposit_withdraw"
    PENALTY = "penalty"
    PRIZE = "prize"
    BONUS_CATCH = "bonus_catch"
    BONUS_SUBSCRIPTION = "bonus_subscription"
    BONUS_POINTS = "bonus_points"


class SeasonStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    PAID_OUT = "paid_out"


class SuspiciousPairStatus(StrEnum):
    FLAGGED = "flagged"
    CLEARED = "cleared"
    BANNED = "banned"


class ServiceCaller(StrEnum):
    BOT = "bot"
    WORKER = "worker"


class CheckinRejectCode(StrEnum):
    """Единый source of truth для reject-кодов чек-ина.

    Используется:
    - exceptions.py: class.code = CheckinRejectCode.X.value
    - proof_validator.py: raise ProofValidationError(CheckinRejectCode.X.value)
    - bot: для type-safety в _text_for_code и для шаблонов
    - frontend: зеркальный enum в apps/frontend/src/shared/types/checkinReject.ts

    Канонический порядок проверок (bot prefilter + backend defense-in-depth
    в enqueue_checkin) — ОБЯЗАН быть идентичным с обеих сторон. Иначе при
    одновременном нарушении нескольких условий пользователь увидит разные
    тексты в чате и в мини-аппе для одной ситуации (дрейф, который мы
    выкорчёвываем этой серией). Правило для шагов 1-4:

      Structural (где/когда — дешёвые по вычислению, но "фундаментальные"):
        1. HABIT_NOT_FOUND        (habit не существует)
        2. MEMBERSHIP_NOT_FOUND   (membership отсутствует)
        3. MEMBERSHIP_PAUSED      (status=paused — deposit < penalty)
        4. MEMBERSHIP_LEFT        (status=left — юзер вышел)
        5. WINDOW_CLOSED          (вне окна чек-ина в TZ клуба)
        6. WRONG_TOPIC            (topic_thread_id не совпадает)
        7. FORWARDED              (forward_date != None)

      State-of-day (что уже записано — дороже по логике, зависит от БД):
        8. ALREADY_CAUGHT         (есть Penalty за club_date)
            — раньше ALREADY_CHECKED_IN, потому что для status='caught'
              оба флага True; если already_checked_in checked first —
              бот ответит "уже отметился" вместо "поймали".
        9. ALREADY_CHECKED_IN     (есть Checkin за club_date)
       10. JOINED_LATE            (joined_at сегодня после закрытия окна)
            — последним среди state-of-day, потому что это structural
              новичок (его вообще не должно быть в потоке чек-ина, но
              JoinButton / race может сюда привести).

      Proof validation (дешёвая, после всех structural + state-of-day):
       11. WRONG_TYPE / TOO_SHORT / STALE_MESSAGE / EMPTY_TEXT  (proof)

    Позиции 8-10 — это СУЩЕСТВУЮЩИЙ порядок бота (см. checkin.py:203-236),
    мы НЕ его меняем, а лишь добавляем позиции 2-7. Менять порядок
    было бы регрессом.

    Pravki §Z-22 (prefilter holes, 5-round fix).
    """

    # 1. Structural — habit / membership
    HABIT_NOT_FOUND = "habit_not_found"
    MEMBERSHIP_NOT_FOUND = "membership_not_found"
    # 2. Membership status (legacy "membership_not_active" остаётся для catch-flow)
    MEMBERSHIP_NOT_ACTIVE = "membership_not_active"
    MEMBERSHIP_PAUSED = "membership_paused"
    MEMBERSHIP_LEFT = "membership_left"
    # 3. Time / location
    WINDOW_CLOSED = "checkin_window_closed"
    WRONG_TOPIC = "not_checkin_topic"
    FORWARDED = "forwarded"
    # 4. State already applied (CATCHED first — semantic priority)
    ALREADY_CAUGHT = "caught_today"
    ALREADY_CHECKED_IN = "checkin_already_exists"
    JOINED_LATE = "joined_late"
    # 5. Proof validation
    WRONG_TYPE = "wrong_type"
    TOO_SHORT = "too_short"
    STALE_MESSAGE = "stale_message"
    EMPTY_TEXT = "empty"


class PenaltyConfig:
    """Константы механики штрафов."""

    # Штраф всегда 100% уходит в призовой фонд клуба.
    FUND_SHARE = 1.0
    CATCHER_BONUS_POINTS = 1

    # Техническая комиссия при возврате депозита.
    DEPOSIT_WITHDRAW_FEE_PERCENT = 5

    # Антифрод: rate limit на "спалить".
    RATE_LIMIT_CATCH = "10/10s"

    # Окно спаливания = окно чек-ина + N часов.
    CATCH_WINDOW_EXTRA_HOURS = 1

    # Сгорание бонусных поинтов.
    BONUS_POINTS_EXPIRY_DAYS = 90
    BONUS_POINTS_EXPIRY_NOTIFY_DAYS = 7

    # Антифрод-эвристика: если один catcher ловит один и тот же violator
    # N+ раз за сезон И violator ни разу не поймал catcher'а — флаг.
    SUSPICIOUS_ASYMMETRY_THRESHOLD = 3


class HttpRateLimitConfig:
    """Общий HTTP rate limit (на пользователя / сервисный caller)."""

    RATE_LIMIT_API_V1 = "60/60s"   # 60 запросов в минуту на /api/v1/*
    RATE_LIMIT_INTERNAL = "120/60s"  # 120 на /internal/* (бот шлёт чаще)