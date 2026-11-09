# AI Code Review Assistant

A Python project scaffold for preparing repository changes, coordinating review
agents, validating evidence, and producing review results.

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest
```

Run the application scaffold with:

```bash
python -m src.main
```

Most pipeline, agent, tool, and release modules are intentionally starting
points. Implement them as the review workflow and its policies are defined.

## Project layout

- `src/pipeline/` — review preparation, orchestration, and branch handling
- `src/agents/` — reviewer roles, team coordination, and prompts
- `src/tools/` — repository inspection and test execution
- `src/models/` — shared review types and enums
- `src/release/` — release gates and evidence validation
- `src/data/` — loading review requests and related data
- `sample_repo/` — small repository used for examples and tests
- `tests/` — automated checks
- `data/` — example policy and request inputs
- `output/` — generated review artifacts (kept out of version control)
