"""Which command-line commands understand the --dry-run flag.

Why this file exists: --dry-run is one global flag, but each command has to
implement it by itself. A command that does not would quietly write to the
database anyway (issue #46). The CLI uses this list to refuse that combination.
"""

from __future__ import annotations

import argparse

# Names of the argparse options (without the leading "--") whose commands honour --dry-run.
DRY_RUN_COMMANDS: tuple[str, ...] = (
    "import_universe",
    "import_symbol_change_aliases",
    "apply_symbol_aliases",
    "purge_bogus_sessions",
    "fill_missing_symbols",
    "detect_renames",  # read-only anyway
)


def dry_run_is_supported(args: argparse.Namespace) -> bool:
    """Return False when --dry-run is given with a command that would ignore it.

    Without --dry-run there is nothing to refuse, so the answer is True.
    """
    if not args.dry_run:
        return True
    return any(getattr(args, name, False) for name in DRY_RUN_COMMANDS)
