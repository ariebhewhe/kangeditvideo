import re
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_changelog_section():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import changelog
    finally:
        sys.path.pop(0)
    text = ("# Changelog\n\n## [Unreleased]\n\n## [1.1.0] - 2026-01-02\n\n### Added\n- B\n\n"
            "## [1.0.0] - 2026-01-01\n\n- A\n\n[1.1.0]: https://example.com\n")
    assert changelog.section("v1.1.0", text) == "### Added\n- B"
    assert changelog.section("1.0.0", text) == "- A"
    assert changelog.section("9.9.9", text) == ""



def test_release_check(monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import release_check
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(release_check.bump_version, "current", lambda: (0, 2, 0))
    monkeypatch.setattr(release_check.changelog, "section", lambda version, text: "- notes")
    monkeypatch.setattr(release_check, "base_version", lambda ref: (0, 1, 0))
    release_check.main([])
    assert capsys.readouterr().out.strip() == "0.2.0"
    monkeypatch.setattr(release_check, "base_version", lambda ref: None)   # stable doesn't exist yet
    release_check.main([])
    monkeypatch.setattr(release_check, "base_version", lambda ref: (0, 2, 0))
    with pytest.raises(SystemExit, match="must be newer"):
        release_check.main([])
    monkeypatch.setattr(release_check, "base_version", lambda ref: (0, 1, 0))
    monkeypatch.setattr(release_check.changelog, "section", lambda version, text: "")
    with pytest.raises(SystemExit, match="no section"):
        release_check.main([])
