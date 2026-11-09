"""Example file operations for exercising repository reviews."""

from pathlib import Path


def read_text(path: str | Path) -> str:
    """Read a UTF-8 text file."""
    return Path(path).read_text(encoding="utf-8")
