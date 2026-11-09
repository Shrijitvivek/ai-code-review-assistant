from src.models.enums import ReviewStatus, Severity
from src.models.review_models import Finding, ReviewRequest, ReviewResult


def test_review_result_starts_pending_with_no_findings() -> None:
    request = ReviewRequest(repository="sample_repo", base_ref="main", head_ref="topic")

    result = ReviewResult(request=request)

    assert result.status is ReviewStatus.PENDING
    assert result.findings == []


def test_finding_accepts_severity_enum() -> None:
    finding = Finding(
        title="Example",
        description="Example finding",
        severity=Severity.LOW,
        file_path="sample.py",
    )

    assert finding.severity == Severity.LOW
