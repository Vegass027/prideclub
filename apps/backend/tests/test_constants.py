from __future__ import annotations

from app.core.constants import (
    MembershipStatus,
    PenaltyConfig,
    PenaltyReason,
    ProofType,
    TransactionType,
)


def test_membership_status_values() -> None:
    assert MembershipStatus.ACTIVE.value == "active"
    assert MembershipStatus.PAUSED.value == "paused"
    assert MembershipStatus.LEFT.value == "left"


def test_penalty_fund_share_full() -> None:
    assert PenaltyConfig.FUND_SHARE == 1.0


def test_proof_types() -> None:
    assert {p.value for p in ProofType} == {"video_note", "photo", "text"}


def test_penalty_reasons() -> None:
    assert PenaltyReason.CAUGHT.value == "caught"
    assert PenaltyReason.WINDOW_CLOSED_NO_CATCH.value == "window_closed_no_catch"


def test_transaction_types_include_penalty() -> None:
    assert TransactionType.PENALTY.value == "penalty"