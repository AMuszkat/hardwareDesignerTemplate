"""How many MORE tracks fit across a line -- the free lanes of a strait, before a plan counts on them.

    tools/kpy tools/route/lanecount.py BOARD.kicad_pcb --layer In5.Cu --x 17.0,22.7 --y 14.6,15.0,15.4,15.8,16.0,16.2,16.6,17.0,17.5,18.0

For each y: the x intervals where a 0.1016 mm track's CENTRE may lie on that layer (0.1016 mm from every other net's track
and via pad, 0.1778 mm from a bare hole), and how many tracks at 0.2032 mm pitch they take.  The least over the y's of a
run is what the run can carry -- BUT READ WHERE THE RUNS ARE: "8 more fit" was a corridor behind three other tracks and two
dead-end slots beside a via; the count is room only where a net can get to it and out again.
That call sees TRACKS AND VIAS ONLY, at one width and one clearance (inner layers of a 4 mil field).

    tools/kpy tools/route/lanecount.py BOARD.kicad_pcb --cut-x 39.0,39.5,43.25 --y 13.2,29.8 --layer F.Cu,In2.Cu,B.Cu \
        --width 0.128 --clearance 0.2032 [--pitch P] [--own NET,NET] [--through 2] [--span 38.3,43.3] [--area-rule]

The other direction, with all the copper: tracks crossing a VERTICAL cut at each x, between the two y.  Every copper item
of the layer is read as pcbnew draws it -- tracks, arcs, via pads (a bare hole where the pad is not flashed), pads and
their holes, copper shapes, FILLED POURS, no-track rule areas -- and grown by the stricter of --clearance and its own
clearance (KiCad holds the stricter of two), plus half the --width.  The outlines are approximated from INSIDE, so the
count is an UPPER bound: per free run of the centre, floor(length / pitch) + 1 tracks (pitch: --width + --clearance).
--area-rule: an area rule overrides the classes at the cut (a BGA field): every item at --clearance alone.
--own: the nets being counted.  Their own copper is no obstacle to them, except a track that already CROSSES the cut
(it holds its lane; the line says how many do, and the demand at that cut is less by them).
--through N (default 2): a run counts as THROUGH only if it carries on east and west for N pitches (stepping a
quarter pitch, drifting at most 45 degrees; inside --span when given); "thru" is what is left of the run at its
narrowest on the way, 0 = a dead end behind a via row or a pad.  A through count is a LEAD (a line may end the run in a
via), the plain count is the bound.  Every run names the two items that end it.
The functions (`copper`, `cut`, `through`) are importable: a count of several layers, widths or via sites builds on them.
"""
import math, os, sys
import pcbnew

mm = pcbnew.ToMM
sh = lambda n: n.rsplit("/", 1)[-1]
ERR = pcbnew.FromMM(0.0005)


def _rings(ps):
    """A SHAPE_POLY_SET as [[ring, ring...], ...] in mm: per polygon its outline and then its holes."""
    out = []
    for i in range(ps.OutlineCount()):
        chains = [ps.Outline(i)] + [ps.Hole(i, h) for h in range(ps.HoleCount(i))]
        out.append([[(mm(c.CPoint(k).x), mm(c.CPoint(k).y)) for k in range(c.PointCount())] for c in chains])
    return out


def _circle(x, y, r, n=96):
    """A disc as an INSCRIBED polygon (the count stays an upper bound)."""
    return [[[(x + r * math.cos(2 * math.pi * k / n), y + r * math.sin(2 * math.pi * k / n)) for k in range(n)]]]


def _ob(label, net, kind, polys, span=None):
    xs = [p[0] for pg in polys for r in pg for p in r]; ys = [p[1] for pg in polys for r in pg for p in r]
    if not xs:
        return None
    return dict(label=label, net=net, kind=kind, polys=polys, span=span, bb=(min(xs), min(ys), max(xs), max(ys)))


