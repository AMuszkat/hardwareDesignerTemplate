#!/usr/bin/env python3
"""The decoupling ring as a plan: a via beside every pad, a stub to it, the pour.

    tools/kpy tools/route/decap.py --board board1 --group R2 \
        --refs C9,C10,C11 --zone routing/R2_zone.json --plan routing/R2_decap.json

A ring cap is only decoupling if BOTH its pads reach a via: the power pad down to
its pour, the ground pad down to the ground plane.  `tools/place/sites.py` finds
where a via is legal (it looks at every layer, which is the check `place.py
check` cannot make); this turns that into copper -- the via, and a stub from
the pad to it that turns only by 45 degrees.

The via and clearances are the board's own (`sites.py`'s defaults: the Default
class's via, the largest class clearance); `--via/--clr/--own/--reach` override
them.  `--pin REF.PAD=x,y` places one via by hand, which a COLUMN of caps needs:
their vias have to stand in a row, and the nearest legal site for each one on
its own does not give that.

The zone file is hand-drawn and holds the outline, the layer, the min width and
the priority: the shape of a power pour is a judgement about what it must not
run under (a sensitive bus that must see ground), and no search makes that
call.  Everything else here is mechanical.  Measure the fill afterwards with
`pour.py`: DRC passes a pour that fell into islands.
"""

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "place"))
import place as P  # noqa: E402
import sites as S  # noqa: E402

def class_width(board_dir, netclass_of, net):
    """the track width the net's class asks for, from the .kicad_pro -- a power
    stub at signal width fails route.py's width gate, and rightly: it is the rail."""
    pro = next(Path(board_dir).glob("*.kicad_pro"), None)
    j = json.loads(pro.read_text()) if pro else {}
    w = {c.get("name"): c.get("track_width") for c in j.get("net_settings", {}).get("classes", [])}
    return w.get(netclass_of(net), 0.2)


def stub(pad, via, w=0.2):
    """pad centre -> via, turning only by 45 degrees: straight if it can be,
    otherwise one axis leg and one diagonal, the diagonal at least w long."""
    (cx, cy), (vx, vy) = pad, via
    dx, dy = vx - cx, vy - cy
    if abs(dx) < 1e-9 or abs(dy) < 1e-9 or abs(abs(dx) - abs(dy)) < 1e-9:
        return [[round(cx, 4), round(cy, 4)], [round(vx, 4), round(vy, 4)]]
    if abs(dx) > abs(dy):                      # run along x, then the diagonal in
        kx = cx + math.copysign(abs(dx) - abs(dy), dx)
        mid = (kx, cy)
    else:
        ky = cy + math.copysign(abs(dy) - abs(dx), dy)
        mid = (cx, ky)
    return [[round(cx, 4), round(cy, 4)], [round(mid[0], 4), round(mid[1], 4)],
            [round(vx, 4), round(vy, 4)]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--refs", required=True, help="the caps, comma separated")
    ap.add_argument("--zone", help="JSON: the pour(s) to add, as the plan file holds them")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--group", required=True)
    ap.add_argument("--why", default="")
    ap.add_argument("--skip", default="", help="REF.PAD entries to leave alone (already routed)")
    ap.add_argument("--pin", default="", help="REF.PAD=x,y;... -- via sites chosen by hand. A column of "
                                              "caps at 1.0 mm pitch needs its vias in a ROW; the nearest "
                                              "legal site for each one on its own does not give that")
    ap.add_argument("--via", help="pad,drill (default: the Default class's)")
    ap.add_argument("--clr", type=float, help="clearance to foreign copper (default: largest class clearance)")
    ap.add_argument("--own", type=float, help="via centre to its own pad")
    ap.add_argument("--reach", type=float, help="how far from its pad a via may be")
    a = ap.parse_args()
    a.board = P.board_dir_arg(a.board)
    B = P.Board(a.board)
    mm = B.mm
    VIA, DRILL, *_ = S.configure(B, a.via, a.clr, a.own, a.reach)
    sys.path.insert(0, str(ROOT / "tools" / "route"))
    import route as R
    RB = R.RBoard(a.board)
    width = lambda net: class_width(a.board, RB.netclass, net)
    skip = set(a.skip.split(",")) if a.skip else set()
    pin = {}
    for q in a.pin.split(";"):
        if q:
            k, v = q.split("=")
            pin[k] = tuple(float(t) for t in v.split(","))
    tracks, vias = [], []
    made_vias, made_tracks = [], []        # what THIS plan has already placed: the
    for ref in a.refs.split(","):          # board does not know about it yet
        f = B.fps.get(ref) or P.die(f"{ref} is not on the board")
        W_ = S.World(B, ignore={ref})
        W_.vias += made_vias
        W_.tracks += made_tracks
        rects, info = [], []
        for p in f.Pads():
            bb = p.GetBoundingBox()
            rects.append((mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())))
            info.append((p.GetNumber(), p.GetNetname()))
        chosen = []
        for k, r in enumerate(rects):
            if f"{ref}.{info[k][0]}" in skip:
                chosen.append(None)
                continue
            key = f"{ref}.{info[k][0]}"
            if key in pin:
                x, y = pin[key]
                if not W_.via_ok(x, y, [r]):
                    P.die(f"{key}: the pinned via site ({x}, {y}) is not legal")
                v = (S.rect_dist(x, y, *r), x, y)
            else:
                v = W_.nearest_via(r, rects, skip=[q for q in chosen if q])
            if v is None:
                P.die(f"{ref}.{info[k][0]}: no legal via site within {S.REACH} mm")
            chosen.append((v[1], v[2]))
            made_vias.append((v[1], v[2], VIA / 2))
            centre = ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
            net = info[k][1]
            vias.append(dict(net=net, at=[v[1], v[2]], pad=VIA, drill=DRILL,
                             _=f"{ref}.{info[k][0]} down to its plane or pour"))
            w = width(net)
            pts = stub(centre, (v[1], v[2]), w)
            if len(pts) > 1 and math.dist(pts[0], pts[-1]) > 1e-6:
                tracks.append(dict(net=net, layer="B.Cu" if f.IsFlipped() else "F.Cu", width=w, pts=pts,
                                   _=f"{ref}.{info[k][0]} to its via, {v[0]:.2f} mm from the pad"))
                made_tracks += [(tuple(pts[i]), tuple(pts[i + 1]), w) for i in range(len(pts) - 1)]
    zones = json.loads(P.boardsel.in_board(a.board, a.zone).read_text()) if a.zone else []
    if isinstance(zones, dict):                 # a plan file: take its "zones"
        zones = zones.get("zones", [])
    if not Path(a.plan).is_absolute() and not Path(a.plan).parent.exists():
        a.plan = str(Path(a.board) / a.plan)    # "routing/R3.json" means the board's routing/
    Path(a.plan).write_text(json.dumps(
        {"group": a.group, "why": a.why, "tracks": tracks, "vias": vias, "zones": zones}, indent=1) + "\n")
    print(f"wrote {a.plan}: {len(tracks)} stubs, {len(vias)} vias, {len(zones)} zone(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
