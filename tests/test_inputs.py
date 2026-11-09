import json

import pytest

from src.data.request_loader import load_requests


def test_loads_request_array(tmp_path) -> None:
    path = tmp_path / "requests.json"
    path.write_text(json.dumps([{"repository": "sample_repo"}]), encoding="utf-8")

    assert load_requests(path) == [{"repository": "sample_repo"}]


def test_rejects_non_object_request(tmp_path) -> None:
    path = tmp_path / "requests.json"
    path.write_text(json.dumps(["invalid"]), encoding="utf-8")

    with pytest.raises(ValueError, match="array of objects"):
        load_requests(path)
