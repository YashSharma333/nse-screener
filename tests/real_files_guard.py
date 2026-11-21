"""Detect tests that change real files under data/ or reports/ (task D14).

Kept separate from conftest.py so the detection logic itself can be tested.
"""

from __future__ import annotations

from pathlib import Path

GUARDED_FOLDERS = ("data", "reports")


def snapshot(project_root: Path) -> dict[str, tuple[int, int]]:
    """Return {path: (size, modified time in ns)} for every file in the guarded folders."""
    seen: dict[str, tuple[int, int]] = {}
    for name in GUARDED_FOLDERS:
        for path in (project_root / name).rglob("*"):
            if path.is_file() and path.name != ".DS_Store":
                info = path.stat()
                seen[str(path)] = (info.st_size, info.st_mtime_ns)
    return seen


def find_changes(
    before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]
) -> list[str]:
    """Describe every file that is new, changed or removed between two snapshots."""
    lines = [f"changed or new: {path}" for path in sorted(after) if before.get(path) != after[path]]
    lines += [f"removed: {path}" for path in sorted(before) if path not in after]
    return lines
