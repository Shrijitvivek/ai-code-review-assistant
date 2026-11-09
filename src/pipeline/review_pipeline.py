"""Top-level review workflow orchestration."""

from collections.abc import Mapping

from langchain_core.runnables import RunnableLambda, RunnableParallel, RunnableSequence

from src.pipeline.checks import (
    CheckResult,
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

review_pipeline = RunnableSequence(
    first=RunnableLambda(receive_request),
    middle=[
        RunnableLambda(validate_request),
        RunnableLambda(identify_files),
        RunnableLambda(prepare_review_context),
    ],
    last=RunnableParallel(
        inventory=RunnableLambda(collect_source_inventory),
        static_analysis=RunnableLambda(run_static_analysis),
        test_results=RunnableLambda(execute_repository_tests),
    ),
)


def prepare_review(
    request: Mapping[str, object],
) -> dict[str, CheckResult]:
    """Prepare a review request and run the independent checks in parallel."""
    return review_pipeline.invoke(request)
