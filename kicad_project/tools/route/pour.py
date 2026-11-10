#!/usr/bin/env python3
"""What does a pour actually do?  Measure it -- and try another outline without saving.

    tools/kpy tools/route/pour.py --board board1
    tools/kpy tools/route/pour.py --board board1 \
        --try +3V3:In2.Cu=routing/R2_zone.json --over 'SIG_*:B.Cu' [--field U1]

A zone's outline is a wish; its FILL is the copper.  DRC passes a pour that has
fallen into islands, a pour whose one via sits in a pocket of its own, and a
power pour laid over a bus that the layout rules say must see ground.  So for
every filled zone this prints, from the fill and not from the outline:

  islands       how many, and their areas; "untied": the ones beside the largest that hold
                no via or through-hole pin of the net (a GND pour split by tracks is fine
                when every piece is stitched; a piece holding nothing is not)
  vias / PTH    same-net vias and through-hole pads: inside which island, or
                OUTSIDE the fill (a via the pour was drawn for and does not reach)
  SMD pads      same-net surface pads that stand over the fill -- each one is a
                via drop away from the rail, which is what a plane is for
  --over        length of the named nets' tracks on another layer that lie UNDER
                the fill, split by --field's courtyard when one is named (inside
                a BGA's own field the overlap is unavoidable; outside it is a
                choice)

`--try NET:LAYER=file.json` replaces that zone's outline with the polygon in the
file (a route.py zone list: an entry for NET/LAYER with "poly"), refills IN
MEMORY and measures.  Nothing is ever written: the board is changed with
`route.py rip` + `apply`, which gate it.

Two things worth knowing before reading the numbers: thermal spokes on a
header inside a plane leave peninsulas (copper between neighbouring antipads
that hangs on the pin alone), and a through-hole pin behind a relief gap is not
found by looking just outside its land -- this looks all the way round at both
radii.
"""

import argparse
import collections
import fnmatch
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "place"))
import place as P  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--try", dest="tries", action="append", default=[],
                    help="NET:LAYER=zonefile.json -- measure with this outline instead (in memory)")
    ap.add_argument("--over", action="append", default=[],
                    help="NETGLOB:LAYER -- report how much of these tracks lies under each fill")
    ap.add_argument("--field", default=None, help="footprint whose courtyard splits the --over report")
    ap.add_argument("--step", type=float, default=0.05, help="sampling step along tracks, mm")
    ap.add_argument("--section", type=float, default=0,
                    help="mm: the floor for every cross-section of the pour on the load path --from -> --to: "
                         "copper summed along each cut across the pour's long axis")
    ap.add_argument("--from", dest="sfrom", default="", help="REF.PIN,... (or REF: all its pins on the net): the sources")
    ap.add_argument("--section-net", default="", help="measure --section on this net's pours only (the pins "
                    "named may carry other nets too -- a connector's GND pins -- and a board-wide GND pour is slow)")
    ap.add_argument("--to", dest="sto", default="", help="REF.PIN,...: the loads")
    a = ap.parse_args()
    a.board = P.board_dir_arg(a.board)
    return report(P.Board(a.board), a)


