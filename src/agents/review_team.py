"""Construction and execution of the bounded AutoGen review team."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_ext.models.openai import OpenAIChatCompletionClient
from dotenv import load_dotenv
from pydantic import TypeAdapter, ValidationError

from src.agents.prompts import format_review_task
from src.models.review_models import Finding
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

    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
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

    content: str | None = None
    for message in reversed(messages):
        if getattr(message, "source", None) != "ReportSynthesizer":
            continue
        candidate = getattr(message, "content", None)
        if isinstance(candidate, str):
            content = candidate
            break
    if content is None:
        raise ValueError("AutoGen result has no text from ReportSynthesizer")

    lines = content.strip().splitlines()
    if not lines or lines[-1].strip() != "REVIEW_COMPLETE":
        raise ValueError("ReportSynthesizer did not complete the report")
    payload = "\n".join(lines[:-1]).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", payload, flags=re.DOTALL)
    if fenced:
        payload = fenced.group(1)

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError("ReportSynthesizer returned invalid JSON") from error
    if not isinstance(parsed, dict) or set(parsed) != {
        "findings", "reviewer_summary", "test_status"
    }:
        raise ValueError("Synthesized report has an invalid object shape")
    if not isinstance(parsed["reviewer_summary"], str) or not parsed[
        "reviewer_summary"
    ].strip():
        raise ValueError("Synthesized report is missing its reviewer summary")
    if parsed["test_status"] not in {"passed", "failed", "unavailable", "unknown"}:
        raise ValueError("Synthesized report has an invalid test status")
    try:
        findings = TypeAdapter(list[Finding]).validate_python(parsed["findings"])
    except (ValidationError, TypeError) as error:
        raise ValueError("Synthesized report contains invalid findings") from error

    return {
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "reviewer_summary": parsed["reviewer_summary"].strip(),
        "test_status": parsed["test_status"],
    }
