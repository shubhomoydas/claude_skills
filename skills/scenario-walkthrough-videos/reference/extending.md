# Extending the renderer

Prefer data over code. Before adding an op, check whether the five ops plus a rule express it: a merge is
`move_row` with `where` and a `delete_row`; a "close" is a `set_field`; a "copy" is `add_row`. Add code only when a
transition changes many rows by a computed pattern that would bloat the JSON.

## Add a transition op

1. In `scripts/engine.py`, section 1, write `op_<name>(sc, state, ctx, t)`:
   - read and check everything first (`find`, `check_value`), then call
     `check_rules(sc, state, "<name>", entity, {"row": ..., "new": ..., "to": ..., "_fields": {...}})` for each row
     it will change, and only then change the state;
   - record each change with `_touch(ctx, entity, id, field, old_value)`, each new row in `ctx.added`, each deleted
     row in `ctx.removed` as `(entity, snapshot, index)`;
   - return the list of `(entity, id)` it touched, so an unbuilt frame can mark them.
2. Add it to `OP_FUNCS` and `OPS`.
3. In `reference/scenario.schema.json`, add the name to the `op` enum of `definitions.transition`, any new keys to
   its `properties`, and an `if`/`then` with its required keys.
4. Document it in the ops table of `transitions-and-rules.md`.
5. Add tests to `tests/test_render.py` (engine tests use `engine.compute_frames`): the change it makes, a refusal by a rule, and a bad input that fails.

## Add an annotation type

1. In `FrameRenderer`, write a method named like the type. Read targets with `self.bbox(key)` (it fails loudly on an
   unknown target). A box that holds text goes through `boxed_note` (wrap, fit, overlap check); register a box other
   annotations may point at with `self.register("callout:<id>", box)`.
2. Add the name to `ANNOTATIONS` and to the drawing order in `render()`.
3. In the schema: the `type` enum of `definitions.annotation`, new keys, and an `if`/`then` with its required keys.
   If it uses a style table, map it in `reference_errors` in `scripts/engine.py` (`tables`).
4. Document it in `layout-rules.md` and add a test that renders it and one that makes it fail.

Anything drawn must have y >= 0 in canvas coordinates: `Svg.render` scales the drawing into the space below the top strip
(`meta.layout.top_margin`) and fails the frame when an element reaches above it. Clip a shape that may overflow a panel
(`<g clip-path>`), as the graph circles are.

## Add a panel kind

Write `<kind>_panel(self, p)` (the dispatcher calls `getattr(self, kind + "_panel")`; `graph_panel` is a worked case) next to `table_panel` and `board_panel`; call `self.panel_frame(p)` for the frame and
title, register every row-like box with `self.register(f"row:{p['id']}:{id}", box)` (solid, so callouts cannot
cover it), and fail with `self.fail(..., fit=True)` when the content does not fit. Add the kind to the schema enum
and its keys, and dispatch it in `render()`.

## Add a check

A check that needs only the JSON goes in `reference_errors` in `engine.py` (it returns every error with its JSON path).
A check that needs the state goes in the op or a rule (engine). A check that needs geometry goes in the renderer.
`validate.py` runs the first two; `render.py --check` and the build run the third. Keep the line: the engine, validator and
planner never import the renderer, and the renderer reads a plan only (`tests/test_stages.py` fails otherwise). A new
field is a schema change: bump the minor version in `x-version` for an optional field, the major for a breaking one.

## Keep it standard-library only

The scripts import only the Python standard library; `jsonschema` is optional. Shell out to `rsvg-convert` and
`ffmpeg` only from the driver functions (`render_all`, `make_video.py`), so the engine and the renderer stay
testable without them.
