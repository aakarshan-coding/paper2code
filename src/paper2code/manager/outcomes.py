from enum import StrEnum


class Outcome(StrEnum):
    """Fixed outcome vocabulary from spec section 4.1. Incomplete is normal, not an error."""

    COMPLETED = "completed"
    COMPLETED_SUSPICIOUS = "completed_suspicious"
    HIDDEN_FAILED = "hidden_failed"
    TESTS_TAMPERED = "tests_tampered"
    INCOMPLETE_BUDGET = "incomplete_budget"
    INCOMPLETE_STUCK = "incomplete_stuck"
    SCOPE_REJECTED = "scope_rejected"
    NO_CANDIDATES = "no_candidates"
    ERROR = "error"
