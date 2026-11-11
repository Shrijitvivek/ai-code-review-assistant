"""Read untrusted JSON without executing request text or source files."""
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from pydantic import TypeAdapter
from src.models.review_models import Finding, ReviewRequest, validate_source_path


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_requests(path: str | Path) -> list[dict[str, Any]]:
    """Validate the whole batch and return dictionaries for pipeline consumers.

    Missing sources remain valid requests (R003).

    Raises OSError for unreadable files, JSONDecodeError for malformed JSON,
    and ValueError (including Pydantic ValidationError) for invalid contracts.
    """
    with Path(path).open(encoding="utf-8-sig") as stream:
        payload = json.load(stream, object_pairs_hook=_unique_keys)
    requests = TypeAdapter(list[ReviewRequest]).validate_python(payload)
    ids = [request.review_id for request in requests]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate review_id")
    return [request.model_dump(mode="json") for request in requests]


def load_request(path: str | Path, review_id: str) -> dict[str, Any]:
    for request in load_requests(path):
        if request["review_id"] == review_id:
            return request
    raise ValueError(f"Unknown review_id: {review_id}")


def resolve_source(repository: str | Path, relative_path: str) -> Path:
    """Resolve existing Python files; deny traversal and escaping symlinks."""
    validate_source_path(relative_path)
    root = Path(repository).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    source = (root / relative_path).resolve(strict=True)
    if not source.is_relative_to(root):
        raise ValueError("Source path escapes repository root")
    if not source.is_file() or source.suffix != ".py":
        raise ValueError("Source must be a Python file")
    return source


def missing_sources(
    request: ReviewRequest | Mapping[str, object], repository: str | Path
) -> list[str]:
    request = ReviewRequest.model_validate(
        request.model_dump() if isinstance(request, ReviewRequest) else dict(request)
    )
    root = Path(repository).resolve(strict=True)
    missing = []
    for name in request.files:
        try:
            resolve_source(root, name)
        except FileNotFoundError:
            missing.append(name)
    return missing


def validate_finding_location(finding: Finding, repository: str | Path) -> Finding:
    """Verify location, not the factual claim or evidence reference itself."""
    source = resolve_source(repository, finding.file)
    if finding.line > len(source.read_text(encoding="utf-8").splitlines()):
        raise ValueError("Finding line exceeds source length")
    return finding


def deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    """Merge identical claims across categories, preserving evidence.

    Caller must supply findings from one commit. Conflicting severities or
    confirmation states remain separate for the evidence validator to resolve.
    """
    unique = {}
    for finding in findings:
        key = (finding.file, finding.line, finding.description.strip().casefold(),
               finding.severity, finding.status, finding.fix)
        if key in unique:
            previous = unique[key]
            unique[key] = previous.model_copy(update={
                "evidence": list(dict.fromkeys(previous.evidence + finding.evidence))})
        else:
            unique[key] = finding.model_copy(deep=True)
    return list(unique.values())
