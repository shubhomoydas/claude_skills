#!/usr/bin/env python3
"""The transition engine and the reference checks of a scenario: the DATA side of the walk-through skill.

Standard library only. Imports nothing from the renderer or the video code. It knows the JSON contract
(reference/scenario.schema.json) and the meaning of the ops and rules; it knows nothing of pixels.

  compute_frames(sc)   -> [(frame, state_after, ctx)]  applies every transition in order, checking fields,
                          foreign keys and rules (raises ScenarioError with a JSON path)
  reference_errors(sc) -> [(json_path, message)]       ids, sources, styles, panels, regions, notes: all of them

validate.py and plan.py are the only callers.
"""
import copy

OPS = ("add_row", "update_row", "set_field", "move_row", "delete_row")
EXPLAINING = ("note", "before_after")      # annotation types that count as an on-image explanation


class ScenarioError(Exception):
    """The JSON is inconsistent, or it asks for something the scenario's own rules refuse."""

    def __init__(self, message, path=None):
        super().__init__(message)
        self.message = message
        self.path = path


class RuleRefusal(ScenarioError):
    """A declared rule (or a foreign key) refuses a transition."""

    def __init__(self, rule_id, message):
        super().__init__(f"refused by rule '{rule_id}': {message}")
        self.rule_id = rule_id
        self.message = message


# =============================================================================
# 1. THE TRANSITION ENGINE
# =============================================================================

class Ctx:
    """What one frame changed. The renderer marks these."""

    def __init__(self):
        self.added = set()       # (entity, id)
        self.changed = {}        # (entity, id, field) -> value before the frame
        self.removed = []        # (entity, snapshot, index in the entity's rows)
        self.refusals = []       # (rule_id, entity, id, message)


def key_field(sc, entity):
    return sc["entities"][entity].get("key", "id")


def fields_of(sc, entity):
    return sc["entities"][entity]["fields"]


def rows(state, entity):
    try:
        return state["rows"][entity]
    except KeyError:
        raise ScenarioError(f"unknown entity '{entity}'") from None


def find(sc, state, entity, ident, required=True):
    k = key_field(sc, entity)
    for r in rows(state, entity):
        if r[k] == ident:
            return r
    if required:
        raise ScenarioError(f"no {entity} row with {k} = {ident!r}")
    return None


def check_value(sc, state, entity, field, value):
    spec = fields_of(sc, entity).get(field)
    if spec is None:
        raise ScenarioError(f"{entity} has no field '{field}'; fields: {sorted(fields_of(sc, entity))}")
    if value is None:
        if spec.get("nullable", True) is False:
            raise ScenarioError(f"{entity}.{field} may not be null")
        return
    if "enum" in spec and value not in spec["enum"]:
        raise ScenarioError(f"{entity}.{field} = {value!r} is not one of {spec['enum']}")
    if "ref" in spec and find(sc, state, spec["ref"], value, required=False) is None:
        raise ScenarioError(f"{entity}.{field} = {value!r}: no such row in {spec['ref']}")


def new_state(sc):
    state = {"rows": {}, "marks": {}}
    for entity, spec in sc["entities"].items():
        state["rows"][entity] = []
        for r in sc["initial_state"].get(entity, []):
            unknown = set(r) - set(spec["fields"])
            if unknown:
                raise ScenarioError(f"initial_state.{entity}: unknown fields {sorted(unknown)}")
            state["rows"][entity].append({f: r.get(f) for f in spec["fields"]})
    for entity in sc["initial_state"]:
        if entity not in sc["entities"]:
            raise ScenarioError(f"initial_state names an unknown entity '{entity}'")
    for entity in sc["entities"]:
        k = key_field(sc, entity)
        seen = set()
        for r in rows(state, entity):
            if r[k] is None or r[k] in seen:
                raise ScenarioError(f"initial_state.{entity}: key {r[k]!r} is missing or repeated")
            seen.add(r[k])
            for f, v in r.items():
                check_value(sc, state, entity, f, v)
    return state


# ---- rules -----------------------------------------------------------------

_CMP = {
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
    "in": lambda a, b: a in b, "not_in": lambda a, b: a not in b,
    "gt": lambda a, b: a is not None and a > b, "ge": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b, "le": lambda a, b: a is not None and a <= b,
    "is_null": lambda a, b: (a is None) == bool(b),
}


