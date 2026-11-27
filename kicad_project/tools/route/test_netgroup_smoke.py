#!/usr/bin/env python3
"""netgroup.py END TO END on a synthetic board (KiCad's pcbnew), before any change to it costs a round:

    tools/kpy tools/route/test_netgroup_smoke.py        (or: tools/kpy -m pytest tools/route/test_netgroup_smoke.py)

WHY.  The unit tests (test_bareholes.py) call netgroup's functions one at a time; nothing ran the
router as a board session runs it, because a run on a board's copy books a budget round.  The
bare-hole change (via_halo, 2026-11-27) passed its unit tests and then crashed EVERY router run at
its first partial write: the board-via loop assigned a float to `full`, the net table that
write_partial() reads (TypeError: 'float' object is not subscriptable) -- a reopened round spent in
26 s.  This test is that run, on a board nobody owns.

THE FIXTURE, built in a temporary folder OUTSIDE kicad_project/ (never a board folder), its .kicad_pro
written by KiCad's own SaveBoard: 4 copper layers, J1 (3 SMD pads, /SIG1-3) and R1 (the same) 22 mm
apart; between them U1, a "field" part (2 x 2 lands, a courtyard) with a GND via inside its
courtyard -- the dog-bone branch of the board-via loop -- and a /SIGV via outside it, both with
unconnected-layer mode REMOVE_EXCEPT_START_AND_END, so both are bare holes on In1 / In2 (via_halo's
bare path, in a field and out of one); a GND zone (unfilled, In2.Cu) makes GND a rail (rail_nets);
a /SIGV track walls F.Cu across the window, so every net changes layer and the plan carries vias.
The spec routes SIG1-3 from J1 to R1 on every layer, U1 its field (open on In1 / In2).

NO BUDGET.  budget.home() charges a run to a board only when the folder lies in a board's folder,
HW_BUDGET_BOARD names one (bgwait --group), or the folder holds a file named after the session's
board (HW_BOARD).  The fixture holds none of these (its board is "ngsmoke.kicad_pcb", HW_BUDGET_*
are dropped from the child's environment, HW_BOARD is kept: the run is what a session would start),
and every board's routing/ledger.json is hashed before and after each run.

  test_fixture_bare_holes  the fixture is what it claims: netgroup.board_via reads both vias as
                           unflashed on In1.Cu and In2.Cu, GND a rail and SIGV not, the GND via
                           inside U1's courtyard.
  test_routes              netgroup.py --board <fixture> <spec> --timeout 60: exit 0, the plan
                           (every net's copper reaches both of its pads, a via each past the wall,
                           none inside the field), its .origin.json, at
                           least one partial write (the run's "removed <plan>.partial.json" line:
                           it exists at the end only if write_partial ran), no ledger touched.
  test_catches_full_clash  the same run on a MUTANT of netgroup.py whose board-via loop names its
                           halo `full` again (the crash): it must fail with the TypeError and leave
                           no plan -- the proof that test_routes catches the defect it was written
                           for.  If the mutation site is gone (the loop was rewritten), this test
                           fails and says so: re-aim MUTANT at the new code, never delete the case.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NG = HERE / "netgroup.py"
KP = HERE.parent.parent                       # kicad_project/
NAME = "ngsmoke"                              # never a board's name: budget.home() finds no board in it
NETS = {"SIG1": 7.0, "SIG2": 10.0, "SIG3": 13.0}
JX, RX = 4.0, 26.0
U1 = (15.0, 10.0)
GND_VIA = (15.0, 10.0)                        # inside U1's courtyard: the field (dog-bone) branch
SIG_VIA = (15.0, 4.0)                         # outside every field: the plain branch
WALL_X = 9.0                                  # a /SIGV track across F.Cu: every net must change layer
MUTANT = ("r_full", "full")                   # the crash of 2026-11-27, read back
TIMEOUT = 60


def build(d):
    """The synthetic board in folder d; returns its .kicad_pcb path."""
    import pcbnew as pn
    FM = pn.FromMM
    pcb = Path(d) / f"{NAME}.kicad_pcb"
    b = pn.NewBoard(str(pcb))
    b.SetCopperLayerCount(4)
    ls = b.GetEnabledLayers()
    for n in ("In1.Cu", "In2.Cu"):
        ls.addLayer(b.GetLayerID(n)) if hasattr(ls, "addLayer") else ls.AddLayer(b.GetLayerID(n))
    b.SetEnabledLayers(ls)
    ds = b.GetDesignSettings()
    ds.m_HoleClearance = FM(0.2)
    ds.m_HoleToHoleMin = FM(0.25)
    ds.m_CopperEdgeClearance = FM(0.3)
    # the layer constants, not GetLayerID("F.CrtYd"): KiCad 10 names it "F.Courtyard", and an unknown
    # name is UNDEFINED -- the saved board then does not load (LoadBoard returns None)
    F, Bk, E, CY = pn.F_Cu, pn.B_Cu, pn.Edge_Cuts, pn.F_CrtYd
    nets = {}
    for n in ["/" + k for k in NETS] + ["/GND", "/SIGV"]:
        nets[n] = pn.NETINFO_ITEM(b, n)
        b.Add(nets[n])
    r = pn.PCB_SHAPE(b)
    r.SetShape(pn.SHAPE_T_RECT)
    r.SetStart(pn.VECTOR2I(FM(0), FM(0)))
    r.SetEnd(pn.VECTOR2I(FM(30), FM(20)))
    r.SetLayer(E)
    r.SetWidth(FM(0.1))
    b.Add(r)

    def part(ref, at, pads, shape, size, crt=None):
        fp = pn.FOOTPRINT(b)
        fp.SetReference(ref)
        fp.SetPosition(pn.VECTOR2I(FM(at[0]), FM(at[1])))
        b.Add(fp)
        for num, (x, y), net in pads:
            p = pn.PAD(fp)
            p.SetNumber(num)
            p.SetAttribute(pn.PAD_ATTRIB_SMD)
            p.SetLayerSet(p.SMDMask())
            p.SetShape(pn.PADSTACK.ALL_LAYERS, shape)
            p.SetSize(pn.PADSTACK.ALL_LAYERS, pn.VECTOR2I(FM(size[0]), FM(size[1])))
            fp.Add(p)
            p.SetPosition(pn.VECTOR2I(FM(x), FM(y)))
            if net:
                p.SetNet(nets[net])
        if crt:
            c = pn.PCB_SHAPE(fp)
            c.SetShape(pn.SHAPE_T_RECT)
            c.SetStart(pn.VECTOR2I(FM(crt[0]), FM(crt[1])))
            c.SetEnd(pn.VECTOR2I(FM(crt[2]), FM(crt[3])))
            c.SetLayer(CY)
            c.SetWidth(FM(0.05))
            fp.Add(c)
        return fp

    part("J1", (JX, 10), [(str(i + 1), (JX, y), "/" + n) for i, (n, y) in enumerate(NETS.items())],
         pn.PAD_SHAPE_RECT, (1.0, 0.6), crt=(JX - 0.75, 6.45, JX + 0.75, 13.55))
    part("R1", (RX, 10), [(str(i + 1), (RX, y), "/" + n) for i, (n, y) in enumerate(NETS.items())],
         pn.PAD_SHAPE_RECT, (1.0, 0.6), crt=(RX - 0.75, 6.45, RX + 0.75, 13.55))
    x0, y0 = U1
    part("U1", U1, [("A1", (x0 - 0.5, y0 - 0.5), "/GND"), ("A2", (x0 + 0.5, y0 - 0.5), None),
                    ("B1", (x0 - 0.5, y0 + 0.5), None), ("B2", (x0 + 0.5, y0 + 0.5), "/GND")],
         pn.PAD_SHAPE_CIRCLE, (0.35, 0.35), crt=(x0 - 1.2, y0 - 1.2, x0 + 1.2, y0 + 1.2))

    def via(at, net):
        v = pn.PCB_VIA(b)
        v.SetPosition(pn.VECTOR2I(FM(at[0]), FM(at[1])))
        v.SetWidth(FM(0.45))
        v.SetDrill(FM(0.2))
        v.SetLayerPair(F, Bk)
        v.Padstack().SetUnconnectedLayerMode(pn.UNCONNECTED_LAYER_MODE_REMOVE_EXCEPT_START_AND_END)
        b.Add(v)
        v.SetNet(nets[net])
    via(GND_VIA, "/GND")
    via(SIG_VIA, "/SIGV")
    # a /SIGV wall on F.Cu across the whole window: every net changes layer twice, so the plan's vias
    # (their net names, hole-to-hole against the bare holes, the field's no-new-via rule) are written too
    t = pn.PCB_TRACK(b)
    t.SetStart(pn.VECTOR2I(FM(WALL_X), FM(0.5)))
    t.SetEnd(pn.VECTOR2I(FM(WALL_X), FM(19.5)))
    t.SetWidth(FM(0.3))
    t.SetLayer(F)
    b.Add(t)
    t.SetNet(nets["/SIGV"])
    # a GND zone (unfilled, a corner of In2.Cu away from the routes): GND is then a rail to rail_nets(),
    # and the GND via's bare hole takes the rail figure (copper-to-hole) instead of the signal one
    z = pn.ZONE(b)
    z.SetLayer(b.GetLayerID("In2.Cu"))
    ol = z.Outline()
    ol.NewOutline()
    for x, y in ((21, 15.5), (28, 15.5), (28, 18.5), (21, 18.5)):
        ol.Append(FM(x), FM(y))
    b.Add(z)
    z.SetNet(nets["/GND"])
    pn.SaveBoard(str(pcb), b)                 # writes ngsmoke.kicad_pro beside it (KiCad's own)
    return pcb


def spec():
    return {"group": "NGSMOKE", "why": "test_netgroup_smoke.py: netgroup.py end to end on a synthetic board",
            "plan": "routing/NGSMOKE.json",
            "window": [1, 1, 29, 19], "pitch": 0.2, "width": 0.2, "clearance": 0.15, "padclr": 0.2,
            "via": [0.6, 0.3], "via_sep": 0.8,
            "field": {"ref": "U1", "open": ["In1.Cu", "In2.Cu"], "clr": 0.1, "width": 0.15},
            "keep_pads": {"In1.Cu": ["SIGV"]},      # the option's whole path: SIGV keeps its pad on In1
            "nets": {n: {"src_pad": ["J1", str(i + 1)], "src": [JX, y], "layer": "F.Cu",
                         "legs": [{"to": ["R1", str(i + 1)]}]}
                     for i, (n, y) in enumerate(NETS.items())}}


def fixture(tag):
    d = Path(tempfile.mkdtemp(prefix=f"ngsmoke_{tag}_"))
    assert "kicad_project" not in str(d.resolve()), d
    pcb = build(d)
    assert (d / f"{NAME}.kicad_pro").is_file(), "SaveBoard wrote no .kicad_pro"
    (d / "routing").mkdir()
    sp = d / "ngsmoke_spec.json"
    sp.write_text(json.dumps(spec(), indent=1))
    return d, pcb, sp


def ledgers():
    """{path: sha256} of every board's routing/ledger.json (absent ones too, as None)."""
    out = {}
    for bd in sorted(KP.glob("board*_*")):
        p = bd / "routing" / "ledger.json"
        out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    return out


