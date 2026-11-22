#!/usr/bin/env python3
"""Tests for areaclear.py without KiCad:  python3 tools/route/test_areaclear.py  (or pytest).

The synthetic area is board1's case: a rule relaxes clearance to 0.1016 inside a box; past its
edge two SE_50 lines need 0.2032.  One pair crosses the edge at 45 degrees still 0.1035 apart (KiCad's
DRC passes it: both segments intersect the area); one pair opens inside the area and is broken at the
edge (no breach)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import areaclear as AC  # noqa: E402

DRU = """(version 1)
# >>> board_setup
(rule "fab: U1 first mm"
  (constraint clearance (min 0.1016mm))
  (condition "A.intersectsArea('U1_FIRST_MM') && B.intersectsArea('U1_FIRST_MM')"))
(rule "fab: BGA_ESCAPE_U1 class inside U1"
  (layer outer)
  (constraint clearance (min 0.0889mm))
  (condition "A.intersectsCourtyard('U1') && B.intersectsCourtyard('U1')"))
(rule "fab: a pair's own gap"
  (constraint clearance (min 0.15mm))
  (condition "A.isCoupledDiffPair() && !A.intersectsCourtyard('U1')"))
(rule "fab: a DRAM pair's own gap"
  (constraint clearance (min 0.15mm))
  (condition "((A.NetName == '*CK_T_A' && B.NetName == '*CK_C_A') || (A.NetName == '*CK_C_A' && B.NetName == '*CK_T_A'))"))
(rule "fab: SE_50 outer in U1's first mm"
  (layer outer)
  (condition "A.NetClass == 'SE_50' && A.intersectsArea('U1_FIRST_MM') && !A.intersectsCourtyard('U1')")
  (constraint track_width (min 0.1016mm) (opt 0.128mm)))
"""
W = 0.1016
BOX = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]]
AREA = {"area": "U1_FIRST_MM", "rule": "fab: U1 first mm", "min": 0.1016, "layer": None,
        "layers": {"F.Cu"}, "rings": BOX}
REQ = AC.required_fn(lambda n: 0.2032, 0.1016, AC.parse_dru(DRU))


def trk(net, a, b):
    return {"kind": "track", "net": net, "layer": "F.Cu", "a": a, "b": b, "w": W}


def test_parse():
    r = AC.parse_dru(DRU)
    assert [(a["area"], a["min"], a["layer"]) for a in r["areas"]] == [("U1_FIRST_MM", 0.1016, None)], r["areas"]
    assert r["courtyards"] == {"U1"}, r["courtyards"]          # the negated ones are not a relaxation
    assert r["pair_min"] == 0.15
    assert ("*CK_T_A", "*CK_C_A", 0.15) in r["pair_names"]


def test_clip():
    assert AC.clip((5, 5), (5, 12), BOX) == [((5, 10.0), (5, 12))]
    assert AC.clip((5, 5), (6, 6), BOX) == []
    assert AC.clip((-1, 5), (11, 5), BOX) == [((-1, 5), (0.0, 5.0)), ((10.0, 5.0), (11, 5))]


def test_breach_at_45():
    """Two 45-degree lines 0.1035 apart (edge to edge) running 0.5 mm past y 10: one breach, 0.1035
    against 0.2032 -- the defect DRC passed."""
    c = (0.1035 + W) / math.sqrt(2)
    items = [trk("/A", (5.0, 9.0), (6.5, 10.5)), trk("/B", (5.0 + c, 9.0 - c), (6.5 + c, 10.5 - c))]
    out = AC.breaches([AREA], items, REQ)
    assert len(out) == 1, out
    r = out[0]
    assert abs(r["gap"] - 0.1035) < 1e-3 and r["need"] == 0.2032, r
    assert r["at"][1] >= 10.0 - 1e-6, r                       # the point is past the edge
    assert "past U1_FIRST_MM" in AC.text(r)


def test_broken_at_edge():
    """The same two nets 0.1035 apart inside the area, opened to the class inside it, each broken at
    y 10 and on at class spacing past it: no breach.  And copper wholly inside is DRC's, not ours."""
    p = 0.1035 + W                                            # centre to centre inside the area
    q = 0.2032 + W + 0.001                                    # ... and past it
    items = [trk("/A", (2.0, 8.0), (2.0, 10.0)), trk("/A", (2.0, 10.0), (2.0, 12.0)),
             trk("/B", (2.0 + p, 8.0), (2.0 + p, 9.0)),
             trk("/B", (2.0 + p, 9.0), (2.0 + q, 9.0 + (q - p))),
             trk("/B", (2.0 + q, 9.0 + (q - p)), (2.0 + q, 10.0)), trk("/B", (2.0 + q, 10.0), (2.0 + q, 12.0)),
             trk("/C", (4.0, 1.0), (4.0, 3.0)), trk("/D", (4.0 + p, 1.0), (4.0 + p, 3.0))]
    assert AC.breaches([AREA], items, REQ) == []


def test_broken_but_not_opened():
    """Broken at the edge but still 0.1035 apart where it crosses: the piece outside breaches (KiCad
    would relax even this one -- the outer segment's round cap reaches into the area)."""
    p = 0.1035 + W
    items = [trk("/A", (2.0, 8.0), (2.0, 10.0)), trk("/A", (2.0, 10.0), (2.0, 12.0)),
             trk("/B", (2.0 + p, 8.0), (2.0 + p, 10.0)), trk("/B", (2.0 + p, 10.0), (2.0 + p, 12.0))]
    out = AC.breaches([AREA], items, REQ)
    assert len(out) >= 1 and all(abs(r["gap"] - 0.1035) < 1e-3 for r in out), out


def test_pairs_and_items():
    """A coupled pair (P / N) and a named pair keep only their pair gap past the edge; a via and a pad
    of another net are copper like a track; a point inside another relaxed region is that region's."""
    p = 0.15 + W + 0.001
    for a, b in (("/X_P", "/X_N"), ("/M_CK_T_A", "/M_CK_C_A")):
        items = [trk(a, (2.0, 9.0), (2.0, 11.0)), trk(b, (2.0 + p, 9.0), (2.0 + p, 11.0))]
        assert AC.breaches([AREA], items, REQ) == [], (a, b)
    via = {"kind": "via", "net": "/V", "layer": "F.Cu", "at": (6.0, 10.5), "r": 0.2}
    pad = {"kind": "pad", "net": "/G", "layer": "F.Cu", "what": "pad C1.1",
           "rings": [[(7.0, 10.3), (7.4, 10.3), (7.4, 10.7), (7.0, 10.7)]]}
    items = [trk("/A", (6.3, 9.5), (6.3, 11.0)), via, trk("/B", (7.55, 9.5), (7.55, 11.0)), pad]
    out = AC.breaches([AREA], items, REQ)
    assert sorted((r["net"], r["kind"]) for r in out) == [("/A", "via"), ("/B", "pad C1.1")], out
    skip = [(None, [[(5.0, 10.0), (9.0, 10.0), (9.0, 12.0), (5.0, 12.0)]])]
    assert AC.breaches([AREA], items, REQ, skip) == []


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print(f"ok  {name}")
    print(f"{n} tests passed")
