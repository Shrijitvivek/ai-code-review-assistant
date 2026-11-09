"""Temporary source inventory, AST analysis, and test-check adapters."""

import ast
import importlib.util
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

CheckResult = dict[str, object]
StaticFinding = dict[str, str | int]


def collect_source_inventory(context: Mapping[str, object]) -> CheckResult:
    """Read selected Python files and return basic source metadata."""
    try:
        sources = _read_selected_sources(context)
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        return {"status": "unavailable", "files": [], "error": str(error)}

    if not sources:
        return {
            "status": "unavailable",
            "files": [],
            "error": "There are no available selected Python files.",
        }

    files = [
        {
            "path": relative_path,
            "line_count": len(source.splitlines()),
            "size_bytes": len(source.encode("utf-8")),
        }
        for relative_path, source in sources
    ]
    return {"status": "completed", "files": files, "error": None}


def run_static_analysis(context: Mapping[str, object]) -> CheckResult:
    """Parse selected Python files and report a few basic AST issues."""
    try:
        sources = _read_selected_sources(context)
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        return {"status": "unavailable", "findings": [], "error": str(error)}

    if not sources:
        return {
            "status": "unavailable",
            "findings": [],
            "error": "There are no available selected Python files.",
        }

    findings: list[StaticFinding] = []
    for relative_path, source in sources:
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError as error:
            findings.append(
                {
                    "file": relative_path,
                    "line": error.lineno or 1,
                    "rule": "syntax-error",
                    "message": error.msg,
                }
            )
            continue

        visitor = _BasicIssueVisitor(relative_path)
        visitor.visit(tree)
        findings.extend(visitor.findings)

    return {"status": "completed", "findings": findings, "error": None}


def execute_repository_tests(context: Mapping[str, object]) -> CheckResult:
    """Run only pytest on sample_repo/test_cart.py with a 30-second timeout."""
    if not isinstance(context, Mapping):
        return {
            "status": "unavailable",
            "error": "Review context must be a mapping.",
        }

    try:
        repository_root = _sample_repository_root()
    except OSError as error:
        return {
            "status": "unavailable",
            "error": f"Unable to resolve the sample repository: {error}",
        }

    test_file = (repository_root / "test_cart.py").resolve(strict=False)
    if not test_file.is_relative_to(repository_root) or not test_file.is_file():
        return {
            "status": "unavailable",
            "error": "The fixed test target sample_repo/test_cart.py is unavailable.",
        }
    if importlib.util.find_spec("pytest") is None:
        return {"status": "unavailable", "error": "pytest is not installed."}

    test_env = os.environ.copy()
    test_env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                str(test_file),
            ],
            cwd=repository_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=test_env,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "status": "unavailable",
            "error": "The fixed pytest command timed out after 30 seconds.",
            "timed_out": True,
            "stdout": _as_text(error.stdout),
            "stderr": _as_text(error.stderr),
        }
    except OSError as error:
        return {
            "status": "unavailable",
            "error": f"Unable to start pytest: {error}",
        }

    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "error": None if result.returncode == 0 else "pytest reported a failure.",
    }


class _BasicIssueVisitor(ast.NodeVisitor):
    """Find a small set of potentially risky Python constructs."""

    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.findings: list[StaticFinding] = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.findings.append(
                {
                    "file": self.relative_path,
                    "line": node.lineno,
                    "rule": "bare-except",
                    "message": "Bare except catches all exception types.",
                }
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
            self.findings.append(
                {
                    "file": self.relative_path,
                    "line": node.lineno,
                    "rule": "dynamic-execution",
                    "message": f"Call to {node.func.id} executes dynamically supplied code.",
                }
            )
        self.generic_visit(node)


def _read_selected_sources(
    context: Mapping[str, object],
) -> list[tuple[str, str]]:
    """Read only validated selected files within the fixed sample repository."""
    if not isinstance(context, Mapping):
        raise TypeError("Review context must be a mapping.")

    selected_files = context.get("files")
    if not isinstance(selected_files, (list, tuple)):
        raise ValueError("Review context field 'files' must be a list or tuple.")

    repository_root = _sample_repository_root()
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()
    for selected_file in selected_files:
        if not isinstance(selected_file, str) or not selected_file.strip():
            raise ValueError("Selected file paths must be non-empty strings.")
        windows_path = PureWindowsPath(selected_file)
        if (
            Path(selected_file).is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
        ):
            raise ValueError(f"Absolute paths are not permitted: {selected_file}")

        normalized = selected_file.replace("\\", "/")
        relative_path = Path(normalized)
        if ".." in relative_path.parts:
            raise ValueError(f"Path traversal is not permitted: {selected_file}")
        if relative_path.suffix != ".py":
            raise ValueError(f"Only Python source files may be read: {selected_file}")

        resolved_path = (repository_root / relative_path).resolve(strict=True)
        if not resolved_path.is_relative_to(repository_root):
            raise ValueError(f"Selected file escapes the sample repository: {selected_file}")
        if not resolved_path.is_file():
            raise ValueError(f"Selected path is not a file: {selected_file}")

        relative_name = relative_path.as_posix()
        if relative_name not in seen:
            sources.append((relative_name, resolved_path.read_text(encoding="utf-8")))
            seen.add(relative_name)
    return sources


def _sample_repository_root() -> Path:
    """Resolve the project's fixed sample repository directory."""
    return (Path(__file__).resolve().parents[2] / "sample_repo").resolve(strict=True)


def _as_text(value: str | bytes | None) -> str:
    """Normalize captured subprocess output for structured results."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""
