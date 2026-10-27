# Scenario format

The scenario is one JSON file, and it is a **contract**: whoever produces it (a person, a program, another model) needs
no knowledge of the renderer, and the renderer needs no knowledge of how the file was made. `scenario.schema.json`
(this folder) is the single source of truth for the shape; this page explains it. `scripts/validate.py` checks the
schema and what a schema cannot check (ids, sources, regions, rules, narration). Examples: `examples/minimal/scenario.json`
(two frames, with narration) and `examples/helpdesk/scenario.json` (ten frames, no narration). To produce a scenario
from another tool, read `producing-scenarios.md`.

## The contract

**Version.** The schema file carries its version in `x-version` (now `1.3`). A scenario names the version it was written
for in `schema_version` (`"MAJOR.MINOR"`; absent means `1.0`; always set it when you produce one).

| Change | Meaning | Effect on scenarios |
|---|---|---|
| Minor (1.0 to 1.1, 1.1 to 1.2, 1.2 to 1.3) | Adds optional fields or looser limits only | Every scenario valid under 1.x stays valid under 1.y, y larger. A scenario for a newer minor than the skill's is refused with "newer than this contract", never half-read (unknown fields are errors). |
| Major (1.x to 2.0) | May remove or change a field, or change what one means | Refused with "major version ... not supported"; migrate the scenario. No automatic migration is promised. |

1.1 added: `schema_version`, `meta.audio`, frame `narration`, `meta.layout.note_font_size`. 1.2 added: the `graph` panel kind (a to-scale 2-D scatter of the rows of one entity, with the rows of a second entity drawn as edges and a radius field drawn as circles; keys below) and the targets `edge:` and `circle:`. 1.3 added: `meta.layout.top_margin` and the default blank top strip (every older scenario gets it when built, with no edit). A 1.0 or 1.1 scenario needs no edit. A 1.0 scenario's
notes now draw at the new default size 21 (1.0 drew 18) unless it sets `note_font_size` to 18.

**Required** (the build fails without them): `meta` (`title`, `width`, `height`, `fps`, `default_seconds`), `style`, `sources`,
`entities`, `initial_state`, `layout.panels`, `frames` (each: `id`, `step`, `title`, `caption`, `status.level`). Everything else
is optional. Each frame also needs, unless its badge has `sources_optional`, `sources`, and, unless it is a `summary`
or `caption_only` frame, one `note`.

**What the renderer guarantees** for a scenario that passes validate and `render.py --check`: the same JSON gives the same frames
byte for byte; no text is cut, no note covers a row, chip or other note, every transition respected the declared fields, keys and
rules, and every frame shows its status badge. A scenario that cannot be drawn that way fails the build; it is never drawn badly.

**Errors.** `validate.py` prints `ERROR <json path>: <message>` for every schema and reference problem (`--json` gives
`{"ok", "errors": [{"path", "message", "stage"}], "warnings"}`; stages: `parse`, `schema`, `version`, `semantic`, `narration`). The
first failing transition is reported (later states depend on it). Layout problems (text fit, no room for a note, overlap) are
reported by `render.py --check` and the build as `RENDER ERROR frame '<id>' ...`. Exit codes: 0 valid, 1 invalid, 2 usage.

## Top-level keys

| Key | Required | What it holds |
|---|---|---|
| `schema_version` | no (set it) | `"1.3"`: the contract version the scenario was written for; absent means 1.0. |
| `meta` | yes | Title, canvas size, timing, fonts, output name, notes, audio settings. |
| `style` | yes | Colours, colour maps, status badges, arrow, highlight and callout styles. |
| `sources` | yes | Every source a claim rests on: `id`, `title`, optional `locator`, `read_on`, `kind`. |
| `entities` | yes | The data model: one entry per entity with its `fields`, `key` and `sources`. |
| `rules` | no | Domain rules that refuse a transition (see `transitions-and-rules.md`). |
| `initial_state` | yes | `{entity: [rows]}` before the first frame. A missing field is null. |
| `layout` | yes | `panels`: where each entity is drawn, as a table or a board. |
| `frames` | yes | The steps of the video, in order. |

