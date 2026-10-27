---
name: text-to-audio
description: Convert a text file or a Markdown file (a book summary, a paper, a full-text book) to an audio file (m4a with chapter marks, m4b, mp3 or wav) with Kokoro-82M, a local text-to-speech model, at no cost. Markdown is first made speakable - headings become chapters, LaTeX math becomes words (rules, or a local Ollama model), tables and code are skipped or read, links and formatting are removed. One command prints one JSON line per file. Use it when the user asks to convert text or Markdown to audio or speech, to make an audiobook or a listening version of a document, or to set up or check the text-to-audio skill. The first run sets the skill up by itself.
---

# text-to-audio

## When to use

- The input is a `.txt` file (plain text, hard-wrapped or not) or a `.md` file (Markdown, with or without LaTeX math).
- The user wants to listen to it: an audiobook of a summary, a paper or a book.
- The output is English speech. Kokoro has voices in other languages too (see Voices), but this skill is tested on
  English only.

Do not use it for a PDF or an EPUB directly: convert those to Markdown first with a PDF or EPUB converter, then use this skill on
the Markdown.

## Run

```
bash <skill-dir>/scripts/run.sh <file.md|file.txt> [more files] [options]
```

The output goes to `audio/<input name>.m4a` in the current working directory by default. Call `run.sh` directly. Do not run setup first: `run.sh` sets itself up when it must. Read only the JSON
line. Do not read a long input into the context.

| Option | Effect |
|---|---|
| `-o <file>` | Output file (one input only). The extension does not set the format; `--format` does. |
| `--out-dir <dir>` | Output folder (default `./audio`). |
| `--format m4a\|m4b\|mp3\|wav` | m4a (default): AAC with chapter marks, plays everywhere. m4b: the same, named for audiobook apps. |
| `--voice <name>` | Kokoro voice (default `af_heart`). See Voices. |
| `--speed 1.1` | Speaking speed (default 1.0). |
| `--math rules\|llm\|skip` | LaTeX math in Markdown: `rules` (default), `llm` (a local Ollama model speaks each formula), `skip` ("a formula"). |
| `--llm-model <name>` | Ollama model for `--math llm` (default `qwen3.5:9b`). |
| `--tables skip\|read` | Tables: skip with a short notice (default), or read row by row ("header: value, ..."). |
| `--code skip\|read` | Code blocks: skip with a notice (default), or read line by line. |
| `--chapter-level 2` | Headings at this level or above start a chapter (default 2: `#` and `##`). |
| `--lexicon <file.tsv>` | Extra pronunciations, applied after `lexicon.tsv` (see Pronunciation). |
| `--input-format auto\|markdown\|text` | auto (default): `.md` and `.markdown` are Markdown, anything else is text. |
| `--title <text>` | Title in the audio metadata (default: the first heading or the file name). |
| `--pause 0.45`, `--chapter-pause 1.0` | Silence after a paragraph, and after a heading, in seconds. |
| `--dry-run` | Write only the speech text (`<out>.speech.txt`) and stop. No audio. Use it to check a document first. |
| `--text-out <file>` | Also write the speech text to this file. |
| `--keep-parts` | Keep the per-chapter WAV files (`<out>.parts/`). |
| `--device auto\|mps\|cuda\|cpu` | Device for Kokoro (default: mps on a Mac, then cuda, then cpu). |
| `--bitrate 48k` | AAC or MP3 bitrate, mono (default 48k: about 21 MB an hour). |

## What happens to Markdown

The speech text keeps every sentence of the prose and changes only what a voice cannot read:

