"""Validated Group 3 input and output contracts (Pydantic v2)."""
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from .enums import Category, FindingStatus, ReleaseDecision, ScanStatus, Severity

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Count = Annotated[int, Field(strict=True, ge=0)]


def validate_source_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or value != value.strip() or "\\" in value or ":" in value
            or path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts
            or path.suffix != ".py" or "\x00" in value):
        raise ValueError("Expected a relative .py path inside the repository")
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ReviewRequest(Contract):
    review_id: Text
    commit_id: Text
    files: list[str] = Field(min_length=1)
    message: Text

    @field_validator("files")
    @classmethod
    def source_paths(cls, files: list[str]) -> list[str]:
        checked = [validate_source_path(path) for path in files]
        if len(set(checked)) != len(checked):
            raise ValueError("Duplicate requested files")
        return checked


class Finding(Contract):
    file: str
    line: int = Field(strict=True, ge=1)
    category: Category
    severity: Severity
    description: Text
    evidence: list[Text] = Field(min_length=1)
    status: FindingStatus
    fix: Text
    tool_reference: Text | None = None
    test_reference: Text | None = None
    _source_path = field_validator("file")(validate_source_path)


class ReviewerReport(Contract):
    findings: list[Finding]
    reviewer_summary: Text


class TestAnalystReport(Contract):
    test_status: Literal["passed", "failed", "unavailable"]
    test_evidence: Text


class SynthesizedReport(Contract):
    findings: list[Finding]
    reviewer_summary: Text
    test_status: Literal["passed", "failed", "unavailable", "unknown"]


class ReviewResult(Contract):
    review_id: Text
    commit_id: Text
    scan_status: ScanStatus
    tests_passed: Count
    tests_failed: Count
    findings: list[Finding] = Field(default_factory=list)
    release_decision: ReleaseDecision
    blockers: list[Text] = Field(default_factory=list)
    missing_checks: list[Text] = Field(default_factory=list)
    reviewer_summary: Text

    @model_validator(mode="after")
    def safe_report(self) -> "ReviewResult":
        # Safety floor only: the release component must still apply policy and
        # verify evidence against the same repository snapshot.
        blocking = self.tests_failed > 0 or any(
            f.severity == Severity.CRITICAL and f.status == FindingStatus.CONFIRMED
            for f in self.findings)
        if blocking and self.release_decision != ReleaseDecision.BLOCK:
            raise ValueError("Failing tests or confirmed critical findings require BLOCK")
        if self.release_decision == ReleaseDecision.PASS and (
            self.scan_status != ScanStatus.COMPLETE or self.missing_checks or self.blockers
            or any(f.status == FindingStatus.UNVERIFIED for f in self.findings)
        ):
            raise ValueError("PASS requires complete checks and verified evidence")
        return self


def report_json_schema() -> dict:
    """Canonical JSON schema; semantic rules also need runtime validation."""
    return ReviewResult.model_json_schema()
