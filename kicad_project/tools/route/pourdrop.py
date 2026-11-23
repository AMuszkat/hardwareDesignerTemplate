#!/usr/bin/env python3
"""A rail's DC drop through its pours, feed vias to every ball: the sheet solved on the fill.

    tools/kpy tools/route/pourdrop.py --board board1 --net VDD_GPU_VPU_DRAM --layers In4.Cu,In5.Cu \\
        --from '21.31,13.47;23.05,12.48;23.15,12.98' --to U1.balls \\
        --amps 2.5 --copper-um 35 --hot-c 105 --max-mohm 6.4 [--raster 0.025] [--json]
    tools/.venv/bin/python tools/route/pourdrop.py --selftest

`pourneck.py` asks how WIDE the copper is across the worst cut and judges it by IPC-2221's fit --
the measure for a pour on its way to a part.  Inside a fine-pitch ball field that fit cannot be
met and is not the physics: the copper between two antipads is a web a fraction of a millimetre
long, sunk into plane at both ends, and what it costs the rail is millivolts.  This measures the
millivolts.  It also closes what a width to the balls leaves open: a ball via the pour never
reaches does not fail a cut (a flow ignores a sink it cannot reach); here it is named and fails.

The model:
  * ONE net's copper on each of --layers (the zone fill, refilled in memory and never saved, its
    tracks, via pads and pads there: `pourneck.geom_of`), rasterised on one grid.  Each layer is a
    sheet of --copper-um copper at --hot-c: rho = 1.724e-8 ohm m x (1 + 0.00393 (T - 20)) (IEC
    60028), a cell-to-cell conductance of one square.
  * a VIA of the net is one node: the cells of its pad on every listed layer it is flashed on are
    tied together, so it carries current between the layers (its barrel between two listed layers
    taken as ideal -- a fraction of a milliohm, several in parallel) and is equipotential.
  * the FEED items (--from, as pourneck.py: 'x,y' vias or pads, REF, REF.PIN) are held at 0 V on
    every layer they appear on.  A layer may lack them: it is then fed through the vias.
  * the LOAD is --to REF.balls: every via in, or one straight track from, a pad of REF on the net
    (and REF's own pads on a listed layer).  Each sinks --amps x (the pads it serves / REF's pads
    on the net): the rail's current shared equally among its balls, as a ball-field rule has it.
  * NOT in the number, and to be budgeted beside it: the barrels (feed vias down, ball vias up),
    the copper from the regulator to the feed vias, and the top-layer link from a ball via to its
    ball.  The limit given here is the page's limit less those.

It prints the worst ball via's drop in milliohm of the whole rail current (and mV), the mean, the
vias reached and the balls served.  --max-mohm gives PASS / FAIL; a ball via the copper does not
reach, or a ball with no via, is FAIL whatever the drop.  Exit 0 PASS (or no limit), 1 FAIL, 2 bad
input.  The self-heating of any copper between the feeds and the balls is bounded by the drop
alone, whatever its shape: dT <= V^2 / (8 k rho) above the copper it starts and ends in
(Kohlrausch; `studies/common.py` joule_rise_bound) -- 20 mV in hot copper is under 6 K.

Two interpreters, as pourneck.py: KiCad's (tools/kpy) reads the board, tools/.venv solves;
`--geom FILE` re-runs the solve on a saved geometry (--save-geom).
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PY = HERE.parent / ".venv" / "bin" / "python"
RHO20_OHM_MM, TCR = 1.724e-5, 0.00393                # annealed copper, IEC 60028


def sheet_mohm(copper_um, hot_c):
    """milliohm per square of `copper_um` copper at `hot_c`."""
    return RHO20_OHM_MM * (1 + TCR * (hot_c - 20.0)) / (copper_um / 1000.0) * 1e3


# ---------------------------------------------------------------------------
# geometry, in KiCad's interpreter
# ---------------------------------------------------------------------------

def geoms_of(B, a, refill=True):
    """The net's copper on each of a.layers from a loaded place.Board / RBoard (pourneck.geom_of,
    lenient: a layer may hold no feed, no ball via, no copper).  Raises pourneck.Fail."""
    import pourneck as PN
    from types import SimpleNamespace
    gs = []
    for i, lay in enumerate(a.layers):
        ns = SimpleNamespace(net=a.net, layer=lay, sfrom=a.sfrom, sto=a.sto, balls=None, source_disc=None,
                             lenient=True)
        gs.append(PN.geom_of(B, ns, refill=refill and i == 0))
    if not any(g["fill"] or g["items"] for g in gs):
        raise PN.Fail(f"{a.net} has no copper on {', '.join(a.layers)}")
    if not any(g["sources"] for g in gs):
        raise PN.Fail(f"--from {a.sfrom}: none of the feed items is on {', '.join(a.layers)}")
    return {"board": gs[0]["board"], "net": a.net, "to": a.sto, "balls_ref": gs[0]["balls_ref"],
            "balls": gs[0]["balls"], "layers": gs}


def extract(a):
    sys.path.insert(0, str(HERE.parent / "place"))
    import place as P  # noqa: E402
    B = P.Board(P.board_dir_arg(a.board))
    return geoms_of(B, a)


# ---------------------------------------------------------------------------
# the solve, in tools/.venv
# ---------------------------------------------------------------------------

def solve(G, raster, amps, copper_um, hot_c):
    import numpy as np
    from matplotlib.path import Path as MPath
    from scipy.sparse import coo_matrix, diags
    from scipy.sparse.csgraph import connected_components
    from scipy.sparse.linalg import spsolve

    layers = G["layers"]
    allr = [pt for g in layers for poly in g["fill"] + g["items"] for pt in poly[0]]
    xs, ys = [p[0] for p in allr], [p[1] for p in allr]
    x0, y0 = min(xs) - 2 * raster, min(ys) - 2 * raster
    nx = int(math.ceil((max(xs) + 2 * raster - x0) / raster))
    ny = int(math.ceil((max(ys) + 2 * raster - y0) / raster))
    cx = x0 + (np.arange(nx) + 0.5) * raster
    cy = y0 + (np.arange(ny) + 0.5) * raster

    def paint(polys):
        m = np.zeros((ny, nx), bool)
        for poly in polys:
            outer = np.asarray(poly[0])
            if len(outer) < 3:
                continue
            i0 = max(0, int((outer[:, 0].min() - x0) / raster) - 1)
            i1 = min(nx, int((outer[:, 0].max() - x0) / raster) + 2)
            j0 = max(0, int((outer[:, 1].min() - y0) / raster) - 1)
            j1 = min(ny, int((outer[:, 1].max() - y0) / raster) + 2)
            if i1 <= i0 or j1 <= j0:
                continue
            gx, gy = np.meshgrid(cx[i0:i1], cy[j0:j1])
            pts = np.column_stack([gx.ravel(), gy.ravel()])
            sub = MPath(outer).contains_points(pts)
            for hole in poly[1:]:
                if len(hole) >= 3:
                    sub &= ~MPath(np.asarray(hole)).contains_points(pts)
            m[j0:j1, i0:i1] |= sub.reshape(j1 - j0, i1 - i0)
        return m

    key = lambda at: (round(at[0], 3), round(at[1], 3))
    n = 0
    tied = {}                                     # a via / a ball pad -> its one node
    sinks = {}                                    # key -> {"at", "pads": set, "label"}
    nids, fixed_cells = [], []
    for g in layers:
        cu = paint(g["fill"]) | paint(g["items"])
        nid = -np.ones((ny, nx), np.int64)
        for it in g.get("vias", []) + g.get("sink_items", []):
            m = paint(it["rings"]) & cu & (nid < 0)
            if not m.any():
                continue
            k = key(it["at"])
            if k not in tied:
                tied[k] = n
                n += 1
            nid[m] = tied[k]
        for it in g.get("sink_items", []):
            s = sinks.setdefault(key(it["at"]), {"at": it["at"], "pads": set(), "label": it.get("label", "")})
            s["pads"] |= set(it["pads"])
        rest = cu & (nid < 0)
        k = int(rest.sum())
        nid[rest] = np.arange(n, n + k)
        n += k
        nids.append(nid)
        fixed_cells.append(paint([p for s in g["sources"] for p in s["rings"]]) & cu)
    if not sinks:
        return {"error": f"--to {G.get('to')}: no ball via or pad of the net on the listed layers"}
    rows, cols = [], []
    for nid in nids:
        for dj, di in ((0, 1), (1, 0)):
            a_, b_ = nid[:ny - dj, :nx - di], nid[dj:, di:]
            e = (a_ >= 0) & (b_ >= 0) & (a_ != b_)
            rows += [a_[e], b_[e]]
            cols += [b_[e], a_[e]]
    R, C = np.concatenate(rows), np.concatenate(cols)
    sq = sheet_mohm(copper_um, hot_c) * 1e-3      # ohm a square
    A = coo_matrix((np.full(len(R), 1.0 / sq), (R, C)), shape=(n, n)).tocsr()
    A.sum_duplicates()
    fix = np.zeros(n, bool)
    for nid, fc in zip(nids, fixed_cells):
        fix[nid[fc]] = True
    if not fix.any():
        return {"error": "the feed items cover no copper cell at this raster"}
    _, comp = connected_components(A, directed=False)
    live = np.isin(comp, np.unique(comp[fix]))
    served = set().union(*[s["pads"] for s in sinks.values()])
    balls = G.get("balls") or len(served)
    cur = np.zeros(n)
    out_s, unreached = [], []
    for k, s in sorted(sinks.items()):
        node = tied.get(k)
        if node is None or not live[node] or fix[node]:
            if node is None or not live[node]:
                unreached.append({"at": [round(v, 3) for v in s["at"]], "pads": sorted(s["pads"])})
            continue
        cur[node] -= amps * len(s["pads"]) / balls
        out_s.append((k, node, s))
    V = np.zeros(n)
    free = live & ~fix
    if out_s:
        L = (diags(np.asarray(A.sum(axis=1)).ravel()) - A).tocsr()
        V[free] = spsolve(L[free][:, free].tocsc(), cur[free])
    res = [{"at": [round(v, 3) for v in s["at"]], "pads": sorted(s["pads"]),
            "mohm": round(float(-V[node]) / amps * 1e3, 3)} for _k, node, s in out_s]
    res.sort(key=lambda r: -r["mohm"])
    tot = sum(len(r["pads"]) for r in res)
    out = {"sheet_mohm_sq": round(sq * 1e3, 4), "cells": int(n), "grid": [int(nx), int(ny)],
           "sinks": res, "unreached": unreached, "reached": f"{len(res)}/{len(sinks)}",
           "balls_served": f"{len(served)}/{balls}", "unserved": max(0, balls - len(served))}
    if res:
        out.update({"worst_mohm": res[0]["mohm"], "worst_at": res[0]["at"],
                    "worst_mv": round(res[0]["mohm"] * amps, 2),
                    "mean_mohm": round(sum(r["mohm"] * len(r["pads"]) for r in res) / tot, 3)})
    return out


def judge(G, a):
    out = {"net": G["net"], "layers": [g["layer"] for g in G["layers"]], "board": G.get("board"),
           "raster_mm": a.raster, "amps": a.amps, "copper_um": a.copper_um, "hot_c": a.hot_c, "to": G.get("to"),
           "from": sorted({s["label"] for g in G["layers"] for s in g["sources"]})}
    r = solve(G, a.raster, a.amps, a.copper_um, a.hot_c)
    out.update(r)
    if "error" in r:
        return out, 2
    whole = not r["unreached"] and not r["unserved"] and "worst_mohm" in r
    if a.max_mohm is None:
        out["verdict"] = None if whole else "FAIL"
        return out, 0 if whole else 1
    out["max_mohm"] = a.max_mohm
    out["verdict"] = "PASS" if whole and r["worst_mohm"] <= a.max_mohm + 1e-9 else "FAIL"
    return out, 0 if out["verdict"] == "PASS" else 1


def measure(G, raster, amps, copper_um, hot_c, max_mohm):
    """The solve on geometry G, run in tools/.venv: the result dict (see judge) and its exit code.
    For a caller in KiCad's interpreter (checks.py's `pour drop`)."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="pourdrop_")
    os.close(fd)
    try:
        Path(path).write_text(json.dumps(G))
        cmd = [str(VENV_PY), __file__, "--geom", path, "--raster", str(raster), "--amps", str(amps),
               "--copper-um", str(copper_um), "--hot-c", str(hot_c), "--max-mohm", str(max_mohm), "--json"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return json.loads(r.stdout), r.returncode
        except ValueError:
            return {"error": (r.stderr or r.stdout).strip()[-400:]}, 2
    finally:
        os.unlink(path)


def faults(out):
    """What fails besides the number, as text."""
    f = []
    if out.get("unreached"):
        f.append("NOT REACHED: " + ", ".join(f"ball via ({u['at'][0]:.2f}, {u['at'][1]:.2f}) [{' '.join(u['pads'])}]"
                                             for u in out["unreached"]))
    if out.get("unserved"):
        f.append(f"{out['unserved']} ball(s) of the net have no via in or linked straight to their pad")
    return f


def show(out):
    name = f"{out['net']} {'+'.join(out['layers'])}"
    if "error" in out:
        print(f"POURDROP ERROR {name}: {out['error']}")
        return
    v = out.get("verdict")
    head = f"POURDROP {v + ' ' if v else ''}{name}: "
    if "worst_mohm" in out:
        head += (f"worst {out['worst_mohm']:.2f} mohm at ball via ({out['worst_at'][0]:.2f}, {out['worst_at'][1]:.2f}) "
                 f"({out['worst_mv']:.1f} mV at {out['amps']:g} A), mean {out['mean_mohm']:.2f}")
    else:
        head += "no ball via reached"
    if out.get("max_mohm") is not None:
        head += f"; limit {out['max_mohm']:g} mohm"
    print(head)
    print(f"  ball vias reached {out['reached']}, balls served {out['balls_served']}; sheet {out['sheet_mohm_sq']:.3f} "
          f"mohm/sq ({out['copper_um']:g} um, {out['hot_c']:g} C); raster {out['raster_mm']:g} mm, {out['cells']} nodes")
    print(f"  from: {'; '.join(out['from'])}")
    for f in faults(out):
        print("  " + f)
    for s in out["sinks"]:
        print(f"    ({s['at'][0]:7.3f}, {s['at'][1]:7.3f})  {s['mohm']:6.2f} mohm  {len(s['pads'])} ball(s): {' '.join(s['pads'])}")


# ---------------------------------------------------------------------------

def selftest():
    """Cases with a hand-calculated resistance, in squares of the sheet."""
    from types import SimpleNamespace
    sq = sheet_mohm(35, 20)                       # 0.4926 mohm a square
    rect = lambda x0, y0, x1, y1: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]
    circ = lambda x, y, r, n=180: [[x + r * math.cos(2 * math.pi * k / n), y + r * math.sin(2 * math.pi * k / n)]
                                   for k in range(n)]
    lay = lambda name, fill, src, sinks, vias=(): {
        "layer": name, "fill": fill, "items": [], "sources": [{"label": "S", "rings": src}] if src else [],
        "sink_items": [{"at": at, "rings": rg, "pads": pads} for at, rg, pads in sinks],
        "vias": [{"at": at, "rings": rg} for at, rg in vias]}
    strip = [rect(0, 0, 10, 1)]
    feed, load = [rect(0, 0, 0.2, 1)], ([9.9, 0.5], [rect(9.8, 0, 10, 1)], ["A1"])
    cases = []
    cases.append(("a strip, 9.6 squares", [lay("L1", strip, feed, [load])], 1, 9.6 * sq, 0.01, "PASS"))
    cases.append(("the same strip on two layers, tied at feed and load: 4.8 squares",
                  [lay("L1", strip, feed, [load]), lay("L2", strip, feed, [load])], 1, 4.8 * sq, 0.01, "PASS"))
    tie = ([0.6, 0.5], [rect(0.5, 0, 0.7, 1)])
    cases.append(("a second layer fed only through a via: 0.3 + 9.1 / 2 squares",
                  [lay("L1", strip, feed, [load], [tie]), lay("L2", strip, None, [load], [tie])], 1,
                  (0.3 + 9.1 / 2) * sq, 0.01, "PASS"))
    disc = [[circ(5, 5, 3.0, 720)]]
    ring = [[circ(5, 5, 3.0, 720), circ(5, 5, 2.8, 720)]]
    cases.append(("a disc fed at its rim, one ball via r 0.2 at the centre: ln(2.8 / 0.2) / 2 pi squares",
                  [lay("L1", disc, ring, [([5, 5], [[circ(5, 5, 0.2)]], ["A1"])])], 1,
                  math.log(2.8 / 0.2) / (2 * math.pi) * sq, 0.03, "PASS"))
    two = [([9.9, 0.5], [rect(9.8, 0, 10, 1)], ["A1", "A2", "A3"]), ([0.3, 4.9], [rect(0, 4.8, 1, 5)], ["B1"])]
    ell = [[[[0, 0], [10, 0], [10, 1], [1, 1], [1, 5], [0, 5]]]]
    # an L fed over its whole corner square: two strips from one pad, 3/4 of the current down the long arm
    cases.append(("two ball vias, 3 balls and 1: the long arm reads 3/4 x 8.8 squares",
                  [lay("L1", ell, [rect(0, 0, 1, 1)], two)], 4, 0.75 * 8.8 * sq, 0.01, "PASS"))
    gap = [rect(0, 0, 4, 1), rect(4.5, 0, 10, 1)]
    cases.append(("a ball via the copper does not reach", [lay("L1", gap, feed, [load])], 1, None, 0, "FAIL"))
    cases.append(("a ball with no via: 1 served of 2", [lay("L1", strip, feed, [load])], 2, None, 0, "FAIL"))
    bad = 0
    for raster in (0.05, 0.025):
        for name, layers, balls, want, tol, verdict in cases:
            G = {"net": "N", "layers": layers, "balls": balls, "to": "U1.balls"}
            a = SimpleNamespace(raster=raster, amps=2.0, copper_um=35, hot_c=20, max_mohm=100.0)
            out, rc = judge(G, a)
            got = out.get("worst_mohm")
            if want is None:
                ok = out.get("verdict") == verdict and rc == 1
                print(f"  {'ok  ' if ok else 'FAIL'} raster {raster}: {name}: {out.get('verdict')}")
            else:
                t = tol * raster / 0.025              # the pads' edges land on the grid: the error is one cell
                ok = got is not None and abs(got - want) <= t * want and out.get("verdict") == verdict
                print(f"  {'ok  ' if ok else 'FAIL'} raster {raster}: {name}: {got} mohm, want {want:.3f} +- {t:.0%}")
            bad += not ok
    ok = abs(sheet_mohm(35, 105) - 0.6571) < 5e-4
    print(f"  {'ok  ' if ok else 'FAIL'} 35 um at 105 C: {sheet_mohm(35, 105):.4f} mohm/sq, want 0.6571")
    bad += not ok
    print("POURDROP SELFTEST " + ("PASS" if not bad else f"FAIL ({bad})"))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--net")
    ap.add_argument("--layers", help="the layers that carry the rail, comma-separated (In4.Cu,In5.Cu)")
    ap.add_argument("--from", dest="sfrom", default="", help="'x,y;x,y' feed vias/pads, or REF / REF.PIN")
    ap.add_argument("--to", dest="sto", default="", help="REF.balls: the vias that feed REF's pads of the net")
    ap.add_argument("--amps", type=float, default=None, help="the rail's current, shared equally among REF's pads on the net")
    ap.add_argument("--copper-um", type=float, default=None, help="the layers' copper thickness, um")
    ap.add_argument("--hot-c", type=float, default=None, help="the copper's temperature, C (the page's hot corner)")
    ap.add_argument("--max-mohm", type=float, default=None, help="the limit on the worst ball via's drop, mohm of --amps")
    ap.add_argument("--raster", type=float, default=0.025, help="mm (default 0.025: a web between antipads is 0.1-0.5 mm)")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    ap.add_argument("--save-geom", default=None, help="keep the net's geometry here (a --geom input)")
    ap.add_argument("--geom", default=None, help="skip the board: run the solve on a saved geometry")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    try:
        import scipy  # noqa: F401
        have = True
    except ImportError:
        have = False
    if (a.selftest or a.geom) and not have:
        return subprocess.call([str(VENV_PY), __file__] + sys.argv[1:])
    if a.selftest:
        return selftest()
    if a.amps is None or a.copper_um is None or a.hot_c is None:
        ap.error("--amps, --copper-um and --hot-c are required: the board's numbers, never defaults here")
    if a.geom:
        out, rc = judge(json.loads(Path(a.geom).read_text()), a)
        if a.json:
            print(json.dumps(out, indent=1))
        else:
            show(out)
        return rc
    if not (a.net and a.layers and a.sfrom and a.sto):
        ap.error("--net, --layers, --from and --to are required (or --geom / --selftest)")
    if not a.sto.endswith(".balls"):
        ap.error("--to takes REF.balls: the drop is measured to the vias that feed REF's pads of the net")
    a.layers = [s.strip() for s in a.layers.replace("+", ",").split(",") if s.strip()]
    sys.path.insert(0, str(HERE))
    import pourneck as PN
    try:
        G = extract(a)
    except PN.Fail as e:
        print(f"POURDROP ERROR {a.net} {'+'.join(a.layers)}: {e}")
        return 2
    path, tmp = a.save_geom, None
    if not path:
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix="pourdrop_")
        os.close(fd)
        path = tmp
    Path(path).write_text(json.dumps(G))
    cmd = [str(VENV_PY), __file__, "--geom", path, "--raster", str(a.raster), "--amps", str(a.amps),
           "--copper-um", str(a.copper_um), "--hot-c", str(a.hot_c)]
    if a.max_mohm is not None:
        cmd += ["--max-mohm", str(a.max_mohm)]
    if a.json:
        cmd.append("--json")
    try:
        return subprocess.call(cmd)
    finally:
        if tmp:
            os.unlink(tmp)


if __name__ == "__main__":
    sys.exit(main())
