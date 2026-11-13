"""Deterministic release decisions based on review results and policy."""

from collections.abc import Mapping, Sequence


_COMPLETED_STATUSES = {"complete", "completed", "passed", "success", "successful"}
_FAILED_STATUSES = {"fail", "failed", "failure", "error"}


def _status(value: object) -> str | None:
    """Return a normalized status from a string or status-bearing mapping."""
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, Mapping):
        for key in ("status", "state", "outcome"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip().casefold()
    return None


def _stage_status(review_results: Mapping[str, object], stage: str) -> str | None:
    """Read a stage status from the `stages` mapping or a top-level entry."""
    stages = review_results.get("stages")
    if isinstance(stages, Mapping) and stage in stages:
        return _status(stages[stage])
    return _status(review_results.get(stage))


def _test_status(review_results: Mapping[str, object]) -> str | None:
    """Read the explicit aggregate test status, if present."""
    for key in ("test_status", "test_results", "tests"):
        if key in review_results:
            return _status(review_results[key])
    tests_passed = review_results.get("tests_passed")
    if isinstance(tests_passed, bool):
        return "passed" if tests_passed else "failed"
    return None


def _evidence_passed(value: object) -> bool | None:
    """Inspect an evidence-validation result without treating unknown as success."""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if not value:
            return None
        results = [_evidence_passed(item) for item in value]
        if any(result is False for result in results):
            return False
        return True if all(result is True for result in results) else None
    if not isinstance(value, Mapping):
        return None

    verified = value.get("verified")
    if verified is False:
        return False

    status = value.get("verification_status")
    if isinstance(status, str):
        normalized_status = status.strip().casefold()
        if normalized_status in _FAILED_STATUSES:
            return False

    checks = value.get("checks")
    if isinstance(checks, Mapping):
        if checks:
            outcomes = [
                check.get("passed") if isinstance(check, Mapping) else None
                for check in checks.values()
            ]
            if any(outcome is False for outcome in outcomes):
                return False
            if all(outcome is True for outcome in outcomes):
                return verified is True or (
                    isinstance(status, str)
                    and status.strip().casefold() in {"verified", "passed", "success"}
                )
            return None
        if verified is True:
            return True
        return None
    if verified is True:
        return True
    if isinstance(status, str) and status.strip().casefold() in {
        "verified",
        "passed",
        "success",
    }:
        return True
    return None


def _configured_severities(value: object) -> set[str] | None:
    """Normalize configured severity names from a sequence or mapping."""
    if isinstance(value, Mapping):
        levels = value.keys()
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        levels = value
    else:
        return None

    if any(not isinstance(level, str) or not level.strip() for level in levels):
        return None
    return {level.strip().upper() for level in levels}


def evaluate_release(
    review_results: Mapping[str, object], policy: Mapping[str, object]
) -> dict[str, object]:
    """Return a release decision from mapping-based results and policy.

    Results may contain stage statuses under ``stages`` or directly under each
    stage name. Each stage status can be a string or a mapping with ``status``,
    ``state``, or ``outcome``. Test results are read from ``test_status``,
    ``test_results``, or ``tests`` using the same shape. Findings are supplied
    as a sequence of mappings in ``findings``; evidence validation is supplied
    in ``evidence_validation`` using the shape returned by the evidence
    validator.

    Missing, malformed, or indeterminate checks never imply success. A
    configured blocking condition takes precedence over human review.
    """
    missing_checks: list[str] = []
    blockers: list[str] = []
    decision_reasons: list[str] = []

    if not isinstance(review_results, Mapping):
        review_results = {}
        missing_checks.append("review_results")
    if not isinstance(policy, Mapping):
        policy = {}
        missing_checks.append("policy")

    raw_required_stages = policy.get("required_stages")
    if (
        isinstance(raw_required_stages, Sequence)
        and not isinstance(raw_required_stages, (str, bytes))
        and bool(raw_required_stages)
        and all(isinstance(stage, str) and stage.strip() for stage in raw_required_stages)
    ):
        required_stages = tuple(
            dict.fromkeys(stage.strip() for stage in raw_required_stages)
        )
    else:
        required_stages = ()
        missing_checks.append("policy.required_stages")

    if not isinstance(policy.get("block_if_test_fails"), bool):
        missing_checks.append("policy.block_if_test_fails")
    if not isinstance(policy.get("block_if_confirmed_critical"), bool):
        missing_checks.append("policy.block_if_confirmed_critical")
    if not isinstance(policy.get("unknown_check_policy"), str) or not policy.get(
        "unknown_check_policy", ""
    ).strip():
        missing_checks.append("policy.unknown_check_policy")

    severity_levels = _configured_severities(policy.get("severity_levels"))
    if severity_levels is None or "CRITICAL" not in severity_levels:
        missing_checks.append("policy.severity_levels")

    for stage in required_stages:
        stage_status = _stage_status(review_results, stage)
        if stage_status not in _COMPLETED_STATUSES:
            missing_checks.append(f"stage:{stage}")
            decision_reasons.append(
                f"Required stage '{stage}' is missing or not completed."
            )

    test_status = _test_status(review_results)
    if test_status in _FAILED_STATUSES:
        decision_reasons.append("The test suite has failed.")
        if policy.get("block_if_test_fails") is True:
            blockers.append("Tests failed and policy blocks releases on test failures.")
    elif test_status not in {"passed", "success", "successful", "complete", "completed"}:
        missing_checks.append("test_results")
        decision_reasons.append("Test results are missing or unavailable.")

    raw_findings = review_results.get("findings")
    if not isinstance(raw_findings, Sequence) or isinstance(raw_findings, (str, bytes)):
        findings: Sequence[object] = ()
        missing_checks.append("findings")
        decision_reasons.append("Findings are missing or malformed.")
    else:
        findings = raw_findings

    if severity_levels is not None and "CRITICAL" in severity_levels:
        for finding in findings:
            if not isinstance(finding, Mapping):
                missing_checks.append("finding")
                decision_reasons.append("A finding has an invalid format.")
                continue
            severity = finding.get("severity")
            finding_status = finding.get("status")
            confirmed = finding.get("confirmed") is True or (
                isinstance(finding_status, str)
                and finding_status.strip().casefold() == "confirmed"
            )
            if (
                confirmed
                and isinstance(severity, str)
                and severity.strip().upper() == "CRITICAL"
                and policy.get("block_if_confirmed_critical") is True
            ):
                blockers.append("A confirmed CRITICAL finding is present.")
                break

    evidence_result = _evidence_passed(review_results.get("evidence_validation"))
    if evidence_result is False:
        missing_checks.append("evidence_validation")
        decision_reasons.append("Evidence validation failed.")
    elif evidence_result is None:
        missing_checks.append("evidence_validation")
        decision_reasons.append("Evidence validation is missing or unavailable.")

    if blockers:
        release_decision = "BLOCK"
    elif missing_checks:
        release_decision = "NEEDS_HUMAN_REVIEW"
        unknown_policy = policy.get("unknown_check_policy")
        if isinstance(unknown_policy, str) and unknown_policy.strip():
            decision_reasons.append(
                f"Unavailable checks require human review under "
                f"unknown_check_policy '{unknown_policy.strip()}'."
            )
        decision_reasons.append("One or more required checks need human review.")
    else:
        release_decision = "PASS"
        decision_reasons.append("All required checks passed.")

    return {
        "release_decision": release_decision,
        "blockers": blockers,
        "missing_checks": list(dict.fromkeys(missing_checks)),
        "decision_reasons": list(dict.fromkeys(decision_reasons)),
    }
