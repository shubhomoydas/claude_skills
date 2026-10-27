#!/usr/bin/env python3
"""Stage 4a, audio: make the narration track and the frame timing from <out>/frames.json (render.py wrote it).

Usage: audio.py --out <dir> [--voice V] [--speed S] [--pause SECONDS] [--language L] [--cache DIR] [--no-srt]

Standard library only. Reads frames.json (meta.audio and each frame's `narration`), and writes, in <out>/audio/:
  timeline.json   per frame: hold, narration_seconds, seconds (the frame's length), start, audio_start; and total
  narration.wav   one mono WAV (each clip's silent ends trimmed) the length of the video: each narration at its frame's audio_start, silence elsewhere
  subtitles.srt   the narration, one cue per sentence, timed from the same numbers
make_video.py --timeline <dir>/audio/timeline.json then lengthens the frames and muxes the track (AAC).

Timing. A frame lasts  max(hold, lead + narration + pause + crossfade)  seconds, where hold is its visual seconds,
lead (0.1 s) is the delay between the frame appearing and the voice starting, and the crossfade term keeps the voice
clear of the next fade. So the voice is never cut and each frame's audio starts 0.1 s after its frame starts.
A frame with no narration keeps its hold and gets silence.

Speech comes from the text-to-audio skill (Kokoro-82M, local): one `run.sh` call for all changed texts. Each
narration is cached as <cache>/<sha256 of text, voice, speed, language>.wav, so a rebuild speaks only what changed.
$WALKTHROUGH_TTS names another command with the same interface (tests use it to mock the voice).

Exit 0: track written, or no narration anywhere (nothing written; the video stays silent).
Exit 3: the speech tools are missing or failed (message "AUDIO SKIPPED: ..."; the caller builds silent).
Exit 1: bad input.
"""
import argparse
import array
import hashlib
import json
import os
import re
import subprocess
import sys
import wave
from pathlib import Path

HERE = Path(__file__).absolute().parent
LEAD = 0.1
DEFAULTS = {"voice": "af_heart", "speed": 1.0, "pause_seconds": 0.6, "language": "en-us"}
TTS_VERSION = "kokoro-v1"
TOOLS_MISSING = 3


def default_tts_command():
    """The command that turns text files into WAVs: $WALKTHROUGH_TTS, else the text-to-audio skill's run.sh."""
    env = os.environ.get("WALKTHROUGH_TTS")
    if env:
        return [env] if Path(env).exists() else None
    run_sh = HERE.parent.parent / "text-to-audio" / "scripts" / "run.sh"
    return ["bash", str(run_sh)] if run_sh.exists() else None


def settings(meta, args):
    """Voice, speed, pause and language: command line over meta.audio over the defaults."""
    out = dict(DEFAULTS)
    out.update(meta.get("audio", {}))
    for key, val in (("voice", args.voice), ("speed", args.speed), ("pause_seconds", args.pause),
                     ("language", args.language)):
        if val is not None:
            out[key] = val
    lang = {"a": "en-us", "b": "en-gb"}.get(out["voice"][:1])
    if lang and not out["language"].lower().startswith("en"):
        raise ValueError(f"voice {out['voice']} is English but meta.audio.language is {out['language']}")
    return out


