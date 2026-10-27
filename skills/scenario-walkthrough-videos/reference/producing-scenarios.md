# Producing a scenario from another tool

For a program, a person or another model that writes the JSON and does not run the renderer. The contract is
`scenario.schema.json` (version in `x-version`; see `scenario-format.md`, "The contract"). You need nothing else from the skill.

## Steps

1. Write one JSON object. Start from `examples/minimal/scenario.json` (2 frames) or build it in Python with
   `scripts/scenario_builder.py` (`scenario`, `source`, `entity`, `table_panel`, `frame`, `note`, `add_row`, ...; standard
   library, writes data only).
2. Set `"schema_version": "1.3"` (1.1 is enough without a graph panel or `top_margin`).
3. Validate: `python3 scripts/validate.py scenario.json --json` (exit 0 = valid; every error has a JSON path). Add `--strict` to
   fail on warnings too. A program can read the JSON and fix the paths it names.
4. Check the layout: `python3 scripts/render.py --scenario scenario.json --out /tmp/x --check`. Then build:
   `bash scripts/build.sh scenario.json <out-dir> [--audio]`.

## Fields you must give

`meta` {title, width 640-3840, height 360-2160, fps 1-60, default_seconds >= 1}, `style` {badges, plus the arrow, highlight and
callout styles your notes name}, `sources` [{id, title}], `entities` {name: {fields, sources}}, `initial_state` {entity: [rows]},
`layout.panels` [{id, kind table|board, entity, region [x, y, w, h], title, columns | lane_field + lanes}], `frames` [{id, step,
title, caption, status {level}, sources, transitions, annotations}]. Everything else is optional.

## What makes the build fail (limits)

| Limit | Value |
|---|---|
| Ids (frame, panel, entity, source, rule) | letters, digits, `_`, `-`; unique; entities need `sources` |
| Frame `step` / `title` / `caption` / `status.detail` | up to 4 / 90 / 120 / 110 characters; the title must fit the 32 px bar, the caption one line |
| Panel region | inside the content area (y from 96 to 1000 at 1080 high), no two panels overlapping |
| Table rows | `(region height - 72) / row_height` per panel (set `window` to show only the first N) |
| Table columns | total width at most panel width minus 24; each cell text must fit its column |
| Board chips | the lane height holds a fixed number; every row's lane value needs a lane |
| Notes | one to three per frame, each with a `target` that exists on that frame; a note gets its own free space, so a long text on a crowded frame fails: shorten it, or set `w`, or hide a panel with the frame's `panels` |
| Every frame | a `note` (or `before_after`), unless it is a `summary` frame or sets `caption_only` |
| Sources | every entity, rule and frame cites existing source ids (frames with a `sources_optional` badge, such as an overview, may not) |
| Transitions | fields exist, enums and nullability hold, foreign keys point at existing rows, a row still referenced cannot be deleted, rules do not refuse (unless the step says `expect_refused: "<rule id>"`) |
| Badges | every frame's `status.level` is a key of `style.badges`; never mark a step `available` that code or a measurement does not show |
| Narration | at most 600 characters; no file path, URL, backtick, table or code symbol (errors); digits are a warning |

Targets: `panel:<id>`, `row:<panel>:<key>`, `cell:<panel>:<key>:<field>`, `lane:<panel>:<value>`, `callout:<id>`.
Ops: `add_row`, `update_row`, `set_field`, `move_row`, `delete_row` (see `transitions-and-rules.md`).

## Checklist

- [ ] `schema_version` is `1.3`; `validate.py --strict` exits 0.
- [ ] Every entity, rule and frame cites a source that says it; the badge is the weakest level the sources support.
- [ ] Each frame's caption is a headline; the explanation is in the notes (and the narration).
- [ ] `render.py --scenario ... --check` prints `layout OK`.
- [ ] Narration, if any, is plain spoken sentences, numbers in words, no identifiers read aloud.
- [ ] Nothing in the JSON depends on a tool: no absolute paths, no references to files outside the scenario.
