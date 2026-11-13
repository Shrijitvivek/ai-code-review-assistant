"""Report-level release invariants; release policy module integration is pending."""
import pytest
from pydantic import ValidationError
from src.models.review_models import ReviewResult


def report(**updates):
    data = dict(review_id="R002", commit_id="demo-a1", scan_status="complete",
                tests_passed=1, tests_failed=0, release_decision="PASS", reviewer_summary="Checked")
    return ReviewResult(**(data | updates))


@pytest.mark.parametrize("decision", ["PASS", "NEEDS_HUMAN_REVIEW"])
def test_failing_tests_require_block(decision):
    with pytest.raises(ValidationError, match="require BLOCK"):
        report(tests_failed=1, release_decision=decision)


def test_block_accepts_test_failures():
    assert report(tests_failed=2, release_decision="BLOCK").tests_failed == 2


@pytest.mark.parametrize("updates", [{"scan_status": "incomplete"},
    {"scan_status": "failed"}, {"missing_checks": ["test timeout"]},
    {"blockers": ["security issue"]}])
def test_unknown_or_blocked_check_cannot_pass(updates):
    with pytest.raises(ValidationError, match="PASS requires"):
        report(**updates)


def test_missing_file_safe_report():
    assert report(scan_status="incomplete", missing_checks=["missing.py"],
                  release_decision="NEEDS_HUMAN_REVIEW").findings == []


def test_confirmed_critical_cannot_pass():
    finding = dict(file="cart.py", line=1, category="security", severity="CRITICAL",
                   description="Unsafe execution", evidence=["static:1"],
                   status="confirmed", fix="Remove execution")
    with pytest.raises(ValidationError, match="require BLOCK"):
        report(findings=[finding])
