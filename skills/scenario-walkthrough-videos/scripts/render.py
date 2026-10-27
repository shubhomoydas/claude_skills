#!/usr/bin/env python3
"""Stage 3, render: draw the frames of a walk-through as SVG and PNG, from a plan or from a scenario file.

Usage: render.py (--plan <plan.json> | --scenario <scenario.json>) --out <dir> [--no-png] [--check]

Standard library only (+ rsvg-convert for PNG). `--plan` reads the file plan.py wrote and needs nothing else.
`--scenario` builds that plan first, in memory (through plan.py, the one interface to the data side); the
renderer never reads the engine, the validator or the way the scenario was made. Writes <dir>/svg, <dir>/png and
<dir>/frames.json (the PNG list, each frame's hold seconds and narration, for the assemble stage).
`--check` draws every frame in memory and writes nothing: it finds the layout problems.

The build fails loudly, naming the frame and the box, when:
  - a text does not fit its box (FitError),
  - a panel holds more rows than it has room for, or a lane more chips than it has room for,
  - a callout or note covers a row, a chip or another note (unless "allow_overlap": true),
  - a note with a target finds no free space for its box (it is placed automatically, next to the target),
  - an annotation names a target that is not on the frame.
Exit 0 = rendered, 1 = a layout or scenario problem, 2 = usage.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

PLAN_MAJOR = "1"
DEFAULT_NOTE_FONT = 21      # px; was 18 before schema 1.1. meta.layout.note_font_size overrides it
DEFAULT_NOTE_WIDTH = 460    # px; was 420
FULL_BLEED = 300            # px: bars and background extend this far past the sides (the drawing is scaled in)
ANNOTATIONS = ("arrow", "highlight", "callout", "badge", "before_after", "tint", "note")


class ScenarioError(Exception):
    """The plan asks for something the renderer cannot draw."""


class FitError(ScenarioError):
    """A text, a row or a chip does not fit its box."""


class Ctx:
    """What one frame changed (read from the plan). The renderer marks these."""

    def __init__(self, rec):
        self.added = {(e, k) for e, k in rec["added"]}
        self.changed = {(e, k, f): old for e, k, f, old in rec["changed"]}
        self.removed = [(e, snap, idx) for e, snap, idx in rec["removed"]]
        self.refusals = [tuple(r) for r in rec["refusals"]]


def key_field(sc, entity):
    return sc["entities"][entity].get("key", "id")


def rows(state, entity):
    try:
        return state["rows"][entity]
    except KeyError:
        raise ScenarioError(f"unknown entity '{entity}'") from None


def find(sc, state, entity, ident):
    k = key_field(sc, entity)
    for r in rows(state, entity):
        if r[k] == ident:
            return r
    raise ScenarioError(f"no {entity} row with {k} = {ident!r}")


def top_margin(meta):
    """Blank strip at the top of every frame, in px: meta.layout.top_margin, default 32 px per 720 px of height
    (48 px on 1080). The Mac player overlays window buttons and the title on the top of the video, so nothing is
    drawn there. The layout is drawn in the full canvas coordinates and scaled to the space below the strip."""
    m = meta.get("layout", {}).get("top_margin")
    return int(m) if m is not None else round(meta["height"] * 32 / 720)


def content_box(sc):
    m = sc["meta"]
    lay = m.get("layout", {})
    top = lay.get("title_height", 84) + 12
    bottom = m["height"] - lay.get("caption_height", 72) - 8
    return top, bottom


# =============================================================================
# 3. TEXT MEASURE AND FIT
# =============================================================================

DEFAULT_FONTS = {"sans": "Helvetica, Arial, sans-serif", "mono": "Menlo, Courier New, monospace",
                 "char_width": {"sans": 0.56, "sans_bold": 0.61, "mono": 0.61}}


class Fonts:
    def __init__(self, meta):
        f = dict(DEFAULT_FONTS, **meta.get("fonts", {}))
        self.family = {"sans": f["sans"], "mono": f["mono"]}
        self.cw = f["char_width"]

    def width(self, text, size, font="sans", bold=False):
        """An upper estimate of the width of a text in px. Narrow and wide letters are weighted."""
        if font == "mono":
            return len(text) * size * self.cw["mono"]
        base = self.cw["sans_bold" if bold else "sans"]
        units = 0.0
        for ch in text:
            if ch in "iljI.,:;|!'` ":
                units += 0.5
            elif ch.isupper() or ch in "mwMW@%":
                units += 1.22
            else:
                units += 0.97
        return units * size * base


class Svg:
    def __init__(self, fonts, frame_id):
        self.parts = []
        self.fonts = fonts
        self.frame_id = frame_id

    def add(self, s):
        self.parts.append(s)

    def rect(self, x, y, w, h, fill="none", stroke="none", sw=1, rx=0, dash="", opacity=1.0):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{opacity}"' if opacity != 1.0 else ""
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}{o}/>')

    def circle(self, cx, cy, r, fill):
        self.add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}"/>')

    def fit(self, text, max_w, size, font="sans", bold=False, where=""):
        w = self.fonts.width(text, size, font, bold)
        if w > max_w:
            raise FitError(f"frame '{self.frame_id}' {where}: '{text}' needs {w:.0f}px, the box has {max_w:.0f}px")
        return w

    def text(self, x, y, text, size=16, fill="#000", font="sans", bold=False, anchor="start",
             max_w=None, where="", italic=False, strike=False):
        if max_w is not None:
            self.fit(text, max_w, size, font, bold, where)
        attrs = f'font-family="{escape(self.fonts.family[font])}" font-size="{size}" fill="{fill}" ' \
                f'text-anchor="{anchor}"'
        if bold:
            attrs += ' font-weight="bold"'
        if italic:
            attrs += ' font-style="italic"'
        if strike:
            attrs += ' text-decoration="line-through"'
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" {attrs}>{escape(text)}</text>')

    def wrap(self, text, max_w, size, font="sans", bold=False, max_lines=99, where=""):
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if self.fonts.width(trial, size, font, bold) <= max_w:
                cur = trial
                continue
            if not cur or self.fonts.width(word, size, font, bold) > max_w:
                raise FitError(f"frame '{self.frame_id}' {where}: word '{word}' is wider than {max_w:.0f}px")
            lines.append(cur)
            cur = word
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            raise FitError(f"frame '{self.frame_id}' {where}: text needs {len(lines)} lines, "
                           f"the box allows {max_lines}: '{text}'")
        return lines

    def pill(self, x, y, text, size, fill, color, pad=10, h=None, font="sans"):
        w = self.fonts.width(text, size, font, True) + 2 * pad
        h = h or size + 12
        self.rect(x, y, w, h, fill=fill, rx=h / 2)
        self.text(x + w / 2, y + h / 2 + size * 0.36, text, size=size, fill=color, bold=True,
                  anchor="middle", font=font)
        return w

    def render(self, width, height, margin=0, bg="#FFFFFF", strip="#1F2A3D"):
        """Wrap the parts: a blank strip of `margin` px at the top, the drawing scaled and centred below it.
        Fails when a drawn element reaches above the drawing area (y < 0), which would sit in the strip."""
        problems = elements_above(self.parts)
        if problems:
            raise FitError(f"frame '{self.frame_id}': {problems[0]} lies in the top {margin}px strip "
                           f"reserved for the player overlay ({len(problems)} element(s))")
        body = "\n".join(self.parts)
        k = (height - margin) / height
        tx = (width - width * k) / 2
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                f'viewBox="0 0 {width} {height}">\n<rect x="0" y="0" width="{width}" height="{height}" fill="{bg}"/>\n'
                f'<rect x="0" y="0" width="{width}" height="{margin}" fill="{strip}"/>\n'
                f'<g id="content" transform="translate({tx:.3f},{margin}) scale({k:.6f})">\n{body}\n</g>\n</svg>\n')


_NUM = r"(-?\d+(?:\.\d+)?)"


def elements_above(parts):
    """Descriptions of the drawn elements whose top edge is above y = 0 of the drawing area (outside <defs>
    and parts clipped to a panel). The drawing area starts below the top margin, so such an element would enter the strip."""
    bad = []
    for part in parts:
        if part.startswith(("<defs", "<clipPath", "<g clip-path")):     # clipped parts stay inside their panel
            continue
        for m in re.finditer(r'<rect x="[^"]*" y="' + _NUM + '"', part):
            if float(m.group(1)) < -0.5:
                bad.append(f"rect at y={m.group(1)}")
        for m in re.finditer(r'<text x="[^"]*" y="' + _NUM + r'" font-family="[^"]*" font-size="' + _NUM + '"', part):
            if float(m.group(1)) - float(m.group(2)) < -0.5:
                bad.append(f"text at y={m.group(1)}")
        for m in re.finditer(r'<circle cx="[^"]*" cy="' + _NUM + '" r="' + _NUM + '"', part):
            if float(m.group(1)) - float(m.group(2)) < -0.5:
                bad.append(f"circle at y={m.group(1)}")
        for m in re.finditer(r'<line x1="[^"]*" y1="' + _NUM + '" x2="[^"]*" y2="' + _NUM + '"', part):
            if min(float(m.group(1)), float(m.group(2))) < -0.5:
                bad.append("line above the area")
        for m in re.finditer(r'<path d="([^"]*)"', part):
            pts = re.findall(_NUM + "," + _NUM, m.group(1))
            if any(float(y) < -0.5 for _, y in pts):
                bad.append("path above the area")
    return bad


def lighten(hex_color, amount):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = (int(c + (255 - c) * amount) for c in (r, g, b))
    return f"#{r:02X}{g:02X}{b:02X}"


def text_of(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# =============================================================================
# 4. FRAME RENDERER
# =============================================================================

DEFAULT_COLORS = {
    "background": "#F4F6FA", "panel": "#FFFFFF", "panel_border": "#C9D1DE", "panel_title": "#1F2A3D",
    "text": "#1F2A3D", "muted": "#5B6678", "row_alt": "#F7F9FC", "title_bar": "#1F2A3D",
    "title_text": "#FFFFFF", "title_muted": "#C9D1DE", "caption_bar": "#FFFFFF", "caption_text": "#1F2A3D",
    "changed": "#FFF1A8", "added": "#DDF5E3", "added_tag": "#1F8A4C", "ghost": "#9AA3B2", "null": "#A0A8B8",
    "moved": "#C2410C", "refused": "#B42318",
}


class FrameRenderer:
    def __init__(self, sc, frame, index, total, state, ctx):
        self.sc, self.meta, self.st = sc, sc["meta"], sc["style"]
        self.c = dict(DEFAULT_COLORS, **self.st.get("colors", {}))
        self.frame, self.index, self.total = frame, index, total
        self.state, self.ctx = state, ctx
        self.svg = Svg(Fonts(self.meta), frame["id"])
        self.top, self.bottom = content_box(sc)
        self.anchors = {}
        self.solid = []          # (key, bbox) of rows, chips and callouts, for the overlap check
        self.reserved = []       # boxes a note must not cover: focus targets and panel headers
        self.headers = []        # panel title and header strips: an arrow should avoid them too
        self.notes = []          # (id, annotation) of the notes drawn, for their arrows and highlights

    # ---- helpers -------------------------------------------------------------
    def fail(self, msg, fit=False):
        raise (FitError if fit else ScenarioError)(f"frame '{self.frame['id']}': {msg}")

    def register(self, key, bbox, solid=True):
        self.anchors[key] = bbox
        if solid:
            self.solid.append((key, bbox))

    def color_of(self, cmap, value):
        if value is None:
            return self.c["null"]
        try:
            return self.st["color_maps"][cmap][str(value)]
        except KeyError:
            self.fail(f"color_map '{cmap}' has no colour for {value!r}")

    def changed(self, entity, ident, field):
        return (entity, ident, field) in self.ctx.changed

    def mark(self, entity, ident):
        level = self.state["marks"].get(f"{entity}:{ident}")
        return self.st["badges"][level] if level else None

    def with_ghosts(self, entity, live):
        """[(row, ghost)]: the live rows, with each row deleted in this frame back at its old index."""
        items = [(r, False) for r in live]
        for e, snap, index in self.ctx.removed:
            if e == entity:
                items.insert(min(index, len(items)), (snap, True))
        return items

    def free(self, x, y, w, h, skip=()):
        """True when the box stays in the content area and covers no row, chip or callout."""
        if y < self.top or y + h > self.bottom or x < 0 or x + w > self.meta["width"]:
            return False
        return all(not (x < bx + bw and bx < x + w and y < by + bh and by < y + h)
                   for k, (bx, by, bw, bh) in self.solid if k not in skip)

    def label_spot(self, box, w, h, key):
        """Where a label of w x h goes next to a box: above, below, then inside the top right corner."""
        x, y, bw, bh = box
        lx = min(max(x, 4), self.meta["width"] - 10 - w)
        rx = min(x + bw - w, self.meta["width"] - 10 - w)
        for cx, cy in ((lx, y - h - 6), (lx, y + bh + 6), (rx, y - h - 6), (rx, y + bh + 6)):
            if self.free(cx, cy, w, h, skip=(key,)):
                return cx, cy
        return rx, y + 4

    def panel_frame(self, p, right_note=None):
        x, y, w, h = p["region"]
        s = self.svg
        accent = p.get("color") or self.st.get("entity_colors", {}).get(p["entity"], self.c["muted"])
        s.rect(x, y, w, h, fill=self.c["panel"], stroke=self.c["panel_border"], sw=1.5, rx=10)
        s.rect(x, y, 7, h, fill=accent, rx=3)
        note_w = s.fonts.width(right_note, 13) + 12 if right_note else 0
        tw = s.fit(p["title"], w - 40 - note_w, 19, bold=True, where=f"panel {p['id']} title")
        s.text(x + 18, y + 27, p["title"], size=19, bold=True, fill=self.c["panel_title"])
        if p.get("subtitle"):
            used = 18 + tw + 12
            s.text(x + used, y + 27, p["subtitle"], size=13, font="mono", fill=accent,
                   max_w=w - used - 12 - note_w, where=f"panel {p['id']} subtitle")
        if right_note:
            s.text(x + w - 12, y + 27, right_note, size=13, fill=self.c["muted"], anchor="end")
        self.register(f"panel:{p['id']}", (x, y, w, h), solid=False)
        return x, y, w, h, accent

    # ---- table panel -----------------------------------------------------------
    def table_panel(self, p):
        e = p["entity"]
        k = key_field(self.sc, e)
        live = list(rows(self.state, e))
        items = self.with_ghosts(e, live)
        if p.get("order") == "newest_first":
            items = list(reversed(items))
        window = p.get("window")
        if window:
            items = items[:window]
        note = f"{len(live)} rows" + (", newest first" if p.get("order") == "newest_first" else "")
        x, y, w, h, _ = self.panel_frame(p, right_note=p.get("note", note))
        s, fs = self.svg, p.get("font_size", 15)
        row_h = p.get("row_height", 30)
        first_y = y + 64
        capacity = min(p.get("max_rows", 99), int((y + h - 8 - first_y) // row_h))
        if len(items) > capacity:
            self.fail(f"panel {p['id']} holds {capacity} rows, the state has {len(items)}; "
                      f"grow the region, lower row_height, or set a window", fit=True)
        cols = p["columns"]
        total = sum(c["width"] for c in cols)
        if total > w - 24:
            self.fail(f"panel {p['id']} columns need {total}px, the panel has {w - 24}px", fit=True)
        cx = x + 14
        for c in cols:
            s.text(cx, first_y - 8, c.get("header", c["field"]), size=12, font="mono", fill=self.c["muted"],
                   max_w=c["width"] - 6, where=f"panel {p['id']} header")
            cx += c["width"]
        ry = first_y
        for i, (r, ghost) in enumerate(items):
            ident = r[k]
            added = (e, ident) in self.ctx.added
            mk = self.mark(e, ident)
            bg = self.c["added"] if added else (self.c["row_alt"] if i % 2 else self.c["panel"])
            if ghost:
                s.rect(x + 10, ry + 1, w - 20, row_h - 3, fill="#F2F3F5", stroke=self.c["ghost"], sw=1.5,
                       rx=4, dash="6 4")
            elif mk:
                s.rect(x + 10, ry + 1, w - 20, row_h - 3, fill=lighten(mk["fill"], 0.9), stroke=mk["fill"],
                       sw=1.5, rx=4, dash="6 4")
            else:
                s.rect(x + 10, ry + 1, w - 20, row_h - 3, fill=bg, rx=4)
            cx = x + 14
            for c in cols:
                f, cw = c["field"], c["width"]
                val = r.get(f)
                txt = text_of(val)
                font = c.get("font", "sans")
                color = self.c["ghost"] if ghost else (self.c["null"] if val is None else self.c["text"])
                indent = 0
                if c.get("color_map") and val is not None:
                    s.circle(cx + 6, ry + row_h / 2, 6, self.color_of(c["color_map"], val))
                    indent = 18
                if not ghost and self.changed(e, ident, f):
                    tw = s.fonts.width(txt, fs, font)
                    s.rect(cx + indent - 4, ry + 4, tw + 8, row_h - 9, fill=self.c["changed"], rx=3)
                s.text(cx + indent, ry + row_h / 2 + fs * 0.36, txt, size=fs, font=font, fill=color,
                       max_w=cw - 6 - indent, where=f"panel {p['id']} row {ident} {f}",
                       italic=bool(mk) and not ghost, strike=ghost)
                self.anchors[f"cell:{p['id']}:{ident}:{f}"] = (cx, ry, cw, row_h)
                cx += cw
            self.register(f"row:{p['id']}:{ident}", (x + 10, ry, w - 20, row_h))
            ry += row_h
        if not items:
            s.text(x + 18, first_y + 20, "(no rows)", size=14, fill=self.c["muted"], italic=True)

    # ---- board panel -----------------------------------------------------------
    def lanes(self, p):
        """[(lane value, label, ghost, lane row or None)]"""
        if p.get("lanes"):
            labels = p.get("lane_labels", {})
            out = [(v, labels.get(str(v), text_of(v)), False, None) for v in p["lanes"]]
        else:
            lf = p["lanes_from"]
            le = lf["entity"]
            lk = key_field(self.sc, le)
            out = [(r[lk], text_of(r[lf.get("label", lk)]), ghost, r)
                   for r, ghost in self.with_ghosts(le, rows(self.state, le))]
        if p.get("null_lane"):
            out.append((None, p["null_lane"], False, None))
        return out

    def board_panel(self, p):
        e = p["entity"]
        k = key_field(self.sc, e)
        lf_field = p["lane_field"]
        x, y, w, h, accent = self.panel_frame(p, right_note=p.get("note"))
        s = self.svg
        lanes = self.lanes(p)
        known = {v for v, *_ in lanes}
        stray = [r[k] for r in rows(self.state, e) if r[lf_field] not in known]
        if stray:
            self.fail(f"board {p['id']}: rows {stray} have a {lf_field} with no lane; add the lane or a null_lane")
        gap = 10
        lw = (w - 28 - (len(lanes) - 1) * gap) / len(lanes)
        chip_w, chip_h, cg = p.get("chip_width", 56), 26, 6
        per_row = max(1, int((lw - 16 + cg) // (chip_w + cg)))
        ly, lh = y + 42, h - 52
        for i, (val, label, ghost, lane_row) in enumerate(lanes):
            lx = x + 16 + i * (lw + gap)
            border = self.c["ghost"] if ghost else lighten(accent, 0.4)
            s.rect(lx, ly, lw, lh, fill="#F2F3F5" if ghost else lighten(accent, 0.94), stroke=border, sw=1.5,
                   rx=8, dash="7 5" if ghost else "")
            le = p.get("lanes_from", {}).get("entity")
            label_field = p.get("lanes_from", {}).get("label")
            tag = None
            if ghost:
                tag = ("deleted", self.c["ghost"])
            elif le and (le, val) in self.ctx.added:
                tag = ("NEW", self.c["added_tag"])
            tag_w = s.fonts.width(tag[0], 12, bold=True) + 16 if tag else 0
            if tag:
                s.pill(lx + lw - tag_w - 6, ly + 6, tag[0], 12, tag[1], "#FFFFFF", pad=8, h=22)
            dot = 0
            if p.get("lane_color_map") and val is not None:
                s.circle(lx + 16, ly + 18, 7, self.color_of(p["lane_color_map"], val))
                dot = 18
            if le and label_field and not ghost and self.changed(le, val, label_field):
                tw = s.fonts.width(label, 16, bold=True)
                s.rect(lx + 6 + dot, ly + 5, tw + 8, 24, fill=self.c["changed"], rx=3)
            s.text(lx + 10 + dot, ly + 23, label, size=16, bold=True,
                   fill=self.c["ghost"] if ghost else self.c["text"], max_w=lw - 20 - dot - tag_w,
                   where=f"board {p['id']} lane {label}", strike=ghost)
            self.register(f"lane:{p['id']}:{text_of(val)}", (lx, ly, lw, lh), solid=False)
            chips = [] if ghost else [r for r in rows(self.state, e) if r[lf_field] == val]
            if not chips:
                s.text(lx + 10, ly + 56, "(deleted row)" if ghost else "(none)", size=13,
                       fill=self.c["muted"], italic=True)
            n_rows = -(-len(chips) // per_row)
            if ly + 38 + n_rows * (chip_h + cg) > ly + lh:
                self.fail(f"board {p['id']} lane '{label}' holds {len(chips)} chips, more than fit", fit=True)
            for j, r in enumerate(chips):
                ident = r[k]
                px = lx + 10 + (j % per_row) * (chip_w + cg)
                py = ly + 38 + (j // per_row) * (chip_h + cg)
                cf = p.get("chip_color_field")
                fill = self.color_of(p["color_map"], r[cf]) if cf and p.get("color_map") else accent
                mk = self.mark(e, ident)
                if mk:
                    s.rect(px, py, chip_w, chip_h, fill="#FFFFFF", stroke=mk["fill"], sw=2, rx=5, dash="5 3")
                    tcolor = mk["fill"]
                else:
                    s.rect(px, py, chip_w, chip_h, fill=fill, rx=5)
                    tcolor = "#FFFFFF"
                if self.changed(e, ident, lf_field) or (e, ident) in self.ctx.added:
                    ring = self.c["moved"] if self.changed(e, ident, lf_field) else self.c["added_tag"]
                    s.rect(px - 3, py - 3, chip_w + 6, chip_h + 6, stroke=ring, sw=2.5, rx=7)
                s.text(px + chip_w / 2, py + 18, text_of(r[p.get("chip_label", k)]), size=13, font="mono",
                       bold=True, fill=tcolor, anchor="middle", max_w=chip_w - 4, where=f"board {p['id']} chip")
                self.register(f"row:{p['id']}:{ident}", (px, py, chip_w, chip_h))

    # ---- graph panel (a 2-D scatter of points, with edges and circles) --------------
    def graph_panel(self, p):
        """Points of one entity drawn at (x_field, y_field) on a to-scale plot, a circle of radius radius_field around
        each point that has one, and the rows of edges_entity as lines (or a loop when both ends are the same point)
        styled by their state. Edges deleted in this frame are drawn dashed, as ghosts."""
        e, k = p["entity"], key_field(self.sc, p["entity"])
        ee = p["edges_entity"]
        ek = key_field(self.sc, ee)
        fa, fb = p.get("edge_from", "a"), p.get("edge_to", "b")
        fw, fs_ = p.get("edge_weight"), p.get("edge_state")
        xf, yf = p["x_field"], p["y_field"]
        rf, cf = p.get("radius_field"), p.get("point_color_field")
        x, y, w, h, accent = self.panel_frame(p, right_note=p.get("note"))
        s = self.svg
        dom = p.get("domain", {"x": [0, 10], "y": [0, 10]})
        (x0, x1), (y0, y1) = dom["x"], dom["y"]
        pad = 34
        px, py, pw, ph = p.get("plot") or [x + 14, y + 44, w - 28, h - 56]
        sc = min((pw - 2 * pad) / (x1 - x0), (ph - 2 * pad) / (y1 - y0))
        bw, bh = (x1 - x0) * sc + 2 * pad, (y1 - y0) * sc + 2 * pad
        X = lambda v: px + pad + (v - x0) * sc
        Y = lambda v: py + bh - pad - (v - y0) * sc
        plot_box = (px, py, bw, bh)
        s.rect(px, py, bw, bh, fill="#FFFFFF", stroke=self.c["panel_border"], sw=1.5, rx=6)
        for gx in range(int(x0), int(x1) + 1):
            s.add(f'<line x1="{X(gx):.1f}" y1="{Y(y0):.1f}" x2="{X(gx):.1f}" y2="{Y(y1):.1f}" stroke="#E6EAF1" stroke-width="1"/>')
            s.text(X(gx), py + bh - 10, str(gx), size=12, fill=self.c["muted"], anchor="middle")
        for gy in range(int(y0), int(y1) + 1):
            s.add(f'<line x1="{X(x0):.1f}" y1="{Y(gy):.1f}" x2="{X(x1):.1f}" y2="{Y(gy):.1f}" stroke="#E6EAF1" stroke-width="1"/>')
            s.text(px + 8, Y(gy) + 4, str(gy), size=12, fill=self.c["muted"])
        clip = f"clip-{p['id']}-{self.index}"
        s.add(f'<clipPath id="{clip}"><rect x="{px:.1f}" y="{py:.1f}" width="{bw:.1f}" height="{bh:.1f}"/></clipPath>')
        pts = {r[k]: r for r in rows(self.state, e)}
        for i, r in pts.items():
            if not (x0 <= r[xf] <= x1 and y0 <= r[yf] <= y1):
                self.fail(f"graph {p['id']}: point {i} at ({r[xf]}, {r[yf]}) is outside the domain {dom}")
        pos = {i: (X(r[xf]), Y(r[yf])) for i, r in pts.items()}
        ccol = p.get("circle_color", "#1F6FB2")
        for i, r in pts.items():                          # circles
            rad = r.get(rf) if rf else None
            if not rad:
                continue
            cx, cy = pos[i]
            R = rad * sc
            s.add(f'<g clip-path="url(#{clip})"><circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R:.1f}" fill="{ccol}" '
                  f'fill-opacity="0.07" stroke="{ccol}" stroke-width="2.5" stroke-dasharray="9 6"/></g>')
            label = f"r {rad:.3f}"
            lw = s.fonts.width(label, 14, bold=True) + 16
            spot = None
            for ang in (-50, -130, 50, 130, -90, 0, 180, 90, -20, -160):
                lx, ly = cx + R * math.cos(math.radians(ang)), cy + R * math.sin(math.radians(ang))
                if px + lw / 2 + 4 <= lx <= px + bw - lw / 2 - 4 and py + 18 <= ly <= py + bh - 24:
                    spot = (lx, ly)
                    break
            if spot:
                s.rect(spot[0] - lw / 2, spot[1] - 11, lw, 22, fill="#FFFFFF", stroke=ccol, sw=1.5, rx=11)
                s.text(spot[0], spot[1] + 5, label, size=14, bold=True, fill=ccol, anchor="middle")
                self.register(f"circle:{p['id']}:{i}", (spot[0] - lw / 2, spot[1] - 11, lw, 22), solid=False)
        styles = p.get("edge_styles", {})                 # edges
        labels = []
        for r, ghost in self.with_ghosts(ee, list(rows(self.state, ee))):
            ident, a, b = r[ek], r[fa], r[fb]
            if a not in pos or b not in pos:
                self.fail(f"graph {p['id']}: edge {ident} joins {a!r} and {b!r}, which are not points of {e}")
            sty = styles.get(r.get(fs_), {}) if fs_ else {}
            if sty.get("hidden") and not ghost:
                continue
            col = self.c["refused"] if ghost else sty.get("color", self.c["muted"])
            wd = 2.5 if ghost else sty.get("width", 3)
            dash = "8 6" if ghost else sty.get("dash", "")
            da = f' stroke-dasharray="{dash}"' if dash else ""
            ax, ay = pos[a]
            bx, by = pos[b]
            if a == b:                                    # a self-edge: a small loop up and left of the point
                mx, my = ax - 20, ay - 20
                s.add(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="12" fill="none" stroke="{col}" stroke-width="{wd}"{da}/>')
                lab = (mx - 14, my - 14)
            else:
                s.add(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{col}" '
                      f'stroke-width="{wd}"{da} stroke-linecap="round"/>')
                t = 0.5
                if p.get("edge_label_pos") and r.get(p["edge_label_pos"]) is not None:
                    t = r[p["edge_label_pos"]]
                lab = (ax + (bx - ax) * t, ay + (by - ay) * t)
            txt = ""
            if fw and sty.get("label", False):
                v = r.get(fw)
                txt = f"{v:.3f}" if isinstance(v, float) else text_of(v)
            lw = (s.fonts.width(txt, 14, bold=True) + 14) if txt else 26
            box = (lab[0] - lw / 2, lab[1] - 11, lw, 22)
            self.register(f"edge:{p['id']}:{ident}", box, solid=False)
            if txt:
                if fw and (ee, ident, fw) in self.ctx.changed:
                    fill = self.c["changed"]
                elif (ee, ident) in self.ctx.added:
                    fill = self.c["added"]
                else:
                    fill = "#FFFFFF"
                labels.append((box, txt, col, fill))
        for (bx_, by_, bw_, bh_), txt, col, fill in labels:
            s.rect(bx_, by_, bw_, bh_, fill=fill, stroke=col, sw=2, rx=11)
            s.text(bx_ + bw_ / 2, by_ + 16, txt, size=14, bold=True, fill=self.c["text"], anchor="middle")
        for i, r in pts.items():                          # points
            cx, cy = pos[i]
            fill = self.color_of(p["color_map"], r[cf]) if cf and p.get("color_map") else accent
            s.add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="17" fill="{fill}" stroke="#FFFFFF" stroke-width="2.5"/>')
            if cf and self.changed(e, i, cf):
                s.add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="23" fill="none" stroke="{self.c["moved"]}" stroke-width="3"/>')
            if (e, i) in self.ctx.added:
                s.add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="23" fill="none" stroke="{self.c["added_tag"]}" stroke-width="3"/>')
            s.text(cx, cy + 6, text_of(r[p.get("point_label", k)]), size=17, bold=True, fill="#FFFFFF", anchor="middle")
            if p.get("show_coords"):
                s.add(f'<text x="{cx + 22:.1f}" y="{cy + 30:.1f}" font-family="{escape(s.fonts.family["sans"])}" font-size="13" '
                      f'fill="{self.c["muted"]}" stroke="#FFFFFF" stroke-width="3.5" paint-order="stroke">'
                      f'({text_of(r[xf])}, {text_of(r[yf])})</text>')
            self.register(f"row:{p['id']}:{i}", (cx - 20, cy - 20, 40, 40), solid=False)
        self.register(f"plot:{p['id']}", plot_box)

    # ---- summary frame ---------------------------------------------------------
    def summary(self):
        s, c = self.svg, self.c
        spec = self.frame["summary"]
        W = self.meta["width"]
        widths = spec["widths"]
        if sum(widths) > W - 80:
            self.fail(f"summary widths {sum(widths)} > {W - 80}", fit=True)
        s.rect(24, self.top, W - 48, self.bottom - self.top, fill=c["panel"], stroke=c["panel_border"], rx=10)
        cx = 40
        for head, cw in zip(spec["columns"], widths):
            s.text(cx, self.top + 40, head, size=20, bold=True, fill=c["text"], max_w=cw - 12,
                   where="summary header")
            cx += cw
        y = self.top + 60
        si = spec.get("status_column")
        for i, row in enumerate(spec["rows"]):
            if len(row) != len(widths):
                self.fail(f"summary row {i} has {len(row)} cells, the table has {len(widths)} columns")
            wrapped = [[cell] if j == si else s.wrap(cell, cw - 16, 20, max_lines=2, where=f"summary row {i}")
                       for j, (cell, cw) in enumerate(zip(row, widths))]
            rh = 18 + 26 * max(len(w_) for w_ in wrapped)
            if y + rh > self.bottom:
                self.fail("summary rows do not fit", fit=True)
            s.rect(32, y, W - 64, rh - 4, fill=c["row_alt"] if i % 2 else c["panel"], rx=6)
            cx = 40
            for j, (lines, cw) in enumerate(zip(wrapped, widths)):
                if j == si:
                    b = self.st["badges"][lines[0]]
                    if s.fonts.width(b["label"], 14, bold=True) + 20 > cw - 8:
                        self.fail(f"summary badge '{b['label']}' is wider than its column", fit=True)
                    s.pill(cx, y + 9, b["label"], 14, b["fill"], b["text"], pad=10, h=26)
                else:
                    for n, line in enumerate(lines):
                        s.text(cx, y + 30 + 26 * n, line, size=20, bold=(j == 0), fill=c["text"])
                cx += cw
            y += rh

    # ---- annotations ---------------------------------------------------------
    def bbox(self, key):
        box = self.anchors.get(key)
        if box is None:
            self.fail(f"annotation target '{key}' is not on the frame; known: {sorted(self.anchors)}")
        return box

    def boxed_note(self, a, title, lines_text, sty, kind):
        """Draw a callout-shaped box; check that it stays in the content area and covers nothing."""
        s = self.svg
        x, y, w = a["x"], a["y"], a["w"]
        fs, lh = self.note_font()
        lines = s.wrap(lines_text, w - 28, fs, max_lines=a.get("max_lines", 6), where=f"{kind} {a.get('id', '')}")
        title_h = lh + 4 if title else 0
        if title:
            s.fit(title, w - 28, fs, bold=True, where=f"{kind} {a.get('id', '')} title")
        h = 14 + title_h + lh * len(lines) + 10
        if y < self.top - 4 or y + h > self.bottom + 4 or x < 0 or x + w > self.meta["width"] - 8:
            self.fail(f"{kind} {a.get('id', '')} ({x},{y},{w}x{h:.0f}) leaves the content area")
        if not a.get("allow_overlap"):
            for key, (bx, by, bw, bh) in self.solid:
                if x < bx + bw and bx < x + w and y < by + bh and by < y + h:
                    self.fail(f"{kind} {a.get('id', '')} ({x},{y},{w}x{h:.0f}) covers {key}; "
                              f"move it or set allow_overlap")
        s.rect(x + 4, y + 5, w, h, fill="#000000", rx=10, opacity=0.08)
        s.rect(x, y, w, h, fill=sty["fill"], stroke=sty["border"], sw=2.5, rx=10)
        ty = y + 14
        if title:
            s.text(x + 14, ty + fs * 0.95, title, size=fs, bold=True, fill=sty["border"])
            ty += title_h
        for i, line in enumerate(lines):
            s.text(x + 14, ty + fs * 0.95 + lh * i, line, size=fs, fill=sty["text"])
        return (x, y, w, h)

    def callout(self, a):
        box = self.boxed_note(a, a.get("title"), a["text"], self.st["callouts"][a["style"]], "callout")
        self.register(f"callout:{a['id']}", box)

    def before_after(self, a):
        """A box that names the value before and after this frame; the values come from the state."""
        _, panel, ident, field = a["target"].split(":", 3)
        p = next((q for q in self.sc["layout"]["panels"] if q["id"] == panel), None)
        if p is None:
            self.fail(f"before_after: no panel '{panel}'")
        key = (p["entity"], ident, field)
        if key not in self.ctx.changed:
            self.fail(f"before_after: {p['entity']} {ident} {field} did not change in this frame")
        after = find(self.sc, self.state, p["entity"], ident)[field]
        text = f"before: {text_of(self.ctx.changed[key])}   after: {text_of(after)}"
        box = self.boxed_note(a, a.get("title"), text, self.st["callouts"][a["style"]], "before_after")
        self.register(f"callout:{a.get('id', a['target'])}", box)
        self.arrow({"from": f"callout:{a.get('id', a['target'])}", "to": a["target"],
                    "style": a.get("arrow_style", next(iter(self.st["arrows"])))})

    # ---- notes: on-image explanations placed next to their target ---------------
    NOTE_STEP = 8
    NOTE_GAP = 10        # clear space kept between a note and any row, chip, header or other note

    def note_font(self):
        """(font size, line height) of the text in notes, callouts and before/after boxes: meta.layout.note_font_size."""
        fs = self.meta.get("layout", {}).get("note_font_size", DEFAULT_NOTE_FONT)
        return fs, round(fs * 4 / 3)

    def note_size(self, a, w):
        s = self.svg
        title = a.get("title")
        fs, lh = self.note_font()
        lines = s.wrap(a["text"], w - 28, fs, max_lines=a.get("max_lines", 6), where=f"note {a.get('id', a['target'])}")
        if title:
            s.fit(title, w - 28, fs, bold=True, where=f"note {a.get('id', a['target'])} title")
        return 14 + (lh + 4 if title else 0) + lh * len(lines) + 10

    def reserve_targets(self):
        """Boxes that notes must not cover: panel headers, the target of every note and highlight, arrow courses."""
        for key, (x, y, w, h) in list(self.anchors.items()):
            if key.startswith("panel:"):
                self.reserved.append((x, y, w, min(h, 72)))
                self.headers.append((x, y, w, min(h, 72)))
        for a in self.frame.get("annotations", []):
            if a["type"] in ("note", "highlight") and a.get("target") in self.anchors:
                x, y, w, h = self.anchors[a["target"]]
                self.reserved.append((x - 10, y - 10, w + 20, h + 20))
            if a["type"] == "arrow" and a.get("from") in self.anchors and a.get("to") in self.anchors:
                (ax, ay, aw, ah), (bx, by, bw, bh) = self.anchors[a["from"]], self.anchors[a["to"]]
                p0, p1 = (ax + aw / 2, ay + ah / 2), (bx + bw / 2, by + bh / 2)
                for i in range(9):                       # the arrow's course, as small boxes
                    t = i / 8
                    self.reserved.append((p0[0] + (p1[0] - p0[0]) * t - 22, p0[1] + (p1[1] - p0[1]) * t - 22, 44, 44))

    def crossings(self, x, y, w, h, target):
        """How many different rows, chips or panel headers the arrow from a box at (x, y, w, h) to the target
        would cross (a sampled estimate; each box counts once)."""
        tx, ty, tw, th = target
        cx, cy = x + w / 2, y + h / 2
        ex, ey = min(max(cx, tx), tx + tw), min(max(cy, ty), ty + th)       # nearest point of the target
        sx, sy = min(max(ex, x), x + w), min(max(ey, y), y + h)             # nearest point of the note
        hit = set()
        boxes = [b for _, b in self.solid] + self.headers
        for i in range(1, 12):
            px, py = sx + (ex - sx) * i / 12, sy + (ey - sy) * i / 12
            if tx - 2 <= px <= tx + tw + 2 and ty - 2 <= py <= ty + th + 2:
                continue
            for n, (bx, by, bw, bh) in enumerate(boxes):
                if bx <= px <= bx + bw and by <= py <= by + bh:
                    hit.add(n)
        return len(hit)

    def place_note(self, a, w, h):
        """The free spot nearest to the target for a w x h box; fails when there is none."""
        tx, ty, tw, th = self.bbox(a["target"])
        W = self.meta["width"]
        panels = [b for k, b in self.anchors.items() if k.startswith("panel:")]
        blocks = [b for _, b in self.solid] + self.reserved
        best, best_cost = None, None
        step = self.NOTE_STEP
        gap = self.NOTE_GAP
        y = int(self.top)
        while y + h <= self.bottom:
            x = 24
            while x + w <= W - 24:
                if all(not (x - gap < bx + bw and bx < x + w + gap and y - gap < by + bh and by < y + h + gap)
                       for bx, by, bw, bh in blocks):
                    dx = max(tx - (x + w), x - (tx + tw), 0)
                    dy = max(ty - (y + h), y - (ty + th), 0)
                    cost = (dx * dx + dy * dy) ** 0.5
                    if any(x < px + pw and px < x + w and y < py + ph and py < y + h for px, py, pw, ph in panels):
                        cost += 60            # prefer the gaps between panels, then a panel's empty part
                    cost += 0.001 * (abs(x + w / 2 - tx - tw / 2) + abs(y + h / 2 - ty - th / 2))
                    cost += 120 * self.crossings(x, y, w, h, (tx, ty, tw, th))
                    if best_cost is None or cost < best_cost:
                        best, best_cost = (x, y), cost
                x += step
            y += step
        if best is None:
            self.fail(f"note on '{a['target']}': no free space for a {w}x{h} box; shorten the text, use a narrower "
                      f"'w', hide a panel with the frame's 'panels', or set 'x' and 'y'")
        return best

    def note(self, a):
        n = len(self.notes) + 1
        nid = a.get("id") or f"note{n}"
        self.bbox(a["target"])                       # fails with the known targets when it is wrong
        w = a.get("w", DEFAULT_NOTE_WIDTH)
        b = dict(a, id=nid)
        if "x" not in a or "y" not in a:
            b["x"], b["y"] = self.place_note(a, w, self.note_size(b, w))
        b["w"] = w
        box = self.boxed_note(b, a.get("title"), a["text"], self.st["callouts"][a.get("style", "info")], "note")
        self.register(f"callout:{nid}", box)
        self.notes.append((nid, a))

    def note_marks(self):
        """The highlight of each note's target and the arrow from the note to it."""
        hl = self.st.get("highlights", {})
        default = "focus" if "focus" in hl else next(iter(hl), None)
        for nid, a in self.notes:
            style = a.get("highlight", default)
            if style:
                self.highlight({"target": a["target"], "style": style, "label": a.get("label")})
            self.note_arrow(nid, a)

    def note_arrow(self, nid, a):
        nx, ny, nw, nh = self.anchors[f"callout:{nid}"]
        tx, ty, tw, th = self.anchors[a["target"]]
        name = a.get("arrow_style") or next(iter(self.st["arrows"]))
        sty = self.st["arrows"][name]
        clamp = lambda v, lo, hi: max(lo, min(hi, v))
        if ny + nh <= ty - 4:                         # note above the target
            sx = clamp(tx + tw / 2, nx + 24, nx + nw - 24)
            p0, p3 = (sx, ny + nh), (clamp(sx, tx + 12, tx + tw - 12), ty - 6)
            k = max(30, (p3[1] - p0[1]) / 2)
            p1, p2 = (p0[0], p0[1] + k), (p3[0], p3[1] - k)
        elif ny >= ty + th + 4:                       # note below
            sx = clamp(tx + tw / 2, nx + 24, nx + nw - 24)
            p0, p3 = (sx, ny), (clamp(sx, tx + 12, tx + tw - 12), ty + th + 6)
            k = max(30, (p0[1] - p3[1]) / 2)
            p1, p2 = (p0[0], p0[1] - k), (p3[0], p3[1] + k)
        elif nx + nw <= tx:                           # note left of the target
            sy = clamp(ty + th / 2, ny + 16, ny + nh - 16)
            p0, p3 = (nx + nw, sy), (tx - 6, clamp(sy, ty + 8, ty + th - 8))
            k = max(30, (p3[0] - p0[0]) / 2)
            p1, p2 = (p0[0] + k, p0[1]), (p3[0] - k, p3[1])
        elif nx >= tx + tw:                           # note right of the target
            sy = clamp(ty + th / 2, ny + 16, ny + nh - 16)
            p0, p3 = (nx, sy), (tx + tw + 6, clamp(sy, ty + 8, ty + th - 8))
            k = max(30, (p0[0] - p3[0]) / 2)
            p1, p2 = (p0[0] - k, p0[1]), (p3[0] + k, p3[1])
        else:
            return                                    # a manual note over its target: no arrow
        self.draw_curve(p0, p1, p2, p3, sty, name)

    def highlight(self, a):
        s = self.svg
        sty = self.st["highlights"][a["style"]]
        x, y, w, h = self.bbox(a["target"])
        pad = 5
        s.rect(x - pad, y - pad, w + 2 * pad, h + 2 * pad, stroke=sty["color"], sw=sty["width"], rx=10,
               dash=sty.get("dash", ""))
        if a.get("label"):
            lw = s.fonts.width(a["label"], 15, bold=True) + 20
            lx, ly = self.label_spot((x - pad, y - pad, w + 2 * pad, h + 2 * pad), lw, 24, a["target"])
            s.pill(lx, ly, a["label"], 15, sty["color"], "#FFFFFF", pad=10, h=24)

    def tint(self, a):
        x, y, w, h = self.bbox(a["target"])
        color = self.c.get(a["color"], a["color"])
        self.svg.rect(x, y, w, h, fill=color, rx=6, opacity=a.get("opacity", 0.28))

    def badge(self, a):
        s = self.svg
        b = self.st["badges"][a["style"]]
        x, y, w, h = self.bbox(a["target"])
        bw = s.fonts.width(a["text"], 13, bold=True) + 18
        s.pill(max(x + w - bw - 8, 4), max(y - 12, self.top - 8), a["text"], 13, b["fill"], b["text"], pad=9, h=24)

    def arrow(self, a):
        s = self.svg
        sty = self.st["arrows"][a["style"]]
        ax, ay, aw, ah = self.bbox(a["from"])
        bx, by, bw, bh = self.bbox(a["to"])
        acy, bcy = ay + ah / 2, by + bh / 2
        loop = False
        if bx >= ax + aw - 4:            # target on the right
            p0, p3 = (ax + aw, acy), (bx - 4, bcy)
            k = max(60, (p3[0] - p0[0]) / 2)
            p1, p2 = (p0[0] + k, p0[1]), (p3[0] - k, p3[1])
        elif bx + bw <= ax + 4:          # target on the left
            p0, p3 = (ax, acy), (bx + bw + 4, bcy)
            k = max(60, (p0[0] - p3[0]) / 2)
            p1, p2 = (p0[0] - k, p0[1]), (p3[0] + k, p3[1])
        else:                            # same column: loop out on the right side
            p0, p3 = (ax + aw, acy), (bx + bw + 4, bcy)
            k = 26
            p1, p2 = (max(p0[0], p3[0]) + k, p0[1]), (max(p0[0], p3[0]) + k, p3[1])
            loop = True
        self.draw_curve(p0, p1, p2, p3, sty, a["style"], a.get("label"), loop)

    def draw_curve(self, p0, p1, p2, p3, sty, name, label=None, loop=False):
        s = self.svg
        path = f"M{p0[0]:.1f},{p0[1]:.1f} C{p1[0]:.1f},{p1[1]:.1f} {p2[0]:.1f},{p2[1]:.1f} {p3[0]:.1f},{p3[1]:.1f}"
        dash = f' stroke-dasharray="{sty["dash"]}"' if sty.get("dash") else ""
        s.add(f'<path d="{path}" fill="none" stroke="#FFFFFF" stroke-width="{sty["width"] + 3}" '
              f'stroke-linecap="round" opacity="0.8"/>')
        s.add(f'<path d="{path}" fill="none" stroke="{sty["color"]}" stroke-width="{sty["width"]}"{dash} '
              f'marker-end="url(#ah-{name})"/>')
        s.circle(p0[0], p0[1], 5, sty["color"])
        if label:
            t = 0.5
            mx = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0]
            my = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1]
            lw = s.fonts.width(label, 15, bold=True) + 20
            if loop:
                mx = min(p0[0], p3[0]) - lw / 2 - 8
            mx = min(max(mx, lw / 2 + 6), self.meta["width"] - lw / 2 - 6)
            s.rect(mx - lw / 2, my - 13, lw, 26, fill="#FFFFFF", stroke=sty["color"], sw=2, rx=13)
            s.text(mx, my + 5, label, size=15, bold=True, fill=sty["color"], anchor="middle")


    def refusals(self):
        """Mark every row whose transition a rule refused in this frame."""
        s = self.svg
        for rule_id, entity, ident, _ in self.ctx.refusals:
            hits = [f"row:{p['id']}:{ident}" for p in self.sc["layout"]["panels"] if p["entity"] == entity]
            hits = [k for k in hits if k in self.anchors]
            for key in hits:
                x, y, w, h = self.anchors[key]
                s.rect(x - 4, y - 4, w + 8, h + 8, stroke=self.c["refused"], sw=3, rx=8, dash="8 5")
            for key in hits[:1]:
                x, y, w, h = self.anchors[key]
                label = f"REFUSED: {rule_id}"
                lw = s.fonts.width(label, 14, bold=True) + 20
                lx, ly = self.label_spot((x, y, w, h), lw, 24, key)
                s.pill(lx, ly, label, 14, self.c["refused"], "#FFFFFF", pad=10, h=24)

    def markers(self):
        out = ["<defs>"]
        for name, sty in self.st["arrows"].items():
            out.append(f'<marker id="ah-{name}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" '
                       f'markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
                       f'fill="{sty["color"]}"/></marker>')
        out.append("</defs>")
        self.svg.add("".join(out))

    # ---- bars ----------------------------------------------------------------
    def title_bar(self):
        s, f = self.svg, self.frame
        W = self.meta["width"]
        th = self.meta.get("layout", {}).get("title_height", 84)
        s.rect(-FULL_BLEED, 0, W + 2 * FULL_BLEED, th, fill=self.c["title_bar"])
        badge = self.st["badges"][f["status"]["level"]]
        s.rect(24, 16, 96, th - 32, fill=badge["fill"], rx=10)
        s.text(72, th / 2 + 10, f["step"], size=26, bold=True, fill=badge["text"], anchor="middle", max_w=88,
               where="step chip")
        bw = s.fonts.width(badge["label"], 18, bold=True) + 28
        s.pill(W - 24 - bw, 12, badge["label"], 18, badge["fill"], badge["text"], pad=14, h=38)
        s.text(W - 24, th - 10, f"{self.index + 1} / {self.total}", size=14, fill=self.c["title_muted"],
               anchor="end")
        max_w = W - 24 - bw - 30 - 140
        s.text(140, 44, f["title"], size=32, bold=True, fill=self.c["title_text"], max_w=max_w, where="title")
        s.text(140, 72, f["status"].get("detail", ""), size=17, fill=self.c["title_muted"], max_w=max_w,
               where="status detail")

    def caption_bar(self):
        s = self.svg
        W, H = self.meta["width"], self.meta["height"]
        ch = self.meta.get("layout", {}).get("caption_height", 72)
        y0 = H - ch
        s.rect(-FULL_BLEED, y0, W + 2 * FULL_BLEED, ch, fill=self.c["caption_bar"])
        s.rect(-FULL_BLEED, y0, W + 2 * FULL_BLEED, 3, fill=self.c["panel_border"])
        lines = s.wrap(self.frame["caption"], W - 80, 28, where="caption",
                       max_lines=self.meta.get("layout", {}).get("caption_max_lines", 1))
        base = y0 + ch / 2 - (len(lines) - 1) * 18 + 10
        for i, line in enumerate(lines):
            s.text(40, base + i * 36, line, size=28, fill=self.c["caption_text"])

    # ---- whole frame ----------------------------------------------------------
    def render(self):
        s = self.svg
        W, H = self.meta["width"], self.meta["height"]
        self.markers()
        s.rect(-FULL_BLEED, 0, W + 2 * FULL_BLEED, H, fill=self.c["background"])
        if self.frame.get("summary"):
            self.summary()
        else:
            for p in self.sc["layout"]["panels"]:
                if self.frame.get("panels") and p["id"] not in self.frame["panels"]:
                    continue
                getattr(self, p["kind"] + "_panel")(p)
        anns = self.frame.get("annotations", [])
        self.reserve_targets()
        for kind in ("tint", "callout", "note", "before_after", "highlight", "arrow", "note_marks", "badge"):
            if kind == "note_marks":
                self.note_marks()
                continue
            for a in anns:
                if a["type"] == kind:
                    getattr(self, kind)(a)
        unknown = [a["type"] for a in anns if a["type"] not in ANNOTATIONS]
        if unknown:
            self.fail(f"unknown annotation types {unknown}; known: {list(ANNOTATIONS)}")
        self.refusals()
        self.title_bar()
        self.caption_bar()
        return s.render(W, H, margin=top_margin(self.meta), bg=self.c["background"], strip=self.c["title_bar"])


