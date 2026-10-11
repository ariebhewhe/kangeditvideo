#!/bin/sh
# KangEdit launcher for macOS and Linux: sets up .venv, the Python packages and FFmpeg when needed, then starts.
cd "$(dirname "$0")" || exit 1
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY=$(command -v "$candidate")
        break
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.10 or newer is required."
    echo "  macOS: brew install python@3.12   (or https://www.python.org/downloads/)"
    echo "  Linux: sudo apt install python3 python3-venv   (or your distribution's equivalent)"
    exit 1
fi
exec "$PY" install.py --run "$@"
