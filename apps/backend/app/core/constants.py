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
    # Pravki-no-deposit-waived-marker (разведка 2026-08-16, коммит A 2026-08-17):
    # маркерная запись для случая, когда юзер был PAUSED на момент закрытия окна
    # чек-ина (deposit < penalty_amount). Списание невозможно → записываем
    # amount=0 маркер, чтобы день был помечен в БД как «уже разрешённый».
    # Гарантирует, что apply_catch после topup юзера отвергается для этого дня.
    # Имя WAIVED_UNABLE_TO_PAY (а не NO_DEPOSIT) потому что покрывает и deposit=0,
    # и частичный deposit < penalty. Миграция не нужна (VARCHAR, 0 строк в проде).
    WAIVED_UNABLE_TO_PAY = "waived_unable_to_pay"


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
    # Pravki-catcher-deposit (Phase 1 Task 1.2, 2026-08-21): новая механика штрафов
    # — штраф делится на 2 части: часть в призовой фонд клуба + часть на депозит ловца.
    # Эта транзакция пишется при apply_catch для зачисления доли ловцу на User.deposit_balance.
    # Сумма = +catcher_amount (копейки, int). Связана с Penalty через related_penalty_id.
    # Удалить в Phase 8 НЕЛЬЗЯ — это часть новой механики, не бонусная миграция.
    CATCHER_DEPOSIT = "catcher_deposit"


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
        6. SUBSCRIPTION_EXPIRED   (продли подписку — НЕ раньше PAUSED/LEFT,
              потому что "пополни депозит" не лечит истёкшую подписку,
              а "продли подписку" лечит и подписку, и (через recompute
              пауз) потенциально воскрешает membership из PAUSED).
        7. MEMBERSHIP_PAUSED      (пополни депозит)
        8. MEMBERSHIP_LEFT        (вступи в клуб заново)

      IV. "Wrong time/topic" (можно исправить перепосылкой/ожиданием):
        9. WINDOW_CLOSED          (окно закрыто — жди завтра)
       10. WRONG_TOPIC            (не тот топик — пошли в правильный)
       11. FORWARDED              (пересланное — запиши своё)

      V. Proof validation (дешёвая, на техническом уровне):
       12. WRONG_TYPE / TOO_SHORT / STALE_MESSAGE / EMPTY_TEXT

    Позиции 3-5 — это СУЩЕСТВУЮЩИЙ порядок бота (см. checkin.py), мы
    НЕ его меняем. WINDOW_CLOSED/WRONG_TOPIC/FORWARDED (категория IV)
    идут ПОСЛЕ них, потому что для пойманного/отметившегося/нового
    участника time/location — вторичная информация по сравнению с
    "что с ним произошло".

    Pravki §Z-22 (prefilter holes, 5-round fix).
    Pravki-subscription-2026-08-17 §Z-22: SUBSCRIPTION_EXPIRED вставлен
    в позицию #6 (выше PAUSED/LEFT, см. §III в этом docstring).
    """

    # I. Fundamental errors
    HABIT_NOT_FOUND = "habit_not_found"
    MEMBERSHIP_NOT_FOUND = "membership_not_found"
    # II. Too late (state-of-day, по decreasing specificity)
    ALREADY_CAUGHT = "caught_today"
    ALREADY_CHECKED_IN = "checkin_already_exists"
    JOINED_LATE = "joined_late"
    # III. Wrong setup (actionable — renew subscription / top up / rejoin)
    # Pravki-subscription-2026-08-17: SUBSCRIPTION_EXPIRED добавлен ПЕРВЫМ
    # в категории III — вытесняет MEMBERSHIP_PAUSED/LEFT в позиции #7/#8.
    SUBSCRIPTION_EXPIRED = "subscription_expired"
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
    # DEPRECATED 2026-08-21 (Phase 1 Task 1.3 — Pravki-catcher-deposit):
    # больше не используется — деньги делятся между ловцом и фондом через
    # Habit.catcher_amount_kopecks. Оставлено для совместимости старого кода.
    FUND_SHARE = 1.0

    # Техническая комиссия при возврате депозита.
    DEPOSIT_WITHDRAW_FEE_PERCENT = 5

    # Антифрод: rate limit на "спалить".
    RATE_LIMIT_CATCH = "10/10s"

    # Окно спаливания = окно чек-ина + N часов.
    # DEPRECATED 2026-08-18: старая формула "окно чек-ина + 1 час" не покрывает
    # случай длинных клубных окон (например 09:00-21:00 → catch только до 22:00).
    # Новая формула использует CATCH_WINDOW_BUFFER_HOURS: catch window
    # заканчивается за N часов ДО начала СЛЕДУЮЩЕГО окна чек-ина.
    # См. Pravki-manual-catch-2026-08-18 §Шаг 1.
    CATCH_WINDOW_EXTRA_HOURS = 1

    # Буфер между концом catch window и началом следующего окна чек-ина.
    # Catch window заканчивается за CATCH_WINDOW_BUFFER_HOURS часов до
    # `next_checkin_window_start` в TZ клуба.
    # Пример: окно 09:00-21:00 Europe/Moscow → catch до next day 07:00 MSK (10ч).
    # Пример: окно 22:00-06:00 Europe/Moscow → catch до next day 20:00 MSK (14ч).
    # Pravki-manual-catch-2026-08-18.
    CATCH_WINDOW_BUFFER_HOURS = 2

    # Антифрод-эвристика: если один catcher ловит один и тот же violator
    # N+ раз за сезон И violator ни разу не поймал catcher'а — флаг.
    SUSPICIOUS_ASYMMETRY_THRESHOLD = 3


class HttpRateLimitConfig:
    """Общий HTTP rate limit (на пользователя / сервисный caller)."""

    RATE_LIMIT_API_V1 = "60/60s"  # 60 запросов в минуту на /api/v1/*
    RATE_LIMIT_INTERNAL = "120/60s"  # 120 на /internal/* (бот шлёт чаще)


class CharacterConfig:
    """Константы геймификации (Phase 3 v2, миграция 019).

    Используется CharacterService (Task 3.3) и worker'ом freeze-cron
    (Task 3.5).

    ⚠️ LEADERBOARD_TOP_LIMIT перенесён в Task 3.6 (там, где будет
    endpoint и SQL с JOIN). Не держать в этом классе преждевременно
    — иначе придётся уточнять контекст использования.
    """

    DEFAULT_STAT_GAIN_PER_CHECKIN = 2
    DEFAULT_STAT_LOSS_PER_MISS = 1

    FREEZE_AFTER_DAYS_INACTIVE = 30
    DEFAULT_FROZEN_REASON = (
        "Характеристика заморожена: нет чек-инов более 30 дней. "
        "Сделай чек-ин, чтобы продолжить рост."
    )

    # Display filter (per Dmitry 21.08.2026).
    # Stat с value >= MIN_STAT_VALUE_TO_SHOW (= 1) ИЛИ is_frozen →
    # показывается в /character/me stats[].
    # Frozen stats при value=0 — остаются (UI рисует ❄ + reason).
    MIN_STAT_VALUE_TO_SHOW = 1

    # Cron (Task 3.5).
    FREEZE_CRON_HOUR_UTC = 4
    FREEZE_CRON_BATCH = 1000
