from __future__ import annotations

from typing import Any

from app.core.constants import CheckinRejectCode


class DomainError(Exception):
    status_code: int = 400
    code: str = "domain_error"

    def __init__(
        self,
        message: str | None = None,
        code: str | None = None,
        **extras: Any,
    ) -> None:
        """Базовый доменный exception.

        Args:
            message: текст для логирования / message-поля в JSON.
            code: машинный код ошибки (по умолчанию = class.code).
            **extras: дополнительные поля, которые попадут в JSON-ответ.
                Например, InsufficientDepositError требует required_kopecks /
                current_kopecks / club_penalty_kopecks для UI-модала
                «Недостаточно средств». Глобальный handler в main.py
                мерджит extras в response content под именами этих полей.
        """
        super().__init__(message or self.code)
        self.message = message or self.code
        if code is not None:
            self.code = code
        self.extras = extras


class InvalidInitDataError(DomainError):
    status_code = 401
    code = "invalid_init_data"


class InitDataExpiredError(DomainError):
    status_code = 401
    code = "init_data_expired"


class MissingInitDataError(DomainError):
    status_code = 401
    code = "missing_init_data"


class MissingServiceTokenError(DomainError):
    status_code = 401
    code = "missing_service_token"


class InvalidServiceTokenError(DomainError):
    status_code = 401
    code = "invalid_service_token"


class ServiceTokenExpiredError(DomainError):
    status_code = 401
    code = "service_token_expired"


class MembershipNotFoundError(DomainError):
    status_code = 404
    code = CheckinRejectCode.MEMBERSHIP_NOT_FOUND.value


class MembershipNotActiveError(DomainError):
    status_code = 400
    code = CheckinRejectCode.MEMBERSHIP_NOT_ACTIVE.value


class SseStreamForbiddenError(DomainError):
    """403 при выдаче SSE-токена для не-члена клуба.

    Отдельное исключение вместо MembershipNotActiveError (который 400) —
    семантика другая: не "плохой запрос", а "не имеешь права на этот ресурс".
    code = "membership_not_active" оставлен для совместимости клиентской логики.
    """

    status_code = 403
    code = "membership_not_active"


class SseNotConfiguredError(DomainError):
    """503: SSE_TOKEN_SECRET не задан в env на сервере.

    Ops-проблема (мисконфиг), не баг юзера. Не 500, чтобы клиент не
    делал бессмысленных retry — пусть покажет "обновите позже".
    """

    status_code = 503
    code = "sse_not_configured"


class TooManySseConnectionsError(DomainError):
    """429: юзер превысил лимит одновременных SSE-соединений.

    Защита от DoS через replayable SSE-токены (TTL 60с, не одноразовые).
    Лимит и стратегия — см. app.services.sse.connection_limiter.
    """

    status_code = 429
    code = "too_many_sse_connections"


class HabitNotFoundError(DomainError):
    status_code = 404
    code = CheckinRejectCode.HABIT_NOT_FOUND.value


class CheckinWindowClosedError(DomainError):
    status_code = 400
    code = CheckinRejectCode.WINDOW_CLOSED.value


class CheckinAlreadyExistsError(DomainError):
    status_code = 409
    code = CheckinRejectCode.ALREADY_CHECKED_IN.value


# Pravki-bug-fixes §Z-21 (Item 4): defense-in-depth на server-side.
# Если у membership уже есть Penalty за сегодня (CAUGHT через apply_catch
# ИЛИ WINDOW_CLOSED_NO_CATCH через cron apply_window_expired), чек-ин
# невозможен — окно дня закрыто со штрафом. Бот в pre-filter должен
# отсеять раньше (state.caught_today), но это fallback на race / bypass /
# прямой вызов internal API / старую версию бота.
#
# 422 (Unprocessable Entity): запрос синтаксически валидный, но
# состояние ресурса не позволяет выполнить операцию (semantically
# точнее чем 409 Conflict).
class CheckinAlreadyCaughtError(DomainError):
    status_code = 422
    code = CheckinRejectCode.ALREADY_CAUGHT.value


class CheckinInvalidProofError(DomainError):
    status_code = 400
    code = "checkin_invalid_proof"


