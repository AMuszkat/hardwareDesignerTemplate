#!/usr/bin/env python3
"""What does a pour actually do?  Measure it -- and try another outline without saving.

    tools/kpy tools/route/pour.py --board board1
    tools/kpy tools/route/pour.py --board board1 \
        --try +3V3:In2.Cu=routing/R2_zone.json --over 'SIG_*:B.Cu' [--field U1]

A zone's outline is a wish; its FILL is the copper.  DRC passes a pour that has
fallen into islands, a pour whose one via sits in a pocket of its own, and a
power pour laid over a bus that the layout rules say must see ground.  So for
every net on every copper layer -- its zones there are ONE copper set, as KiCad's
connectivity joins them -- this prints, from the fill and not from the outline:

  islands       how many, and their areas; "untied": the ones beside the largest that hold
                no via or through-hole pin of the net (a GND pour split by tracks is fine
                when every piece is stitched; a piece holding nothing is not)
  vias / PTH    same-net vias and through-hole pads: inside which island, or
                OUTSIDE the fill (a via the pour was drawn for and does not reach)
  SMD pads      same-net surface pads that stand over the fill -- each one is a
                via drop away from the rail, which is what a plane is for
  POURNET       last: every island holding a pad or via joined to the rest of its net by
                KiCad's connectivity, else each one cut off -- its area, what stands on it,
                what its cluster reaches (route.py audit's `pournet`, check's RULE pournet)
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
    report(P.Board(a.board), a)
    return 0


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

    # one report per NET PER COPPER LAYER: every zone of that net filling that layer is one
    # copper set.  KiCad knocks a lower-priority zone out of a higher-priority one of the same
    # net and its connectivity joins the two along the shared edge; judged zone by zone, the
    # lower one's sliver beside the edge read as an untied island and the higher one's vias as
    # "not reached" (board1 R6a_real: a VDD_SOC prio-2 piece of 0.21 mm2 against the prio-5
    # corridor).  A rule area (keep-out) is not a pour.
    groups = collections.OrderedDict()
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        for lid in z.GetLayerSet().CuStack():
            groups.setdefault((short(z.GetNetname()), lid), []).append(z)
    if not groups:
        print("no pours on this board")
    result = {"pours": {}, "cutoff": [], "judged": 0}
    net_islands = collections.defaultdict(list)     # net -> [(area, key, k, items)] for the connectivity pass
    for (net, lid), zs in groups.items():
        lay = pn.BOARD.GetStandardLayerName(lid)
        z = zs[0]
        fill = pn.SHAPE_POLY_SET()
        for z_ in zs:
            fill.Append(z_.GetFilledPolysList(lid))
        if len(zs) > 1:
            # join pieces that share an edge: grow 1 um, union, shrink back.  A gap of any real
            # clearance (>= 0.05 mm) stays a gap
            fill.Inflate(1000, pn.CORNER_STRATEGY_CHAMFER_ALL_CORNERS, 100, True)
            fill.Inflate(-1000, pn.CORNER_STRATEGY_CHAMFER_ALL_CORNERS, 100, True)
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
              + " ".join(f"[{k}] {areas[i]:.1f}" for k, i in enumerate(order[:6]))
              + (f"  ({len(zs)} zones, one copper set)" if len(zs) > 1 else ""))

        def island(x, y):
            v = pn.VECTOR2I(pn.FromMM(x), pn.FromMM(y))
            for k, i in enumerate(order):
                if isl[i].Contains(v):
                    return k
            return None

        # same-net vias and through-hole pads
        per, outside = {}, []
        on = {}                     # island -> [(item, text)]: every pad and via standing on it
        for t in b.Tracks():
            if t.Type() == pn.PCB_VIA_T and short(t.GetNetname()) == net:
                x, y = mm(t.GetPosition().x), mm(t.GetPosition().y)
                # a via is IN the fill when the copper reaches its annulus: test just outside the hole
                r = mm(t.GetWidth(lid)) / 2 - 0.02
                k = next((island(x + dx, y + dy) for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r), (0, 0))
                          if island(x + dx, y + dy) is not None), None)
                (per.setdefault(k, []) if k is not None else outside).append(("via", x, y))
                if k is not None:
                    on.setdefault(k, []).append((t, f"via ({x:.2f}, {y:.2f})"))
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
                        on.setdefault(k, []).append((p, f"{f.GetReference()}.{p.GetNumber()}"))
                else:
                    if island(x, y) is not None or any(
                            island(x + dx, y + dy) is not None for dx, dy in ((0.6, 0), (-0.6, 0), (0, 0.6), (0, -0.6))):
                        smd_over.append(f"{f.GetReference()}.{p.GetNumber()}")
                    if p.IsOnLayer(lid):
                        # an SMD land on this layer: on the island its copper touches
                        ps = pn.SHAPE_POLY_SET()
                        p.TransformShapeToPolygon(ps, lid, pn.FromMM(0.01), pn.FromMM(0.005), pn.ERROR_OUTSIDE)
                        pb = ps.BBox()
                        for k, i in enumerate(order):
                            ib = isl[i].BBox()
                            if (pb.GetRight() < ib.GetLeft() or pb.GetLeft() > ib.GetRight()
                                    or pb.GetBottom() < ib.GetTop() or pb.GetTop() > ib.GetBottom()):
                                continue
                            x_ = pn.SHAPE_POLY_SET(ps)
                            x_.BooleanIntersection(isl[i])
                            if x_.OutlineCount():
                                on.setdefault(k, []).append((p, f"{f.GetReference()}.{p.GetNumber()}"))
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
        key = f"{net} on {lay}"
        result["pours"][key] = {"islands": n, "untied": len(untied), "zones": len(zs)}
        for k in range(n):
            bb_ = isl[order[k]].BBox()
            net_islands[net].append({"area": areas[order[k]], "key": key, "k": k, "items": on.get(k, []),
                                     "at": (mm(bb_.Centre().x), mm(bb_.Centre().y))})

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

    if groups:
        pour_net(B, net_islands, result)
    return result


def pour_net(B, net_islands, result):
    """Every pour island that holds a pad or a via must join the rest of its net, by KiCad's own
    connectivity (the fill as it is now, zones' shared edges, tracks, vias, pads on every layer).
    DRC cannot be trusted for it: its unconnected list is capped (499 on board1), so one more
    open item on a big board is not seen; and the untied-island test above passes a piece that
    holds a via -- the via may lead nowhere (board1 R6a_real: C22.1 and its in-pad via (26.0,
    17.5) on a 0.3 mm2 NVCC_DRAM island of In4, cut off by the fill, every gate PASS).
    The net's own copper is the cluster of its LARGEST pour island that holds an item (any layer);
    an island whose items are in another cluster is cut off.  Prints one POURNET line, and one
    line per island cut off: the island, its area, what stands on it, what its cluster reaches."""
    pn, b = B.pn, B.b
    short = lambda n: n.split("/")[-1]
    b.BuildConnectivity()
    conn = b.GetConnectivity()
    uid = lambda it: it.m_Uuid.AsString()

    def cluster(item):
        got = {uid(x): x for x in conn.GetConnectedItems(item)}
        got[uid(item)] = item
        return got

    def name(it):
        if it.Type() == pn.PCB_VIA_T:
            return f"via ({pn.ToMM(it.GetPosition().x):.2f}, {pn.ToMM(it.GetPosition().y):.2f})"
        if it.Type() == pn.PCB_PAD_T:
            return f"{it.GetParentFootprint().GetReference()}.{it.GetNumber()}"
        return None

    for net, isls in net_islands.items():
        held = [i for i in isls if i["items"]]
        result["judged"] += len(held)
        if not held:
            continue
        main_isl = max(held, key=lambda i: i["area"])
        main = cluster(main_isl["items"][0][0])
        for i in held:
            if i is main_isl:
                continue
            item = i["items"][0][0]
            if uid(item) in main:
                continue
            c = cluster(item)
            on_ids = {uid(it) for it, _ in i["items"]}
            beyond = [name(x) for k_, x in c.items() if k_ not in on_ids and name(x)]
            what = ", ".join(t for _, t in i["items"][:8]) + (" ..." if len(i["items"]) > 8 else "")
            reach = (f"joined to nothing else of {net}" if not beyond else
                     f"joined only to {len(beyond)} other item(s) ({', '.join(beyond[:4])}"
                     f"{' ...' if len(beyond) > 4 else ''}), not to {net}'s pour {main_isl['key']} [{main_isl['k']}]")
            result["cutoff"].append({"pour": i["key"], "island": i["k"], "area": i["area"], "at": i["at"],
                                     "items": [t for _, t in i["items"]], "beyond": beyond})
            print(f"   ⚠ cut off: {i['key']} island [{i['k']}] {i['area']:.2f} mm2 at ({i['at'][0]:.1f}, {i['at'][1]:.1f})"
                  f" holds {what}: {reach}")
    cut = result["cutoff"]
    print(f"\nPOURNET {'FAIL' if cut else 'PASS'}: {len(cut)} of {result['judged']} pour island(s) holding a pad or "
          f"via cut off from the rest of their net (KiCad's connectivity)"
          + ("".join(f"\n   {c['pour']} [{c['island']}] {c['area']:.2f} mm2 at ({c['at'][0]:.1f}, {c['at'][1]:.1f}): "
                     f"{', '.join((c['items'] + c['beyond'])[:6])}" for c in cut)))


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
    # from the FARTHEST load to the nearest source: every load pad draws through the cuts between
    # it and the sources, so the window starts at the last one (board2 RV22.4: starting at J1's
    # first +5V pin never cut the pour where pins 79-87 draw)
    lo, hi = (min(da), min(sa)) if max(da) < min(sa) else (max(sa), max(da))
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
