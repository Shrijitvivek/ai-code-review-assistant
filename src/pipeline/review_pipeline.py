"""Top-level review workflow orchestration."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from langchain_core.runnables import RunnableLambda, RunnableParallel, RunnableSequence

from src.pipeline.branches import review_branch
from src.pipeline.checks import (
    collect_source_inventory,
    execute_repository_tests,
    run_static_analysis,
)
from src.pipeline.preparation import (
    identify_files,
    prepare_review_context,
    receive_request,
    validate_request,
)
from src.release.evidence_validator import validate_findings
from src.release.release_gate import evaluate_release


def _route_review_context(context: Mapping[str, object]) -> dict[str, object]:
    """Add the branch's route result without dropping prepared review data."""
    # No trusted release-blocking metadata is currently supplied by preparation.
    # Keep the default non-blocking; never infer this policy from the message.
    branch_input = {**context, "release_blocking": False}
    route = review_branch.invoke(branch_input)
    return {**context, **route}


def load_review_policy() -> Mapping[str, object]:
    """Load the configured release policy without supplying implicit defaults."""
    policy_path = Path(__file__).resolve().parents[2] / "data" / "review_policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(policy, Mapping):
        raise ValueError(f"Review policy must be a JSON object: {policy_path}")
    return policy


def _finalize_review(results: Mapping[str, object]) -> dict[str, object]:
    """Validate findings and attach the deterministic release decision."""
    context = results.get("context")
    if not isinstance(context, Mapping):
        raise TypeError("Review pipeline result is missing its prepared context.")

    checks = {
        "inventory": results.get("inventory"),
        "static_analysis": results.get("static_analysis"),
        "test_results": results.get("test_results"),
    }
    static_analysis = checks["static_analysis"]
    findings_value = (
        static_analysis.get("findings")
        if isinstance(static_analysis, Mapping)
        else None
    )
    findings_are_valid = (
        isinstance(findings_value, Sequence)
        and not isinstance(findings_value, (str, bytes))
        and all(isinstance(finding, Mapping) for finding in findings_value)
    )
    if findings_are_valid:
        findings: object = list(findings_value)
    else:
        findings = findings_value
        error = "Static analysis returned malformed or unavailable findings."
        if isinstance(static_analysis, Mapping):
            checks["static_analysis"] = {
                **static_analysis,
                "status": "unavailable",
                "error": static_analysis.get("error") or error,
            }
        else:
            checks["static_analysis"] = {
                "status": "unavailable",
                "error": error,
                "result": static_analysis,
            }
    trusted_references = (
        {
            reference
            for finding in findings_value
            if isinstance(finding, Mapping)
            for reference in (finding.get("tool_reference"),)
            if isinstance(reference, str) and reference.strip()
        }
        if findings_are_valid
        else set()
    )
    evidence_validation = validate_findings(
        findings,
        allowed_references=trusted_references,
    )

    test_results = checks["test_results"]
    inventory = checks["inventory"]
    static_analysis = checks["static_analysis"]
    source_scan_complete = (
        isinstance(inventory, Mapping)
        and inventory.get("status") == "completed"
        and isinstance(static_analysis, Mapping)
        and static_analysis.get("status") == "completed"
        and findings_are_valid
        and not context.get("missing_files")
    )
    stages: dict[str, object] = {
        "source_scan": {
            "status": "completed" if source_scan_complete else "unavailable"
        },
        "correctness": {"status": "unavailable"},
        "security": {"status": "unavailable"},
        "testing": test_results,
    }
    release_results: dict[str, object] = {
        "checks": checks,
        "stages": stages,
        "test_results": test_results,
        "findings": findings,
        "evidence_validation": evidence_validation,
    }
    release = evaluate_release(release_results, load_review_policy())

    return {
        **context,
        **{key: value for key, value in results.items() if key != "context"},
        "checks": checks,
        "findings": findings,
        "evidence_validation": evidence_validation,
        "release_decision": release["release_decision"],
        "blockers": release["blockers"],
        "missing_checks": release["missing_checks"],
        "decision_reasons": release["decision_reasons"],
    }


review_pipeline = RunnableSequence(
    first=RunnableLambda(receive_request),
    middle=[
        RunnableLambda(validate_request),
        RunnableLambda(identify_files),
        RunnableLambda(prepare_review_context),
        RunnableLambda(_route_review_context),
    ],
    last=RunnableParallel(
        context=RunnableLambda(lambda context: context),
        inventory=RunnableLambda(collect_source_inventory),
        static_analysis=RunnableLambda(run_static_analysis),
        test_results=RunnableLambda(execute_repository_tests),
    )
    | RunnableLambda(_finalize_review),
)


def prepare_review(
    request: Mapping[str, object],
) -> dict[str, object]:
    """Run checks, validate findings, and return a release decision."""
    return review_pipeline.invoke(request)