# Pravki-manual-catch-2026-08-18 §Шаг 2: ловля за club_date после закрытия
# catch window. 422: запрос синтаксически валидный, но состояние ресурса
# (время) не позволяет выполнить операцию. Семантически точнее чем 409.
#
# Логика окна — в Habit.is_within_catch_window(now_utc, club_date):
# (checkin_window_end, catch_window_end] в UTC.
class CatchWindowClosedError(DomainError):
    status_code = 422
    code = "catch_window_closed"


class PenaltyAlreadyProcessedError(DomainError):
    status_code = 409
    code = "penalty_already_processed"


class CannotCatchSelfError(DomainError):
    status_code = 400
    code = "cannot_catch_self"


class TooManyCatchAttemptsError(DomainError):
    status_code = 429
    code = "too_many_catch_attempts"


class InvalidPrizeRulesError(DomainError):
    status_code = 400
    code = "invalid_prize_rules"


class NotOwnerError(DomainError):
    status_code = 403
    code = "not_owner"


class HabitValidationError(DomainError):
    status_code = 400
    code = "habit_validation"


class HabitInactiveError(DomainError):
    status_code = 409
    code = "habit_inactive"


class HabitArchivedError(DomainError):
    status_code = 404
    code = "habit_archived"


class HabitMemberLimitReachedError(DomainError):
    status_code = 409
    code = "habit_member_limit_reached"


class InvalidTopicLinkError(DomainError):
    status_code = 422
    code = "invalid_topic_link"


class HabitTopicDuplicateError(DomainError):
    status_code = 409
    code = "habit_topic_duplicate"


class HabitTopicMismatchError(DomainError):
    status_code = 400
    code = "habit_topic_chat_mismatch"


class CheckinWrongTopicError(DomainError):
    """Сообщение пришло не из того топика, что привязан к клубу."""

    status_code = 422
    code = CheckinRejectCode.WRONG_TOPIC.value


class PhotoUnavailableError(DomainError):
    """Юзер не имеет photo_file_id (нет аватарки в Telegram или worker не подтянул)."""
    status_code = 404
    code = "photo_unavailable"


class TelegramUnavailableError(DomainError):
    """Telegram Bot API недоступен (timeout / 5xx / невалидный file_id)."""
    status_code = 502
    code = "telegram_unavailable"


class InsufficientDepositError(DomainError):
    """403: user.deposit_balance < habit.penalty_amount при join.

    Pravki-deposit-sse.md §Z-3.2: единственный порог 1× penalty. Возвращаем
    client'у все три поля, чтобы UI мог показать модал с конкретными суммами:
    - required_kopecks: сколько нужно для вступления (= penalty клуба).
    - current_kopecks: сколько сейчас на депозите.
    - club_penalty_kopecks: штраф конкретного клуба (часто = required_kopecks,
      но если хотим — будущее может разрешать partial topup с cap'ом).
    """
    status_code = 403
    code = "insufficient_deposit"

    def __init__(
        self,
        *,
        required_kopecks: int,
        current_kopecks: int,
        club_penalty_kopecks: int,
    ) -> None:
        super().__init__(
            self.code,
            required_kopecks=required_kopecks,
            current_kopecks=current_kopecks,
            club_penalty_kopecks=club_penalty_kopecks,
        )


class CheckinJoinedLateError(DomainError):
    """422: пользователь вступил в клуб сегодня ПОСЛЕ закрытия checkin_window.

    Pravki-bug-fixes §Z-19 (joiner-late protection): защита от попытки чек-ина
    в день вступления. Используется и в worker `process_checkin` (симметричная
    серверная защита против race / старого бота / прямого вызова), и в
    CheckinService.get_today_status (для статуса в TodayResponse).

    code = CheckinRejectCode.JOINED_LATE.value — бот мапит его в дружественное
    сообщение с временем окна клуба (см. apps/bot/bot/handlers/checkin_texts.py).
    """
    status_code = 422
    code = CheckinRejectCode.JOINED_LATE.value