def _scope_value(value, scopes):
    """'$row.id' -> the id of the row in scope 'row'. Other values pass through."""
    if isinstance(value, str) and value.startswith("$"):
        scope, _, field = value[1:].partition(".")
        row = scopes.get(scope)
        return None if row is None else row.get(field)
    return value


def _compare(cond, actual, where):
    ops = [k for k in cond if k in _CMP]
    if len(ops) != 1:
        raise ScenarioError(f"{where}: a condition needs exactly one of {sorted(_CMP)}, got {sorted(cond)}")
    return _CMP[ops[0]](actual, cond[ops[0]])


def eval_cond(sc, state, cond, scopes, where="rule"):
    """Evaluate a rule condition. See reference/scenario-format.md, section Rules."""
    if "all" in cond:
        return all(eval_cond(sc, state, c, scopes, where) for c in cond["all"])
    if "any" in cond:
        return any(eval_cond(sc, state, c, scopes, where) for c in cond["any"])
    if "not" in cond:
        return not eval_cond(sc, state, cond["not"], scopes, where)
    if "field" in cond:
        scope, _, field = cond["field"].partition(".")
        if scope not in ("row", "new", "to"):
            raise ScenarioError(f"{where}: field '{cond['field']}' must start with row., new. or to.")
        row = scopes.get(scope)
        return _compare(cond, None if row is None else row.get(field), where)
    if "count" in cond:
        spec = cond["count"]
        where_ = {f: _scope_value(v, scopes) for f, v in spec.get("where", {}).items()}
        n = sum(1 for r in rows(state, spec["entity"]) if all(r.get(f) == v for f, v in where_.items()))
        return _compare(cond, n, where)
    raise ScenarioError(f"{where}: unknown condition {cond}")


def check_rules(sc, state, op, entity, scopes):
    """Raise RuleRefusal when a declared rule refuses this transition."""
    rule_op = "update_row" if op == "set_field" else op
    for rule in sc.get("rules", []):
        ops = rule["op"] if isinstance(rule["op"], list) else [rule["op"]]
        if rule_op not in ops or rule["entity"] != entity:
            continue
        if "field" in rule and rule["field"] not in scopes.get("_fields", ()):
            continue
        if eval_cond(sc, state, rule["when"], scopes, where=f"rule '{rule['id']}'"):
            raise RuleRefusal(rule["id"], rule["message"])


# ---- ops -------------------------------------------------------------------

def _touch(ctx, entity, ident, field, old):
    ctx.changed.setdefault((entity, ident, field), old)


def op_add_row(sc, state, ctx, t):
    e = t["entity"]
    k = key_field(sc, e)
    row = {f: t["row"].get(f) for f in fields_of(sc, e)}
    unknown = set(t["row"]) - set(fields_of(sc, e))
    if unknown:
        raise ScenarioError(f"{e} has no fields {sorted(unknown)}")
    if row[k] is None:
        raise ScenarioError(f"add_row: the new {e} row has no {k}")
    if find(sc, state, e, row[k], required=False) is not None:
        raise ScenarioError(f"add_row: {e} {row[k]!r} already exists")
    for f, v in row.items():
        check_value(sc, state, e, f, v)
    check_rules(sc, state, "add_row", e, {"row": row, "new": row, "_fields": set(t["row"])})
    rows(state, e).append(row)
    ctx.added.add((e, row[k]))
    return [(e, row[k])]


def op_update_row(sc, state, ctx, t, op="update_row"):
    e = t["entity"]
    row = find(sc, state, e, t["id"])
    changes = t["set"] if op == "update_row" else {t["field"]: t["value"]}
    if key_field(sc, e) in changes:
        raise ScenarioError(f"{op}: the key of {e} cannot change; add a row and delete the old one")
    for f, v in changes.items():
        check_value(sc, state, e, f, v)
    new = dict(row, **changes)
    check_rules(sc, state, op, e, {"row": row, "new": new, "_fields": set(changes)})
    for f, v in changes.items():
        if row[f] != v:
            _touch(ctx, e, t["id"], f, row[f])
            row[f] = v
    return [(e, t["id"])]


