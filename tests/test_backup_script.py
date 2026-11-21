"""Tests for scripts/backup_db.sh: one backup folder, a validity check, and rotation (D14)."""

from __future__ import annotations

import gzip
import os
import shutil
import stat
import subprocess
from pathlib import Path

from config.settings import PROJECT_ROOT

OLD_TIME = 1_600_000_000  # September 2020: older than any file the script creates now


def _setup(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """A throwaway project with the real script, a fake mysqldump and a fake HOME."""
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "backup_db.sh"
    shutil.copy(PROJECT_ROOT / "scripts" / "backup_db.sh", script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "mysqldump"
    # Prints a tiny dump, or fails when FAKE_DUMP_FAIL is set.
    fake.write_text('#!/bin/sh\n[ -n "$FAKE_DUMP_FAIL" ] && exit 1\necho "-- Dump completed"\n')
    fake.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home),
        "MYSQL_READONLY_USER": "reader",
        "MYSQL_READONLY_PASSWORD": "not-a-real-password",
    }
    env.pop("BACKUP_DIR", None)
    env.pop("KEEP_BACKUPS", None)
    return script, home, env


def _run(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, check=False
    )


def _old_backups(folder: Path, count: int) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(count):
        path = folder / f"nse_screener_2020-01-0{i + 1}_000000.sql.gz"
        path.write_bytes(gzip.compress(b"old"))
        os.utime(path, (OLD_TIME + i, OLD_TIME + i))
        files.append(path)
    return files


def test_default_folder_is_nse_backups_under_home(tmp_path: Path) -> None:
    script, home, env = _setup(tmp_path)

    proc = _run(script, env)

    assert proc.returncode == 0, proc.stderr
    files = list((home / "nse-backups").glob("nse_screener_*.sql.gz"))
    assert len(files) == 1
    assert b"Dump completed" in gzip.decompress(files[0].read_bytes())
    assert not (home / "Documents").exists()  # never the iCloud-synced folder


def test_only_the_newest_backups_are_kept(tmp_path: Path) -> None:
    script, home, env = _setup(tmp_path)
    folder = home / "nse-backups"
    olds = _old_backups(folder, 6)
    other = folder / "another_db_2020-01-01_000000.sql.gz"
    other.write_bytes(gzip.compress(b"x"))
    env["KEEP_BACKUPS"] = "3"

    proc = _run(script, env)

    assert proc.returncode == 0, proc.stderr
    remaining = sorted(p.name for p in folder.glob("nse_screener_*.sql.gz"))
    assert len(remaining) == 3  # the new dump plus the 2 newest old ones
    assert olds[5].name in remaining and olds[4].name in remaining
    assert olds[0].exists() is False
    assert other.exists()  # another database's backup is never touched


def test_failed_dump_keeps_old_backups_and_leaves_no_partial_file(tmp_path: Path) -> None:
    script, home, env = _setup(tmp_path)
    folder = home / "nse-backups"
    olds = _old_backups(folder, 5)
    env["KEEP_BACKUPS"] = "1"
    env["FAKE_DUMP_FAIL"] = "1"

    proc = _run(script, env)

    assert proc.returncode == 1
    assert "Backup FAILED" in proc.stderr
    assert sorted(folder.glob("*.sql.gz")) == sorted(olds)  # nothing new, nothing deleted


def test_keep_backups_must_be_a_positive_number(tmp_path: Path) -> None:
    script, _home, env = _setup(tmp_path)
    env["KEEP_BACKUPS"] = "0"

    proc = _run(script, env)

    assert proc.returncode == 1
    assert "KEEP_BACKUPS" in proc.stderr
