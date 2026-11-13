"""Command-line entry point for the integrated review workflow."""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import re
from collections.abc import Mapping, MutableMapping, Sequence

from pydantic import BaseModel

from src.data.request_loader import deduplicate_findings, load_request, load_requests
from src.agents.review_team import (
    extract_reviewer_report,
    extract_synthesized_report,
    extract_test_analyst_report,
    extract_test_runner_status,
    run_review_team,
)
from src.models.review_models import Finding, ReviewResult
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
_MAX_CONCURRENT_REVIEWS = 4


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
    return {
        path: result["tool_reference"]
        for path, result in _read_tool_results(messages, reviewer).items()
        if isinstance(result.get("tool_reference"), str)
    }


def _read_tool_sources(messages: list[object], reviewer: str) -> dict[str, str]:
    return {
        path: result["content"]
        for path, result in _read_tool_results(messages, reviewer).items()
        if isinstance(result.get("content"), str)
    }


def _read_tool_results(
    messages: list[object], reviewer: str
) -> dict[str, Mapping[str, object]]:
    tool_results: dict[str, Mapping[str, object]] = {}
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
                tool_results[path] = tool_result
    return tool_results


def _correct_finding_lines(
    findings: object, sources: Mapping[str, str]
) -> list[dict[str, int]] | None:
    if not isinstance(findings, list):
        return None
    corrections: list[dict[str, int]] = []
    for finding in findings:
        if not isinstance(finding, MutableMapping):
            return None
        path = finding.get("file")
        line = finding.get("line")
        evidence = finding.get("evidence")
        source = sources.get(path) if isinstance(path, str) else None
        if (
            not isinstance(source, str)
            or not isinstance(line, int)
            or isinstance(line, bool)
            or not isinstance(evidence, list)
            or not all(isinstance(item, str) for item in evidence)
        ):
            return None
        lines = source.splitlines()
        normalized_evidence = [" ".join(item.split()) for item in evidence]
        matching_lines = [
            line_number
            for line_number, source_line in enumerate(lines, start=1)
            if source_line.strip()
            and not source_line.lstrip().startswith("#")
            and any(
                " ".join(source_line.split()) in excerpt
                for excerpt in normalized_evidence
            )
        ]
        if not matching_lines or (
            len(matching_lines) > 1 and line not in matching_lines
        ):
            return None
        actual_line = line if line in matching_lines else matching_lines[0]
        source_line = lines[actual_line - 1].strip()
        if source_line.startswith(("def ", "async def ", "class ", '"""', "'''")):
            return None
        if finding.get("category") == "security" and _is_plain_business_logic_line(
            source, actual_line
        ):
            return None
        if line != actual_line:
            finding["line"] = actual_line
            corrections.append({"reported_line": line, "verified_line": actual_line})
    return corrections


