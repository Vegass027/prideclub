"""initial schema

Revision ID: 001_initial_schema
Revises: 000_extensions
Create Date: 2026-01-01 00:00:01.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "001_initial_schema"
down_revision: Union[str, None] = "000_extensions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/Moscow"),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("accepted_offer_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    proof_type = postgresql.ENUM("video_note", "photo", "text", name="proof_type", create_type=True)
    proof_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "habits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("checkin_window_start", sa.Time(), nullable=False),
        sa.Column("checkin_window_end", sa.Time(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/Moscow"),
        sa.Column("penalty_amount", sa.Integer(), nullable=False),
        sa.Column("price_month", sa.Integer(), nullable=False),
        sa.Column("proof_type", postgresql.ENUM(name="proof_type", create_type=False), nullable=False),
        sa.Column("prize_pool", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_habits_is_active", "habits", ["is_active"])

    membership_status = postgresql.ENUM("active", "paused", "left", name="membership_status", create_type=True)
    membership_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("habit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("habits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", postgresql.ENUM(name="membership_status", create_type=False), nullable=False, server_default="active"),
        sa.Column("deposit_balance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("subscription_until", sa.Date(), nullable=True),
        sa.Column("joined_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "habit_id", name="uq_memberships_user_habit"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_index("ix_memberships_habit_id", "memberships", ["habit_id"])
    op.create_index("ix_memberships_status", "memberships", ["status"])

    checkin_status = postgresql.ENUM("done", "missed", name="checkin_status", create_type=True)
    checkin_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "checkins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("status", postgresql.ENUM(name="checkin_status", create_type=False), nullable=False),
        sa.Column("proof_message_id", sa.BigInteger(), nullable=True),
        sa.Column("verified_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("membership_id", "date", name="uq_checkins_membership_date"),
    )
    op.create_index("ix_checkins_date", "checkins", ["date"])

    op.create_table(
        "penalties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False),
        sa.Column("catcher_membership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memberships.id", ondelete="SET NULL"), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("fund_share", sa.Integer(), nullable=False),
        sa.Column("catcher_bonus_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_penalties_membership_id", "penalties", ["membership_id"])
    op.create_index("ix_penalties_catcher_membership_id", "penalties", ["catcher_membership_id"])

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("balance_after", sa.BigInteger(), nullable=True),
        sa.Column("related_penalty_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("penalties.id", ondelete="SET NULL"), nullable=True),
        sa.Column("related_membership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memberships.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("idempotency_key", name="uq_transactions_idempotency_key"),
    )
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])
    op.create_index("ix_transactions_type", "transactions", ["type"])

    season_status = postgresql.ENUM("active", "closed", "paid_out", name="season_status", create_type=True)
    season_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "seasons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("habit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("habits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("starts_at", sa.Date(), nullable=False),
        sa.Column("ends_at", sa.Date(), nullable=False),
        sa.Column("prize_pool", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", postgresql.ENUM(name="season_status", create_type=False), nullable=False, server_default="active"),
    )
    op.create_index("ix_seasons_habit_id", "seasons", ["habit_id"])

    op.create_table(
        "season_stats",
        sa.Column("season_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("seasons.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memberships.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("streak_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_penalties_caught", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_penalties_received", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("season_stats")
    op.drop_index("ix_seasons_habit_id", table_name="seasons")
    op.drop_table("seasons")
    op.execute("DROP TYPE IF EXISTS season_status")

    op.drop_index("ix_transactions_type", table_name="transactions")
    op.drop_index("ix_transactions_user_id", table_name="transactions")
    op.drop_table("transactions")
    op.drop_index("ix_penalties_catcher_membership_id", table_name="penalties")
    op.drop_index("ix_penalties_membership_id", table_name="penalties")
    op.drop_table("penalties")

    op.drop_index("ix_checkins_date", table_name="checkins")
    op.drop_table("checkins")
    op.execute("DROP TYPE IF EXISTS checkin_status")

    op.drop_index("ix_memberships_status", table_name="memberships")
    op.drop_index("ix_memberships_habit_id", table_name="memberships")
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_table("memberships")
    op.execute("DROP TYPE IF EXISTS membership_status")

    op.drop_index("ix_habits_is_active", table_name="habits")
    op.drop_table("habits")
    op.execute("DROP TYPE IF EXISTS proof_type")

    op.drop_table("users")