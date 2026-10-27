# Review checklist

The build has already checked geometry: text fit, row capacity, overlap, targets, rules. The review checks meaning.
Read `contact_sheet.png` once; open at most 3 or 4 frames at full size.

## On the contact sheet (all frames at once)

- [ ] The story reads left to right, top to bottom: each frame changes one thing that the next one builds on.
- [ ] The status badges change where the truth changes: every frame that shows something not built has an
      unbuilt badge (designed, planned, inferred), and its rows are dashed.
- [ ] No frame is a near copy of the one before it with nothing marked (no green, yellow, ring or annotation).
- [ ] The top strip (48 px on 1080) is blank on every frame: the player overlay covers it when the pointer is over the video.
- [ ] The busiest frame is still readable at thumbnail size: the changed row or chip stands out.
- [ ] The colour of one category, lane or entity is the same in every frame.
- [ ] The first frame explains how to read a frame (or the video starts with a simple state); the last frame
      sums up, with the status of each step.

## On the full-size frames (pick 3 or 4)

Pick the busiest frame, one frame with a refusal, a rename or a merge, and the unbuilt frame.

- [ ] Each note sits next to its focus, an arrow lands on the focus area, the highlight is on it, and no note covers
      a value the frame is about. The caption is only a one-line headline.
- [ ] The notes say what changed and why, in the words of the panel (field and row names match).
- [ ] The title states the step, not the mechanism ("Wi-Fi merges into Network", not "move_row with where").
- [ ] Arrows start and end on the right rows; no label sits on top of a value that matters.
- [ ] A highlight label or a REFUSED label covers no value the caption talks about.
- [ ] A before/after box shows the old and the new value the caption names.
- [ ] Ghost rows and lanes appear only on the frame of the delete.
- [ ] Nothing reads as built that the sources do not show as built.

## Against the sources

- [ ] Each `available` frame cites a source of kind `code` or `measurement`, or the reader is told otherwise.
- [ ] Each rule cites the place that enforces it; a rule taken from a design only belongs to a designed frame.
- [ ] `meta.notes` says which data is invented.

## When a check fails

Fix the JSON (caption, annotation position, frame order, badge level), rebuild, and look again at the contact
sheet only. Re-open a full-size frame only if the fix was in that frame.
