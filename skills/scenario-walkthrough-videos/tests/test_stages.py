"""Tests of the stage split (validate, plan, render, audio, assemble) and of the contract.

Run: python3 -m unittest discover -s <skill>/tests -v
The speech command is mocked (WALKTHROUGH_TTS): no Kokoro model is needed. The end-to-end tests are skipped
without ffmpeg and rsvg-convert.
"""
import ast
import json
import math
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

HERE = Path(__file__).absolute().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
import audio  # noqa: E402
import plan  # noqa: E402
import validate  # noqa: E402

HELPDESK = SKILL / "examples" / "helpdesk" / "scenario.json"
MINIMAL = SKILL / "examples" / "minimal" / "scenario.json"
SCHEMA = json.loads((SKILL / "reference" / "scenario.schema.json").read_text())

MOCK_TTS = '''#!/usr/bin/env python3
import json, math, os, struct, sys, wave
args = sys.argv[1:]
out = args[args.index("--out-dir") + 1]
with open(os.path.join(out, "calls.log"), "a") as log:
    for f in args:
        if f.endswith(".txt"):
            log.write(os.path.basename(f) + "\\n")
for f in args:
    if not f.endswith(".txt"):
        continue
    text = open(f).read()
    secs = len(text) / 15.0
    rate = 24000
    lead = int(0.25 * rate)
    n = int(secs * rate)
    frames = [0] * lead + [int(8000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(n)] + [0] * int(0.2 * rate)
    path = os.path.join(out, os.path.basename(f)[:-4] + ".wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(frames), *frames))
    print(json.dumps({"ok": True, "out": path}))
'''


def scenario(path=MINIMAL):
    return json.loads(Path(path).read_text())


def tools_present():
    return all(shutil.which(t) or os.path.exists(f"/opt/homebrew/bin/{t}") for t in ("ffmpeg", "rsvg-convert"))


def mock_tts(tmp):
    p = Path(tmp) / "mock_tts.py"
    p.write_text(MOCK_TTS)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def run(*cmd, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=e)


