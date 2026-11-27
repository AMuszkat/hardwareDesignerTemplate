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


# Pair rules as a handoff writes them for a bus: its own lines on one layer (an OR group a side, the
# class, a layer clause), the bus beside a ground via (an item type, two mirrored halves), and rules
# with a term this parser does not read.
DRU2 = """(version 1)
(rule "bus, own lines on F.Cu"
  (layer "F.Cu")
  (constraint clearance (min 0.1016mm))
  (condition "A.NetClass == 'SE_50' && B.NetClass == 'SE_50' && (A.NetName == '*BUS_D*' || A.NetName == '*BUS_M*' || A.NetName == '*BUS_RST') && (B.NetName == '*BUS_D*' || B.NetName == '*BUS_M*' || B.NetName == '*BUS_RST')"))
(rule "bus beside a GND via on F.Cu"
  (layer "F.Cu")
  (constraint clearance (min 0.1016mm))
  (condition "(A.NetClass == 'SE_50' && (A.NetName == '*BUS_D*' || A.NetName == '*BUS_M*') && B.NetName == 'GND' && B.Type == 'Via') || (B.NetClass == 'SE_50' && (B.NetName == '*BUS_D*' || B.NetName == '*BUS_M*') && A.NetName == 'GND' && A.Type == 'Via')"))
(rule "wide lines only"
  (constraint clearance (min 0.1016mm))
  (condition "A.NetName == '*WIDE*' && B.NetName == '*WIDE*' && A.Width >= 0.2mm"))
(rule "mixed operators"
  (constraint clearance (min 0.1016mm))
  (condition "A.NetName == '*MIX1' && B.NetName == '*MIX2' || A.NetName == '*MIX3'"))
(rule "outside a courtyard"
  (constraint clearance (min 0.1016mm))
  (condition "A.NetName == '*CY1' && B.NetName == '*CY2' && !A.intersectsCourtyard('U9')"))
(rule "a function"
  (constraint clearance (min 0.1016mm))
  (condition "A.hasNetclass('SE_50') && B.NetName == '*FN*'"))
"""
CLS = {"/M/BUS_D1": "SE_50", "/M/BUS_D2": "SE_50", "/M/BUS_M0": "SE_50", "/M/BUS_RST": "SE_50",
       "/M/BUS_CLK": "SE_50", "/M/OLD_BUS_D9": "Default", "GND": "Default", "/M/GND": "Default"}
REQ2 = AC.required_fn(lambda n: 0.2032, 0.0889, AC.parse_dru(DRU2), lambda n: CLS.get(n, "Default"))
T = {"kind": "track"}
V = {"kind": "via"}


def test_parse_pair_rules():
    r = AC.parse_dru(DRU2)
    assert [(p["rule"], p["layer"], p["min"]) for p in r["pair_rules"]] == [
        ("bus, own lines on F.Cu", "F.Cu", 0.1016), ("bus beside a GND via on F.Cu", "F.Cu", 0.1016)], r["pair_rules"]
    nm = {m["rule"]: m["term"] for m in r["not_modelled"]}
    assert set(nm) == {"wide lines only", "mixed operators", "outside a courtyard", "a function"}, nm
    assert "A.Width" in nm["wide lines only"] and "&& and ||" in nm["mixed operators"], nm
    assert "hasNetclass" in nm["a function"] and "!intersectsCourtyard('U9')" in nm["outside a courtyard"], nm
    # ... and the same rule IS read once the file has a clearance rule for that courtyard
    own = DRU2 + """(rule "inside U9" (constraint clearance (min 0.09mm))
      (condition "A.intersectsCourtyard('U9') && B.intersectsCourtyard('U9')"))"""
    assert "outside a courtyard" in [p["rule"] for p in AC.parse_dru(own)["pair_rules"]]
    assert AC.parse_dru(DRU)["not_modelled"] == []            # every rule of the older file is read
    assert [p["rule"] for p in AC.parse_dru(DRU)["pair_rules"]] == ["fab: a pair's own gap", "fab: a DRAM pair's own gap"]


