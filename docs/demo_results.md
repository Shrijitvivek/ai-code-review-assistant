# Demo results

These are observed **contract-level** demonstrations on 10 November 2026. The four requests and source files were read
from the supplied nested Group 3 ZIP into a temporary directory, then passed
to `load_requests` and `missing_sources`. No fixture was overwritten.

| Case | Observed result |
| --- | --- |
| R001 | Valid request; cart.py and file_ops.py found |
| R002 | Valid request; cart.py found |
| R003 | Valid request; missing_sources returned [missing.py] |
| R004 | Valid request; injection message preserved exactly as data |
| Unsafe report | ReviewResult rejected PASS with tests_failed=1 |

Reproduce input checks using the README loader commands and the extracted
Group 3 paths. `python -m pytest tests -q` reproduces the deterministic model,
input, security and release-invariant cases. To produce a JSON report, construct
`ReviewResult` from actual tool results, call `model_validate`, and serialize
with `model_dump_json(indent=2)`.
