"""Validate finding evidence against the project's fixed sample repository.

This module only checks file paths, line numbers, and supporting references.
It never executes repository code or interprets source comments.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path, PureWindowsPath


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_REPOSITORY = (_REPOSITORY_ROOT / "sample_repo").resolve()
_REFERENCE_KEYS = (
    "tool_reference",
    "test_reference",
    "supporting_reference",
    "tool_or_test_reference",
)


def _check_result(passed: bool, reason: str | None = None) -> dict[str, object]:
    """Build the consistent result shape used by each evidence check."""
    result: dict[str, object] = {"passed": passed}
    if reason is not None:
        result["reason"] = reason
    return result


def _validate_file_path(value: object) -> tuple[dict[str, object], Path | None]:
    """Check that a relative file path resolves to a file inside sample_repo."""
    if not isinstance(value, str) or not value.strip():
        return (
            {
                "file_path_is_relative": _check_result(
                    False, "A non-empty relative file_path is required."
                ),
                "file_exists": _check_result(False, "No valid file path was provided."),
                "file_is_inside_repository": _check_result(
                    False, "No valid file path was provided."
                ),
            },
            None,
        )

    normalized = value.replace("\\", "/")
    candidate = Path(normalized)
    windows_candidate = PureWindowsPath(value)
    is_relative = (
        not candidate.is_absolute()
        and not windows_candidate.is_absolute()
        and not windows_candidate.drive
        and ".." not in candidate.parts
    )
    relative_check = _check_result(
        is_relative,
        None if is_relative else "Absolute paths and path traversal are not allowed.",
    )
    if not is_relative:
        return (
            {
                "file_path_is_relative": relative_check,
                "file_exists": _check_result(False, "The file path is not a safe relative path."),
                "file_is_inside_repository": _check_result(
                    False, "The file path is not a safe relative path."
                ),
            },
            None,
        )

    try:
        resolved = (_SAMPLE_REPOSITORY / candidate).resolve(strict=False)
    except (OSError, RuntimeError) as error:
        reason = f"The file path could not be resolved: {error}."
        return (
            {
                "file_path_is_relative": relative_check,
                "file_exists": _check_result(False, reason),
                "file_is_inside_repository": _check_result(False, reason),
            },
            None,
        )

    try:
        resolved.relative_to(_SAMPLE_REPOSITORY)
    except ValueError:
        return (
            {
                "file_path_is_relative": relative_check,
                "file_exists": _check_result(False, "The resolved path escapes sample_repo."),
                "file_is_inside_repository": _check_result(
                    False, "The resolved path escapes sample_repo."
                ),
            },
            None,
        )

    inside_check = _check_result(True)
    try:
        exists = resolved.is_file()
    except OSError as error:
        reason = f"The file could not be inspected: {error}."
        return (
            {
                "file_path_is_relative": relative_check,
                "file_exists": _check_result(False, reason),
                "file_is_inside_repository": inside_check,
            },
            None,
        )

    exists_check = _check_result(
        exists, None if exists else "The referenced file does not exist or is not a file."
    )
    return (
        {
            "file_path_is_relative": relative_check,
            "file_exists": exists_check,
            "file_is_inside_repository": inside_check,
        },
        resolved if exists else None,
    )


def validate_finding(finding: Mapping[str, object]) -> dict[str, object]:
    """Validate one mapping-based finding against ``sample_repo``.

    Findings use ``file_path`` (or ``file``) and ``line`` (or ``line_number``).
    A supporting reference must be a non-empty string in one of ``tool_reference``,
    ``test_reference``, ``supporting_reference``, or
    ``tool_or_test_reference``. The reference check confirms presence only; it
    does not establish that a referenced test ran or a tool produced a result.
    The finding is verified only if every required check passes.
    """
    checks: dict[str, object]
    if not isinstance(finding, Mapping):
        checks = {
            "file_path_is_relative": _check_result(False, "Finding must be a mapping."),
            "file_exists": _check_result(False, "Finding must be a mapping."),
            "file_is_inside_repository": _check_result(False, "Finding must be a mapping."),
            "line_is_integer": _check_result(False, "Finding must be a mapping."),
            "line_is_within_file": _check_result(False, "Finding must be a mapping."),
            "supporting_reference_present": _check_result(
                False, "Finding must be a mapping."
            ),
        }
        return {
            "verified": False,
            "verification_status": "failed",
            "checks": checks,
            "failure_reasons": [
                f"{name}: {check['reason']}" for name, check in checks.items()
            ],
        }

    file_checks, resolved_file = _validate_file_path(
        finding.get("file_path", finding.get("file"))
    )
    checks = dict(file_checks)

    line = finding.get("line", finding.get("line_number"))
    is_integer = isinstance(line, int) and not isinstance(line, bool)
    checks["line_is_integer"] = _check_result(
        is_integer,
        None if is_integer else "The line number must be an integer greater than zero.",
    )
    line_in_range = False
    if is_integer and line > 0:
        if resolved_file is None:
            line_reason = "The line number cannot be verified because the file is unavailable."
        else:
            try:
                with resolved_file.open("r", encoding="utf-8") as source_file:
                    line_count = sum(1 for _ in source_file)
            except (OSError, UnicodeError) as error:
                line_reason = f"The file could not be read to verify its line count: {error}."
            else:
                line_in_range = line <= line_count
                line_reason = (
                    None
                    if line_in_range
                    else f"The line number exceeds the file's {line_count} lines."
                )
    elif is_integer:
        line_reason = "The line number must be greater than zero."
    else:
        line_reason = "The line number must be an integer greater than zero."
    checks["line_is_within_file"] = _check_result(line_in_range, line_reason)

    reference = next(
        (
            finding.get(key)
            for key in _REFERENCE_KEYS
            if isinstance(finding.get(key), str) and finding.get(key).strip()
        ),
        None,
    )
    has_reference = isinstance(reference, str)
    checks["supporting_reference_present"] = _check_result(
        has_reference,
        None
        if has_reference
        else "A non-empty supporting tool or test reference is required.",
    )

    failure_reasons = [
        f"{name}: {check['reason']}"
        for name, check in checks.items()
        if isinstance(check, Mapping) and not check["passed"]
    ]
    verified = not failure_reasons
    return {
        "verified": verified,
        "verification_status": "verified" if verified else "failed",
        "checks": checks,
        "failure_reasons": failure_reasons,
    }


def validate_findings(
    findings: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Validate a sequence of findings and return per-finding and aggregate results.

    An empty sequence is valid and reported with ``no_findings`` status. Every
    finding in a non-empty sequence must pass individual evidence validation.
    """
    if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
        return {
            "verified": False,
            "verification_status": "failed",
            "checks": {"findings_is_sequence": _check_result(False, "Findings must be a sequence.")},
            "findings": [],
            "failure_reasons": ["findings_is_sequence: Findings must be a sequence."],
        }

    if not findings:
        return {
            "verified": True,
            "verification_status": "no_findings",
            "checks": {
                "findings_is_sequence": _check_result(True),
                "all_findings_verified": _check_result(True),
            },
            "findings": [],
            "failure_reasons": [],
        }

    results = [validate_finding(finding) for finding in findings]
    all_verified = all(result["verified"] for result in results)
    failures = [
        f"Finding {index}: {reason}"
        for index, result in enumerate(results)
        for reason in result["failure_reasons"]
    ]
    return {
        "verified": all_verified,
        "verification_status": "verified" if all_verified else "failed",
        "checks": {
            "findings_is_sequence": _check_result(True),
            "all_findings_verified": _check_result(
                all_verified,
                None if all_verified else "One or more findings did not pass all checks.",
            ),
        },
        "findings": results,
        "failure_reasons": failures,
    }
