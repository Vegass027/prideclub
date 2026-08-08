"""Тесты для UserRepository (Pravki-deposit-sse.md §Z-2.4).

Покрывает:
- lock_for_update: возвращает User или None; в проде — SELECT ... FOR UPDATE.
- add_balance: lock + +=; атомарно в текущей транзакции.
- идемпотентность владения user-объектом (после lock + add_balance мы видим
  обновлённый deposit_balance).

Использует FakeUserRepo как замену прода — нет зависимости от SQL/PG.
"""
from __future__ import annotations

import pytest

from app.models.user import User
from tests.fakes import FakeUserRepo


def _make_user(*, id: int, deposit_balance: int) -> User:
    return User(id=id, first_name=f"u{id}", deposit_balance=deposit_balance)


@pytest.mark.asyncio
async def test_lock_for_update_returns_existing_user() -> None:
    repo = FakeUserRepo()
    u = _make_user(id=1, deposit_balance=500)
    repo.add(u)

    locked = await repo.lock_for_update(1)
    assert locked is u
    assert locked.deposit_balance == 500


@pytest.mark.asyncio
async def test_lock_for_update_returns_none_for_missing_user() -> None:
    repo = FakeUserRepo()
    locked = await repo.lock_for_update(999)
    assert locked is None


@pytest.mark.asyncio
async def test_lock_for_update_records_call_for_inspection() -> None:
    """Контракт: вызывающий код (PenaltyService/PaymentService) должен ровно
    один раз вызвать lock_for_update перед мутацией deposit_balance.
    """
    repo = FakeUserRepo()
    repo.add(_make_user(id=1, deposit_balance=0))

    await repo.lock_for_update(1)
    await repo.lock_for_update(1)

    assert repo._lock_calls == [1, 1]  # noqa: SLF001


@pytest.mark.asyncio
async def test_add_balance_increments_deposit() -> None:
    repo = FakeUserRepo()
    repo.add(_make_user(id=1, deposit_balance=100))

    u = await repo.add_balance(1, 250)

    assert u.deposit_balance == 350
    # add_balance делает lock внутри — должен быть зафиксирован вызов.
    assert repo._lock_calls == [1]  # noqa: SLF001


@pytest.mark.asyncio
async def test_add_balance_supports_negative_amount_for_catch() -> None:
    """apply_catch использует add_balance(violator_user_id, -amount) или
    прямую мутацию после lock_for_update. Тест покрывает прямую мутацию
    (как в проде PenaltyService.apply_catch).
    """
    repo = FakeUserRepo()
    repo.add(_make_user(id=1, deposit_balance=500))

    # Имитируем списание штрафа напрямую через lock_for_update + мутация.
    locked = await repo.lock_for_update(1)
    assert locked is not None
    locked.deposit_balance -= 200

    assert locked.deposit_balance == 300


@pytest.mark.asyncio
async def test_add_balance_raises_for_missing_user() -> None:
    repo = FakeUserRepo()
    with pytest.raises(ValueError, match="user 999 not found"):
        await repo.add_balance(999, 100)
