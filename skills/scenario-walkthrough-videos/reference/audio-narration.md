# Narration and the audio track

Optional. No `narration` anywhere means no audio and a build exactly as before. Audio is made only when the build asks for it:
`build.sh <scenario.json> <out-dir> --audio` (or `audio.py` then `make_video.py --timeline ...`). `--burn-subtitles` draws the
subtitles on the picture (needs an ffmpeg with libass; implies `--audio`).

## In the scenario

- Frame `narration`: the text spoken while the frame shows, 1 to 600 characters. One to three short sentences work best.
- `meta.audio` (all optional): `voice` (default `af_heart`; see the text-to-audio skill for the list), `speed` (1.0), `pause_seconds`
  (0.6), `language` (`en-us`; the voice and the language must agree, the speech is English). `audio.py` flags `--voice --speed
  --pause --language` override them.

Write for the ear: numbers in words ("twelve hundred", not 1,200), no file names, paths, identifiers or symbols (say "the chunker"),
no tables or code. `validate.py` fails on a path, URL, backtick, code or table symbol, or over 600 characters, and warns on digits
(`--strict` fails on warnings). Pronunciation fixes go in the text-to-audio skill's `lexicon.tsv` or a `--lexicon` file.

## Timing

A frame lasts `max(hold, lead + narration + pause + crossfade)` seconds, where `hold` is `frame.seconds` (or `meta.default_seconds`),
`lead` is 0.1 s (the voice starts 0.1 s after its frame starts), `narration` is the length of the speech with the silent ends of the
clip trimmed, and `crossfade` is `meta.crossfade_seconds` (not for the last frame). So the voice is never cut and ends before the
next frame starts. A frame with no narration keeps its hold and gets silence. `audio/timeline.json` holds, per frame, `hold`,
`narration_seconds`, `seconds`, `start` and `audio_start`; the video length is the sum of the `seconds` minus one crossfade per join.

## Speech and cache

`audio.py` calls the text-to-audio skill's `scripts/run.sh` once for all texts that are not yet cached (Kokoro-82M, local, no network
after setup, no paid API, no model tokens): `--input-format text --format wav --voice V --speed S --pause 0 --chapter-pause 0`. Each
text is cached as `<cache>/<sha256 of text, voice, speed, language>.wav`; the cache is `$WALKTHROUGH_AUDIO_CACHE`, else
`<out-dir>/audio-cache`, so a rebuild speaks only what changed. `$WALKTHROUGH_TTS` names another command with the same interface (the
tests use a mock). If the command is missing or fails, `audio.py` exits 3 and `build.sh` prints `AUDIO SKIPPED: <why>` and builds the
silent video (the PDF of the slides is made in every case).

First run on a machine: the text-to-audio skill sets itself up (a Python 3.10-3.12 venv in `~/.cache/text-to-audio/venv`, PyTorch,
Kokoro, spaCy model, Kokoro-82M weights): about 1 GB of downloads (1.1 GB on disk) and a few minutes, once. Speech then runs at about
10 s of audio per second on an Apple GPU, plus about 9 s of start-up per call.

## Outputs

`audio/narration.wav` (mono 24 kHz, as long as the video, muxed as AAC 128 kbit/s at 48 kHz), `audio/subtitles.srt` (one cue per
sentence, timed from `audio_start`; copied next to the MP4 as `<output_name>.srt`). `join_parts.py` joins several parts and shifts
their subtitles. Loudness is whatever the voice gives (about minus 26 dB mean, peaks near minus 2 dB); no normalisation is applied.
Whether the voice sounds good is a listening judgement the build cannot make.
