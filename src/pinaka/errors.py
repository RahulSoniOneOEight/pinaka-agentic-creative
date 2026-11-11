class PinakaError(Exception):
    """Base error for expected controller failures."""


class NotFoundError(PinakaError):
    pass


class ConflictError(PinakaError):
    pass


class InvalidTransitionError(PinakaError):
    pass


class ApprovalRequiredError(PinakaError):
    pass


class BudgetExceededError(PinakaError):
    pass


class VarianceApprovalRequiredError(BudgetExceededError):
    """Raised when a provider result exceeds the plan's variance tolerance."""

    def __init__(
        self,
        message: str,
        *,
        job_id: str,
        plan_id: str,
        item_id: str,
        estimated_minor: int,
        actual_minor: int,
        allowed_minor: int,
        currency: str,
    ) -> None:
        super().__init__(message)
        self.job_id = job_id
        self.plan_id = plan_id
        self.item_id = item_id
        self.estimated_minor = estimated_minor
        self.actual_minor = actual_minor
        self.allowed_minor = allowed_minor
        self.currency = currency


class IntegrityError(PinakaError):
    pass


class UnsafePathError(PinakaError):
    pass


class ConfigurationError(PinakaError):
    pass


class RightsEligibilityError(PinakaError):
    pass


class QuotaExceededError(PinakaError):
    pass
