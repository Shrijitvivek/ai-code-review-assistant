"""Enums shared by review models and pipeline components."""

from enum import StrEnum


class Severity(StrEnum):
    """Relative impact of a review finding."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ReviewStatus(StrEnum):
    """Lifecycle state of a review."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"


class Category(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    TESTING = "testing"


class FindingStatus(StrEnum):
    CONFIRMED = "confirmed"
    UNVERIFIED = "unverified"


class ScanStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class ReleaseDecision(StrEnum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
