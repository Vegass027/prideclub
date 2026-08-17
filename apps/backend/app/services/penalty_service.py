from __future__ import annotations

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
            raise MembershipNotActiveError()

        # 2. SELECT FOR UPDATE на user. Сериализует все параллельные
        #    catch/topup этого юзера в любых клубах (Z-2.4).
        violator_user = await self._user_repo.lock_for_update(violator.user_id)
        assert violator_user is not None, "violator membership has no user"

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
        # Решение: перечитать violator под user-lock'ом + повторная
        # проверка. Один additional SELECT из БД, ~1ms overhead.
        await self._session.refresh(violator)
        if violator.status != MembershipStatus.ACTIVE:
            raise MembershipNotActiveError()

        habit = await self._habit_repo.get(str(violator.habit_id))
        if habit is None:
            raise HabitNotFoundError()

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

        # Списываем депозит (но не ниже 0).
        amount = min(habit.penalty_amount, violator_user.deposit_balance)
        if amount <= 0:
            # Депозит исчерпан. Статус membership'а пересчитается в recompute ниже
            # (PAUSED если депозит < penalty этого клуба, иначе ACTIVE).
            # Pravki правка B: единый централизованный пересчёт, никаких
            # построчных `status = PAUSED` в этом методе.
            raise PenaltyAlreadyProcessedError("deposit_exhausted", code="deposit_exhausted")

        violator_user.deposit_balance -= amount
        await self._habit_repo.add_to_prize_pool(str(habit.id), amount)

        # Применяется ли кэтчер-бонус — отдельная проверка suspicious_pairs (см. apply_catch_bonus).
        grant_catcher_bonus = not await self._suspicious_repo.lookup_flagged(
            catcher_membership_id, violator_membership_id
        )

        penalty = Penalty(
            id=str(uuid4()),
            membership_id=violator_membership_id,
            catcher_membership_id=catcher_membership_id if grant_catcher_bonus else None,
            amount=amount,
            fund_share=amount,
            catcher_bonus_points=PenaltyConfig.CATCHER_BONUS_POINTS if grant_catcher_bonus else 0,
            reason=PenaltyReason.CAUGHT,
            date=club_date,
            bonus_applied=False,
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
                "habit_id": str(habit.id),
                "club_date": str(club_date),
                "user_deposit_after": violator_user.deposit_balance,
            },
        )
        return penalty

    async def apply_window_expired(
        self, *, violator_membership_id: str, club_date
    ) -> Penalty | None:
        """Штраф за пропуск без улова: cron close_catch_window.

        Идемпотентность обеспечивается INSERT ON CONFLICT DO NOTHING через
        уникальный индекс (membership_id, date, reason).

        Тот же паттерн, что и apply_catch: membership читается без лока,
        лок только на user. Membership-лок не нужен (Z-2.4 / Q1).
        """
        violator = await self._membership_repo.get(violator_membership_id)
        if violator.status != MembershipStatus.ACTIVE:
            return None

        violator_user = await self._user_repo.lock_for_update(violator.user_id)
        assert violator_user is not None, "violator membership has no user"

        habit = await self._habit_repo.get(str(violator.habit_id))
        if habit is None:
            return None

        # Если уже есть штраф за сегодня — идемпотентный no-op.
        existing = await self._session.execute(
            Penalty.__table__.select().where(
                Penalty.membership_id == violator_membership_id,
                Penalty.date == club_date,
            )
        )
        if existing.first() is not None:
            return None

        amount = min(habit.penalty_amount, violator_user.deposit_balance)
        if amount <= 0:
            # Pravki-no-deposit-waived-marker (разведка 2026-08-16): депозит
            # пуст — списывать нечего. Пишем маркер WAIVED_UNABLE_TO_PAY (amount=0),
            # чтобы день был помечен в БД как «уже разрешённый». Без этого
            # apply_catch после topup юзера успешно списывал бы деньги за уже
            # прошедший день → финансовая дыра.
            #
            # NOTE: основной путь для PAUSED юзеров — через отдельный метод
            # mark_waived_unable_to_pay, вызываемый из close_catch_window для
            # status=PAUSED. Эта ветка apply_window_expired пишет WAIVED
            # только для редкого ACTIVE+deposit=0 (между списанием штрафа и
            # recompute_pause_status). Контракт маркера единый (reason=WAIVED_UNABLE_TO_PAY,
            # amount=0), идемпотентность обоих путей покрыта existing-check
            # в начале apply_window_expired (строки 244-252).
            #
            # Маркер НЕ финансовое событие, поэтому:
            #   - Checkin НЕ пишем (юзер не «пропустил», у него просто не было денег)
            #   - Transaction НЕ создаём (amount=0 не двигает баланс)
            #   - recompute_pause_status НЕ вызываем (balance не менялся,
            #     статус и так консистентен)
            # existing-check выше (строки 244-252) ловит дубль при повторном
            # запуске cron'а для того же (membership, date) → INSERT с теми же
            # (membership_id, date, reason) даст UNIQUE-конфликт в проде,
            # а fake-сессия в тестах увидит существующую запись через execute().
            waived = Penalty(
                id=str(uuid4()),
                membership_id=violator_membership_id,
                catcher_membership_id=None,
                amount=0,
                fund_share=0,
                catcher_bonus_points=0,
                reason=PenaltyReason.WAIVED_UNABLE_TO_PAY,
                date=club_date,
                bonus_applied=False,
            )
            self._session.add(waived)
            await self._session.flush()
            self._logger.info(
                "penalty_window_expired_waived",
                extra={
                    "violator_membership_id": violator_membership_id,
                    "amount": 0,
                    "habit_id": str(habit.id),
                    "club_date": str(club_date),
                    "user_deposit_at_close": violator_user.deposit_balance,
                },
            )
            return None

        violator_user.deposit_balance -= amount
        await self._habit_repo.add_to_prize_pool(str(habit.id), amount)

        penalty = Penalty(
            id=str(uuid4()),
            membership_id=violator_membership_id,
            catcher_membership_id=None,
            amount=amount,
            fund_share=amount,
            catcher_bonus_points=0,
            reason=PenaltyReason.WINDOW_CLOSED_NO_CATCH,
            date=club_date,
            bonus_applied=False,
        )
        self._session.add(penalty)
        # Flush перед transaction — см. apply_catch().
        await self._session.flush()

        # Pravki-bug-fixes §Z-21 (missed badge): пишем Checkin(status='missed')
        # сразу после penalty, чтобы /members и /today отображали правильный
        # статус. ON CONFLICT DO UPDATE: если Checkin уже был с другим
        # статусом — перезаписываем. Race с apply_catch невозможен потому что
        # оба метода проверяют existing Penalty первым делом (idempotent guard).
        await self._checkin_repo.upsert_status(
            membership_id=str(violator_membership_id),
            on_date=club_date,
            status=CheckinStatus.MISSED,
        )

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

        # Единственный источник статуса — recompute_pause_status (Z-2.5, правка B).
        await self._membership_service.recompute_pause_status(violator.user_id)

        await self._session.flush()
        self._logger.info(
            "penalty_window_expired",
            extra={
                "violator_membership_id": violator_membership_id,
                "amount": amount,
                "habit_id": str(habit.id),
                "user_deposit_after": violator_user.deposit_balance,
            },
        )
        return penalty

    async def mark_waived_unable_to_pay(
        self,
        *,
        violator_membership_id: str,
        club_date,
    ) -> Penalty | None:
        """Записать маркер WAIVED_UNABLE_TO_PAY для PAUSED юзера без чек-ина.

        Pravki-no-deposit-waived-marker (разведка 2026-08-16, коммит A 2026-08-17).
        Вызывается из close_catch_window._close_for_habit для membership со
        status=PAUSED (авто-пауза через recompute_pause_status при deposit < penalty).

        Контракт:
        - Только для status=PAUSED. Иначе return None (defensive — ACTIVE
          идёт через apply_window_expired, LEFT skip'ается явно).
        - Идемпотентность: existing Penalty за (membership_id, date) →
          return None. Защищает от дублей при retry cron'а + от гонки
          с manual catch (если кто-то успел поймать юзера между моментом
          когда PAUSED определился и моментом когда мы пишем маркер).
        - Маркер НЕ финансовое событие:
          * Checkin НЕ пишем (юзер не «пропустил», у него пауза)
          * Transaction НЕ создаём (amount=0 не двигает баланс)
          * recompute_pause_status НЕ вызываем (balance не менялся)
        - Возвращает созданную Penalty для единообразия с apply_window_expired,
          но caller (close_catch_window) использует только truthy-check
          для инкремента waived-счётчика.

        Отличие от apply_window_expired WAIVED-ветки:
        apply_window_expired WAIVED пишет маркер для ACTIVE+deposit=0
        (крайне редкий случай между списанием и recompute).
        Эта функция пишет маркер для PAUSED (нормальный основной случай).
        """
        violator = await self._membership_repo.get(violator_membership_id)
        if violator is None:
            return None
        if violator.status != MembershipStatus.PAUSED:
            # Defensive: ACTIVE/LEFT — не наш случай.
            # ACTIVE идёт через apply_window_expired, LEFT skip'ается в выве.
            return None

        # Идемпотентность: existing Penalty за день = уже разрешённый день.
        existing = await self._session.execute(
            Penalty.__table__.select().where(
                Penalty.membership_id == violator_membership_id,
                Penalty.date == club_date,
            )
        )
        if existing.first() is not None:
            return None

        # Читаем habit для логирования (id пригодится в event'е ниже).
        habit = await self._habit_repo.get(str(violator.habit_id))
        # user — для текущего deposit_balance в логе. Если user_repo не
        # инжектирован (некоторые тесты конструируют PenaltyService без
        # user_repo) — пропускаем поле в логе.
        user_deposit_at_close: int | None = None
        if self._user_repo is not None:
            user = await self._user_repo.get(violator.user_id)
            if user is not None:
                user_deposit_at_close = user.deposit_balance

        waived = Penalty(
            id=str(uuid4()),
            membership_id=violator_membership_id,
            catcher_membership_id=None,
            amount=0,
            fund_share=0,
            catcher_bonus_points=0,
            reason=PenaltyReason.WAIVED_UNABLE_TO_PAY,
            date=club_date,
            bonus_applied=False,
        )
        self._session.add(waived)
        await self._session.flush()
        self._logger.info(
            "penalty_window_expired_waived",
            extra={
                "violator_membership_id": violator_membership_id,
                "amount": 0,
                "habit_id": str(violator.habit_id),
                "club_date": str(club_date),
                "user_deposit_at_close": user_deposit_at_close,
                "via": "mark_waived_unable_to_pay",
            },
        )
        return waived
