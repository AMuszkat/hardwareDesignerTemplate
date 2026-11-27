#!/usr/bin/env python3
"""A via the fab strips of its unconnected inner pads (unconnected-layer mode "remove") is only its
HOLE on those layers: netgroup's halo there (via_halo, rail_nets, board_via) and areaclear's check
of a signal hole (hole_required_fn, bare_holes, run).

    tools/kpy tools/route/test_bareholes.py        # the pcbnew cases too (a synthetic board in memory)
    python3 tools/route/test_bareholes.py          # the plain-Python cases; the pcbnew ones are skipped
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import areaclear as AC  # noqa: E402

try:
    import pcbnew  # noqa: F401
    HAVE_PCBNEW = True
except ImportError:
    HAVE_PCBNEW = False
if HAVE_PCBNEW:
    import netgroup as NG  # noqa: E402  (imports place, which needs pcbnew)
else:
    NG = None

HOLECLR = 0.1778
W2 = 0.1                 # the routed track's half width
PAD = dict(r=0.2, drill_r=0.1, unfl={"In1.Cu", "In2.Cu"})


def close(a, b):
    assert abs(a - b) < 1e-9, (a, b)


def _halo():
    if NG is not None:
        return NG.via_halo, NG.rail_nets
    # netgroup needs pcbnew to import; the two functions are plain Python, read them alone
    import ast
    src = (Path(__file__).resolve().parent / "netgroup.py").read_text()
    tree = ast.parse(src)
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("via_halo", "rail_nets"):
            exec(compile(ast.Module([node], []), "netgroup.py", "exec"), ns)
    return ns["via_halo"], ns["rail_nets"]


via_halo, rail_nets = _halo()


# --- netgroup: the halo per layer -------------------------------------------------------------

def test_gnd_unflashed_in_channel():
    """GND in the DRAM channel, the spec's padclr 0.284: the bare hole keeps copper-to-hole only."""
    v = dict(PAD, net="GND")
    close(via_halo(v, "In1.Cu", True, 0.284, HOLECLR, W2), 0.1 + HOLECLR + W2)
    close(via_halo(v, "F.Cu", True, 0.284, HOLECLR, W2), 0.2 + 0.284 + W2)        # flashed: the pad


def test_dq_unflashed():
    """A DQ via (own clearance 0.2032): its hole wall keeps the DQ's spacing."""
    v = dict(PAD, net="DQ3")
    close(via_halo(v, "In2.Cu", False, 0.2032, HOLECLR, W2), 0.1 + 0.2032 + W2)
    assert via_halo(v, "In2.Cu", False, 0.2032, HOLECLR, W2) < 0.2 + 0.2032 + W2   # the 0.4032 is gone


def test_ck_pair_rule_area():
    """A CK via in the pair-rule area (the group's padclr 0.284 is SC's figure there)."""
    v = dict(PAD, net="CK_T_A")
    close(via_halo(v, "In1.Cu", False, 0.284, HOLECLR, W2), 0.1 + 0.284 + W2)


def test_flashed_layer_unchanged():
    for rail in (True, False):
        v = dict(PAD, net="X")
        close(via_halo(v, "B.Cu", rail, 0.2032, HOLECLR, W2), 0.2 + 0.2032 + W2)
        v = dict(PAD, net="X", unfl=set())
        close(via_halo(v, "In1.Cu", rail, 0.2032, HOLECLR, W2), 0.2 + 0.2032 + W2)
    # a spec's extra_vias / reserve_vias entry has no hole: its pad stands on every layer
    close(via_halo(dict(net="X", r=0.2), "In1.Cu", False, 0.2032, HOLECLR, W2), 0.2 + 0.2032 + W2)
    # never below copper-to-hole, whatever the signal figure
    close(via_halo(dict(PAD, net="X"), "In1.Cu", False, 0.1, HOLECLR, W2), 0.1 + HOLECLR + W2)


