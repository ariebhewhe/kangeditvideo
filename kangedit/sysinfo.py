"""A plain-text report of the user's environment (Help → Copy system info) to paste into bug reports: app
build, OS, hardware, library versions, FFmpeg and GPU support, screens and folders."""

from __future__ import annotations

import locale
import os
import platform
import subprocess
import sys
from importlib import metadata

from . import APP_NAME, version_string

# Python packages worth naming in a report (distribution name, import name).
PACKAGES = (("PySide6", "PySide6"), ("numpy", "numpy"), ("opencv-python", "cv2"), ("av", "av"),
            ("faster-whisper", "faster_whisper"), ("ctranslate2", "ctranslate2"), ("yt-dlp", "yt_dlp"))


def _package_version(dist: str, module: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:   # packaged builds don't always ship the metadata
        pass
    try:
        mod = __import__(module)
        return str(getattr(mod, "__version__", "installed"))
    except Exception:  # noqa: BLE001 — a report never fails over one line
        return "not installed"


def _memory_gb() -> str:
    try:
        if os.name == "nt":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _Status()
            st.dwLength = ctypes.sizeof(_Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            total, free = st.ullTotalPhys, st.ullAvailPhys
            return f"{total / 2**30:.1f} GB ({free / 2**30:.1f} GB free)"
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return f"{total / 2**30:.1f} GB"
    except Exception:  # noqa: BLE001
        return "unknown"


def _cpu_name() -> str:
    if sys.platform.startswith("linux"):   # platform.processor() is only "x86_64" there
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    if sys.platform == "darwin":
        try:
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                                  timeout=5).stdout.strip() or platform.processor()
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.processor() or "unknown"


def _os_name() -> str:
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]}"
    if os.name == "nt":
        return f"Windows {platform.version()} ({platform.win32_edition() or platform.release()})"
    try:
        info = platform.freedesktop_os_release()
        return f"{info.get('PRETTY_NAME', 'Linux')} (kernel {platform.release()})"
    except OSError:
        return platform.platform()


def _ffmpeg_lines() -> list[str]:
    from . import ffmpeg
    exe = ffmpeg.ffmpeg_bin()
    if not exe:
        return ["FFmpeg: not found"]
    try:
        first = ffmpeg.run([exe, "-hide_banner", "-version"]).stdout.decode("utf-8", "replace").splitlines()
        version = first[0] if first else "unknown version"
    except (OSError, subprocess.SubprocessError) as e:
        version = f"error: {e}"
    lines = [f"FFmpeg: {version}", f"FFmpeg path: {exe}"]
    try:
        lines.append(f"GPU decoding: {ffmpeg.hw_decoder() or 'none (CPU)'}")
        for codec in ("h264", "h265"):
            enc = ffmpeg.hw_encoder(codec)
            lines.append(f"GPU encoding {codec.upper()}: "
                         + (f"{ffmpeg.hw_encoder_label(enc)} ({enc})" if enc else "none (CPU)"))
    except Exception as e:  # noqa: BLE001
        lines.append(f"GPU detection failed: {e}")
    return lines


def _screen_lines() -> list[str]:
    try:
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is None:
            return []
        lines = [f"Qt platform: {QGuiApplication.platformName()}"]
        for i, s in enumerate(QGuiApplication.screens()):
            g, avail = s.geometry(), s.availableGeometry()
            lines.append(f"Screen {i + 1}: {g.width()}x{g.height()} (usable {avail.width()}x{avail.height()}), "
                         f"scale {s.devicePixelRatio():g}" + (" [primary]" if s == QGuiApplication.primaryScreen()
                                                               else ""))
        return lines
    except Exception:  # noqa: BLE001
        return []


def _folder_lines() -> list[str]:
    from . import crashlog
    from .config import APP_DIR, app_data_dir, cache_root
    lines = []
    for label, get in (("App folder", lambda: APP_DIR), ("Data folder", app_data_dir), ("Cache folder", cache_root),
                       ("Log file", crashlog.log_path)):
        try:
            lines.append(f"{label}: {get()}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"{label}: error: {e}")
    return lines


def report(gpu: bool = True) -> str:
    """The report as plain text. gpu=False skips FFmpeg and the GPU checks (they run short test encodes)."""
    try:
        from PySide6.QtCore import qVersion
        qt = qVersion()
    except Exception:  # noqa: BLE001
        qt = "unknown"
    build = "packaged build" if getattr(sys, "frozen", False) else "from source"
    lines = [
        f"{APP_NAME} {version_string()} ({build})",
        f"OS: {_os_name()}",
        f"Architecture: {platform.machine()}",
        f"CPU: {_cpu_name()} ({os.cpu_count()} threads)",
        f"Memory: {_memory_gb()}",
        f"Python: {platform.python_version()} ({platform.python_implementation()})",
        f"Qt: {qt}",
    ]
    lines += [f"{dist}: {_package_version(dist, module)}" for dist, module in PACKAGES]
    try:
        lines.append(f"Locale: {locale.getlocale()[0] or 'unknown'}")
    except ValueError:
        pass
    lines += _screen_lines()
    if gpu:
        lines += _ffmpeg_lines()
    lines += _folder_lines()
    return "\n".join(lines)
