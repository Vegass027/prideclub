"""Unit-тесты для admin endpoint поля Habit.catcher_amount_kopecks (Pravki-catcher-deposit,
Phase 1 Task 1.5, 2026-08-21).

Покрытие:
1. Pydantic AdminHabitCreateRequest: поле принимается с ge=0 валидацией,
   default=0 для существующих клубов (обратная совместимость).
2. HabitService.create(): поле пробрасывается в Habit объект.
3. HabitService.update(): поле обновляется через fields dict (без заморозки —
   в отличие от price_month/penalty_amount, не финансовое обязательство).
4. Pydantic AdminHabitUpdateRequest: опционально (default=None = не менять).
5. AdminHabitOut: содержит catcher_amount_kopecks в response.

⚠️ Тесты идут через HabitService напрямую, минуя TestClient —
admin_habits_api тесты требуют Redis и падают (pre-existing baseline).
"""

from __future__ import annotations

from datetime import UTC, time

import pytest
from pydantic import ValidationError

from app.schemas import (
    AdminHabitCreateRequest,
    AdminHabitOut,
    AdminHabitUpdateRequest,
)
from app.services.habit_service import HabitService
from tests.fakes import FakeHabitRepo, FakeMembershipRepo

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _base_create_kwargs(**overrides) -> dict:
    base = dict(
        title="Планка 30 мин",
        description="Держим планку",
        photo_url=None,
        telegram_invite_link="https://t.me/+abc123",
        stat_name="Эстетика тела",
        stat_icon="💪",
        chat_id=-1001234567890,
        checkin_window_start=time(6, 0),
        checkin_window_end=time(11, 0),
        timezone="Europe/Moscow",
        proof_type="video_note",
        proof_types=["video_note"],
        price_month=100_00,  # 1000₽
        penalty_amount=30_00,  # 300₽ (3x от 100₽ — больше min)
        catcher_amount_kopecks=0,  # Pravki-catcher-deposit Task 1.5
        stat_gain_per_checkin=2,
        stat_loss_per_miss=1,
        member_limit=None,
        curator_id=None,
        checkin_topic_link="https://t.me/c/-1001234567890/1",
        notifications_topic_link="https://t.me/c/-1001234567890/2",
        chat_topic_link=None,
    )
    base.update(overrides)
    return base


def _service_kwargs(**overrides) -> dict:
    """То же что _base_create_kwargs, но с timezone_str для HabitService.

    Различия между Pydantic-схемой (AdminHabitCreateRequest) и сервисом:
    - timezone → timezone_str (HabitService.create)
    - proof_type не передаётся (HabitService.create принимает только proof_types;
      proof_type синхронизируется сервисом из proof_types[0]).
    """
    base = _base_create_kwargs(**overrides)
    if "timezone" in base:
        base["timezone_str"] = base.pop("timezone")
    base.pop("proof_type", None)
    return base


def _make_service() -> tuple[HabitService, FakeHabitRepo, FakeMembershipRepo]:
    habit_repo = FakeHabitRepo()
    membership_repo = FakeMembershipRepo()
    svc = HabitService(session=None, habit_repo=habit_repo, membership_repo=membership_repo)
    return svc, habit_repo, membership_repo


# Pydantic-схемы: AdminHabitCreateRequest
# ---------------------------------------------------------------------------


class TestAdminHabitCreateRequestSchema:
    def test_default_catcher_amount_kopecks_is_zero(self) -> None:
        """Без явного catcher_amount_kopecks → default=0 (старое поведение)."""
        payload = AdminHabitCreateRequest(**_base_create_kwargs())
        assert payload.catcher_amount_kopecks == 0

    def test_accepts_positive_catcher_amount_kopecks(self) -> None:
        """Положительная сумма ловцу принимается."""
        payload = AdminHabitCreateRequest(
            **_base_create_kwargs(catcher_amount_kopecks=10_00)  # 100₽
        )
        assert payload.catcher_amount_kopecks == 10_00

    def test_accepts_catcher_amount_greater_than_penalty(self) -> None:
        """catcher_amount_kopecks >= penalty_amount допустим — clamp в apply_catch."""
        # penalty_amount=30_00 (300₽), catcher=50_00 (500₽).
        # Edge case: в apply_catch clamp к фактическому списанию (min).
        payload = AdminHabitCreateRequest(
            **_base_create_kwargs(
                penalty_amount=30_00,
                catcher_amount_kopecks=50_00,
            )
        )
        assert payload.catcher_amount_kopecks == 50_00

    def test_rejects_negative_catcher_amount(self) -> None:
        """Отрицательная сумма ловцу отвергается (ge=0)."""
        with pytest.raises(ValidationError) as exc_info:
            AdminHabitCreateRequest(**_base_create_kwargs(catcher_amount_kopecks=-100))
        assert "catcher_amount_kopecks" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Pydantic-схемы: AdminHabitUpdateRequest
# ---------------------------------------------------------------------------


