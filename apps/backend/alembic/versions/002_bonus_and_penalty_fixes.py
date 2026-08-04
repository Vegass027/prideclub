"""bonus and penalty fixes + auxiliary tables

Revision ID: 002_bonus_and_penalty_fixes
Revises: 001_initial_schema
Create Date: 2026-01-01 00:00:02.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "002_bonus_and_penalty_fixes"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("penalties", sa.Column("bonus_applied", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("penalties", sa.Column("reason", sa.String(length=64), nullable=False, server_default="caught"))
    op.add_column("penalties", sa.Column("date", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE")))
    op.create_unique_constraint(
        "uq_penalty_per_day_reason", "penalties", ["membership_id", "date", "reason"]
    )

    op.add_column("users", sa.Column("bonus_points", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("bonus_points_updated_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("memberships", sa.Column("bonus_points", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("memberships", sa.Column("auto_renew_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    op.add_column("seasons", sa.Column("prize_rules_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    op.create_table(
        "daily_streak_snapshots",
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("streak_days", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("membership_id", "date", name="pk_daily_streak_snapshots"),
    )

    op.create_table(
        "suspicious_pairs",
        sa.Column("membership_id_a", postgresql.UUID(as_uuid=True), sa.ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False),
        sa.Column("membership_id_b", postgresql.UUID(as_uuid=True), sa.ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("detected_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="flagged"),
        sa.PrimaryKeyConstraint("membership_id_a", "membership_id_b", name="pk_suspicious_pairs"),
    )

    op.create_table(
        "bonus_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("reward_type", sa.String(length=64), nullable=False),
        sa.Column("reward_value", sa.Integer(), nullable=False),
    )

    op.create_table(
        "season_prize_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("habit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("habits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rank_from", sa.Integer(), nullable=False),
        sa.Column("rank_to", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("percentage", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.UniqueConstraint("habit_id", "metric", "rank_from", "rank_to", name="uq_season_prize_rules"),
    )

    op.create_table(
        "pricing_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("habit_rank", sa.Integer(), nullable=False),
        sa.Column("price_month", sa.Integer(), nullable=False),
        sa.Column("active_from", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("active_to", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "offer_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("effective_from", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("document_url", sa.Text(), nullable=False),
    )

    op.create_table(
        "user_consents",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("offer_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("offer_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("accepted_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "offer_version_id", name="pk_user_consents"),
    )


def downgrade() -> None:
    op.drop_table("user_consents")
    op.drop_table("offer_versions")
    op.drop_table("pricing_rules")
    op.drop_table("season_prize_rules")
    op.drop_table("bonus_rules")
    op.drop_table("suspicious_pairs")
    op.drop_table("daily_streak_snapshots")

    op.drop_column("seasons", "prize_rules_snapshot")

    op.drop_column("memberships", "auto_renew_enabled")
    op.drop_column("memberships", "bonus_points")
    op.drop_column("users", "bonus_points_updated_at")
    op.drop_column("users", "bonus_points")

    op.drop_constraint("uq_penalty_per_day_reason", "penalties", type_="unique")
    op.drop_column("penalties", "date")
    op.drop_column("penalties", "reason")
    op.drop_column("penalties", "bonus_applied")