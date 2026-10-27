#!/usr/bin/env bash
# Run the converter with the skill venv. Usage: run.sh <file.md|file.txt> [...] [options of text_to_audio.py]
# On the first run, or after requirements.txt or the model revision changed, it runs setup.sh first. The setup
# output goes to a log file, so stdout stays one JSON line per input. That line then carries a "setup" field.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${TEXT_TO_AUDIO_VENV:-$HOME/.cache/text-to-audio/venv}"
LOG="$(dirname "$VENV")/setup.log"
if ! TEXT_TO_AUDIO_VENV="$VENV" bash "$HERE/setup.sh" --check-only >/dev/null 2>&1; then
  mkdir -p "$(dirname "$VENV")"
  START=$(date +%s)
  if ! TEXT_TO_AUDIO_VENV="$VENV" bash "$HERE/setup.sh" >"$LOG" 2>&1; then
    echo "{\"ok\": false, \"error\": \"setup failed; see $LOG\"}"
    exit 1
  fi
  export TEXT_TO_AUDIO_SETUP="setup ran first (new venv or changed requirements): $(( $(date +%s) - START )) s; log $LOG"
fi
# Library warnings go to stderr; stdout is the JSON lines only.
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore}" HF_HUB_DISABLE_PROGRESS_BARS=1 TOKENIZERS_PARALLELISM=false
exec "$VENV/bin/python" "$HERE/text_to_audio.py" "$@"
