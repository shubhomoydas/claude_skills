# Annotations and layout rules

## The frame

A frame is the canvas (`meta.width` x `meta.height`, 1920 x 1080 tested) in a blank top strip and three bands: the title
bar (step chip, title, status detail, status badge, frame number), the content area with the panels, and the caption bar
(a one-line headline at 28 px; `meta.layout.caption_max_lines` allows more). The content area runs from `title_height + 12`
to `height - caption_height - 8`: 96 to 1000 px with the defaults (title 84, caption 72). Panel regions, notes and every
other coordinate are in these canvas pixels.

## The top strip is reserved (schema 1.3)

The Mac player overlays the window buttons and the title on the top of the video when the pointer is over it, so nothing
written there can be read. Every frame therefore leaves a blank strip at the top, `meta.layout.top_margin` px
(default 32 px per 720 px of height: 48 px on 1080, 32 on 720). It is a solid band in the title-bar colour and holds nothing.
The whole drawing (bars, panels, notes, arrows, graph) is scaled uniformly, about 0.956 on 1080, and centred in the space below
it, so the frame size, the video size and every region in the scenario stay as written; the title and caption bars run to the
edges. Text is about 4 % smaller than the coordinates say (a 20 px note is drawn at about 19 px). A larger margin shrinks the
drawing more; the validator warns below 24 px per 720 px of height (36 on 1080), just under the height of the player overlay at small windows. Auto-placement and the other checks work in
the unscaled canvas, so they respect the strip by construction, and the renderer also fails the frame when any drawn element
(rect, text, circle, line, path; clipped parts excepted) reaches above the drawing area: `frame '<id>': ... lies in the top
48px strip`. A producer that sets `top_margin: 0` gets no strip and takes the risk.

## Explain on the image (the default)

Text in notes, callouts and before/after boxes is `meta.layout.note_font_size` px (default 21; schema 1.0 drew 18) with a line height of 4/3 of it; the fit, overlap and free-space checks use that size, and a note keeps 10 px clear of rows, chips, headers and other notes. A larger size needs a wider `w` or more `max_lines`; the build says which note does not fit.

The explanation of a frame lives in `note` annotations, not in the caption. A note has a `target` (the focus), a
`text`, optional `title`, `style` (a callout style), `w` (default 460), `max_lines`, `highlight` (a highlight style, or
`false` for none; default `focus`, else the first style) and `arrow_style`. The renderer:

1. places the box in the free space nearest to the target, preferring the gaps between panels, then a panel's empty
   part. It never covers a row, a chip, another note, a panel header, the target of any note or highlight, or the
   course of an `arrow` annotation;
2. draws a highlight around the target and an arrow from the note to it;
3. fails the build, naming the frame and the target, when no free spot exists: shorten the text, narrow `w`, hide a
   panel with the frame's `panels` (a hidden panel frees its region), or give `x` and `y`.

With `x` and `y` the note is placed there (manual); the overlap check still applies unless `allow_overlap` is set. One
or two notes per frame, each on its own focus. A frame with no `note` (or `before_after`) is refused unless it is a
`summary` frame or sets `"caption_only": true`.

## Annotation types

| type | Keys | Draws |
|---|---|---|
| `arrow` | `from`, `to`, `style`, optional `label` | A curve from one target to another, with a head and a label pill at the middle. Same column: it loops out on the right. |
| `highlight` | `target`, `style`, optional `label` | A box around the target; the label goes above, below, or inside, wherever it covers no row. |
| `note` | `target`, `text`, optional `id`, `title`, `style`, `w`, `max_lines` (6), `highlight`, `arrow_style`, `x`, `y`, `allow_overlap` | The on-image explanation: a box auto-placed next to the target, with a highlight and an arrow (see above). Becomes the target `callout:<id>`. |
| `callout` | `id`, `x`, `y`, `w`, `text`, `style`, optional `title`, `max_lines` (6), `allow_overlap` | A note box at a fixed place, with no arrow; use `note` instead unless you need a free-standing box. |
| `badge` | `target`, `text`, `style` (a badge level) | A small pill on the top right of the target. |
| `before_after` | `target` (a `cell:` target), `x`, `y`, `w`, `style` (a callout style), optional `title`, `arrow_style` | A box "before: X after: Y" with an arrow to the cell. The values come from the state; it fails when the field did not change in this frame. |
| `tint` | `target`, `color` (a `colors` name or `#RRGGBB`), optional `opacity` (0.28) | A see-through fill over the target, for a colour change or a group. |

Automatic marks need no annotation: a green row or ring for an added row, a yellow cell for a changed field, an
orange ring for a moved chip, a dashed struck row or lane for a deleted one, a dashed row in the badge colour for an
unbuilt change, and a red "REFUSED" box for a refused transition.

## Targets

| Target | Meaning |
|---|---|
| `panel:<panel>` | The whole panel. |
| `row:<panel>:<id>` | A table row, or a chip on a board. |
| `cell:<panel>:<id>:<field>` | One cell of a table row. |
| `lane:<panel>:<value>` | A board lane; the value of a null lane is `NULL`. |
| `callout:<id>` | A callout or a before/after box drawn earlier in the frame. |
| `edge:<panel>:<id>` | Graph panel: the label spot of an edge (its middle, or `edge_label_pos`; a self-edge: its loop). |
| `circle:<panel>:<id>` | Graph panel: the radius pill of the circle around a point. |
| `row:<panel>:<id>` | Graph panel: the point itself. |

An unknown target stops the build and lists the known ones. Drawing order: tint, callout, before_after, highlight,
arrow, badge, refusals, then the bars, so an arrow can point at a callout.

## What the build refuses

- A text wider than its box: a cell, a header, a panel title, a lane title, a chip label, the frame title, the
  status detail, a callout line, a word wider than a callout. The error gives the text, the width needed and the
  width of the box. Text is measured with `meta.fonts.char_width`; it errs on the wide side.
- More rows than a table panel holds (`(region height - 72) / row_height`, or `max_rows`), unless `window` is set.
- More chips than a lane holds.
- Columns wider than the panel (total width above panel width - 24).
- A callout or before/after box that covers a row, a chip or another callout, unless `allow_overlap` is set, or
  that leaves the content area.
- Two panels that overlap, a panel outside the content area, a row whose lane value has no lane.
- A caption over its line limit (1 by default), a summary table taller than the content area.
- A note with no free space for its box; a frame (not a summary) with no note and no `caption_only`.

## Readable frames

- At most about 10 rows per table and 8 lanes per board on a 1920 x 1080 frame; split the story across frames
  rather than shrinking the font. The smallest text drawn is 12 px (headers); the cells are 14 to 15 px.
- One idea per frame: one or two notes, each with its own target, and a one-line caption.
- Colours: dark text (`#1F2A3D`) on light fills; white text only on fills dark enough for it (the default badge
  and chip colours are). Keep one colour per meaning across the video: one colour per category in `color_maps`,
  red for refused and planned, amber for designed, purple for inferred.
- Do not encode meaning in colour alone: a changed cell is also named in a note, an unbuilt row is also
  dashed and italic, a deleted row is also struck through.
- Leave free space in a panel for the callouts of later frames: the overlap check places nothing for you.
