from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import PenaltyReason
from app.db.session import Base

if TYPE_CHECKING:
    from app.models.membership import Membership


class Penalty(Base):
    __tablename__ = "penalties"
    __table_args__ = (
        UniqueConstraint("membership_id", "date", "reason", name="uq_penalty_per_day_reason"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    membership_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False
    )
    catcher_membership_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("memberships.id", ondelete="SET NULL"), nullable=True
    )

    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    fund_share: Mapped[int] = mapped_column(Integer, nullable=False)
    catcher_bonus_points: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    reason: Mapped[PenaltyReason] = mapped_column(
        String(64), nullable=False, server_default=PenaltyReason.CAUGHT.value
    )
    date: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=func.current_date()
    )
    bonus_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    membership: Mapped[Membership] = relationship(
        back_populates="penalties_received",
        foreign_keys="Penalty.membership_id",
    )