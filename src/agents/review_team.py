"""Construction and execution of the bounded AutoGen review team."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.messages import StructuredMessage
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_ext.models.openai import OpenAIChatCompletionClient
from dotenv import load_dotenv
from pydantic import BaseModel, TypeAdapter, ValidationError

from src.agents.prompts import format_review_task
from src.models.review_models import (
    ReviewerReport,
    SynthesizedReport,
    TestAnalystReport,
)
from src.agents.reviewers import (
    CorrectnessReviewer,
    ReportSynthesizer,
    SecurityReviewer,
    TestAnalyst,
)


_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_MAX_MESSAGES = 12
_MAX_ALLOWED_MESSAGES = 48


def _configured_message_limit(value: int | None = None) -> int:
    if value is None:
        raw_value = os.getenv("MAX_AGENT_MESSAGES", str(_DEFAULT_MAX_MESSAGES))
        try:
            value = int(raw_value)
        except ValueError as error:
            raise ValueError("MAX_AGENT_MESSAGES must be an integer") from error
    if not 1 <= value <= _MAX_ALLOWED_MESSAGES:
        raise ValueError(
            f"MAX_AGENT_MESSAGES must be between 1 and {_MAX_ALLOWED_MESSAGES}"
        )
    return value


def create_model_client() -> OpenAIChatCompletionClient:
    """Create the configured OpenAI-compatible model client from local settings."""
    env_file = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(dotenv_path=env_file, override=False)

    model = os.getenv("OPENAI_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key == "your_api_key_here":
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. Set it in the environment or project .env file."
        )

    base_url = (
        os.getenv("OPENAI_BASE_URL", "").strip()
        or "https://api.openai.com/v1"
    )
    return OpenAIChatCompletionClient(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


def create_review_team(
    model_client: Any,
    *,
    max_messages: int | None = None,
) -> RoundRobinGroupChat:
    """Build the four-role team with both completion and message-count stops.

    The supplied model client remains owned by the caller. Use
    :func:`run_review_team` for a one-shot run with managed client cleanup.
    """
    message_limit = _configured_message_limit(max_messages)
    termination = TextMentionTermination("REVIEW_COMPLETE") | MaxMessageTermination(
        max_messages=message_limit
    )
    return RoundRobinGroupChat(
        participants=[
            CorrectnessReviewer(model_client),
            SecurityReviewer(model_client),
            TestAnalyst(model_client),
            ReportSynthesizer(model_client),
        ],
        termination_condition=termination,
        custom_message_types=[
            StructuredMessage[ReviewerReport],
            StructuredMessage[TestAnalystReport],
            StructuredMessage[SynthesizedReport],
        ],
    )


async def run_review_team(
    request: Mapping[str, object],
    *,
    model_client: Any | None = None,
    max_messages: int | None = None,
) -> Any:
    """Run a fresh team for one request and return AutoGen's task result.

    The optional client is useful for caller-managed configuration and tests. A
    client created here is closed before returning, including on failure.
    """
    owns_client = model_client is None
    active_client = model_client if model_client is not None else create_model_client()
    try:
        team = create_review_team(active_client, max_messages=max_messages)
        task = format_review_task(request)
        return await team.run(task=task)
    finally:
        if owns_client:
            await active_client.close()


def extract_synthesized_report(result: Any) -> dict[str, object]:
    """Validate and return the final JSON report emitted by ReportSynthesizer."""
    messages = getattr(result, "messages", None)
    if not isinstance(messages, list):
        raise ValueError("AutoGen result does not contain a message list")

    content: object | None = None
    for message in reversed(messages):
        if getattr(message, "source", None) != "ReportSynthesizer":
            continue
        candidate = getattr(message, "content", None)
        if isinstance(candidate, (str, SynthesizedReport)):
            content = candidate
            break
    if content is None:
        raise ValueError("AutoGen result has no text from ReportSynthesizer")

    if isinstance(content, str):
        lines = content.strip().splitlines()
        if not lines or lines[-1].strip() != "REVIEW_COMPLETE":
            raise ValueError("ReportSynthesizer did not complete the report")
        payload: object = "\n".join(lines[:-1]).strip()
    else:
        payload = content
    parsed = _extract_json_object(payload, expected_keys={
        "findings", "reviewer_summary", "test_status"
    })
    if parsed is None:
        raise ValueError("ReportSynthesizer returned invalid or incomplete JSON")
    if set(parsed) != {"findings", "reviewer_summary", "test_status"}:
        raise ValueError("Synthesized report has an invalid object shape")
    try:
        report = TypeAdapter(SynthesizedReport).validate_python(parsed)
    except (ValidationError, TypeError) as error:
        raise ValueError("Synthesized report does not match the report schema") from error

    return {
        "findings": [finding.model_dump(mode="json") for finding in report.findings],
        "reviewer_summary": report.reviewer_summary,
        "test_status": report.test_status,
    }


def extract_reviewer_report(
    content: object, *, expected_category: str | None = None
) -> dict[str, object]:
    """Parse and validate a reviewer response using the existing report schema."""
    parsed = _extract_json_object(
        content, expected_keys={"findings", "reviewer_summary"}
    )
    if parsed is None or set(parsed) != {"findings", "reviewer_summary"}:
        raise ValueError("Reviewer returned invalid or incomplete JSON")
    try:
        report = TypeAdapter(ReviewerReport).validate_python(parsed)
    except (ValidationError, TypeError) as error:
        raise ValueError("Reviewer response does not match the report schema") from error
    summary_words = report.reviewer_summary.casefold().split()
    if len(summary_words) < 5 or " ".join(summary_words) in {
        "review completed",
        "review complete",
        "analysis complete",
        "looks good",
        "no issues found",
    }:
        raise ValueError("Reviewer response is missing a meaningful summary")
    if expected_category is not None and any(
        finding.category != expected_category for finding in report.findings
    ):
        raise ValueError("Reviewer finding has an invalid category for its role")
    return report.model_dump(mode="json")


def extract_test_analyst_report(content: object) -> dict[str, object]:
    """Parse and validate the TestAnalyst's explicit test status and evidence."""
    parsed = _extract_json_object(
        content, expected_keys={"test_status", "test_evidence"}
    )
    if parsed is None or set(parsed) != {"test_status", "test_evidence"}:
        raise ValueError("TestAnalyst returned invalid or incomplete JSON")
    try:
        report = TypeAdapter(TestAnalystReport).validate_python(parsed)
    except (ValidationError, TypeError) as error:
        raise ValueError("TestAnalyst response does not match the report schema") from error
    return report.model_dump(mode="json")