def test_inside_field():
    F = {"_clr": 0.1016, "_shrink": 0.05}
    sig, gnd = dict(PAD, net="DQ0"), dict(PAD, net="GND")
    # the field's 0.1016 is less than the hole's: 0.1778 from the wall, signal or rail
    close(via_halo(sig, "In1.Cu", False, 0.2032, HOLECLR, W2, F=F), 0.1 + HOLECLR - 0.05 + W2)
    close(via_halo(gnd, "In1.Cu", True, 0.2032, HOLECLR, W2, F=F), 0.1 + HOLECLR - 0.05 + W2)
    # a field figure above the hole's holds for a signal, not for a rail
    F2 = {"_clr": 0.25, "_shrink": 0.05}
    close(via_halo(sig, "In1.Cu", False, 0.2032, HOLECLR, W2, F=F2), 0.1 + 0.25 - 0.05 + W2)
    close(via_halo(gnd, "In1.Cu", True, 0.2032, HOLECLR, W2, F=F2), 0.1 + HOLECLR - 0.05 + W2)
    # flashed in the field: the pad at the field's figure, as before
    close(via_halo(sig, "F.Cu", False, 0.2032, HOLECLR, W2, F=F), 0.2 + 0.1016 - 0.05 + W2)


def test_keep_pads():
    v = dict(PAD, net="+1V8")
    kp = {"In1.Cu": ["+1V8"]}
    close(via_halo(v, "In1.Cu", True, 0.2032, HOLECLR, W2, keep_pads=kp), 0.2 + 0.2032 + W2)
    close(via_halo(v, "In2.Cu", True, 0.2032, HOLECLR, W2, keep_pads=kp), 0.1 + HOLECLR + W2)
    F = {"_clr": 0.1016, "_shrink": 0.05}
    close(via_halo(v, "In1.Cu", True, 0.2032, HOLECLR, W2, F=F, keep_pads=kp), 0.2 + 0.1016 - 0.05 + W2)


def test_rail_nets():
    r = rail_nets(["/GND", None], ["/+1V1_SOC", "", "GND"], {"In5.Cu": ["+1V8"]})
    assert r == {"GND", "+1V1_SOC", "+1V8"}, r
    assert "DQ0" not in r                 # an unknown net is a signal: the stricter side


# --- areaclear: a signal hole's wall ----------------------------------------------------------

DRU = """(version 1)
(rule "fab: CK / DQS from other signals in the channel"
  (constraint clearance (min 0.284mm))
  (condition "A.intersectsArea('DRAM_CHANNEL') && B.intersectsArea('DRAM_CHANNEL') && ((A.NetClass == 'DDR_DIFF' && B.NetClass == 'SE_50') || (A.NetClass == 'SE_50' && B.NetClass == 'DDR_DIFF'))"))
(rule "fab: U1 first mm"
  (constraint clearance (min 0.1016mm))
  (condition "A.intersectsArea('U1_FIRST_MM') && B.intersectsArea('U1_FIRST_MM')"))
"""
CLS = {"/CK_T_A": "DDR_DIFF", "/DQ0": "SE_50", "/DQ1": "SE_50", "/GND": "Default"}
NEED = AC.hole_required_fn(lambda n: 0.2032 if n != "/GND" else 0.1, 0.1016, AC.parse_dru(DRU), HOLECLR,
                           lambda n: CLS.get(n, "Default"))
