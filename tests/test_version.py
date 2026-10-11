import re
import subprocess
import sys
from pathlib import Path

import kangedit

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", kangedit.__version__)


def test_tag_check():
    script = [sys.executable, str(ROOT / "scripts" / "bump_version.py"), "--check"]
    assert subprocess.run(script + [f"v{kangedit.__version__}"]).returncode == 0
    assert subprocess.run(script + ["v0.0.0-not-this"], capture_output=True).returncode != 0


def test_bump(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import bump_version
    finally:
        sys.path.pop(0)
    init = tmp_path / "__init__.py"
    init.write_text('"""x"""\n__version__ = "1.4.9"\n', encoding="utf-8")
    monkeypatch.setattr(bump_version, "INIT", init)
    bump_version.main(["patch"])
    assert '__version__ = "1.4.10"' in init.read_text()
    bump_version.main(["minor"])
    assert '__version__ = "1.5.0"' in init.read_text()
    bump_version.main(["major"])
    assert '__version__ = "2.0.0"' in init.read_text()
