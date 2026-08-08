"""users: deposit_balance — глобальный депозит на пользователя (Pravki-deposit-sse.md §Z-2.1).

Шаг 1 из двух (014b — DROP COLUMN на memberships.deposit_balance, отдельной
миграцией, чтобы было окно 1+ день между шагами для проверки что нигде в
коде не сломалось).

Зачем:
    Раньше депозит хранился на memberships.deposit_balance (отдельная копилка
    под каждый клуб). Это приводило к багу "deposit_exhausted после первой
    успешной поимки" — если membership жертвы никогда не пополнялась через
    radio-выбор клуба в TopUpModal, там был 0, и `min(penalty, 0) = 0`
    бросал PenaltyAlreadyProcessedError("deposit_exhausted") даже если у юзера
    в профиле деньги были.

    Глобальный депозит на users.deposit_balance решает эту проблему: один
    баланс на юзера, общий для всех клубов. MembershipService.recompute_pause_status
    централизованно замораживает те клубы, где депозита не хватает.

Backfill:
    На момент миграции (snapshot 2026-08-07) на проде 0 юзеров с реальными
    деньгами на депозитах. Поэтому тривиальный SUM(memberships.deposit_balance WHERE active)
    + sanity-проверки (защита от регрессии на будущее, когда появятся данные).

Sanity-проверки (RAISE в случае расхождения):
    1. SUM(users.deposit_balance) == SUM(memberships.deposit_balance) до миграции
       (полная консистентность: ни рубля не потеряно и не придумано).
    2. COUNT(users WHERE deposit_balance < 0) == 0 (нельзя уйти в минус).
    3. COUNT(users WHERE deposit_balance > 0 AND NOT EXISTS активной membership) == 0
       (юзеры с деньгами но без активных клубов — деньги в никуда, признак бага).

    Если любая sanity-проверка падает — op.execute с RAISE EXCEPTION прерывает
    миграцию. ALTER TABLE ADD COLUMN уже выполнен, но данные не записаны →
    БД остаётся в консистентном состоянии (column есть, значение по дефолту 0).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014a_user_deposit"
down_revision: str | None = "013_user_photo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Шаг 1: ADD COLUMN с дефолтом 0.
    op.add_column(
        "users",
        sa.Column(
            "deposit_balance",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
            comment=(
                "Глобальный депозит пользователя (в копейках). Общий для всех клубов. "
                "Списание и пополнение — через users.deposit_balance, не через "
                "memberships.deposit_balance (см. Pravki-deposit-sse.md §Z-2)."
            ),
        ),
    )

    # Шаг 2: backfill — сумма по активным memberships.
    op.execute(
        """
        UPDATE users
        SET deposit_balance = COALESCE(
            (SELECT SUM(deposit_balance) FROM memberships
             WHERE user_id = users.id AND status = 'active'),
            0
        )
        """
    )

    # Шаг 3: sanity-проверки. Любая упавшая → RAISE EXCEPTION, миграция не проходит.
    op.execute(
        """
        DO $$
        DECLARE
            users_sum BIGINT;
            members_sum BIGINT;
            negative_count INT;
            orphaned_count INT;
        BEGIN
            -- 1. SUM(users.deposit_balance) == SUM(memberships.deposit_balance WHERE active).
            --    Если разошлись — потеряли или придумали деньги.
            SELECT COALESCE(SUM(deposit_balance), 0) INTO users_sum FROM users;
            SELECT COALESCE(SUM(deposit_balance), 0) INTO members_sum
                FROM memberships WHERE status = 'active';
            IF users_sum <> members_sum THEN
                RAISE EXCEPTION
                    'migration 014a sanity check #1 failed: SUM(users.deposit_balance)=% != SUM(memberships.deposit_balance WHERE active)=%',
                    users_sum, members_sum;
            END IF;

            -- 2. Никаких отрицательных значений.
            SELECT COUNT(*) INTO negative_count FROM users WHERE deposit_balance < 0;
            IF negative_count <> 0 THEN
                RAISE EXCEPTION
                    'migration 014a sanity check #2 failed: % users with deposit_balance < 0',
                    negative_count;
            END IF;

            -- 3. Юзеры с деньгами но без активной membership = деньги в никуда.
            SELECT COUNT(*) INTO orphaned_count FROM users
                WHERE deposit_balance > 0
                  AND NOT EXISTS (
                      SELECT 1 FROM memberships
                      WHERE user_id = users.id AND status = 'active'
                  );
            IF orphaned_count <> 0 THEN
                RAISE EXCEPTION
                    'migration 014a sanity check #3 failed: % users have deposit_balance > 0 but no active membership',
                    orphaned_count;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Downgrade: дропаем колонку. memberships.deposit_balance всё ещё на месте
    # (его уберёт 014b при upgrade в обратном порядке). При даунгрейде 014b
    # не запускается — memberships.deposit_balance остаётся с актуальным значением,
    # а users.deposit_balance просто исчезает. Деньги не теряются.
    op.drop_column("users", "deposit_balance")