def copper(b, layer, half, clr, box=None, zones=True, own_clr=True):
    """Every copper item on `layer` as an obstacle to the CENTRE of a track of half-width `half` (or of a via of that
    radius) held `clr` off other nets: [{label, net, kind, polys, span, bb}].  Each item is grown by
    max(clr, its own clearance) + half; a bare hole by Board Setup's copper-to-hole + half.  `box` (x0, y0, x1, y1)
    drops what lies outside it (take a margin of 1 mm).  `span` is a track's own x extent, for cut()'s `own`.
    own_clr=False: an area rule overrides the classes there (a BGA field) -- every item at `clr` alone."""
    lid = b.GetLayerID(layer); ds = b.GetDesignSettings(); holeclr = mm(ds.m_HoleClearance); out = []

    def inbox(bb, m):
        return box is None or not (mm(bb.GetRight()) + m < box[0] or mm(bb.GetLeft()) - m > box[2]
                                   or mm(bb.GetBottom()) + m < box[1] or mm(bb.GetTop()) - m > box[3])

    def grow(item):
        try:
            return (max(clr, mm(item.GetOwnClearance(lid))) if own_clr else clr) + half
        except Exception:
            return clr + half

    def poly(item, g):
        ps = pcbnew.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(ps, lid, pcbnew.FromMM(g), ERR, pcbnew.ERROR_INSIDE)
        return _rings(ps)
    for t in b.GetTracks():
        g = grow(t)
        if not inbox(t.GetBoundingBox(), g):
            continue
        net = sh(t.GetNetname())
        if t.Type() == pcbnew.PCB_VIA_T:
            x, y = mm(t.GetPosition().x), mm(t.GetPosition().y)
            if not t.IsOnLayer(lid):
                continue
            if t.FlashLayer(lid):
                out.append(_ob(f"via {net} ({x:.3f}, {y:.3f})", net, "via", _circle(x, y, mm(t.GetWidth(lid)) / 2 + g), (x, x)))
            else:
                out.append(_ob(f"hole of via {net} ({x:.3f}, {y:.3f})", net, "hole",
                               _circle(x, y, mm(t.GetDrillValue()) / 2 + holeclr + half), (x, x)))
        elif t.GetLayer() == lid:
            a, e = t.GetStart(), t.GetEnd()
            out.append(_ob(f"track {net} ({mm(a.x):.3f}, {mm(a.y):.3f})-({mm(e.x):.3f}, {mm(e.y):.3f})", net, "track",
                           poly(t, g), (min(mm(a.x), mm(e.x)), max(mm(a.x), mm(e.x)))))
    for f in b.GetFootprints():
        for p in f.Pads():
            if not inbox(p.GetBoundingBox(), 1.0):
                continue
            net = sh(p.GetNetname()); nm = f"pad {f.GetReference()}.{p.GetNumber()} {net}"
            x, y = mm(p.GetPosition().x), mm(p.GetPosition().y)
            if p.IsOnLayer(lid) and (p.GetDrillSizeX() == 0 or p.FlashLayer(lid)):
                out.append(_ob(nm, net, "pad", poly(p, grow(p))))
            if p.GetDrillSizeX() > 0:      # the hole, on every layer (a slot is taken at its long radius: not a bound there)
                out.append(_ob("hole of " + nm, net, "hole",
                               _circle(x, y, max(mm(p.GetDrillSizeX()), mm(p.GetDrillSizeY())) / 2 + holeclr + half)))
    for d in b.GetDrawings():
        try:
            if d.Type() != pcbnew.PCB_SHAPE_T or d.GetLayer() != lid or not inbox(d.GetBoundingBox(), 1.0):
                continue
        except Exception:
            continue
        out.append(_ob(f"copper shape {sh(d.GetNetname())}", sh(d.GetNetname()), "shape", poly(d, grow(d))))
    for z in (b.Zones() if zones else []):
        if not z.IsOnLayer(lid) or not inbox(z.GetBoundingBox(), 1.0):
            continue
        if z.GetIsRuleArea():
            if not z.GetDoNotAllowTracks():
                continue
            ps = pcbnew.SHAPE_POLY_SET(z.Outline()); g = half; nm = f"no-track rule area {z.GetZoneName()}"
        else:
            ps = pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(lid)); g = grow(z); nm = f"pour {sh(z.GetNetname())}"
        if box is not None:                  # a board-wide plane: cut it to the box before it is grown
            bx = pcbnew.SHAPE_POLY_SET(); bx.NewOutline()
            for qx, qy in ((box[0] - 1, box[1] - 1), (box[2] + 1, box[1] - 1), (box[2] + 1, box[3] + 1), (box[0] - 1, box[3] + 1)):
                bx.Append(pcbnew.FromMM(qx), pcbnew.FromMM(qy))
            ps.BooleanIntersection(bx)
        if ps.OutlineCount():
            ps.Inflate(pcbnew.FromMM(g), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, ERR)
            out.append(_ob(nm, sh(z.GetNetname()), "pour", _rings(ps)))
    return [o for o in out if o]


