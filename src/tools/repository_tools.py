"""Read-only tools restricted to the project's sample repository."""

from __future__ import annotations

import json
from pathlib import Path

from autogen_core.tools import FunctionTool

from src.models.review_models import validate_source_path


_MAX_FILE_CHARACTERS = 100_000


def _repository_root() -> Path:
    """Return the fixed sample repository root, independent of the caller's cwd."""
    return (Path(__file__).resolve().parents[2] / "sample_repo").resolve(strict=True)


def _resolve_sample_file(relative_path: str) -> Path:
    """Validate a relative Python path and prevent symlink escapes."""
    validate_source_path(relative_path)
    root = _repository_root()
    candidate = (root / relative_path).resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise ValueError("Requested path is outside the sample repository")
    if not candidate.is_file() or candidate.suffix.casefold() != ".py":
        raise ValueError("Requested path is not a Python source file")
    return candidate


def list_repository_files() -> str:
    """List Python source files available under the fixed sample repository."""
    root = _repository_root()
    paths: list[str] = []
    for candidate in root.rglob("*.py"):
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.is_relative_to(root) and resolved.is_file():
                paths.append(candidate.relative_to(root).as_posix())
        except (OSError, RuntimeError):
            continue
    return json.dumps({"status": "ok", "files": sorted(set(paths))})


def read_repository_file(relative_path: str) -> str:
    """Read a UTF-8 Python file by relative path inside the fixed sample repository."""
    try:
        source = _resolve_sample_file(relative_path)
        content = source.read_text(encoding="utf-8")
    except FileNotFoundError:
        return json.dumps({"status": "unavailable", "error": "File does not exist."})
    except (OSError, UnicodeError, ValueError) as error:
        return json.dumps({"status": "denied", "error": str(error)})

    if len(content) > _MAX_FILE_CHARACTERS:
        return json.dumps({
            "status": "truncated",
            "path": relative_path,
            "content": content[:_MAX_FILE_CHARACTERS],
            "limit": _MAX_FILE_CHARACTERS,
        })
    return json.dumps({
        "status": "ok",
        "path": relative_path,
        "content": content,
        "tool_reference": f"read_repository_file:{relative_path}",
    })


def repository_read_tools() -> list[FunctionTool]:
    """Return the fixed set of read-only tools usable by reviewer agents."""
    return [
        FunctionTool(
            list_repository_files,
            description=list_repository_files.__doc__ or "",
            strict=True,
        ),
        FunctionTool(
            read_repository_file,
            description=read_repository_file.__doc__ or "",
            strict=True,
        ),
    ]
