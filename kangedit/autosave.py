"""Automatic project saving: rolling backups of saved projects, and a recovery copy of untitled ones.

A saved project is written to its own file, and the version it replaces is first copied into a backup
folder in the per-user data folder (at most one copy every few minutes, the newest BACKUP_KEEP kept).
An untitled project cannot be saved in place, so a recovery copy is kept instead and offered again
after a crash.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import app_data_dir
from .models import Project

AUTOSAVE_SECONDS = 60          # how often a changed project is saved
BACKUP_KEEP = 20               # backups kept per project
BACKUP_EVERY = 300.0           # seconds: at most one backup per project in this time
RECOVERY_STALE = 180.0         # seconds: older recovery copies come from an app that is no longer running
SOURCE_FILE = "source.txt"     # in a backup folder: the project file the backups are of


def backup_root() -> Path:
    path = app_data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_dir(project_path: str) -> Path:
    """The backup folder of one project file (named after it, unique per path)."""
    full = os.path.normcase(os.path.abspath(project_path))
    stem = Path(project_path).stem
    return backup_root() / f"{stem}-{hashlib.sha1(full.encode('utf-8')).hexdigest()[:8]}"


def backups(project_path: str) -> list[Path]:
    """Backups of a project file, newest first."""
    d = backup_dir(project_path)
    if not d.is_dir():
        return []
    return sorted(d.glob("*" + Project.FILE_EXT), key=lambda p: p.stat().st_mtime, reverse=True)


def make_backup(project_path: str, min_interval: float = BACKUP_EVERY, keep: int = BACKUP_KEEP,
                now: Optional[float] = None) -> Optional[Path]:
    """Copy the project file as it is on disk (before it is overwritten) into its backup folder, unless the
    newest backup is younger than min_interval. Prunes to the newest `keep`. Returns the new backup."""
    if not os.path.isfile(project_path):
        return None
    now = time.time() if now is None else now
    old = backups(project_path)
    if old and now - old[0].stat().st_mtime < min_interval:
        return None
    d = backup_dir(project_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / SOURCE_FILE).write_text(os.path.abspath(project_path), encoding="utf-8")
    stamp = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H-%M-%S")
    dest = d / f"{Path(project_path).stem} {stamp}{Project.FILE_EXT}"
    shutil.copy2(project_path, dest)
    os.utime(dest, (now, now))
    for extra in backups(project_path)[keep:]:
        try:
            extra.unlink()
        except OSError:
            pass
    return dest


def backup_source(backup: str) -> Optional[str]:
    """The project file a backup was made of (None when unknown)."""
    try:
        return (Path(backup).parent / SOURCE_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


# ---------------------------------------------------------------- untitled projects
def _recovery_dir() -> Path:
    path = app_data_dir() / "autosave"
    path.mkdir(parents=True, exist_ok=True)
    return path


def recovery_path() -> Path:
    """This app's recovery copy (one per running app)."""
    return _recovery_dir() / f"untitled-{os.getpid()}{Project.FILE_EXT}"


def write_recovery(project: Project) -> Path:
    """Save an untitled project's recovery copy (the project stays untitled and unsaved)."""
    path = recovery_path()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(project.to_dict(), fh, indent=1)
    os.replace(tmp, path)
    return path


def clear_recovery() -> None:
    try:
        recovery_path().unlink()
    except OSError:
        pass


def stale_recoveries(now: Optional[float] = None) -> list[Path]:
    """Recovery copies left by an app that closed without saving (newest first)."""
    now = time.time() if now is None else now
    mine = recovery_path()
    found = [p for p in _recovery_dir().glob("untitled-*" + Project.FILE_EXT)
             if p != mine and now - p.stat().st_mtime > RECOVERY_STALE]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)
