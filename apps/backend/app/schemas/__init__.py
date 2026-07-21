from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class HabitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str | None = None
    chat_id: int
    checkin_window_start: str
    checkin_window_end: str
    timezone: str
    penalty_amount: int
    price_month: int
    proof_type: str
    prize_pool: int
    members_count: int = 0
    is_active: bool


class MarketplaceResponse(BaseModel):
    items: list[HabitOut]


class MembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: int
    habit_id: str
    status: str
    deposit_balance: int
    subscription_until: date | None
    auto_renew_enabled: bool
    joined_at: datetime


class CheckinStatusOut(BaseModel):
    status: str  # done | missed | pending | not_started
    streak_days: int
    deadline_at: datetime | None


class TodayResponse(BaseModel):
    habit: HabitOut
    membership: MembershipOut
    checkin: CheckinStatusOut


class CheckinIngestPayload(BaseModel):
    """Payload из Bot Gateway → Worker → Backend (через внутреннее API)."""

    user_id: int
    chat_id: int
    message_id: int
    proof_type: str  # video_note | photo | text
    message_sent_at: datetime
    text: str | None = None
    duration_seconds: int | None = None


class InternalCheckinResult(BaseModel):
    checkin_id: str | None
    accepted: bool
    code: str  # ok | checkin_already_exists | checkin_window_closed | membership_not_active | invalid_proof | habit_not_found