def cut(obs, x, y0, y1, own=()):
    """The free runs of a track's centre on the vertical line at x between y0 and y1: [(ya, yb, what ends it at ya,
    what ends it at yb)], and the own nets whose track already crosses there.  Exact: the line against every outline."""
    iv = []; crossing = set()
    for o in obs:
        L, T, R, B = o["bb"]
        if L > x or R < x or B < y0 or T > y1:
            continue
        if o["net"] in own:
            if o["kind"] != "track" or not (o["span"][0] - 1e-6 <= x <= o["span"][1] + 1e-6):
                continue
            crossing.add(o["net"])
        for pg in o["polys"]:
            ys = []
            for r in pg:
                for (x1, yy1), (x2, yy2) in zip(r, r[1:] + r[:1]):
                    if (x1 <= x) != (x2 <= x):
                        ys.append(yy1 + (x - x1) * (yy2 - yy1) / (x2 - x1))
            ys.sort()
            iv += [(a, e, o["label"]) for a, e in zip(ys[::2], ys[1::2])]
    iv.sort(); runs = []; at = y0; was = "the cut's end"
    for a, e, lab in iv:
        if a > at and at < y1:
            runs.append((at, min(a, y1), was, lab if a < y1 else "the cut's end"))
        if e > at:
            at, was = e, lab
    if at < y1:
        runs.append((at, y1, was, "the cut's end"))
    return runs, crossing


def slots(length, pitch):
    return int(length / pitch + 1e-9) + 1


def through(obs, x, run, pitch, n=2, own=(), span=None, y0=-1e9, y1=1e9):
    """What is left of a free run `n` pitches east and west of its cut: (slots at the narrowest, the x there).  The run
    is carried a quarter pitch a step, drifting at most 45 degrees, through the free runs of each line it meets."""
    least, where = slots(run[1] - run[0], pitch), x
    for sgn in (-1, 1):
        reach = [(run[0], run[1])]; dx = pitch / 4
        for k in range(1, 4 * n + 1):
            xx = x + sgn * k * dx
            if span and not (span[0] <= xx <= span[1]):
                break
            free, _ = cut(obs, xx, y0, y1, own)
            nxt = []
            for a, e, *_ in free:
                for lo, hi in reach:
                    lo2, hi2 = max(a, lo - dx), min(e, hi + dx)
                    if lo2 <= hi2:
                        nxt.append((lo2, hi2))
            nxt.sort(); reach = []
            for lo, hi in nxt:
                if reach and lo <= reach[-1][1]:
                    reach[-1] = (reach[-1][0], max(reach[-1][1], hi))
                else:
                    reach.append((lo, hi))
            s = sum(slots(hi - lo, pitch) for lo, hi in reach)
            if s < least:
                least, where = s, xx
            if not reach:
                return 0, xx
    return least, where