def op_set_field(sc, state, ctx, t):
    return op_update_row(sc, state, ctx, t, op="set_field")


def op_move_row(sc, state, ctx, t):
    """Change a foreign key: point one or more rows at another row of the referenced entity."""
    e, field = t["entity"], t["field"]
    spec = fields_of(sc, e).get(field)
    if spec is None or "ref" not in spec:
        raise ScenarioError(f"move_row: {e}.{field} is not a foreign key (declare \"ref\")")
    if "where" in t:
        targets = [r for r in rows(state, e) if all(r.get(f) == v for f, v in t["where"].items())]
        if not targets:
            raise ScenarioError(f"move_row: no {e} row matches {t['where']}")
    else:
        ids = t["ids"] if "ids" in t else [t["id"]]
        targets = [find(sc, state, e, i) for i in ids]
    dest = None if t["to"] is None else find(sc, state, spec["ref"], t["to"])
    check_value(sc, state, e, field, t["to"])
    extra = t.get("set", {})
    for f, v in extra.items():
        check_value(sc, state, e, f, v)
    for row in targets:            # check every row first, so a refusal leaves the state untouched
        new = dict(row, **{field: t["to"]}, **extra)
        check_rules(sc, state, "move_row", e, {"row": row, "new": new, "to": dest,
                                               "_fields": {field, *extra}})
    k = key_field(sc, e)
    for row in targets:
        for f, v in {field: t["to"], **extra}.items():
            if row[f] != v:
                _touch(ctx, e, row[k], f, row[f])
                row[f] = v
    return [(e, r[k]) for r in targets]


def op_delete_row(sc, state, ctx, t):
    e = t["entity"]
    row = find(sc, state, e, t["id"])
    for other, spec in sc["entities"].items():
        for f, fs in spec["fields"].items():
            if fs.get("ref") == e and any(r[f] == t["id"] for r in rows(state, other)):
                raise RuleRefusal("foreign-key", f"{other}.{f} still points at {e} {t['id']!r}")
    check_rules(sc, state, "delete_row", e, {"row": row, "_fields": set()})
    index = rows(state, e).index(row)
    rows(state, e).remove(row)
    ctx.removed.append((e, copy.deepcopy(row), index))
    state["marks"].pop(f"{e}:{t['id']}", None)
    return []


OP_FUNCS = {"add_row": op_add_row, "update_row": op_update_row, "set_field": op_set_field,
            "move_row": op_move_row, "delete_row": op_delete_row}


def apply_transition(sc, state, ctx, t):
    fn = OP_FUNCS.get(t.get("op"))
    if fn is None:
        raise ScenarioError(f"unknown op '{t.get('op')}'; known: {sorted(OP_FUNCS)}")
    expected = t.get("expect_refused")
    try:
        touched = fn(sc, state, ctx, t)
    except RuleRefusal as r:
        if expected and r.rule_id == expected:
            ident = t.get("id") or (t.get("ids") or [None])[0] or (t.get("row") or {}).get(key_field(sc, t["entity"]))
            ctx.refusals.append((r.rule_id, t["entity"], ident, r.message))
            return []
        raise
    if expected:
        raise ScenarioError(f"expected rule '{expected}' to refuse this transition, but it went through")
    return touched


def compute_frames(sc):
    """Return [(frame, state_after, ctx)] for every frame, applying transitions in order."""
    state = new_state(sc)
    badges = sc["style"]["badges"]
    out = []
    for fi, frame in enumerate(sc["frames"]):
        ctx = Ctx()
        touched = []
        for i, t in enumerate(frame.get("transitions", [])):
            try:
                touched += apply_transition(sc, state, ctx, t)
            except ScenarioError as e:
                raise ScenarioError(f"frame '{frame['id']}' transition {i} ({t.get('op')}): {e}",
                                    path=f"$.frames[{fi}].transitions[{i}]") from None
        level = frame["status"]["level"]
        if badges.get(level, {}).get("unbuilt"):
            for e, ident in touched:
                state["marks"][f"{e}:{ident}"] = level
        out.append((frame, copy.deepcopy(state), ctx))
    return out


def state_after(sc, frame_id):
    for frame, state, _ in compute_frames(sc):
        if frame["id"] == frame_id:
            return state
    raise ScenarioError(f"no frame '{frame_id}'")


