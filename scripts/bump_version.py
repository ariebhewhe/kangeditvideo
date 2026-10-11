#!/usr/bin/env python3
"""Show, bump or check KangEdit's version (kangedit/__init__.py is the single source; semantic versioning).

    python scripts/bump_version.py                 print the current version
    python scripts/bump_version.py patch           1.2.3 -> 1.2.4   (also: minor, major)
    python scripts/bump_version.py 2.0.0           set an explicit version
    python scripts/bump_version.py --check v1.2.3  exit 1 unless the tag matches the version

Prints the resulting version on the last line.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parent.parent / "kangedit" / "__init__.py"
PATTERN = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.M)
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def current() -> tuple[int, int, int]:
    m = PATTERN.search(INIT.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f'No __version__ = "X.Y.Z" line in {INIT}')
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def write(version: str) -> None:
    text = INIT.read_text(encoding="utf-8")
    INIT.write_text(PATTERN.sub(f'__version__ = "{version}"', text, count=1), encoding="utf-8", newline="\n")


def main(args: list[str]) -> None:
    major, minor, patch = current()
    now = f"{major}.{minor}.{patch}"
    if not args:
        print(now)
        return
    if args[0] == "--check":
        tag = args[1] if len(args) > 1 else ""
        if tag.removeprefix("refs/tags/").removeprefix("v") != now:
            sys.exit(f"Tag {tag!r} does not match kangedit.__version__ {now!r}. "
                     f"Run scripts/bump_version.py first, or tag v{now}.")
        print(now)
        return
    part = args[0]
    new = {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0",
           "patch": f"{major}.{minor}.{patch + 1}"}.get(part, part.removeprefix("v"))
    if not SEMVER.match(new):
        sys.exit(f"Not a version or bump part: {part!r} (use major, minor, patch or X.Y.Z)")
    if tuple(map(int, new.split("."))) <= (major, minor, patch):
        sys.exit(f"New version {new} must be greater than {now}")
    write(new)
    print(new)


if __name__ == "__main__":
    main(sys.argv[1:])
