#!/bin/sh
# Double-clickable KangEdit launcher for macOS Finder.
cd "$(dirname "$0")" || exit 1
exec sh ./run.sh "$@"
