from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import MembershipStatus
from app.db.session import Base

if TYPE_CHECKING:
    from app.models.checkin import Checkin
    from app.models.habit import Habit
    from app.models.penalty import Penalty
    from app.models.user import User


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "habit_id", name="uq_memberships_user_habit"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    habit_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("habits.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[MembershipStatus] = mapped_column(
        Enum(
            MembershipStatus,
            name="membership_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=MembershipStatus.ACTIVE,
        server_default=MembershipStatus.ACTIVE.value,
    )

    auto_renew_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    subscription_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="memberships")
    habit: Mapped[Habit] = relationship(back_populates="memberships")
    checkins: Mapped[list[Checkin]] = relationship(back_populates="membership")
    penalties_received: Mapped[list[Penalty]] = relationship(
        back_populates="membership",
        foreign_keys="Penalty.membership_id",
    )