def ledger_check(before):
    """No board's ledger may carry this run.  A ledger another session's job wrote in the same seconds
    changes its sha too: that is reported, and only a ledger naming this fixture fails."""
    after = ledgers()
    for p, s in after.items():
        if s == before.get(p):
            continue
        txt = Path(p).read_text() if Path(p).is_file() else ""
        assert "ngsmoke" not in txt.lower() and "NGSMOKE" not in txt, \
            f"{p}: the fixture's run was charged to a board's budget"
        print(f"   note: {p} changed during the run (another job's; it does not name this fixture)")


def run(d, sp, script=None):
    """netgroup.py (or a mutant source of it) on the fixture, as a board session starts it."""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("HW_BUDGET_"):
            env.pop(k)
    env["HW_JOBS"] = "1"
    args = ["--board", str(d), str(sp), "--timeout", str(TIMEOUT)]
    if script is None:
        cmd = [sys.executable, str(NG), *args]
    else:
        # the mutant runs AS netgroup.py (its __file__, so its sys.path and imports are the real ones)
        boot = ("import sys; ng, src = sys.argv[1], sys.argv[2]; sys.argv = [ng] + sys.argv[3:]; "
                "exec(compile(open(src).read(), ng, 'exec'), {'__name__': '__main__', '__file__': ng})")
        cmd = [sys.executable, "-c", boot, str(NG), str(script), *args]
    before = ledgers()
    r = subprocess.run(cmd, cwd=str(d), capture_output=True, text=True, env=env, timeout=TIMEOUT + 120)
    ledger_check(before)
    return r.returncode, r.stdout + r.stderr