class Contract(unittest.TestCase):
    def check(self, sc, **kw):
        return validate.validate(sc, SCHEMA, **kw)

    def paths(self, res):
        return [e["path"] for e in res["errors"]]

    def test_examples_are_valid(self):
        for p in (HELPDESK, MINIMAL):
            res = self.check(scenario(p))
            self.assertTrue(res["ok"], res)

    def test_errors_carry_json_paths(self):
        sc = scenario()
        sc["frames"][1]["title"] = 5
        del sc["frames"][0]["caption"]
        sc["meta"]["extra"] = 1
        res = self.check(sc, force_mini=True)
        self.assertFalse(res["ok"])
        self.assertIn("$.frames[1].title", self.paths(res))
        self.assertIn("$.frames[0]", self.paths(res))
        self.assertIn("$.meta", self.paths(res))
        self.assertTrue(all(e["stage"] == "schema" for e in res["errors"]))

    def test_both_schema_validators_agree_on_the_paths(self):
        sc = scenario()
        sc["meta"]["audio"] = {"voice": "af_heart", "speed": 9}
        a = {e["path"] for e in self.check(sc, force_mini=True)["errors"]}
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            self.skipTest("jsonschema not installed")
        b = {e["path"] for e in self.check(sc)["errors"]}
        self.assertEqual(a, b)
        self.assertIn("$.meta.audio.speed", a)

    def test_reference_errors_are_all_reported_with_paths(self):
        sc = scenario()
        sc["frames"][0]["sources"] = ["NOPE"]
        sc["frames"][1]["status"]["level"] = "mystery"
        res = self.check(sc)
        self.assertIn("$.frames[0].sources[0]", self.paths(res))
        self.assertIn("$.frames[1].status.level", self.paths(res))

    def test_transition_error_names_the_transition(self):
        sc = scenario()
        sc["frames"][1]["transitions"][0]["set"] = {"state": "bogus"}
        res = self.check(sc)
        self.assertEqual(res["errors"][0]["path"], "$.frames[1].transitions[0]")
        self.assertEqual(res["errors"][0]["stage"], "semantic")

    def test_version_rules(self):
        sc = scenario()
        sc["schema_version"] = "2.0"
        self.assertIn("$.schema_version", self.paths(self.check(sc)))      # a major change is breaking
        sc["schema_version"] = "1.9"
        self.assertIn("newer", self.check(sc)["errors"][0]["message"])      # a newer minor needs a newer skill
        sc["schema_version"] = "1.0"
        self.assertTrue(self.check(sc)["ok"])                                 # an older minor stays valid
        self.assertTrue(any("1.1 feature" in w["message"] for w in self.check(sc)["warnings"]))
        del sc["schema_version"]
        self.assertTrue(self.check(sc)["ok"])
        self.assertTrue(any("missing" in w["message"] for w in self.check(sc)["warnings"]))
        sc["schema_version"] = "one"
        self.assertFalse(self.check(sc)["ok"])

    def test_old_scenario_without_new_fields_is_valid(self):
        sc = scenario(HELPDESK)
        sc.pop("schema_version", None)
        self.assertTrue(self.check(sc)["ok"])

    def test_narration_checks(self):
        for bad in ("Open corpus.py and read it.", "See src/agent/kg_build.py now.", "Use `code` here.",
                    "Go to https://example.com now.", "a | b", "x" * 601):
            errs, _ = validate.narration_problems(bad)
            self.assertTrue(errs, bad)
        errs, warns = validate.narration_problems("There are 5 chunks.")
        self.assertEqual(errs, [])
        self.assertTrue(warns)
        self.assertEqual(validate.narration_problems("There are five chunks, and the chunker is simple.")[0], [])
        sc = scenario()
        sc["frames"][0]["narration"] = "Read chunk.py."
        res = self.check(sc)
        self.assertIn("$.frames[0].narration", self.paths(res))
        sc["frames"][0]["narration"] = "There are 5 chunks."
        self.assertTrue(self.check(sc)["ok"])
        self.assertTrue(self.check(sc)["warnings"])

    def test_cli_json_output_and_exit_codes(self):
        ok = run(sys.executable, SCRIPTS / "validate.py", MINIMAL, "--json")
        self.assertEqual(ok.returncode, 0)
        self.assertTrue(json.loads(ok.stdout)["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            sc = scenario()
            sc["frames"][0]["narration"] = "There are 5 chunks."
            p = Path(tmp) / "s.json"
            p.write_text(json.dumps(sc))
            self.assertEqual(run(sys.executable, SCRIPTS / "validate.py", p).returncode, 0)
            strict = run(sys.executable, SCRIPTS / "validate.py", p, "--strict", "--json")
            self.assertEqual(strict.returncode, 1)
            self.assertFalse(json.loads(strict.stdout)["ok"])
            p.write_text("{not json")
            bad = run(sys.executable, SCRIPTS / "validate.py", p, "--json")
            self.assertEqual(bad.returncode, 1)
            self.assertEqual(json.loads(bad.stdout)["errors"][0]["stage"], "parse")


class Boundaries(unittest.TestCase):
    """No imports across the data / render line."""

    def imports(self, name):
        tree = ast.parse((SCRIPTS / f"{name}.py").read_text())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        return found

    def test_data_side_never_imports_the_render_side(self):
        render_side = {"render", "make_video", "audio", "join_parts"}
        for name in ("engine", "validate", "plan", "scenario_builder"):
            self.assertFalse(self.imports(name) & render_side, name)

    def test_render_side_reads_the_plan_only(self):
        self.assertFalse(self.imports("render") & {"engine", "validate", "audio", "make_video"})
        for name in ("make_video", "audio", "join_parts"):
            self.assertFalse(self.imports(name) & {"engine", "validate", "plan", "render"}, name)

    def test_scripts_use_only_the_standard_library(self):
        std = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
        local = {p.stem for p in SCRIPTS.glob("*.py")}
        for p in SCRIPTS.glob("*.py"):
            for mod in self.imports(p.stem) - local:
                if std is not None and mod != "jsonschema":      # validate.py uses it when installed
                    self.assertIn(mod, std, f"{p.name} imports {mod}")


class Plan(unittest.TestCase):
    def test_plan_alone(self):
        pl = plan.build_plan(scenario(HELPDESK))
        self.assertEqual(pl["plan_version"], plan.PLAN_VERSION)
        self.assertEqual(len(pl["frames"]), len(scenario(HELPDESK)["frames"]))
        json.dumps(pl)                                     # the plan is plain JSON
        self.assertIn("requests", pl["frames"][1]["state"]["rows"])
        self.assertTrue(any(f["added"] for f in pl["frames"]))

    def test_plan_carries_seconds_and_narration(self):
        sc = scenario()
        sc["frames"][1]["seconds"] = 9
        pl = plan.build_plan(sc)
        self.assertEqual([f["seconds"] for f in pl["frames"]], [4, 9])
        self.assertIn("narration", pl["frames"][0])

    def test_cli_writes_the_file_and_fails_on_a_bad_scenario(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan.json"
            res = run(sys.executable, SCRIPTS / "plan.py", MINIMAL, "-o", out)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(len(json.loads(out.read_text())["frames"]), 2)
            sc = scenario()
            sc["frames"][1]["transitions"][0]["set"] = {"state": "bogus"}
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps(sc))
            res = run(sys.executable, SCRIPTS / "plan.py", bad, "-o", Path(tmp) / "p2.json")
            self.assertEqual(res.returncode, 1)
            self.assertIn("PLAN ERROR", res.stderr)


class RenderAlone(unittest.TestCase):
    def test_render_from_a_plan_file_needs_no_data_side_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            pj = Path(tmp) / "plan.json"
            pj.write_text(json.dumps(plan.build_plan(scenario(HELPDESK))))
            code = ("import sys\nfor m in ('engine','plan','validate'): sys.modules[m] = None\n"
                    f"sys.path.insert(0, {str(SCRIPTS)!r})\nimport render\n"
                    f"sys.argv = ['render.py', '--plan', {str(pj)!r}, '--out', {tmp!r}, '--no-png']\n"
                    "sys.exit(render.main())")
            res = run(sys.executable, "-c", code)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(len(list((Path(tmp) / "svg").glob("*.svg"))), 10)
            fj = json.loads((Path(tmp) / "frames.json").read_text())
            self.assertEqual(len(fj["frames"]), 10)

    def test_render_check_from_a_scenario_file(self):
        res = run(sys.executable, SCRIPTS / "render.py", "--scenario", MINIMAL, "--out", "/nonexistent", "--check")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("layout OK", res.stdout)

    def test_render_rejects_a_plan_of_another_major_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            pl = plan.build_plan(scenario())
            pl["plan_version"] = "2.0"
            pj = Path(tmp) / "plan.json"
            pj.write_text(json.dumps(pl))
            res = run(sys.executable, SCRIPTS / "render.py", "--plan", pj, "--out", tmp, "--check")
            self.assertEqual(res.returncode, 1)
            self.assertIn("plan_version", res.stderr)

    def test_layout_problem_is_a_render_error_not_a_validate_error(self):
        sc = scenario()
        sc["frames"][0]["title"] = "T" * 90                # schema allows 90; at 32 px it cannot fit
        self.assertTrue(validate.validate(sc, SCHEMA)["ok"])
        res = run(sys.executable, SCRIPTS / "render.py", "--scenario", MINIMAL, "--out", "/nonexistent", "--check")
        self.assertEqual(res.returncode, 0)
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "b.json"
            bad.write_text(json.dumps(sc))
            res = run(sys.executable, SCRIPTS / "render.py", "--scenario", bad, "--out", tmp, "--check")
            self.assertEqual(res.returncode, 1)
            self.assertIn("RENDER ERROR", res.stderr)


class NoteFont(unittest.TestCase):
    def test_note_font_size_is_a_setting_and_changes_the_box(self):
        import render
        sc = scenario(HELPDESK)
        sc["frames"][1]["annotations"][0]["text"] = "word " * 14
        big = render.render_svgs(sc)[1][1]
        sc["meta"].setdefault("layout", {})["note_font_size"] = 18
        small = render.render_svgs(sc)[1][1]
        self.assertIn('font-size="21"', big)
        self.assertIn('font-size="18"', small)
        self.assertEqual(render.DEFAULT_NOTE_FONT, 21)

    def test_schema_bounds_the_setting(self):
        sc = scenario()
        sc["meta"]["layout"] = {"note_font_size": 60}
        self.assertIn("$.meta.layout.note_font_size", [e["path"] for e in validate.validate(sc, SCHEMA)["errors"]])


class Timing(unittest.TestCase):
    def test_duration_is_the_longer_of_hold_and_voice(self):
        frames = [{"id": "a", "seconds": 6}, {"id": "b", "seconds": 6}, {"id": "c", "seconds": 6}]
        rows, total = audio.plan_timeline(frames, {0: 2.0, 1: 10.0}, pause=0.6, xfade=0.5)
        self.assertEqual(rows[0]["seconds"], 6)                                   # short voice: hold wins
        self.assertAlmostEqual(rows[1]["seconds"], 0.1 + 10.0 + 0.6 + 0.5, 3)    # long voice: voice wins
        self.assertEqual(rows[2]["seconds"], 6)                                   # no narration: hold
        self.assertIsNone(rows[2]["audio_start"])

    def test_starts_follow_the_crossfade_and_audio_leads_by_a_tenth(self):
        frames = [{"id": "a", "seconds": 5}, {"id": "b", "seconds": 5}]
        rows, total = audio.plan_timeline(frames, {0: 1, 1: 1}, pause=0.6, xfade=0.5)
        self.assertEqual(rows[0]["start"], 0)
        self.assertAlmostEqual(rows[1]["start"], 4.5)
        for r in rows:
            self.assertAlmostEqual(r["audio_start"] - r["start"], audio.LEAD)
        self.assertAlmostEqual(total, 5 + 5 - 0.5)                                # sum minus one crossfade

    def test_voice_ends_before_the_next_frame_starts(self):
        frames = [{"id": "a", "seconds": 1}, {"id": "b", "seconds": 1}]
        rows, _ = audio.plan_timeline(frames, {0: 7.3, 1: 2.2}, pause=0.6, xfade=0.5)
        end = rows[0]["audio_start"] + 7.3
        self.assertLessEqual(end + 0.6, rows[1]["start"] + 1e-6)
        self.assertGreaterEqual(rows[0]["seconds"], 0.1 + 7.3 + 0.6 + 0.5 - 1e-6)

    def test_no_crossfade(self):
        rows, total = audio.plan_timeline([{"id": "a", "seconds": 3}, {"id": "b", "seconds": 3}], {1: 1}, 0.6, 0)
        self.assertEqual((rows[1]["start"], total), (3, 6))

    def test_trim_and_mix_place_the_voice_at_audio_start(self):
        rate = 24000
        pcm = struct.pack("<%dh" % (rate * 2), *([0] * rate + [8000] * (rate // 2) + [0] * (rate // 2)))
        trimmed = audio.trim_silence(pcm, rate)
        self.assertAlmostEqual(len(trimmed) / 2 / rate, 0.5 + 0.06, delta=0.01)
        self.assertEqual(audio.trim_silence(struct.pack("<10h", *([0] * 10)), rate), b"")
        with tempfile.TemporaryDirectory() as tmp:
            wp = Path(tmp) / "a.wav"
            with wave.open(str(wp), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(pcm)
            rows = [{"audio_start": 1.0}]
            out = Path(tmp) / "m.wav"
            audio.mix(rows, [wp], 4.0, out)
            with wave.open(str(out), "rb") as w:
                data = w.readframes(w.getnframes())
                self.assertAlmostEqual(w.getnframes() / rate, 4.0, places=2)
            samples = struct.unpack("<%dh" % (len(data) // 2), data)
            first = next(i for i, v in enumerate(samples) if v)
            self.assertAlmostEqual(first / rate, 1.0 + 0.03, delta=0.01)       # 0.03 s of margin kept before the voice

    def test_srt_has_one_cue_per_sentence_inside_the_frame(self):
        rows = [{"audio_start": 2.0, "narration_seconds": 6.0}]
        srt = audio.srt_text(rows, ["First sentence here. Second one is a bit longer."])
        self.assertIn("00:00:02,000 -->", srt)
        self.assertEqual(srt.count("-->"), 2)
        self.assertIn("00:00:08,000", srt)


class Cache(unittest.TestCase):
    def test_key_changes_with_text_voice_speed_language(self):
        k = audio.cache_key("hello", "af_heart", 1.0, "en-us")
        self.assertEqual(k, audio.cache_key("hello", "af_heart", 1.0, "en-us"))
        for other in (audio.cache_key("hello!", "af_heart", 1.0, "en-us"), audio.cache_key("hello", "am_adam", 1.0, "en-us"),
                      audio.cache_key("hello", "af_heart", 1.1, "en-us"), audio.cache_key("hello", "af_heart", 1.0, "en-gb")):
            self.assertNotEqual(k, other)

    def frames_json(self, tmp, texts, xfade=0.5, extra_meta=None):
        out = Path(tmp) / "out"
        out.mkdir(exist_ok=True)
        meta = {"crossfade_seconds": xfade}
        meta.update(extra_meta or {})
        frames = [{"id": f"f{i}", "png": "png/x.png", "seconds": 3, **({"narration": t} if t else {})}
                  for i, t in enumerate(texts)]
        (out / "frames.json").write_text(json.dumps({"meta": meta, "frames": frames}))
        return out

    def test_only_changed_narration_is_spoken_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            tts = mock_tts(tmp)
            out = self.frames_json(tmp, ["First text is here.", None, "Third text is here."])
            cache = Path(tmp) / "cache"
            env = {"WALKTHROUGH_TTS": tts}
            res = run(sys.executable, SCRIPTS / "audio.py", "--out", out, "--cache", cache, env=env)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("2 spoken, 0 from cache", res.stdout)
            log = (cache / "calls.log").read_text().split()
            self.assertEqual(len(log), 2)
            res = run(sys.executable, SCRIPTS / "audio.py", "--out", out, "--cache", cache, env=env)
            self.assertIn("0 spoken, 2 from cache", res.stdout)
            self.assertEqual(len((cache / "calls.log").read_text().split()), 2)       # no new calls
            out = self.frames_json(tmp, ["First text is here.", None, "Third text changed now."])
            res = run(sys.executable, SCRIPTS / "audio.py", "--out", out, "--cache", cache, env=env)
            self.assertIn("1 spoken, 1 from cache", res.stdout)
            self.assertEqual(len((cache / "calls.log").read_text().split()), 3)
            tl = json.loads((out / "audio" / "timeline.json").read_text())
            self.assertIsNone(tl["frames"][1]["audio_start"])
            self.assertTrue((out / "audio" / "narration.wav").exists())
            self.assertTrue((out / "audio" / "subtitles.srt").exists())

    def test_settings_come_from_meta_audio_and_the_command_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.frames_json(tmp, ["Some words."], extra_meta={"audio": {"voice": "am_adam", "speed": 1.2}})
            args = audio.argparse.Namespace(voice=None, speed=None, pause=None, language=None)
            cfg = audio.settings(json.loads((out / "frames.json").read_text())["meta"], args)
            self.assertEqual((cfg["voice"], cfg["speed"], cfg["pause_seconds"]), ("am_adam", 1.2, 0.6))
            args.voice = "bf_emma"
            self.assertEqual(audio.settings({"audio": {"voice": "am_adam"}}, args)["voice"], "bf_emma")
            with self.assertRaises(ValueError):
                audio.settings({"audio": {"language": "fr"}}, audio.argparse.Namespace(
                    voice=None, speed=None, pause=None, language=None))

    def test_no_narration_means_no_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.frames_json(tmp, [None, None])
            res = run(sys.executable, SCRIPTS / "audio.py", "--out", out)
            self.assertEqual(res.returncode, 0)
            self.assertIn("silent", res.stdout)
            self.assertFalse((out / "audio").exists())

    def test_missing_speech_tool_skips_with_a_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.frames_json(tmp, ["Some words."])
            res = run(sys.executable, SCRIPTS / "audio.py", "--out", out, "--cache", Path(tmp) / "c",
                      env={"WALKTHROUGH_TTS": "/no/such/tool"})
            self.assertEqual(res.returncode, audio.TOOLS_MISSING)
            self.assertIn("AUDIO SKIPPED", res.stderr)
            self.assertFalse((out / "audio").exists())

    def test_failing_speech_tool_skips_with_a_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.sh"
            bad.write_text("#!/bin/sh\necho '{\"ok\": false, \"error\": \"no model\"}'\nexit 1\n")
            bad.chmod(0o755)
            out = self.frames_json(tmp, ["Some words."])
            res = run(sys.executable, SCRIPTS / "audio.py", "--out", out, "--cache", Path(tmp) / "c",
                      env={"WALKTHROUGH_TTS": str(bad)})
            self.assertEqual(res.returncode, audio.TOOLS_MISSING)
            self.assertIn("no model", res.stderr)


def duration(path):
    res = run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path,
              env={"PATH": os.environ["PATH"] + ":/opt/homebrew/bin"})
    return float(res.stdout.strip())


def streams(path):
    res = run("ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path,
              env={"PATH": os.environ["PATH"] + ":/opt/homebrew/bin"})
    return sorted(res.stdout.split())


@unittest.skipUnless(tools_present(), "ffmpeg and rsvg-convert are needed")
class EndToEnd(unittest.TestCase):
    def build(self, scenario_path, out, *opts, env=None):
        e = {"PATH": os.environ["PATH"] + ":/opt/homebrew/bin"}
        e.update(env or {})
        return run("bash", SCRIPTS / "build.sh", scenario_path, out, *opts, env=e)

    def test_audio_build_with_a_mocked_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            res = self.build(MINIMAL, out, "--audio", env={"WALKTHROUGH_TTS": mock_tts(tmp)})
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            mp4 = out / "minimal.mp4"
            self.assertEqual(streams(mp4), ["audio", "video"])
            tl = json.loads((out / "audio" / "timeline.json").read_text())
            self.assertAlmostEqual(duration(mp4), tl["total_seconds"], delta=0.2)
            self.assertTrue((out / "minimal.srt").exists())
            self.assertTrue((out / "contact_sheet.png").exists())
            for r in tl["frames"]:
                self.assertGreaterEqual(r["seconds"], r["hold"])

    def test_default_build_is_silent_even_with_narration(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            res = self.build(MINIMAL, out)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(streams(out / "minimal.mp4"), ["video"])
            self.assertFalse((out / "audio").exists())

    def test_audio_requested_but_no_narration_builds_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            res = self.build(HELPDESK, out, "--audio")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("silent", res.stdout)
            self.assertEqual(streams(out / "helpdesk-walkthrough.mp4"), ["video"])

    def test_audio_requested_but_tool_missing_builds_silent_with_a_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            res = self.build(MINIMAL, out, "--audio", env={"WALKTHROUGH_TTS": "/no/such/tool"})
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("AUDIO SKIPPED", res.stderr)
            self.assertEqual(streams(out / "minimal.mp4"), ["video"])

    def test_stages_run_alone_on_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            env = {"PATH": os.environ["PATH"] + ":/opt/homebrew/bin", "WALKTHROUGH_TTS": mock_tts(tmp)}
            steps = [("validate.py", MINIMAL), ("plan.py", MINIMAL, "-o", out / "plan.json"),
                     ("render.py", "--plan", out / "plan.json", "--out", out),
                     ("audio.py", "--out", out), ("make_video.py", "--out", out, "--timeline", out / "audio" / "timeline.json")]
            for step in steps:
                res = run(sys.executable, SCRIPTS / step[0], *step[1:], env=env)
                self.assertEqual(res.returncode, 0, f"{step[0]}: {res.stderr}")
            self.assertTrue((out / "minimal.mp4").exists())

    def test_join_parts_shifts_the_subtitles(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"WALKTHROUGH_TTS": mock_tts(tmp)}
            outs = []
            for i in range(2):
                out = Path(tmp) / f"p{i}"
                self.assertEqual(self.build(MINIMAL, out, "--audio", env=env).returncode, 0)
                outs.append(out / "minimal.mp4")
            joined, srt = Path(tmp) / "j.mp4", Path(tmp) / "j.srt"
            res = run(sys.executable, SCRIPTS / "join_parts.py", "--out", joined, "--srt", srt, *outs,
                      env={"PATH": os.environ["PATH"] + ":/opt/homebrew/bin"})
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertAlmostEqual(duration(joined), 2 * duration(outs[0]), delta=0.3)
            text = srt.read_text()
            blocks = text.strip().split("\n\n")
            n = len(blocks) // 2
            self.assertEqual(blocks[-1].split("\n")[0], str(2 * n))      # cues renumbered across parts
            start = blocks[n].split("\n")[1].split(" --> ")[0]           # first cue of the second part
            secs = int(start[3:5]) * 60 + int(start[6:8]) + int(start[9:]) / 1000
            self.assertGreater(secs, duration(outs[0]))


if __name__ == "__main__":
    unittest.main()
