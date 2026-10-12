#!/usr/bin/env python3
"""Check that this checkout is ready to be released into the stable branch.

    python scripts/release_check.py [BASE]     BASE: the stable branch's ref (default: origin/stable)

Passes when kangedit.__version__ is newer than the version on BASE (or BASE doesn't exist yet) and
CHANGELOG.md has a non-empty section for it. Prints the version on success.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bump_version  # noqa: E402
import changelog  # noqa: E402

ROOT = bump_version.INIT.parent.parent


def base_version(ref: str) -> tuple[int, int, int] | None:
    r = subprocess.run(["git", "show", f"{ref}:kangedit/__init__.py"], cwd=ROOT, capture_output=True, text=True)
    m = bump_version.PATTERN.search(r.stdout) if r.returncode == 0 else None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def main(args: list[str]) -> None:
    base = args[0] if args else "origin/stable"
    new = bump_version.current()
    version = ".".join(map(str, new))
    old = base_version(base)
    errors = []
    if old is not None and new <= old:
        errors.append(f"Version {version} must be newer than {'.'.join(map(str, old))} on {base}: "
                      f"run scripts/bump_version.py patch|minor|major.")
    text = changelog.CHANGELOG.read_text(encoding="utf-8") if changelog.CHANGELOG.exists() else ""
    if not changelog.section(version, text):
        errors.append(f"CHANGELOG.md has no section for {version}: rename [Unreleased] to [{version}] - date.")
    if errors:
        sys.exit("\n".join(errors))
    print(version)


if __name__ == "__main__":
    main(sys.argv[1:])
