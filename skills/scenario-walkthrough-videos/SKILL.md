---
name: scenario-walkthrough-videos
description: >-
  Make a short explainer video (MP4 plus a contact sheet) that walks through a process, a data lifecycle or the states
  of a system, step by step, from one JSON scenario: entities shown as table panels, lane boards and point-edge graphs, an initial state,
  and frames that each apply a few transitions (add, update, move a foreign key, delete) and draw annotations (arrows,
  highlight boxes, callouts, badges, before/after boxes, tints). Code draws every frame as SVG (Python standard
  library), rsvg-convert makes the PNGs and ffmpeg the video, so no image model runs and a rebuild costs no tokens.
  Domain rules are data that the validator runs on every step; each frame carries a status badge (available,
  designed, planned, inferred) and cites its sources, so an unbuilt feature never looks available. The scenario is a
  versioned JSON contract (schema_version), so any program or person can produce it; an optional per-frame narration
  becomes a voice track made locally (Kokoro, text-to-audio skill) when the build asks for audio.
when_to_use: >-
  Use for "make a walk-through video of this process", "animate how a record moves through the system", "explainer
  video of the data lifecycle", "show the states step by step as a video", "storyboard these transitions", "rebuild
  the walk-through after this change", or a repeatable video of a design built from annotated images. SKIP for: a
  slide deck (a deck tool or the pptx skill); a live screen recording of an app; a live-spoken or
  recorded-voice video; a one-off diagram or chart (draw it directly).
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# scenario-walkthrough-videos

One JSON file holds the whole video: what exists, how it starts, and what each step changes and says. The scripts
apply the steps in order, check every change against the declared fields, foreign keys and rules, draw each frame,
and stitch the frames into an MP4. Because the frames are code, a fix is an edit to the JSON and a rebuild, and the
build fails loudly instead of drawing a wrong or cramped picture.

This skill lives in its own folder (`<skills>/scenario-walkthrough-videos/`); paths below are relative to it. The worked example is `examples/helpdesk/scenario.json`: a small help desk, 10 frames, 55 s.

## When to use

- The subject is a sequence of states: a record's life, a queue, a workflow, a migration, a data model under a set
  of operations, a design that is partly built.
- The reader learns more from seeing rows move than from prose: what changes, what stays, what is refused.
- The video will be rebuilt as the design changes, so it must be cheap and repeatable.

When NOT to use: a slide deck (use a deck tool); a live recording of a real screen; a human-recorded voice (the
optional voice is a local text-to-speech track); a single diagram (draw it once instead).

## Prerequisites

| Tool | Check | Install (macOS) |
|---|---|---|
| `python3` 3.8+ | `python3 --version` | standard library only |
| `rsvg-convert` | `rsvg-convert --version` | `brew install librsvg` |
| `ffmpeg`, `ffprobe` | `ffmpeg -version` | `brew install ffmpeg` |
| `jsonschema` (optional) | `python3 -c "import jsonschema"` | without it the built-in validator runs |

`scripts/build.sh` checks the three tools first and stops with the name of a missing one. Set `RSVG_CONVERT` or
`FFMPEG` to use a binary outside `PATH`. Audio (optional, `--audio`) needs the `text-to-audio` skill (Kokoro, local); its
first run sets up a venv and downloads about 1 GB (see `reference/audio-narration.md`). Without it the build says
"AUDIO SKIPPED" and makes the silent video.

## Procedure

1. **Gather the facts and cite them.** List the entities, their fields and keys, the operations and the rules that
   refuse an operation. Read the code, the design or the document for each one; record each as a `sources` entry
   (title, locator such as a file and line or a section, the date read). Every entity, rule and frame cites at
   least one source id. Separate what you read in code (built) from what a design or roadmap says (not built).
2. **Write the scenario JSON.** Start from `examples/helpdesk/scenario.json`. Give every frame a one-line caption and
   one or more `note` annotations (target + text; no x, y). Declare `entities` (fields with
   `ref`, `enum`, `nullable`), `rules`, `initial_state`, `layout.panels`, and the `frames`. Each frame applies a few
   transitions to the previous state, so the file stays small. Format: `reference/scenario-format.md`; ops and rules:
   `reference/transitions-and-rules.md`; annotations and layout: `reference/layout-rules.md`.
3. **Validate.** `python3 scripts/validate.py <scenario.json>` (`--json` for machine output, `--strict` makes warnings
   errors). It checks the schema (`reference/scenario.schema.json`), the version, the references, every transition with
   its rules and the narration text. Every error has a JSON path. Layout (text fit, overlap) is checked by
   `render.py --check` and by the build.
4. **Build.** `bash scripts/build.sh <scenario.json> <out-dir> [--audio]` runs the stages validate, plan, render, (audio),
   video, pdf (below) and prints the frame count, the length and the size. `--audio` adds the voice from each frame's
   `narration`; without it the video is silent. One video per build: a silent copy of a voiced video is made only when
   the user asks (build again without `--audio` into another folder). Every build also writes `<name>.pdf` next to the
   MP4: the slides, one frame per page, with the top strip and bars as in the video.
5. **Review cheaply.** Read `contact_sheet.png` once, then at most 3 or 4 frames at full size (`png/NNN-<id>.png`):
   the busiest frame, a frame with a refusal or a rename, and the planned or inferred frame. Use
   `reference/review-checklist.md`.
6. **Fix and rebuild.** Edit the JSON (or a panel region), run step 4 again. Do not read the SVG text.
7. **Deliver** the MP4, the PDF and the contact sheet paths, the frame count and the length, and the list of frames whose
   badge is not `available`. Keep build output out of git (write it to a scratch or output folder).

