#!/usr/bin/env bash
# Builds dist/basara4-windows (and dist/basara4-windows.zip) via packaging/basara4/make_package.py.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."

python=${WINPYTHON:-}
if [[ -z $python ]]; then
    for candidate in python3 python /c/Python3*/python.exe "${LOCALAPPDATA:-/c/Users/$USER/AppData/Local}"/Programs/Python/Python3*/python.exe; do
        if command -v "$candidate" >/dev/null 2>&1 || [[ -x $candidate ]]; then
            python=$candidate
            break
        fi
    done
fi
[[ -n $python ]] || { echo 'Need a Python 3 or WINPYTHON=path\to\python.exe' >&2; exit 1; }

"$python" -m pip install -q --disable-pip-version-check pyinstaller pillow numpy etcpak iced-x86==1.21.0 || true

if [[ ! " $* " =~ " --update " ]]; then
    rm -rf dist/basara4-windows
fi

"$python" packaging/basara4/make_package.py --out dist/basara4-windows "$@"

mkdir -p dist
rm -f dist/basara4-windows.zip dist/basara4-windows.zip.sha256
if command -v powershell >/dev/null 2>&1; then
    (cd dist && powershell -NoProfile -Command "Compress-Archive -Path basara4-windows -DestinationPath basara4-windows.zip")
elif command -v zip >/dev/null 2>&1; then
    (cd dist && zip -r basara4-windows.zip basara4-windows)
fi

if [[ -f dist/basara4-windows.zip ]]; then
    if command -v sha256sum >/dev/null 2>&1; then
        (cd dist && sha256sum -b basara4-windows.zip > basara4-windows.zip.sha256)
    fi
fi