SQ = lambda x0, y0, x1, y1: [[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]   # noqa: E731
REG = {("Area", "DRAM_CHANNEL"): [({"In1.Cu"}, SQ(0, 0, 10, 10))],
       ("Area", "U1_FIRST_MM"): [({"In1.Cu"}, SQ(20, 0, 30, 10))]}


def hole(net, x, y, layer="In1.Cu"):
    return {"net": net, "layer": layer, "at": (x, y), "r": 0.1}


def passing(net, x, y, gap, w=0.1, layer="In1.Cu"):
    """A vertical track of net whose edge is gap from the wall of a 0.2 hole at (x, y)."""
    cx = x + 0.1 + w / 2 + gap
    return {"kind": "track", "net": net, "layer": layer, "a": (cx, y - 1), "b": (cx, y + 1), "w": w}


def test_parse_keeps_every_rule():
    r = AC.parse_dru(DRU)
    assert [x["min"] for x in r["all_rules"]] == [0.284, 0.1016]
    assert all(x["cond"] is not None for x in r["all_rules"])
    assert r["not_modelled"] == []        # the area rules stay areas, not pair rules


def test_hole_dq():
    out = AC.bare_holes([hole("/DQ0", 15, 5)], [passing("/DQ1", 15, 5, 0.19)], NEED, REG)
    assert len(out) == 1 and abs(out[0]["need"] - 0.2032) < 1e-9 and abs(out[0]["gap"] - 0.19) < 1e-6, out
    assert "bare hole" in AC.text(out[0]) and out[0]["area"] == AC.HOLES
    assert AC.bare_holes([hole("/DQ0", 15, 5)], [passing("/DQ1", 15, 5, 0.21)], NEED, REG) == []
    # its own net's track, another layer's track: nothing
    assert AC.bare_holes([hole("/DQ0", 15, 5)], [passing("/DQ0", 15, 5, 0.0)], NEED, REG) == []
    assert AC.bare_holes([hole("/DQ0", 15, 5)], [passing("/DQ1", 15, 5, 0.0, layer="In2.Cu")], NEED, REG) == []


def test_hole_ck_in_pair_rule_area():
    """A CK via (DDR_DIFF) and an SE_50 track, both in the channel: 0.284 from the hole wall."""
    out = AC.bare_holes([hole("/CK_T_A", 5, 5)], [passing("/DQ0", 5, 5, 0.25)], NEED, REG)
    assert len(out) == 1 and abs(out[0]["need"] - 0.284) < 1e-9, out
    assert AC.bare_holes([hole("/CK_T_A", 5, 5)], [passing("/DQ0", 5, 5, 0.29)], NEED, REG) == []
    # out of the channel the class's 0.2032 stands
    assert AC.bare_holes([hole("/CK_T_A", 15, 5)], [passing("/DQ0", 15, 5, 0.25)], NEED, REG) == []
    # the hole meets the channel, the track (beyond its edge) does not: undecided, so the 0.284 is
    # kept (a term that cannot be decided never lowers the number)
    out = AC.bare_holes([hole("/CK_T_A", 10.05, 5)], [passing("/DQ0", 10.05, 5, 0.25)], NEED, REG)
    assert len(out) == 1 and abs(out[0]["need"] - 0.284) < 1e-9, out
    # neither meets it: the class's 0.2032
    assert AC.bare_holes([hole("/CK_T_A", 10.2, 5)], [passing("/DQ0", 10.2, 5, 0.25)], NEED, REG) == []


def test_hole_inside_field():
    """In U1's first mm the field's 0.1016 is less than the hole's: 0.1778 from the wall."""
    out = AC.bare_holes([hole("/DQ0", 25, 5)], [passing("/DQ1", 25, 5, 0.15)], NEED, REG)
    assert len(out) == 1 and abs(out[0]["need"] - HOLECLR) < 1e-9, out
    assert AC.bare_holes([hole("/DQ0", 25, 5)], [passing("/DQ1", 25, 5, 0.18)], NEED, REG) == []


def test_hole_never_below_copper_to_hole():
    need = AC.hole_required_fn(lambda n: 0.1, 0.09, AC.parse_dru(""), HOLECLR)
    assert abs(need("/A", "/B") - HOLECLR) < 1e-9
    out = AC.bare_holes([hole("/A", 5, 5)], [passing("/B", 5, 5, 0.17)], need)
    assert len(out) == 1, out


# --- pcbnew: a synthetic 4-layer board ---------------------------------------------------------

def _board():
    mm = pcbnew.FromMM
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(4)
    ds = b.GetDesignSettings()
    ds.m_HoleClearance = mm(HOLECLR)
    ds.m_MinClearance = mm(0.1)
    nets = {}
    for n in ("/DQ0", "/DQ1", "/GND"):
        nets[n] = pcbnew.NETINFO_ITEM(b, n)
        b.Add(nets[n])

    def via(net, x, y):
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        v.SetWidth(mm(0.4))
        v.SetDrill(mm(0.2))
        v.SetNet(nets[net])
        v.Padstack().SetUnconnectedLayerMode(pcbnew.UNCONNECTED_LAYER_MODE_REMOVE_EXCEPT_START_AND_END)
        b.Add(v)
        return v

    def track(net, layer, a, z, w=0.1):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1])))
        t.SetEnd(pcbnew.VECTOR2I(mm(z[0]), mm(z[1])))
        t.SetWidth(mm(w))
        t.SetLayer(layer)
        t.SetNet(nets[net])
        b.Add(t)
    v_dq = via("/DQ0", 10, 10)
    via("/GND", 20, 10)
    zone = pcbnew.ZONE(b)
    zone.SetLayer(pcbnew.In2_Cu)
    zone.SetNet(nets["/GND"])
    ol = zone.Outline()
    ol.NewOutline()
    for x, y in ((15, 5), (25, 5), (25, 15), (15, 15)):
        ol.Append(mm(x), mm(y))
    b.Add(zone)
    cx = 0.1 + 0.05 + 0.19            # a 0.1 track 0.19 from the hole wall
    track("/DQ1", pcbnew.In1_Cu, (10 + cx, 8), (10 + cx, 12))      # past DQ0's bare hole: a row
    track("/DQ1", pcbnew.In1_Cu, (20 + cx, 8), (20 + cx, 12))      # past GND's: KiCad's, no row
    track("/DQ1", pcbnew.F_Cu, (10 + cx + 0.2, 8), (10 + cx + 0.2, 12))   # F.Cu: flashed, not a hole
    return b, v_dq


