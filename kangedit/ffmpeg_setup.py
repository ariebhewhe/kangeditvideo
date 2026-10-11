"""Automatic FFmpeg download (standard library only: also used by setup.py before dependencies exist).

Static builds with the GPU encoders (NVENC, AMF, Quick Sync / VideoToolbox) are fetched into
<app folder>/ffmpeg/bin, which ffmpeg.ffmpeg_bin() checks before PATH:
  * Windows and Linux: BtbN's daily FFmpeg builds (github.com/BtbN/FFmpeg-Builds)
  * macOS: Martin Riedl's static builds (ffmpeg.martin-riedl.de)
"""

from __future__ import annotations

import io
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

_BTBN = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
_RIEDL = "https://ffmpeg.martin-riedl.de/redirect/latest/macos/{arch}/release/{tool}.zip"

Progress = Callable[[float, str], None]


def bin_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "ffmpeg" / "bin"


def exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def local_binary(name: str) -> Optional[str]:
    path = bin_dir() / exe(name)
    return str(path) if path.is_file() else None


def _arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64", "armv8l") else "x86_64"


def sources() -> list[str]:
    """Archive URLs for this platform (each holds ffmpeg and/or ffprobe)."""
    arch = _arch()
    if os.name == "nt":
        return [_BTBN + ("ffmpeg-master-latest-winarm64-gpl.zip" if arch == "arm64" else "ffmpeg-master-latest-win64-gpl.zip")]
    if sys.platform == "darwin":
        a = "arm64" if arch == "arm64" else "amd64"
        return [_RIEDL.format(arch=a, tool="ffmpeg"), _RIEDL.format(arch=a, tool="ffprobe")]
    if sys.platform.startswith("linux"):
        return [_BTBN + ("ffmpeg-master-latest-linuxarm64-gpl.tar.xz" if arch == "arm64"
                         else "ffmpeg-master-latest-linux64-gpl.tar.xz")]
    raise RuntimeError(f"No automatic FFmpeg download for {sys.platform}; install FFmpeg and put it on PATH.")


def _download(url: str, progress: Optional[Progress], label: str, cancel=None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "KangEdit-setup"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        while True:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("cancelled")
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            buf.write(chunk)
            if progress:
                mb = buf.tell() / 1e6
                progress(buf.tell() / total if total else 0.0,
                         f"{label}: {mb:.0f}" + (f" / {total / 1e6:.0f} MB" if total else " MB"))
        return buf.getvalue()


def _extract(data: bytes, url: str, dest: Path) -> list[str]:
    """Write the ffmpeg / ffprobe executables found in the archive into dest."""
    wanted = {exe("ffmpeg"), exe("ffprobe")}
    found = []
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                name = os.path.basename(info.filename)
                if name in wanted and not info.is_dir():
                    with zf.open(info) as src, open(dest / name, "wb") as out:
                        shutil.copyfileobj(src, out)
                    found.append(name)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for member in tf.getmembers():
                name = os.path.basename(member.name)
                if name in wanted and member.isfile():
                    with tf.extractfile(member) as src, open(dest / name, "wb") as out:
                        shutil.copyfileobj(src, out)
                    found.append(name)
    return found


def install(progress: Optional[Progress] = None, cancel=None) -> Path:
    """Download FFmpeg + FFprobe into bin_dir(). Returns that folder."""
    dest = bin_dir()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="bin_", dir=dest.parent))
    try:
        found: list[str] = []
        urls = sources()
        for k, url in enumerate(urls):
            label = f"Downloading FFmpeg ({k + 1}/{len(urls)})" if len(urls) > 1 else "Downloading FFmpeg"
            data = _download(url, (lambda p, m: progress((k + p) / len(urls), m)) if progress else None, label, cancel)
            found += _extract(data, url, tmp)
        missing = {exe("ffmpeg"), exe("ffprobe")} - set(found)
        if missing:
            raise RuntimeError("the download did not contain " + ", ".join(sorted(missing)))
        for name in found:
            p = tmp / name
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if sys.platform == "darwin":   # files fetched by a program normally aren't quarantined; be sure
                os.system(f"xattr -d com.apple.quarantine '{p}' 2>/dev/null")
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
        return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure(progress: Optional[Progress] = None) -> Optional[str]:
    """Path of a usable ffmpeg: the downloaded one, else PATH, else download it now."""
    if local_binary("ffmpeg") and local_binary("ffprobe"):
        return local_binary("ffmpeg")
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return shutil.which("ffmpeg")
    install(progress)
    return local_binary("ffmpeg")


if __name__ == "__main__":   # python -m kangedit.ffmpeg_setup [--force]
    last = [-1]

    def show(p: float, msg: str):
        pct = int(p * 100)
        if pct != last[0]:
            last[0] = pct
            print(f"\r{msg} ({pct}%)   ", end="", flush=True)

    if "--force" in sys.argv:
        print("Installed to", install(show))
    else:
        print("\nFFmpeg:", ensure(show))
