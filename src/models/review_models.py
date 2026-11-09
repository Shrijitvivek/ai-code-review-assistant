"""Typed data structures used to represent code reviews."""

from dataclasses import dataclass, field

from .enums import ReviewStatus, Severity


@dataclass(frozen=True)
class ReviewRequest:
    """A request to review a repository change."""

    repository: str
    base_ref: str
    head_ref: str


@dataclass(frozen=True)
class Finding:
    """A finding reported by a reviewer."""

    title: str
    description: str
    severity: Severity
    file_path: str
    line: int | None = None


@dataclass
class ReviewResult:
    """The collected outcome of reviewing a change."""

    request: ReviewRequest
    status: ReviewStatus = ReviewStatus.PENDING
    findings: list[Finding] = field(default_factory=list)
