from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import PenaltyConfig, SuspiciousPairStatus
from app.core.logging import get_logger
from app.models.penalty import Penalty
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository


class SuspiciousPairsService:
    """Антифрод-эвристика для пар ловцов (docs/06 §7.3).

    Правило:
    - Если в окне `LOOKBACK_DAYS` дней один и тот же catcher ловит один и тот же
      violator ≥ SUSPICIOUS_ASYMMETRY_THRESHOLD раз, И violator ни разу не
      поймал catcher'а — это асимметрия → флаг "flagged".
    - Флагованные пары по-прежнему платят штраф (антифрод не отменяет правила
      клуба), но кэтчер-бонус не начисляется и пара не попадает в лидерборд.
    """

    LOOKBACK_DAYS = 30  # сезонный срез

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = SuspiciousPairsRepository(session)
        self._logger = get_logger("suspicious_pairs_service")

    async def evaluate_after_catch(
        self,
        *,
        catcher_membership_id: str,
        violator_membership_id: str,
        club_date: date,
    ) -> tuple[bool, str | None]:
        """Проверяет пару после успешного catch.

        Возвращает (suspicious, reason). Если suspicious=True — пара уже в
        suspicious_pairs в статусе 'flagged' (или 'banned').
        """
        # Уже забанена/флагована — повторно не считаем.
        existing = await self._repo.get(catcher_membership_id, violator_membership_id)
        if existing is not None and existing.status in (
            SuspiciousPairStatus.FLAGGED.value,
            SuspiciousPairStatus.BANNED.value,
        ):
            return True, existing.reason

        since = club_date - timedelta(days=self.LOOKBACK_DAYS)
        threshold = PenaltyConfig.SUSPICIOUS_ASYMMETRY_THRESHOLD

        a_to_b = await self._count_catches(
            catcher=catcher_membership_id, violator=violator_membership_id, since=since
        )
        b_to_a = await self._count_catches(
            catcher=violator_membership_id, violator=catcher_membership_id, since=since
        )

        # Асимметрия: один ловит другого N+ раз, в обратку 0.
        if a_to_b >= threshold and b_to_a == 0:
            reason = f"asymmetry:{a_to_b}>={threshold}, reverse={b_to_a}"
            await self._repo.flag(
                a=catcher_membership_id,
                b=violator_membership_id,
                reason=reason,
                status=SuspiciousPairStatus.FLAGGED.value,
            )
            self._logger.warning(
                "suspicious_pair_flagged",
                extra={
                    "catcher_membership_id": catcher_membership_id,
                    "violator_membership_id": violator_membership_id,
                    "a_to_b": a_to_b,
                    "b_to_a": b_to_a,
                },
            )
            return True, reason

        return False, None

    async def is_blocked_for_bonus(
        self, catcher_membership_id: str, violator_membership_id: str
    ) -> bool:
        """Пара в статусе 'flagged' или 'banned' → кэтчер-бонус НЕ начисляется."""
        pair = await self._repo.get(catcher_membership_id, violator_membership_id)
        return pair is not None and pair.status in (
            SuspiciousPairStatus.FLAGGED.value,
            SuspiciousPairStatus.BANNED.value,
        )

    async def _count_catches(
        self, *, catcher: str, violator: str, since: date
    ) -> int:
        # Penalty хранит (membership_id=violator, catcher_membership_id=catcher).
        result = await self._session.execute(
            select(func.count(Penalty.id)).where(
                Penalty.membership_id == violator,
                Penalty.catcher_membership_id == catcher,
                Penalty.date >= since,
            )
        )
        return int(result.scalar_one())
