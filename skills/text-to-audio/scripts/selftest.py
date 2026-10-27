"""Self-test: convert a small Markdown file and a small text file to audio and check the results.
Prints one JSON line with "selftest": "pass" or "fail". Exit 0 on a pass."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MD = r"""# Self Test

This is **bold** text with a [link](https://example.com) and inline math $x_i^2$, e.g. a square.

## Second Part

- One item.
- Another item.

$$
\frac{1}{\sqrt{2}}
$$

| A | B |
|---|---|
| 1 | 2 |

```python
print("hi")
```
"""
TXT = """PLAIN TEXT TEST
===============

A plain text paragraph that is wrapped
over two lines.
"""


def main():
    import soundfile  # noqa: F401  (the venv is complete)
    checks = {}
    with tempfile.TemporaryDirectory() as d:
        md, txt = os.path.join(d, "selftest.md"), os.path.join(d, "selftest.txt")
        open(md, "w").write(MD)
        open(txt, "w").write(TXT)
        r = subprocess.run([sys.executable, os.path.join(HERE, "text_to_audio.py"), md, txt, "--out-dir", d],
                           capture_output=True, text=True)
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        checks["exit_0"] = r.returncode == 0
        checks["one_json_line_per_input"] = len(lines) == 2
        res = [json.loads(l) for l in lines] if checks["one_json_line_per_input"] else [{}, {}]
        m, t = res
        checks["markdown_ok"] = bool(m.get("ok"))
        checks["markdown_chapters"] = m.get("chapters") == 2
        checks["markdown_audio"] = m.get("audio_seconds", 0) > 5
        checks["math_found"] = m.get("inline_math") == 1 and m.get("display_math") == 1
        checks["table_and_code_found"] = m.get("tables") == 1 and m.get("code_blocks") == 1
        checks["text_ok"] = bool(t.get("ok")) and t.get("audio_seconds", 0) > 1
        out = m.get("out", "")
        checks["m4a_written"] = bool(out) and os.path.exists(out) and os.path.getsize(out) > 10000
        r2 = subprocess.run([sys.executable, os.path.join(HERE, "text_to_audio.py"), md, "--out-dir", d,
                             "--dry-run"], capture_output=True, text=True)
        speech = open(os.path.join(d, "selftest.speech.txt")).read() if r2.returncode == 0 else ""
        checks["speech_text_clean"] = (all(w in speech for w in ("x sub i squared", "for example", "square root"))
                                       and not any(ch in speech for ch in "$*|#`\\"))
    ok = all(checks.values())
    info = {"selftest": "pass" if ok else "fail", "checks": checks}
    if not ok:
        info["stderr"] = r.stderr[-800:]
        info["results"] = res
    print(json.dumps(info))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
