from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import SeasonStatus
from app.db.session import Base


if TYPE_CHECKING:
    from app.models.habit import Habit


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    habit_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("habits.id", ondelete="CASCADE"), nullable=False
    )
    starts_at: Mapped[date] = mapped_column(Date, nullable=False)
    ends_at: Mapped[date] = mapped_column(Date, nullable=False)
    prize_pool: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    prize_rules_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[SeasonStatus] = mapped_column(
        Enum(SeasonStatus, name="season_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=SeasonStatus.ACTIVE,
        server_default=SeasonStatus.ACTIVE.value,
    )

    habit: Mapped["Habit"] = relationship()
    stats: Mapped[list["SeasonStats"]] = relationship(back_populates="season")


class SeasonStats(Base):
    __tablename__ = "season_stats"

    season_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("seasons.id", ondelete="CASCADE"),
        primary_key=True,
    )
    membership_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("memberships.id", ondelete="CASCADE"),
        primary_key=True,
    )
    streak_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_penalties_caught: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_penalties_received: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    season: Mapped["Season"] = relationship(back_populates="stats")