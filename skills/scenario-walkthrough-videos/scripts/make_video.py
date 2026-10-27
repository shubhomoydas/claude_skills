#!/usr/bin/env python3
"""Stage 4b, assemble: stitch the rendered PNG frames (and the narration track, if any) into an MP4 and a contact sheet.

Usage: make_video.py --out <dir> [--timeline <dir>/audio/timeline.json] [--burn-subtitles] [--no-crossfade] [--cols N]

Reads <out>/frames.json, which render.py writes. With --timeline (audio.py wrote it) each frame lasts its timeline
`seconds`, the narration WAV is muxed as AAC, and the subtitle file is copied next to the MP4 as <name>.srt;
--burn-subtitles also draws it on the picture (needs ffmpeg built with libass). Without --timeline the video is
silent and exactly as before. Each frame shows for its "seconds". With
meta.crossfade_seconds > 0, ffmpeg fades each frame into the next one (xfade); the total length is the
sum of the seconds minus one crossfade for each join. With 0 (or --no-crossfade) the frames are cut.

Output: <out>/<meta.output_name or "walkthrough">.mp4 (H.264, yuv420p) and <out>/contact_sheet.png.
Prints one line per output; exits 1 when the video length differs from the plan by more than 0.5 s.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_ffmpeg():
    for cand in (os.environ.get("FFMPEG"), shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg",
                 "/usr/local/bin/ffmpeg"):
        if cand and Path(cand).exists():
            return cand
    raise SystemExit("ffmpeg not found; install it or set FFMPEG")


def video_command(ffmpeg, frames, out_dir, fps, xfade, out_file, audio=None, subtitles=None):
    cmd = [ffmpeg, "-y", "-loglevel", "error"]
    for f in frames:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{f['seconds']:.3f}", "-i", str(out_dir / f["png"])]
    if audio:
        cmd += ["-i", str(audio)]
    chains = [f"[{i}:v]setpts=PTS-STARTPTS,fps={fps},format=yuv420p[v{i}]" for i in range(len(frames))]
    if len(frames) == 1:
        last = "v0"
    elif xfade > 0:
        offset, prev = 0.0, "v0"
        for i in range(1, len(frames)):
            offset += frames[i - 1]["seconds"] - xfade
            label = f"x{i}"
            chains.append(f"[{prev}][v{i}]xfade=transition=fade:duration={xfade}:offset={offset:.3f}[{label}]")
            prev = label
        last = prev
    else:
        chains.append("".join(f"[v{i}]" for i in range(len(frames))) + f"concat=n={len(frames)}:v=1:a=0[cat]")
        last = "cat"
    if subtitles:
        esc = str(subtitles).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        style = "FontName=Helvetica,FontSize=22,Outline=2,MarginV=70"
        chains.append(f"[{last}]subtitles='{esc}':force_style='{style}'[sub]")
        last = "sub"
    cmd += ["-filter_complex", ";".join(chains), "-map", f"[{last}]"]
    if audio:
        cmd += ["-map", f"{len(frames)}:a", "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20",
            "-r", str(fps), "-movflags", "+faststart", str(out_file)]
    return cmd


def contact_sheet(ffmpeg, frames, out_dir, cols=4):
    """One PNG with every frame at 480 x 270, in reading order."""
    cols = min(cols, len(frames))
    rows = math.ceil(len(frames) / cols)
    tmp = out_dir / "contact_input"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    for i, f in enumerate(frames):           # ffmpeg tile reads one numbered sequence
        shutil.copyfile(out_dir / f["png"], tmp / f"{i:03d}.png")
    out = out_dir / "contact_sheet.png"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", "1", "-i", str(tmp / "%03d.png"),
                    "-vf", f"scale=480:270,tile={cols}x{rows}:padding=8:margin=8:color=white",
                    "-frames:v", "1", "-update", "1", str(out)], check=True)
    shutil.rmtree(tmp)
    return out


def probe_seconds(ffmpeg, path):
    ffprobe = str(Path(ffmpeg).with_name("ffprobe"))
    if not Path(ffprobe).exists():
        return None
    res = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True, help="the folder render.py wrote")
    ap.add_argument("--no-crossfade", action="store_true")
    ap.add_argument("--timeline", type=Path, help="audio.py's timeline.json: lengthen frames, add the voice")
    ap.add_argument("--burn-subtitles", action="store_true", help="draw the narration on the picture")
    ap.add_argument("--cols", type=int, default=4, help="frames per row of the contact sheet")
    args = ap.parse_args()
    data = json.loads((args.out / "frames.json").read_text())
    meta, frames = data["meta"], data["frames"]
    xfade = 0.0 if args.no_crossfade else float(meta.get("crossfade_seconds", 0))
    audio = subs = None
    if args.timeline:
        tl = json.loads(args.timeline.read_text())
        if len(tl["frames"]) != len(frames):
            raise SystemExit("timeline.json does not match frames.json (run audio.py again)")
        if abs(tl["crossfade_seconds"] - xfade) > 1e-9:
            raise SystemExit("the timeline was planned with a different crossfade (run audio.py again)")
        for f, row in zip(frames, tl["frames"]):
            f["seconds"] = row["seconds"]
        audio = args.timeline.parent / tl["audio"]
        subs = args.timeline.parent / tl["subtitles"] if tl.get("subtitles") else None
    if args.burn_subtitles:
        if not subs:
            raise SystemExit("--burn-subtitles needs narration (a timeline with subtitles)")
        if "subtitles" not in subprocess.run([find_ffmpeg(), "-hide_banner", "-filters"], capture_output=True,
                                             text=True).stdout:
            raise SystemExit("this ffmpeg has no 'subtitles' filter (libass); drop --burn-subtitles")
    if xfade and min(f["seconds"] for f in frames) <= xfade:
        raise SystemExit("a frame is shorter than the crossfade")
    ffmpeg = find_ffmpeg()
    video = args.out / f"{meta.get('output_name', 'walkthrough')}.mp4"
    subprocess.run(video_command(ffmpeg, frames, args.out, meta.get("fps", 30), xfade, video, audio,
                                  subs if args.burn_subtitles else None), check=True)
    if subs:
        shutil.copyfile(subs, video.with_suffix(".srt"))
    sheet = contact_sheet(ffmpeg, frames, args.out, args.cols)
    expected = sum(f["seconds"] for f in frames) - xfade * (len(frames) - 1)
    actual = probe_seconds(ffmpeg, video)
    if actual is not None and abs(actual - expected) > 0.5:
        print(f"VIDEO ERROR: length {actual:.2f} s, expected {expected:.2f} s", file=sys.stderr)
        return 1
    size_mb = video.stat().st_size / 1e6
    print(f"video: {video} ({len(frames)} frames, {actual or expected:.1f} s, {size_mb:.2f} MB"
          f"{', with audio' if audio else ''})")
    print(f"contact sheet: {sheet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
