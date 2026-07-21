from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.core.constants import (
    MembershipStatus,
    PenaltyConfig,
    PenaltyReason,
    TransactionType,
)
from app.models.membership import Membership
from app.models.penalty import Penalty
from app.models.user import User
from app.repositories.membership_repository import MembershipRepository
from app.services.bonus_service import BonusService
from sqlalchemy import select
from tests.fakes import FakeMembershipRepo


class _FakeSession:
    def __init__(self) -> None:
        self.users: dict[int, User] = {}
        self.memberships: dict[str, Membership] = {}
        self.penalties: dict[str, Penalty] = {}
        self.transactions: list = []
        self.committed = False

    def add(self, obj) -> None:
        if isinstance(obj, Penalty):
            self.penalties[obj.id] = obj
        else:
            self.transactions.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    async def execute(self, stmt):
        from sqlalchemy.sql import Select

        from app.models.auxiliary import BonusRule

        compiled = getattr(stmt, "element", None)
        # Penalty lookup by id
        if isinstance(stmt, Select) and Penalty in stmt.column_descriptions and False:
            pass

        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def scalar_one_or_none(self_inner):
                return self_inner._rows[0][1] if self_inner._rows else None

            def all(self_inner):
                return self_inner._rows

        try:
            from sqlalchemy.sql import column

            cols = set(getattr(stmt, "selected_columns", None) or [])
        except Exception:
            cols = set()

        # Heuristic — return empty result; service-level logic covers happy path via direct injections.
        return _Result([])


@pytest.mark.asyncio
async def test_apply_catch_bonus_idempotent() -> None:
    session = _FakeSession()
    user = User(id=1, first_name="cat", bonus_points=0)
    session.users[user.id] = user
    catcher_m = Membership(
        id=str(uuid4()), user_id=user.id, habit_id=str(uuid4()),
        status=MembershipStatus.ACTIVE,
    )
    session.memberships[catcher_m.id] = catcher_m
    penalty = Penalty(
        id=str(uuid4()),
        membership_id=str(uuid4()),
        catcher_membership_id=str(catcher_m.id),
        amount=100,
        fund_share=100,
        reason=PenaltyReason.CAUGHT,
        date=date(2026, 1, 1),
    )
    session.penalties[penalty.id] = penalty

    service = BonusService(session=session, membership_repo=FakeMembershipRepo())  # type: ignore[arg-type]

    # First call doesn't see the user from `session.execute(penalty_query)`, so returns 0
    # because the heuristic returns empty. Validate idempotency by patching the
    # session.execute to return the row.
    original_execute = session.execute

    async def execute_with_obj(stmt):
        from sqlalchemy import select as sa_select

        from app.models.auxiliary import BonusRule
        from app.models.membership import Membership as M
        from app.models.penalty import Penalty as P
        from app.models.user import User as U

        compiled = stmt.compile()
        if Penalty in compiled.column_keys:
            return _Result([(penalty,)])
        if M in compiled.column_keys and compiled.column_keys.get(M) is User.id:
            return _Result([(catcher_m,)])
        if U in compiled.column_keys:
            return _Result([(user,)])
        return await original_execute(stmt)

    session.execute = execute_with_obj  # type: ignore[assignment]

    applied = await service.apply_catch_bonus(
        catcher_membership_id=str(catcher_m.id),
        penalty_id=str(penalty.id),
    )
    assert applied == PenaltyConfig.CATCHER_BONUS_POINTS
    assert penalty.bonus_applied is True
    assert user.bonus_points == PenaltyConfig.CATCHER_BONUS_POINTS

    applied_again = await service.apply_catch_bonus(
        catcher_membership_id=str(catcher_m.id),
        penalty_id=str(penalty.id),
    )
    assert applied_again == 0  # идемпотентно