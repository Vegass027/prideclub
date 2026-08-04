from __future__ import annotations


class DomainError(Exception):
    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, message: str | None = None, code: str | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        if code is not None:
            self.code = code


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
    code = "membership_not_found"


class MembershipNotActiveError(DomainError):
    status_code = 400
    code = "membership_not_active"


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


class HabitNotFoundError(DomainError):
    status_code = 404
    code = "habit_not_found"


class CheckinWindowClosedError(DomainError):
    status_code = 400
    code = "checkin_window_closed"


class CheckinAlreadyExistsError(DomainError):
    status_code = 409
    code = "checkin_already_exists"


class CheckinInvalidProofError(DomainError):
    status_code = 400
    code = "checkin_invalid_proof"


class PenaltyAlreadyProcessedError(DomainError):
    status_code = 409
    code = "penalty_already_processed"


class CannotCatchSelfError(DomainError):
    status_code = 400
    code = "cannot_catch_self"


class CatchWindowClosedError(DomainError):
    status_code = 400
    code = "catch_window_closed"


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
    code = "not_checkin_topic"


class PhotoUnavailableError(DomainError):
    """Юзер не имеет photo_file_id (нет аватарки в Telegram или worker не подтянул)."""
    status_code = 404
    code = "photo_unavailable"


class TelegramUnavailableError(DomainError):
    """Telegram Bot API недоступен (timeout / 5xx / невалидный file_id)."""
    status_code = 502
    code = "telegram_unavailable"