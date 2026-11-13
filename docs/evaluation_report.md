# Evaluation report — Pallavi, Group 3

Evaluation date: 10 November 2026. Scope: input handling, Pydantic contracts,
report safety invariants, deterministic tests and documentation.

## Observed results

Command: `.venv\Scripts\python.exe -m pytest tests/test_models.py tests/test_inputs.py tests/test_release_gate.py tests/test_security.py -q`

Result: **54 passed, 1 skipped**. The skipped test requires Windows permission
to create a symlink; traversal and extension rejection tests passed. Tests use
local temporary files and no OpenAI calls. Environment: Python 3.14.7,
Pydantic 2.13.4, pytest 8.3.5. Python 3.11/3.12 is the assignment target.

| Area | Verified behavior |
| --- | --- |
| Inputs | Batch and ID selection; unknown ID; malformed JSON; wrong shapes; missing fields; duplicate JSON keys and review IDs; missing input file; empty batch |
| Pipeline compatibility | R001?R004 loader dictionaries accepted directly; fixed pytest target and 30-second deadline checked with a mocked subprocess |
| Models | JSON round trip; closed enums; forbidden extra fields; strict positive line numbers and nonnegative test counts; nonempty evidence |
| Source safety | Traversal, absolute Windows/POSIX paths, alternate streams, NULs and wrong extensions denied; missing source reported |
| Evidence | Existing file and in-range line required; matching claims deduplicated while retaining evidence |
| Report safety | Failed tests and confirmed critical findings require BLOCK; incomplete/missing checks cannot PASS |
| Injection | Malicious request text loaded without execution |

The original ZIP's four requests were validated without replacing the local
data. R001/R002/R004 had no missing requested sources in the ZIP fixture;
R003 reported `missing.py`. A fabricated PASS with one failed test was rejected
by report validation. See demo results for the observed cases.
