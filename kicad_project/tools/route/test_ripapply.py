#!/usr/bin/env python3
"""Tests for `route.py apply` / `rip` on a synthetic board (KiCad's pcbnew and kicad-cli):

    tools/kpy tools/route/test_ripapply.py            (or: tools/kpy -m pytest tools/route/test_ripapply.py)

The board is built in a temporary folder OUTSIDE kicad_project/ (never a board folder): two
through-hole pads of /N1 joined F.Cu -> via -> In1.Cu (4 layers), a net-tied polygon on P1, a /N2 pad pair.
The via carries flags a field-by-field record would drop (remove unconnected layers, tenting).

  test_round_trip   a plan that rips the via, both segments and the polygon and lays a new path:
                    apply, rip, apply, rip -- the board after each rip is the board before the
                    first apply, item by item (uuid and KiCad's own s-expression of each).
  test_gone_matches the same plan applied, ripped by an OLD-format rip (the removed copper left
                    off, its manifest without "removed_items"), then applied again: the vias and
                    segments it rips are gone and match the group's own earlier record -> done,
                    with a note, not exit 2.
  test_missing_ripvia_releases_lock
                    a plan whose ripvia is not on the board exits 2, names the via, and leaves no
                    ~<board>.kicad_pro.lck behind: the next apply of a good plan succeeds.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTE = HERE / "route.py"
NAME = "synth"


def build(d):
    """The synthetic board in folder d; returns its .kicad_pcb path."""
    import pcbnew as pn
    FM = pn.FromMM
    pcb = Path(d) / f"{NAME}.kicad_pcb"
    b = pn.NewBoard(str(pcb))
    b.SetCopperLayerCount(4)
    ls = b.GetEnabledLayers()
    ls.addLayer(b.GetLayerID("In1.Cu")) if hasattr(ls, "addLayer") else ls.AddLayer(b.GetLayerID("In1.Cu"))
    ls.addLayer(b.GetLayerID("In2.Cu")) if hasattr(ls, "addLayer") else ls.AddLayer(b.GetLayerID("In2.Cu"))
    b.SetEnabledLayers(ls)
    I1 = b.GetLayerID("In1.Cu")
    F, Bk, E = b.GetLayerID("F.Cu"), b.GetLayerID("B.Cu"), b.GetLayerID("Edge.Cuts")
    nets = {}
    for n in ("/N1", "/N2"):
        nets[n] = pn.NETINFO_ITEM(b, n)
        b.Add(nets[n])
    r = pn.PCB_SHAPE(b)
    r.SetShape(pn.SHAPE_T_RECT)
    r.SetStart(pn.VECTOR2I(FM(0), FM(0)))
    r.SetEnd(pn.VECTOR2I(FM(30), FM(20)))
    r.SetLayer(E)
    r.SetWidth(FM(0.1))
    b.Add(r)
    for ref, (x, y), net in (("P1", (10, 10), "/N1"), ("P2", (20, 10), "/N1"),
                             ("P3", (10, 16), "/N2"), ("P4", (20, 16), "/N2")):
        fp = pn.FOOTPRINT(b)
        fp.SetReference(ref)
        fp.SetPosition(pn.VECTOR2I(FM(x), FM(y)))
        b.Add(fp)
        p = pn.PAD(fp)
        p.SetNumber("1")
        p.SetAttribute(pn.PAD_ATTRIB_PTH)
        p.SetLayerSet(p.PTHMask())
        p.SetShape(pn.PADSTACK.ALL_LAYERS, pn.PAD_SHAPE_CIRCLE)
        p.SetSize(pn.PADSTACK.ALL_LAYERS, pn.VECTOR2I(FM(1.6), FM(1.6)))
        p.SetDrillSize(pn.VECTOR2I(FM(0.8), FM(0.8)))
        fp.Add(p)
        p.SetPosition(pn.VECTOR2I(FM(x), FM(y)))
        p.SetNet(nets[net])

    def seg(layer, a, e, net="/N1", w=0.25):
        t = pn.PCB_TRACK(b)
        t.SetStart(pn.VECTOR2I(FM(a[0]), FM(a[1])))
        t.SetEnd(pn.VECTOR2I(FM(e[0]), FM(e[1])))
        t.SetWidth(FM(w))
        t.SetLayer(layer)
        b.Add(t)
        t.SetNet(nets[net])
    seg(F, (10, 10), (15, 10))
    seg(I1, (15, 10), (20, 10))
    seg(F, (10, 16), (20, 16), "/N2")
    v = pn.PCB_VIA(b)
    v.SetPosition(pn.VECTOR2I(FM(15), FM(10)))
    v.SetWidth(FM(0.6))
    v.SetDrill(FM(0.3))
    v.SetLayerPair(F, Bk)
    v.Padstack().SetUnconnectedLayerMode(pn.UNCONNECTED_LAYER_MODE_REMOVE_EXCEPT_START_AND_END)
    v.SetFrontTentingMode(pn.TENTING_MODE_TENTED)
    v.SetBackTentingMode(pn.TENTING_MODE_NOT_TENTED)
    b.Add(v)
    v.SetNet(nets["/N1"])
    s = pn.PCB_SHAPE(b)
    s.SetShape(pn.SHAPE_T_POLY)
    s.SetFilled(True)
    s.SetWidth(0)
    s.SetLayer(F)
    s.SetPolyPoints([pn.VECTOR2I(FM(x), FM(y)) for x, y in ((10, 9.5), (11.5, 9.5), (11.5, 10.5), (10, 10.5))])
    b.Add(s)
    s.SetNetCode(nets["/N1"].GetNetCode())
    pn.SaveBoard(str(pcb), b)
    return pcb


PLAN = {"group": "R9", "why": "test: re-lay /N1 through a new via",
        "rips": [{"net": "/N1", "layer": "F.Cu", "pts": [[10, 10], [15, 10]]},
                 {"net": "/N1", "layer": "In1.Cu", "pts": [[15, 10], [20, 10]]}],
        "ripvias": [{"net": "/N1", "at": [15, 10]}],
        "polyrips": [{"net": "/N1", "layer": "F.Cu", "at": [10, 9.5]}],
        "tracks": [{"net": "/N1", "layer": "F.Cu", "width": 0.25, "pts": [[10, 10], [12, 12], [15, 12]]},
                   {"net": "/N1", "layer": "B.Cu", "width": 0.25, "pts": [[15, 12], [18, 12], [20, 10]]}],
        "vias": [{"net": "/N1", "at": [15, 12], "pad": 0.6, "drill": 0.3}]}


def snapshot(pcb):
    """{uuid: KiCad's s-expression} of every track, via and drawing on the saved board."""
    import pcbnew as pn
    b = pn.LoadBoard(str(pcb))
    io = pn.PCB_IO_KICAD_SEXPR()
    out = {}
    for it in list(b.GetTracks()) + list(b.GetDrawings()) + list(b.Zones()):
        io.Format(it)
        out[it.m_Uuid.AsString()] = " ".join(io.GetStringOutput(True).split())
    return out


def route(d, *args):
    env = dict(os.environ, HW_DRC_CACHE="0")
    env.pop("HW_BOARD", None)
    r = subprocess.run([sys.executable, str(ROUTE), *args, "--board", str(d)], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def locks(d):
    """the lock files in d once no kicad-cli naming d is alive (an orphan takes its lock AFTER the
    caller has exited, and dies on its closed stdout without removing it: wait it out, 30 s)"""
    import time
    t0 = time.monotonic()
    while time.monotonic() - t0 < 30:
        alive = False
        for pid in os.listdir("/proc"):
            try:
                if pid.isdigit() and open(f"/proc/{pid}/comm").read().strip() == "kicad-cli" \
                        and str(d) in open(f"/proc/{pid}/cmdline").read():
                    alive = True
            except OSError:
                pass
        if not alive:
            break
        time.sleep(0.5)
    return sorted(p.name for p in Path(d).glob("~*.lck"))


def fixture(tag):
    d = Path(tempfile.mkdtemp(prefix=f"ripapply_{tag}_"))
    assert "kicad_project" not in str(d.resolve()), d
    pcb = build(d)
    (d / "routing").mkdir()
    (d / "routing" / "R9.json").write_text(json.dumps(PLAN, indent=1))
    return d, pcb


def diff(a, b):
    return ([u for u in a if u not in b], [u for u in b if u not in a], [u for u in a if u in b and a[u] != b[u]])


def test_round_trip():
    d, pcb = fixture("rt")
    try:
        s0 = snapshot(pcb)
        for rnd in (1, 2):
            rc, out = route(d, "apply", "routing/R9.json")
            assert rc == 0, f"apply {rnd}: {out}"
            assert "removed 2 segments, 1 vias, 1 polygons" in out, out
            man = json.loads((d / "routing" / "R9.applied.json").read_text())
            assert len(man["removed_items"]) == 4 and all(r.get("sexpr") for r in man["removed_items"]), man
            s1 = snapshot(pcb)
            assert diff(s0, s1)[0] and diff(s0, s1)[1], "apply changed nothing"
            rc, out = route(d, "rip", "R9")
            assert rc == 0, f"rip {rnd}: {out}"
            assert "restored 4 of 4 removed items" in out, out
            s2 = snapshot(pcb)
            gone, extra, changed = diff(s0, s2)
            assert not (gone or extra or changed), f"round {rnd}: gone {gone} extra {extra} changed {changed}"
            (d / "routing" / "R9.applied.json.ripped").unlink()
            assert not locks(d), locks(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_gone_matches():
    d, pcb = fixture("gone")
    try:
        rc, out = route(d, "apply", "routing/R9.json")
        assert rc == 0, out
        # an OLD rip: only the added copper taken back, the manifest without its record
        import pcbnew as pn
        man = d / "routing" / "R9.applied.json"
        j = json.loads(man.read_text())
        added = set(j["tracks"] + j["vias"] + j["zones"] + j["polys"])
        b = pn.LoadBoard(str(pcb))
        for it in list(b.GetTracks()) + list(b.GetDrawings()):
            if it.m_Uuid.AsString() in added:
                b.RemoveNative(it)
        pn.SaveBoard(str(pcb), b)
        del j["removed_items"]
        man.with_suffix(".json.ripped").write_text(json.dumps(j, indent=1))
        man.unlink()
        rc, out = route(d, "apply", "routing/R9.json")
        assert rc == 0, out
        assert out.count("already gone") >= 4, out
        rc, out = route(d, "rip", "R9")
        assert rc == 0, out
        assert "no record" in out, out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_missing_ripvia_releases_lock():
    d, pcb = fixture("lock")
    try:
        bad = dict(PLAN, group="R8", ripvias=[{"net": "/N1", "at": [16, 10]}])
        (d / "routing" / "R8.json").write_text(json.dumps(bad, indent=1))
        rc, out = route(d, "apply", "routing/R8.json")
        assert rc == 2, out
        assert "/N1 via at [16, 10]" in out, out
        assert not locks(d), f"left behind: {locks(d)}\n{out}"
        rc, out = route(d, "apply", "routing/R9.json")
        assert rc == 0, out
        assert not locks(d), locks(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    fails = 0
    for name, fn in [(k, v) for k, v in dict(globals()).items() if k.startswith("test_")]:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL {name}: {str(e)[-2500:]}")
    sys.exit(1 if fails else 0)