class _B:
    def __init__(self, b, d):
        self.pn, self.b, self.dir, self.gates = pcbnew, b, str(d), {}

    def class_clearance(self, net):
        return 0.2032


def test_pcbnew_flash_and_radius():
    """The synthetic via's disc radius per layer, through netgroup's own record of it."""
    if not HAVE_PCBNEW:
        print("     (skipped: no pcbnew -- run with tools/kpy)")
        return
    b, v = _board()
    rec = NG.board_via(pcbnew, b, v, "DQ0", 0.2032)
    assert rec["unfl"] == {"In1.Cu", "In2.Cu"}, rec["unfl"]
    close(rec["drill_r"], 0.1)
    close(rec["r"], 0.2)
    rail = rail_nets([], [z.GetNetname() for z in b.Zones() if not z.GetIsRuleArea()], {})
    assert "GND" in rail and "DQ0" not in rail, rail
    radius = {L: via_halo(rec, L, rec["net"] in rail, 0.2032, HOLECLR, W2) for L in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")}
    close(radius["F.Cu"], 0.2 + 0.2032 + W2)
    close(radius["B.Cu"], 0.2 + 0.2032 + W2)
    close(radius["In1.Cu"], 0.1 + 0.2032 + W2)
    close(radius["In2.Cu"], 0.1 + 0.2032 + W2)


def test_pcbnew_areaclear_run():
    """run() on a board with no rule area: the signal hole is judged, the GND hole and the
    flashed layer are not, and info["names"] carries the check so route.py check / audit run it."""
    if not HAVE_PCBNEW:
        print("     (skipped: no pcbnew -- run with tools/kpy)")
        return
    import tempfile
    b, _ = _board()
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "x.kicad_dru").write_text("(version 1)\n")
        out, info = AC.run(_B(b, d))
    assert AC.HOLES in info["names"], info.get("names")
    assert [(r["layer"], r["net"], r["other"]) for r in out] == [("In1.Cu", "/DQ1", "/DQ0")], out
    assert abs(out[0]["gap"] - 0.19) < 1e-4 and abs(out[0]["need"] - 0.2032) < 1e-9, out


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print(f"ok  {name}")
    print(f"{n} tests passed")
