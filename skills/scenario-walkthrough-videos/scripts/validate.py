#!/usr/bin/env python3
"""Stage 1, validate: check a scenario JSON against the contract. Reports every problem with a JSON path.

Usage: validate.py <scenario.json> [schema.json] [--mini] [--schema-only] [--json] [--strict]

Data side only: standard library, imports engine.py (the transition engine) and nothing from the renderer.
Layout problems (text fit, overlap, no room for a note) are found by `render.py --check`, not here.

1. Schema. Standard library only: when the `jsonschema` package is installed it is used, else the small
   validator below (`--mini` forces it). The small validator covers the keywords the schema uses: type
   (a name or a list), enum, const, required, properties, additionalProperties, items, minItems, maxItems,
   minLength, maxLength, pattern, minimum, maximum, $ref, allOf, if/then. Any other keyword in the schema
   is an error, so a schema edit cannot silently weaken the check.
2. Version: `schema_version` ("MAJOR.MINOR", default "1.0") against the schema's `x-version`: same major,
   minor not newer than the schema's.
3. References (engine.py): unique ids, cited sources, known styles, panel regions, notes, summary statuses;
   every one is reported. Then every transition in order with its fields, foreign keys and declared rules;
   the first failing transition is reported (later states depend on it).
4. Narration: text a voice cannot say well. Errors: a path, URL, backtick, table or code symbol, over 600
   characters. Warnings: digits (write numbers in words). `--strict` turns warnings into errors.

Output: one line per problem, `ERROR <json path>: <message>` on stderr; `--json` prints one JSON object on stdout
instead ({"ok", "errors": [{"path", "message", "stage"}], "warnings": [...], "frames", "schema_version"}).
Exit code 0 = valid, 1 = invalid, 2 = usage.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).absolute().parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402
DEFAULT_SCHEMA = HERE.parent / "reference" / "scenario.schema.json"

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
_KNOWN = {
    "$schema", "$id", "title", "description", "definitions", "type", "enum", "const",
    "required", "properties", "additionalProperties", "items", "minItems", "maxItems",
    "minLength", "maxLength", "pattern", "minimum", "maximum", "$ref", "allOf", "if", "then", "x-version",
}


def _is_type(value, name):
    if isinstance(name, list):
        return any(_is_type(value, n) for n in name)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


class MiniValidator:
    def __init__(self, root):
        self.root = root

    def lookup_ref(self, ref):
        if not ref.startswith("#/"):
            raise ValueError(f"only local $ref is supported: {ref}")
        node = self.root
        for part in ref[2:].split("/"):
            node = node[part]
        return node

    def errors(self, inst, schema, path="$"):
        out = []
        unknown = set(schema) - _KNOWN
        if unknown:
            out.append(f"{path}: schema uses unsupported keywords {sorted(unknown)}")
        if "$ref" in schema:
            out += self.errors(inst, self.lookup_ref(schema["$ref"]), path)
        if "type" in schema and not _is_type(inst, schema["type"]):
            return out + [f"{path}: expected {schema['type']}, got {type(inst).__name__}"]
        if "enum" in schema and inst not in schema["enum"]:
            out.append(f"{path}: {inst!r} not in {schema['enum']}")
        if "const" in schema and inst != schema["const"]:
            out.append(f"{path}: {inst!r} != const {schema['const']!r}")
        if isinstance(inst, str):
            if "minLength" in schema and len(inst) < schema["minLength"]:
                out.append(f"{path}: shorter than {schema['minLength']}")
            if "maxLength" in schema and len(inst) > schema["maxLength"]:
                out.append(f"{path}: {len(inst)} chars, longer than {schema['maxLength']}: {inst!r}")
            if "pattern" in schema and not re.search(schema["pattern"], inst):
                out.append(f"{path}: {inst!r} does not match {schema['pattern']}")
        if _is_type(inst, "number"):
            if "minimum" in schema and inst < schema["minimum"]:
                out.append(f"{path}: {inst} < minimum {schema['minimum']}")
            if "maximum" in schema and inst > schema["maximum"]:
                out.append(f"{path}: {inst} > maximum {schema['maximum']}")
        if isinstance(inst, dict):
            for key in schema.get("required", []):
                if key not in inst:
                    out.append(f"{path}: missing required '{key}'")
            props = schema.get("properties", {})
            for key, val in inst.items():
                if key in props:
                    out += self.errors(val, props[key], f"{path}.{key}")
                elif "additionalProperties" in schema:
                    extra = schema["additionalProperties"]
                    if extra is False:
                        out.append(f"{path}: unexpected property '{key}'")
                    elif isinstance(extra, dict):
                        out += self.errors(val, extra, f"{path}.{key}")
        if isinstance(inst, list):
            if "minItems" in schema and len(inst) < schema["minItems"]:
                out.append(f"{path}: fewer than {schema['minItems']} items")
            if "maxItems" in schema and len(inst) > schema["maxItems"]:
                out.append(f"{path}: more than {schema['maxItems']} items")
            if "items" in schema:
                for i, item in enumerate(inst):
                    out += self.errors(item, schema["items"], f"{path}[{i}]")
        for sub in schema.get("allOf", []):
            out += self.errors(inst, sub, path)
        if "if" in schema and not self.errors(inst, schema["if"], path):
            out += self.errors(inst, schema.get("then", {}), path)
        return out


def _path(parts):
    out = "$"
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else f".{p}"
    return out


def schema_errors(scenario, schema, force_mini=False):
    """Return a list of (json_path, message). Empty means valid."""
    if not force_mini:
        try:
            import jsonschema
        except ImportError:
            force_mini = True
    if force_mini:
        out = []
        for e in MiniValidator(schema).errors(scenario, schema):
            path, _, msg = e.partition(": ")
            out.append((path, msg))
        return out
    validator = jsonschema.Draft7Validator(schema)
    return sorted(((_path(e.absolute_path), e.message) for e in validator.iter_errors(scenario)),
                  key=lambda x: x[0])


def version_errors(scenario, schema):
    want = schema.get("x-version", "1.0")
    got = scenario.get("schema_version", "1.0")
    try:
        gm, gn = (int(x) for x in got.split("."))
    except ValueError:
        return [("$.schema_version", f"{got!r} is not MAJOR.MINOR")]
    wm, wn = (int(x) for x in want.split("."))
    if gm != wm:
        return [("$.schema_version", f"major version {gm} is not supported (this contract is {want}); "
                 f"a major change is breaking, so the scenario must be migrated")]
    if gn > wn:
        return [("$.schema_version", f"minor version {got} is newer than this contract ({want}); "
                 f"update the skill or produce {want}")]
    return []


_PATH_RE = re.compile(r"(?:[\w.-]+/)+[\w.-]+|\b[\w-]+\.(?:py|md|json|jsonl|txt|sh|yaml|yml|js|html|csv|toml)\b")
_BAD_CHARS = set("`|{}[]<>=_#\\~^*")


def narration_problems(text):
    """(errors, warnings) of one narration text: what a voice cannot say well."""
    errs, warns = [], []
    if len(text) > 600:
        errs.append(f"{len(text)} characters; keep a frame's narration under 600 (about 40 seconds)")
    if "http://" in text or "https://" in text:
        errs.append("contains a URL")
    m = _PATH_RE.search(text)
    if m:
        errs.append(f"contains a path or file name ({m.group(0)!r}); say what it is instead")
    bad = sorted(c for c in set(text) if c in _BAD_CHARS)
    if bad:
        errs.append(f"contains characters a voice cannot say: {' '.join(bad)} (code, table or markup)")
    if "\n" in text or "   " in text:
        errs.append("contains a line break or a column gap (a table or a listing)")
    if re.search(r"\d", text):
        warns.append("contains digits; write numbers in words (for example 'forty two')")
    return errs, warns


def narration_issues(scenario):
    errs, warns = [], []
    for i, f in enumerate(scenario["frames"]):
        if "narration" in f:
            e, w = narration_problems(f["narration"])
            errs += [(f"$.frames[{i}].narration", m) for m in e]
            warns += [(f"$.frames[{i}].narration", m) for m in w]
    return errs, warns


def semantic_errors(scenario):
    """References, then transitions with rules. [(json_path, message)]."""
    errs = engine.reference_errors(scenario)
    if errs:
        return errs
    try:
        engine.compute_frames(scenario)
    except engine.ScenarioError as e:
        return [(e.path or "$.initial_state", e.message)]
    return []


MARGIN_FLOOR_PER_720 = 24     # px of top margin per 720 px of frame height, below which the validator warns


def validate(scenario, schema, force_mini=False, schema_only=False):
    """The whole check as data: {"ok", "errors": [...], "warnings": [...]}; each item has path, message, stage."""
    errors, warnings = [], []

    def add(stage, items, into):
        into += [{"path": p, "message": m, "stage": stage} for p, m in items]

    add("schema", schema_errors(scenario, schema, force_mini), errors)
    if not errors:
        add("version", version_errors(scenario, schema), errors)
        if "schema_version" not in scenario:
            warnings.append({"path": "$.schema_version", "stage": "version",
                             "message": "missing; read as 1.0. Set it to the contract version you produce"})
        elif scenario["schema_version"] == "1.0" and any("narration" in f for f in scenario["frames"]):
            warnings.append({"path": "$.schema_version", "stage": "version",
                             "message": "narration is a 1.1 feature; set schema_version to 1.1"})
        elif scenario["schema_version"] in ("1.0", "1.1") and any(
                q.get("kind") == "graph" for q in scenario.get("layout", {}).get("panels", [])):
            warnings.append({"path": "$.schema_version", "stage": "version",
                             "message": "the graph panel is a 1.2 feature; set schema_version to 1.2"})
        ver = scenario.get("schema_version", "1.0")
        lay = scenario.get("meta", {}).get("layout", {})
        if "top_margin" in lay:
            if ver in ("1.0", "1.1", "1.2"):
                warnings.append({"path": "$.meta.layout.top_margin", "stage": "version",
                                 "message": "top_margin is a 1.3 feature; set schema_version to 1.3"})
            floor = round(scenario["meta"]["height"] * MARGIN_FLOOR_PER_720 / 720)
            if lay["top_margin"] < floor:
                warnings.append({"path": "$.meta.layout.top_margin", "stage": "layout",
                                 "message": f"{lay['top_margin']} px is below {floor} px ({MARGIN_FLOOR_PER_720} px per 720 px of "
                                            f"height): the player overlay (window buttons, title) may cover what is drawn at the top"})
        if not schema_only and not errors:
            add("semantic", semantic_errors(scenario), errors)
            ne, nw = narration_issues(scenario)
            add("narration", ne, errors)
            add("narration", nw, warnings)
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def main(argv):
    flags = {a for a in argv[1:] if a.startswith("--")}
    args = [a for a in argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    scenario_path = Path(args[0])
    schema_path = Path(args[1]) if len(args) > 1 else DEFAULT_SCHEMA
    try:
        scenario = json.loads(scenario_path.read_text())
    except (OSError, ValueError) as e:
        res = {"ok": False, "errors": [{"path": "$", "message": f"cannot read JSON: {e}", "stage": "parse"}],
               "warnings": []}
        scenario = None
    else:
        schema = json.loads(schema_path.read_text())
        res = validate(scenario, schema, "--mini" in flags, "--schema-only" in flags)
    if "--strict" in flags and res["warnings"]:
        res["errors"] += res["warnings"]
        res["warnings"] = []
        res["ok"] = False
    if scenario is not None:
        res["frames"] = len(scenario.get("frames", [])) if isinstance(scenario, dict) else 0
        res["schema_version"] = scenario.get("schema_version", "1.0") if isinstance(scenario, dict) else None
    if "--json" in flags:
        print(json.dumps(res, indent=1))
    else:
        for w in res["warnings"]:
            print(f"WARNING {w['path']}: {w['message']}", file=sys.stderr)
        for e in res["errors"]:
            print(f"ERROR {e['path']}: {e['message']}", file=sys.stderr)
        if res["ok"]:
            print(f"OK: {scenario_path.name} ({res['frames']} frames, schema {res['schema_version']}, "
                  f"{len(res['warnings'])} warnings)")
        else:
            print(f"{len(res['errors'])} error(s) in {scenario_path}", file=sys.stderr)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
