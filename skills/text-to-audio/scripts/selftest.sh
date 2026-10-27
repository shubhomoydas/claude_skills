#!/usr/bin/env bash
# Convert a small Markdown file (headings, a list, inline and display math, a table, code) to audio and check the
# result. Prints one JSON line. Exit 0 on a pass, 1 on a failure.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${TEXT_TO_AUDIO_VENV:-$HOME/.cache/text-to-audio/venv}"
[ -x "$VENV/bin/python" ] || { echo "{\"ok\": false, \"error\": \"no venv at $VENV; run $HERE/setup.sh\"}"; exit 2; }
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore}" HF_HUB_DISABLE_PROGRESS_BARS=1
exec "$VENV/bin/python" "$HERE/selftest.py" "$@"
