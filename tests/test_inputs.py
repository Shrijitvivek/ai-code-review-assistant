import json
import pytest
from src.data.request_loader import load_request, load_requests, missing_sources


def row():
    return dict(review_id="R001", commit_id="demo-a1", files=["cart.py"], message="Review")


def test_load_and_select(tmp_path):
    path = tmp_path / "requests.json"
    path.write_text(json.dumps([row()]), encoding="utf-8")
    assert load_requests(path)[0]["review_id"] == "R001"
    assert load_request(path, "R001")["commit_id"] == "demo-a1"
    with pytest.raises(ValueError, match="Unknown review_id"):
        load_request(path, "R999")


@pytest.mark.parametrize("payload", ['{', '{}', '[null]', '[1]', '[{}]',
    '[{"review_id":"R1","review_id":"R2"}]', 'null'])
def test_bad_json_or_records(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        load_requests(path)


def test_missing_input(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_requests(tmp_path / "missing.json")


def test_duplicate_ids(tmp_path):
    path = tmp_path / "requests.json"
    path.write_text(json.dumps([row(), row()]), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate review_id"):
        load_requests(path)


def test_missing_source_is_traceable(tmp_path):
    from src.models.review_models import ReviewRequest
    request = ReviewRequest(**row())
    assert missing_sources(request, tmp_path) == ["cart.py"]
    assert missing_sources(row(), tmp_path) == ["cart.py"]
    (tmp_path / "cart.py").write_text("pass\n", encoding="utf-8")
    assert missing_sources(request, tmp_path) == []


def test_empty_batch(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("[]", encoding="utf-8")
    assert load_requests(path) == []


@pytest.mark.parametrize("review_id", ["R001", "R002", "R003", "R004"])
def test_loaded_request_passes_directly_to_existing_pipeline(monkeypatch, review_id):
    from pathlib import Path
    from types import SimpleNamespace
    from src.pipeline import checks
    from src.pipeline.review_pipeline import prepare_review

    calls = []

    def fixed_test_run(command, **options):
        assert command[1:3] == ["-m", "pytest"]
        assert Path(command[-1]).name == "test_cart.py"
        assert options["timeout"] == 30
        assert options.get("shell", False) is False
        assert Path(options["cwd"]).name == "sample_repo"
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="4 passed", stderr="")

    monkeypatch.setattr(checks.subprocess, "run", fixed_test_run)
    path = Path(__file__).resolve().parents[1] / "data" / "review_requests.json"
    request = load_request(path, review_id)
    result = prepare_review(request)
    # The current pipeline returns request metadata, the check results, and
    # release-gate output alongside these three check fields.
    assert {"inventory", "static_analysis", "test_results"} <= result.keys()
    if review_id == "R003":
        assert result["test_results"]["status"] == "unavailable"
        assert result["test_results"]["missing_files"] == ["missing.py"]
        assert calls == []
    else:
        assert result["test_results"]["status"] == "passed"
        assert len(calls) == 1  # Request text cannot suppress the fixed test check.
    expected = "unavailable" if review_id == "R003" else "completed"
    assert result["inventory"]["status"] == expected
