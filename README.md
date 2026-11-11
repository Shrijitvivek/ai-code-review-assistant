# AI Code Review & Release Readiness Assistant — Group 3

Pallavi's contribution provides validated inputs, Pydantic report contracts,
deterministic tests, and documentation.

## Install

Use Python 3.11 or 3.12 for the assignment. From the repository root on Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m pytest tests -q
```

On macOS/Linux use `python3.12 -m venv .venv`, `source .venv/bin/activate`,
and `cp .env.example .env`. Set `OPENAI_API_KEY` in your private `.env`.
The configured provider is OpenAI and `OPENAI_MODEL=gpt-4o-mini`.
Tests and input validation do not require a key or network.

## Execute and validate inputs

```powershell
python -m src.main
python -c "from src.data.request_loader import load_requests; print(load_requests('data/review_requests.json'))"
python -c "from src.data.request_loader import load_request; print(load_request('data/review_requests.json', 'R001'))"
python -c "import json; from src.models.review_models import report_json_schema; print(json.dumps(report_json_schema(), indent=2))"
```

The request file contains R001?R004. Validate source paths against `sample_repo`.
To run the preparation pipeline directly with a validated request:

```powershell
python -c "from src.data.request_loader import load_request; from src.pipeline.review_pipeline import prepare_review; print(prepare_review(load_request('data/review_requests.json', 'R001')))"
```

## Data contracts

`load_requests(path)` validates records with `ReviewRequest` and returns
`list[dict]` for direct compatibility with the preparation pipeline.
`load_request(path, review_id)` returns one validated dictionary. To work with
a Pydantic instance, use `ReviewRequest.model_validate(request)`.
Requests require `review_id`, `commit_id`, nonempty relative Python `files`, and
`message`. Unknown keys, duplicate IDs, duplicate JSON keys and invalid records
raise `ValueError`; unreadable files raise `OSError`. `load_request` rejects
unknown IDs. Empty batches are allowed. Request paths resolve relative to the
explicit repository root, never relative to the JSON file.

`missing_sources` identifies missing source checks. `resolve_source` denies
traversal, absolute paths, non-Python files and symlinks escaping the root.
`validate_finding_location` checks that a cited file and line exist. This does
not verify the claim: the evidence component must validate tool references,
request scope and commit identity before confirming a finding.
`deduplicate_findings` merges matching descriptions and locations across
categories while retaining evidence; pass findings from only one snapshot.

`ReviewResult.model_validate(payload)` and `.model_dump_json(indent=2)` provide
validated JSON output. [JSON schema](docs/review_report.schema.json) defines
all report fields and closed enums. Findings include the assignment fields
plus a required `description` to identify the claim. Severities are uppercase;
categories are `correctness`, `security`, `testing`; finding status is
`confirmed` or `unverified`. Scan status is `complete`, `incomplete`, or `failed`.
Release decisions are `PASS`, `BLOCK`, or `NEEDS_HUMAN_REVIEW`.

Runtime report validation rejects non-BLOCK decisions when tests fail or a
critical finding is confirmed. PASS requires a complete scan, no missing
checks/blockers, and no unverified findings. These are safety invariants;
the team release gate must calculate the decision using policy and validated
evidence. JSON Schema alone does not express these cross-field rules.
Revalidate after modifying models; mutable collections and `model_copy` are
not substitutes for final validation. Never merge evidence from different commits.

## Architecture and evaluation

![Architecture](docs/architecture.png)

See [evaluation](docs/evaluation_report.md) and [demo results](docs/demo_results.md).
Run `python -m pytest tests -q` for the deterministic core suite. Run the supplied
buggy repository's tests separately from its directory using
`python -m pytest -q`; expected fixture failures must not be hidden or repaired.
The current local sample differs from the supplied assignment fixture.
