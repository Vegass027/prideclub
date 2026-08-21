from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.user_stats import UserStats


class UserStatsRepository:
    """Глобальный per-user-per-character счётчик (Phase 3 v2).

    Per layering rule (AGENTS.md): слой репозитория без бизнес-логики.
    Все write-методы мутируют переданный ORM-объект или делают bulk
    UPDATE, но НИКОГДА не вызывают session.commit() — коммит на
    уровне caller (CharacterService / worker task).

    ⚠️ Race-safety критична — `get_or_create_for_update` ЗАЩИЩАЕТ от
    гонок при двух параллельных чек-инах из разных клубов, качающих
    одну stat, через INSERT ... ON CONFLICT (user_id,
    stat_definition_id) DO NOTHING RETURNING + SELECT FOR UPDATE
    fallback. Тот же паттерн, что CheckinRepository.get_or_create_done
    (миграции 008 + 011). Под READ COMMITTED достаточно — НЕ
    нужен SERIALIZABLE / advisory locks. Полный разбор сценариев
    (A/B/C) — recon Phase 3.2 от 21.08.2026 §3.2.

    ⚠️ `freeze()` НЕ идемпотентен (повторный вызов обновляет
    frozen_at). Идемпотентность — в CharacterService.apply_freeze
    (Task 3.3) через guard `if stat.is_frozen: return stat` ДО
    вызова repo.freeze(). bulk_freeze здесь защищён на SQL-уровне
    WHERE `is_frozen IS false` (defense-in-depth от worker
    redelivery / дубля батча / нескольких cron-инстансов).

    ⚠️ `iter_for_freeze_cron` НЕ использует OFFSET (recon fix 1
    от 21.08.2026). После bulk_freeze выпавшие строки исчезают из
    WHERE is_frozen=false, и OFFSET привёл бы к пропуску ещё не
    замороженных candidate'ов. Top-N по тому же WHERE +
    стабильный ORDER BY (stat_definition_id, last_checkin_at, id)
    каждый раз даёт следующую свежую пачку.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._logger = get_logger("user_stats_repository")

    # ─── Race-safe row acquisition ────────────────────────────

    async def get_or_create_for_update(
        self,
        *,
        user_id: int,
        stat_definition_id: str,
    ) -> tuple[UserStats, bool]:
        """INSERT ... ON CONFLICT DO NOTHING + SELECT FOR UPDATE fallback.

        Returns `(row, created_now)`:
            (row, True)  — мы создали (RETURNING не пустой);
            (row, False) — row уже был (наш INSERT не сработал,
                           прочитали чужой под FOR UPDATE).

        Caller получает ORM-row под lock в нашей же tx и может
        сразу звать increment_value / decrement_with_floor /
        unfreeze / touch_last_checkin — никакого отдельного
        lock_for_update() не требуется.

        Под READ COMMITTED корректно сериализует параллельные
        create-then-increment из разных клубов с одной stat
        (см. recon §3.2).
        """
        stmt = (
            pg_insert(UserStats)
            .values(
                user_id=user_id,
                stat_definition_id=stat_definition_id,
                value=0,
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "stat_definition_id"],
            )
            .returning(UserStats)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None:
            return row, True

        # Conflict path — другая tx уже создала row (или ещё не
        # закоммитила; тогда FOR UPDATE заблокируется до её
        # commit'а, что и нужно для сериализации).
        select_stmt = (
            select(UserStats)
            .where(
                UserStats.user_id == user_id,
                UserStats.stat_definition_id == stat_definition_id,
            )
            .with_for_update()
        )
        res = await self._session.execute(select_stmt)
        existing = res.scalar_one()  # гарантированно существует
        return existing, False

    async def lock_for_update(self, stat_id: str) -> UserStats | None:
        """SELECT ... FOR UPDATE по UUID PK.

        Для сценариев когда caller уже знает stat_id (admin, ручной
        unfreeze, тесты). Для обычного increment/decrement
        используется get_or_create_for_update.
        """
        result = await self._session.execute(
            select(UserStats)
            .where(UserStats.id == stat_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    # ─── Reads ───────────────────────────────────────────────

    async def list_for_user(self, user_id: int) -> list[UserStats]:
        """WHERE user_id=:id ORDER BY value DESC.

        Используется в GET /character/me. ORDER DESC — топ-характеристики
        сначала.
        """
        result = await self._session.execute(
            select(UserStats)
            .where(UserStats.user_id == user_id)
            .order_by(UserStats.value.desc())
        )
        return list(result.scalars().all())

    async def iter_for_leaderboard(
        self,
        *,
        stat_definition_id: str,
        limit: int = 100,
    ) -> list[UserStats]:
        """ORDER BY value DESC, user_id ASC LIMIT 100.

        Детерминированный tiebreak по user_id (стабильное
        пагинирование + тесты не flaky). Фильтр LEFT membership
        — в CharacterService.get_leaderboard через дополнительный
        JOIN (репозиторий НЕ знает про membership).
        """
        result = await self._session.execute(
            select(UserStats)
            .where(UserStats.stat_definition_id == stat_definition_id)
            .order_by(UserStats.value.desc(), UserStats.user_id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def iter_for_freeze_cron(
        self,
        *,
        threshold_days: int = 30,
        batch: int = 1000,
    ) -> AsyncIterator[list[str]]:
        """Async generator: yield батчи stat.id для bulk_freeze.

        Drives partial index ix_user_stats_freeze_cron
        (`WHERE is_frozen=false AND last_checkin_at IS NOT NULL`)
        → cron scan быстрый.

        ⚠️ `last_checkin_at IS NULL` (юзер ни разу не чек-инился)
        НЕ входит в cron. Семантика из TZ v2 §4.4: «никогда не делал
        чек-ин» ≠ «заморожен за неактивность».

        ⚠️ OFFSET НЕ используется (recon Phase 3.2 fix 1). После
        bulk_freeze выпавшие строки исчезают из WHERE is_frozen=false;
        OFFSET пропустил бы ещё не замороженных candidate'ов. Подход:
        каждый SELECT делает top-N по тому же WHERE + стабильному
        ORDER BY — естественно «протаскивает» через весь набор.

        ORDER BY (stat_definition_id, last_checkin_at, id) —
        полностью детерминирован (id как финальный tiebreak), что
        даёт стабильную пагинацию при прочих равных.

        Одна короткая tx на батч (caller commit'ит между yield'ами).
        """
        threshold_dt = datetime.now(tz=timezone.utc) - timedelta(days=threshold_days)
        while True:
            stmt = (
                select(UserStats.id)
                .where(
                    UserStats.is_frozen.is_(False),
                    UserStats.last_checkin_at.is_not(None),
                    UserStats.last_checkin_at < threshold_dt,
                )
                .order_by(
                    UserStats.stat_definition_id,
                    UserStats.last_checkin_at,
                    UserStats.id,
                )
                .limit(batch)
            )
            result = await self._session.execute(stmt)
            ids = [str(row[0]) for row in result.all()]
            if not ids:
                return
            yield ids

    # ─── Writes (caller уже держит lock или имеет row) ────

    async def increment_value(
        self, stat: UserStats, gain: int
    ) -> UserStats:
        """stat.value += gain. Без commit.

        Defensive: gain <= 0 → no-op + WARN лог non_positive_increment.
        Корректное поведение ожидается только на positive gain
        (от check-in).
        """
        if gain <= 0:
            self._logger.warning(
                "non_positive_increment",
                extra={"stat_id": str(stat.id), "gain": gain},
            )
            return stat
        stat.value = stat.value + gain
        return stat

    async def decrement_with_floor(
        self, stat: UserStats, loss: int
    ) -> UserStats:
        """stat.value = GREATEST(0, stat.value - loss). Без commit.

        Floor на 0 (TZ v2 §3.3 + skill: «никогда не уходит в
        минус»). DB CHECK (value >= 0) — страховка на случай бага
        в Python-стороне.

        Defensive: loss <= 0 → no-op + WARN non_positive_decrement.
        При floor-в-ноль — INFO stat_decrement_floored_at_zero для
        diagnostics (видно что catch «съел» больше остатка).
        """
        if loss <= 0:
            self._logger.warning(
                "non_positive_decrement",
                extra={"stat_id": str(stat.id), "loss": loss},
            )
            return stat
        new_value = max(0, stat.value - loss)
        if new_value != stat.value - loss:
            self._logger.info(
                "stat_decrement_floored_at_zero",
                extra={
                    "stat_id": str(stat.id),
                    "old_value": stat.value,
                    "requested_loss": loss,
                    "actual_loss": stat.value - new_value,
                },
            )
        stat.value = new_value
        return stat

    async def freeze(self, stat: UserStats, reason_text: str) -> UserStats:
        """stat.is_frozen=true, stat.frozen_at=now(), frozen_reason_text=:r.

        ⚠️ НЕ идемпотентен (см. module docstring). Идемпотентность —
        за CharacterService.apply_freeze (Task 3.3) через guard
        ДО вызова repo.
        """
        stat.is_frozen = True
        stat.frozen_at = datetime.now(tz=timezone.utc)
        stat.frozen_reason_text = reason_text
        return stat

    async def unfreeze(self, stat: UserStats) -> UserStats:
        """Сбрасывает все 4 frozen-связанных поля:

        - is_frozen=false → характеристика снова активна;
        - frozen_at=NULL → UI перестаёт показывать дату заморозки;
        - frozen_reason_text=NULL → старый текст («Характеристика
          заморожена: нет чек-инов более 30 дней…») не висит в
          ответе API рядом с активной характеристикой (иначе легко
          показать лишнее);
        - last_checkin_at=now() → cron не заморозит юзера в
          ближайшие 30 дней (per TZ v2 §4).

        value сохраняется (не сбрасывается).
        """
        stat.is_frozen = False
        stat.frozen_at = None
        stat.frozen_reason_text = None
        stat.last_checkin_at = datetime.now(tz=timezone.utc)
        return stat

    async def touch_last_checkin(self, stat: UserStats) -> None:
        """stat.last_checkin_at = now(). Без commit.

        Вызывается из CharacterService.increment_on_checkin ВСЕГДА
        (даже если value не инкрементнулся и не был frozen) — чтобы
        cron не заморозил «активно чек-инящегося» юзера.
        """
        stat.last_checkin_at = datetime.now(tz=timezone.utc)

    async def bulk_freeze(
        self, stat_ids: list[str], reason_text: str
    ) -> int:
        """UPDATE user_stats SET is_frozen=true ... WHERE id = ANY(:ids)
        AND is_frozen = false.

        Cron batch-update. Двойная защита:
        1. Cron вызывает только на отфильтрованных is_frozen=false
           строках через iter_for_freeze_cron (нормальный путь).
        2. WHERE ... AND is_frozen.is_(False) в самом UPDATE — на
           случай worker-redelivery, дубля батча или нескольких
           cron-инстансов. rowcount тогда честно показывает именно
           впервые замороженные строки (для diagnostics в worker log).

        Один короткий UPDATE на batch (не per-row ORM) — эффективнее
        на больших объёмах.

        Использует Python-side now() (захваченный один раз для
        consistency внутри батча).
        """
        if not stat_ids:
            return 0
        now_utc = datetime.now(tz=timezone.utc)
        stmt = (
            update(UserStats)
            .where(
                UserStats.id.in_(stat_ids),
                UserStats.is_frozen.is_(False),
            )
            .values(
                is_frozen=True,
                frozen_at=now_utc,
                frozen_reason_text=reason_text,
            )
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)
