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
    # Pravki-no-deposit-waived-marker (разведка 2026-08-16):
    # маркерная запись для случая, когда apply_window_expired НЕ смог
    # списать штраф из-за пустого депозита (amount=0). Гарантирует, что
    # день помечен как «уже разрешённый» в БД → apply_catch отвергается
    # даже после topup юзером.
    WAIVED_NO_DEPOSIT = "waived_no_deposit"


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
    выкорчёвываем этой серией).

    Принцип приоритета: для каждой пары кодов побеждает тот, чей текст
    даёт юзеру БОЛЕЕ СПЕЦИФИЧНУЮ и БОЛЕЕ ПОЛЕЗНУЮ информацию. Не
    абстрактная категория ("structural первее state-of-day"), а
    прагматика copy.

    Ревизия Шага 1.1 (после первого прогона): категоризация
    "structural vs state-of-day" была ОШИБОЧНОЙ. Конкретный кейс из
    Item 4 / §Z-21: поймать можно ТОЛЬКО того, кто не отметился в окно,
    значит caught_today=True ВСЕГДА сопровождается is_within_checkin_window=False.
    Если первый попадёт WINDOW_CLOSED (старая ошибка структурной
    категоризации), юзер увидит бесполезное "окно закрыто" вместо
    конкретного "поймали, штраф списан". Реальный сценарий — 95%
    пойманных участников. Фикс: state-of-day идёт РАНЬШЕ time/location.

    Категории (по приоритету сообщения, не по типу проверки):

      I. Fundamental errors (юзер в неправильном контексте):
        1. HABIT_NOT_FOUND        (не тот чат)
        2. MEMBERSHIP_NOT_FOUND   (не участник)

      II. "Too late" (принятие решения финально — деньги/штраф):
        3. ALREADY_CAUGHT         (штраф списан — самое специфичное)
            — важнее ALREADY_CHECKED_IN (для status='caught' оба
              флага True; если already_checked_in first — бот
              скажет "уже отметился" вместо "поймали").
        4. ALREADY_CHECKED_IN     (уже отметился — нельзя второй раз)
        5. JOINED_LATE            (новичок, у которого день уже
              фактически провалился — первый чек-ин будет завтра)

      III. Wrong setup (нужно действие чтобы получить доступ):
        6. MEMBERSHIP_PAUSED      (пополни депозит)
        7. MEMBERSHIP_LEFT        (вступи в клуб заново)

      IV. "Wrong time/topic" (можно исправить перепосылкой/ожиданием):
        8. WINDOW_CLOSED          (окно закрыто — жди завтра)
        9. WRONG_TOPIC            (не тот топик — пошли в правильный)
       10. FORWARDED              (пересланное — запиши своё)

      V. Proof validation (дешёвая, на техническом уровне):
       11. WRONG_TYPE / TOO_SHORT / STALE_MESSAGE / EMPTY_TEXT

    Позиции 3-5 — это СУЩЕСТВУЮЩИЙ порядок бота (см. checkin.py), мы
    НЕ его меняем. WINDOW_CLOSED/WRONG_TOPIC/FORWARDED (категория IV)
    идут ПОСЛЕ них, потому что для пойманного/отметившегося/нового
    участника time/location — вторичная информация по сравнению с
    "что с ним произошло".

    Pravki §Z-22 (prefilter holes, 5-round fix).
    """

    # I. Fundamental errors
    HABIT_NOT_FOUND = "habit_not_found"
    MEMBERSHIP_NOT_FOUND = "membership_not_found"
    # II. Too late (state-of-day, по decreasing specificity)
    ALREADY_CAUGHT = "caught_today"
    ALREADY_CHECKED_IN = "checkin_already_exists"
    JOINED_LATE = "joined_late"
    # III. Wrong setup (actionable — top up / rejoin)
    MEMBERSHIP_NOT_ACTIVE = "membership_not_active"  # legacy, остаётся для catch-flow
    MEMBERSHIP_PAUSED = "membership_paused"
    MEMBERSHIP_LEFT = "membership_left"
    # IV. Wrong time/topic
    WINDOW_CLOSED = "checkin_window_closed"
    WRONG_TOPIC = "not_checkin_topic"
    FORWARDED = "forwarded"
    # V. Proof validation
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