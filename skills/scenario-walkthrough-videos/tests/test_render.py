"""Offline tests for the transition engine, the rules, the validator and the layout checks.

Run: python3 -m unittest discover -s <skill>/tests -v
The end-to-end test builds the help-desk example; it is skipped when ffmpeg or rsvg-convert is missing.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).absolute().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))
import engine  # noqa: E402
import plan  # noqa: E402
import make_pdf  # noqa: E402
import render  # noqa: E402
import validate  # noqa: E402

EXAMPLE = SKILL / "examples" / "helpdesk" / "scenario.json"
SCHEMA = SKILL / "reference" / "scenario.schema.json"


def example():
    return json.loads(EXAMPLE.read_text())


def frame(fid, transitions, level="available", annotations=None):
    return {"id": fid, "step": "9", "title": "test", "caption": "test", "status": {"level": level},
            "sources": ["MODEL"], "transitions": transitions, "annotations": annotations or [],
            "caption_only": True}


def with_frames(*frames):
    """The example up to the first frame, plus test frames after it."""
    sc = example()
    sc["frames"] = sc["frames"][:1] + list(frames)
    return sc


def add_request(rid, **extra):
    row = {"id": rid, "summary": "test", "status": "new"}
    row.update(extra)
    return {"op": "add_row", "entity": "requests", "row": row}


class Engine(unittest.TestCase):
    def test_move_row_changes_the_foreign_key(self):
        sc = with_frames(frame("a", [add_request("R1")]),
                         frame("b", [{"op": "move_row", "entity": "requests", "id": "R1",
                                      "field": "category_id", "to": "C2"}]))
        frames = engine.compute_frames(sc)
        row = engine.find(sc, frames[-1][1], "requests", "R1")
        self.assertEqual(row["category_id"], "C2")
        self.assertEqual(frames[-1][2].changed[("requests", "R1", "category_id")], None)

    def test_move_row_where_moves_every_match(self):
        sc = with_frames(frame("a", [add_request("R1", category_id="C3"), add_request("R2", category_id="C3"),
                                     add_request("R3", category_id="C1")]),
                         frame("b", [{"op": "move_row", "entity": "requests", "where": {"category_id": "C3"},
                                      "field": "category_id", "to": "C1"}]))
        state = engine.compute_frames(sc)[-1][1]
        self.assertEqual({r["category_id"] for r in state["rows"]["requests"]}, {"C1"})

    def test_move_row_to_an_unknown_row_fails(self):
        sc = with_frames(frame("a", [add_request("R1"),
                                     {"op": "move_row", "entity": "requests", "id": "R1",
                                      "field": "category_id", "to": "C9"}]))
        with self.assertRaisesRegex(engine.ScenarioError, "no categories row"):
            engine.compute_frames(sc)

    def test_move_row_needs_a_foreign_key(self):
        sc = with_frames(frame("a", [add_request("R1"),
                                     {"op": "move_row", "entity": "requests", "id": "R1",
                                      "field": "summary", "to": "x"}]))
        with self.assertRaisesRegex(engine.ScenarioError, "not a foreign key"):
            engine.compute_frames(sc)

    def test_add_row_checks_enum_and_duplicate_key(self):
        sc = with_frames(frame("a", [add_request("R1", status="lost")]))
        with self.assertRaisesRegex(engine.ScenarioError, "not one of"):
            engine.compute_frames(sc)
        sc = with_frames(frame("a", [add_request("R1"), add_request("R1")]))
        with self.assertRaisesRegex(engine.ScenarioError, "already exists"):
            engine.compute_frames(sc)

    def test_update_row_and_set_field(self):
        sc = with_frames(frame("a", [{"op": "update_row", "entity": "categories", "id": "C2",
                                      "set": {"name": "Messaging"}},
                                     {"op": "set_field", "entity": "agents", "id": "A1",
                                      "field": "team", "value": "Tier 2"}]))
        _, state, ctx = engine.compute_frames(sc)[-1]
        self.assertEqual(engine.find(sc, state, "categories", "C2")["name"], "Messaging")
        self.assertEqual(ctx.changed[("categories", "C2", "name")], "Email")
        self.assertEqual(engine.find(sc, state, "agents", "A1")["team"], "Tier 2")

    def test_delete_row_refuses_while_referenced(self):
        sc = with_frames(frame("a", [add_request("R1", category_id="C3"),
                                     {"op": "delete_row", "entity": "categories", "id": "C3"}]))
        with self.assertRaisesRegex(engine.ScenarioError, "foreign-key"):
            engine.compute_frames(sc)

    def test_delete_row_keeps_a_ghost_for_the_frame(self):
        sc = with_frames(frame("a", [{"op": "delete_row", "entity": "categories", "id": "C4"}]))
        _, state, ctx = engine.compute_frames(sc)[-1]
        self.assertIsNone(engine.find(sc, state, "categories", "C4", required=False))
        self.assertEqual(ctx.removed[0][1]["name"], "Hardware")

    def test_state_flows_from_frame_to_frame(self):
        sc = with_frames(frame("a", [add_request("R1")]), frame("b", []))
        frames = engine.compute_frames(sc)
        self.assertEqual(len(frames[-1][1]["rows"]["requests"]), 1)
        self.assertFalse(frames[-1][2].added)

    def test_unbuilt_level_marks_the_touched_rows(self):
        sc = with_frames(frame("a", [add_request("R1")], level="planned"))
        state = engine.compute_frames(sc)[-1][1]
        self.assertEqual(state["marks"], {"requests:R1": "planned"})


class Rules(unittest.TestCase):
    def test_declared_rule_refuses_a_transition(self):
        sc = with_frames(frame("a", [add_request("R1"),
                                     {"op": "move_row", "entity": "requests", "id": "R1",
                                      "field": "agent_id", "to": "A1"}]))
        with self.assertRaisesRegex(engine.ScenarioError, "assign-needs-category"):
            engine.compute_frames(sc)

    def test_count_condition(self):
        base = [add_request("R1", category_id="C1", agent_id="A1")]
        to_tier1 = {"op": "update_row", "entity": "requests", "id": "R1", "set": {"status": "escalated"}}
        with self.assertRaisesRegex(engine.ScenarioError, "escalate-needs-tier-2"):
            engine.compute_frames(with_frames(frame("a", base + [to_tier1])))
        to_tier2 = {"op": "move_row", "entity": "requests", "id": "R1", "field": "agent_id", "to": "A3",
                    "set": {"status": "escalated"}}
        state = engine.compute_frames(with_frames(frame("a", base + [to_tier2])))[-1][1]
        self.assertEqual(engine.find(example(), state, "requests", "R1")["status"], "escalated")

    def test_expect_refused_leaves_the_state_untouched(self):
        sc = with_frames(frame("a", [add_request("R1"),
                                     {"op": "move_row", "entity": "requests", "id": "R1", "field": "agent_id",
                                      "to": "A1", "expect_refused": "assign-needs-category"}]))
        _, state, ctx = engine.compute_frames(sc)[-1]
        self.assertIsNone(state["rows"]["requests"][0]["agent_id"])
        self.assertEqual(ctx.refusals[0][0], "assign-needs-category")

    def test_expect_refused_fails_when_the_rule_lets_it_through(self):
        sc = with_frames(frame("a", [add_request("R1", category_id="C1"),
                                     {"op": "move_row", "entity": "requests", "id": "R1", "field": "agent_id",
                                      "to": "A1", "expect_refused": "assign-needs-category"}]))
        with self.assertRaisesRegex(engine.ScenarioError, "went through"):
            engine.compute_frames(sc)

    def test_rule_with_field_ignores_other_fields(self):
        sc = with_frames(frame("a", [add_request("R1"),
                                     {"op": "set_field", "entity": "requests", "id": "R1",
                                      "field": "summary", "value": "new text"}]))
        engine.compute_frames(sc)   # assign-needs-category watches agent_id only


class Layout(unittest.TestCase):
    def test_example_renders(self):
        svgs = render.render_svgs(example())
        self.assertEqual(len(svgs), 10)
        self.assertTrue(all(s.startswith("<svg") for _, s in svgs))

    def test_overflow_fails_loudly(self):
        sc = example()
        sc["frames"][1]["transitions"][0]["row"]["summary"] = "A summary far too long for the column it sits in"
        with self.assertRaisesRegex(render.FitError, "frame 'arrive'.*needs"):
            render.render_svgs(sc)

    def test_too_many_rows_fail(self):
        sc = with_frames(frame("a", [add_request(f"R{i}") for i in range(1, 13)]))
        with self.assertRaisesRegex(render.FitError, "holds 11 rows"):
            render.render_svgs(sc)

    def test_manual_note_overlap_fails(self):
        sc = example()
        note = sc["frames"][4]["annotations"][0]
        note.update(x=60, y=250, w=460)
        with self.assertRaisesRegex(render.ScenarioError, "covers row:requests"):
            render.render_svgs(sc)
        note["allow_overlap"] = True
        render.render_svgs(sc)

    def test_unknown_target_fails(self):
        sc = example()
        sc["frames"][1]["annotations"][0]["to"] = "lane:status:lost"
        with self.assertRaisesRegex(render.ScenarioError, "not on the frame"):
            render.render_svgs(sc)

    def test_before_after_needs_a_change(self):
        sc = example()
        sc["frames"][5]["transitions"] = []
        with self.assertRaisesRegex(render.ScenarioError, "did not change"):
            render.render_svgs(sc)

    def test_overlapping_panels_fail(self):
        sc = example()
        sc["layout"]["panels"][1]["region"] = [24, 400, 860, 424]
        self.assertRegex(refs(sc), "overlap")

    def test_frame_needs_a_source(self):
        sc = example()
        sc["frames"][1]["sources"] = []
        self.assertRegex(refs(sc), "cite at least one source")


def plan_record(f, state, ctx):
    """The plan's record of a frame (what plan.py writes), for the renderer's Ctx."""
    return {"added": [list(a) for a in ctx.added], "changed": [[e, k, fl, old] for (e, k, fl), old in ctx.changed.items()],
            "removed": [list(r) for r in ctx.removed], "refusals": [list(r) for r in ctx.refusals]}


def refs(sc):
    """The reference problems of a scenario as one string (empty when consistent)."""
    return "; ".join(f"{p}: {m}" for p, m in engine.reference_errors(sc))


def frame_renderer(sc, fid):
    """The FrameRenderer of frame fid after render(), to read where things were placed."""
    frames = engine.compute_frames(sc)
    for i, (f, state, ctx) in enumerate(frames):
        if f["id"] == fid:
            fr = render.FrameRenderer(sc, f, i, len(frames), state, render.Ctx(plan_record(f, state, ctx)))
            svg = fr.render()
            return fr, svg
    raise KeyError(fid)


def inside(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


class Notes(unittest.TestCase):
    """The on-image explanation: a note with a target is placed next to it, with an arrow."""

    def test_note_is_auto_placed_in_free_space(self):
        fr, _ = frame_renderer(example(), "triage")
        boxes = [b for k, b in fr.anchors.items() if k.startswith("callout:")]
        self.assertTrue(boxes)
        for b in boxes:
            for key, other in fr.solid:
                if not key.startswith("callout:"):
                    self.assertFalse(inside(b, other), f"{b} covers {key}")
            self.assertGreaterEqual(b[1], fr.top)
            self.assertLessEqual(b[1] + b[3], fr.bottom)

    def test_note_sits_near_its_target_and_not_on_it(self):
        fr, _ = frame_renderer(example(), "refuse")
        nid = next(k for k in fr.anchors if k.startswith("callout:"))
        note, target = fr.anchors[nid], fr.anchors["row:requests:R5"]
        self.assertFalse(inside(note, target))
        gap = max(target[1] - (note[1] + note[3]), note[1] - (target[1] + target[3]), 0)
        self.assertLess(gap, 60)

    def test_notes_do_not_overlap_each_other(self):
        sc = example()
        sc["frames"][1]["annotations"] += [
            {"type": "note", "target": "lane:status:triaged", "text": "A second note on the same frame.", "style": "info"},
            {"type": "note", "target": "panel:agents", "text": "A third note on the same frame.", "style": "info"}]
        fr, _ = frame_renderer(sc, "arrive")
        boxes = [b for k, b in fr.anchors.items() if k.startswith("callout:")]
        self.assertEqual(len(boxes), 3)
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                self.assertFalse(inside(a, b))

    def test_note_draws_an_arrow_and_a_highlight(self):
        sc = example()
        plain = frame_renderer(sc, "refuse")[1]
        sc["frames"][4]["annotations"][0]["highlight"] = False
        no_mark = frame_renderer(sc, "refuse")[1]
        self.assertIn("marker-end", plain)
        self.assertGreater(plain.count("<rect"), no_mark.count("<rect"))      # the highlight box
        self.assertEqual(plain.count("marker-end"), no_mark.count("marker-end"))   # the arrow stays

    def test_note_with_no_free_space_fails(self):
        sc = example()
        sc["frames"][4]["annotations"][0].update(w=1860, max_lines=10,
                                                  text=" ".join(["words"] * 250))
        sc["frames"][4]["annotations"][0].pop("title", None)
        with self.assertRaisesRegex(render.ScenarioError, "no free space"):
            render.render_svgs(sc)

    def test_unknown_note_target_fails(self):
        sc = example()
        sc["frames"][4]["annotations"][0]["target"] = "row:requests:R99"
        with self.assertRaisesRegex(render.ScenarioError, "not on the frame"):
            render.render_svgs(sc)

    def test_frame_with_only_a_caption_is_refused(self):
        sc = example()
        sc["frames"][1]["annotations"] = [a for a in sc["frames"][1]["annotations"] if a["type"] != "note"]
        self.assertRegex(refs(sc), "no on-image explanation")
        sc["frames"][1]["caption_only"] = True            # the explicit opt-out
        self.assertEqual(refs(sc), "")

    def test_summary_frame_needs_no_note(self):
        self.assertEqual(refs(example()), "")                # the last frame is a summary with no note

    def test_caption_is_one_line(self):
        sc = example()
        sc["frames"][1]["caption"] = "A long caption " * 12
        with self.assertRaisesRegex(render.FitError, "caption"):
            render.render_svgs(sc)


def graph_scenario():
    """Four points, a circle around one, three edges (one a self-edge): the smallest scene for the graph panel."""
    import scenario_builder as b
    ents = {"points": b.entity("Points", {"id": {"nullable": False}, "x": {}, "y": {}, "r": {},
                                          "grp": {"enum": ["base", "noise"]}}, ["S"]),
            "edges": b.entity("Edges", {"id": {"nullable": False}, "a": {"ref": "points"}, "b": {"ref": "points"},
                                        "w": {}, "st": {"enum": ["faint", "on"]}}, ["S"])}
    panels = [b.graph_panel("g", "points", [24, 96, 1000, 900], "Points", "edges", "x", "y",
                            {"x": [0, 4], "y": [0, 3]}, radius_field="r", point_color_field="grp",
                            color_map="grp", edge_weight="w", edge_state="st",
                            edge_styles={"on": {"color": "#1F8A4C", "width": 5, "label": True}}),
              b.table_panel("e", "edges", [1040, 96, 856, 500], "Edges",
                            [b.column("id", 120), b.column("w", 120), b.column("st", 120)])]
    style = dict(b.DEFAULT_STYLE)
    style["color_maps"] = {"grp": {"base": "#3B5B92", "noise": "#9AA3B2"}}
    pts = [{"id": p, "x": x, "y": y, "r": None, "grp": "base"} for p, x, y in
           (("A", 0, 0), ("B", 1, 0), ("C", 3, 2), ("D", 2, 1))]
    f1 = b.frame("one", "1", "Points", "Four points", "info", [], panels=["g"],
                 notes=[b.note("row:g:C", "C is far from the rest.")])
    f2 = b.frame("two", "2", "Circle and edges", "A circle and two edges", "available", ["S"],
                 transitions=[b.update_row("points", "A", r=1.0),
                              b.add_row("edges", id="A-B", a="A", b="B", w=1.0, st="on"),
                              b.add_row("edges", id="A-A", a="A", b="A", w=1.0, st="on")],
                 notes=[b.note("edge:g:A-B", "An edge with its weight."), b.note("circle:g:A", "A radius.")])
    f3 = b.frame("three", "3", "Remove an edge", "A removed edge is dashed", "available", ["S"],
                 transitions=[b.delete_row("edges", "A-B") if hasattr(b, "delete_row")
                              else {"op": "delete_row", "entity": "edges", "id": "A-B"},
                              b.update_row("points", "C", grp="noise")],
                 notes=[b.note("row:g:C", "C is now noise.")])
    return b.scenario("graph test", [b.source("S", "test", kind="example")], ents, panels, [f1, f2, f3],
                      initial_state={"points": pts, "edges": []}, style=style)


class GraphPanel(unittest.TestCase):
    def test_graph_renders_points_circles_edges_and_a_ghost(self):
        svgs = {f["id"]: svg for f, svg in render.render_svgs(graph_scenario())}
        self.assertIn("clip-path", svgs["two"])                       # the circle, clipped to the plot
        self.assertIn("<line", svgs["two"])                           # an edge
        self.assertIn("r 1.000", svgs["two"])                         # the circle's radius label
        self.assertIn('stroke-dasharray="8 6"', svgs["three"])        # the edge deleted in this frame
        self.assertNotIn('stroke-dasharray="8 6"', svgs["two"])

    def test_graph_scenario_is_valid(self):
        res = validate.validate(graph_scenario(), json.loads(SCHEMA.read_text()))
        self.assertTrue(res["ok"], res["errors"])
        self.assertTrue(validate.validate(graph_scenario(), json.loads(SCHEMA.read_text()), force_mini=True)["ok"])

    def test_note_is_not_placed_on_the_plot(self):
        fr, _ = frame_renderer(graph_scenario(), "two")
        plot = fr.anchors["plot:g"]
        for nid, _ in fr.notes:
            nx, ny, nw, nh = fr.anchors[f"callout:{nid}"]
            px, py, pw, ph = plot
            self.assertFalse(nx < px + pw and px < nx + nw and ny < py + ph and py < ny + nh)

    def test_edge_field_must_be_a_ref(self):
        sc = graph_scenario()
        sc["entities"]["edges"]["fields"]["a"] = {}
        self.assertRegex(refs(sc), "must be a ref to points")

    def test_unknown_graph_fields_are_reported(self):
        sc = graph_scenario()
        sc["layout"]["panels"][0]["x_field"] = "nope"
        sc["layout"]["panels"][0]["edge_weight"] = "nope2"
        self.assertRegex(refs(sc), "'nope' is not a field of points")
        self.assertRegex(refs(sc), "'nope2' is not a field of edges")

    def test_point_outside_the_domain_fails(self):
        sc = graph_scenario()
        sc["initial_state"]["points"][0]["x"] = 9
        with self.assertRaisesRegex(render.ScenarioError, "outside the domain"):
            render.render_svgs(sc)

    def test_graph_in_an_older_schema_version_warns(self):
        sc = graph_scenario()
        sc["schema_version"] = "1.1"
        res = validate.validate(sc, json.loads(SCHEMA.read_text()))
        self.assertTrue(any("graph panel is a 1.2 feature" in w["message"] for w in res["warnings"]), res)

    def test_graph_needs_its_required_keys(self):
        sc = graph_scenario()
        del sc["layout"]["panels"][0]["domain"]
        errs = validate.schema_errors(sc, json.loads(SCHEMA.read_text()), force_mini=True)
        self.assertTrue(any("domain" in m for _, m in errs), errs)


class Validator(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA.read_text())

    def test_example_is_valid_with_both_validators(self):
        sc = example()
        self.assertEqual(validate.schema_errors(sc, self.schema, force_mini=True), [])
        self.assertEqual(validate.schema_errors(sc, self.schema), [])
        self.assertEqual(validate.semantic_errors(sc), [])

    def test_mini_validator_catches_a_bad_op_and_a_missing_field(self):
        sc = example()
        sc["frames"][1]["transitions"][0]["op"] = "teleport_row"
        del sc["frames"][1]["caption"]
        errs = validate.schema_errors(sc, self.schema, force_mini=True)
        self.assertTrue(any("teleport_row" in m for _, m in errs), errs)
        self.assertTrue(any("caption" in m for _, m in errs), errs)

    def test_mini_validator_checks_op_specific_fields(self):
        sc = example()
        del sc["frames"][2]["transitions"][0]["to"]
        errs = validate.schema_errors(sc, self.schema, force_mini=True)
        self.assertTrue(any("'to'" in m for _, m in errs), errs)

    def test_cli_exit_codes(self):
        ok = subprocess.run([sys.executable, str(SKILL / "scripts" / "validate.py"), str(EXAMPLE), "--mini"],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            sc = example()
            sc["frames"][4]["transitions"][0].pop("expect_refused")
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps(sc))
            res = subprocess.run([sys.executable, str(SKILL / "scripts" / "validate.py"), str(bad)],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 1)
            self.assertIn("assign-needs-category", res.stderr)


def tools_present():
    return all(shutil.which(t) or os.path.exists(f"/opt/homebrew/bin/{t}") for t in ("ffmpeg", "rsvg-convert"))


class TopMargin(unittest.TestCase):
    """The top strip is blank: the Mac player overlays window buttons and the title there (schema 1.3)."""

    def drawn(self, sc):
        return render.render_svgs(sc)[0][1]

    def test_default_is_64_per_720_of_height(self):
        sc = example()
        self.assertEqual(sc["meta"]["height"], 1080)
        self.assertEqual(render.top_margin(sc["meta"]), 48)
        sc["meta"]["height"], sc["meta"]["width"] = 720, 1280
        self.assertEqual(render.top_margin(sc["meta"]), 32)

    def test_old_scenario_gets_the_margin(self):
        sc = example()
        sc.pop("schema_version", None)
        svg = self.drawn(sc)
        self.assertIn('translate(', svg)
        self.assertRegex(svg, r'<g id="content" transform="translate\([\d.]+,48\) scale\(0\.9555')

    def test_override_and_zero(self):
        sc = example()
        sc["meta"].setdefault("layout", {})["top_margin"] = 120
        self.assertIn(",120) scale(", self.drawn(sc))
        sc["meta"]["layout"]["top_margin"] = 0
        self.assertIn(",0) scale(1.000000)", self.drawn(sc))

    def test_every_drawn_element_is_below_the_strip(self):
        """Parse the final SVG: each element of the content group, after the transform, starts at or below the margin."""
        import re
        sc = example()
        svg = self.drawn(sc)
        m = re.search(r'translate\(([\d.]+),(\d+)\) scale\(([\d.]+)\)', svg)
        margin, k = int(m.group(2)), float(m.group(3))
        body = svg[svg.index('<g id="content"'):]
        ys = [float(v) for v in re.findall(r'<(?:rect|text) x="[^"]*" y="(-?[\d.]+)"', body)]
        self.assertGreaterEqual(min(ys), 0)
        self.assertGreaterEqual(margin + k * min(ys), margin)
        self.assertAlmostEqual(margin + k * 1080, 1080, places=3)      # the bottom stays at the frame bottom

    def test_check_fails_for_an_element_in_the_strip(self):
        s = render.Svg(render.Fonts({}), "f")
        s.rect(0, 0, 100, 40, fill="#000")
        s.render(1920, 1080, margin=48)                                  # fine at y = 0
        s.rect(10, -5, 100, 20, fill="#000")
        with self.assertRaisesRegex(render.ScenarioError, "top 48px strip"):
            s.render(1920, 1080, margin=48)
        for add in ('<text x="5" y="8" font-family="x" font-size="20">t</text>', '<circle cx="5" cy="3" r="9" fill="#000"/>',
                    '<path d="M1,2 C3,-9 4,5 6,7" fill="none"/>', '<line x1="0" y1="-3" x2="9" y2="9"/>'):
            t = render.Svg(render.Fonts({}), "f")
            t.add(add)
            with self.assertRaises(render.ScenarioError, msg=add):
                t.render(1920, 1080, margin=48)

    def test_a_note_cannot_be_placed_in_the_strip(self):
        sc = example()
        fr, _ = frame_renderer(sc, sc["frames"][0]["id"])
        self.assertFalse(fr.free(100, -20, 200, 60))
        self.assertGreaterEqual(fr.top, 48)

    def test_validator_warns_below_the_floor(self):
        schema = json.loads(SCHEMA.read_text())
        sc = example()
        sc["schema_version"] = "1.3"
        sc["meta"].setdefault("layout", {})["top_margin"] = 30       # 1080 high: the floor is 36
        res = validate.validate(sc, schema)
        self.assertTrue(res["ok"], res)
        self.assertTrue(any("below 36 px" in w["message"] for w in res["warnings"]), res)
        sc["meta"]["layout"]["top_margin"] = 36
        self.assertFalse(any("top_margin" in w["path"] for w in validate.validate(sc, schema)["warnings"]))
        sc["schema_version"] = "1.2"
        self.assertTrue(any("1.3 feature" in w["message"] for w in validate.validate(sc, schema)["warnings"]))
        sc["schema_version"] = "1.3"
        sc["meta"]["layout"]["top_margin"] = 301
        self.assertFalse(validate.validate(sc, schema)["ok"])

    def test_clipped_shapes_may_overflow_their_panel(self):
        s = render.Svg(render.Fonts({}), "f")
        s.add('<g clip-path="url(#c)"><circle cx="50" cy="20" r="90" fill="#000"/></g>')
        self.assertIn("<g", s.render(1920, 1080, margin=48))

    def test_graph_panel_draws_inside_the_content_group(self):
        svg = self.drawn(graph_scenario())
        self.assertIn('<g id="content"', svg)


@unittest.skipUnless(tools_present(), "ffmpeg or rsvg-convert is missing")
class Slides(unittest.TestCase):
    def test_pdf_has_one_page_per_frame_in_both_methods(self):
        with tempfile.TemporaryDirectory() as tmp:
            sc = example()
            Path(tmp, "s.json").write_text(json.dumps(sc))
            p = plan.build_plan(sc)
            n = len(render.render_plan(p, tmp))
            for vector in (True, False):
                pdf, pages, how = make_pdf.make_pdf(tmp, vector=vector)
                self.assertEqual(pages, n, how)
                self.assertTrue(pdf.stat().st_size > 5_000)
                if make_pdf.page_count(pdf) is not None:
                    self.assertEqual(make_pdf.page_count(pdf), n)
            self.assertIn("vector", make_pdf.make_pdf(tmp, vector=True)[2])


@unittest.skipUnless(tools_present(), "ffmpeg or rsvg-convert is missing")
class EndToEnd(unittest.TestCase):
    def test_build_the_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = subprocess.run(["bash", str(SKILL / "scripts" / "build.sh"), str(EXAMPLE), tmp],
                                 capture_output=True, text=True, timeout=600)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertTrue((Path(tmp) / "helpdesk-walkthrough.mp4").stat().st_size > 100_000)
            self.assertTrue((Path(tmp) / "contact_sheet.png").exists())
            self.assertEqual(len(list((Path(tmp) / "png").glob("*.png"))), 10)
            pdf = Path(tmp) / "helpdesk-walkthrough.pdf"            # the slides: one frame per page, always made
            self.assertTrue(pdf.exists())
            self.assertEqual(len(list((Path(tmp) / "svg").glob("*.svg"))), 10)
            if make_pdf.page_count(pdf) is not None:
                self.assertEqual(make_pdf.page_count(pdf), 10)
            self.assertFalse(list(Path(tmp).glob("*silent*")))       # no silent copy is made on the side


if __name__ == "__main__":
    unittest.main()
