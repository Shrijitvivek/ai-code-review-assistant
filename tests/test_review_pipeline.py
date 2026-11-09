import subprocess
from pathlib import Path

import pytest

from src.pipeline.preparation import PreparedReviewContext
from src.pipeline.review_pipeline import prepare_review, review_pipeline


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def test_pipeline_prepares_context_for_changed_files(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "Test User")
    _git(repository, "config", "user.email", "test@example.com")
    file_path = repository / "reviewed.py"
    file_path.write_text("value = 1\n", encoding="utf-8")
    _git(repository, "add", "reviewed.py")
    _git(repository, "commit", "-m", "initial")
    base_ref = _git(repository, "rev-parse", "HEAD")

    file_path.write_text("value = 2\n", encoding="utf-8")
    _git(repository, "commit", "-am", "change")
    head_ref = _git(repository, "rev-parse", "HEAD")

    context = prepare_review(
        {
            "repository": str(repository),
            "base_ref": base_ref,
            "head_ref": head_ref,
        }
    )

    assert isinstance(context, PreparedReviewContext)
    assert context.files == ("reviewed.py",)
    assert "+value = 2" in context.diff


def test_pipeline_rejects_missing_request_fields(tmp_path) -> None:
    with pytest.raises(ValueError, match="base_ref"):
        review_pipeline.invoke(
            {"repository": str(tmp_path), "head_ref": "HEAD"}
        )


def test_pipeline_rejects_non_repository_path(tmp_path) -> None:
    with pytest.raises(ValueError, match="Git command failed"):
        prepare_review(
            {
                "repository": str(tmp_path),
                "base_ref": "HEAD",
                "head_ref": "HEAD",
            }
        )
