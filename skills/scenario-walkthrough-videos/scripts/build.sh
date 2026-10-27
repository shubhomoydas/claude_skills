#!/usr/bin/env bash
# Build a walk-through video from a scenario JSON, as five stages that each run alone on files.
# Usage: build.sh <scenario.json> <out-dir> [--audio] [--burn-subtitles] [--no-crossfade]
#   1 validate.py   scenario.json -> report (schema, version, references, transitions, rules, narration)
#   2 plan.py       scenario.json -> <out-dir>/plan.json (the state of every frame)
#   3 render.py     plan.json -> <out-dir>/svg, png, frames.json (layout problems stop the build here)
#   4 audio.py      frames.json -> <out-dir>/audio/{timeline.json,narration.wav,subtitles.srt}   (only with --audio)
#   5 make_video.py frames.json (+ timeline.json) -> <out-dir>/<output_name>.mp4, contact_sheet.png
#   6 make_pdf.py   frames.json, svg -> <out-dir>/<output_name>.pdf (one frame per page, always made)
# One video per build: a silent copy of a voiced video is never made unless you ask (build again without --audio).
# Without --audio the video is silent. With --audio and no narration, or with the speech tools missing, it says so
# and builds silent. Each run replaces <out-dir>/svg, png, plan.json, the MP4 and the contact sheet.
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 <scenario.json> <out-dir> [--audio] [--burn-subtitles] [--no-crossfade]" >&2
  exit 2
fi
SCENARIO="$1"
OUT="$2"
shift 2
AUDIO=0
BURN=0
VIDEO_ARGS=()
for a in "$@"; do
  case "$a" in
    --audio) AUDIO=1 ;;
    --burn-subtitles) AUDIO=1; BURN=1 ;;
    *) VIDEO_ARGS+=("$a") ;;
  esac
done
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"

for tool in ffmpeg rsvg-convert python3; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool (see SKILL.md, Prerequisites)" >&2; exit 2; }
done

echo "== 1. validate"
python3 "$HERE/validate.py" "$SCENARIO"

echo "== 2. plan"
mkdir -p "$OUT"
python3 "$HERE/plan.py" "$SCENARIO" -o "$OUT/plan.json"

echo "== 3. render"
python3 "$HERE/render.py" --plan "$OUT/plan.json" --out "$OUT"

TIMELINE=()
rm -rf "$OUT/audio"
if [ "$AUDIO" = 1 ]; then
  echo "== 4. audio"
  set +e
  python3 "$HERE/audio.py" --out "$OUT"
  rc=$?
  set -e
  if [ $rc -eq 0 ] && [ -f "$OUT/audio/timeline.json" ]; then
    TIMELINE=(--timeline "$OUT/audio/timeline.json")
  elif [ $rc -ne 0 ] && [ $rc -ne 3 ]; then
    exit $rc
  fi
fi

echo "== 5. video"
if [ ${#TIMELINE[@]} -gt 0 ] && [ "$BURN" = 1 ]; then
  VIDEO_ARGS+=(--burn-subtitles)
fi
python3 "$HERE/make_video.py" --out "$OUT" ${TIMELINE[@]+"${TIMELINE[@]}"} ${VIDEO_ARGS[@]+"${VIDEO_ARGS[@]}"}

echo "== 6. pdf"
set +e
python3 "$HERE/make_pdf.py" --out "$OUT"
rc=$?
set -e
if [ $rc -ne 0 ] && [ $rc -ne 3 ]; then exit $rc; fi