## meta

| Key | Default | Notes |
|---|---|---|
| `title` | required | Shown nowhere on the frames; for the file and the reader. |
| `width`, `height` | required | 1920 x 1080 is the tested size. Panel regions are in these pixels. |
| `fps` | required | 30 is enough for still frames with a crossfade. |
| `default_seconds` | required | Seconds a frame shows when it sets no `seconds`. |
| `crossfade_seconds` | 0 | Fade between frames; each fade shortens the video by its length. |
| `output_name` | `walkthrough` | The MP4 is `<out>/<output_name>.mp4`. |
| `layout.title_height`, `layout.caption_height` | 84, 72 | The bars at the top and bottom. The content area lies between them. |
| `layout.top_margin` | 32 px per 720 px of height (48 on 1080) | Schema 1.3. Blank strip at the top of every frame, for the player overlay (window buttons, title); the drawing is scaled into the space below. 0 to 300; the validator warns below 24 per 720 (36 on 1080). Within 1.3 the default was first 64 per 720, then halved to 32; the field and range did not change. See `layout-rules.md`. |
| `layout.caption_max_lines` | 1 | Lines the caption may take. |
| `layout.note_font_size` | 21 | Font size in px of the text in notes, callouts and before/after boxes (12 to 36); line height is 4/3 of it. The default note width is 460 px. |
| `audio` | none | `voice` (Kokoro name, default `af_heart`), `speed` (1.0), `pause_seconds` (0.6, silence after a narration), `language` (`en-us`). Used only when the build asks for audio. |
| `fonts` | Helvetica, Menlo | `sans`, `mono` and `char_width` (average character width as a share of the font size, used to measure text). |
| `notes`, `version`, `date` | none | Free text. Say here when the example data is invented. |

## style

| Key | Shape | Used by |
|---|---|---|
| `colors` | `{name: "#RRGGBB"}` | Overrides of the defaults in `render.py` (`DEFAULT_COLORS`): background, panel, text, muted, changed, added, ghost, null, moved, refused, title and caption bars. |
| `entity_colors` | `{entity: colour}` | The accent bar of each panel. |
| `color_maps` | `{map: {value: colour}}` | A dot in front of a table cell, a chip fill, a lane dot. Every value that appears needs a colour. |
| `badges` | `{level: {fill, text, label, unbuilt?, sources_optional?}}` | The status badge of a frame and the summary table. |
| `arrows`, `highlights` | `{name: {color, width, dash?}}` | Arrow and highlight styles. |
| `callouts` | `{name: {fill, border, text}}` | Callout and before/after boxes. |

## entities

```json
"requests": {
  "title": "Requests",
  "key": "id",
  "sources": ["MODEL"],
  "fields": {
    "id": {"nullable": false},
    "category_id": {"ref": "categories"},
    "status": {"enum": ["new", "triaged"], "nullable": false}
  }
}
```

- `key` (default `id`) must be a field; its value is unique and never changes.
- `ref` makes a field a foreign key: a value must be the key of a row of that entity. Only a `ref` field can be the
  `field` of a `move_row`, and a row that is still referenced cannot be deleted.
- `enum` limits the values; `nullable: false` forbids null.

## layout.panels

Every panel has `id`, `kind` (`table`, `board` or `graph`), `entity`, `region` `[x, y, w, h]` in canvas pixels, and `title`.
Regions must lie inside the content area and must not overlap. One entity may appear in several panels.

Table keys: `columns` (`field`, `width`, optional `header`, `font` `sans`|`mono`, `color_map`), `row_height` (30),
`font_size` (15), `max_rows`, `order` (`insert` or `newest_first`), `window` (show only the first N rows after
ordering, for a log), `subtitle`, `note` (the right-hand note; default "N rows").