## Rules that cost most when broken

- **Code draws the images, never an image model.** The renderer is deterministic; the same JSON gives the same
  frames. A picture from an image model cannot be checked against the state and costs tokens on every rebuild.
- **The explanation sits on the image, not in the caption.** Each frame (except a `summary` frame) carries at least one
  `note`: a text box with a `target` that the renderer places in the free space nearest to the target, with an arrow to
  it and a highlight around it, so the note never covers the focus. The caption bar is a one-line headline. A frame
  with no note fails the build; `"caption_only": true` on the frame is the explicit opt-out.
- **The top strip stays blank.** The Mac player overlays window buttons and the title on the top of the video, so every
  frame leaves a strip there (`meta.layout.top_margin`, default 48 px on 1080, 32 on 720) and scales the drawing into the
  space below; nothing may be drawn in it, and the build fails if it is. Text is about 4 % smaller than the coordinates say.
- **Review few frames.** The contact sheet shows every frame at 480 x 270; full-size reads are for 3 or 4 frames.
  The build already checks text fit, row capacity, overlap and targets, so the eye checks meaning, not geometry.
- **Accuracy: every frame carries a status badge.** Levels in the example: `available` (read in code, runs today),
  `designed` (a design exists, no code), `planned` (on a roadmap, no design), `inferred` (deduced, not run or
  read), `info` (an overview frame). A level whose badge sets `"unbuilt": true` draws every row it adds or changes
  dashed, in every later frame. Never mark an unbuilt step `available`; when unsure, use `inferred` and say why in
  `status.detail`.
- **Every claim cites a source.** Entities and rules need `sources`; frames need them unless their badge sets
  `"sources_optional": true`. The validator fails on an unknown or missing source id.
- **Rules are data, and the build runs them.** A step the real system refuses is a rule in `rules`; show it with
  `"expect_refused": "<rule id>"`, which fails the build if the rule does not refuse.
- **Loud failures stay loud.** Do not set `allow_overlap` or widen a column to silence an error before you look at
  the frame; shorten the text or move the callout first.

## Contract and stages

The scenario JSON is the only interface between whoever produces the data and the renderer. The schema is the single
source of truth (`reference/scenario.schema.json`, version in `x-version`, now 1.3; the scenario says which version it
was written for in `schema_version`). A minor change only adds optional fields, a major change breaks; rules in
`reference/scenario-format.md`. A producer that is not Claude starts from `reference/producing-scenarios.md`,
`examples/minimal/scenario.json` and `scripts/scenario_builder.py`. The data side (`engine`, `validate`, `plan`) never
imports the render side, and the renderer reads a plan file only; `tests/test_stages.py` enforces both.

## Quick reference

| Need | Where |
|---|---|
| Every key of the JSON, the style block, sources, panels | `reference/scenario-format.md` |
| The ops (add_row, update_row, set_field, move_row, delete_row) and the rule conditions | `reference/transitions-and-rules.md` |
| Annotation types, targets, layout limits, colours | `reference/layout-rules.md` |
| A scatter of points with edges and circles (graph panel), example | `reference/scenario-format.md` (graph keys), `examples/points-graph/scenario.json` |
| The contract: required and optional fields, errors, versions | `reference/scenario-format.md` |
| Producing a scenario from another tool | `reference/producing-scenarios.md` |
| Narration, voice, timing, cache, subtitles, setup | `reference/audio-narration.md` |
| Add a new op, annotation type or panel kind | `reference/extending.md` |
| What to look at on the contact sheet | `reference/review-checklist.md` |
| Run the tests | `python3 -m unittest discover -s tests -v` (the end-to-end test skips without ffmpeg or rsvg-convert) |

## Scripts

Each stage runs alone on files; `build.sh` chains them.

| Stage | Command | Reads, writes |
|---|---|---|
| 1 validate | `validate.py <scenario.json> [--json] [--strict] [--mini] [--schema-only]` | the scenario; a report, exit 1 on any error |
| 2 plan | `plan.py <scenario.json> -o <plan.json>` | the scenario; the state of every frame |
| 3 render | `render.py (--plan <plan.json> \| --scenario <s.json>) --out <dir> [--no-png] [--check]` | `svg/`, `png/`, `frames.json` |
| 4 audio | `audio.py --out <dir> [--voice V] [--speed S] [--pause SEC] [--language L] [--cache DIR]` | `frames.json`; `audio/{timeline.json,narration.wav,subtitles.srt}` |
| 5 video | `make_video.py --out <dir> [--timeline <dir>/audio/timeline.json] [--burn-subtitles] [--no-crossfade]` | MP4 (+ `.srt`), `contact_sheet.png` |
| 6 slides | `make_pdf.py --out <dir> [--no-vector]` | `<name>.pdf`: one frame per page (rsvg-convert, vector; Pillow on the PNGs as a fallback or with `--no-vector`; exit 3 and the build goes on when neither exists) |
| all | `build.sh <scenario.json> <out-dir> [--audio] [--burn-subtitles] [--no-crossfade]` | everything above |
| join | `join_parts.py --out <joined.mp4> [--srt <joined.srt>] [--silent] part1.mp4 ...` | one MP4 and one SRT from several parts |

`engine.py` (the transition engine and reference checks) and `scenario_builder.py` (the optional producer helper) are
modules, not commands.

## Hand-off

- A voice track: write `narration` per frame (speakable: numbers in words, no paths) and build with `--audio`.
- A chart inside a frame: out of scope; render the chart to an image elsewhere and show it in a deck instead.