def _is_plain_business_logic_line(source: str, line: int) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    def plain_expression(node: ast.AST) -> bool:
        if isinstance(node, (ast.Name, ast.Constant)):
            return True
        if isinstance(node, ast.BinOp):
            return isinstance(
                node.op,
                (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
            ) and plain_expression(node.left) and plain_expression(node.right)
        if isinstance(node, ast.Compare):
            return (
                plain_expression(node.left)
                and all(plain_expression(item) for item in node.comparators)
                and all(isinstance(
                    operator,
                    (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE),
                ) for operator in node.ops)
            )
        return False

    return any(
        isinstance(node, ast.Return)
        and node.lineno == line
        and plain_expression(node.value)
        for node in ast.walk(tree)
    )


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
        output = reviewer_outputs.get(reviewer, {})
        if output.get("status") != "completed":
            continue
        parsed = output.get("parsed")
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


def _reviewer_attributions(
    reviewer_outputs: Mapping[str, Mapping[str, object]],
) -> dict[tuple[object, ...], list[str]]:
    attributions: dict[tuple[object, ...], list[str]] = {}
    for reviewer in _REVIEWER_STAGES.values():
        output = reviewer_outputs.get(reviewer, {})
        parsed = output.get("parsed")
        raw_findings = parsed.get("findings") if isinstance(parsed, Mapping) else None
        if output.get("status") != "completed" or not isinstance(raw_findings, list):
            continue
        for raw_finding in raw_findings:
            if not isinstance(raw_finding, Mapping):
                continue
            finding = Finding.model_validate(raw_finding)
            key = (
                finding.file,
                finding.line,
                finding.description.strip().casefold(),
                finding.severity,
                finding.status,
                finding.fix,
            )
            attributions.setdefault(key, []).append(reviewer)
    return attributions


def _bind_synthesized_evidence(
    report: Mapping[str, object],
    reviewer_outputs: Mapping[str, Mapping[str, object]],
    source_contents: Mapping[str, str],
) -> tuple[dict[str, object], list[dict[str, int]] | None]:
    normalized = dict(report)
    findings = report.get("findings")
    if not isinstance(findings, list):
        return normalized, None
    normalized_findings = [
        dict(finding) if isinstance(finding, Mapping) else finding
        for finding in findings
    ]
    line_corrections = _correct_finding_lines(normalized_findings, source_contents)
    trusted_references: dict[str, tuple[object, object]] = {}
    for reviewer in _REVIEWER_STAGES.values():
        output = reviewer_outputs.get(reviewer, {})
        if output.get("status") != "completed":
            continue
        parsed = output.get("parsed")
        if not isinstance(parsed, Mapping) or not isinstance(parsed.get("findings"), list):
            continue
        for finding in parsed["findings"]:
            if isinstance(finding, Mapping):
                trusted_references[_finding_content_key(finding)] = (
                    finding.get("tool_reference"),
                    finding.get("test_reference"),
                )
    for index, finding in enumerate(normalized_findings):
        if not isinstance(finding, Mapping):
            continue
        item = dict(finding)
        references = trusted_references.get(_finding_content_key(item))
        item["tool_reference"] = references[0] if references else None
        item["test_reference"] = references[1] if references else None
        normalized_findings[index] = item
    normalized["findings"] = normalized_findings
    return normalized, line_corrections


def _reviewer_outputs(
    result: object,
    *,
    requested_files: Sequence[object] = (),
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
        read_sources = {
            reviewer: _read_tool_sources(messages, reviewer)
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
                except ValueError as error:
                    outputs[source] = {
                        "status": "unavailable",
                        "content": _stored_content(content),
                        "error": f"{type(error).__name__}: {error}",
                    }
                    continue
                parsed_findings = parsed_output.get("findings")
                line_corrections = _correct_finding_lines(
                    parsed_findings, read_sources[source]
                )
                if (
                    not requested_paths
                    or not requested_paths <= set(read_references[source])
                    or not set(read_references[source]) <= requested_paths
                    or not isinstance(parsed_findings, list)
                    or line_corrections is None
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
                        "error": (
                            "Reviewer did not read exactly the requested source files "
                            "or returned an unsupported finding line, excerpt, or "
                            "security classification."
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
                if line_corrections:
                    outputs[source]["line_corrections"] = line_corrections
            elif source == "TestAnalyst":
                if not isinstance(content, (str, Mapping, BaseModel)):
                    continue
                try:
                    parsed_output = extract_test_analyst_report(content)
                except ValueError as error:
                    outputs[source] = {
                        "status": "unavailable",
                        "content": _stored_content(content),
                        "error": f"{type(error).__name__}: {error}",
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
                if not valid_test_result:
                    outputs[source]["error"] = (
                        "TestAnalyst status does not match an available test-runner result."
                    )
            else:
                outputs[source] = {
                    "status": "unavailable",
                    "content": _stored_content(content),
                }
    for name in _REVIEWER_NAMES:
        if outputs[name]["content"] is None and "error" not in outputs[name]:
            outputs[name]["error"] = "No response was produced by this agent."
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


def _test_counts(test_results: object) -> tuple[int, int]:
    if not isinstance(test_results, Mapping):
        return 0, 0
    output = "\n".join(
        value for value in (test_results.get("stdout"), test_results.get("stderr"))
        if isinstance(value, str)
    )
    passed = re.search(r"\b(\d+)\s+passed\b", output)
    failed = re.search(r"\b(\d+)\s+failed\b", output)
    return (
        int(passed.group(1)) if passed else 0,
        int(failed.group(1)) if failed else 0,
    )


def _review_result(
    request: Mapping[str, object],
    *,
    stages: Mapping[str, object],
    test_results: object,
    reviewer_outputs: Mapping[str, Mapping[str, object]],
    autogen_report: Mapping[str, object] | None,
    missing_files: Sequence[object],
    release: Mapping[str, object],
    evidence_validation: Mapping[str, object],
) -> dict[str, object]:
    """Build the assignment's strict ReviewResult alongside diagnostic details."""
    tests_passed, tests_failed = _test_counts(test_results)
    reviewer_findings = _deduplicated_review_findings(reviewer_outputs)
    missing_checks = list(release.get("missing_checks", []))
    complete = (
        all(_status(stages.get(name)) in _COMPLETED_STATUSES for name in (
            "source_scan", "correctness", "security", "testing"
        ))
        and not missing_files
        and evidence_validation.get("verified") is True
    )
    release_decision = release["release_decision"]
    if release_decision == "PASS" and (
        not complete
        or missing_checks
        or release.get("blockers")
        or any(
            isinstance(finding, Mapping)
            and finding.get("status") == "unverified"
            for finding in reviewer_findings
        )
    ):
        release_decision = "NEEDS_HUMAN_REVIEW"
        missing_checks.append(
            "Report consistency checks do not support a PASS decision."
        )
    if missing_files:
        summary = (
            "Review was not completed because requested source files are missing: "
            + ", ".join(str(path) for path in missing_files)
            + "."
        )
    elif autogen_report is not None:
        summary_value = autogen_report.get("reviewer_summary")
        summary = (
            summary_value.strip()
            if isinstance(summary_value, str) and summary_value.strip()
            else "The reviewer synthesis did not provide a usable summary."
        )
    else:
        summaries = [
            parsed["reviewer_summary"]
            for output in reviewer_outputs.values()
            if output.get("status") == "completed"
            and isinstance((parsed := output.get("parsed")), Mapping)
            and isinstance(parsed.get("reviewer_summary"), str)
        ]
        summary = " ".join(summaries) or (
            "Reviewer synthesis is unavailable; the release decision is based on "
            "the checks and evidence that completed."
        )
    result = ReviewResult.model_validate({
        "review_id": request["review_id"],
        "commit_id": request["commit_id"],
        "scan_status": "complete" if complete else "incomplete",
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "findings": reviewer_findings,
        "release_decision": release_decision,
        "blockers": release.get("blockers", []),
        "missing_checks": list(dict.fromkeys(missing_checks)),
        "reviewer_summary": summary,
    })
    return result.model_dump(mode="json")


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
    reviewer_attributions = _reviewer_attributions(reviewer_outputs)
    for finding in combined_findings:
        if not isinstance(finding, dict) or finding.get("source") != "autogen":
            continue
        key = (
            finding.get("file"),
            finding.get("line"),
            str(finding.get("description", "")).strip().casefold(),
            finding.get("severity"),
            finding.get("status"),
            finding.get("fix"),
        )
        finding["reviewers"] = reviewer_attributions.get(key, [])

    evidence_findings = list(combined_findings)
    for reviewer in _REVIEWER_STAGES.values():
        output = reviewer_outputs.get(reviewer, {})
        if output.get("status") == "completed":
            continue
        parsed = output.get("parsed")
        findings = parsed.get("findings") if isinstance(parsed, Mapping) else None
        if isinstance(findings, list):
            evidence_findings.extend(
                finding for finding in findings if isinstance(finding, Mapping)
            )

    evidence_validation = validate_findings(
        evidence_findings,
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
    if missing_files:
        test_status = "unavailable"
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
                and _pipeline_checks_complete(trusted_pipeline)
                and not missing_files
                else "unavailable"
            )
        }
        for stage, reviewer in _REVIEWER_STAGES.items()
    })
    pipeline_test_status = _status(pipeline_test_results)
    testing_available = (
        pipeline_test_status in {"passed", "failed"}
        and not missing_files
    )
    stages["testing"] = {
        "status": "completed" if testing_available else "unavailable"
    }
    synthesis_available = (
        reviewer_outputs.get("ReportSynthesizer", {}).get("status") == "completed"
    )
    stages["report_synthesis"] = {
        "status": "completed" if synthesis_available else "unavailable"
    }
    release_results: dict[str, object] = {
        "stages": stages,
        "test_results": {"status": test_status},
        "findings": combined_findings,
        "evidence_validation": evidence_validation,
    }
    release = evaluate_release(release_results, policy)
    if not synthesis_available and release["release_decision"] != "BLOCK":
        release = {
            **release,
            "release_decision": "NEEDS_HUMAN_REVIEW",
            "missing_checks": list(dict.fromkeys([
                *release["missing_checks"],
                "report_synthesis",
            ])),
            "decision_reasons": list(dict.fromkeys([
                *release["decision_reasons"],
                "The consolidated report synthesis is missing or unavailable.",
            ])),
        }

    validated_report = _review_result(
        request,
        stages=stages,
        test_results=pipeline_test_results,
        reviewer_outputs=reviewer_outputs,
        autogen_report=autogen_report,
        missing_files=missing_files,
        release=release,
        evidence_validation=evidence_validation,
    )
    if validated_report["release_decision"] != release["release_decision"]:
        release = {
            **release,
            "release_decision": validated_report["release_decision"],
            "missing_checks": validated_report["missing_checks"],
            "decision_reasons": list(dict.fromkeys([
                *release["decision_reasons"],
                "The canonical report safety checks require human review.",
            ])),
        }

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
        "release_decision": validated_report["release_decision"],
        "validated_report": validated_report,
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

    autogen_report: dict[str, object] | None = None
    autogen_error: str | None = None
    reviewer_outputs: dict[str, dict[str, object]] = {
        name: {"status": "unavailable", "content": None}
        for name in _REVIEWER_NAMES
    }
    missing_sources = (
        pipeline_result.get("missing_files", [])
        if pipeline_result is not None
        else []
    )
    if not isinstance(missing_sources, Sequence) or isinstance(
        missing_sources, (str, bytes)
    ):
        missing_sources = []
    if missing_sources:
        missing_source_message = (
            "AutoGen review was not run because requested source files are missing: "
            f"{', '.join(str(path) for path in missing_sources)}."
        )
        reviewer_outputs = {
            name: {
                "status": "unavailable",
                "content": None,
                "error": missing_source_message,
            }
            for name in _REVIEWER_NAMES
        }
    else:
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
            messages = getattr(team_result, "messages", [])
            source_contents = {
                path: content
                for reviewer in _REVIEWER_STAGES.values()
                for path, content in _read_tool_sources(messages, reviewer).items()
            } if isinstance(messages, list) else {}
            reviewer_outputs["ReportSynthesizer"] = {
                **reviewer_outputs["ReportSynthesizer"],
                "status": "unavailable",
            }
            autogen_report, synthesized_line_corrections = _bind_synthesized_evidence(
                extract_synthesized_report(team_result),
                reviewer_outputs,
                source_contents,
            )
            if synthesized_line_corrections:
                reviewer_outputs["ReportSynthesizer"]["line_corrections"] = (
                    synthesized_line_corrections
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
            if reviewer_outputs["ReportSynthesizer"]["status"] != "completed":
                reviewer_outputs["ReportSynthesizer"]["error"] = (
                    "Synthesized output did not agree with the available structured "
                    "reviewer findings and test results."
                )
        except Exception as error:
            autogen_error = _error_message(error)
            reviewer_outputs = {
                name: (
                    {
                        **output,
                        "status": "unavailable",
                        "error": output.get("error") or autogen_error,
                    }
                    if output.get("content") is None
                    else output
                )
                for name, output in reviewer_outputs.items()
            }
            reviewer_outputs["ReportSynthesizer"] = {
                **reviewer_outputs["ReportSynthesizer"],
                "status": "unavailable",
                "error": autogen_error,
            }

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
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_REVIEWS)

    async def review(request: Mapping[str, object]) -> dict[str, object]:
        async with semaphore:
            return await _review_request(request, policy, policy_error)

    return list(await asyncio.gather(*(review(request) for request in requests)))


def main(argv: list[str] | None = None) -> int:
    """Load a request batch, run both review systems, and print JSON reports."""
    parser = argparse.ArgumentParser(
        description="Review repository changes and evaluate release readiness."
    )
    parser.add_argument("request_file", help="Path to a JSON request batch.")
    parser.add_argument(
        "--review-id",
        help="Review only the request with this ID instead of the full batch.",
    )
    args = parser.parse_args(argv)

    try:
        requests = (
            [load_request(args.request_file, args.review_id)]
            if args.review_id
            else load_requests(args.request_file)
        )
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