# Pravki §Z-22 (Step 3, hole #3): сплит MembershipNotActiveError.
# До этого момента checkin_service.py бросал единый MembershipNotActiveError
# для status in (paused, left), и бот не мог различить "пополни депозит" vs
# "rejoin" — был один generic текст. Теперь:
#   - MembershipPaused: deposit < penalty этого клуба (автопауза через
#     recompute_pause_status). Recovery: topup deposit в мини-аппе.
#   - MembershipLeft: юзер сам вышел из клуба (явное действие, нельзя
#     восстановить через topup — нужно заново Subscribe/Join).
#
# MembershipNotActiveError ОСТАВЛЯЕМ для catch-flow (см. MembersPage.tsx
# CATCH_ERROR_LABELS) — там сплит на paused/left не нужен (catch-flow
# работает только с активными, но если попадёт non-active — generic).
class CheckinMembershipPausedError(DomainError):
    """422: membership.status == PAUSED (deposit < penalty этого клуба)."""

    status_code = 422
    code = CheckinRejectCode.MEMBERSHIP_PAUSED.value


class CheckinSubscriptionExpiredError(DomainError):
    """422: membership.subscription_until < habit.club_date(now_utc).

    Pravki-subscription-2026-08-17 §Z-22 (canonical #6): подписка истекла,
    пользователь должен продлить участие через POST /api/v1/payments/subscribe.
    Ставится ПЕРЕД MEMBERSHIP_PAUSED (#7), потому что "пополни депозит" не
    лечит истёкшую подписку — пользователь зациклится на ошибке PAUSED после
    topup. Реакция "продли подписку" лечит ОБЕ причины: подписку и
    (через recompute_pause_status) возможный PAUSED-статус.

    Q2 (Pravki-subscription-2026-08-17): сравнение по club_date в TZ клуба,
    без grace period. subscription_until == club_date → ещё валиден (последний день).
    """

    status_code = 422
    code = CheckinRejectCode.SUBSCRIPTION_EXPIRED.value


class CheckinMembershipLeftError(DomainError):
    """422: membership.status == LEFT (юзер вышел из клуба)."""

    status_code = 422
    code = CheckinRejectCode.MEMBERSHIP_LEFT.value


class InsufficientDepositChoiceError(DomainError):
    """422: deposit_amount_kopecks < habit.penalty_amount при subscribe_and_join.

    Pravki-subscribe-and-join.md §Z-13: пользователь выбрал в JoinPayModal
    сумму депозита, которая НЕ покрывает штраф клуба. Это UI-баг (модалка
    фильтрует пресеты, но пользователь мог ввести «свою сумму» ниже порога).
    Отдельное 422 (не 403 как у InsufficientDepositError) — потому что здесь
    запрос вообще не выполнился, а не «отказ из-за текущего баланса».
    """
    status_code = 422
    code = "insufficient_deposit_choice"

    def __init__(
        self,
        *,
        required_kopecks: int,
        chosen_kopecks: int,
    ) -> None:
        super().__init__(
            self.code,
            required_kopecks=required_kopecks,
            chosen_kopecks=chosen_kopecks,
        )


class SubscriptionRequiredError(DomainError):
    """422: subscription_accepted=False при отсутствии активной подписки.

    Pravki-subscribe-and-join.md §Z-13.1: единственная запрещённая комбинация
    в матрице server-side gate. UI адаптирует модалку (прячет чекбокс если
    подписка активна), но если кто-то шлёт False в обход — 422.
    """
    status_code = 422
    code = "subscription_required"


class AlreadyActiveError(DomainError):
    """409: membership.status == ACTIVE — повторный /subscribe для клуба, где
    юзер уже состоит.

    Pravki-subscribe-and-join.md §Z-13 шаг 3c: вместо silent success возвращаем
    явный код, чтобы UI мог показать «Ты уже в клубе, обнови страницу».
    Идемпотентно для случаев гонки (parallel POST → второй видит уже ACTIVE).
    """
    status_code = 409
    code = "already_active"


class IdempotencyConflictError(DomainError):
    """400: тот же idempotency_key использован с другими параметрами.

    Pravki-subscribe-and-join.md §Z-14.1: клиент должен генерировать uuid4
    один раз и ретраить с тем же ключом. Если шлёт тот же ключ но другие
    параметры (habit_id, deposit_amount_kopecks) — это явная ошибка клиента.
    На практике не должно случаться если uuid4 генерится правильно.
    """
    status_code = 400
    code = "idempotency_conflict"