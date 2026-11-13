"""Policy-based routing for prepared review requests."""

from collections.abc import Mapping
from typing import TypedDict

from langchain_core.runnables import RunnableBranch, RunnableLambda


class ReviewRouteResult(TypedDict):
    """Structured routing directive for a review request."""

    route: str
    action: str
    review_id: str
    commit_id: str
    missing_files: list[str]


def _routing_context(context: object) -> tuple[str, str, list[str], bool]:
    """Validate structured routing fields; never infer policy from user text."""
    if not isinstance(context, Mapping):
        raise TypeError("Review routing context must be a mapping.")

    review_id = context.get("review_id")
    if not isinstance(review_id, str) or not review_id.strip():
        raise ValueError("Review routing context requires a non-empty review_id.")

    commit_id = context.get("commit_id")
    if not isinstance(commit_id, str) or not commit_id.strip():
        raise ValueError("Review routing context requires a non-empty commit_id.")

    missing_files = context.get("missing_files", ())
    if not isinstance(missing_files, (list, tuple)) or any(
        not isinstance(path, str) or not path.strip() for path in missing_files
    ):
        raise ValueError(
            "Review routing context field 'missing_files' must be a list of paths."
        )

    release_blocking = context.get("release_blocking", False)
    if not isinstance(release_blocking, bool):
        raise ValueError(
            "Review routing context field 'release_blocking' must be a boolean."
        )

    return review_id, commit_id, list(missing_files), release_blocking


def _has_missing_files(context: object) -> bool:
    return bool(_routing_context(context)[2])


def _is_release_blocking(context: object) -> bool:
    return _routing_context(context)[3]


def _route_result(
    context: object,
    *,
    route: str,
    action: str,
) -> ReviewRouteResult:
    review_id, commit_id, missing_files, _ = _routing_context(context)
    return {
        "route": route,
        "action": action,
        "review_id": review_id,
        "commit_id": commit_id,
        "missing_files": missing_files,
    }


review_branch = RunnableBranch(
    (
        _has_missing_files,
        RunnableLambda(
            lambda context: _route_result(
                context,
                route="missing_file_handling",
                action="report_missing_files",
            )
        ),
    ),
    (
        _is_release_blocking,
        RunnableLambda(
            lambda context: _route_result(
                context,
                route="urgent_review_handling",
                action="prioritize_review",
            )
        ),
    ),
    RunnableLambda(
        lambda context: _route_result(
            context,
            route="standard_review_handling",
            action="perform_standard_review",
        )
    ),
)