# =============================================================================
# 5. DRIVER
# =============================================================================

def find_tool(env, name):
    for cand in (os.environ.get(env), shutil.which(name), f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if cand and Path(cand).exists():
            return cand
    raise SystemExit(f"{name} not found; install it or set {env}")


def check_plan(plan):
    if not isinstance(plan, dict) or "scenario" not in plan or "frames" not in plan:
        raise ScenarioError("not a plan file: needs 'scenario' and 'frames' (run plan.py)")
    major = str(plan.get("plan_version", "")).split(".")[0]
    if major != PLAN_MAJOR:
        raise ScenarioError(f"plan_version {plan.get('plan_version')!r}: this renderer reads major {PLAN_MAJOR}")


def render_plan_svgs(plan):
    """Draw every frame of a plan and return [(frame_record, svg text)] without writing anything."""
    check_plan(plan)
    sc = plan["scenario"]
    by_id = {f["id"]: f for f in sc["frames"]}
    out = []
    for rec in plan["frames"]:
        out.append((rec, FrameRenderer(sc, by_id[rec["id"]], rec["index"], len(plan["frames"]),
                                       rec["state"], Ctx(rec)).render()))
    return out


def render_svgs(sc):
    """Convenience: plan in memory, then draw. [(frame_record, svg text)]. Writes nothing."""
    import plan as planner
    return render_plan_svgs(planner.build_plan(sc))


def render_plan(plan, out_dir, png=True):
    out_dir = Path(out_dir)
    svgs = render_plan_svgs(plan)
    svg_dir, png_dir = out_dir / "svg", out_dir / "png"
    for d in (svg_dir, png_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    rsvg = find_tool("RSVG_CONVERT", "rsvg-convert") if png else None
    meta = plan["scenario"]["meta"]
    bg = dict(DEFAULT_COLORS, **plan["scenario"]["style"].get("colors", {}))["background"]
    steps = {f["id"]: f["step"] for f in plan["scenario"]["frames"]}
    manifest = []
    for i, (rec, svg) in enumerate(svgs):
        name = f"{i + 1:03d}-{rec['id']}"
        svg_path = svg_dir / f"{name}.svg"
        svg_path.write_text(svg)
        png_path = png_dir / f"{name}.png"
        if png:
            subprocess.run([rsvg, "-w", str(meta["width"]), "-h", str(meta["height"]), "-b", bg,
                            "-o", str(png_path), str(svg_path)], check=True)
        item = {"id": rec["id"], "step": steps[rec["id"]], "png": str(png_path.relative_to(out_dir)),
                "seconds": rec["seconds"]}
        if rec.get("narration"):
            item["narration"] = rec["narration"]
        manifest.append(item)
    (out_dir / "frames.json").write_text(json.dumps({"meta": meta, "frames": manifest}, indent=2))
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--plan", type=Path)
    src.add_argument("--scenario", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-png", action="store_true", help="write the SVG files only")
    ap.add_argument("--check", action="store_true", help="draw in memory, write nothing")
    args = ap.parse_args()
    try:
        if args.plan:
            plan = json.loads(args.plan.read_text())
        else:
            import plan as planner
            plan = planner.build_plan(json.loads(args.scenario.read_text()))
        if args.check:
            n = len(render_plan_svgs(plan))
            print(f"layout OK: {n} frames drawn in memory")
            return 0
        manifest = render_plan(plan, args.out, png=not args.no_png)
    except Exception as e:      # ScenarioError here, or engine.ScenarioError from the plan stage
        if isinstance(e, (ScenarioError,)) or type(e).__name__ == "ScenarioError":
            print(f"RENDER ERROR: {e}", file=sys.stderr)
            return 1
        raise
    total = sum(f["seconds"] for f in manifest)
    print(f"rendered {len(manifest)} frames to {args.out} ({total:.0f} s before crossfades)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
