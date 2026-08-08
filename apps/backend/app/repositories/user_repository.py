from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self, *, id: int, first_name: str, username: str | None
    ) -> User:
        """INSERT ON CONFLICT DO UPDATE — идемпотентное обновление."""
        stmt = (
            pg_insert(User)
            .values(id=id, first_name=first_name, username=username)
            .on_conflict_do_update(
                index_elements=[User.id],
                set_={"first_name": first_name, "username": username},
            )
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get(self, user_id: int) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def lock_for_update(self, user_id: int) -> User | None:
        """SELECT ... FOR UPDATE по users.id — для списания/пополнения депозита.

        Pravki-deposit-sse.md §Z-2.4: один лок на user сериализует параллельные
        catch/topup этого юзера в любых клубах. Применяется в PenaltyService.apply_catch
        и PaymentService._apply.

        Возвращает None если user не существует (вызывающий код бросает
        соответствующее доменное исключение).
        """
        result = await self._session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def add_balance(self, user_id: int, amount: int) -> User:
        """lock_for_update + deposit_balance += amount. Атомарно в текущей транзакции.

        Pravki-deposit-sse.md §Z-2.3. Вызывающий код управляет commit'ом.
        Возвращает залоченный User для удобства чтения balance_after.
        """
        u = await self.lock_for_update(user_id)
        if u is None:
            raise ValueError(f"user {user_id} not found")
        u.deposit_balance += amount
        return u