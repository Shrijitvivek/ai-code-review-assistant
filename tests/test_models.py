"""Deterministic schema checks; no API access."""
import pytest
from pydantic import ValidationError
from src.models.review_models import Finding, ReviewRequest, ReviewResult, report_json_schema


def request_data():
    return dict(review_id="R001", commit_id="demo-a1", files=["cart.py"], message="Review")


def finding_data():
    return dict(file="cart.py", line=1, category="correctness", severity="HIGH",
                description="Wrong subtotal", evidence=["pytest:test_total"],
                status="confirmed", fix="Sum item prices")


def report_data():
    return dict(review_id="R001", commit_id="demo-a1", scan_status="complete",
                tests_passed=1, tests_failed=0, release_decision="PASS",
                reviewer_summary="All required checks completed")


def test_request_and_report_round_trip():
    assert ReviewRequest(**request_data()).files == ["cart.py"]
    result = ReviewResult(**report_data())
    assert ReviewResult.model_validate_json(result.model_dump_json()) == result
    assert set(report_json_schema()["required"]) >= {
        "review_id", "commit_id", "scan_status", "tests_passed", "tests_failed",
        "release_decision", "reviewer_summary"}
    assert "findings" in report_json_schema()["properties"]


@pytest.mark.parametrize("field,value", [("line", 0), ("line", True), ("line", "1"),
    ("severity", "urgent"), ("status", "probably"), ("evidence", []), ("fix", " ")])
def test_rejects_invalid_findings(field, value):
    data = finding_data()
    data[field] = value
    with pytest.raises(ValidationError):
        Finding(**data)


@pytest.mark.parametrize("patch", [{"review_id": " "}, {"files": []},
    {"files": ["cart.py", "cart.py"]}, {"commit_id": 3}, {"extra": "ignored?"}])
def test_rejects_invalid_request(patch):
    with pytest.raises(ValidationError):
        ReviewRequest(**(request_data() | patch))


@pytest.mark.parametrize("count", [-1, True, "2"])
def test_strict_test_counts(count):
    with pytest.raises(ValidationError):
        ReviewResult(**(report_data() | {"tests_passed": count}))


def test_mutable_defaults_are_independent():
    first, second = ReviewResult(**report_data()), ReviewResult(**report_data())
    first.missing_checks.append("security")
    assert second.missing_checks == []
