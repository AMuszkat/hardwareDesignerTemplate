#!/usr/bin/env python3
"""fastside.py -- the guide's "ground beside" rule, measured on the FILL.

    tools/kpy tools/copper/fastside.py [--board B]                       the tally of the board as it is
    tools/kpy tools/copper/fastside.py --pcb BEFORE.kicad_pcb --after AFTER.kicad_pcb --guide <board dir>
                                                                          what AFTER lost against BEFORE; exit 1 if anything

The guide row `ground beside | Fast: GND within 0.6 mm` means: every track of
class Fast has GND copper on each side, on its own layer, within 0.6 mm of the
track's edge.  Judged on the refilled fill (both boards are refilled in memory,
nothing is saved), because a geometry gap test is not enough: a sliver with no
via of its own is removed by the island rule.  Tracks inside a `via field`
part's courtyard are not judged (the ball field has no ground beside).

BEFORE/AFTER: each AFTER segment is compared at the nearest point of the same
net's BEFORE copper within --near; the length that had ground on a side before
and has not after is reported.  Each board needs its .kicad_pro beside it, or
every net reads as Default and no Fast segment exists."""
import argparse
import collections
import math
import sys

import common as C


def clip_out(a, e, box):
    """The parts of segment a-e outside the box (Liang-Barsky)."""
    x0, y0, x1, y1 = box; dx, dy = e[0] - a[0], e[1] - a[1]; t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return [(a, e)]
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
    if t0 >= t1:
        return [(a, e)]
    pt = lambda t: (a[0] + t * dx, a[1] + t * dy)
    out = []
    if t0 > 1e-6:
        out.append((a, pt(t0)))
    if t1 < 1 - 1e-6:
        out.append((pt(t1), e))
    return [s for s in out if math.dist(*s) > 1e-3]


def load(path, pcbnew, G):
    """(fast segments, GND fill per layer) of a board, refilled in memory."""
    import guide
    rule = G["ground_beside"]
    mm = pcbnew.ToMM
    b = pcbnew.LoadBoard(str(path))
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    fields = [(mm(x0), mm(y0), mm(x1), mm(y1)) for x0, y0, x1, y1 in guide.field_boxes(b, G, pcbnew)]
    fast = []
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T or rule["class"] not in (t.GetNetClassName() or "").split(","):
            continue
        a, e = (mm(t.GetStart().x), mm(t.GetStart().y)), (mm(t.GetEnd().x), mm(t.GetEnd().y))
        parts = [(a, e)]
        for f in fields:
            parts = [q for s in parts for q in clip_out(s[0], s[1], f)]
        for p, q in parts:
            fast.append((C.short(t.GetNetname()), b.GetLayerName(t.GetLayer()), p, q, mm(t.GetWidth()) / 2))
    fill = collections.defaultdict(list)
    for z in b.Zones():
        if z.GetIsRuleArea() or C.short(z.GetNetname()) != rule["net"]:
            continue
        for lid in z.GetLayerSet().Seq():
            fill[b.GetLayerName(lid)].append(z.GetFilledPolysList(lid))
    return fast, fill


def sides(fill, L, p, nrm, hw, within, pcbnew):
    out = []
    for s in (+1, -1):
        g = False
        for d in (0.25, 0.3, 0.4, 0.5, 0.6, within):
            if d > within:
                continue
            v = pcbnew.VECTOR2I(pcbnew.FromMM(p[0] + s * nrm[0] * (hw + d)), pcbnew.FromMM(p[1] + s * nrm[1] * (hw + d)))
            if any(f.Contains(v) for f in fill[L]):
                g = True
                break
        out.append(g)
    return out


def tally(fast, fill, within, pcbnew, step=0.05):
    t = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    for n, L, a, e, hw in fast:
        ln = math.dist(a, e)
        if ln < 1e-6:
            continue
        nrm = (-(e[1] - a[1]) / ln, (e[0] - a[0]) / ln); k = max(1, int(ln / step))
        for i in range(k):
            tt = (i + 0.5) / k; p = (a[0] + tt * (e[0] - a[0]), a[1] + tt * (e[1] - a[1]))
            t[n][2 - sum(sides(fill, L, p, nrm, hw, within, pcbnew))] += ln / k
    return t


