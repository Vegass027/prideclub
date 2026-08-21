from __future__ import annotations

from app.models.auxiliary import (
    DailyStreakSnapshot,
    OfferVersion,
    PricingRule,
    SeasonPrizeRule,
    SuspiciousPair,
    UserConsent,
)
from app.models.checkin import Checkin
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.penalty import Penalty
from app.models.season import Season, SeasonStats
from app.models.stat_definition import StatDefinition
from app.models.transaction import Transaction
from app.models.user import User
from app.models.user_stats import UserStats
from app.models.user_status import UserStatus

__all__ = [
    "User",
    "Habit",
    "Membership",
    "Checkin",
    "Penalty",
    "Transaction",
    "Season",
    "SeasonStats",
    "DailyStreakSnapshot",
    "SuspiciousPair",
    "SeasonPrizeRule",
    "PricingRule",
    "OfferVersion",
    "UserConsent",
    # NEW (Phase 3.1, migration 019 — global stats + справочник)
    "StatDefinition",
    "UserStatus",
    "UserStats",
] 