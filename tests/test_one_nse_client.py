"""CL-20: NSE is downloaded through ONE client (src/ingestion/nse_files.py).

Why a test: three separate HTTP clients (own headers, own warm-up, own retry rules) had grown
back after the first download helper was written. This fails the build if a second one appears.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THE_ONE_CLIENT = Path("src/ingestion/nse_files.py")


def _python_files() -> list[Path]:
    """Every project Python file that ships code (not tests, not the virtualenv)."""
    folders = ("src", "scripts", "ui", "config")
    files = [ROOT / "run_pipeline.py"]
    for folder in folders:
        files.extend((ROOT / folder).rglob("*.py"))
    return [path for path in files if path.exists()]


def _imports_requests(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "requests" for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "requests":
            return True
    return False


def _creates_session(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "Session":
                return True
    return False


def test_only_nse_files_imports_requests() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _python_files()
        if path.relative_to(ROOT) != THE_ONE_CLIENT
        and _imports_requests(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert offenders == [], f"These modules talk HTTP themselves; use nse_files.fetch: {offenders}"


def test_requests_session_is_created_in_one_module_only() -> None:
    creators = [
        str(path.relative_to(ROOT))
        for path in _python_files()
        if _creates_session(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert creators == [str(THE_ONE_CLIENT)]
