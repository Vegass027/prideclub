from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import CheckinStatus
from app.db.session import Base

if TYPE_CHECKING:
    from app.models.membership import Membership


class Checkin(Base):
    __tablename__ = "checkins"
    __table_args__ = (
        UniqueConstraint("membership_id", "date", name="uq_checkins_membership_date"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    membership_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[CheckinStatus] = mapped_column(
        Enum(CheckinStatus, name="checkin_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    proof_message_id: Mapped[int | None] = mapped_column(nullable=True)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    membership: Mapped[Membership] = relationship(back_populates="checkins")