def report(B, a):
    """The pour report for a board already loaded -- `route.py audit` calls it in-process
    instead of loading pcbnew and the board again.  `a` carries main's options (tries, over,
    field, step, board).  Refills B's zones in memory; never saves."""
    pn, b = B.pn, B.b
    mm = B.mm
    short = lambda n: n.split("/")[-1]

    def layer_name(z):
        """Canonical (F.Cu, In1.Cu, ...): a board may rename its layers."""
        return pn.BOARD.GetStandardLayerName(z.GetLayerSet().Seq()[0])

    changed = False
    for t in a.tries:
        key, path = t.split("=", 1)
        net, lay = key.split(":")
        p = Path(path)
        if not p.exists():
            p = Path(a.board) / path
        zf = json.loads(p.read_text())
        zf = zf.get("zones", []) if isinstance(zf, dict) else zf     # a plan file, or a bare list
        hit = [e for e in zf if e["net"] == net and e["layer"] == lay]
        if not hit:
            sys.exit(f"--try {key}: {p.name} holds no zone for {net} on {lay}")
        entry = hit[0]
        zs = [z for z in b.Zones() if short(z.GetNetname()) == net and layer_name(z) == lay]
        if not zs:
            sys.exit(f"no {net} zone on {lay} to replace")
        z = zs[0]
        o = z.Outline()
        o.RemoveAllContours()
        o.NewOutline()
        for x, y in entry["poly"]:
            o.Append(pn.FromMM(x), pn.FromMM(y))
        z.SetIsFilled(False)
        changed = True
        print(f"TRY {net} on {lay}: {len(entry['poly'])} corners from {p.name} (in memory only)")
    # ALWAYS refill: the fill saved in the file is whatever KiCad last computed, and
    # a scripted write can leave it stale (the file saying three islands where a
    # refill says one).  A measurement of a stale fill is a measurement of nothing.
    with P.quiet_stderr():
        pn.ZONE_FILLER(b).Fill(b.Zones())

    if a.field and a.field not in B.fps:
        sys.exit(f"--field {a.field}: not on the board")
    fc = B.courtyard(B.fps[a.field]) if a.field else None
    in_field = lambda x, y: bool(fc) and fc[0] <= x <= fc[2] and fc[1] <= y <= fc[3]

    # one report per zone PER COPPER LAYER it fills; a rule area (keep-out) is not a pour
    targets = [(z, lid) for z in b.Zones() if not z.GetIsRuleArea()
               for lid in z.GetLayerSet().CuStack()]
    if not targets:
        print("no pours on this board")
    for z, lid in targets:
        net, lay = short(z.GetNetname()), pn.BOARD.GetStandardLayerName(lid)
        fill = z.GetFilledPolysList(lid)
        n = fill.OutlineCount()
        isl = []
        for i in range(n):
            s = pn.SHAPE_POLY_SET()
            s.AddOutline(fill.Outline(i))
            for h in range(fill.HoleCount(i)):
                s.AddHole(fill.Hole(i, h))
            isl.append(s)
        areas = [s.Area() / 1e12 for s in isl]
        order = sorted(range(n), key=lambda i: -areas[i])
        print(f"\n{net} on {lay}: {n} island(s), {sum(areas):.1f} mm2  "
              + " ".join(f"[{k}] {areas[i]:.1f}" for k, i in enumerate(order[:6])))

        def island(x, y):
            v = pn.VECTOR2I(pn.FromMM(x), pn.FromMM(y))
            for k, i in enumerate(order):
                if isl[i].Contains(v):
                    return k
            return None

        # same-net vias and through-hole pads
        per, outside = {}, []
        for t in b.Tracks():
            if t.Type() == pn.PCB_VIA_T and short(t.GetNetname()) == net:
                x, y = mm(t.GetPosition().x), mm(t.GetPosition().y)
                # a via is IN the fill when the copper reaches its annulus: test just outside the hole
                r = mm(t.GetWidth(lid)) / 2 - 0.02
                k = next((island(x + dx, y + dy) for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r), (0, 0))
                          if island(x + dx, y + dy) is not None), None)
                (per.setdefault(k, []) if k is not None else outside).append(("via", x, y))
        smd_over = []
        for f in b.Footprints():
            for p in f.Pads():
                if short(p.GetNetname()) != net:
                    continue
                x, y = mm(p.GetPosition().x), mm(p.GetPosition().y)
                if p.GetAttribute() == pn.PAD_ATTRIB_PTH:
                    # a through-hole pin is ON the fill when copper stands just outside its land
                    # (solid) or just outside its relief gap (thermal): look all the way round, at both
                    bb = p.GetBoundingBox()
                    half = max(mm(bb.GetWidth()), mm(bb.GetHeight())) / 2
                    hit = set()
                    for r in (half + 0.05, half + mm(z.GetThermalReliefGap()) + 0.1):
                        for i in range(16):
                            k = island(x + r * math.cos(i * math.pi / 8), y + r * math.sin(i * math.pi / 8))
                            if k is not None:
                                hit.add(k)
                    for k in hit:
                        per.setdefault(k, []).append((f"{f.GetReference()}.{p.GetNumber()} PTH", x, y))
                elif island(x, y) is not None or any(
                        island(x + dx, y + dy) is not None for dx, dy in ((0.6, 0), (-0.6, 0), (0, 0.6), (0, -0.6))):
                    smd_over.append(f"{f.GetReference()}.{p.GetNumber()}")
        for k in sorted(per):
            items = per[k]
            pth = [i[0] for i in items if i[0] != "via"]
            print(f"   island [{k}]: {sum(1 for i in items if i[0] == 'via')} via(s)"
                  + (f", through-hole pins {' '.join(q.replace(' PTH', '') for q in pth)}" if pth else ""))
        # an island is TIED when it holds a via or a through-hole pin of the net (it reaches the
        # net's other layers); the largest is the pour itself.  A GND pour split by tracks is fine
        # with every piece stitched; a piece holding nothing is dead copper or a cut-off feed
        untied = [k for k in range(1, n) if k not in per]
        def _where(k):
            bb_ = isl[order[k]].BBox()
            return f"[{k}] {areas[order[k]]:.1f} mm2 at ({mm(bb_.Centre().x):.1f}, {mm(bb_.Centre().y):.1f})"
        print(f"   untied: {len(untied)} island(s) beside the largest hold no {net} via or through-hole pin"
              + (": " + ", ".join(_where(k) for k in untied[:8]) if untied else ""))
        if outside:
            print(f"   ⚠ {len(outside)} {net} via(s) NOT reached by the fill: "
                  + " ".join(f"({x:.2f},{y:.2f})" for _, x, y in outside[:12]))
        print(f"   {len(smd_over)} {net} SMD pad(s) stand over the fill"
              + (": " + " ".join(sorted(smd_over)) if smd_over and len(smd_over) <= 60 else ""))

        if getattr(a, "section", 0) and (not getattr(a, "section_net", "") or net == a.section_net):
            section_report(pn, b, z, lid, net, isl, a, short)

        for spec in a.over:
            glob, olay = spec.split(":")
            inside = outsidef = 0.0
            worst = []
            for t in b.Tracks():
                if t.Type() != pn.PCB_TRACE_T or pn.BOARD.GetStandardLayerName(t.GetLayer()) != olay:
                    continue
                if not fnmatch.fnmatch(short(t.GetNetname()), glob):
                    continue
                x1, y1, x2, y2 = mm(t.GetStart().x), mm(t.GetStart().y), mm(t.GetEnd().x), mm(t.GetEnd().y)
                L = math.hypot(x2 - x1, y2 - y1)
                k = max(1, int(L / a.step))
                under_out = 0.0
                for i in range(k):
                    u = (i + 0.5) / k
                    x, y = x1 + (x2 - x1) * u, y1 + (y2 - y1) * u
                    if island(x, y) is None:
                        continue
                    if in_field(x, y):
                        inside += L / k
                    else:
                        outsidef += L / k
                        under_out += L / k
                if under_out > 0.05:
                    worst.append((under_out, short(t.GetNetname()), (x1, y1), (x2, y2)))
            if a.field:
                print(f"   under this fill, {glob} on {olay}: {outsidef:.2f} mm OUTSIDE {a.field}'s courtyard, "
                      f"{inside:.2f} mm inside it")
            else:
                print(f"   under this fill, {glob} on {olay}: {outsidef:.2f} mm")
            for w in sorted(worst, reverse=True)[:6]:
                print(f"        {w[0]:.2f} mm  {w[1]}  {w[2]} - {w[3]}")


