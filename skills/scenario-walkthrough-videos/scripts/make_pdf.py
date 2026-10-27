#!/usr/bin/env python3
"""Stage 4c, slides: one PDF of the frames, one frame per page, next to the video.

Usage: make_pdf.py --out <dir> [--no-vector]

Reads <out>/frames.json and <out>/svg/NNN-<id>.svg (render.py wrote both). The page is the frame at its full size
(1920 x 1080 px; rsvg-convert makes the page 1440 x 810 pt, the same shape), with the blank top strip
and the bars exactly as in the video. Output: <out>/<meta.output_name or "walkthrough">.pdf.

Method, with tools the build already needs: `rsvg-convert --format=pdf` draws all the SVG files into one multi-page
vector PDF (text stays text). With --no-vector, or if rsvg-convert cannot do it, Pillow (when installed) saves the
final frame PNGs as a PDF, one per page. With neither, the script says so and exits 3 (the build goes on).
Prints one line: the path and the page count. Exit 0 = written, 1 = error, 3 = no tool.
"""
import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_rsvg():
    for cand in (os.environ.get("RSVG_CONVERT"), shutil.which("rsvg-convert"), "/opt/homebrew/bin/rsvg-convert",
                 "/usr/local/bin/rsvg-convert"):
        if cand and Path(cand).exists():
            return cand
    return None


def page_count(path):
    """Pages according to pdfinfo when it is installed (poppler), else None (rsvg-convert writes one page per SVG)."""
    exe = shutil.which("pdfinfo") or ("/opt/homebrew/bin/pdfinfo" if Path("/opt/homebrew/bin/pdfinfo").exists() else None)
    if not exe:
        return None
    for line in subprocess.run([exe, str(path)], capture_output=True, text=True).stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return None


def make_pdf(out_dir, vector=True):
    out_dir = Path(out_dir)
    data = json.loads((out_dir / "frames.json").read_text())
    meta, frames = data["meta"], data["frames"]
    pdf = out_dir / f"{meta.get('output_name', 'walkthrough')}.pdf"
    svgs = [out_dir / "svg" / (Path(f["png"]).stem + ".svg") for f in frames]
    pngs = [out_dir / f["png"] for f in frames]
    rsvg = find_rsvg() if vector else None
    if rsvg and all(p.exists() for p in svgs):
        res = subprocess.run([rsvg, "--format=pdf", "-o", str(pdf)] + [str(p) for p in svgs],
                             capture_output=True, text=True)
        if res.returncode == 0 and pdf.exists() and page_count(pdf) in (None, len(frames)):
            return pdf, len(frames), "rsvg-convert (vector)"
    try:                                    # optional, loaded by name so the scripts stay standard-library only
        Image = importlib.import_module("PIL.Image")
    except ImportError:
        return None, 0, None
    imgs = [Image.open(p).convert("RGB") for p in pngs]
    imgs[0].save(pdf, "PDF", save_all=True, append_images=imgs[1:], resolution=96.0)
    return pdf, len(imgs), "Pillow (PNG pages)"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True, help="the folder render.py wrote")
    ap.add_argument("--no-vector", action="store_true", help="use the PNG frames (Pillow) instead of rsvg-convert")
    args = ap.parse_args()
    pdf, n, how = make_pdf(args.out, vector=not args.no_vector)
    if pdf is None:
        print("PDF SKIPPED: neither rsvg-convert nor Pillow is available", file=sys.stderr)
        return 3
    print(f"pdf: {pdf} ({n} pages, {how})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