def cut_main(A, pcb):
    opt = lambda k, d=None: A[A.index(k) + 1] if k in A else d
    b = pcbnew.LoadBoard(pcb)
    xs = [float(v) for v in opt("--cut-x").split(",")]; y0, y1 = [float(v) for v in opt("--y").split(",")][:2]
    W, C = float(opt("--width", 0.1016)), float(opt("--clearance", 0.1016)); P = float(opt("--pitch", W + C))
    own = set(filter(None, opt("--own", "").split(","))); n = int(opt("--through", 2))
    span = [float(v) for v in opt("--span").split(",")] if opt("--span") else None
    box = (min(xs) - 1.0 - n * P, y0 - 1.0, max(xs) + 1.0 + n * P, y1 + 1.0)
    for layer in opt("--layer", "In5.Cu").split(","):
        obs = copper(b, layer, W / 2, C, box, own_clr="--area-rule" not in A); worst = None
        for x in xs:
            runs, crossing = cut(obs, x, y0, y1, own); tot = thr = 0; txt = []
            for r in runs:
                s = slots(r[1] - r[0], P); t, wx = through(obs, x, r, P, n, own, span, y0, y1); tot += s; thr += t
                txt.append(f"{r[0]:.3f}-{r[1]:.3f} {s}" + (f" (thru {t} at x {wx:.2f})" if t < s else "") + f" [{r[2]} | {r[3]}]")
            worst = tot if worst is None else min(worst, tot)
            print(f"{layer} x {x:6.3f}: {tot:2d} more track(s) fit, {thr} through" +
                  (f"; {len(crossing)} of the own nets cross here already" if own else "") + "\n    " + "\n    ".join(txt))
        print(f"{layer} y {y0}-{y1}, width {W} clearance {C} pitch {P:.4f}: the least over these cuts is {worst} more track(s)")


def legacy(A):
    opt = lambda k, d=None: A[A.index(k) + 1] if k in A else d
    pcb = next((a for a in A if a.endswith(".kicad_pcb")), None)
    if not pcb:
        sys.exit("lanecount.py: name the .kicad_pcb file")
    b = pcbnew.LoadBoard(pcb); b.BuildConnectivity(); mm = pcbnew.ToMM; sh = lambda n: n.rsplit("/", 1)[-1]
    layer = opt("--layer", "In5.Cu"); lid = b.GetLayerID(layer)
    X0, X1 = [float(v) for v in opt("--x").split(",")]; YS = [float(v) for v in opt("--y").split(",")]
    W, C, H = 0.1016, 0.1016, 0.1778
    tracks = []; vias = []
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            vias.append((mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetWidth(lid)) / 2 if t.FlashLayer(lid) else None, mm(t.GetDrillValue()) / 2, sh(t.GetNetname())))
        elif t.GetLayer() == lid:
            tracks.append(((mm(t.GetStart().x), mm(t.GetStart().y)), (mm(t.GetEnd().x), mm(t.GetEnd().y)), mm(t.GetWidth()) / 2, sh(t.GetNetname())))


    def dseg(px, py, a, e):
        dx, dy = e[0] - a[0], e[1] - a[1]; L2 = dx * dx + dy * dy
        t = 0 if L2 == 0 else max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
        return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)


    worst = None
    for y in YS:
        free = []; run = None; here = set(); n = int(round((X1 - X0) / 0.005))
        for i in range(n + 1):
            x = X0 + i * 0.005; ok = True
            for a, e, hw, net in tracks:
                if abs(y - (a[1] + e[1]) / 2) < abs(a[1] - e[1]) / 2 + 0.5 and dseg(x, y, a, e) < hw + C + W / 2:
                    ok = False; here.add(net); break
            if ok:
                for vx, vy, pr, dr, net in vias:
                    if abs(vx - x) < 0.6 and abs(vy - y) < 0.6 and math.hypot(vx - x, vy - y) < ((pr + C) if pr else (dr + H)) + W / 2:
                        ok = False; break
            if ok and run is None:
                run = x
            if not ok and run is not None:
                free.append((run, x - 0.005)); run = None
        if run is not None:
            free.append((run, X1))
        lanes = sum(int((b_ - a_) / 0.2032 + 1e-9) + 1 for a_, b_ in free)
        worst = lanes if worst is None else min(worst, lanes)
        print(f"y {y:6.2f}: {lanes:2d} more track(s) fit  | free centre runs: " + " ".join(f"{a_:.2f}-{b_:.2f}" for a_, b_ in free if b_ - a_ >= 0) + f"  | there now: {len(here)} nets")
    print(f"{layer} x {X0}-{X1}: the least over these lines is {worst} more track(s)")


if __name__ == "__main__":
    A = sys.argv[1:]
    if "--cut-x" in A:
        pcb = next((a for a in A if a.endswith(".kicad_pcb")), None)
        if not pcb or "--y" not in A:
            sys.exit("lanecount.py --cut-x X,..: name the .kicad_pcb file and --y Y0,Y1")
        cut_main(A, pcb)
    else:
        legacy(A)
