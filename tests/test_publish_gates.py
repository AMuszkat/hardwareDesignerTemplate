#!/usr/bin/env python3
"""Three publisher gates, unit-tested without a board:

  1  a Decided row's id in net text ('by D83') is not reported as a
     designator with no COMPONENTS line; a real stray designator still is;
  2  a layout row is published wrapped at LAYOUT_WRAP, continuation lines
     indented under its text, so tools/review/measure.py's row parser still
     reads it as one row and a grep returns a line, not the whole rule;
  3  a drawing that is new or changed since the published copy does not
     publish without "checked": {"snapshot", "gates": "PASS"}, nor when the
     snapshot it names is not the board's newest PCB snapshot; an unchanged
     published drawing needs nothing.

    python3 tests/test_publish_gates.py

Needs no KiCad. Exit 0 pass / 1 fail; the last stdout line is PASS / FAIL.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publish_t", ROOT / "tools" / "handoff" / "publish.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
sys.path.insert(0, str(ROOT / "tools" / "review"))
import types  # noqa: E402
_pn = types.ModuleType("pcbnew")                                # layout_rows needs no KiCad
_pn.ToMM = lambda v: v
sys.modules.setdefault("pcbnew", _pn)
import measure  # noqa: E402  (layout_rows, the row parser the review reads 10_layout.md with)

fails = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + name + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


def board(tmp, **attrs):
    b = P.Board.__new__(P.Board)
    b.id, b.board = "board9", "board9_test"
    b.handoff = os.path.join(tmp, "kicad_project", b.board, "handoff")
    data = dict(attrs)
    b.get = lambda k, d=None: data.get(k, d)
    return b


with tempfile.TemporaryDirectory() as tmp:
    P.ROOT = tmp
    os.makedirs(os.path.join(tmp, "design"))
    Path(tmp, "design", "STATUS.md").write_text(
        "| id | date | decision |\n|---|---|---|\n| D83 | 2026-11-11 | **X** |\n| D92 | 2026-11-12 | **Y** |\n")

    # 1 -- decision ids
    check("decision ids read from STATUS", P._decision_ids() == {"D83", "D92"}, str(P._decision_ids()))
    stray = P._stray_designators("U1 pin 3 R1 routed by D83 and D92; R7 pulls it up; D5 clamps it",
                                 {"U1", "R1"} | P._decision_ids())
    check("no decision id reported", "D83" not in stray and "D92" not in stray, str(stray))
    check("stray designators still reported", stray == ["D5", "R7"], str(stray))

    # 2 -- layout wrapping
    long_rule = ("A rule " + "with many words and DRAM_CA0_A, In1-In6 and 0.2032 mm, " * 60).strip()
    b = board(tmp, LAYOUT=[("Short rule.", ["src1"]), (long_rule, ["src2", "src3"])])
    md = b.layout_md()
    lines = [x for x in md.splitlines() if x[:1].isdigit() or x.startswith(" ")]   # the rows, not the intro
    check("no row line over the wrap width", len(lines) > 3 and max(len(x) for x in lines) <= P.LAYOUT_WRAP,
          str(max(len(x) for x in lines)))
    check("hyphenated names kept whole", "In1-In6" in md and "DRAM_CA0_A" in md)
    hd = Path(b.handoff)
    hd.mkdir(parents=True)
    (hd / "10_layout.md").write_text(md)
    rows = measure.layout_rows(hd.parent)
    check("measure.py reads two rows", [r[0] for r in rows] == [1, 2], str([r[0] for r in rows]))
    check("row 2 reads back whole", rows and " ".join(rows[1][1].split()).startswith(" ".join(long_rule.split())[:200]))

    # 3 -- drawings
    dsrc = Path(tmp, "tools", "handoff", "drawings")
    dsrc.mkdir(parents=True)
    rel = "tools/handoff/drawings/d.json"

    def refused(obj, publish_copy=None):
        Path(tmp, rel).write_text(json.dumps(obj))
        pub = hd / "drawings" / "d.json"
        if publish_copy is not None:
            pub.parent.mkdir(exist_ok=True)
            pub.write_text(publish_copy)
        elif pub.exists():
            pub.unlink()
        try:
            board(tmp)._drawing_checked("d.json", rel)
            return None
        except P.PublishError as e:
            return str(e)

    check("new drawing without proof refused", refused({"what": "x"}) is not None)
    check("proof without PASS refused", refused({"checked": {"snapshot": "RV9-pcb", "gates": "FAIL"}}) is not None)
    check("new drawing with proof, no snapshots here, publishes",
          refused({"checked": {"snapshot": "RV9-pcb", "gates": "PASS"}}) is None)
    snaps = Path(tmp, "out", "reviews", "board9_test")
    (snaps / "RV9-pcb").mkdir(parents=True)
    time.sleep(0.05)
    (snaps / "RV12-pcb").mkdir()
    (snaps / "RV13-schematic").mkdir()
    msg = refused({"checked": {"snapshot": "RV9-pcb", "gates": "PASS"}})
    check("proof on an older snapshot refused", msg is not None and "RV12-pcb" in msg, str(msg))
    check("proof on the newest PCB snapshot publishes",
          refused({"checked": {"snapshot": "RV12-pcb", "gates": "PASS"}}) is None)
    same = json.dumps({"what": "old, published before the gate"})
    check("unchanged published drawing needs no proof", refused(json.loads(same), publish_copy=same) is None)

print("PASS" if not fails else "FAIL")
sys.exit(1 if fails else 0)
