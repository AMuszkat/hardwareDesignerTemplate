#!/usr/bin/env python3
"""Does every rail via still reach its pour? -- the gate a BGA escape on a power layer needs.

    tools/kpy tools/route/pourreach.py --board board1 --layer In4.Cu --box 21.705,14.205,36.295,28.795 \\
        --vias placement/vias_U1.json --pad-nets VDD_SOC,VDD_ARM,VDD_GPU_VPU_DRAM,NVCC_DRAM \\
        [--plans routing/R1_esc_In4.json ...] [--nfp] [--min-width 0.1] [--sides W,N,S] [--json out.json]

A signal track routed through a power layer's via field can close the last gap between a
rail's via and the rest of its pour: DRC passes (there is no pour yet), the fill later leaves
the via on an island or joins it through a sliver no fab can etch. This answers it before
there is a pour: for each rail net N (`--pad-nets`, the pours this layer carries), copper of
N may fill any point at least `--clr-*` from every other net's copper on the layer and
`--min-width` / 2 from both sides (a neck narrower than the fab's minimum copper does not
fill); a via of N REACHES when that space connects it to the outside of `--box` (the pour
beyond the field) -- through other vias of N too. `--sides` limits the outside to the box's
named sides (W/N/E/S), for a layer whose other side the signal escapes take.

Copper on the layer: every via of the `--vias` models and the plans (their pads, or with
`--nfp` the HOLE + `--clr-hole` for a via of a net that is not a pour here and sits inside
`--box` -- 14_fab's removed non-functional pads), the plans' tracks on `--layer`, the
board's own pads, tracks and vias on it. Raster `--res` (0.0125 mm), plain python (KiCad's
interpreter has no numpy).

Prints one line per rail via cut off and `POURREACH PASS|FAIL <layer>: k of n rail vias cut off`;
exit 1 on any. `--json` writes the per-via result (and, with `--corridors`, a reach path of
`--min-width` per via from the no-plan geometry -- reserve it as the rail net's copper in the
router spec so the escape leaves the lane open).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "place"))
import place as P  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--board", default=None)
    ap.add_argument("--layer", required=True)
    ap.add_argument("--box", required=True, help="x0,y0,x1,y1: the field; outside it is the pour")
    ap.add_argument("--vias", action="append", default=[], help="fanout model json (list of {net, x, y})")
    ap.add_argument("--plans", nargs="*", default=[], help="route plans whose copper is on the layer")
    ap.add_argument("--pad-nets", required=True, help="the pours this layer carries (comma list)")
    ap.add_argument("--nets", default=None, help="test only these pour nets (default: all --pad-nets)")
    ap.add_argument("--nfp", action="store_true", help="other nets' vias inside --box are holes here")
    ap.add_argument("--via-pad", type=float, default=0.4)
    ap.add_argument("--drill", type=float, default=0.2)
    ap.add_argument("--clr-track", type=float, default=0.0889, help="pour to another net's track / pad")
    ap.add_argument("--clr-hole", type=float, default=0.1778, help="pour to another net's hole")
    ap.add_argument("--min-width", type=float, default=0.1, help="the narrowest copper that fills")
    ap.add_argument("--sides", default="W,N,E,S")
    ap.add_argument("--margin", type=float, default=1.0, help="raster beyond --box, mm")
    ap.add_argument("--res", type=float, default=0.0125)
    ap.add_argument("--json", default=None)
    ap.add_argument("--corridors", action="store_true", help="with --json: a reach path per via, plans left out")
    ap.add_argument("--feeds", nargs="*", default=[],
                    help="plans holding FEEDS: a cut-off via passes if its island on --layer holds a via of its "
                         "net from which that net's track on another layer reaches a via of the net outside --box")
    ap.add_argument("--max-mohm", type=float, default=50.0, help="a feed's pour-to-ball limit")
    ap.add_argument("--sheet-mohm", type=float, default=0.491, help="copper sheet resistance, mohm/sq (1 oz)")
    ap.add_argument("--via-mohm", type=float, default=1.0, help="one via barrel, mohm")
    a = ap.parse_args()
    import collections
    import math

    a.board = P.board_dir_arg(a.board)
    B = P.Board(a.board)
    pn = B.pn
    lid = B.b.GetLayerID(a.layer)
    x0, y0, x1, y1 = (float(v) for v in a.box.split(","))
    pours = [n.strip() for n in a.pad_nets.split(",") if n.strip()]
    test = [n.strip() for n in a.nets.split(",")] if a.nets else pours
    short = lambda n: (n or "").rsplit("/", 1)[-1]
    M, R = a.margin, a.res
    X0, Y0 = x0 - M, y0 - M
    nx, ny = int((x1 - x0 + 2 * M) / R) + 1, int((y1 - y0 + 2 * M) / R) + 1

    vias = []                                   # (net, x, y, kind, plan?)
    for f in a.vias:
        p = Path(f) if Path(f).is_absolute() else Path(B.dir) / f
        for v in json.loads(p.read_text()):
            vias.append((short(v["net"]), v["x"], v["y"], False))
    tracks = []                                 # (net, (ax, ay), (bx, by), w, plan?)
    for f in a.plans:
        p = Path(f) if Path(f).is_absolute() else Path(B.dir) / f
        pl = json.loads(p.read_text())
        for t in pl.get("tracks", []):
            if t.get("layer") == a.layer:
                for u, w in zip(t["pts"], t["pts"][1:]):
                    tracks.append((short(t["net"]), tuple(u), tuple(w), float(t["width"]), True))
        for v in pl.get("vias", []):
            vias.append((short(v["net"]), v["at"][0], v["at"][1], True))
    seen = set()
    vias = [v for v in vias if not ((round(v[1], 3), round(v[2], 3)) in seen or seen.add((round(v[1], 3), round(v[2], 3))))]
    pads = []                                   # (net, L, T, R, B)
    for ref, f in B.fps.items():
        for p in f.Pads():
            if p.IsOnLayer(lid):
                bb = p.GetBoundingBox()
                pads.append((short(p.GetNetname()), B.mm(bb.GetLeft()), B.mm(bb.GetTop()), B.mm(bb.GetRight()), B.mm(bb.GetBottom())))
    for t in B.b.GetTracks():
        if t.Type() == pn.PCB_VIA_T:
            vias.append((short(t.GetNetname()), B.mm(t.GetPosition().x), B.mm(t.GetPosition().y), False))
        elif t.GetLayer() == lid:
            tracks.append((short(t.GetNetname()), (B.mm(t.GetStart().x), B.mm(t.GetStart().y)),
                           (B.mm(t.GetEnd().x), B.mm(t.GetEnd().y)), B.mm(t.GetWidth()), False))
    inbox = lambda x, y: x0 <= x <= x1 and y0 <= y <= y1
    half = a.min_width / 2

    def span(lo, hi, o, n):
        return max(0, int((lo - o) / R)), min(n - 1, int((hi - o) / R) + 1)

    def blocked_for(net, with_plans=True):
        """bytearray ny*nx: 1 where copper of `net` (min-width / 2 from its edge) may not be"""
        m = bytearray(nx * ny)

        def disc(x, y, r):
            i0, i1 = span(y - r, y + r, Y0, ny)
            r2 = r * r
            for i in range(i0, i1 + 1):
                dy = Y0 + i * R - y
                w = r2 - dy * dy
                if w < 0:
                    continue
                w = math.sqrt(w)
                j0, j1 = max(0, math.ceil((x - w - X0) / R)), min(nx - 1, math.floor((x + w - X0) / R))
                if j1 >= j0:
                    m[i * nx + j0:i * nx + j1 + 1] = b"\x01" * (j1 - j0 + 1)

        def capsule(u, w, r):
            L = math.dist(u, w)
            n = max(1, int(L / (R * 2)))
            for k in range(n + 1):
                t = k / n
                disc(u[0] + (w[0] - u[0]) * t, u[1] + (w[1] - u[1]) * t, r)

        for n_, x, y, plan in vias:
            if n_ == net or (plan and not with_plans):
                continue
            hole = a.nfp and n_ not in pours and inbox(x, y)
            disc(x, y, (a.drill / 2 + a.clr_hole if hole else a.via_pad / 2 + a.clr_track) + half)
        for n_, u, w, wd, plan in tracks:
            if n_ == net or (plan and not with_plans):
                continue
            capsule(u, w, wd / 2 + a.clr_track + half)
        for n_, L, T, Rr, Bb in pads:
            if n_ == net:
                continue
            r = a.clr_track + half
            # a rounded box: its rectangle grown by r, the corners as discs
            for (yl, yh, xl, xh) in ((T, Bb, L - r, Rr + r), (T - r, Bb + r, L, Rr)):
                if yh < Y0 or xh < X0 or yl > Y0 + (ny - 1) * R or xl > X0 + (nx - 1) * R:
                    continue                      # wholly outside the raster
                i0, i1 = span(yl, yh, Y0, ny)
                j0, j1 = span(xl, xh, X0, nx)
                if j1 < j0:
                    continue
                for i in range(i0, i1 + 1):
                    m[i * nx + j0:i * nx + j1 + 1] = b"\x01" * (j1 - j0 + 1)
            for cx, cy in ((L, T), (Rr, T), (L, Bb), (Rr, Bb)):
                disc(cx, cy, r)
        assert len(m) == nx * ny, "raster resized -- a slice assignment overran"
        return m

    sides = {s_.strip().upper() for s_ in a.sides.split(",")}

    def is_out(i, j):
        x, y = X0 + j * R, Y0 + i * R
        if x0 <= x <= x1 and y0 <= y <= y1:
            return False
        return ((x < x0 and "W" in sides) or (x > x1 and "E" in sides) or x0 <= x <= x1) and \
               ((y < y0 and "N" in sides) or (y > y1 and "S" in sides) or y0 <= y <= y1) and \
               not (x < x0 and "W" not in sides) and not (x > x1 and "E" not in sides) and \
               not (y < y0 and "N" not in sides) and not (y > y1 and "S" not in sides)
    OUT = [k for k in range(nx * ny) if is_out(*divmod(k, nx))]

    def flood(blk, parents=False):
        """BFS from every free outside cell; returns the reached bytearray (and parents)"""
        seen = bytearray(nx * ny)
        par = [-1] * (nx * ny) if parents else None
        q = collections.deque()
        for k in OUT:
            if not blk[k]:
                seen[k] = 1
                q.append(k)
                if parents:
                    par[k] = k
        while q:
            k = q.popleft()
            i, j = divmod(k, nx)
            for kk, ok in ((k - 1, j > 0), (k + 1, j < nx - 1), (k - nx, i > 0), (k + nx, i < ny - 1)):
                if ok and not seen[kk] and not blk[kk]:
                    seen[kk] = 1
                    if parents:
                        par[kk] = k
                    q.append(kk)
        return seen, par

    res, cut, total = [], [], 0
    for net in test:
        mine = [(x, y) for n_, x, y, _ in vias if n_ == net and inbox(x, y)]
        if not mine:
            continue
        seen, _ = flood(blocked_for(net))
        for x, y in mine:
            total += 1
            k = int(round((y - Y0) / R)) * nx + int(round((x - X0) / R))
            ok = bool(seen[k])
            rec = dict(net=net, at=[x, y], reaches=ok)
            if not ok:
                cut.append(rec)
                print(f"   cut off: {net} via ({x:.3f}, {y:.3f})")
            res.append(rec)
        if a.corridors:
            # the no-plan geometry: a shortest reach path from each via to the outside, kept
            # min-width wide -- reserved in the router spec, the escape cannot close it
            _, par = flood(blocked_for(net, with_plans=False), parents=True)
            for rec in (r_ for r_ in res if r_["net"] == net):
                x, y = rec["at"]
                k = int(round((y - Y0) / R)) * nx + int(round((x - X0) / R))
                if par[k] < 0:
                    rec["corridor"] = None
                    continue
                pts = []
                while par[k] != k:
                    i, j = divmod(k, nx)
                    pts.append((round(X0 + j * R, 4), round(Y0 + i * R, 4)))
                    k = par[k]
                i, j = divmod(k, nx)
                pts.append((round(X0 + j * R, 4), round(Y0 + i * R, 4)))
                keep = [pts[0]]
                for p0, p1, p2 in zip(pts, pts[1:], pts[2:]):
                    if (round(p1[0] - p0[0], 4), round(p1[1] - p0[1], 4)) != (round(p2[0] - p1[0], 4), round(p2[1] - p1[1], 4)):
                        keep.append(p1)
                keep.append(pts[-1])
                rec["corridor"] = keep
    sides_s = ",".join(sorted(sides))
    if a.feeds and cut:
        # D60-style feeds: the via's island on this layer, then another layer's track to the pour
        fvias, ftracks = [], []
        for f in a.feeds:
            p_ = Path(f) if Path(f).is_absolute() else Path(B.dir) / f
            pl = json.loads(p_.read_text())
            fvias += [(short(v["net"]), v["at"][0], v["at"][1]) for v in pl.get("vias", [])]
            for t in pl.get("tracks", []):
                if t.get("layer") != a.layer:
                    for u, w in zip(t["pts"], t["pts"][1:]):
                        ftracks.append((short(t["net"]), t["layer"], tuple(u), tuple(w), float(t["width"])))
        allv = {(n_, round(x, 4), round(y, 4)) for n_, x, y, _ in vias} | {(n_, round(x, 4), round(y, 4)) for n_, x, y in fvias}
        still = []
        for rec in cut:
            net, (x, y) = rec["net"], rec["at"]
            blk = blocked_for(net)
            k0 = int(round((y - Y0) / R)) * nx + int(round((x - X0) / R))
            isl, q = {k0}, collections.deque([k0])
            while q:
                k = q.popleft()
                i, j = divmod(k, nx)
                for kk, ok in ((k - 1, j > 0), (k + 1, j < nx - 1), (k - nx, i > 0), (k + nx, i < ny - 1)):
                    if ok and kk not in isl and not blk[kk]:
                        isl.add(kk); q.append(kk)
            cell = lambda vx, vy: int(round((vy - Y0) / R)) * nx + int(round((vx - X0) / R))
            starts = [(vx, vy) for n_, vx, vy in allv if n_ == net and inbox(vx, vy) and cell(vx, vy) in isl]
            # the net's feed tracks as a graph; Dijkstra on resistance from the island's vias
            import heapq
            adj = collections.defaultdict(list)
            for n_, lay, u, w, wd in ftracks:
                if n_ != net:
                    continue
                r_ = math.dist(u, w) / wd * a.sheet_mohm
                ku, kw = (round(u[0], 4), round(u[1], 4)), (round(w[0], 4), round(w[1], 4))
                adj[ku].append((kw, r_)); adj[kw].append((ku, r_))
            outv = {(vx, vy) for n_, vx, vy in allv if n_ == net and not inbox(vx, vy)}
            best = None
            for sx, sy in starts:
                isl_r = math.dist((sx, sy), (x, y)) / max(a.min_width, 0.1) * a.sheet_mohm   # island, conservative
                dist = {(sx, sy): isl_r + a.via_mohm}
                hq = [(dist[(sx, sy)], (sx, sy))]
                while hq:
                    d, k = heapq.heappop(hq)
                    if d > dist.get(k, 1e18):
                        continue
                    if k in outv:
                        tot = d + a.via_mohm
                        if best is None or tot < best[0]:
                            best = (tot, (sx, sy), k)
                        break
                    for kk, r_ in adj.get(k, ()):
                        if d + r_ < dist.get(kk, 1e18):
                            dist[kk] = d + r_
                            heapq.heappush(hq, (d + r_, kk))
            if best and best[0] <= a.max_mohm + 1e-9:
                rec["fed"] = dict(mohm=round(best[0], 1), via=list(best[1]), to=list(best[2]))
                print(f"   fed: {net} via ({x:.3f}, {y:.3f}) -- island via {best[1]}, feed to {best[2]}, "
                      f"{best[0]:.1f} mohm (<= {a.max_mohm:g})")
            else:
                still.append(rec)
                if best:
                    print(f"   feed too long: {net} via ({x:.3f}, {y:.3f}) {best[0]:.1f} mohm > {a.max_mohm:g}")
        cut = still
    verdict = "PASS" if not cut else "FAIL"
    print(f"POURREACH {verdict} {a.layer}: {len(cut)} of {total} rail vias cut off "
          f"(min width {a.min_width}, sides {sides_s})")
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))
    return 0 if not cut else 1


if __name__ == "__main__":
    sys.exit(main())
