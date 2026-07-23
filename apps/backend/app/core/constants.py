from __future__ import annotations

from enum import Enum


class MembershipStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    LEFT = "left"


class CheckinStatus(str, Enum):
    DONE = "done"
    MISSED = "missed"


class PenaltyReason(str, Enum):
    CAUGHT = "caught"
    WINDOW_CLOSED_NO_CATCH = "window_closed_no_catch"


class ProofType(str, Enum):
    VIDEO_NOTE = "video_note"
    PHOTO = "photo"
    TEXT = "text"


# Список всех значений ProofType для валидации входящих payload'ов
# и для админских чекбоксов.
PROOF_TYPE_VALUES: tuple[str, ...] = tuple(pt.value for pt in ProofType)


class TransactionType(str, Enum):
    SUBSCRIPTION = "subscription"
    DEPOSIT_TOPUP = "deposit_topup"
    DEPOSIT_WITHDRAW = "deposit_withdraw"
    PENALTY = "penalty"
    PRIZE = "prize"
    BONUS_CATCH = "bonus_catch"
    BONUS_SUBSCRIPTION = "bonus_subscription"
    BONUS_POINTS = "bonus_points"


class SeasonStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    PAID_OUT = "paid_out"


class SuspiciousPairStatus(str, Enum):
    FLAGGED = "flagged"
    CLEARED = "cleared"
    BANNED = "banned"


class ServiceCaller(str, Enum):
    BOT = "bot"
    WORKER = "worker"


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