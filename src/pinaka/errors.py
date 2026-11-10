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
