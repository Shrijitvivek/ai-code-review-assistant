"""Top-level review workflow orchestration."""

from collections.abc import Mapping

from langchain_core.runnables import RunnableLambda, RunnableSequence

from src.pipeline.preparation import (
    PreparedReviewContext,
    identify_files,
    prepare_review_context,
    receive_request,
    validate_request,
)

review_pipeline = RunnableSequence(
    first=RunnableLambda(receive_request),
    middle=[
        RunnableLambda(validate_request),
        RunnableLambda(identify_files),
    ],
    last=RunnableLambda(prepare_review_context),
)


def prepare_review(
    request: Mapping[str, object],
) -> PreparedReviewContext:
    """Run request validation and repository preparation in sequence."""
    return review_pipeline.invoke(request)
