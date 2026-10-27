"""Producer kit: small functions that emit a valid scenario dict (schema 1.3). Optional; plain JSON works as well.

Standard library only. Imports nothing from the renderer or the engine: it only writes data. The result is checked
by validate.py (the schema is the contract), not by this module.

    import scenario_builder as sb
    sc = sb.scenario("My video", sources=[sb.source("DOC", "The design doc", "section 2", kind="document")],
                     entities={"tasks": sb.entity("Tasks", {"id": {}, "state": {"enum": ["todo", "done"]}}, ["DOC"])},
                     panels=[sb.table_panel("tasks", "tasks", [24, 96, 860, 400], "Tasks",
                                            [sb.column("id", 80, font="mono"), sb.column("state", 160)])],
                     frames=[sb.frame("one", "1", "A task is added", "Task T1 starts as todo.", "available", ["DOC"],
                                      transitions=[sb.add_row("tasks", id="T1", state="todo")],
                                      notes=[sb.note("row:tasks:T1", "The new row is todo.")],
                                      narration="A task is added, and it starts as to do.")])
    sb.write(sc, "scenario.json")
"""
import json

DEFAULT_STYLE = {
    "colors": {"background": "#F4F6FA"},
    "badges": {
        "info": {"fill": "#E3E8F0", "text": "#1F2A3D", "label": "OVERVIEW", "sources_optional": True},
        "available": {"fill": "#1F8A4C", "text": "#FFFFFF", "label": "AVAILABLE"},
        "designed": {"fill": "#C77700", "text": "#FFFFFF", "label": "DESIGNED, NOT BUILT", "unbuilt": True},
        "planned": {"fill": "#B42318", "text": "#FFFFFF", "label": "PLANNED", "unbuilt": True},
        "inferred": {"fill": "#6941C6", "text": "#FFFFFF", "label": "INFERRED, NOT RUN", "unbuilt": True},
    },
    "arrows": {"flow": {"color": "#1F6FB2", "width": 4}},
    "highlights": {"focus": {"color": "#1F6FB2", "width": 4}},
    "callouts": {"info": {"fill": "#EEF4FF", "border": "#1F6FB2", "text": "#1F2A3D"}},
}


def source(sid, title, locator=None, kind="document", read_on=None):
    d = {"id": sid, "title": title, "kind": kind}
    if locator:
        d["locator"] = locator
    if read_on:
        d["read_on"] = read_on
    return d


def entity(title, fields, sources, key="id"):
    return {"title": title, "key": key, "sources": list(sources), "fields": fields}


def graph_panel(pid, entity_name, region, title, edges_entity, x_field, y_field, domain, **kw):
    """A scatter of the rows of entity_name with the rows of edges_entity as lines (schema 1.2)."""
    return dict({"id": pid, "kind": "graph", "entity": entity_name, "region": region, "title": title,
                 "edges_entity": edges_entity, "x_field": x_field, "y_field": y_field, "domain": domain}, **kw)


def column(field, width, **kw):
    return dict({"field": field, "width": width}, **kw)


def table_panel(pid, entity_name, region, title, columns, **kw):
    return dict({"id": pid, "kind": "table", "entity": entity_name, "region": region, "title": title,
                 "columns": columns}, **kw)


def board_panel(pid, entity_name, region, title, lane_field, lanes, **kw):
    return dict({"id": pid, "kind": "board", "entity": entity_name, "region": region, "title": title,
                 "lane_field": lane_field, "lanes": lanes}, **kw)


def add_row(entity_name, **row):
    return {"op": "add_row", "entity": entity_name, "row": row}


def update_row(entity_name, ident, **values):
    return {"op": "update_row", "entity": entity_name, "id": ident, "set": values}


def note(target, text, title=None, style="info", **kw):
    d = dict({"type": "note", "target": target, "text": text, "style": style}, **kw)
    if title:
        d["title"] = title
    return d


def frame(fid, step, title, caption, level, sources, transitions=None, notes=(), narration=None, detail="",
          seconds=None, **kw):
    f = {"id": fid, "step": step, "title": title, "caption": caption,
         "status": {"level": level, "detail": detail}, "sources": list(sources), "annotations": list(notes)}
    if transitions:
        f["transitions"] = list(transitions)
    if narration:
        f["narration"] = narration
    if seconds:
        f["seconds"] = seconds
    f.update(kw)
    return f


def scenario(title, sources, entities, panels, frames, initial_state=None, rules=None, style=None, audio=None,
             width=1920, height=1080, fps=30, default_seconds=6, crossfade_seconds=0.5, output_name="walkthrough",
             top_margin=None, **meta):
    meta = dict({"title": title, "width": width, "height": height, "fps": fps, "default_seconds": default_seconds,
                 "crossfade_seconds": crossfade_seconds, "output_name": output_name}, **meta)
    if top_margin is not None:      # blank strip at the top, px (schema 1.3); absent = the default
        meta["layout"] = dict(meta.get("layout", {}), top_margin=top_margin)
    if audio:
        meta["audio"] = audio
    sc = {"schema_version": "1.3", "meta": meta, "style": style or DEFAULT_STYLE, "sources": sources,
          "entities": entities, "initial_state": initial_state or {}, "layout": {"panels": panels},
          "frames": frames}
    if rules:
        sc["rules"] = rules
    return sc


def write(sc, path):
    with open(path, "w") as fh:
        json.dump(sc, fh, indent=1)
        fh.write("\n")
