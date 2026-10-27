#!/usr/bin/env python3
"""Stage 2, plan: turn a valid scenario into a render plan (the state of every frame) as one JSON file.

Usage: plan.py <scenario.json> -o <plan.json> [--json]

Standard library only; imports only engine.py (the data side). The plan is the interface to the renderer:

  {"plan_version": "1.0", "schema_version": "<scenario's, default 1.0>",
   "scenario": {...the scenario, unchanged...},
   "frames": [{"id", "index", "seconds", "narration"?, "state": {"rows": {entity: [row]}, "marks": {...}},
               "added": [[entity, id]], "changed": [[entity, id, field, value_before]],
               "removed": [[entity, row_snapshot, index]], "refusals": [[rule_id, entity, id, message]]}]}

`seconds` is the visual hold time (frame.seconds or meta.default_seconds). The renderer needs nothing but
this file; it does not need to know how the scenario was made. Exit 0 = plan written, 1 = the scenario is
inconsistent (the same problems validate.py reports), 2 = usage.
"""
import json
import sys
from pathlib import Path

import engine

PLAN_VERSION = "1.0"


def build_plan(sc):
    """Return the plan dict. Raises engine.ScenarioError (with .path) on the first problem."""
    errs = engine.reference_errors(sc)
    if errs:
        path, msg = errs[0]
        more = f" (and {len(errs) - 1} more)" if len(errs) > 1 else ""
        raise engine.ScenarioError(f"{path}: {msg}{more}", path=path)
    default = sc["meta"].get("default_seconds", 6)
    frames = []
    for i, (frame, state, ctx) in enumerate(engine.compute_frames(sc)):
        rec = {"id": frame["id"], "index": i, "seconds": frame.get("seconds", default),
               "state": state,
               "added": sorted([e, k] for e, k in ctx.added),
               "changed": [[e, k, f, old] for (e, k, f), old in ctx.changed.items()],
               "removed": [[e, snap, idx] for e, snap, idx in ctx.removed],
               "refusals": [list(r) for r in ctx.refusals]}
        if frame.get("narration"):
            rec["narration"] = frame["narration"]
        frames.append(rec)
    return {"plan_version": PLAN_VERSION, "schema_version": sc.get("schema_version", "1.0"),
            "scenario": sc, "frames": frames}


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    out = None
    if "-o" in argv:
        out = Path(argv[argv.index("-o") + 1])
        args = [a for a in args if a != str(out)]
    if len(args) != 1 or out is None:
        print(__doc__.split("\n\n")[1].strip(), file=sys.stderr)
        return 2
    sc = json.loads(Path(args[0]).read_text())
    try:
        plan = build_plan(sc)
    except engine.ScenarioError as e:
        print(f"PLAN ERROR: {e}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=1))
    print(f"plan: {out} ({len(plan['frames'])} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
