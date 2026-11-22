#!/usr/bin/env python3
"""The narrowest copper a pour's current must cross: a max-flow / min-cut on its fill.

    tools/kpy tools/route/pourneck.py --board board1 --net VDD_ARM --layer In4.Cu \
        --from '34.73,8.35;34.73,9.50' --to 22.5,15.0,35.5,28.0 \
        --amps 2.2 --rise 10 --copper-um 35 [--raster 0.05] [--json]
    tools/.venv/bin/python tools/route/pourneck.py --selftest

A section across a pour's long axis (`pour.py --section`) measures straight cuts; a
pour that bends round vias, antipads and another rail's fill has its narrowest cut
on a curve no straight line finds.  This takes ONE net's copper on ONE layer -- the
zone fill (refilled in memory, never saved), the net's tracks, via pads and pads on
that layer -- rasterises it, ties the cells of the FEED items (--from) to a source
and the copper inside the LOAD region (--to) to a sink, and asks for the least total
copper width across any cut separating the two: the min cut of the copper graph,
the "least copper width across any cut" of board1's RV64.  It prints that width,
the cut's pieces (each with its own width and its box, mm), the width the current
needs and PASS / FAIL.

The measure:
  * each copper cell is a node; each pair of 8-neighbour copper cells an edge, axis
    edges weighted (sqrt2 - 1) x raster and diagonal ones (1 - 1/sqrt2) x raster, so
    a straight cut at 0 or 45 degrees through a wide strip costs its length (the
    Cauchy-Crofton weights, normalised; --selftest holds it within 2 %).  A cut is a
    curve through copper; its cost is the copper it crosses.  At the strip's edges a
    0.05 mm raster loses up to about one cell (board1 RV64: 0.025 agrees with 0.05 to
    0.03 mm) -- run both when the margin is that small.
  * a VIA is its pad on this layer, copper like the fill.  A FEED via's pad cells are
    the source and a via inside the load region is sink, so its barrel is never a
    neck (10_layout's pour-cut row: "a via's barrel is not a neck" -- it is judged by
    the via rule); the copper round a feed via's pad IS judged (board1 RV64:
    NVCC_DRAM's narrowest cut is the ring round its feed vias).  Same-net copper on
    other layers is NOT modelled: a cut that another layer's copper bypasses reads
    narrower here than it is -- say so beside the number when there is any.
  * the required width is IPC-2221's fit, internal layer: I = 0.024 x dT^0.44 x
    A^0.725, A in mil2 (A = width x thickness); --rise (K) and --copper-um are the
    board's numbers (its layout page), never defaults here.  The current is --amps,
    or --amps-per-ball x the --balls part's pads on the net (all of them are behind
    a cut to the whole field; a cut that has some balls on the feed side carries
    less -- this tool does not split the field, so it judges the whole current).

--from: ';'-separated items: 'x,y' (the net's via or pad on --layer at that point,
within 0.3 mm), 'REF' or 'REF.PIN' (that part's pads of the net on --layer).
--to: 'x0,y0,x1,y1' (the net's copper with its cell centre inside the box), or 'REF'
(that part's courtyard box).  Exit 0 PASS, 1 FAIL, 2 bad input.

Two interpreters: KiCad's (tools/kpy) reads and refills the board -- loaded with its
project files, since a lone .kicad_pcb fills at default clearances -- and writes the
net's geometry (--save-geom keeps it); the flow runs in tools/.venv (numpy, scipy),
started by this script on that file.  `--geom FILE` re-runs only the flow (another
--raster, other amps) in seconds.
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
IPC_K, IPC_B, IPC_C = 0.024, 0.44, 0.725            # IPC-2221 internal-layer fit (constants of the fit)
MIL = 0.0254


def need_width_mm(amps, rise_k, copper_um):
    """IPC-2221 internal: the width (mm) that carries `amps` at `rise_k` on `copper_um`."""
    area = (amps / (IPC_K * rise_k ** IPC_B)) ** (1.0 / IPC_C)          # mil2
    return area / (copper_um / 1000.0 / MIL) * MIL


def carries_amps(width_mm, rise_k, copper_um):
    area = (width_mm / MIL) * (copper_um / 1000.0 / MIL)
    return IPC_K * rise_k ** IPC_B * area ** IPC_C


# ---------------------------------------------------------------------------
# geometry, in KiCad's interpreter
# ---------------------------------------------------------------------------

def extract(a):
    sys.path.insert(0, str(HERE.parent / "place"))
    import place as P  # noqa: E402
    B = P.Board(P.board_dir_arg(a.board))
    return geom_of(B, a)


class Fail(SystemExit):
    pass


def geom_of(B, a, refill=True):
    """The net's copper on a.layer from a loaded place.Board (route.py's RBoard too): fill, items,
    the --from items' shapes, the load box.  Refills B's zones in memory (never saves)."""
    import place as P  # noqa: E402  (on sys.path: extract, or route.py)
    sys_exit = sys.exit
    def die(msg):
        raise Fail(msg)
    sys.exit = die
    try:
        return _geom_of(B, a, refill, P)
    finally:
        sys.exit = sys_exit


def _geom_of(B, a, refill, P):
    pn, b, mm = B.pn, B.b, B.mm
    short = lambda n: n.split("/")[-1]
    lid = b.GetLayerID(a.layer)
    if lid < 0:
        lid = getattr(pn, a.layer.replace(".", "_"), -1)
    if lid < 0:
        sys.exit(f"--layer {a.layer}: no such layer")
    if refill:
        with P.quiet_stderr():
            pn.ZONE_FILLER(b).Fill(b.Zones())

    def rings(ps):
        out = []
        for i in range(ps.OutlineCount()):
            ch = ps.Outline(i)
            r = [[[mm(ch.CPoint(k).x), mm(ch.CPoint(k).y)] for k in range(ch.PointCount())]]
            for h in range(ps.HoleCount(i)):
                hc = ps.Hole(i, h)
                r.append([[mm(hc.CPoint(k).x), mm(hc.CPoint(k).y)] for k in range(hc.PointCount())])
            out.append(r)
        return out

    def shape(item):
        ps = pn.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(ps, lid, 0, pn.FromMM(0.002), pn.ERROR_INSIDE)
        ps.Simplify()
        return rings(ps)

    zones = [z for z in b.Zones() if not z.GetIsRuleArea() and short(z.GetNetname()) == a.net
             and z.IsOnLayer(lid)]
    fill = []
    for z in zones:
        fill += rings(z.GetFilledPolysList(lid))
    items, cand = [], []                          # cand: (kind, label, x, y, rings) for --from
    for t in b.GetTracks():
        if short(t.GetNetname()) != a.net:
            continue
        if t.Type() == pn.PCB_VIA_T:
            if not (t.IsOnLayer(lid) and t.FlashLayer(lid)):
                continue
            s = shape(t)
            p = t.GetPosition()
            cand.append(("via", f"via ({mm(p.x):.3f}, {mm(p.y):.3f})", mm(p.x), mm(p.y), s))
            items += s
        elif t.GetLayer() == lid:
            items += shape(t)
    for f in b.GetFootprints():
        for p in f.Pads():
            if short(p.GetNetname()) == a.net and p.IsOnLayer(lid) and p.FlashLayer(lid):
                s = shape(p)
                q = p.GetPosition()
                cand.append((f"{f.GetReference()}.{p.GetNumber()}", f"{f.GetReference()}.{p.GetNumber()}",
                             mm(q.x), mm(q.y), s))
                items += s
    if not fill and not items:
        sys.exit(f"{a.net} has no copper on {a.layer}")

    srcs = []
    for tok in [t.strip() for t in a.sfrom.split(";") if t.strip()]:
        if "," in tok:
            x, y = (float(v) for v in tok.split(","))
            best = min(cand, key=lambda c: math.hypot(c[2] - x, c[3] - y), default=None)
            if best is None or math.hypot(best[2] - x, best[3] - y) > 0.3:
                sys.exit(f"--from {tok}: no {a.net} via or pad on {a.layer} within 0.3 mm")
            srcs.append({"label": best[1], "rings": best[4]})
        else:
            ref, _, num = tok.partition(".")
            hit = [c for c in cand if c[0] != "via" and c[0].split(".")[0] == ref
                   and (not num or c[0].split(".", 1)[1] == num)]
            if not hit:
                sys.exit(f"--from {tok}: no pad of {a.net} on {a.layer} (an SMD part on another layer: "
                         f"name its feed vias by 'x,y')")
            srcs += [{"label": c[1], "rings": c[4]} for c in hit]

    sink = None
    toks = a.sto.split(",")
    if len(toks) == 4:
        sink = [float(v) for v in toks]
        sink_label = f"box x {sink[0]:g}-{sink[2]:g}, y {sink[1]:g}-{sink[3]:g}"
    else:
        if a.sto not in B.fps:
            sys.exit(f"--to {a.sto}: neither a box x0,y0,x1,y1 nor a part on the board")
        sink = list(B.courtyard(B.fps[a.sto]))
        sink_label = f"{a.sto}'s courtyard x {sink[0]:.2f}-{sink[2]:.2f}, y {sink[1]:.2f}-{sink[3]:.2f}"
    balls_ref = a.balls or (a.sto if len(toks) != 4 else None)
    balls = None
    if balls_ref:
        if balls_ref not in B.fps:
            sys.exit(f"--balls {balls_ref}: not on the board")
        balls = sum(1 for p in B.fps[balls_ref].Pads() if short(p.GetNetname()) == a.net)
    return {"board": str(B.pcb), "net": a.net, "layer": a.layer, "zones": len(zones),
            "fill": fill, "items": items, "sources": srcs, "sink": sink, "sink_label": sink_label,
            "balls_ref": balls_ref, "balls": balls}


# ---------------------------------------------------------------------------
# the flow, in tools/.venv
# ---------------------------------------------------------------------------

def solve(g, raster):
    import numpy as np
    from matplotlib.path import Path as MPath
    from scipy import ndimage
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import breadth_first_order, maximum_flow

    allr = [pt for poly in g["fill"] + g["items"] for ring in poly[:1] for pt in ring]
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

    cu = paint(g["fill"]) | paint(g["items"])
    src = paint([p for s in g["sources"] for p in s["rings"]]) & cu
    bx = g["sink"]
    inbox = ((cx[None, :] >= bx[0]) & (cx[None, :] <= bx[2]) & (cy[:, None] >= bx[1]) & (cy[:, None] <= bx[3]))
    snk = inbox & cu & ~src
    if not src.any():
        return {"error": "the feed items cover no copper cell at this raster"}
    if not snk.any():
        return {"error": "the load region holds none of the net's copper"}

    # nodes: copper cells, then S and T
    idx = -np.ones((ny, nx), np.int64)
    cj, ci = np.nonzero(cu)
    n = len(cj)
    idx[cj, ci] = np.arange(n)
    S, T = n, n + 1
    SCALE = 100000
    wa, wd = math.sqrt(2) - 1, 1 - 1 / math.sqrt(2)
    rows, cols, caps, wts = [], [], [], []
    for dj, di, w in ((0, 1, wa), (1, 0, wa), (1, 1, wd), (1, -1, wd)):
        j2, i2 = cj + dj, ci + di
        ok = (j2 >= 0) & (j2 < ny) & (i2 >= 0) & (i2 < nx)
        a_, b_ = idx[cj[ok], ci[ok]], idx[np.clip(j2[ok], 0, ny - 1), np.clip(i2[ok], 0, nx - 1)]
        keep = b_ >= 0
        a_, b_ = a_[keep], b_[keep]
        c = int(round(w * SCALE))
        rows += [a_, b_]
        cols += [b_, a_]
        caps += [np.full(len(a_), c, np.int64)] * 2
    BIG = SCALE * 100000
    s_nodes = idx[src]
    t_nodes = idx[snk]
    rows += [np.full(len(s_nodes), S), t_nodes]
    cols += [s_nodes, np.full(len(t_nodes), T)]
    caps += [np.full(len(s_nodes), BIG, np.int64), np.full(len(t_nodes), BIG, np.int64)]
    R, C, K = np.concatenate(rows), np.concatenate(cols), np.concatenate(caps)
    G = csr_matrix((K.astype(np.int32 if K.max() < 2 ** 31 else np.int64), (R, C)), shape=(n + 2, n + 2))
    G.sum_duplicates()
    res = maximum_flow(G, S, T, method="dinic")
    width = res.flow_value / SCALE * raster
    if res.flow_value >= BIG:
        return {"error": "a feed item lies inside the load region: no cut separates them"}
    F = res.flow.tocsr()
    Gc = G.tocoo()
    fv = np.asarray(F[Gc.row, Gc.col]).ravel()
    resid = Gc.data.astype(np.int64) - fv
    pos = resid > 0
    Rg = csr_matrix((np.ones(pos.sum(), np.int8), (Gc.row[pos], Gc.col[pos])), shape=G.shape)
    reach = np.zeros(n + 2, bool)
    reach[breadth_first_order(Rg, S, directed=True, return_predecessors=False)] = True
    cut = reach[Gc.row] & ~reach[Gc.col] & (Gc.row < n) & (Gc.col < n)
    cr, cc, cw = Gc.row[cut], Gc.col[cut], Gc.data[cut].astype(np.int64)
    # pieces: the cut edges' midpoints, grouped by touching (8-connected, dilated one cell)
    mid_x = (cx[ci[cr]] + cx[ci[cc]]) / 2
    mid_y = (cy[cj[cr]] + cy[cj[cc]]) / 2
    mark = np.zeros((ny, nx), bool)
    mark[cj[cr], ci[cr]] = True
    mark[cj[cc], ci[cc]] = True
    lab, nlab = ndimage.label(ndimage.binary_dilation(mark, np.ones((3, 3), bool)), np.ones((3, 3), int))
    pl = lab[cj[cr], ci[cr]]
    # the raster's edge loss: where a cut piece meets the copper's edge the graph lacks the one
    # diagonal edge a cell outside the copper would carry, (1 - 1/sqrt2) x raster an END.  A piece
    # that is a closed loop (the ring round a feed via) has no end and takes nothing.
    edge = cu & ~ndimage.binary_erosion(cu, np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool), border_value=0)
    ends_lab, _ = ndimage.label(mark & edge, np.ones((3, 3), int))
    pieces, corr = [], 0.0
    for k in range(1, nlab + 1):
        sel = pl == k
        if not sel.any():
            continue
        ends = len(set(np.unique(ends_lab[(lab == k) & mark & edge])) - {0})
        raw = float(cw[sel].sum()) / SCALE * raster
        c = ends * wd * raster
        corr += c
        pieces.append({"width_mm": round(raw + c, 3), "raw_mm": round(raw, 3), "ends": ends,
                       "x": [round(float(mid_x[sel].min()), 2), round(float(mid_x[sel].max()), 2)],
                       "y": [round(float(mid_y[sel].min()), 2), round(float(mid_y[sel].max()), 2)]})
    pieces.sort(key=lambda p: -p["width_mm"])
    return {"width_mm": round(width + corr, 3), "raw_mm": round(width, 3), "cells": int(n), "grid": [int(nx), int(ny)],
            "source_cells": int(src.sum()), "sink_cells": int(snk.sum()), "pieces": pieces}


def judge(g, a):
    out = {"net": g["net"], "layer": g["layer"], "board": g.get("board"), "raster_mm": a.raster,
           "from": [s["label"] for s in g["sources"]], "to": g["sink_label"]}
    r = solve(g, a.raster)
    out.update(r)
    if "error" in r:
        return out, 2
    amps = a.amps
    if amps is None and a.amps_per_ball is not None:
        if not g.get("balls"):
            out["error"] = "--amps-per-ball needs --balls REF (or --to REF) with pads on the net"
            return out, 2
        amps = a.amps_per_ball * g["balls"]
        out["balls"] = f"{g['balls']} of {g['balls_ref']}'s pads x {a.amps_per_ball:g} A"
    if amps is None:
        return out, 0
    if a.rise is None or a.copper_um is None:
        out["error"] = "--amps needs --rise K and --copper-um UM (the board's layout page gives them)"
        return out, 2
    need = need_width_mm(amps, a.rise, a.copper_um)
    out.update({"amps": round(amps, 4), "need_mm": round(need, 3),
                "carries_amps": round(carries_amps(r["width_mm"], a.rise, a.copper_um), 3),
                "verdict": "PASS" if r["width_mm"] >= need - 1e-9 else "FAIL"})
    return out, 0 if out["verdict"] == "PASS" else 1


def measure(g, raster, amps, rise, copper_um):
    """The flow on geometry g, run in tools/.venv: the result dict (see judge) and its exit code.
    For a caller in KiCad's interpreter (checks.py's `pour neck`)."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="pourneck_")
    os.close(fd)
    try:
        Path(path).write_text(json.dumps(g))
        cmd = [str(VENV_PY), __file__, "--geom", path, "--raster", str(raster), "--amps", str(amps),
               "--rise", str(rise), "--copper-um", str(copper_um), "--json"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return json.loads(r.stdout), r.returncode
        except ValueError:
            return {"error": (r.stderr or r.stdout).strip()[-400:]}, 2
    finally:
        os.unlink(path)


def show(out):
    if "error" in out and "width_mm" not in out:
        print(f"POURNECK ERROR {out['net']} {out['layer']}: {out['error']}")
        return
    print(f"{out['net']} on {out['layer']}: from {', '.join(out['from'])} to {out['to']}  "
          f"(raster {out['raster_mm']:g} mm, {out['cells']} copper cells)")
    for p in out["pieces"]:
        print(f"   cut piece {p['width_mm']:.3f} mm (raw {p['raw_mm']:.3f}, {p['ends']} edge end(s)) at x {p['x'][0]:.2f}-{p['x'][1]:.2f}, "
              f"y {p['y'][0]:.2f}-{p['y'][1]:.2f}")
    if "verdict" in out:
        bl = f" ({out['balls']})" if out.get("balls") else ""
        print(f"POURNECK {out['verdict']} {out['net']} {out['layer']}: least cut {out['width_mm']:.3f} mm "
              f"(raw raster {out['raw_mm']:.3f}; carries {out['carries_amps']:.2f} A), {out['amps']:g} A{bl} needs {out['need_mm']:.3f} mm")
    else:
        print(f"POURNECK {out['net']} {out['layer']}: least cut {out['width_mm']:.3f} mm (no current given)")
    if out.get("error"):
        print(f"POURNECK ERROR: {out['error']}")


# ---------------------------------------------------------------------------
# a hand case
# ---------------------------------------------------------------------------

def selftest():
    """A 10 mm strip 3 mm wide; a 1.0 mm bar of copper bridging it at a 0.8 mm gap
    between two 'antipads'; a 45-degree strip 2 mm wide.  Each least cut is known."""
    class A:
        raster, amps, amps_per_ball, rise, copper_um = 0.025, 2.2, None, 10, 35
    sq = lambda x0, y0, x1, y1: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]
    fails = []

    def case(name, fill, src, box, want, tol):
        g = {"net": "N", "layer": "In4.Cu", "fill": fill, "items": [], "sources": [{"label": "s", "rings": src}],
             "sink": box, "sink_label": "box"}
        for r in (0.05, 0.025):
            A.raster = r
            got = solve(g, r)["width_mm"]
            ok = abs(got - want) <= tol
            print(f"  {name:28s} raster {r:<5g} want {want:.3f}  got {got:.3f}  {'ok' if ok else 'WRONG'}")
            if not ok:
                fails.append(name)
    # straight strip, cut across: 3.0
    case("strip 3 mm", [sq(0, 0, 10, 3)], [sq(0, 0, 0.5, 3)], [9.5, 0, 10, 3], 3.0, 0.06)
    # a 3 mm strip with a hole leaving 0.5 + 0.6 at x = 5: 1.1
    case("strip with a hole, 1.1 mm", [[sq(0, 0, 10, 3)[0], [[4.5, 0.5], [5.5, 0.5], [5.5, 2.4], [4.5, 2.4]]]],
         [sq(0, 0, 0.5, 3)], [9.5, 0, 10, 3], 1.1, 0.06)
    # a 45-degree strip of 2 mm perpendicular width
    d = 2 / math.sqrt(2)
    diag = [[[0, 0], [d, -d], [10 + d, 10 - d], [10, 10]]]
    case("45-degree strip 2 mm", [diag], [sq(-1, -2, 1.2, 1.2)], [9.3, 8.0, 12, 12], 2.0, 0.08)
    a = A()
    a.raster = 0.05
    print(f"  IPC-2221 internal, 10 K, 35 um: 2.2 A needs {need_width_mm(2.2, 10, 35):.3f} mm "
          f"(RV64: 2.32); 0.175 mm carries {carries_amps(0.175, 10, 35):.2f} A (10_layout: 0.34)")
    if abs(need_width_mm(2.2, 10, 35) - 2.32) > 0.01 or abs(carries_amps(0.175, 10, 35) - 0.34) > 0.01:
        fails.append("ipc")
    print("SELFTEST", "FAIL " + ", ".join(fails) if fails else "PASS")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--net")
    ap.add_argument("--layer")
    ap.add_argument("--from", dest="sfrom", default="", help="'x,y;x,y' feed vias/pads, or REF / REF.PIN")
    ap.add_argument("--to", dest="sto", default="", help="x0,y0,x1,y1 (the load box) or REF (its courtyard)")
    ap.add_argument("--balls", default=None, help="REF whose pads on the net count for --amps-per-ball")
    ap.add_argument("--amps", type=float, default=None, help="the current through the cut")
    ap.add_argument("--amps-per-ball", type=float, default=None, help="x the --balls part's pads on the net")
    ap.add_argument("--rise", type=float, default=None, help="K, the temperature rise of the fit")
    ap.add_argument("--copper-um", type=float, default=None, help="the layer's copper thickness, um")
    ap.add_argument("--raster", type=float, default=0.05, help="mm (default 0.05)")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    ap.add_argument("--save-geom", default=None, help="keep the net's geometry here (a --geom input)")
    ap.add_argument("--geom", default=None, help="skip the board: run the flow on a saved geometry")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    try:
        import scipy  # noqa: F401
        have_flow = True
    except ImportError:
        have_flow = False
    if a.geom:
        if not have_flow:
            return subprocess.call([str(VENV_PY), __file__] + sys.argv[1:])
        g = json.loads(Path(a.geom).read_text())
        out, rc = judge(g, a)
        print(json.dumps(out, indent=1) if a.json else "", end="\n" if a.json else "")
        if not a.json:
            show(out)
        return rc
    if not (a.net and a.layer and a.sfrom and a.sto):
        ap.error("--net, --layer, --from and --to are required (or --geom / --selftest)")
    try:
        g = extract(a)
    except Fail as e:
        print(f"POURNECK ERROR {a.net} {a.layer}: {e}")
        return 2
    path = a.save_geom
    tmp = None
    if not path:
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix="pourneck_")
        os.close(fd)
        path = tmp
    Path(path).write_text(json.dumps(g))
    argv = [x for x in sys.argv[1:]]
    cmd = [str(VENV_PY), __file__, "--geom", path, "--raster", str(a.raster)]
    for k, v in (("--amps", a.amps), ("--amps-per-ball", a.amps_per_ball), ("--rise", a.rise),
                 ("--copper-um", a.copper_um)):
        if v is not None:
            cmd += [k, str(v)]
    if a.json:
        cmd.append("--json")
    del argv
    try:
        return subprocess.call(cmd)
    finally:
        if tmp:
            os.unlink(tmp)


if __name__ == "__main__":
    sys.exit(main())
