"""Check rendered text bounds and text collisions on every design sheet.

Uses the installed Inkscape renderer, not character-count estimates. SVGs
are passed on stdin; IDs are added only to the in-memory copy. This also
works with snap Inkscape, whose /tmp is private. No outputs are modified.
This supplements Sheet.lint(): it does not prove electrical connectivity,
detect text/wire crossings, or replace visual review.

    python tools/diagrams/check_diagrams.py
    python tools/diagrams/check_diagrams.py /absolute/path/to/diagram.svg
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
SVG = "{http://www.w3.org/2000/svg}"


def text_problems(labels, bounds, canvas, tolerance=0.8):
    """Compare rendered glyph rectangles, allowing subpixel rounding."""
    problems = []
    left, top, width, height = canvas
    measured = []
    for key, label in labels.items():
        if key not in bounds:
            problems.append(f"unmeasured text: {label}")
            continue
        x, y, w, h = bounds[key]
        if (x < left - tolerance or y < top - tolerance
                or x + w > left + width + tolerance
                or y + h > top + height + tolerance):
            problems.append(f"text off canvas: {label}")
        for other, (ox, oy, ow, oh) in measured:
            if (min(x + w, ox + ow) - max(x, ox) > tolerance
                    and min(y + h, oy + oh) - max(y, oy) > tolerance):
                problems.append(f"text overlap: {other} <-> {label}")
        measured.append((label, (x, y, w, h)))
    return problems


def check(path, inkscape):
    root = ET.parse(path).getroot()
    canvas = tuple(map(float, root.attrib["viewBox"].split()))
    labels = {}
    # Replace all IDs in the temporary copy to avoid collisions. Existing
    # marker references need not resolve: only text geometry is queried.
    for i, element in enumerate(root.iter()):
        key = f"review_{i}"
        element.set("id", key)
        if element.tag == SVG + "text":
            label = "".join(element.itertext()).strip()
            if label:
                labels[key] = label
    result = subprocess.run([inkscape, "--pipe", "--query-all"],
                            input=ET.tostring(root, encoding="unicode"),
                            text=True, capture_output=True, check=True,
                            timeout=60)
    bounds = {}
    for line in result.stdout.splitlines():
        fields = line.split(",")
        if len(fields) == 5 and fields[0] in labels:
            bounds[fields[0]] = tuple(map(float, fields[1:]))
    return text_problems(labels, bounds, canvas)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    inkscape = shutil.which("inkscape")
    if not inkscape:
        parser.error("Inkscape is required for rendered text checks")
    paths = args.paths or sorted((ROOT / "design" / "diagrams").glob("*.svg"))
    failed = False
    for path in paths:
        problems = check(path, inkscape)
        for problem in problems:
            print(f"{path.name}: {problem}")
        if not problems:
            print(f"{path.name}: rendered text OK")
        failed |= bool(problems)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())