"""AutoGen reviewer agents with role-specific prompts and limited tools."""

from __future__ import annotations

from typing import Any

from autogen_agentchat.agents import AssistantAgent

from src.agents.prompts import (
    CORRECTNESS_REVIEWER_PROMPT,
    REPORT_SYNTHESIZER_PROMPT,
    SECURITY_REVIEWER_PROMPT,
    TEST_ANALYST_PROMPT,
)
from src.models.review_models import (
    ReviewerReport,
    SynthesizedReport,
    TestAnalystReport,
)
from src.tools.repository_tools import repository_read_tools
from src.tools.test_runner import repository_test_tool


def CorrectnessReviewer(model_client: Any) -> AssistantAgent:
    """Create the correctness reviewer with sample-repository read tools only."""
    return AssistantAgent(
        name="CorrectnessReviewer",
        model_client=model_client,
        system_message=CORRECTNESS_REVIEWER_PROMPT,
        tools=repository_read_tools(),
        output_content_type=ReviewerReport,
        max_tool_iterations=3,
    )


def SecurityReviewer(model_client: Any) -> AssistantAgent:
    """Create the security reviewer with sample-repository read tools only."""
    return AssistantAgent(
        name="SecurityReviewer",
        model_client=model_client,
        system_message=SECURITY_REVIEWER_PROMPT,
        tools=repository_read_tools(),
        output_content_type=ReviewerReport,
        max_tool_iterations=3,
    )


def TestAnalyst(model_client: Any) -> AssistantAgent:
    """Create the test analyst with read tools and one fixed test command."""
    return AssistantAgent(
        name="TestAnalyst",
        model_client=model_client,
        system_message=TEST_ANALYST_PROMPT,
        tools=[*repository_read_tools(), repository_test_tool()],
        output_content_type=TestAnalystReport,
        max_tool_iterations=3,
    )


def ReportSynthesizer(model_client: Any) -> AssistantAgent:
    """Create the final reviewer; it receives no repository or execution tools."""
    return AssistantAgent(
        name="ReportSynthesizer",
        model_client=model_client,
        system_message=REPORT_SYNTHESIZER_PROMPT,
        output_content_type=SynthesizedReport,
    )
