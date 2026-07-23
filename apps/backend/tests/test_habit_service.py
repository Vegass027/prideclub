"""Unit-тесты HabitService (TZ §3.6).

Покрывают:
- create: валидация всех полей, is_active принудительно false, чат-ид уникален
- update: частичное обновление, заморозка финансовых полей, запрет изменения архивного
- archive: soft-delete с archived_at=now, идемпотентность
- restore: снимает архив, is_active остаётся false
- set_active: активация архивного запрещена, идемпотентность
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from uuid import uuid4

import pytest

from app.core.constants import ProofType
from app.core.exceptions import (
    HabitArchivedError,
    HabitNotFoundError,
    HabitTopicMismatchError,
    HabitValidationError,
)
from app.services.habit_service import HabitService
from tests.fakes import FakeHabitRepo, FakeMembershipRepo, make_habit


def _make_service(
    habit_repo: FakeHabitRepo | None = None,
    membership_repo: FakeMembershipRepo | None = None,
) -> tuple[HabitService, FakeHabitRepo, FakeMembershipRepo]:
    habit_repo = habit_repo or FakeHabitRepo()
    membership_repo = membership_repo or FakeMembershipRepo()
    svc = HabitService(
        session=None, habit_repo=habit_repo, membership_repo=membership_repo
    )
    return svc, habit_repo, membership_repo


def _base_kwargs(**overrides) -> dict:
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
        timezone_str="Europe/Moscow",
        proof_types=["video_note"],
        price_month=100_00,
        penalty_amount=10_00,
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


class TestCreate:
    async def test_creates_habit_with_is_active_false(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())

        assert habit.id is not None
        assert habit.is_active is False
        assert habit.archived_at is None
        assert habit.title == "Планка 30 мин"
        assert habit.stat_name == "Эстетика тела"
        assert habit.stat_icon == "💪"
        assert habit.penalty_amount == 10_00
        assert habit.price_month == 100_00
        assert habit in (await repo.list_active()) or habit not in (
            await repo.list_active()
        )
        assert habit not in (await repo.list_active())

    async def test_rejects_short_title(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(admin_id=42, **_base_kwargs(title="ab"))
        assert exc_info.value.code == "habit_title_too_short"

    async def test_rejects_long_title(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(admin_id=42, **_base_kwargs(title="x" * 129))
        assert exc_info.value.code == "habit_title_too_long"

    async def test_rejects_empty_stat_name(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(admin_id=42, **_base_kwargs(stat_name="   "))
        assert exc_info.value.code == "habit_stat_name_empty"

    async def test_rejects_invalid_invite_link(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(
                admin_id=42,
                **_base_kwargs(telegram_invite_link="https://example.com/join"),
            )
        assert exc_info.value.code == "habit_invite_link_format"

    async def test_accepts_null_invite_link(self) -> None:
        svc, _, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs(telegram_invite_link=None))
        assert habit.telegram_invite_link is None

    async def test_rejects_invalid_timezone(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(admin_id=42, **_base_kwargs(timezone_str="Moon/Tranquility"))
        assert exc_info.value.code == "habit_timezone_invalid"

    async def test_rejects_window_start_ge_end(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(
                admin_id=42,
                **_base_kwargs(
                    checkin_window_start=time(12, 0),
                    checkin_window_end=time(6, 0),
                ),
            )
        assert exc_info.value.code == "habit_window_order"

    async def test_rejects_negative_price(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(admin_id=42, **_base_kwargs(price_month=0))
        assert exc_info.value.code == "habit_price_invalid"

    async def test_rejects_float_price(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(admin_id=42, **_base_kwargs(price_month=99.99))  # type: ignore[arg-type]
        assert exc_info.value.code == "habit_price_invalid"

    async def test_rejects_negative_penalty(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(admin_id=42, **_base_kwargs(penalty_amount=-1))
        assert exc_info.value.code == "habit_penalty_invalid"

    async def test_rejects_zero_stat_gain(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(admin_id=42, **_base_kwargs(stat_gain_per_checkin=0))
        assert exc_info.value.code == "habit_stat_gain_invalid"

    async def test_rejects_negative_stat_loss(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(admin_id=42, **_base_kwargs(stat_loss_per_miss=-1))
        assert exc_info.value.code == "habit_stat_loss_invalid"

    async def test_rejects_zero_member_limit(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(admin_id=42, **_base_kwargs(member_limit=0))
        assert exc_info.value.code == "habit_member_limit_invalid"

    async def test_rejects_duplicate_chat_id(self) -> None:
        svc, repo, _ = _make_service()
        await svc.create(admin_id=42, **_base_kwargs())
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.create(admin_id=42, **_base_kwargs(title="Другой клуб"))
        assert exc_info.value.code == "habit_chat_id_duplicate"

    async def test_chat_topic_link_optional(self) -> None:
        svc, _, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        assert habit.chat_topic_thread_id is None

    async def test_chat_topic_link_assigned(self) -> None:
        svc, _, _ = _make_service()
        habit = await svc.create(
            admin_id=42,
            **_base_kwargs(chat_topic_link="https://t.me/c/-1001234567890/3"),
        )
        assert habit.chat_topic_thread_id == 3

    async def test_chat_topic_must_be_in_same_chat(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitTopicMismatchError):
            await svc.create(
                admin_id=42,
                **_base_kwargs(
                    chat_topic_link="https://t.me/c/-1007777777777/3",
                ),
            )

    async def test_chat_topic_must_differ_from_other_topics(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitValidationError):
            await svc.create(
                admin_id=42,
                **_base_kwargs(chat_topic_link="https://t.me/c/-1001234567890/1"),
            )


class TestUpdate:
    async def test_partial_update_changes_only_provided_fields(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())

        updated = await svc.update(
            admin_id=42,
            habit_id=habit.id,
            fields={"description": "Новое описание"},
        )
        assert updated.description == "Новое описание"
        assert updated.title == "Планка 30 мин"
        assert updated.price_month == 100_00

    async def test_update_title_strips_whitespace(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        updated = await svc.update(
            admin_id=42,
            habit_id=habit.id,
            fields={"title": "  Новый заголовок  "},
        )
        assert updated.title == "Новый заголовок"

    async def test_update_rejects_invalid_invite_link(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.update(
                admin_id=42,
                habit_id=habit.id,
                fields={"telegram_invite_link": "ftp://example.com"},
            )
        assert exc_info.value.code == "habit_invite_link_format"

    async def test_update_not_found_raises(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitNotFoundError):
            await svc.update(
                admin_id=42,
                habit_id=str(uuid4()),
                fields={"title": "X" * 50},
            )

    async def test_update_archived_raises(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        await svc.archive(admin_id=42, habit_id=habit.id)

        with pytest.raises(HabitArchivedError):
            await svc.update(
                admin_id=42,
                habit_id=habit.id,
                fields={"title": "Новое имя"},
            )

    async def test_financial_fields_editable_after_first_member(self) -> None:
        """Owner может менять price_month/penalty_amount даже после первого участника.

        Middleware /admin/v1/* уже гейтит доступ только owner'у —
        заморозка финансов СНЯТА. Используется сценарий «поднять цену
        с нового месяца с уведомлением участников».
        """
        svc, repo, ms_repo = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        # Регистрируем активного участника.
        ms_repo.add_for(user_id=1, habit_id=str(habit.id))
        repo.set_active_member_count(str(habit.id), 1)

        updated = await svc.update(
            admin_id=42,
            habit_id=str(habit.id),
            fields={"price_month": 50_00, "penalty_amount": 20_00},
        )
        assert updated.price_month == 50_00
        assert updated.penalty_amount == 20_00

    async def test_financial_fields_editable_when_no_members(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())

        updated = await svc.update(
            admin_id=42,
            habit_id=habit.id,
            fields={"price_month": 50_00, "penalty_amount": 5_00},
        )
        assert updated.price_month == 50_00
        assert updated.penalty_amount == 5_00

    async def test_update_window_validates_pair(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        with pytest.raises(HabitValidationError) as exc_info:
            await svc.update(
                admin_id=42,
                habit_id=habit.id,
                fields={
                    "checkin_window_start": time(14, 0),
                    "checkin_window_end": time(6, 0),
                },
            )
        assert exc_info.value.code == "habit_window_order"


class TestArchive:
    async def test_archive_sets_archived_at_and_inactive(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        await svc.set_active(admin_id=42, habit_id=habit.id, is_active=True)

        archived = await svc.archive(admin_id=42, habit_id=habit.id)

        assert archived.archived_at is not None
        assert archived.is_active is False
        assert archived.archived_at.tzinfo is not None

    async def test_archive_idempotent(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        first = await svc.archive(admin_id=42, habit_id=habit.id)
        second = await svc.archive(admin_id=42, habit_id=habit.id)
        assert first.archived_at == second.archived_at

    async def test_archive_not_found_raises(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitNotFoundError):
            await svc.archive(admin_id=42, habit_id=str(uuid4()))


class TestRestore:
    async def test_restore_clears_archived_at_keeps_inactive(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        await svc.archive(admin_id=42, habit_id=habit.id)
        assert habit.is_active is False

        restored = await svc.restore(admin_id=42, habit_id=habit.id)

        assert restored.archived_at is None
        assert restored.is_active is False

    async def test_restore_idempotent(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        first = await svc.restore(admin_id=42, habit_id=habit.id)
        assert first.archived_at is None
        second = await svc.restore(admin_id=42, habit_id=habit.id)
        assert second.archived_at is None

    async def test_restore_not_found_raises(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitNotFoundError):
            await svc.restore(admin_id=42, habit_id=str(uuid4()))


class TestSetActive:
    async def test_activate_changes_flag(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        assert habit.is_active is False

        updated = await svc.set_active(admin_id=42, habit_id=habit.id, is_active=True)
        assert updated.is_active is True

    async def test_deactivate_works(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        await svc.set_active(admin_id=42, habit_id=habit.id, is_active=True)
        updated = await svc.set_active(admin_id=42, habit_id=habit.id, is_active=False)
        assert updated.is_active is False

    async def test_activate_archived_raises(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        await svc.archive(admin_id=42, habit_id=habit.id)
        with pytest.raises(HabitArchivedError):
            await svc.set_active(admin_id=42, habit_id=habit.id, is_active=True)

    async def test_set_active_idempotent(self) -> None:
        svc, repo, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        first = await svc.set_active(admin_id=42, habit_id=habit.id, is_active=False)
        second = await svc.set_active(admin_id=42, habit_id=habit.id, is_active=False)
        assert first.is_active == second.is_active

    async def test_set_active_not_found_raises(self) -> None:
        svc, _, _ = _make_service()
        with pytest.raises(HabitNotFoundError):
            await svc.set_active(admin_id=42, habit_id=str(uuid4()), is_active=True)


class TestUpdateTopics:
    async def test_update_accepts_topics_matching_habit_chat(self) -> None:
        svc, _, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        updated = await svc.update(
            admin_id=42,
            habit_id=habit.id,
            fields={
                "checkin_topic_link": "https://t.me/c/-1001234567890/100",
                "notifications_topic_link": "https://t.me/c/-1001234567890/200",
            },
        )
        assert updated.checkin_topic_thread_id == 100
        assert updated.notifications_topic_thread_id == 200

    async def test_update_rejects_topic_from_other_chat(self) -> None:
        svc, _, _ = _make_service()
        habit = await svc.create(admin_id=42, **_base_kwargs())
        with pytest.raises(HabitTopicMismatchError):
            await svc.update(
                admin_id=42,
                habit_id=habit.id,
                fields={
                    "checkin_topic_link": "https://t.me/c/-1007777777777/1",
                },
            )

    async def test_update_swallows_chat_id_from_topic_when_zero(self) -> None:
        """Если habit.chat_id был 0 (клубы до миграции), первая валидная
        ссылка на топик привязывает чат клуба к часу из ссылки."""
        from uuid import uuid4
        from tests.fakes import make_habit

        svc, repo, _ = _make_service()
        orphan = make_habit(id=str(uuid4()), chat_id=0)
        repo.add(orphan)
        updated = await svc.update(
            admin_id=42,
            habit_id=orphan.id,
            fields={
                "checkin_topic_link": "https://t.me/c/-1001234567890/5",
                "notifications_topic_link": "https://t.me/c/-1001234567890/6",
            },
        )
        assert updated.chat_id == -1001234567890
        assert updated.checkin_topic_thread_id == 5
        assert updated.notifications_topic_thread_id == 6


# ───────── Multi-proof_types (миграция 012) ─────────


@pytest.mark.asyncio
async def test_create_accepts_multiple_proof_types() -> None:
    """Клуб создаётся с proof_types из 2-3 значений."""
    svc, repo, _ = _make_service()
    habit = await svc.create(
        admin_id=42,
        **_base_kwargs(proof_types=["video_note", "photo"]),
    )
    assert habit.proof_types == ["video_note", "photo"]
    assert habit.proof_type.value == "video_note"  # alias первого


@pytest.mark.asyncio
async def test_create_validates_proof_types_count() -> None:
    """0 или >3 значений → HabitValidationError."""
    svc, _, _ = _make_service()
    with pytest.raises(HabitValidationError) as exc:
        await svc.create(admin_id=42, **_base_kwargs(proof_types=[]))
    assert exc.value.code == "habit_proof_types_count"

    with pytest.raises(HabitValidationError) as exc:
        await svc.create(
            admin_id=42,
            **_base_kwargs(proof_types=["video_note", "photo", "text", "extra"]),
        )
    assert exc.value.code == "habit_proof_types_count"


@pytest.mark.asyncio
async def test_create_validates_proof_types_duplicates() -> None:
    svc, _, _ = _make_service()
    with pytest.raises(HabitValidationError) as exc:
        await svc.create(
            admin_id=42,
            **_base_kwargs(proof_types=["video_note", "video_note"]),
        )
    assert exc.value.code == "habit_proof_types_duplicates"


@pytest.mark.asyncio
async def test_create_validates_proof_types_values() -> None:
    svc, _, _ = _make_service()
    with pytest.raises(HabitValidationError) as exc:
        await svc.create(
            admin_id=42,
            **_base_kwargs(proof_types=["video_note", "audio"]),
        )
    assert exc.value.code == "habit_proof_types_invalid"


@pytest.mark.asyncio
async def test_update_proof_types_syncs_proof_type_alias() -> None:
    """update с proof_types синхронизирует proof_type (alias первого)."""
    from uuid import uuid4

    from tests.fakes import make_habit

    svc, repo, _ = _make_service()
    h = make_habit(id=str(uuid4()), proof=ProofType.VIDEO_NOTE)
    repo.add(h)
    updated = await svc.update(
        admin_id=42,
        habit_id=h.id,
        fields={"proof_types": ["photo", "text"]},
    )
    assert updated.proof_types == ["photo", "text"]
    assert updated.proof_type == ProofType.PHOTO  # alias


@pytest.mark.asyncio
async def test_update_proof_types_validates() -> None:
    from uuid import uuid4

    from tests.fakes import make_habit

    svc, repo, _ = _make_service()
    h = make_habit(id=str(uuid4()))
    repo.add(h)
    with pytest.raises(HabitValidationError):
        await svc.update(
            admin_id=42,
            habit_id=h.id,
            fields={"proof_types": ["unknown"]},
        )


# ───────── force_update_financials (owner-only escape hatch) ─────────


@pytest.mark.asyncio
async def test_force_update_financials_requires_confirm() -> None:
    """Без confirm=true — HabitValidationError, никаких изменений."""
    from uuid import uuid4

    from tests.fakes import make_habit

    svc, repo, _ = _make_service()
    h = make_habit(id=str(uuid4()))
    repo.add(h)
    old_price = h.price_month
    with pytest.raises(HabitValidationError) as exc:
        await svc.force_update_financials(
            admin_id=42,
            habit_id=h.id,
            price_month=999,
            penalty_amount=None,
            confirm=False,
        )
    assert exc.value.code == "habit_force_financials_confirm_required"
    # Состояние не изменилось.
    assert h.price_month == old_price


@pytest.mark.asyncio
async def test_force_update_financials_requires_at_least_one_field() -> None:
    """Пустые поля → HabitValidationError."""
    from uuid import uuid4

    from tests.fakes import make_habit

    svc, repo, _ = _make_service()
    h = make_habit(id=str(uuid4()))
    repo.add(h)
    with pytest.raises(HabitValidationError) as exc:
        await svc.force_update_financials(
            admin_id=42,
            habit_id=h.id,
            price_month=None,
            penalty_amount=None,
            confirm=True,
        )
    assert exc.value.code == "habit_force_financials_no_fields"


@pytest.mark.asyncio
async def test_force_update_financials_validates_positive_amounts() -> None:
    from uuid import uuid4

    from tests.fakes import make_habit

    svc, repo, _ = _make_service()
    h = make_habit(id=str(uuid4()))
    repo.add(h)
    with pytest.raises(HabitValidationError) as exc:
        await svc.force_update_financials(
            admin_id=42,
            habit_id=h.id,
            price_month=-1,
            penalty_amount=None,
            confirm=True,
        )
    assert exc.value.code == "habit_price_invalid"


@pytest.mark.asyncio
async def test_force_update_financials_updates_values() -> None:
    from uuid import uuid4

    from tests.fakes import make_habit

    svc, repo, _ = _make_service()
    h = make_habit(
        id=str(uuid4()),
        chat_id=-1001,
    )
    h.price_month = 100_00
    h.penalty_amount = 10_00
    repo.add(h)
    result = await svc.force_update_financials(
        admin_id=42,
        habit_id=h.id,
        price_month=500_00,
        penalty_amount=50_00,
        confirm=True,
    )
    assert result["old_price_month"] == 100_00
    assert result["new_price_month"] == 500_00
    assert result["old_penalty_amount"] == 10_00
    assert result["new_penalty_amount"] == 50_00
    assert h.price_month == 500_00
    assert h.penalty_amount == 50_00


@pytest.mark.asyncio
async def test_force_update_financials_no_op_when_unchanged() -> None:
    """Если значения совпадают — no-op (без UPDATE), без audit log."""
    from uuid import uuid4

    from tests.fakes import make_habit

    svc, repo, _ = _make_service()
    h = make_habit(id=str(uuid4()))
    h.price_month = 100_00
    h.penalty_amount = 10_00
    repo.add(h)
    result = await svc.force_update_financials(
        admin_id=42,
        habit_id=h.id,
        price_month=100_00,  # то же самое
        penalty_amount=10_00,  # то же самое
        confirm=True,
    )
    assert result["old_price_month"] == 100_00
    assert result["new_price_month"] == 100_00


@pytest.mark.asyncio
async def test_force_update_financials_only_changes_target_fields() -> None:
    """ГЛАВНЫЙ ТЕСТ: force_update_financials НЕ трогает ничего, кроме price/penalty.

    Проверяет что:
    - users.deposit_balance участника не изменился,
    - memberships.subscription_until не изменился,
    - memberships.auto_renew_enabled не изменился,
    - memberships.status не изменился,
    - active_members_count не сбросился.
    """
    from uuid import uuid4

    from tests.fakes import (
        FakeMembershipRepo,
        make_habit,
    )

    svc, repo, _ = _make_service()
    h = make_habit(id=str(uuid4()))
    h.price_month = 100_00
    h.penalty_amount = 10_00
    repo.add(h)

    # Создаём активное membership с конкретным состоянием.
    membership = svc._membership_repo.add_for(  # noqa: SLF001
        user_id=12345,
        habit_id=h.id,
    )
    membership.deposit_balance = 500_00
    membership.subscription_until = None
    membership.auto_renew_enabled = True
    repo.set_active_member_count(h.id, 5)

    snapshot = {
        "user_id": membership.user_id,
        "deposit_balance": membership.deposit_balance,
        "subscription_until": membership.subscription_until,
        "auto_renew_enabled": membership.auto_renew_enabled,
        "active_members_count": 5,
    }

    result = await svc.force_update_financials(
        admin_id=42,
        habit_id=h.id,
        price_month=500_00,
        penalty_amount=50_00,
        confirm=True,
    )
    # В FakeService session=None, commit() не вызываем — проверяем состояние in-memory.

    # Цены поменялись.
    assert result["new_price_month"] == 500_00
    assert result["new_penalty_amount"] == 50_00

    # Участник не тронут.
    from_repo = await svc._membership_repo.get(membership.id)  # noqa: SLF001
    assert from_repo.deposit_balance == snapshot["deposit_balance"]
    assert from_repo.subscription_until == snapshot["subscription_until"]
    assert from_repo.auto_renew_enabled == snapshot["auto_renew_enabled"]
    assert from_repo.status.value == "active"  # MembershipStatus.ACTIVE
    # Счётчик участников не сбросился.
    assert await repo.count_active_members(h.id) == 5  # noqa: SLF001