def cache_key(text, voice, speed, language):
    blob = json.dumps([TTS_VERSION, text, voice, float(speed), language.lower()], ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


def trim_silence(pcm, rate, threshold=0.01, margin=0.03):
    """Cut leading and trailing near-silence (below `threshold` of full scale) from 16-bit mono PCM bytes, keeping
    `margin` seconds each side. The voice leaves about a third of a second of silence before it starts; without
    the trim each frame's audio would start late by that much."""
    n = len(pcm) // 2
    samples = array.array("h")
    samples.frombytes(pcm[:n * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    limit = int(threshold * 32768)
    first = next((i for i, v in enumerate(samples) if abs(v) > limit), None)
    if first is None:
        return b""
    last = n - 1 - next(i for i, v in enumerate(reversed(samples)) if abs(v) > limit)
    m = int(margin * rate)
    a, b = max(0, first - m), min(n, last + 1 + m)
    return pcm[a * 2:b * 2]


def load_wav(path):
    """(sample rate, trimmed PCM bytes) of a mono 16-bit WAV."""
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path}: need mono 16-bit WAV")
        return w.getframerate(), trim_silence(w.readframes(w.getnframes()), w.getframerate())


def wav_seconds(path):
    """Length in seconds of the speech in a WAV, silence at both ends trimmed."""
    rate, pcm = load_wav(path)
    return len(pcm) / 2 / rate


def plan_timeline(frames, narration_seconds, pause, xfade, lead=LEAD):
    """frames: [{'id', 'seconds'}]; narration_seconds: {frame index: seconds}. Returns (rows, total)."""
    rows, t = [], 0.0
    for i, f in enumerate(frames):
        n = narration_seconds.get(i)
        hold = f["seconds"]
        length = hold if n is None else max(hold, lead + n + pause + (xfade if i < len(frames) - 1 else 0))
        rows.append({"id": f["id"], "hold": hold, "narration_seconds": n, "seconds": round(length, 3),
                     "start": round(t, 3), "audio_start": None if n is None else round(t + lead, 3)})
        t += length - xfade
    total = t + xfade if frames else 0.0
    return rows, round(total, 3)


def synthesize(todo, cache, cfg, command):
    """todo: {key: text}. Speak every text into <cache>/<key>.wav with ONE call. Raises RuntimeError."""
    cache.mkdir(parents=True, exist_ok=True)
    files = []
    for key, text in todo.items():
        p = cache / f"{key}.txt"
        p.write_text(text)
        files.append(str(p))
    cmd = command + files + ["--input-format", "text", "--format", "wav", "--out-dir", str(cache),
                             "--voice", cfg["voice"], "--speed", str(cfg["speed"]),
                             "--pause", "0", "--chapter-pause", "0"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    for line in res.stdout.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not rec.get("ok", False):
            raise RuntimeError(rec.get("error", "speech failed"))
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip()[-400:] or f"exit {res.returncode}")
    missing = [k for k in todo if not (cache / f"{k}.wav").exists()]
    if missing:
        raise RuntimeError(f"no audio written for {len(missing)} text(s)")


def mix(rows, wavs, total, path):
    """Write one mono 16-bit WAV of `total` seconds with each narration placed at its audio_start."""
    rate = None
    clips = []
    for row, wp in zip(rows, wavs):
        if wp is None:
            continue
        r, pcm = load_wav(wp)
        if rate is None:
            rate = r
        elif r != rate:
            raise ValueError(f"{wp}: sample rate {r} differs from {rate}")
        clips.append((row["audio_start"], pcm))
    rate = rate or 24000
    buf = bytearray(int(round(total * rate)) * 2)
    for start, data in clips:
        off = int(round(start * rate)) * 2
        buf[off:off + len(data)] = data[:max(0, len(buf) - off)]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(buf))


def sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


def _stamp(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def wrap(text, width=42):
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return "\n".join(lines + [cur])


def srt_text(rows, texts):
    """One cue per sentence; a frame's narration time is shared by its sentences in proportion to length."""
    cues = []
    for row, text in zip(rows, texts):
        if not text:
            continue
        parts = sentences(text)
        total_chars = sum(len(p) for p in parts)
        t = row["audio_start"]
        for p in parts:
            dur = row["narration_seconds"] * len(p) / total_chars
            cues.append((t, t + dur, p))
            t += dur
    return "".join(f"{i}\n{_stamp(a)} --> {_stamp(b)}\n{wrap(p)}\n\n" for i, (a, b, p) in enumerate(cues, 1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--voice")
    ap.add_argument("--speed", type=float)
    ap.add_argument("--pause", type=float)
    ap.add_argument("--language")
    ap.add_argument("--cache", type=Path, help="default $WALKTHROUGH_AUDIO_CACHE, else <out>/audio-cache")
    ap.add_argument("--no-srt", action="store_true")
    args = ap.parse_args(argv)
    data = json.loads((args.out / "frames.json").read_text())
    meta, frames = data["meta"], data["frames"]
    texts = [f.get("narration") for f in frames]
    if not any(texts):
        print("no narration in the scenario: the video stays silent")
        return 0
    try:
        cfg = settings(meta, args)
    except ValueError as e:
        print(f"AUDIO ERROR: {e}", file=sys.stderr)
        return 1
    cache = args.cache or (Path(os.environ["WALKTHROUGH_AUDIO_CACHE"]) if os.environ.get("WALKTHROUGH_AUDIO_CACHE")
                           else args.out / "audio-cache")
    keys = [cache_key(t, cfg["voice"], cfg["speed"], cfg["language"]) if t else None for t in texts]
    todo = {k: t for k, t in zip(keys, texts) if k and not (cache / f"{k}.wav").exists()}
    spoken = len([k for k in keys if k]) - len(todo)
    if todo:
        command = default_tts_command()
        if command is None:
            print("AUDIO SKIPPED: the text-to-audio skill's scripts/run.sh was not found "
                  "(set WALKTHROUGH_TTS to a command that speaks text files); building silent", file=sys.stderr)
            return TOOLS_MISSING
        try:
            synthesize(todo, cache, cfg, command)
        except (RuntimeError, OSError) as e:
            print(f"AUDIO SKIPPED: speech synthesis failed ({e}); building silent", file=sys.stderr)
            return TOOLS_MISSING
    seconds = {i: wav_seconds(cache / f"{k}.wav") for i, k in enumerate(keys) if k}
    xfade = float(meta.get("crossfade_seconds", 0)) if len(frames) > 1 else 0.0
    rows, total = plan_timeline(frames, seconds, cfg["pause_seconds"], xfade)
    adir = args.out / "audio"
    adir.mkdir(exist_ok=True)
    mix(rows, [cache / f"{k}.wav" if k else None for k in keys], total, adir / "narration.wav")
    timeline = {"settings": cfg, "crossfade_seconds": xfade, "lead_seconds": LEAD, "total_seconds": total,
                "audio": "narration.wav", "frames": rows}
    if not args.no_srt:
        (adir / "subtitles.srt").write_text(srt_text(rows, texts))
        timeline["subtitles"] = "subtitles.srt"
    (adir / "timeline.json").write_text(json.dumps(timeline, indent=1))
    longer = sum(1 for r in rows if r["seconds"] > r["hold"] + 1e-6)
    print(f"audio: {adir / 'narration.wav'} ({total:.1f} s; {len(todo)} spoken, {spoken} from cache; "
          f"{longer} frames lengthened to fit the voice)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
