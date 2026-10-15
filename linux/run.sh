#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python=python3
if [[ -f "$root/python-runtime.path" ]]; then
    IFS= read -r python < "$root/python-runtime.path"
    if [[ "$python" != /* || ! -x "$python" ]]; then
        echo "AMDNR Linux Manager: shared Python runtime is missing. Open the launcher and Repair this game." >&2
        exit 1
    fi
fi
exec "$python" "$root/launch.py" "$@"