Board keys: `lane_field` (the field that picks the lane), and either `lanes` (a fixed list of values, with optional
`lane_labels`) or `lanes_from` (`{entity, label}`: one lane per row of another entity, for a foreign key);
`null_lane` (the label of a lane for null), `lane_color_map`, `chip_label` (default the key), `chip_width` (56),
`chip_color_field` plus `color_map` (the chip fill).

Graph keys (schema 1.2; `x_field`, `y_field`, `edges_entity` and `domain` are required): the panel's entity holds the points,
`x_field` and `y_field` their coordinates, and `domain` `{"x": [min, max], "y": [min, max]}` the data range; the plot is drawn
to scale (equal units on both axes) in the top left of the panel (`plot` `[x, y, w, h]` overrides the area) with a grid at the integers, and a point
outside the domain fails the build. Optional: `radius_field` (a number field; a dashed circle of that radius around the point,
clipped to the plot, with a pill "r 1.000"; null or 0 draws none), `point_color_field` with `color_map` (the point fill; a change
of that field draws an orange ring), `point_label` (default the key), `show_coords`, `circle_color`. The rows of
`edges_entity` are the edges: `edge_from` and `edge_to` (default `a` and `b`; both must be `ref` fields to the point entity;
the same point at both ends draws a small loop, a self-edge), `edge_weight` (the number shown on the edge), `edge_state` (a field
whose value picks a style in `edge_styles`: `{state: {color, width, dash, label, hidden}}`; `label` draws the weight on the edge, `hidden`
omits it), `edge_label_pos` (a number field, 0 to 1: where along the edge the label sits, default 0.5). An edge deleted in this frame
is drawn dashed red, unlabelled (a ghost, as for a table row). Everything is data: moving a point, growing a circle, adding or removing
an edge is an `update_row`, `add_row` or `delete_row`. Targets: `row:<panel>:<point>`, `edge:<panel>:<edge>` (the edge's label
spot), `circle:<panel>:<point>` (the radius pill). The plot area is solid, so a note never lands on it; leave room beside
the graph (a second panel, hidden on some frames) for the notes.

## frames

| Key | Required | Notes |
|---|---|---|
| `id` | yes | Unique; used in file names and error messages. |
| `step` | yes | Up to 4 characters, shown in the coloured chip at the top left. |
| `title` | yes | Up to 90 characters; must fit the title bar at 32 px. |
| `caption` | yes | A one-line headline (up to 120 characters). The explanation goes in a `note`, not here. |
| `caption_only` | no | `true` opts the frame out of the rule that every frame has an on-image `note` (use for a title or divider frame). |
| `status` | yes | `level` (a key of `style.badges`) and `detail` (one line under the title). |
| `sources` | yes, unless the badge has `sources_optional` | Source ids for this step. |
| `transitions` | no | Applied in order to the previous frame's state. |
| `annotations` | yes, unless `summary` or `caption_only` | Drawn over the panels (see `layout-rules.md`). At least one `note` (target + text, auto-placed with an arrow) or `before_after`. |
| `seconds` | no | The visual hold time; overrides `meta.default_seconds`. With audio a frame lasts `max(seconds, narration + pause + lead + crossfade)`. |
| `narration` | no | Text spoken over this frame (up to 600 characters). Written for the ear: numbers in words, no paths or code. A frame without it is silent. See `audio-narration.md`. |
| `panels` | no | Show only these panel ids on this frame. |
| `summary` | no | Draw a table instead of the panels: `columns`, `widths`, `rows`, `status_column` (a column whose cells are badge levels). |

## sources

```json
{"id": "GUIDE-3", "title": "Process guide, section 3: assignment", "locator": "docs/guide.md:120",
 "read_on": "2026-10-23", "kind": "document"}
```

`kind` is one of `code`, `design`, `document`, `measurement`, `interview`, `example`. A frame whose badge is
`available` should cite at least one `code` or `measurement` source; the validator does not enforce this, the
review does.
