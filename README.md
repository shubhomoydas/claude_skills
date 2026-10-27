# claude_skills

Reusable [Claude Code](https://claude.com/claude-code) skills that anyone can copy and use. Each skill is a
self-contained folder under `skills/` with a `SKILL.md` (the instructions Claude reads) and, where needed, scripts,
references and tests. No skill depends on any other private repository or on a particular project layout.

## Skills

| Skill | What it does | Needs |
|---|---|---|
| [`scenario-walkthrough-videos`](skills/scenario-walkthrough-videos/SKILL.md) | Makes a short explainer video (MP4, a PDF of the slides and a contact sheet) of a process, a data lifecycle or a system's states from one JSON scenario. Code draws every frame as SVG, so a rebuild costs no tokens. Domain rules are checked on every step, and each frame cites its sources. Optional voice-over from per-frame narration. | Python 3.8+, `rsvg-convert`, `ffmpeg`; audio needs `text-to-audio` |
| [`task-tracker`](skills/task-tracker/SKILL.md) | Keeps a running follow-up list in one Markdown file (`tasks-record.md`): a prioritized TODO list, notes on the tasks, and a Done list, newest first. Claude adds, reprioritizes, completes and lists tasks, and asks before adding a task of its own. | Nothing |
| [`text-to-audio`](skills/text-to-audio/SKILL.md) | Converts a text or Markdown file (a summary, a paper, a book) to audio (m4a with chapter marks, m4b, mp3 or wav) with Kokoro-82M, a local text-to-speech model, at no cost. Headings become chapters, math is read in words, and tables and code are skipped or read. | Python 3.10-3.12; first run downloads about 1 GB |

## Prerequisites and what gets installed

### scenario-walkthrough-videos

- **You install:** `rsvg-convert` (macOS: `brew install librsvg`) and `ffmpeg` with `ffprobe` (`brew install ffmpeg`).
  Python 3.8+ is needed; the scripts use only the standard library. `jsonschema` is optional (without it a built-in
  validator runs). `scripts/build.sh` checks the tools first and stops with the name of a missing one.
- **The skill installs:** nothing. It writes only the scenario's output folder (frames, MP4, PDF, contact sheet).
- **Voice-over (optional, `--audio`):** needs the `text-to-audio` skill (below) and its one-time install. Without it,
  the build says "AUDIO SKIPPED" and makes a silent video.
- **Local:** yes. No image model and no network call run; a rebuild costs no tokens.

### task-tracker

- **You install:** nothing.
- **The skill installs:** nothing. It creates and edits one file, `tasks-record.md`, in the project root (or the file
  you name).

### text-to-audio

- **You need:** Python 3.10, 3.11 or 3.12 (Kokoro does not support 3.13 yet), network access for the first run, about
  1 GB of free disk, and a short path to the venv (under about 120 characters).
- **The first run installs, by itself:**
  - a Python venv at `~/.cache/text-to-audio/venv` (set `TEXT_TO_AUDIO_VENV` for another place) with pinned packages:
    PyTorch, Kokoro, misaki with a bundled espeak-ng, spaCy and its small English model, soundfile, and
    `imageio-ffmpeg` (which bundles an ffmpeg binary, so no system ffmpeg is needed);
  - the Kokoro-82M model (about 330 MB, pinned revision) in the Hugging Face cache (`~/.cache/huggingface`);
  - a setup log at `~/.cache/text-to-audio/setup.log`, and a ready marker in the venv. Setup runs a self-test and
    runs again only when the pinned packages or the model revision change.
- **Ollama is optional.** It is used only for `--math llm`, where a local model speaks each LaTeX formula in words
  (you install Ollama and pull a model, for example `qwen3.5:9b`, yourself; answers are cached in
  `~/.cache/text-to-audio/llm-cache.json`). With Ollama, everything stays on your machine.
- **When Ollama is not available:** the skill reads math with its built-in rules (the default, `--math rules`), which
  also stays fully local, and uses the rules for any formula a model reply gets wrong. If you want better wording for
  difficult formulas, ask Claude to rewrite them in words in a copy of the Markdown before the conversion. That
  works, but it is **not fully local**: the text of those passages is then sent to Claude. Nothing is sent to Claude
  unless you ask for it.

## Install

Copy the skill folders you want into your project's `.claude/skills/` (or into `~/.claude/skills/` to use them in
every project):

```bash
git clone https://github.com/shubhomoydas/claude_skills.git
cp -r claude_skills/skills/task-tracker  your-project/.claude/skills/
```

Keep `text-to-audio` next to `scenario-walkthrough-videos` if you want narrated videos: the video skill finds the
audio skill as a sibling folder (or set `WALKTHROUGH_TTS` to another text-to-speech command).

Then ask Claude for what the skill does ("make a walk-through video of this process", "add a task: ...", "convert this
summary to audio"). Claude loads the matching skill from its description.

## Layout

```
skills/
  <skill-name>/
    SKILL.md        # when to use it and the procedure
    scripts/        # code the skill runs (where it has any)
    reference/      # longer notes loaded on demand
    tests/          # tests for the scripts
```

## Licence

MIT, see [LICENSE](LICENSE).
