from __future__ import annotations

from app.models import (
    Checkin,
    DailyStreakSnapshot,
    Habit,
    Membership,
    Penalty,
    Season,
    SeasonStats,
    SuspiciousPair,
    Transaction,
    User,
)
from app.models.auxiliary import (
    OfferVersion,
    PricingRule,
    SeasonPrizeRule,
    UserConsent,
)


def test_models_importable() -> None:
    assert User.__tablename__ == "users"
    assert Habit.__tablename__ == "habits"
    assert Membership.__tablename__ == "memberships"
    assert Checkin.__tablename__ == "checkins"
    assert Penalty.__tablename__ == "penalties"
    assert Transaction.__tablename__ == "transactions"
    assert Season.__tablename__ == "seasons"
    assert SeasonStats.__tablename__ == "season_stats"


def test_auxiliary_models_importable() -> None:
    assert DailyStreakSnapshot.__tablename__ == "daily_streak_snapshots"
    assert SuspiciousPair.__tablename__ == "suspicious_pairs"
    assert SeasonPrizeRule.__tablename__ == "season_prize_rules"
    assert PricingRule.__tablename__ == "pricing_rules"
    assert OfferVersion.__tablename__ == "offer_versions"
    assert UserConsent.__tablename__ == "user_consents"