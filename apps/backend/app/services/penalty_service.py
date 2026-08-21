from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    CheckinStatus,
    MembershipStatus,
    PenaltyConfig,
    PenaltyReason,
    TransactionType,
)
from app.core.exceptions import (
    CannotCatchSelfError,
    CatchWindowClosedError,
    HabitNotFoundError,
    MembershipNotActiveError,
    PenaltyAlreadyProcessedError,
    TooManyCatchAttemptsError,
)
from app.core.logging import get_logger
from app.core.utils import parse_rate_limit_spec
from app.models.penalty import Penalty
from app.models.transaction import Transaction
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository
from app.repositories.user_repository import UserRepository
from app.services.membership_service import MembershipService


class RedisPort(Protocol):
    async def incr_catch(self, catcher_user_id: int) -> int: ...


class PenaltyService:
    """Бизнес-логика штрафов.

    Одна операция = одна транзакция (вызывающий код в worker управляет commit'ом).

    Pravki-deposit-sse.md §Z-2: депозит живёт на users.deposit_balance (НЕ на
    membership). Списание идёт через user-level lock, что автоматически сериализует
    параллельные catch/topup этого юзера в любых клубах. После списания
    MembershipService.recompute_pause_status пересчитывает статусы всех
    ACTIVE/PAUSED membership'ов юзера (см. §Z-2.5).
    """

    def __init__(
        self,
        session: AsyncSession,
        habit_repo: HabitRepository,
        membership_repo: MembershipRepository,
        checkin_repo: CheckinRepository,
        suspicious_repo: SuspiciousPairsRepository,
        user_repo: UserRepository | None = None,
        membership_service: MembershipService | None = None,
        redis_port: RedisPort | None = None,
    ) -> None:
        self._session = session
        self._habit_repo = habit_repo
        self._membership_repo = membership_repo
        self._checkin_repo = checkin_repo
        self._suspicious_repo = suspicious_repo
        self._user_repo = user_repo or UserRepository(session)
        self._membership_service = membership_service or MembershipService(
            session=session,
            habit_repo=habit_repo,
            membership_repo=membership_repo,
            user_repo=self._user_repo,
        )
        self._redis = redis_port
        self._logger = get_logger("penalty_service")

    async def apply_catch(
        self,
        *,
        catcher_user_id: int,
        violator_membership_id: str,
        club_date,
        catcher_membership_id: str | None,
        now_utc: datetime | None = None,
    ) -> Penalty:
        if self._redis is not None:
            count = await self._redis.incr_catch(catcher_user_id)
            if count > parse_rate_limit_spec(PenaltyConfig.RATE_LIMIT_CATCH)[0]:
                raise TooManyCatchAttemptsError()

        # 1. Membership читаем БЕЗ лока — нужен только для habit_id и user_id.
        #    Pravki-deposit-sse.md §Z-2.4 / Q1: единственный лок — на user.
        #    Membership-лок не нужен, потому что:
        #      (a) депозит живёт на user (Pravki Z-2.1);
        #      (b) penalty-INSERT защищён UNIQUE (membership_id, date, reason)
        #          — гонку ловит Z-2.8 IntegrityError handler;
        #      (c) статус membership'а пересчитывается централизованно в
        #          recompute_pause_status ниже (читает свежее состояние через JOIN).
        violator = await self._membership_repo.get(violator_membership_id)
        if catcher_membership_id is not None and catcher_membership_id == violator_membership_id:
            raise CannotCatchSelfError()
        if violator.status != MembershipStatus.ACTIVE:
            # Первый re-check ACTIVE (Z-22): до лока, чтобы быстро отсеять
            # уже-Paused жертв без захвата блокировки.
            raise MembershipNotActiveError()

        # 2. SELECT FOR UPDATE на user'ов в ASC-порядке (deadlock-free).
        #    Pravki-catcher-deposit (Phase 1 Task 1.3, 2026-08-21): ловец
        #    теперь тоже блокируется (нужно для зачисления на его депозит).
        #    ASC-сортировка обязательна ДО первого lock_for_update, чтобы
        #    избежать крест-накрест deadlock'а между параллельными catch'ами
        #    (например, X ловит Y + Y ловит Z → если порядок локов разный
        #    в разных транзакциях, получаем deadlock).
        user_ids_to_lock: set[int] = {violator.user_id}
        if catcher_user_id != violator.user_id:
            # catcher_user_id уже в аргументах apply_catch (line 81). Если
            # catcher == violator — CannotCatchSelfError выше уже отсеял,
            # но защищаемся на случай edge-case (catcher_membership_id=None
            # но catcher_user_id == violator.user_id).
            user_ids_to_lock.add(catcher_user_id)
        for uid in sorted(user_ids_to_lock):
            await self._user_repo.lock_for_update(uid)
        violator_user = await self._user_repo.get(violator.user_id)
        assert violator_user is not None, "violator membership has no user"
        # Lookup catcher_user_obj (None если catcher_amount == 0 или catcher_membership_id is None).
        catcher_user_obj = None
        if catcher_user_id in user_ids_to_lock and catcher_user_id != violator.user_id:
            catcher_user_obj = await self._user_repo.get(catcher_user_id)

        # Pravki-paused-race-2026-08-14: defense-in-depth. Между SELECT
        # violator (шаг 1) и lock_for_update(user) (шаг 2) существует окно
        # гонки: параллельная транзакция (другой catch того же юзера,
        # или cron apply_window_expired) могла изменить membership.status
        # через recompute_pause_status и закоммитить. После user-lock'а
        # мы сериализованы с этими операциями (теперь мы видим свежие
        # данные на user-row), но `violator` объект — из identity map
        # SQLAlchemy, загружен ДО лока. Без refresh'а мы бы использовали
        # staled статус. Финансово amount-guard ниже ловит (если
        # balance уже обнулён, то min(penalty, 0)=0 → reject). Но
        # семантически inconsistent: Penalty создаётся для жертвы с
        # membership.status != ACTIVE → UI жертвы может мигнуть
        # "поймали" → "уже на паузе".
        # Второй re-check ACTIVE (Z-22): под локом, после refresh'а
        # violator. ОБЯЗАТЕЛЬНО оставить на месте (Дмитрий, 2026-08-21).
        await self._session.refresh(violator)
        if violator.status != MembershipStatus.ACTIVE:
            raise MembershipNotActiveError()
        # Pravki-subscription-2026-08-17 §Z-22 (canonical #6): если подписка
        # жертвы истекла, ловить бессмысленно — после renew подписки через
        # /payments/subscribe membership реактивируется (recompute_pause_status
        # воскрешает из PAUSED), но старый catch остаётся. Защита: reject с тем
        # же MembershipNotActiveError (UI уже умеет мапить, см. CATCH_ERROR_LABELS
        # в MembersPage.tsx). Сравнение по club_date (Q2): без grace period.
        if violator.subscription_until is not None and violator.subscription_until < club_date:
            raise MembershipNotActiveError()

        habit = await self._habit_repo.get(str(violator.habit_id))
        if habit is None:
            raise HabitNotFoundError()

        # Pravki-manual-catch-2026-08-18 §Шаг 2 v2: race-free resolution.
        #
        # now_utc резолвится ПОД lock'ом, прямо перед проверкой. Если
        # now_utc не передан (прод-обёртка worker'а) — берём datetime.now(UTC)
        # здесь. Параметр остаётся только для тестов с замороженным временем.
        #
        # Race без этого фикса: ловец нажал «Поймать» в последнюю допустимую
        # секунду окна → time-check прошёл (now=T0) → ждёт user-lock пока
        # другая транзакция (например, topup) завершится → lock window
        # уже закрылся → lock освободился → старый код всё ещё создавал
        # Penalty после границы. Теперь now_utc capture = момент после
        # lock'а, штраф после границы невозможен.
        if now_utc is None:
            now_utc = datetime.now(tz=timezone.utc)

        # Catch window — единая серверная проверка. Ловить можно ТОЛЬКО
        # после закрытия check-in окна и ДО закрытия catch window. UI
        # скрывает кнопку по `is_within_catch_window` в /members, но эта
        # проверка здесь — единственный авторитет (защита от прямого
        # API-вызова в обход UI, гонок, устаревших клиентов).
        if not habit.is_within_catch_window(now_utc, club_date):
            raise CatchWindowClosedError()

        # Идемпотентность: если за день есть ЛЮБАЯ Penalty
        # (CAUGHT / WINDOW_CLOSED_NO_CATCH / WAIVED_UNABLE_TO_PAY) —
        # повторный catch для того же дня отвергается.
        #
        # Это закрывает несколько дыр (Pravki-no-deposit-waived-marker,
        # разведка 2026-08-16):
        # 1) После WAIVED_UNABLE_TO_PAY (см. apply_window_expired WAIVED-ветка
        #    и mark_waived_unable_to_pay для PAUSED юзеров) юзер топит депозит →
        #    apply_catch должен отвергаться, иначе списываются деньги за
        #    уже прошедший день.
        # 2) Бонус — прямой POST /catch поверх существующего
        #    WINDOW_CLOSED_NO_CATCH теперь корректно отвергается.
        #    Раньше фильтр `reason == CAUGHT` пропускал, и UNIQUE-индекс
        #    uq_penalty_per_day_reason тоже не срабатывал (reason
        #    отличался). Это позволяло обойти can_catch=False через прямой
        #    запрос в обход UI и списать штраф дважды.
        # 3) Регрессия — повторный catch поверх существующего CAUGHT.
        #    Это было защищено раньше явно через reason-фильтр, теперь
        #    покрывается общим условием.
        existing = await self._session.execute(
            Penalty.__table__.select().where(
                Penalty.membership_id == violator_membership_id,
                Penalty.date == club_date,
            )
        )
        if existing.first() is not None:
            raise PenaltyAlreadyProcessedError()

        # === Финансы (Pravki-catcher-deposit, Phase 1 Task 1.3) ===
        #
        # Клэмп ДО: списываем фактически доступное (не больше номинала и
        # не больше текущего депозита). Это исторически сложилось и
        # означает, что Penalty.amount в БД = фактически списанное.
        # CHECK ck_penalties_amount_equals_sum (миграция 017) гарантирует
        # amount = catcher_amount + fund_share.
        amount = min(habit.penalty_amount, violator_user.deposit_balance)
        if amount <= 0:
            # Депозит исчерпан. Статус membership'а пересчитается в recompute ниже
            # (PAUSED если депозит < penalty этого клуба, иначе ACTIVE).
            # Pravki правка B: единый централизованный пересчёт, никаких
            # построчных `status = PAUSED` в этом методе.
            raise PenaltyAlreadyProcessedError("deposit_exhausted", code="deposit_exhausted")

        # Разделение штрафа на 2 части:
        # - catcher_amount — ловцу на депозит (ОТ amount, не от номинала!)
        #   Если считать от номинала: при клэмпе (deposit < penalty) мы бы
        #   раздали больше, чем списали — расхождение баланса.
        # - fund_share (= amount - catcher_amount) — в призовой фонд клуба.
        # Если catcher_amount_kopecks == 0 → всё в фонд (старое поведение).
        # Если catcher_amount_kopecks >= amount → всё ловцу, фонд = 0.
        catcher_amount = min(habit.catcher_amount_kopecks, amount)
        fund_share_amount = amount - catcher_amount

        # Suspicious pair (variant A, ПДмитрий 2026-08-21): деньги НЕ блокируются
        # (сговор финансово невыгоден в текущей модели). Только метка для
        # лидерборда — лидерборд фильтрует flagged пары из метрик catches_count.
        is_suspicious_pair = await self._suspicious_repo.lookup_flagged(
            catcher_membership_id, violator_membership_id
        )

        # 1. Списание с депозита нарушителя (одна сумма — amount).
        violator_user.deposit_balance -= amount

        # 2. Зачисление ловцу (если есть доля и catcher_user_obj загружен).
        #    Под тем же user-lock'ом — деньги движутся атомарно.
        if catcher_amount > 0 and catcher_user_obj is not None:
            catcher_user_obj.deposit_balance += catcher_amount

        # 3. Доля в призовой фонд клуба (fund_share_amount, не amount).
        if fund_share_amount > 0:
            await self._habit_repo.add_to_prize_pool(str(habit.id), fund_share_amount)

        # 4. Penalty insert.
        #    Pravki-catcher-deposit: разделение на fund_share/catcher_amount,
        #    флаг is_suspicious_pair для лидерборда. catcher_membership_id
        #    ВСЕГДА пишем (variant A — деньги идут даже для suspicious пар,
        #    просто лидерборд скрывает).
        penalty = Penalty(
            id=str(uuid4()),
            membership_id=violator_membership_id,
            catcher_membership_id=catcher_membership_id,
            amount=amount,
            fund_share=fund_share_amount,
            catcher_amount=catcher_amount,
            is_suspicious_pair=is_suspicious_pair,
            reason=PenaltyReason.CAUGHT,
            date=club_date,
        )
        self._session.add(penalty)

        # Flush penalty ПЕРЕД добавлением transaction — Postgres FK
        # `transactions.related_penalty_id → penalties.id` проверяется
        # per-statement, и INSERT transaction в той же flush() видит ещё
        # не зафиксированный penalty → ForeignKeyViolationError.
        # Сначала flush'им penalty (INSERT + RETURNING), затем transaction.
        await self._session.flush()

        # Pravki-bug-fixes §Z-21 (caught badge): пишем Checkin(status='caught')
        # сразу после penalty, чтобы /members (can_catch=False) и /today
        # (статус='Пойман') отображали правильное состояние без перезагрузки.
        # ON CONFLICT DO UPDATE: если Checkin уже есть (status='missed' от cron
        # apply_window_expired или status='done' от гонки — юзер успел
        # отметиться после поимки), перезаписываем на 'caught'. proof_message_id
        # сохраняется если был (для истории).
        await self._checkin_repo.upsert_status(
            membership_id=str(violator_membership_id),
            on_date=club_date,
            status=CheckinStatus.CAUGHT,
        )

        # 5. Transaction для нарушителя (штраф, отрицательная сумма).
        transaction = Transaction(
            id=str(uuid4()),
            user_id=violator.user_id,
            type=TransactionType.PENALTY.value,
            amount=-amount,
            balance_after=violator_user.deposit_balance,
            related_penalty_id=penalty.id,
            related_membership_id=violator_membership_id,
        )
        self._session.add(transaction)

        # 6. Transaction для ловца (зачисление доли, положительная сумма).
        if catcher_amount > 0 and catcher_user_obj is not None:
            catcher_deposit_tx = Transaction(
                id=str(uuid4()),
                user_id=catcher_user_obj.id,
                type=TransactionType.CATCHER_DEPOSIT.value,
                amount=+catcher_amount,
                balance_after=catcher_user_obj.deposit_balance,
                related_penalty_id=penalty.id,
                related_membership_id=catcher_membership_id,
            )
            self._session.add(catcher_deposit_tx)

        # Единственный источник статуса membership при изменении депозита —
        # централизованный recompute_pause_status (Pravki Z-2.5, правка B).
        await self._membership_service.recompute_pause_status(violator.user_id)

        await self._session.flush()

        self._logger.info(
            "penalty_caught",
            extra={
                "violator_membership_id": violator_membership_id,
                "catcher_membership_id": catcher_membership_id,
                "amount": amount,
                "catcher_amount": catcher_amount,
                "fund_share_amount": fund_share_amount,
                "is_suspicious_pair": is_suspicious_pair,
                "habit_id": str(habit.id),
                "club_date": str(club_date),
                "violator_deposit_after": violator_user.deposit_balance,
            },
        )
        return penalty

    async def apply_window_expired(
        self, *, violator_membership_id: str, club_date
    ) -> Penalty | None:
        """DEPRECATED 2026-08-18 (Pravki-manual-catch-2026-08-18 §Шаг 3).

        Авто-списание за пропуск без улова отключено. Штраф возможен ТОЛЬКО
        при ручной поимке (см. `apply_catch`). Старые Celery-сообщения или
        retry из брокера могут вызвать этот метод — возвращаем None без
        raise'ов чтобы worker не падал. Метод сохранён для обратной
        совместимости; **удалить** в cleanup-коммите после того как
        подтверждено отсутствие задач в брокере.

        Защита от поздней ловли теперь через `Habit.is_within_catch_window`
        в `apply_catch` (Шаг 2) — `CatchWindowClosedError` после границы.
        """
        self._logger.warning(
            "deprecated_auto_penalty_skipped",
            extra={
                "method": "apply_window_expired",
                "violator_membership_id": violator_membership_id,
                "club_date": str(club_date),
            },
        )
        return None

    async def mark_waived_unable_to_pay(
        self,
        *,
        violator_membership_id: str,
        club_date,
    ) -> Penalty | None:
        """DEPRECATED 2026-08-18 (Pravki-manual-catch-2026-08-18 §Шаг 3).

        Маркер WAIVED_UNABLE_TO_PAY для PAUSED-юзеров больше не пишется.
        Защита от поздней ловли теперь через `Habit.is_within_catch_window`
        в `apply_catch` (Шаг 2). Старые Celery-сообщения или retry из
        брокера могут вызвать этот метод — возвращаем None без raise'ов
        чтобы worker не падал. Метод сохранён для обратной совместимости;
        **удалить** в cleanup-коммите после подтверждения отсутствия задач.
        """
        self._logger.warning(
            "deprecated_auto_penalty_skipped",
            extra={
                "method": "mark_waived_unable_to_pay",
                "violator_membership_id": violator_membership_id,
                "club_date": str(club_date),
            },
        )
        return None
