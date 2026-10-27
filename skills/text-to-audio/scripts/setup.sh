#!/usr/bin/env bash
# Set up text-to-audio: create the Python venv, install the pinned packages, download the Kokoro model at the pinned
# revision, run the self-test, and write the ready marker. run.sh calls this on the first run. A user can also run it.
# Usage: setup.sh [--force | --check-only] [venv-dir]
#   (default venv: $TEXT_TO_AUDIO_VENV, else ~/.cache/text-to-audio/venv)
#   --force       set up again, even when the ready marker matches
#   --check-only  exit 0 when the setup is done and current, else exit 1; changes nothing
# Set PYTHON to a Python 3.10, 3.11 or 3.12 interpreter to choose one; else the newest of those on the PATH is used.
# Keep the venv path short: the bundled espeak-ng cannot read its data from a very long path (about 160 characters).
# Prints JSON lines; the last line is the self-test result. Exit 0 only when the self-test passes.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORCE=0
CHECK=0
if [ "${1:-}" = "--force" ]; then FORCE=1; shift; fi
if [ "${1:-}" = "--check-only" ]; then CHECK=1; shift; fi
VENV="${1:-${TEXT_TO_AUDIO_VENV:-$HOME/.cache/text-to-audio/venv}}"
MODEL_REPO="hexgrad/Kokoro-82M"
MODEL_REVISION="$(sed -n 's/^MODEL_REVISION = "\(.*\)"/\1/p' "$HERE/text_to_audio.py")"
STAMP="$(cat "$HERE/../requirements.txt"; echo "$MODEL_REPO@$MODEL_REVISION")"
STAMP="$(printf '%s' "$STAMP" | shasum -a 256 2>/dev/null | cut -c1-16 || printf '%s' "$STAMP" | sha256sum | cut -c1-16)"
MARKER="$VENV/.text-to-audio-ready"
if [ "$CHECK" = 1 ]; then
  [ -x "$VENV/bin/python" ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$STAMP" ]
  exit $?
fi

if [ "$FORCE" = 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$STAMP" ]; then
  echo "{\"setup\": \"already done\", \"venv\": \"$VENV\"}"
  exit 0
fi

# One setup at a time: a second caller waits for the first.
mkdir -p "$(dirname "$VENV")"
LOCK="$VENV.lock"
for _ in $(seq 1 900); do
  if mkdir "$LOCK" 2>/dev/null; then break; fi
  sleep 2
done
[ -d "$LOCK" ] || { echo '{"ok": false, "error": "setup lock not acquired"}'; exit 1; }
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT
if [ "$FORCE" = 0 ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$STAMP" ]; then
  echo "{\"setup\": \"already done\", \"venv\": \"$VENV\"}"
  exit 0
fi

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.12 python3.11 python3.10; do
    if command -v "$c" >/dev/null; then PY="$c"; break; fi
  done
fi
[ -n "$PY" ] || { echo '{"ok": false, "error": "no python3.10, 3.11 or 3.12 found; set PYTHON"}'; exit 1; }
# Use the base interpreter, not the python of a venv that happens to be active.
BASE_PY="$("$PY" -c 'import sys, os; v = sys.version_info; assert (3, 10) <= v[:2] <= (3, 12), v
p = os.path.join(sys.base_prefix, "bin", "python%d.%d" % v[:2]); print(p if os.path.exists(p) else sys.executable)')"
rm -f "$MARKER"
if [ ! -x "$VENV/bin/python" ]; then
  "$BASE_PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$HERE/../requirements.txt"
HF_HUB_DISABLE_PROGRESS_BARS=1 MODEL_REPO="$MODEL_REPO" MODEL_REVISION="$MODEL_REVISION" "$VENV/bin/python" - <<'PY'
import os
from huggingface_hub import snapshot_download
path = snapshot_download(os.environ["MODEL_REPO"], revision=os.environ["MODEL_REVISION"],
                         allow_patterns=["config.json", "kokoro-v1_0.pth", "voices/*.pt"])
print('{"setup": "model ok", "model": "%s"}' % path)
PY
TEXT_TO_AUDIO_VENV="$VENV" bash "$HERE/selftest.sh"
echo "$STAMP" > "$MARKER"
