from __future__ import annotations


class DomainError(Exception):
    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code


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