| Markdown | Spoken as |
|---|---|
| `#` and `##` headings | A new chapter (a chapter mark in the m4a), and the heading is read. Deeper headings are read, with a pause. |
| ALL-CAPS headings | Normal case, for tidy chapter titles. Short acronyms stay (LLM, GPU). The voice reads all-caps words as words and spells unknown acronyms either way. |
| `**bold**`, `*italic*`, `` `code` ``, `~~strike~~` | The words only. |
| `[text](url)`, `[[wiki link]]` | The text. A bare URL becomes "a link". |
| Images, HTML tags and comments (`<!-- page 12 -->`), YAML front matter, footnote marks | Dropped. |
| Lists, block quotes | One paragraph per item, with a pause. |
| Inline math `$x_i^2$` | Words, by rules: "x sub i squared". |
| Display math `$$...$$`, ```` ```math ```` | "Equation: ..." in words; over 400 characters, "A long equation is shown here." |
| Tables | "A table with N rows is skipped here." (or read, with `--tables read`). |
| Code blocks | "A code listing is skipped here." (or read, with `--code read`). |
| Margin notes and figure notes from PDF converters (`*[Margin note] ...*`) | Dropped. |
| `x = y`, `a + b`, `<=`, `>=`, `≈` standing alone in prose | "equals", "plus", "is at most", "is at least", "is approximately". |

Plain text: paragraphs are split at blank lines and the hard-wrapped lines of a paragraph are joined. A heading is a
short line standing alone (after a blank or a rule line). Major headings are underlined with `===`, set between two
`===` rules, or start with CHAPTER, PART or APPENDIX (the style of typical plain-text book summaries).
Minor headings are underlined with `---`, or are an ALL-CAPS line on its own (the style of typical plain-text paper notes).
The major headings start chapters when there are two or more of them; otherwise the minor
ones do. Rule lines are dropped. A block of column-aligned lines
(three or more spaces inside a line: a table or a listing) is skipped with a notice, or read with `--tables read`.

### Math: rules or LLM

- `rules` (the default) is instant and reads clean LaTeX well: fractions, roots, sums and products with limits,
  sub- and superscripts (squared, transpose, inverse), Greek letters, accents (x hat), norms, kets, bras and inner
  products, `\mathbb{E}`, relations and arrows. A formula is read symbol by symbol, so a long one is hard to follow.
  Also: the rows of an `aligned`/`cases` block are read as separate sentences (`&` is silent); `x_*` is "x star";
  `\nabla_\theta f` is "the gradient with respect to theta of f"; `\lvert x \rangle` is "ket x" and `\lvert x \rvert`
  or `|x|` "the absolute value of x"; Cov, Var, Corr, tr are words; a lower-case `\mathrm`/`\operatorname` name
  (`do`, `softmax`) is a word, an upper-case one (`ATE`, `KL`) is spelled; `\mathcal{N}` is "normal" and a
  multi-letter `\mathcal{GP}` is "G P". For clean LaTeX, rules now read as well as `llm` (narratives/quantum,
  2026-10-04), so prefer rules.
- `llm` sends the formulas of each paragraph (not the prose) to a local Ollama model, which returns one spoken
  form per formula as JSON. The prose is never sent back through the model, so it cannot change. A reply that is
  malformed, has the wrong count, or still holds LaTeX is rejected and the rules are used (`llm_fallbacks`). Answers
  are cached in `~/.cache/text-to-audio/llm-cache.json`, so a re-run makes no calls for the same formulas. Ollama
  must be running with the model pulled. On the 17-page Nomic Embed paper (119 formulas): 34 calls, about 2
  minutes with `qwen3.5:9b` (2026-10-04).
- Math that a PDF converter took from a PDF text layer can be garbled (broken fractions, stray symbols). Neither
  mode can repair it; `skip` is the cleanest for such a paper.

**No Ollama?** The default `--math rules` needs none and stays fully local. If the rules read a formula badly, the
agent running this skill (Claude) can rewrite those formulas in words in a copy of the Markdown before the run. Tell
the user first that this is not fully local: the passages are then sent to Claude. Do it only when the user agrees.

## Pronunciation

`lexicon.tsv` (next to this file) holds whole-word replacements applied to the speech text: abbreviations (e.g., i.e.,
et al., Fig., Eq., Sec.) and terms the voice gets wrong (LoRA, arXiv, qubit, LaTeX). Unknown all-caps acronyms
(MTEB, GPU) are spelled out by the voice without an entry. One pair per line,
separated by a TAB; `#` starts a comment. Add a line when a term comes out wrong, or pass a file of your own with
`--lexicon`. Kokoro reads numbers, years, dates and most acronyms by itself.

## The JSON line

One line per input on stdout. On a failure the line is `{"ok": false, "input": ..., "error": "..."}` and the exit
code is 1.

| Field | Meaning |
|---|---|
| `ok`, `out`, `input`, `format`, `voice`, `device` | Status and the files. |
| `chapters`, `paragraphs`, `characters` | The speech text. |
| `audio_seconds`, `audio` | Audio length, in seconds and as h:mm:ss. |
| `seconds`, `realtime_factor` | Wall time; audio seconds per second of wall time. |
| `inline_math`, `display_math`, `tables`, `code_blocks`, `images`, `links` | What the preparation found. |
| `llm_rewrites`, `llm_calls`, `llm_cached`, `llm_fallbacks` | With `--math llm`: formulas spoken by the model, calls, cache hits, rejected replies. |
| `chapters_reused` | Chapters taken from a previous, stopped run (see Resume). |
| `speech_text` | With `--dry-run` or `--text-out`: the speech text file. |
| `setup` | Present only when setup ran before this conversion. |

