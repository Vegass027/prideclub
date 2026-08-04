from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

if TYPE_CHECKING:
    pass


from app.db.session import Base


class DailyStreakSnapshot(Base):
    __tablename__ = "daily_streak_snapshots"

    membership_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("memberships.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    streak_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class SuspiciousPair(Base):
    __tablename__ = "suspicious_pairs"

    membership_id_a: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("memberships.id", ondelete="CASCADE"), primary_key=True
    )
    membership_id_b: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("memberships.id", ondelete="CASCADE"), primary_key=True
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="flagged")


class BonusRule(Base):
    __tablename__ = "bonus_rules"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reward_value: Mapped[int] = mapped_column(Integer, nullable=False)


class SeasonPrizeRule(Base):
    __tablename__ = "season_prize_rules"
    __table_args__ = (
        UniqueConstraint(
            "habit_id", "metric", "rank_from", "rank_to", name="uq_season_prize_rules"
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    habit_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("habits.id", ondelete="CASCADE"), nullable=False
    )
    rank_from: Mapped[int] = mapped_column(Integer, nullable=False)
    rank_to: Mapped[int] = mapped_column(Integer, nullable=False)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    percentage: Mapped[float] = mapped_column(Numeric(precision=5, scale=2), nullable=False)


class PricingRule(Base):
    __tablename__ = "pricing_rules"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    habit_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    price_month: Mapped[int] = mapped_column(Integer, nullable=False)
    active_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    active_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OfferVersion(Base):
    __tablename__ = "offer_versions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    document_url: Mapped[str] = mapped_column(String, nullable=False)


class UserConsent(Base):
    __tablename__ = "user_consents"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    offer_version_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("offer_versions.id", ondelete="CASCADE"), primary_key=True
    )
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)