"""Preparation of explicitly requested repository files for review."""

from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import TypedDict


class ReceivedRequest(TypedDict):
    """Incoming request wrapper passed between preparation stages."""

    raw_request: Mapping[str, object]


class ValidatedRequest(TypedDict):
    """Validated request data and the fixed repository root."""

    review_id: str
    commit_id: str
    files: tuple[str, ...]
    message: str
    repository_root: Path


class SelectedFiles(TypedDict):
    """Validated request data partitioned into available and missing files."""

    review_id: str
    commit_id: str
    selected_files: tuple[str, ...]
    missing_files: tuple[str, ...]
    message: str
    repository_root: Path


class PreparedReviewContext(TypedDict):
    """Structured context passed to the later review stages."""

    review_id: str
    commit_id: str
    files: tuple[str, ...]
    missing_files: tuple[str, ...]
    message: str
    repository_root: Path


def receive_request(request: Mapping[str, object]) -> ReceivedRequest:
    """Wrap a mapping request for validation."""
    if not isinstance(request, Mapping):
        raise TypeError("Review request must be a mapping.")
    return {"raw_request": request}


def validate_request(data: ReceivedRequest) -> ValidatedRequest:
    """Validate required request fields and resolve the fixed sample repository."""
    raw_request = data["raw_request"]
    review_id = _required_string(raw_request, "review_id")
    commit_id = _required_string(raw_request, "commit_id")
    message = _required_string(raw_request, "message")
    requested_files = raw_request.get("files")
    if not isinstance(requested_files, (list, tuple)) or not requested_files:
        raise ValueError("Review request field 'files' must be a non-empty list of paths.")

    files: list[str] = []
    for file_path in requested_files:
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("Every requested file path must be a non-empty string.")
        files.append(file_path.strip())

    repository_root = (Path(__file__).resolve().parents[2] / "sample_repo").resolve(
        strict=True
    )
    if not repository_root.is_dir():
        raise ValueError(f"Sample repository is not a directory: {repository_root}")

    return {
        "review_id": review_id,
        "commit_id": commit_id,
        "files": tuple(files),
        "message": message,
        "repository_root": repository_root,
    }


def identify_files(data: ValidatedRequest) -> SelectedFiles:
    """Select safe requested Python files and record unavailable files."""
    repository_root = data["repository_root"]
    selected_files: list[str] = []
    missing_files: list[str] = []

    for requested_path in data["files"]:
        windows_path = PureWindowsPath(requested_path)
        if (
            Path(requested_path).is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
        ):
            raise ValueError(f"Absolute paths are not permitted: {requested_path}")

        normalized_path = requested_path.replace("\\", "/")
        relative_path = Path(normalized_path)
        if ".." in relative_path.parts:
            raise ValueError(f"Path traversal is not permitted: {requested_path}")
        if relative_path.suffix != ".py":
            raise ValueError(f"Only Python source files may be reviewed: {requested_path}")

        candidate = (repository_root / relative_path).resolve(strict=False)
        if not candidate.is_relative_to(repository_root):
            raise ValueError(f"Requested file escapes the sample repository: {requested_path}")
        if candidate.is_file():
            if relative_path.as_posix() not in selected_files:
                selected_files.append(relative_path.as_posix())
        else:
            if relative_path.as_posix() not in missing_files:
                missing_files.append(relative_path.as_posix())

    return {
        "review_id": data["review_id"],
        "commit_id": data["commit_id"],
        "selected_files": tuple(selected_files),
        "missing_files": tuple(missing_files),
        "message": data["message"],
        "repository_root": repository_root,
    }


def prepare_review_context(data: SelectedFiles) -> PreparedReviewContext:
    """Build the final context without interpreting the request message."""
    return {
        "review_id": data["review_id"],
        "commit_id": data["commit_id"],
        "files": data["selected_files"],
        "missing_files": data["missing_files"],
        "message": data["message"],
        "repository_root": data["repository_root"],
    }


def _required_string(values: Mapping[str, object], field: str) -> str:
    value = values.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Review request field '{field}' must be a non-empty string.")
    return value.strip()