def extract_test_runner_status(result: Any) -> str | None:
    """Return only the status from an actual run_repository_tests tool message."""
    messages = getattr(result, "messages", None)
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        source = getattr(message, "source", None)
        content = getattr(message, "content", None)
        if source == "run_repository_tests" and isinstance(content, str):
            status = _test_status_from_json(content)
            if status is not None:
                return status
        if source == "TestAnalyst" and isinstance(content, list):
            for result_message in content:
                if getattr(result_message, "name", None) != "run_repository_tests":
                    continue
                tool_content = getattr(result_message, "content", None)
                if isinstance(tool_content, str):
                    status = _test_status_from_json(tool_content)
                    if status is not None:
                        return status
    return None


def _test_status_from_json(content: str) -> str | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    status = parsed.get("status") if isinstance(parsed, dict) else None
    return status if status in {"passed", "failed", "unavailable"} else None


def _extract_json_object(
    content: object, *, expected_keys: set[str]
) -> dict[str, object] | None:
    if isinstance(content, BaseModel):
        parsed_content = content.model_dump(mode="json")
        return parsed_content if isinstance(parsed_content, dict) else None
    if isinstance(content, Mapping):
        return dict(content)
    if not isinstance(content, str):
        return None

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError(f"Duplicate JSON key: {key}")
            parsed[key] = value
        return parsed

    def reject_non_json_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON constant: {value}")

    decoder = json.JSONDecoder(
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_json_constant,
    )
    candidates: list[dict[str, object]] = []
    incomplete_candidate = False
    malformed_candidate = False
    index = 0
    while index < len(content):
        if content[index] != "{":
            index += 1
            continue
        try:
            parsed, end = decoder.raw_decode(content, index)
        except (json.JSONDecodeError, ValueError):
            tail = content[index:]
            closing = tail.find("}")
            malformed_text = tail if closing < 0 else tail[: closing + 1]
            if any(f'"{key}"' in malformed_text for key in expected_keys):
                malformed_candidate = True
            index += 1
            continue
        if isinstance(parsed, dict):
            keys = set(parsed)
            if expected_keys <= keys:
                candidates.append(parsed)
            elif expected_keys & keys:
                incomplete_candidate = True
        index = end

    if malformed_candidate or incomplete_candidate or len(candidates) != 1:
        return None
    return candidates[0]