def test_or_groups_class_and_layer():
    """An OR group a side: any two lines of the bus, the class on both, on the rule's layer only."""
    for a, b in (("/M/BUS_D1", "/M/BUS_D2"), ("/M/BUS_M0", "/M/BUS_D1"), ("/M/BUS_RST", "/M/BUS_M0"),
                 ("/m/bus_d1", "/M/BUS_D2")):
        cls = dict(CLS, **{"/m/bus_d1": "se_50"})            # KiCad's == ignores case
        req = AC.required_fn(lambda n: 0.2032, 0.0889, AC.parse_dru(DRU2), lambda n: cls.get(n, "Default"))
        assert req(a, b, T, T, "F.Cu") == 0.1016, (a, b)
        assert req(a, b, V, T, "F.Cu") == 0.1016, (a, b)       # no Type term: a via of the bus is its copper too
        assert req(a, b, T, T, "In2.Cu") == 0.2032, (a, b)     # the layer clause
        assert req(a, b, T, T, "B.Cu") == 0.2032, (a, b)
        assert req(a, b) == 0.2032, (a, b)                     # no layer given: never relaxed
    assert REQ2("/M/BUS_D1", "/M/BUS_CLK", T, T, "F.Cu") == 0.2032      # a name outside the group
    assert REQ2("/M/BUS_D1", "/M/OLD_BUS_D9", T, T, "F.Cu") == 0.2032   # the name, not the class
    no_class = AC.required_fn(lambda n: 0.2032, 0.0889, AC.parse_dru(DRU2))
    assert no_class("/M/BUS_D1", "/M/BUS_D2", T, T, "F.Cu") == 0.2032   # a class term with no class known


def test_type_via_and_mirror():
    """`B.Type == 'Via'`: the ground VIA is relaxed, a ground track, pad or pour is not; either item
    may be A (KiCad tries both, and the rule's two mirrored halves say the same); F.Cu only."""
    pad, pour, arc = {"kind": "pad"}, {"kind": "poly"}, {"kind": "track", "arc": True}
    assert REQ2("/M/BUS_D1", "GND", T, V, "F.Cu") == 0.1016
    assert REQ2("GND", "/M/BUS_D1", V, T, "F.Cu") == 0.1016
    assert REQ2("/M/BUS_M0", "GND", V, V, "F.Cu") == 0.1016
    for other in (T, pad, pour, arc, None):
        assert REQ2("/M/BUS_D1", "GND", T, other, "F.Cu") == 0.2032, other
        assert REQ2("GND", "/M/BUS_D1", other, T, "F.Cu") == 0.2032, other
    assert REQ2("GND", "/M/BUS_D1", T, V, "F.Cu") == 0.2032     # the BUS line's via beside a ground track
    assert REQ2("/M/BUS_D1", "GND", T, V, "In2.Cu") == 0.2032
    assert REQ2("/M/BUS_D1", "/M/GND", T, V, "F.Cu") == 0.2032  # 'GND' is the whole name, not a pattern
    assert REQ2("/M/BUS_RST", "GND", T, V, "F.Cu") == 0.2032    # a line the via rule does not name
    assert REQ2("/M/BUS_CLK", "GND", T, V, "F.Cu") == 0.2032
    # one half alone is read as written: A the line, B the via -- and still either item may be A
    one = AC.parse_dru('(rule "h" (constraint clearance (min 0.1mm)) '
                       '(condition "A.NetName == \'*D*\' && B.NetName == \'GND\' && B.Type == \'Via\'"))')
    req = AC.required_fn(lambda n: 0.2032, 0.0889, one)
    assert req("/D1", "GND", T, V, "F.Cu") == 0.1 and req("GND", "/D1", V, T, "F.Cu") == 0.1
    assert req("/D1", "GND", V, T, "F.Cu") == 0.2032


def test_unknown_term_ignored():
    """A rule with a term this parser does not read gives its number to nobody."""
    for a, b in (("/WIDE1", "/WIDE2"), ("/A_MIX1", "/A_MIX2"), ("/A_MIX3", "/Z"), ("/CY1", "/CY2"),
                 ("/M/BUS_D1", "/FN1")):
        assert REQ2(a, b, T, T, "F.Cu") == 0.2032, (a, b)
    for cond in ("A.NetName == 'X' && B.Layer == 'F.Cu'", "!A.NetName == 'X'", "A.NetName == 'X' && (B.NetName == 'Y'",
                 "A.NetName == X", "A.NetName == 'X' B.NetName == 'Y'", "A.existsOnLayer('F.Cu')",
                 "A.Type == 'Footprint'", "A.NetName == 'X' && B.NetName == 'Y')", ""):
        r = AC.parse_dru(f'(rule "u" (constraint clearance (min 0.05mm)) (condition "{cond}"))')
        assert r["pair_rules"] == [] and [m["rule"] for m in r["not_modelled"]] == ["u"], (cond, r)