A field with the value 0 can be absent.

## Resume and progress

Each chapter is written as a WAV in `<out>.parts/`, named by a hash of its text and the voice settings. A run that
stops (Ctrl-C, a crash) resumes: the next run with the same settings reuses the finished chapters. `<out>.progress`
gets one line per chapter; read it to follow a long run. Both are removed after a successful run, unless
`--keep-parts`.

## Voices

The default `af_heart` is the best-rated American English voice. The first letter is the language and the second
the gender: `a` American English, `b` British English. English voices:

- American female: af_heart, af_bella, af_nicole, af_aoede, af_kore, af_sarah, af_nova, af_sky, af_alloy,
  af_jessica, af_river
- American male: am_michael, am_fenrir, am_puck, am_echo, am_eric, am_liam, am_onyx, am_adam, am_santa
- British: bf_emma, bf_isabella, bf_alice, bf_lily, bm_george, bm_fable, bm_lewis, bm_daniel

Other languages exist (e Spanish, f French, h Hindi, i Italian, p Portuguese, j Japanese, z Chinese), but the
preparation rules and the lexicon are English, and Japanese and Chinese need extra packages that setup does not
install.

## Setup

Setup is automatic. The first `run.sh` on a machine runs `scripts/setup.sh`: it creates a venv
(`~/.cache/text-to-audio/venv`; set `TEXT_TO_AUDIO_VENV` for another), installs the pinned packages from
`requirements.txt` (PyTorch, Kokoro, misaki with a bundled espeak-ng, spaCy's small English model, soundfile, and
imageio-ffmpeg, which bundles an ffmpeg binary), downloads Kokoro-82M at a pinned revision (about 330 MB, into the
Hugging Face cache), runs the self-test, and writes a ready marker. It needs about 1 GB of downloads and a few
minutes. Tell the user this before the first run on a new machine. The setup output goes to
`~/.cache/text-to-audio/setup.log`. `run.sh` runs setup again when `requirements.txt` or the model revision changes.

- Python 3.10, 3.11 or 3.12 (Kokoro does not support 3.13 yet). Setup takes the newest on the PATH; set `PYTHON` to
  choose one.
- Keep the venv path short (under about 120 characters): the bundled espeak-ng cannot read its data from a long path
  and fails with "Error processing file .../phontab".
- `bash <skill-dir>/scripts/setup.sh --check-only` exits 0 when the setup is current; `--force` sets up again.
- No system ffmpeg, espeak-ng or Homebrew package is needed. A system `ffmpeg` on the PATH is used when present.

## Check the machine

```
bash <skill-dir>/scripts/selftest.sh
```

It converts a small Markdown file (two chapters, a list, inline and display math, a table, code) and a small text
file, and checks the JSON lines, the audio length, the m4a, and that the speech text has no Markdown or LaTeX left.
It prints one JSON line with `"selftest": "pass"` or `"fail"`.

## Time and size

Measured on a MacBook Pro M5, 24 GB, 2026-10-04:

| Work | Speed |
|---|---|
| Kokoro on the GPU (MPS) | about 11 s of audio per second (6,000 characters: 385 s of audio in 35 s) |
| Kokoro on the CPU | about 6 s of audio per second |
| Model load | about 4 s |

Speech runs at about 900 to 1,000 characters a minute, so a 50,000-character paper is about 50 minutes of audio
(about 5 minutes to make), and a 600,000-character book about 10 hours (about an hour to make). At 48 kbps mono an
hour of audio is about 21 MB.

## Output and git

Add `audio/` to your `.gitignore`: a book is 100 to 300 MB of audio, and GitHub refuses files over 100 MB. Keep the audio
out of the repository, or use Git LFS if it must be versioned.

## Known limits

- Kokoro reads about 500 phoneme tokens at a time; misaki splits longer paragraphs at punctuation. A very long
  sentence with no punctuation can be cut at an odd point.
- Proper names and rare terms can be stressed wrongly; add them to the lexicon.
- Math read by rules is literal ("e to the power s of q sub i ... over tau") and tiring for long formulas.
- Tables are skipped by default. A table read row by row is accurate but slow to listen to.
- The voice has one tone. It does not act out dialogue.

## Licences

Kokoro-82M (weights and code) is Apache-2.0. misaki is Apache-2.0. espeak-ng (bundled by espeakng-loader) is
GPL-3.0. PyTorch is BSD-3-Clause. spaCy and en_core_web_sm are MIT. The ffmpeg binary in imageio-ffmpeg is LGPL or
GPL, depending on the build.
