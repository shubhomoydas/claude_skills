#!/usr/bin/env python3
"""Join several built parts (MP4s, each from one scenario) into one MP4, and their subtitle files into one SRT.

Usage: join_parts.py --out <joined.mp4> [--srt <joined.srt>] [--silent] part1.mp4 part2.mp4 ...

Standard library + ffmpeg. The video streams are copied; audio is re-encoded to AAC once so the joins carry no gaps.
A part's subtitles are the .srt next to it (same name); a part without one adds none. Each part's cues are shifted by
the summed duration of the parts before it. `--silent` drops the audio (the joined file is a silent video).
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_TS = re.compile(r"(\d+):(\d+):(\d+),(\d+)")


def to_s(m):
    h, mi, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def stamp(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def shift_srt(text, offset, start_index):
    """Shift every cue by `offset` seconds and renumber from start_index. Returns (text, next_index)."""
    out, idx = [], start_index
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        a, b = lines[1].split(" --> ")
        ta, tb = to_s(_TS.match(a.strip())) + offset, to_s(_TS.match(b.strip())) + offset
        out.append(f"{idx}\n{stamp(ta)} --> {stamp(tb)}\n" + "\n".join(lines[2:]))
        idx += 1
    return ("\n\n".join(out) + "\n\n") if out else "", idx


def duration(ffprobe, path):
    res = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("parts", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--srt", type=Path)
    ap.add_argument("--silent", action="store_true")
    args = ap.parse_args(argv)
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    with tempfile.TemporaryDirectory() as tmp:
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{p.resolve()}'\n" for p in args.parts))
        audio = ["-an"] if args.silent else ["-c:a", "aac", "-b:a", "128k"]
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c:v", "copy", *audio, "-movflags", "+faststart", str(args.out)], check=True)
    if args.srt:
        offset, idx, text = 0.0, 1, ""
        for p in args.parts:
            srt = p.with_suffix(".srt")
            if srt.exists():
                chunk, idx = shift_srt(srt.read_text(), offset, idx)
                text += chunk
            offset += duration(ffprobe, p)
        args.srt.write_text(text)
    print(f"joined {len(args.parts)} parts: {args.out} ({duration(ffprobe, args.out):.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
