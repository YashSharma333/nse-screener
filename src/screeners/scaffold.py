"""
Screener Scaffolding Generator CLI (E1)
=======================================
Generates a production-ready, contract-compliant systematic Screener module
and companion unit test from a template.

Usage:
------
python -m src.screeners.scaffold --key my_breakout --name "My Breakout" --category breakout
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config.settings import PROJECT_ROOT

SCREENER_TEMPLATE = '''"""
{name} Screener
================{underline}
{description}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.screeners.base import Screener
from src.screeners.conditions.price import cond_above_sma
from src.screeners.conditions.volume import cond_volume_surge
from src.screeners.registry import register_screener


@dataclass(frozen=True)
class {class_name}Params:
    """Configurable parameters for {name} Screener."""

    sma_window: int = 50
    volume_surge_mult: float = 1.5


@register_screener(key="{key}")
class {class_name}Screener(Screener):
    """{description}"""

    key = "{key}"
    name = "{name}"
    description = "{description}"
    category = "{category}"
    version = "1.0"
    status: Literal["core", "experimental"] = "{status}"

    evidence_columns: tuple[str, ...] = (
        "close",
        "sma_50",
        "volume",
        "volume_sma_20",
    )
    required_columns: tuple[str, ...] = (
        "close",
        "sma_50",
        "volume",
        "volume_sma_20",
    )
    params_cls = {class_name}Params

    def price_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {{
            "above_50_sma": cond_above_sma(df, sma_col="sma_50"),
        }}

    def volume_conditions(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        params: {class_name}Params = self.params
        return {{
            "volume_surge": cond_volume_surge(df, multiplier=params.volume_surge_mult),
        }}
'''

TEST_TEMPLATE = '''"""
Unit tests for {name} Screener.
"""

from __future__ import annotations

import pandas as pd

from src.screeners.{key} import {class_name}Params, {class_name}Screener
from src.screeners.registry import get_screener


def test_{key}_screener_instantiation():
    screener = get_screener("{key}")
    assert screener.key == "{key}"
    assert screener.status == "{status}"
    assert isinstance(screener.params, {class_name}Params)


def test_{key}_screener_execution():
    df = pd.DataFrame({{
        "symbol": ["SYM1", "SYM2"],
        "date": [pd.to_datetime("2024-01-01").date()] * 2,
        "close": [110.0, 90.0],
        "sma_50": [100.0, 100.0],
        "volume": [300000.0, 100000.0],
        "volume_sma_20": [150000.0, 150000.0],
    }})

    screener = {class_name}Screener()
    passed = screener.run(df)
    assert len(passed) == 1
    assert passed.iloc[0]["symbol"] == "SYM1"

    explained = screener.explain(df)
    assert explained["passed"].tolist() == [True, False]
    assert "cond_above_50_sma" in explained.columns
    assert "cond_volume_surge" in explained.columns
'''


def scaffold_screener(
    key: str,
    name: str,
    category: str = "general",
    description: str = "",
    status: str = "experimental",
    root_dir: Path = PROJECT_ROOT,
) -> tuple[Path, Path]:
    """Generate screener module and companion unit test."""
    clean_key = key.lower().replace("-", "_").strip()
    class_name = "".join(word.capitalize() for word in clean_key.split("_"))
    desc = description or f"Systematic screening for {name} setups."
    underline = "=" * len(name)

    code = SCREENER_TEMPLATE.format(
        name=name,
        key=clean_key,
        class_name=class_name,
        category=category.lower(),
        description=desc,
        status=status,
        underline=underline,
    )

    test_code = TEST_TEMPLATE.format(
        name=name,
        key=clean_key,
        class_name=class_name,
        status=status,
    )

    screener_file = root_dir / "src" / "screeners" / f"{clean_key}.py"
    test_file = root_dir / "tests" / f"test_screener_{clean_key}.py"

    screener_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.parent.mkdir(parents=True, exist_ok=True)

    screener_file.write_text(code, encoding="utf-8")
    test_file.write_text(test_code, encoding="utf-8")

    return screener_file, test_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate systematic screener scaffold")
    parser.add_argument("--key", required=True, help="Unique screener snake_case key")
    parser.add_argument("--name", required=True, help="Human-readable screener title")
    parser.add_argument(
        "--category", default="general", help="Category: trend, breakout, swing, etc."
    )
    parser.add_argument("--description", default="", help="Plain-language description")
    parser.add_argument("--status", choices=["core", "experimental"], default="experimental")
    parser.add_argument("--root-dir", default=None, help="Root directory (default: project root)")

    args = parser.parse_args()
    root = Path(args.root_dir) if args.root_dir else PROJECT_ROOT

    try:
        mod_p, test_p = scaffold_screener(
            key=args.key,
            name=args.name,
            category=args.category,
            description=args.description,
            status=args.status,
            root_dir=root,
        )
        print(f"✓ Created Screener module: {mod_p.relative_to(root)}")
        print(f"✓ Created Unit test:       {test_p.relative_to(root)}")
        print("\nNext steps:")
        print(f"1. Add 'import src.screeners.{args.key}' to src/screeners/__init__.py")
        print(f"2. Customize screening conditions in {mod_p.name}")
        print(f"3. Run: pytest {test_p}")
    except Exception as exc:
        print(f"✗ Scaffolding failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
