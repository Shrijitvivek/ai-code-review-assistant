# AI Code Review & Release Readiness Assistant 

Pallavi's contribution provides validated inputs, Pydantic report contracts, deterministic tests, and documentation.

## Installation

Use Python 3.11 or 3.12 for the assignment.

### Windows (PowerShell)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m pytest tests -q
```

### macOS / Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m pytest tests -q
```

Set `OPENAI_API_KEY` in your private `.env` file. The configured provider is OpenAI, with `OPENAI_MODEL=gpt-4o-mini`.

Tests and input validation do not require an API key or network access.

**Important:** Never commit your `.env` file or expose API keys in source control.

## Execute the application and validate inputs

Run the application from the repository root:

```bash
python -m src.main data/review_requests.json
```

Validate all requests:

```bash
python -c "from src.data.request_loader import load_requests; print(load_requests('data/review_requests.json'))"
```

Validate a specific request:

```bash
python -c "from src.data.request_loader import load_request; print(load_request('data/review_requests.json', 'R001'))"
```

Display the report JSON schema:

```bash
python -c "import json; from src.models.review_models import report_json_schema; print(json.dumps(report_json_schema(), indent=2))"
```

The request file contains review scenarios R001–R004. Source paths are validated against `sample_repo`.

To run the preparation pipeline directly with a validated request:

```bash
python -c "from src.data.request_loader import load_request; from src.pipeline.review_pipeline import prepare_review; print(prepare_review(load_request('data/review_requests.json', 'R001')))"
```

## Data contracts and validation

### Request loading

- `load_requests(path)` validates records using `ReviewRequest` and returns `list[dict]` for compatibility with the preparation pipeline.
- `load_request(path, review_id)` returns one validated request dictionary.
- To create a Pydantic instance, use `ReviewRequest.model_validate(request)`.

Requests require:
- `review_id`
- `commit_id`
- A non-empty list of relative Python file paths
- `message`

Unknown keys, duplicate IDs, duplicate JSON keys, and invalid records raise `ValueError`. Unreadable files raise `OSError`. `load_request` rejects unknown IDs. Empty batches are allowed.

Request paths resolve relative to the explicit repository root, not relative to the JSON file.

### Source and evidence validation

- `missing_sources` identifies missing source checks.
- `resolve_source` denies path traversal, absolute paths, non-Python files, and symlinks escaping the repository root.
- `validate_finding_location` checks whether a cited file and line exist.
- Evidence validation must also check tool references, request scope, and commit identity before confirming a finding.

A valid file location alone does not prove that a finding is correct.

### Finding deduplication

`deduplicate_findings` merges matching descriptions and locations across categories while retaining evidence. Pass findings from only one repository snapshot to avoid mixing evidence from different versions.

### Report contracts

`ReviewResult.model_validate(payload)` validates a report, while `.model_dump_json(indent=2)` produces formatted JSON output.

The JSON Schema is available at:

`docs/review_report.schema.json`

Findings include the assignment fields and a required `description` field identifying the issue.

Supported values:

| Field | Allowed values |
|---|---|
| Severity | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| Category | `correctness`, `security`, `testing` |
| Finding status | `confirmed`, `unverified` |
| Scan status | `complete`, `incomplete`, `failed` |
| Release decision | `PASS`, `BLOCK`, `NEEDS_HUMAN_REVIEW` |

### Release decision safety rules

Runtime report validation rejects non-`BLOCK` decisions when tests fail or a critical finding is confirmed.

A `PASS` decision requires:
- A complete scan.
- No missing checks.
- No blockers.
- No unverified findings.

These are safety invariants. The team release gate must calculate the final decision using the configured policy and validated evidence.

JSON Schema alone does not express these cross-field rules.

Revalidate reports after modifying models. Mutable collections and `model_copy` are not substitutes for final validation. Never merge evidence from different commits.

## Architecture and evaluation

![Architecture](docs/architecture.png)

See the following project documentation:

- [Evaluation Report](docs/evaluation_report.md)
- [Demo Results](docs/demo_results.md)

## Testing

Run the deterministic core test suite from the repository root:

```bash
python -m pytest tests -q
```

Run the supplied buggy repository's tests separately from its directory:

```bash
cd sample_repo
python -m pytest -q
```

Expected fixture failures must not be hidden or repaired.

`sample_repo/cart.py` intentionally retains the three assignment defects (subtotal addition, incorrect percentage discount, and exclusive free-shipping threshold). `sample_repo/test_cart.py` asserts the intended behavior, so running the sample tests directly is expected to fail. The review pipeline executes those tests and deterministically returns `BLOCK` when they fail. Do not repair the intentional sample-source bugs.

## Current implementation status

The application demonstrates the LangChain preparation pipeline, AutoGen multi-agent review workflow, test execution, evidence validation, and deterministic release decisions.

The current demo includes four scenarios:
- R001 – Standard code review.
- R002 – Release readiness review.
- R003 – Missing source file handling.
- R004 – Prompt injection handling.

Some agent outputs may be unavailable or require further validation. Review the generated reports and release decisions rather than assuming every review has completed successfully.