def test_last_rule_wins_and_never_lower_undecided():
    """KiCad takes the LAST rule that matches; a rule that cannot be decided never lowers the number,
    and one that would raise it does."""
    two = ('(rule "a" (constraint clearance (min {0}mm)) (condition "A.NetName == \'/P\' && B.NetName == \'/Q\'"))\n'
           '(rule "b" (constraint clearance (min {1}mm)) (condition "(A.NetName == \'/P\' || A.NetName == \'/R\') '
           '&& B.NetName != \'/R\'"))')
    assert AC.required_fn(lambda n: 0.2, 0.09, AC.parse_dru(two.format(0.15, 0.1)))("/P", "/Q") == 0.1
    assert AC.required_fn(lambda n: 0.2, 0.09, AC.parse_dru(two.format(0.1, 0.15)))("/Q", "/P") == 0.15
    assert AC.required_fn(lambda n: 0.2, 0.09, AC.parse_dru(two.format(0.1, 0.15)))("/R", "/R2") == 0.15
    assert AC.required_fn(lambda n: 0.2, 0.09, AC.parse_dru(two.format(0.1, 0.15)))("/R", "/Q2") == 0.15
    cls = '(rule "c" (constraint clearance (min {0}mm)) (condition "A.NetClass == \'HV\' && B.NetName == \'*\'"))'
    assert AC.required_fn(lambda n: 0.2, 0.09, AC.parse_dru(cls.format(0.1)))("/P", "/Q") == 0.2
    assert AC.required_fn(lambda n: 0.2, 0.09, AC.parse_dru(cls.format(0.5)))("/P", "/Q") == 0.5
    assert AC.required_fn(lambda n: 0.2, 0.09, AC.parse_dru(cls.format(0.5)), lambda n: "Default")("/P", "/Q") == 0.2
    assert AC.required_fn(lambda n: 0.2, 0.09, AC.parse_dru(cls.format(0.5)), lambda n: "HV,Default")("/P", "/Q") == 0.5


def test_breaches_with_pair_rules():
    """Through `breaches`: two lines of the bus 0.1035 apart past the area's edge pass on F.Cu and fail
    on In2.Cu; a bus line 0.11 from a ground via passes on F.Cu, and fails beside a ground track, a
    ground via on In2.Cu and another net's via."""
    area = dict(AREA, layers={"F.Cu", "In2.Cu"})
    p = 0.1035 + W

    def lay(layer, other):
        a = dict(trk("/M/BUS_D1", (2.0, 9.0), (2.0, 12.0)), layer=layer)
        return AC.breaches([area], [a, dict(other, layer=layer)], REQ2)

    line = trk("/M/BUS_D2", (2.0 + p, 9.0), (2.0 + p, 12.0))
    assert lay("F.Cu", line) == []
    out = lay("In2.Cu", line)
    assert len(out) == 1 and abs(out[0]["gap"] - 0.1035) < 1e-3 and out[0]["need"] == 0.2032, out
    assert len(lay("F.Cu", trk("/M/BUS_CLK", (2.0 + p, 9.0), (2.0 + p, 12.0)))) == 1
    d = 0.11 + W / 2 + 0.2                                    # centre of a 0.4 via, 0.11 from the line's edge
    via = {"kind": "via", "net": "GND", "at": (2.0 + d, 11.0), "r": 0.2}
    assert lay("F.Cu", via) == []
    assert len(lay("In2.Cu", via)) == 1
    assert len(lay("F.Cu", dict(via, net="/M/BUS_CLK"))) == 1
    out = lay("F.Cu", trk("GND", (2.0 + 0.11 + W, 10.5), (2.0 + 0.11 + W, 11.5)))
    assert len(out) == 1 and abs(out[0]["gap"] - 0.11) < 1e-3 and out[0]["need"] == 0.2032, out
    gpad = {"kind": "pad", "net": "GND", "what": "pad C1.2",
            "rings": [[(2.0 + W / 2 + 0.11, 10.8), (2.6, 10.8), (2.6, 11.2), (2.0 + W / 2 + 0.11, 11.2)]]}
    assert [r["kind"] for r in lay("F.Cu", gpad)] == ["pad C1.2"]


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print(f"ok  {name}")
    print(f"{n} tests passed")
