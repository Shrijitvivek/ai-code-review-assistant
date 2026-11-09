"""Enums shared by review models and pipeline components."""

from enum import StrEnum


class Severity(StrEnum):
    """Relative impact of a review finding."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewStatus(StrEnum):
    """Lifecycle state of a review."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"
