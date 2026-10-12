#!/usr/bin/env python3
"""Print one version's section of CHANGELOG.md (used as the GitHub release notes).

    python scripts/changelog.py 1.2.3     the body under "## [1.2.3] ..." (empty if there is none)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def section(version: str, text: str) -> str:
    version = version.removeprefix("v")
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |^\[[^\]]+\]: |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def main(args: list[str]) -> None:
    if len(args) != 1:
        sys.exit(__doc__)
    text = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else ""
    print(section(args[0], text))


if __name__ == "__main__":
    main(sys.argv[1:])
