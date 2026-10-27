"""Convert a text or Markdown file to an audio file with Kokoro-82M, a local text-to-speech model.

Usage: text_to_audio.py <file> [<file> ...] [-o <out> | --out-dir <dir>] [options]
Prints one JSON line per input file. See ../SKILL.md.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from speech_text import LLM, Options, load_lexicon, prepare  # noqa: E402

MODEL_REPO = "hexgrad/Kokoro-82M"
MODEL_REVISION = "f3ff3571791e39611d31c381e3a41a3af07b4987"
RATE = 24000
DEFAULT_OUT_DIR = os.path.join(os.getcwd(), "audio")
FORMATS = {"m4a": ("aac", "ipod"), "m4b": ("aac", "ipod"), "mp3": ("libmp3lame", "mp3"), "wav": (None, "wav")}


def log(path, msg):
    if path:
        with open(path, "a") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")


def model_files():
    """Local paths of the pinned Kokoro weights, config and voices (downloaded once into the Hugging Face cache)."""
    from huggingface_hub import snapshot_download
    patterns = ["config.json", "kokoro-v1_0.pth", "voices/*.pt"]
    try:
        root = snapshot_download(MODEL_REPO, revision=MODEL_REVISION, allow_patterns=patterns, local_files_only=True)
        if not os.path.exists(os.path.join(root, "kokoro-v1_0.pth")):
            raise FileNotFoundError
    except Exception:
        root = snapshot_download(MODEL_REPO, revision=MODEL_REVISION, allow_patterns=patterns)
    return root


class Voice:
    def __init__(self, voice, speed, device):
        import torch
        from kokoro import KModel, KPipeline
        if device == "auto":
            device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        root = model_files()
        model = KModel(repo_id=MODEL_REPO, config=os.path.join(root, "config.json"),
                       model=os.path.join(root, "kokoro-v1_0.pth")).to(device).eval()
        # The first letter of a voice name is its language: a = American English, b = British English, ...
        self.pipeline = KPipeline(lang_code=voice[0], repo_id=MODEL_REPO, model=model)
        path = os.path.join(root, "voices", voice + ".pt")
        if not os.path.exists(path):
            names = sorted(f[:-3] for f in os.listdir(os.path.join(root, "voices")))
            raise SystemExit(json.dumps({"ok": False, "error": f"unknown voice {voice}; voices: {' '.join(names)}"}))
        self.voice = self.pipeline.load_voice(path)
        self.speed = speed

    def say(self, text):
        import numpy as np
        parts = [a.numpy() if hasattr(a, "numpy") else a
                 for _, _, a in self.pipeline(text, voice=self.voice, speed=self.speed, split_pattern=None)
                 if a is not None]
        return np.concatenate(parts) if parts else np.zeros(0, dtype="float32")


def silence(seconds):
    import numpy as np
    return np.zeros(int(RATE * seconds), dtype="float32")


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def ff_escape(s):
    return re.sub(r"([=;#\\\n])", r"\\\1", s)


def encode(wavs, titles, durations, out, fmt, bitrate, title):
    """Join the chapter WAVs into one file; m4a/m4b/mp3 get chapter marks and a title."""
    import soundfile as sf
    if fmt == "wav":
        with sf.SoundFile(out, "w", RATE, 1, "PCM_16") as w:
            for p in wavs:
                w.write(sf.read(p, dtype="float32")[0])
        return
    work = out + ".parts"
    lst = os.path.join(work, "concat.txt")
    with open(lst, "w") as f:
        for p in wavs:
            f.write("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n")
    meta = os.path.join(work, "chapters.txt")
    with open(meta, "w") as f:
        f.write(f";FFMETADATA1\ntitle={ff_escape(title)}\nartist=Kokoro-82M\ngenre=Speech\n")
        t = 0
        for name, d in zip(titles, durations):
            ms = int(round(d * 1000))
            f.write(f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={t}\nEND={t + ms}\ntitle={ff_escape(name)}\n")
            t += ms
    codec, muxer = FORMATS[fmt]
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
           "-i", meta, "-map_metadata", "1", "-map_chapters", "1", "-c:a", codec, "-b:a", bitrate, "-ac", "1",
           "-f", muxer, out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr.strip()[-500:])


def convert(path, out, a, voice, lexicon, llm):
    t0 = time.time()
    opt = Options(math=a.math, tables=a.tables, code=a.code, chapter_level=a.chapter_level, llm=llm,
                  lexicon=lexicon)
    markdown = {"auto": None, "markdown": True, "text": False}[a.input_format]
    chapters, stats = prepare(path, opt, title=a.title, markdown=markdown)
    if llm:
        llm.save()
    if not chapters:
        return {"ok": False, "input": path, "error": "no speakable text"}
    title = a.title or chapters[0].title
    chars = sum(len(p) for c in chapters for p in c.paragraphs)
    base = {"input": path, "chapters": len(chapters), "paragraphs": sum(len(c.paragraphs) for c in chapters),
            "characters": chars}
    if a.text_out or a.dry_run:
        tpath = a.text_out or os.path.splitext(out)[0] + ".speech.txt"
        with open(tpath, "w") as f:
            for c in chapters:
                f.write(f"=== {c.title}\n\n" + "\n\n".join(c.paragraphs) + "\n\n")
        base["speech_text"] = tpath
    if a.dry_run:
        return {"ok": True, **base, **{k: v for k, v in stats.items() if v},
                **({"llm_calls": llm.calls, "llm_cached": llm.cached, "llm_fallbacks": llm.fallbacks} if llm else {}),
                "seconds": round(time.time() - t0, 1)}

    import soundfile as sf
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    work = out + ".parts"
    os.makedirs(work, exist_ok=True)
    progress = out + ".progress"
    open(progress, "w").close()
    wavs, durations, reused = [], [], 0
    key0 = f"{a.voice}|{a.speed}|{MODEL_REVISION}|{a.pause}|{a.chapter_pause}"
    tts_s = 0.0
    for n, c in enumerate(chapters, 1):
        # A chapter is cached by its text and the voice settings, so a re-run after a stop resumes.
        h = hashlib.sha1((key0 + "\n" + "\n".join(c.paragraphs)).encode()).hexdigest()[:16]
        wav = os.path.join(work, f"{n:04d}-{h}.wav")
        if os.path.exists(wav):
            reused += 1
        else:
            import numpy as np
            t1 = time.time()
            pieces = []
            for k, p in enumerate(c.paragraphs):
                pieces.append(voice.say(p))
                pieces.append(silence(a.chapter_pause if k == 0 and len(c.paragraphs) > 1 else a.pause))
            pieces.append(silence(a.chapter_pause))
            audio = np.concatenate(pieces)
            sf.write(wav + ".tmp.wav", audio, RATE, subtype="PCM_16")
            os.replace(wav + ".tmp.wav", wav)
            tts_s += time.time() - t1
        info = sf.info(wav)
        durations.append(info.frames / RATE)
        wavs.append(wav)
        log(progress, f"chapter {n}/{len(chapters)} {sum(durations):.0f} s audio: {c.title[:60]}")
    encode(wavs, [c.title for c in chapters], durations, out, a.format, a.bitrate, title)
    if not a.keep_parts:
        shutil.rmtree(work, ignore_errors=True)
        os.remove(progress)
    audio_s = sum(durations)
    res = {"ok": True, "out": out, **base, "audio_seconds": round(audio_s, 1),
           "audio": time.strftime("%H:%M:%S", time.gmtime(audio_s)), "format": a.format, "voice": a.voice,
           "device": voice.device, "seconds": round(time.time() - t0, 1)}
    if tts_s:
        res["realtime_factor"] = round(audio_s / max(time.time() - t0, 1e-6), 1)
    if reused:
        res["chapters_reused"] = reused
    res.update({k: v for k, v in stats.items() if v})
    if llm:
        res.update(llm_calls=llm.calls, llm_cached=llm.cached, llm_fallbacks=llm.fallbacks)
    return res


def main():
    ap = argparse.ArgumentParser(description="Text or Markdown to audio with Kokoro-82M.")
    ap.add_argument("inputs", nargs="+", help="text (.txt) or Markdown (.md) files")
    ap.add_argument("-o", "--out", help="output file (one input only)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="output folder (default: ./audio)")
    ap.add_argument("--format", default="m4a", choices=sorted(FORMATS), help="m4a (default), m4b, mp3 or wav")
    ap.add_argument("--voice", default="af_heart", help="Kokoro voice (default af_heart; see SKILL.md)")
    ap.add_argument("--speed", type=float, default=1.0, help="speaking speed (default 1.0)")
    ap.add_argument("--device", default="auto", choices=["auto", "mps", "cuda", "cpu"])
    ap.add_argument("--bitrate", default="48k", help="AAC or MP3 bitrate (default 48k, mono)")
    ap.add_argument("--input-format", default="auto", choices=["auto", "markdown", "text"],
                    help="auto: .md/.markdown are Markdown, anything else is plain text")
    ap.add_argument("--math", default="rules", choices=["rules", "llm", "skip"],
                    help="LaTeX math: rules (default), llm (a local Ollama model rewrites it), skip")
    ap.add_argument("--llm-model", default="qwen3.5:9b", help="Ollama model for --math llm")
    ap.add_argument("--ollama", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    ap.add_argument("--tables", default="skip", choices=["skip", "read"], help="Markdown tables")
    ap.add_argument("--code", default="skip", choices=["skip", "read"], help="code blocks")
    ap.add_argument("--chapter-level", type=int, default=2, help="headings at this level or above are chapters")
    ap.add_argument("--lexicon", action="append", default=[], help="extra pronunciation file (TSV)")
    ap.add_argument("--title", help="title in the audio metadata (default: from the file)")
    ap.add_argument("--pause", type=float, default=0.45, help="silence after a paragraph, seconds")
    ap.add_argument("--chapter-pause", type=float, default=1.0, help="silence after a heading, seconds")
    ap.add_argument("--text-out", help="also write the speech text to this file")
    ap.add_argument("--dry-run", action="store_true", help="write only the speech text (<out>.speech.txt)")
    ap.add_argument("--keep-parts", action="store_true", help="keep the per-chapter WAVs (<out>.parts/)")
    a = ap.parse_args()
    if a.out and len(a.inputs) > 1:
        print(json.dumps({"ok": False, "error": "-o takes one input; use --out-dir for several"}))
        return 1
    lexicon = load_lexicon([os.path.join(HERE, "..", "lexicon.tsv")] + a.lexicon)
    llm = LLM(a.llm_model, a.ollama) if a.math == "llm" else None
    voice = None
    rc = 0
    for path in a.inputs:
        if not os.path.isfile(path):
            print(json.dumps({"ok": False, "input": path, "error": "file not found"}))
            rc = 1
            continue
        stem = re.sub(r"\.[^.]+$", "", os.path.basename(path))
        out = a.out or os.path.join(a.out_dir, f"{stem}.{a.format}")
        try:
            if voice is None and not a.dry_run:
                voice = Voice(a.voice, a.speed, a.device)
            res = convert(path, out, a, voice, lexicon, llm)
        except Exception as e:  # one JSON line, also on a failure
            res = {"ok": False, "input": path, "error": f"{type(e).__name__}: {e}"}
        if os.environ.get("TEXT_TO_AUDIO_SETUP"):
            res["setup"] = os.environ["TEXT_TO_AUDIO_SETUP"]
        print(json.dumps(res), flush=True)
        rc |= 0 if res.get("ok") else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