def section_report(pn, b, z, lid, net, isl, a, short):
    """Every cross-section of the pour on the load path >= a.section mm: cut the fill across
    its long axis every 0.05 mm between the sources (--from) and the loads (--to) and sum the
    copper on each cut -- the fill's islands and the net's own lands on this layer.  A via's
    antipad, another net's hop or a pin row each take their width out of the cut (board2
    D67(b): signal hops across the +5V strip between two connectors, 1.0 mm floor).  Prints
    the narrowest cut and where it is."""
    def pins(spec):
        out = []
        for tok in [t for t in spec.split(",") if t]:
            ref, _, num = tok.partition(".")
            f = b.FindFootprintByReference(ref)
            if f is None:
                sys.exit(f"--section: no footprint {ref}")
            out += [p for p in f.Pads() if short(p.GetNetname()) == net and (not num or p.GetNumber() == num)]
        return out
    src, dst = pins(a.sfrom), pins(a.sto)
    if not src or not dst:
        print(f"   section: --from / --to name no {net} pin -- skipped")
        return None
    fill = pn.SHAPE_POLY_SET()
    for s_ in isl:
        fill.Append(s_)
    for p in src + dst:                                  # the lands themselves carry the current
        if p.IsOnLayer(lid):
            p.TransformShapeToPolygon(fill, lid, 0, pn.FromMM(0.005), pn.ERROR_INSIDE)
    fill.Simplify()
    bb = fill.BBox()
    horiz = bb.GetWidth() >= bb.GetHeight()          # cut across the long axis
    c = (lambda p: p.GetPosition().x) if horiz else (lambda p: p.GetPosition().y)
    sa, da = [pn.ToMM(c(p)) for p in src], [pn.ToMM(c(p)) for p in dst]
    lo, hi = (max(da), min(sa)) if max(da) < min(sa) else (max(sa), min(da))
    if lo >= hi:
        lo, hi = min(sa + da), max(sa + da)
    t0, t1 = (pn.ToMM(bb.GetY()), pn.ToMM(bb.GetBottom())) if horiz else (pn.ToMM(bb.GetX()), pn.ToMM(bb.GetRight()))
    worst = None
    k, step = 0, 0.05
    while lo + k * step <= hi + 1e-9:
        u = lo + k * step
        k += 1
        tot, run, t = 0.0, 0.0, t0
        while t <= t1 + 1e-9:                          # 10 um samples along the cut
            v = pn.VECTOR2I(pn.FromMM(u), pn.FromMM(t)) if horiz else pn.VECTOR2I(pn.FromMM(t), pn.FromMM(u))
            if fill.Contains(v):
                tot += 0.01
            t += 0.01
        if worst is None or tot < worst[0]:
            worst = (tot, u)
    ok = worst[0] >= a.section - 1e-6
    ax = "x" if horiz else "y"
    print(f"   section >= {a.section:g} mm on the load path ({ax} {lo:.2f} .. {hi:.2f}): "
          f"{'PASS' if ok else 'FAIL'} -- narrowest {worst[0]:.2f} mm of copper at {ax} = {worst[1]:.2f}")
    return ok

if __name__ == "__main__":
    main()
