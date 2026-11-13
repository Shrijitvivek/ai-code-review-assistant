"""Command-line entry point for the integrated review workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from src.data.request_loader import deduplicate_findings
from src.agents.review_team import (
    extract_reviewer_report,
    extract_synthesized_report,
    extract_test_analyst_report,
    extract_test_runner_status,
    run_review_team,
)
from src.data.request_loader import load_requests
from src.models.review_models import Finding
from src.pipeline.review_pipeline import load_review_policy, prepare_review
from src.release.evidence_validator import validate_findings
from src.release.release_gate import evaluate_release


_REVIEWER_STAGES = {
    "correctness": "CorrectnessReviewer",
    "security": "SecurityReviewer",
}
_REVIEWER_NAMES = (
    "CorrectnessReviewer",
    "SecurityReviewer",
    "TestAnalyst",
    "ReportSynthesizer",
)
_COMPLETED_STATUSES = {"complete", "completed", "passed", "success", "successful"}


def _error_message(error: Exception) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


def _status(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, Mapping):
        status = value.get("status")
        if isinstance(status, str) and status.strip():
            return status.strip().casefold()
    return None


def _read_tool_references(
    messages: list[object], reviewer: str
) -> dict[str, str]:
    references: dict[str, str] = {}
    for message in messages:
        if getattr(message, "source", None) != reviewer:
            continue
        content = getattr(message, "content", None)
        if not isinstance(content, (list, tuple)):
            continue
        for result in content:
            if getattr(result, "name", None) != "read_repository_file":
                continue
            tool_content = getattr(result, "content", None)
            if isinstance(tool_content, str):
                try:
                    tool_result = json.loads(tool_content)
                except json.JSONDecodeError:
                    continue
            elif isinstance(tool_content, Mapping):
                tool_result = tool_content
            else:
                continue
            path = tool_result.get("path")
            reference = tool_result.get("tool_reference")
            if (
                tool_result.get("status") == "ok"
                and isinstance(path, str)
                and reference == f"read_repository_file:{path}"
            ):
                references[path] = reference
    return references


def _bind_reviewer_evidence(
    report: dict[str, object], references: Mapping[str, str]
) -> dict[str, object]:
    findings = report.get("findings")
    if not isinstance(findings, list):
        return report
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        path = finding.get("file")
        finding["tool_reference"] = (
            references.get(path) if isinstance(path, str) else None
        )
        finding["test_reference"] = None
    return report


def _stored_content(content: object) -> object:
    if isinstance(content, BaseModel):
        return content.model_dump(mode="json")
    return content if isinstance(content, (str, Mapping)) else None


def _finding_content_key(finding: Mapping[str, object]) -> str:
    return json.dumps(
        {
            key: finding.get(key)
            for key in (
                "file",
                "line",
                "category",
                "severity",
                "description",
                "evidence",
                "status",
                "fix",
            )
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _deduplicated_review_findings(
    reviewer_outputs: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    findings: list[Finding] = []
    for reviewer in _REVIEWER_STAGES.values():
        parsed = reviewer_outputs.get(reviewer, {}).get("parsed")
        raw_findings = parsed.get("findings") if isinstance(parsed, Mapping) else None
        if not isinstance(raw_findings, list):
            continue
        findings.extend(
            Finding.model_validate(finding)
            for finding in raw_findings
            if isinstance(finding, Mapping)
        )
    return [
        finding.model_dump(mode="json")
        for finding in deduplicate_findings(findings)
    ]


def _bind_synthesized_evidence(
    report: Mapping[str, object],
    reviewer_outputs: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    normalized = dict(report)
    findings = report.get("findings")
    if not isinstance(findings, list):
        return normalized
    trusted_references: dict[str, tuple[object, object]] = {}
    for reviewer in _REVIEWER_STAGES.values():
        parsed = reviewer_outputs.get(reviewer, {}).get("parsed")
        if not isinstance(parsed, Mapping) or not isinstance(parsed.get("findings"), list):
            continue
        for finding in parsed["findings"]:
            if isinstance(finding, Mapping):
                trusted_references[_finding_content_key(finding)] = (
                    finding.get("tool_reference"),
                    finding.get("test_reference"),
                )
    normalized_findings: list[object] = []
    for finding in findings:
        if not isinstance(finding, Mapping):
            normalized_findings.append(finding)
            continue
        item = dict(finding)
        references = trusted_references.get(_finding_content_key(item))
        item["tool_reference"] = references[0] if references else None
        item["test_reference"] = references[1] if references else None
        normalized_findings.append(item)
    normalized["findings"] = normalized_findings
    return normalized


def _reviewer_outputs(
    result: object, *, requested_files: Sequence[object] = ()
) -> dict[str, dict[str, object]]:
    messages = getattr(result, "messages", None)
    test_runner_status = extract_test_runner_status(result)
    requested_paths = {
        path for path in requested_files if isinstance(path, str) and path
    }
    outputs = {
        name: {"status": "unavailable", "content": None} for name in _REVIEWER_NAMES
    }
    if isinstance(messages, list):
        read_references = {
            reviewer: _read_tool_references(messages, reviewer)
            for reviewer in _REVIEWER_STAGES.values()
        }
        for message in messages:
            source = getattr(message, "source", None)
            content = getattr(message, "content", None)
            if source not in outputs:
                continue
            if source in _REVIEWER_STAGES.values():
                if not isinstance(content, (str, Mapping, BaseModel)):
                    continue
                try:
                    category = {
                        reviewer: stage for stage, reviewer in _REVIEWER_STAGES.items()
                    }[source]
                    parsed_output = extract_reviewer_report(
                        content, expected_category=category
                    )
                except ValueError:
                    outputs[source] = {
                        "status": "unavailable",
                        "content": _stored_content(content),
                    }
                    continue
                parsed_findings = parsed_output.get("findings")
                if (
                    not requested_paths
                    or not requested_paths <= set(read_references[source])
                    or not set(read_references[source]) <= requested_paths
                    or not isinstance(parsed_findings, list)
                    or any(
                        not isinstance(finding, Mapping)
                        or finding.get("file") not in requested_paths
                        for finding in parsed_findings
                    )
                ):
                    outputs[source] = {
                        "status": "unavailable",
                        "content": _stored_content(content),
                        "parsed": _bind_reviewer_evidence(
                            parsed_output, read_references[source]
                        ),
                    }
                    continue
                parsed_output = _bind_reviewer_evidence(
                    parsed_output, read_references[source]
                )
                outputs[source] = {
                    "status": "completed",
                    "content": _stored_content(content),
                    "parsed": parsed_output,
                }
            elif source == "TestAnalyst":
                if not isinstance(content, (str, Mapping, BaseModel)):
                    continue
                try:
                    parsed_output = extract_test_analyst_report(content)
                except ValueError:
                    outputs[source] = {
                        "status": "unavailable",
                        "content": _stored_content(content),
                    }
                    continue
                valid_test_result = (
                    test_runner_status in {"passed", "failed"}
                    and parsed_output["test_status"] == test_runner_status
                )
                outputs[source] = {
                    "status": "completed" if valid_test_result else "unavailable",
                    "content": _stored_content(content),
                    "test_status": parsed_output["test_status"],
                    "test_runner_status": test_runner_status,
                    "parsed": parsed_output,
                }
            else:
                outputs[source] = {
                    "status": "unavailable",
                    "content": _stored_content(content),
                }
    return outputs


def _synthesized_report_agrees(
    report: Mapping[str, object],
    reviewer_outputs: Mapping[str, Mapping[str, object]],
) -> tuple[bool, bool]:
    source_findings = _deduplicated_review_findings({
        reviewer: output
        for reviewer, output in reviewer_outputs.items()
        if output.get("status") == "completed"
    })

    synthesized_findings = report.get("findings")
    if not isinstance(synthesized_findings, list) or not all(
        isinstance(finding, Mapping) for finding in synthesized_findings
    ):
        return False, False
    synthesized = [
        finding.model_dump(mode="json")
        for finding in deduplicate_findings([
            Finding.model_validate(finding)
            for finding in synthesized_findings
        ])
    ]

    source_keys = {_finding_agreement_key(finding) for finding in source_findings}
    synthesized_keys = {_finding_agreement_key(finding) for finding in synthesized}
    findings_agree = source_keys == synthesized_keys

    test_analyst = reviewer_outputs.get("TestAnalyst", {})
    analyst_status = test_analyst.get("test_status")
    report_status = report.get("test_status")
    tests_agree = (
        test_analyst.get("status") == "completed"
        and isinstance(analyst_status, str)
        and isinstance(report_status, str)
        and report_status.strip().casefold() == analyst_status
    )
    return findings_agree, tests_agree


def _finding_agreement_key(finding: Mapping[str, object]) -> str:
    return json.dumps(
        {
            key: finding.get(key)
            for key in (
                "file",
                "line",
                "category",
                "severity",
                "description",
                "evidence",
                "status",
                "fix",
                "tool_reference",
                "test_reference",
            )
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _findings_from(
    findings: object,
    *,
    source: str,
) -> list[object]:
    if not isinstance(findings, list):
        return [findings]

    combined: list[object] = []
    for finding in findings:
        if isinstance(finding, Mapping):
            item = dict(finding)
            item["source"] = source
        else:
            item = finding
        combined.append(item)
    return combined


def _combined_test_status(pipeline_status: object, analyst_status: object) -> str:
    statuses = (_status(pipeline_status), _status(analyst_status))
    if any(status in {"failed", "failure", "fail"} for status in statuses):
        return "failed"
    if statuses == ("passed", "passed"):
        return "passed"
    return "unavailable"


def _pipeline_identity_matches(
    request: Mapping[str, object],
    pipeline_result: Mapping[str, object] | None,
) -> bool:
    return pipeline_result is not None and all(
        pipeline_result.get(field) == request.get(field)
        for field in ("review_id", "commit_id")
    )


def _trusted_evidence_references(
    pipeline_result: Mapping[str, object] | None,
    reviewer_outputs: Mapping[str, Mapping[str, object]],
) -> set[str]:
    references: set[str] = set()
    if pipeline_result is not None:
        checks = pipeline_result.get("checks")
        static_analysis = checks.get("static_analysis") if isinstance(checks, Mapping) else None
        findings = (
            static_analysis.get("findings")
            if isinstance(static_analysis, Mapping)
            and static_analysis.get("status") == "completed"
            else None
        )
        if isinstance(findings, list):
            references.update(
                finding["tool_reference"]
                for finding in findings
                if isinstance(finding, Mapping)
                and isinstance(finding.get("tool_reference"), str)
                and finding["tool_reference"].strip()
            )
    for reviewer in _REVIEWER_STAGES.values():
        output = reviewer_outputs.get(reviewer, {})
        parsed = output.get("parsed")
        findings = parsed.get("findings") if isinstance(parsed, Mapping) else None
        if output.get("status") == "completed" and isinstance(findings, list):
            references.update(
                finding["tool_reference"]
                for finding in findings
                if isinstance(finding, Mapping)
                and isinstance(finding.get("tool_reference"), str)
                and finding["tool_reference"].strip()
            )
    return references


def _pipeline_checks_complete(pipeline_result: Mapping[str, object] | None) -> bool:
    if pipeline_result is None:
        return False
    checks = pipeline_result.get("checks")
    if not isinstance(checks, Mapping):
        return False
    static_analysis = checks.get("static_analysis")
    missing_files = pipeline_result.get("missing_files")
    static_findings = (
        static_analysis.get("findings")
        if isinstance(static_analysis, Mapping)
        else None
    )
    return (
        isinstance(static_analysis, Mapping)
        and isinstance(static_findings, list)
        and all(
            isinstance(finding, Mapping)
            and isinstance(finding.get("tool_reference"), str)
            and bool(finding["tool_reference"].strip())
            for finding in static_findings
        )
        and isinstance(missing_files, (list, tuple))
        and not missing_files
        and all(
            _status(checks.get(name)) in _COMPLETED_STATUSES
            for name in ("inventory", "static_analysis")
        )
    )


def _report(
    request: Mapping[str, object],
    pipeline_result: Mapping[str, object] | None,
    pipeline_error: str | None,
    autogen_report: Mapping[str, object] | None,
    autogen_error: str | None,
    reviewer_outputs: Mapping[str, Mapping[str, object]],
    policy: Mapping[str, object],
    policy_error: str | None,
) -> dict[str, object]:
    errors: list[dict[str, str]] = []
    for source, message in (
        ("langchain", pipeline_error),
        ("autogen", autogen_error),
        ("review_policy", policy_error),
    ):
        if message is not None:
            errors.append({"source": source, "message": message})

    pipeline_matches_request = _pipeline_identity_matches(request, pipeline_result)
    if pipeline_result is not None and not pipeline_matches_request:
        errors.append({
            "source": "langchain",
            "message": "Pipeline result review_id or commit_id does not match the request.",
        })
    trusted_pipeline = pipeline_result if pipeline_matches_request else None

    pipeline_findings: object = None
    pipeline_test_results: object = {"status": "unavailable"}
    missing_files: list[object] = []
    if trusted_pipeline is not None:
        checks = trusted_pipeline.get("checks")
        if isinstance(checks, Mapping):
            static_analysis = checks.get("static_analysis")
            if isinstance(static_analysis, Mapping):
                pipeline_findings = static_analysis.get("findings")
            pipeline_test_results = checks.get(
                "test_results", {"status": "unavailable"}
            )
        raw_missing_files = trusted_pipeline.get("missing_files", [])
        if isinstance(raw_missing_files, (list, tuple)):
            missing_files = list(raw_missing_files)

    trusted_pipeline_findings = (
        [
            finding for finding in pipeline_findings
            if isinstance(finding, Mapping)
            and isinstance(finding.get("tool_reference"), str)
            and finding["tool_reference"].strip()
        ]
        if isinstance(pipeline_findings, list)
        else pipeline_findings
    )
    combined_findings = _findings_from(
        trusted_pipeline_findings, source="langchain_static_analysis"
    )
    combined_findings.extend(_findings_from(
        _deduplicated_review_findings(reviewer_outputs),
        source="autogen",
    ))

    evidence_validation = validate_findings(
        combined_findings,
        allowed_references=_trusted_evidence_references(
            trusted_pipeline, reviewer_outputs
        ),
    )
    autogen_test_status = (
        autogen_report.get("test_status", "unavailable")
        if autogen_report is not None
        else "unavailable"
    )
    test_status = _combined_test_status(
        pipeline_test_results,
        reviewer_outputs.get("TestAnalyst", {}).get("test_status"),
    )
    findings_agree = False
    tests_agree = False
    if autogen_report is not None:
        findings_agree, tests_agree = _synthesized_report_agrees(
            autogen_report, reviewer_outputs
        )

    stages = {
        "source_scan": {
            "status": (
                "completed"
                if _pipeline_checks_complete(trusted_pipeline) and not missing_files
                else "unavailable"
            )
        }
    }
    stages.update({
        stage: {
            "status": (
                "completed"
                if reviewer_outputs.get(reviewer, {}).get("status") == "completed"
                and findings_agree
                and _pipeline_checks_complete(trusted_pipeline)
                and not missing_files
                else "unavailable"
            )
        }
        for stage, reviewer in _REVIEWER_STAGES.items()
    })
    pipeline_test_status = _status(pipeline_test_results)
    test_analyst_available = (
        reviewer_outputs.get("TestAnalyst", {}).get("status") == "completed"
    )
    synthesized_test_available = (
        reviewer_outputs.get("ReportSynthesizer", {}).get("status") == "completed"
        and tests_agree
    )
    testing_available = (
        pipeline_test_status in {"passed", "failed"}
        and test_analyst_available
        and reviewer_outputs.get("TestAnalyst", {}).get("test_status")
        in {"passed", "failed"}
        and synthesized_test_available
    )
    stages["testing"] = {
        "status": "completed" if testing_available else "unavailable"
    }
    release_results: dict[str, object] = {
        "stages": stages,
        "test_results": {"status": test_status},
        "findings": combined_findings,
        "evidence_validation": evidence_validation,
    }
    release = evaluate_release(release_results, policy)

    report: dict[str, object] = {
        "review_id": request["review_id"],
        "commit_id": request["commit_id"],
        "request": dict(request),
        "langchain": dict(pipeline_result) if pipeline_result is not None else None,
        "autogen": {
            "report": dict(autogen_report) if autogen_report is not None else None,
            "reviewer_outputs": {
                name: dict(reviewer_outputs.get(name, {}))
                for name in _REVIEWER_NAMES
            },
        },
        "checks": {
            "stages": stages,
            "langchain_tests": pipeline_test_results,
            "autogen_test_status": autogen_test_status,
            "missing_files": missing_files,
        },
        "findings": combined_findings,
        "evidence_validation": evidence_validation,
        "release_decision": release["release_decision"],
        "blockers": release["blockers"],
        "missing_checks": release["missing_checks"],
        "decision_reasons": release["decision_reasons"],
        "errors": errors,
    }
    return report


async def _review_request(
    request: Mapping[str, object],
    policy: Mapping[str, object],
    policy_error: str | None,
) -> dict[str, object]:
    pipeline_result: Mapping[str, object] | None = None
    pipeline_error: str | None = None
    try:
        result = prepare_review(request)
        if isinstance(result, Mapping):
            pipeline_result = result
        else:
            pipeline_error = "TypeError: LangChain pipeline returned a non-mapping result."
    except Exception as error:
        pipeline_error = _error_message(error)

    autogen_report: Mapping[str, object] | None = None
    autogen_error: str | None = None
    reviewer_outputs: dict[str, dict[str, object]] = {
        name: {"status": "unavailable", "content": None}
        for name in _REVIEWER_NAMES
    }
    try:
        team_result = await run_review_team(request)
        requested_files = request.get("files")
        reviewer_outputs = _reviewer_outputs(
            team_result,
            requested_files=(
                requested_files
                if isinstance(requested_files, Sequence)
                and not isinstance(requested_files, (str, bytes))
                else ()
            ),
        )
        reviewer_outputs["ReportSynthesizer"] = {
            **reviewer_outputs["ReportSynthesizer"],
            "status": "unavailable",
        }
        autogen_report = _bind_synthesized_evidence(
            extract_synthesized_report(team_result), reviewer_outputs
        )
        findings_agree, tests_agree = _synthesized_report_agrees(
            autogen_report, reviewer_outputs
        )
        reviewer_outputs["ReportSynthesizer"] = {
            **reviewer_outputs["ReportSynthesizer"],
            "status": (
                "completed"
                if findings_agree
                and tests_agree
                and all(
                    reviewer_outputs.get(name, {}).get("status") == "completed"
                    for name in (
                        *_REVIEWER_STAGES.values(),
                        "TestAnalyst",
                    )
                )
                else "unavailable"
            ),
        }
    except Exception as error:
        autogen_error = _error_message(error)

    return _report(
        request,
        pipeline_result,
        pipeline_error,
        autogen_report,
        autogen_error,
        reviewer_outputs,
        policy,
        policy_error,
    )


async def _run_requests(
    requests: Sequence[Mapping[str, object]],
    policy: Mapping[str, object],
    policy_error: str | None,
) -> list[dict[str, object]]:
    reports = []
    for request in requests:
        reports.append(await _review_request(request, policy, policy_error))
    return reports


def main(argv: list[str] | None = None) -> int:
    """Load a request batch, run both review systems, and print JSON reports."""
    parser = argparse.ArgumentParser(
        description="Review repository changes and evaluate release readiness."
    )
    parser.add_argument("request_file", help="Path to a JSON request batch.")
    args = parser.parse_args(argv)

    try:
        requests = load_requests(args.request_file)
    except (OSError, ValueError) as error:
        print(json.dumps({"error": _error_message(error), "reports": []}, indent=2))
        return 2

    policy: Mapping[str, object] = {}
    policy_error: str | None = None
    try:
        policy = load_review_policy()
    except (OSError, ValueError, TypeError) as error:
        policy_error = _error_message(error)

    reports = asyncio.run(_run_requests(requests, policy, policy_error))
    print(json.dumps(reports, indent=2, ensure_ascii=False, default=str))
    return 1 if any(report["errors"] for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