# =============================================================================
# 2. REFERENCE CHECKS (what the JSON Schema cannot check)
# =============================================================================

def content_box(sc):
    m = sc["meta"]
    lay = m.get("layout", {})
    top = lay.get("title_height", 84) + 12
    bottom = m["height"] - lay.get("caption_height", 72) - 8
    return top, bottom


def reference_errors(sc):
    """Every reference problem the JSON Schema cannot see, as [(json_path, message)]. Empty means consistent."""
    errs = []
    style = sc["style"]
    src_ids = {s["id"] for s in sc["sources"]}
    ents = sc["entities"]
    seen_src = set()
    for i, s in enumerate(sc["sources"]):
        if s["id"] in seen_src:
            errs.append((f"$.sources[{i}].id", f"duplicate source id '{s['id']}'"))
        seen_src.add(s["id"])
    for name, ent in ents.items():
        p = f"$.entities.{name}"
        if ent.get("key", "id") not in ent["fields"]:
            errs.append((f"{p}.key", f"key '{ent.get('key', 'id')}' is not a field"))
        if not ent.get("sources"):
            errs.append((f"{p}.sources", "cite at least one source"))
        for j, s in enumerate(ent.get("sources", [])):
            if s not in src_ids:
                errs.append((f"{p}.sources[{j}]", f"unknown source '{s}'"))
        for f, fs in ent["fields"].items():
            if "ref" in fs and fs["ref"] not in ents:
                errs.append((f"{p}.fields.{f}.ref", f"ref to unknown entity '{fs['ref']}'"))
    rule_ids = set()
    for i, rule in enumerate(sc.get("rules", [])):
        p = f"$.rules[{i}]"
        if rule["id"] in rule_ids:
            errs.append((f"{p}.id", f"duplicate rule id '{rule['id']}'"))
        rule_ids.add(rule["id"])
        if rule["entity"] not in ents:
            errs.append((f"{p}.entity", f"unknown entity '{rule['entity']}'"))
        for j, s in enumerate(rule.get("sources", [])):
            if s not in src_ids:
                errs.append((f"{p}.sources[{j}]", f"unknown source '{s}'"))
    top, bottom = content_box(sc)
    W = sc["meta"]["width"]
    panels = sc["layout"]["panels"]
    pids = set()
    for pi, p in enumerate(panels):
        pp = f"$.layout.panels[{pi}]"
        if p["id"] in pids:
            errs.append((f"{pp}.id", f"duplicate panel id '{p['id']}'"))
        pids.add(p["id"])
        if p["entity"] not in ents:
            errs.append((f"{pp}.entity", f"panel '{p['id']}': unknown entity '{p['entity']}'"))
            continue
        x, y, w, h = p["region"]
        if x < 0 or y < top or x + w > W or y + h > bottom:
            errs.append((f"{pp}.region", f"panel '{p['id']}' region {p['region']} leaves the content area "
                         f"(x 0..{W}, y {top}..{bottom})"))
        fields = ents[p["entity"]]["fields"]
        cols = p.get("columns", [])
        for ci, c in enumerate(cols):
            if c["field"] not in fields:
                errs.append((f"{pp}.columns[{ci}].field", f"unknown field '{c['field']}'"))
        for fkey in ("lane_field", "chip_color_field"):
            if fkey in p and p[fkey] not in fields:
                errs.append((f"{pp}.{fkey}", f"'{p[fkey]}' is not a field of {p['entity']}"))
        for ci, c in enumerate(cols):
            cm = c.get("color_map")
            if cm and cm not in style.get("color_maps", {}):
                errs.append((f"{pp}.columns[{ci}].color_map", f"unknown color_map '{cm}'"))
        cm = p.get("color_map")
        if cm and cm not in style.get("color_maps", {}):
            errs.append((f"{pp}.color_map", f"unknown color_map '{cm}'"))
        if p["kind"] == "board" and not (p.get("lanes") or p.get("lanes_from")):
            errs.append((pp, f"panel '{p['id']}': a board needs 'lanes' or 'lanes_from'"))
        if p["kind"] == "graph":
            ee = p["edges_entity"]
            if ee not in ents:
                errs.append((f"{pp}.edges_entity", f"unknown entity '{ee}'"))
            else:
                efields = ents[ee]["fields"]
                for fkey, dflt in (("edge_from", "a"), ("edge_to", "b"), ("edge_weight", None), ("edge_state", None),
                                   ("edge_label_pos", None)):
                    name = p.get(fkey, dflt)
                    if name and name not in efields:
                        errs.append((f"{pp}.{fkey}", f"'{name}' is not a field of {ee}"))
                for fkey in ("edge_from", "edge_to"):
                    f = efields.get(p.get(fkey, "a" if fkey == "edge_from" else "b"), {})
                    if f.get("ref") != p["entity"]:
                        errs.append((f"{pp}.{fkey}", f"graph '{p['id']}': the edge field must be a ref to {p['entity']}"))
            for fkey in ("x_field", "y_field", "radius_field", "point_color_field", "point_label"):
                if fkey in p and p[fkey] not in fields:
                    errs.append((f"{pp}.{fkey}", f"'{p[fkey]}' is not a field of {p['entity']}"))
            if p.get("point_color_field") and not p.get("color_map"):
                errs.append((pp, f"graph '{p['id']}': point_color_field needs a color_map"))
        lf = p.get("lanes_from")
        if lf and lf["entity"] not in ents:
            errs.append((f"{pp}.lanes_from.entity", f"unknown entity '{lf['entity']}'"))
    for i, a in enumerate(panels):
        for b in panels[i + 1:]:
            ax, ay, aw, ah = a["region"]
            bx, by, bw, bh = b["region"]
            if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                errs.append((f"$.layout.panels[{i}].region", f"panels '{a['id']}' and '{b['id']}' overlap"))
    seen = set()
    tables = {"arrow": "arrows", "highlight": "highlights", "callout": "callouts", "badge": "badges",
              "before_after": "callouts", "tint": None, "note": "callouts"}
    for fi, f in enumerate(sc["frames"]):
        fp = f"$.frames[{fi}]"
        if f["id"] in seen:
            errs.append((f"{fp}.id", f"duplicate frame id '{f['id']}'"))
        seen.add(f["id"])
        level = f["status"]["level"]
        if level not in style["badges"]:
            errs.append((f"{fp}.status.level", f"frame '{f['id']}': no badge style '{level}'"))
        elif not f.get("sources") and not style["badges"][level].get("sources_optional"):
            errs.append((f"{fp}.sources", f"frame '{f['id']}': cite at least one source (level '{level}' needs one)"))
        for j, s in enumerate(f.get("sources", [])):
            if s not in src_ids:
                errs.append((f"{fp}.sources[{j}]", f"frame '{f['id']}': unknown source '{s}'"))
        for pid in f.get("panels", []):
            if pid not in pids:
                errs.append((f"{fp}.panels", f"frame '{f['id']}': unknown panel '{pid}'"))
        if not f.get("summary") and not f.get("caption_only") and \
                not any(a["type"] in EXPLAINING for a in f.get("annotations", [])):
            errs.append((f"{fp}.annotations", f"frame '{f['id']}': no on-image explanation. Add a 'note' annotation "
                         f"(target + text; it is placed next to the target with an arrow) or set "
                         f"\"caption_only\": true on the frame"))
        for ai, a in enumerate(f.get("annotations", [])):
            ap = f"{fp}.annotations[{ai}]"
            if a["type"] == "note":
                if a.get("highlight") not in (None, False) and a["highlight"] not in style.get("highlights", {}):
                    errs.append((f"{ap}.highlight", f"note highlight '{a['highlight']}' not in style.highlights"))
                if a.get("arrow_style") and a["arrow_style"] not in style.get("arrows", {}):
                    errs.append((f"{ap}.arrow_style", f"note arrow_style '{a['arrow_style']}' not in style.arrows"))
            table = tables.get(a["type"])
            if table and a.get("style", "") not in style.get(table, {}):
                errs.append((f"{ap}.style", f"{a['type']} style '{a.get('style')}' not in style.{table}"))
        if f.get("summary"):
            si = f["summary"].get("status_column")
            for ri, row in enumerate(f["summary"]["rows"]):
                if si is not None and row[si] not in style["badges"]:
                    errs.append((f"{fp}.summary.rows[{ri}][{si}]", f"summary status '{row[si]}' is not a badge"))
    return errs