class TestAdminHabitUpdateRequestSchema:
    def test_default_catcher_amount_kopecks_is_none(self) -> None:
        """Без явного поля → default=None (НЕ обновлять)."""
        payload = AdminHabitUpdateRequest(title="Новое название")
        assert payload.catcher_amount_kopecks is None

    def test_accepts_explicit_zero_to_reset(self) -> None:
        """Явный 0 — сбросить к "всё в фонд" (старое поведение)."""
        payload = AdminHabitUpdateRequest(catcher_amount_kopecks=0)
        assert payload.catcher_amount_kopecks == 0

    def test_accepts_positive_value(self) -> None:
        """Положительная сумма ловцу принимается."""
        payload = AdminHabitUpdateRequest(catcher_amount_kopecks=10_00)
        assert payload.catcher_amount_kopecks == 10_00

    def test_rejects_negative(self) -> None:
        """Отрицательная сумма отвергается (ge=0)."""
        with pytest.raises(ValidationError) as exc_info:
            AdminHabitUpdateRequest(catcher_amount_kopecks=-100)
        assert "catcher_amount_kopecks" in str(exc_info.value)


# ---------------------------------------------------------------------------
# HabitService.create()
# ---------------------------------------------------------------------------


class TestHabitServiceCreate:
    @pytest.mark.asyncio
    async def test_create_with_catcher_amount_kopecks(self) -> None:
        """Pydantic-схема принимает поле → HabitService.create() сохраняет в Habit."""
        svc, _, _ = _make_service()
        habit = await svc.create(
            admin_id=42,
            **_service_kwargs(
                penalty_amount=30_00,  # 300₽
                catcher_amount_kopecks=10_00,  # 100₽ ловцу
            ),
        )
        assert habit.catcher_amount_kopecks == 10_00

    @pytest.mark.asyncio
    async def test_create_default_catcher_amount_kopecks_zero(self) -> None:
        """Без явного поля → default 0 (старое поведение "всё в фонд")."""
        svc, _, _ = _make_service()
        habit = await svc.create(admin_id=42, **_service_kwargs())
        assert habit.catcher_amount_kopecks == 0


# ---------------------------------------------------------------------------
# HabitService.update()
# ---------------------------------------------------------------------------


class TestHabitServiceUpdate:
    @pytest.mark.asyncio
    async def test_update_catcher_amount_kopecks(self) -> None:
        """PATCH обновляет поле (без заморозки — в отличие от penalty/price_month)."""
        svc, _, _ = _make_service()
        # Сначала создаём клуб с catcher=0
        habit = await svc.create(admin_id=42, **_service_kwargs())
        assert habit.catcher_amount_kopecks == 0
        # Затем обновляем на 100₽
        updated = await svc.update(
            admin_id=42,
            habit_id=str(habit.id),
            fields={"catcher_amount_kopecks": 10_00},
        )
        assert updated.catcher_amount_kopecks == 10_00

    @pytest.mark.asyncio
    async def test_update_reset_to_zero(self) -> None:
        """PATCH с catcher_amount_kopecks=0 — сброс к старому поведению."""
        svc, _, _ = _make_service()
        habit = await svc.create(
            admin_id=42,
            **_service_kwargs(catcher_amount_kopecks=10_00),
        )
        assert habit.catcher_amount_kopecks == 10_00

        updated = await svc.update(
            admin_id=42,
            habit_id=str(habit.id),
            fields={"catcher_amount_kopecks": 0},
        )
        assert updated.catcher_amount_kopecks == 0


# ---------------------------------------------------------------------------
# AdminHabitOut (response)
# ---------------------------------------------------------------------------


class TestAdminHabitOut:
    def test_default_catcher_amount_kopecks_is_zero(self) -> None:
        """Если в конструктор не передано — default 0 (безопасно для UI)."""
        from datetime import datetime
        from uuid import uuid4

        out = AdminHabitOut(
            id=str(uuid4()),
            title="Test",
            description=None,
            chat_id=100,
            checkin_window_start="09:00:00",
            checkin_window_end="21:00:00",
            timezone="Europe/Moscow",
            penalty_amount=30_00,
            price_month=100_00,
            proof_type="video_note",
            proof_types=["video_note"],
            prize_pool=0,
            is_active=False,
            photo_url=None,
            telegram_invite_link=None,
            stat_name="Test",
            stat_icon=None,
            stat_gain_per_checkin=2,
            stat_loss_per_miss=1,
            member_limit=None,
            curator_id=None,
            checkin_topic_thread_id=None,
            notifications_topic_thread_id=None,
            checkin_topic_link=None,
            notifications_topic_link=None,
            chat_topic_thread_id=None,
            chat_topic_link=None,
            archived_at=None,
            created_at=datetime.now(tz=UTC),
        )
        assert out.catcher_amount_kopecks == 0

    def test_explicit_catcher_amount_in_response(self) -> None:
        """Поле есть в AdminHabitOut и доступно для UI."""
        from datetime import datetime
        from uuid import uuid4

        out = AdminHabitOut(
            id=str(uuid4()),
            title="Test",
            description=None,
            chat_id=100,
            checkin_window_start="09:00:00",
            checkin_window_end="21:00:00",
            timezone="Europe/Moscow",
            penalty_amount=30_00,
            price_month=100_00,
            proof_type="video_note",
            proof_types=["video_note"],
            prize_pool=0,
            is_active=False,
            photo_url=None,
            telegram_invite_link=None,
            stat_name="Test",
            stat_icon=None,
            stat_gain_per_checkin=2,
            stat_loss_per_miss=1,
            catcher_amount_kopecks=10_00,
            member_limit=None,
            curator_id=None,
            checkin_topic_thread_id=None,
            notifications_topic_thread_id=None,
            checkin_topic_link=None,
            notifications_topic_link=None,
            chat_topic_thread_id=None,
            chat_topic_link=None,
            archived_at=None,
            created_at=datetime.now(tz=UTC),
        )
        assert out.catcher_amount_kopecks == 10_00