def near(pts, xy, tol=0.35):
    return any(abs(p[0] - xy[0]) <= tol and abs(p[1] - xy[1]) <= tol for p in pts)


def test_fixture_bare_holes():
    import pcbnew as pn
    sys.path.insert(0, str(HERE))
    import netgroup  # noqa: E402
    d, pcb, _ = fixture("vias")
    try:
        b = pn.LoadBoard(str(pcb))
        vs = {(round(pn.ToMM(t.GetPosition().x), 3), round(pn.ToMM(t.GetPosition().y), 3)): t
              for t in b.GetTracks() if t.Type() == pn.PCB_VIA_T}
        for at, net in ((GND_VIA, "GND"), (SIG_VIA, "SIGV")):
            rec = netgroup.board_via(pn, b, vs[at], net, 0.15)
            assert rec["unfl"] == {"In1.Cu", "In2.Cu"}, (at, rec["unfl"])
            assert abs(rec["drill_r"] - 0.1) < 1e-9, rec
        zn = [z.GetNetname().rsplit("/", 1)[-1] for z in b.Zones() if not z.GetIsRuleArea()]
        rail = netgroup.rail_nets([], zn, {})
        assert "GND" in rail and "SIGV" not in rail, rail
        fp = {f.GetReference(): f for f in b.GetFootprints()}["U1"]
        bb = [g.GetBoundingBox() for g in fp.GraphicalItems() if g.GetLayer() == pn.F_CrtYd][0]
        assert pn.ToMM(bb.GetLeft()) < GND_VIA[0] < pn.ToMM(bb.GetRight()) \
            and pn.ToMM(bb.GetTop()) < GND_VIA[1] < pn.ToMM(bb.GetBottom())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_routes():
    d, pcb, sp = fixture("run")
    try:
        rc, out = run(d, sp)
        plan_p = d / "routing" / "NGSMOKE.json"
        assert rc == 0, f"netgroup.py exited {rc}:\n{out[-3000:]}"
        assert plan_p.is_file(), out[-2000:]
        assert plan_p.with_suffix(".origin.json").is_file(), "no origin record beside the plan"
        # write_partial ran at least once: its file exists at the end only then, and the run says so
        assert re.search(r"removed NGSMOKE\.partial\.json", out), \
            f"no partial was written (write_partial never ran -- the crash's code path is untested):\n{out[-2000:]}"
        assert not plan_p.with_suffix(".partial.json").exists()
        plan = json.loads(plan_p.read_text())
        for n, y in NETS.items():
            pts = [tuple(q) for t in plan["tracks"] if t["net"] == "/" + n for q in t["pts"]]
            assert pts, f"/{n}: no copper in the plan"
            assert near(pts, (JX, y)) and near(pts, (RX, y)), f"/{n}: its copper does not reach J1 and R1"
            vs = [q["at"] for q in plan["vias"] if q["net"] == "/" + n]
            assert len(vs) >= 1, f"/{n}: no via -- the F.Cu wall at x {WALL_X} must be crossed on another layer"
        for q in plan["vias"]:          # the field takes no new via (its dog-bones are the only ones)
            assert not (U1[0] - 1.2 <= q["at"][0] <= U1[0] + 1.2 and U1[1] - 1.2 <= q["at"][1] <= U1[1] + 1.2), q
        assert not (d / "routing" / "ledger.json").exists(), "the fixture grew a ledger of its own"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_catches_full_clash():
    src = NG.read_text()
    old, new = MUTANT
    k = len(re.findall(rf"\b{old}\b", src))
    assert k >= 2, (f"the mutation site is gone: netgroup.py has no `{old}` in its board-via loop any more -- "
                    f"re-aim MUTANT at the code that replaced it (the name write_partial reads), never drop this case")
    d, pcb, sp = fixture("mutant")
    try:
        mut = d / "netgroup_mutant.py"
        mut.write_text(re.sub(rf"\b{old}\b", new, src))
        rc, out = run(d, sp, script=mut)
        assert rc != 0, f"the mutant ran clean -- this test would not catch the crash:\n{out[-2000:]}"
        assert "TypeError" in out and "not subscriptable" in out, f"the mutant failed otherwise:\n{out[-2000:]}"
        assert not (d / "routing" / "NGSMOKE.json").exists(), "the mutant left a plan"
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    fails = 0
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - fails}/{len(tests)} passed")
    sys.exit(1 if fails else 0)
