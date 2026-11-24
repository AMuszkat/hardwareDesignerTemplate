#!/usr/bin/env python3
"""Tests for sitesolve.py -- the exact via-site count (gen: pcbnew, solve: CP-SAT, table: both, parallel).

    tools/kpy tools/route/test_sitesolve.py                    the synthetic board, anywhere
    tools/kpy tools/route/test_sitesolve.py --board-test [--copy DIR] [--spec SPEC] [--r10c DIR] [--out DIR]
                                                               + board1's identity (skipped without the copy)

THE SYNTHETIC BOARD (built in a temporary folder OUTSIDE kicad_project/, class clearance 0.1, via 0.4/0.2,
track 0.1): six nets S0..S5 on SMD pads 0.3 mm apart in a row at y 12.0, each allowed ONE site, straight
north at y 10.0 (so a net's via stands 0.3 from its neighbour's link: 0.3 - 0.2 - 0.05 < 0.1, adjacent
nets exclude each other).  Fixed copper: a via of net Q at (9.4, 10.0) kills S0; another at
(10.0, 9.55) kills S1 (c-c 0.45 < 0.5) and spares S2 (c-c 0.541); a net-tied polygon of net P over
x 11.0-13.0 kills S4 (edge 0.1 away) and S5 (inside).  By hand:
    base                 S2, S3 live, adjacent         -> 1 of 6, OPTIMAL
    --pull P:0.6         P's west edge to x 11.6: S4, S5 live too; {S2, S4} -> 2 of 6, OPTIMAL
    --need 6             six nets, at most two slots   -> INFEASIBLE
    --need 2 (base)      one slot                      -> INFEASIBLE
All four through `table` (par.cmap, the table.tsv), then `run` on the same folder takes the cached
candidates.

THE BOARD TEST: board1 NORTHGAP r10c (gen10c.py + solve10c.py, hard-coded) against the same strip as a
spec (board1_module/tmpWork/SITESOLVE/build_ng_spec.py): the rows of r10c/rows10c.json named below must
come back with r10c's count and failing nets, and 8 of 8 INFEASIBLE (r10c STATE T6).  The copy must be
outside kicad_project/ (a copy inside a board folder IS that board to every tool).
The last line is `TEST_SITESOLVE PASS|FAIL: n failure(s)`; exit 0 pass, 1 fail, 2 cannot run.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KP = HERE.parent.parent
TOOL = HERE / "sitesolve.py"
KPY = HERE.parent / "kpy"
FAILS = []


def ok(cond, what):
    print(("ok    " if cond else "FAIL  ") + what, flush=True)
    if not cond:
        FAILS.append(what)


def opt(a, name, default=None):
    return a[a.index(name) + 1] if name in a else default


def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def table_rows(out):
    """table.tsv -> {label: (sited/N, failing, status, solve file)}"""
    lines = (Path(out) / "table.tsv").read_text().splitlines()[1:]
    return {c[0]: (c[1], c[2], c[4], c[5]) for c in (ln.split("\t") for ln in lines)}


# ---------------------------------------------------------------------------------------- synthetic
SPEC = {
    "nets": {f"S{i}": {"pad": f"J1.{i + 1}"} for i in range(6)},
    "region": [9.0, 10.0, 13.0, 10.0], "grid": 0.1,
    "link": {"layer": "F.Cu", "style": "straight", "axis": "y", "max_lateral": 0.0},
    "box": [8.0, 8.0, 14.0, 13.0],
    "pulls": {"P": {"net": "P", "layer": "F.Cu", "verts": [10.9, 8.9, 11.1, 11.1], "dir": [1, 0]}},
}
ROWS = [{"label": "base", "gen": [], "solve": []},
        {"label": "pull P 0.6", "gen": ["--pull", "P:0.6"], "solve": []},
        {"label": "need 6", "gen": [], "solve": ["--need", "6"]},
        {"label": "need 2", "gen": [], "solve": ["--need", "2"]}]


def build(d):
    import pcbnew as pn
    FM = pn.FromMM
    pcb = Path(d) / "synth.kicad_pcb"
    b = pn.NewBoard(str(pcb))
    F, E = b.GetLayerID("F.Cu"), b.GetLayerID("Edge.Cuts")
    nets = {}
    for n in [f"S{i}" for i in range(6)] + ["Q", "P"]:
        nets[n] = pn.NETINFO_ITEM(b, n)
        b.Add(nets[n])
    r = pn.PCB_SHAPE(b)
    r.SetShape(pn.SHAPE_T_RECT)
    r.SetStart(pn.VECTOR2I(FM(0), FM(0)))
    r.SetEnd(pn.VECTOR2I(FM(20), FM(20)))
    r.SetLayer(E)
    r.SetWidth(FM(0.1))
    b.Add(r)
    fp = pn.FOOTPRINT(b)
    fp.SetReference("J1")
    fp.SetPosition(pn.VECTOR2I(FM(10), FM(12)))
    b.Add(fp)
    rect = getattr(pn, "PAD_SHAPE_RECTANGLE", None) or pn.PAD_SHAPE_RECT
    for i in range(6):
        p = pn.PAD(fp)
        p.SetNumber(str(i + 1))
        p.SetAttribute(pn.PAD_ATTRIB_SMD)
        p.SetLayerSet(p.SMDMask())
        p.SetShape(pn.PADSTACK.ALL_LAYERS, rect)
        p.SetSize(pn.PADSTACK.ALL_LAYERS, pn.VECTOR2I(FM(0.15), FM(0.15)))
        fp.Add(p)
        p.SetPosition(pn.VECTOR2I(FM(9.7 + 0.3 * i), FM(12.0)))
        p.SetNet(nets[f"S{i}"])
    for x, y in ((9.4, 10.0), (10.0, 9.55)):
        v = pn.PCB_VIA(b)
        v.SetPosition(pn.VECTOR2I(FM(x), FM(y)))
        v.SetWidth(FM(0.4))
        v.SetDrill(FM(0.2))
        v.SetLayerPair(F, b.GetLayerID("B.Cu"))
        b.Add(v)
        v.SetNet(nets["Q"])
    s = pn.PCB_SHAPE(b)
    s.SetShape(pn.SHAPE_T_POLY)
    s.SetFilled(True)
    s.SetWidth(0)
    s.SetLayer(F)
    s.SetPolyPoints([pn.VECTOR2I(FM(x), FM(y)) for x, y in ((11.0, 9.0), (13.0, 9.0), (13.0, 11.0), (11.0, 11.0))])
    b.Add(s)
    s.SetNetCode(nets["P"].GetNetCode())
    pn.SaveBoard(str(pcb), b)
    # the project file, written after the board (rules and the one class the count reads)
    (Path(d) / "synth.kicad_pro").write_text(json.dumps({
        "board": {"design_settings": {"rules": {"min_clearance": 0.1, "min_hole_clearance": 0.1,
                                                "min_hole_to_hole": 0.25}}},
        "meta": {"filename": "synth.kicad_pro", "version": 1},
        "net_settings": {"classes": [{"name": "Default", "clearance": 0.1, "track_width": 0.1,
                                      "via_diameter": 0.4, "via_drill": 0.2}], "meta": {"version": 3}}}, indent=1))
    return pcb


def synthetic():
    print("== synthetic board", flush=True)
    with tempfile.TemporaryDirectory(prefix="sitesolve_") as t:
        d, out = Path(t) / "board", Path(t) / "out"
        d.mkdir()
        build(d)
        spec = Path(t) / "spec.json"
        spec.write_text(json.dumps(SPEC))
        rows = Path(t) / "rows.json"
        rows.write_text(json.dumps(ROWS))
        rc, txt = sh([sys.executable, TOOL, "table", "--board", d, spec, rows, "--out", out, "--jobs", "2"])
        if rc or not (out / "table.tsv").exists():
            print(txt)
            ok(False, f"table ran (rc {rc})")
            return
        T = table_rows(out)
        cf = sorted(out.glob("cands_*.json.gz"))
        ok(len(cf) == 2, f"two candidate sets for four rows (base, pulled): {len(cf)}")
        base = json.loads((out / T["base"][3]).read_text())
        ok(T["base"][2] == "OPTIMAL" and T["base"][0] == "1/6", f"base: 1 of 6, OPTIMAL -- {T['base'][0]} {T['base'][2]}")
        ok(set(base.get("nets") or {}) <= {"S2", "S3"}, f"base sites S2 or S3: {sorted(base.get('nets') or {})}")
        ok(T["pull P 0.6"][2] == "OPTIMAL" and T["pull P 0.6"][0] == "2/6",
           f"--pull P:0.6: 2 of 6, OPTIMAL -- {T['pull P 0.6'][0]} {T['pull P 0.6'][2]}")
        ok(T["need 6"][2] == "INFEASIBLE", f"--need 6 (N > slots): INFEASIBLE -- {T['need 6'][2]}")
        ok(T["need 2"][2] == "INFEASIBLE", f"--need 2 at base: INFEASIBLE -- {T['need 2'][2]}")
        import gzip
        for c in cf:
            D = json.load(gzip.open(c, "rt"))
            pulled = "--pull" in D["args"]
            nets = sorted({c["net"] for c in D["cands"]})
            exp = ["S2", "S3", "S4", "S5"] if pulled else ["S2", "S3"]
            ok(nets == exp and len(D["cands"]) == len(exp),
               f"{'pulled' if pulled else 'base'}: one site each for {exp} only (Q's vias, P's polygon kill the rest): {nets}")
            ok(D["rules"]["min_clearance"] == 0.1 and D["rules"]["link_width"] == 0.1 and D["rules"]["via"] == [0.4, 0.2],
               f"{c.name}: rules from the board (class clearance 0.1, track 0.1, via 0.4/0.2): {D['rules']}")
        rc, txt = sh([sys.executable, TOOL, "run", "--board", d, spec, "--out", out, "--pull", "P:0.6"])
        ok(rc == 0 and "candidates cached" in txt and "OPTIMAL sited 2/6" in txt,
           "run --pull P:0.6 on the table's folder: cached candidates, OPTIMAL 2/6")


# ---------------------------------------------------------------------------------------- board1 identity
ID_ROWS = [("today's terms (base)", [], []),
           ("T2+T3 SOC 0.36 + AUD_FS", ["--pull", "VDD_SOC:0.36", "--drop", "AUD_FS", "--line", "AUD_FS"], [])]


def board_test(a):
    print("== board1 NORTHGAP r10c identity", flush=True)
    copy = Path(opt(a, "--copy", "~/.cache/board1_tmp/NG_r10S")).expanduser().resolve()
    spec = Path(opt(a, "--spec", KP / "board1_module/tmpWork/SITESOLVE/ng_r10c.json")).expanduser().resolve()
    r10c = Path(opt(a, "--r10c", KP / "board1_module/tmpWork/NORTHGAP/r10c")).expanduser().resolve()
    miss = [str(p) for p in (copy, spec, r10c / "rows10c.json") if not p.exists()]
    if miss:
        print(f"SKIP board test: absent {', '.join(miss)}")
        return
    if KP.resolve() in copy.parents:
        print(f"board test: the copy {copy} is inside kicad_project/ -- a copy there is the real board to the tools")
        sys.exit(2)
    out = Path(opt(a, "--out") or tempfile.mkdtemp(prefix="sitesolve_id_")).expanduser().resolve()
    want = {r[0]: r for r in json.loads((r10c / "rows10c.json").read_text())}
    rows = [{"label": lab, "gen": g, "solve": s} for lab, g, s in ID_ROWS]
    rows.append({"label": "z8", "gen": [], "solve": ["--need", "8"]})
    out.mkdir(parents=True, exist_ok=True)
    (out / "rows.json").write_text(json.dumps(rows, indent=1))
    rc, txt = sh([sys.executable, TOOL, "table", "--board", copy, spec, out / "rows.json", "--out", out, "--jobs", "3"])
    (out / "table.log").write_text(txt)
    if rc or not (out / "table.tsv").exists():
        print(txt[-3000:])
        ok(False, f"table ran on the copy (rc {rc})")
        return
    T = table_rows(out)
    for lab, _g, _s in ID_ROWS:
        if lab not in want:
            ok(False, f"row {lab!r} is in rows10c.json")
            continue
        ref = json.loads((r10c / "out" / want[lab][1]).read_text())
        mine = json.loads((out / T[lab][3]).read_text())
        ok(mine["status"] == ref["status"] == "OPTIMAL" and mine["sited"] == ref["sited"],
           f"{lab}: {mine['status']} {mine['sited']}/8 -- r10c {ref['status']} {ref['sited']}/8 ({want[lab][1]})")
        print(f"      failing nets: {mine['failed']} -- r10c {ref['failed']}"
              f"{'' if mine['failed'] == ref['failed'] else ' (an equal count; ties may fall either way)'}")
    ok(T["z8"][2] == "INFEASIBLE", f"8 of 8 at today's terms: {T['z8'][2]} -- r10c STATE T6 INFEASIBLE")
    print(f"      results: {out}")


def main():
    a = sys.argv[1:]
    synthetic()
    if "--board-test" in a:
        board_test(a)
    print(f"TEST_SITESOLVE {'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failure(s)")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