def lost(fb, gb, fa, ga, within, pcbnew, near=0.5, step=0.05):
    """Rows describing AFTER stretches that lost ground beside; total mm."""
    def ps(p, q, r):
        dx, dy = r[0] - q[0], r[1] - q[1]; L = dx * dx + dy * dy
        u = 0 if L == 0 else max(0, min(1, ((p[0] - q[0]) * dx + (p[1] - q[1]) * dy) / L))
        f = (q[0] + u * dx, q[1] + u * dy)
        return math.hypot(f[0] - p[0], f[1] - p[1]), f
    rows, total = [], 0.0
    for n, L, a, e, hw in fa:
        ln = math.dist(a, e)
        if ln < 1e-6:
            continue
        nrm = (-(e[1] - a[1]) / ln, (e[0] - a[0]) / ln); k = max(1, int(ln / step))
        mine = [(a2, e2, hw2) for n2, L2, a2, e2, hw2 in fb if n2 == n and L2 == L]
        lo = {+1: 0.0, -1: 0.0}
        for i in range(k):
            tt = (i + 0.5) / k; p = (a[0] + tt * (e[0] - a[0]), a[1] + tt * (e[1] - a[1]))
            now = sides(ga, L, p, nrm, hw, within, pcbnew)
            if all(now):
                continue
            best = min(((ps(p, a2, e2), hw2) for a2, e2, hw2 in mine), default=None, key=lambda q: q[0][0])
            if not best or best[0][0] >= near:
                continue
            was = sides(gb, L, best[0][1], nrm, best[1], within, pcbnew)
            for j, s in enumerate((+1, -1)):
                if was[j] and not now[j]:
                    lo[s] += ln / k
        if lo[1] + lo[-1] > step / 2:
            total += lo[1] + lo[-1]
            rows.append(f"{n:8s} {L:6s} ({a[0]:.3f},{a[1]:.3f})->({e[0]:.3f},{e[1]:.3f}) len {ln:.2f}: lost ground "
                        + ", ".join(f"side{'+' if s > 0 else '-'} {v:.2f} mm" for s, v in lo.items() if v > step / 2))
    return rows, total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_board_args(ap)
    ap.add_argument("--after", help="a second board (the trial): report what it lost against the first")
    ap.add_argument("--near", type=float, default=0.5)
    a = C.args(ap)
    C.load(a)
    pcbnew = C.pcbnew_module()
    rule = C.G.get("ground_beside")
    if not rule:
        print("the guide has no 'ground beside' row: nothing to judge")
        return 0
    fb, gb = load(a.pcb, pcbnew, C.G)
    if not fb:
        C.die(f"no {rule['class']}-class track on {a.pcb.name}: is its .kicad_pro beside it? (exit 2)")
    tb = tally(fb, gb, rule["within"], pcbnew)
    if not a.after:
        print(f"{rule['class']} tracks with {rule['net']} on 2 / 1 / 0 sides within {rule['within']} mm, mm per net:")
        for n in sorted(tb):
            print(f"  {n:12s} {tb[n][0]:7.1f} {tb[n][1]:7.1f} {tb[n][2]:7.1f}")
        return 0
    fa, ga = load(str(C.in_board(a, a.after)), pcbnew, C.G)
    if not fa:
        C.die(f"no {rule['class']}-class track on {a.after}: is its .kicad_pro beside it?")
    ta = tally(fa, ga, rule["within"], pcbnew)
    rows, total = lost(fb, gb, fa, ga, rule["within"], pcbnew, a.near)
    print("\n".join(rows))
    print(f"per net, mm with {rule['net']} on 2 / 1 / 0 sides:  before  ->  after")
    for n in sorted(set(tb) | set(ta)):
        print(f"  {n:9s} {tb[n][0]:6.1f} {tb[n][1]:6.1f} {tb[n][2]:6.1f}   ->  {ta[n][0]:6.1f} {ta[n][1]:6.1f} {ta[n][2]:6.1f}")
    print(f"{len(rows)} segment(s) lost ground beside over {total:.2f} mm in all")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
