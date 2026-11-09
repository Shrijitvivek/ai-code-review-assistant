"""Load review requests from JSON files."""

import json
from pathlib import Path
from typing import Any


def load_requests(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSON array of review request objects.

    Raises:
        ValueError: If the JSON document is not an array of objects.
        json.JSONDecodeError: If the file contains invalid JSON.
        OSError: If the file cannot be read.
    """
    with Path(path).open(encoding="utf-8") as request_file:
        requests = json.load(request_file)

    if not isinstance(requests, list) or not all(
        isinstance(request, dict) for request in requests
    ):
        raise ValueError("Review requests must be a JSON array of objects.")

    return requests
