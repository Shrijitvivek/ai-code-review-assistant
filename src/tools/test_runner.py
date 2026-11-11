"""A fixed, bounded test tool for the sample repository."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


_MAX_OUTPUT_CHARACTERS = 12_000


def _test_timeout() -> int:
    try:
        configured = int(os.getenv("TEST_TIMEOUT_SECONDS", "30"))
    except ValueError:
        configured = 30
    return min(max(configured, 1), 120)


def run_repository_tests() -> str:
    """Run only sample_repo/test_cart.py with pytest; accepts no command or path."""
    root = (Path(__file__).resolve().parents[2] / "sample_repo").resolve(strict=True)
    target = root / "test_cart.py"
    if not target.is_file():
        return json.dumps({
            "status": "unavailable",
            "error": "The fixed test target sample_repo/test_cart.py is unavailable.",
        })

    command = [sys.executable, "-m", "pytest", "-q", "test_cart.py"]
    try:
        result = subprocess.run(
            command,
            cwd=root,
            shell=False,
            capture_output=True,
            text=True,
            timeout=_test_timeout(),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return json.dumps({
            "status": "unavailable",
            "error": f"Fixed test target exceeded the {_test_timeout()} second timeout.",
            "stdout": stdout[-_MAX_OUTPUT_CHARACTERS:],
            "stderr": stderr[-_MAX_OUTPUT_CHARACTERS:],
        })
    except OSError as error:
        return json.dumps({"status": "unavailable", "error": str(error)})

    status = "passed" if result.returncode == 0 else "failed"
    return json.dumps({
        "status": status,
        "returncode": result.returncode,
        "command": ["python", "-m", "pytest", "-q", "test_cart.py"],
        "stdout": result.stdout[-_MAX_OUTPUT_CHARACTERS:],
        "stderr": result.stderr[-_MAX_OUTPUT_CHARACTERS:],
    })
