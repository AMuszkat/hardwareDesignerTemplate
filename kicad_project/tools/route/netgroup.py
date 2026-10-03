#!/usr/bin/env python3
"""Route a group of signal nets from a spec file -- the negotiated router
(maze.py) driven by data instead of by a script per group.

    tools/kpy tools/route/netgroup.py --board board1 routing/R3_spec.json
    tools/kpy tools/route/netgroup.py --board board1 routing/R3_spec.json --only SPI_CLK,SPI_CS

It writes the spec's "plan" file, which `route.py check` then judges like any
other plan: KiCad's DRC on a scratch copy and the rule gates in gates.json.

THE SPEC

  {"group": "R3", "why": "...", "plan": "routing/R3_spi.json",
   "window": [x0, y0, x1, y1], "pitch": 0.2, "width": 0.2,
   "clearance": 0.15,        # between the nets routed here (their class)
   "padclr": 0.2,            # to everything else (the strictest class near)
   "via": [0.6, 0.3], "via_sep": 0.8,
   "layers": ["F.Cu", "B.Cu"],          # default: every copper layer that is not a
                                        # gates.json plane layer
   "edge": 0.5,              # default: gates.json "edge", else Board Setup
   "plane": {"zone": "routing/R2_3v3.json", "net": "+3V3", "layer": "In2.Cu", "apron": 1.0},
   "nets": {
     "SPI_CLK": {"src": [134.1, 129.4], "layer": "F.Cu",
                 "legs": [{"to": ["R7", "2"]}, {"to": ["J2", "1"]}]},
     "SPI_CS":  {"src": [139.5, 118.8], "layer": "F.Cu",
                 "legs": [{"to": ["R13", "1"]}, {"to": ["R16", "2"], "from": "src"}]}}}

A net's "src" is the free end of copper it already has (an escape); the
heading it arrives there with is read off that copper.  Leg k starts at the
previous leg's pad ("from": "prev", the default), back at the source
("from": "src"), or at the net's own via nearest the goal ("from": "via": a
via entry is free under the angle rule, for when the pad is walled in by the
others' first legs), or anywhere along its own earlier copper ("from": "trunk":
a tee at 0/45 degrees to the trunk) and ends on a pad.  All nets' leg k are
negotiated together, then frozen as fixed copper before leg k+1 -- a pull-up
pad visited on the way is part of the path, not a stub hung off a finished run.

WHAT IS BLOCKED, beyond the board's own copper:
  * a "field" footprint's pad field, if the spec names one (its escapes are
    laid; nothing new goes in -- unless "open" lists layers, below);
  * the plane named in "plane" on its layer, except an apron round each net's
    own escape end on that layer -- a signal across a plane is a slot;
  * a band "edge" mm (+ half a track) inside the board edge;
  * a via inside any goal pad (an 0402 with a drill in it is a solder thief);
  * the spec's "block": [{"rect": [x0, y0, x1, y1], "layers": [...], "vias": true}];
  * the spec's "extra_tracks"/"extra_vias" (the group's ground and supply
    vias and pad links), which go into the plan as they are;
  * the spec's "reserve_tracks"/"reserve_vias": a later group's copper, kept
    free here and NOT written to the plan.  Reserve the later groups' tracks,
    not just their vias.

OPTIONS
  "field": {"ref": "U1", "open": ["In2.Cu", "B.Cu"], "clr": 0.1016, "width": 0.127}
    names a fine-pitch part whose pad field is shut, and opens it on those
    layers (no lands there; the dog-bones are the obstacles, at the field's own
    clearance "clr", for a track of "width").  A net's prelaid dog-bone (an
    extra_via inside the field) is a legal layer change for it; no new via is
    laid inside the field.  "open": [] keeps it shut on every layer;
  a net's "avoid": [[x0, y0, x1, y1(, layer)], ...] prices those cells out for it
    ("avoid_cost", default 1e4 per cell -- soft: negotiation outbids it; 1e8
    makes it effectively hard for that net) -- a connector's pin field that
    belongs to the nets ending there;
  a leg's "goal_layer" (default F.Cu) is the layer it must arrive on -- a
    through-hole pin walled in on F.Cu is reached on B.Cu;
  a leg's "to_xy": [x, y] ends it on a point, and "to_path": [[x, y], ...]
    anywhere along that polyline of the net's own prelaid copper; its
    "goal_dirs": [headings 1..8, clockwise from east] lists the headings it may
    arrive with (a via is always a legal arrival): [2, 4, 6, 8] is a 45-degree
    tee onto a straight run, [99] a via only;
  a leg's "stage" (default: its index) is the negotiation round it joins;
  a net's "layers": [...] restricts the layers it may use (its source layer
    and the goal's layer are always allowed) -- two nets that insist on
    crossing on one layer are parted by taking a layer from one of them;
  a net's "src_candidates": [[x, y, layer], ...] -- the first from which its
    first goal is reachable becomes its source;
  "pad_via_gap": 0.15 lets a via stand that close to a pad of its own net
    (default: the foreign clearance) -- never inside it;
  "fix_exact": true judges a 45-degree step against the frozen copper by its end
    cells, with that copper's halo grown by the step's sagitta
    (maze.Grid.fix_exact) -- for escapes frozen from an earlier run, whose
    parallel diagonals the default corner test cannot rebuild;
  "retrace_cost": 20 prices each cell of a net's OWN frozen copper it would run
    along (not round its leg's start): standing on it is legal, running back
    along it lays copper on copper and a 180-degree joint where the route
    leaves;
  "plane": {..., "open": [[x0, y0, x1, y1], ...], "open_cost": 3} lets any
    net cross the plane inside those rectangles at a price per cell -- every
    such crossing is a slot, so `pour.py` judges the result;
  "layer_cost": {"B.Cu": 1.5} prices a layer per step.

A net that starts on a pad rather than on escape copper (the far side of a
series resistor) names it as "src_pad" and gives a grid point inside it as
"src".

When negotiation stalls it prints, per net, how many cells it can reach at
all (`reach`): a net that reaches a few dozen cells against tens of
thousands for the others is WALLED IN -- a placement or fanout defect to fix,
not a route to keep negotiating for.  `--repair` re-routes the overlapping
nets one at a time with the rest hard.  A stalled run writes
<plan>.partial.json for a look (never for a gate).
"""

import argparse
import collections
import json
import math
import sys
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "place"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import place as P  # noqa: E402
import maze  # noqa: E402
from corners import straighten  # noqa: E402


def heading(a, b):
    """maze's heading of the step a -> b: 1..8 clockwise from east (y down)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ang = math.degrees(math.atan2(dy, dx)) % 360
    return int(round(ang / 45.0)) % 8 + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("spec")
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--pres0", type=float, default=0.5)
    ap.add_argument("--pres-mul", type=float, default=1.1)
    ap.add_argument("--hist", type=float, default=0.5)
    ap.add_argument("--hist-decay", type=float, default=0.7)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--only", default="", help="route only these nets (short names), comma separated")
    ap.add_argument("--repair", action="store_true",
                    help="when negotiation stalls, re-route the overlapping nets one at a time, the rest hard")
    ap.add_argument("--limit", type=int, default=900000,
                    help="A* expansions per route; a big window hits the default and reads as 'walled in'")
    a = ap.parse_args()
    a.board = P.board_dir_arg(a.board)

    spec_path = Path(a.spec)
    if not spec_path.is_absolute() and not spec_path.exists():
        spec_path = Path(a.board) / spec_path
    S = json.loads(spec_path.read_text())
    # everything the run needs, BEFORE an hour of routing: a missing key found
    # at the end costs the whole result
    lack = [k for k in ("window", "nets", "why", "plan") if k not in S]
    if lack:
        sys.exit(f"{spec_path.name}: the spec lacks {lack} (tools/route/README.md)")
    for n_, q_ in S["nets"].items():
        stages = [lg.get("stage", i) for i, lg in enumerate(q_.get("legs", []))]
        dup = sorted({x for x in stages if stages.count(x) > 1})
        if dup:
            sys.exit(f"{n_}: two legs share stage {dup} -- one leg of a net per stage "
                     "(the second would be dropped)")
    W, PITCH = float(S.get("width", 0.2)), float(S.get("pitch", 0.2))
    RETRACE = float(S.get("retrace_cost", 20.0))     # per cell of a net's own frozen copper it runs along
    VIA, DRILL = S.get("via", [0.6, 0.3])
    maze.CLR, maze.PADCLR, maze.W, maze.VIA_R = float(S.get("clearance", 0.15)), float(S.get("padclr", 0.2)), W, VIA / 2
    B = P.Board(a.board)
    import pcbnew
    gates_file = Path(a.board) / "routing" / "gates.json"
    gates = json.loads(gates_file.read_text()) if gates_file.exists() else {}
    planes = {q["layer"] for q in gates.get("plane_layers", []) or []}
    canon = lambda lid: pcbnew.BOARD.GetStandardLayerName(lid)    # a board may rename its layers
    planes = {canon(B.b.GetLayerID(n)) for n in planes}
    LAYERS = [canon(B.b.GetLayerID(n)) for n in S["layers"]] if S.get("layers") else \
        [canon(l) for l in B.b.GetEnabledLayers().CuStack() if canon(l) not in planes]
    EDGE = float(S["edge"]) if "edge" in S else \
        float((gates.get("edge") or {}).get("min", B.edge_clr))
    print(f"layers {', '.join(LAYERS)}; copper kept {EDGE} mm off the edge")

    # short name -> full net name
    full, leaves = {}, collections.defaultdict(list)
    for n in B.b.GetNetsByName().keys():
        s = str(n)
        full.setdefault(s.rsplit("/", 1)[-1], s)
        leaves[s.rsplit("/", 1)[-1]].append(s)
    nets = [n for n in S["nets"] if not a.only or n in a.only.split(",")]
    for n in nets:
        if n not in full:
            sys.exit(f"{n}: no such net on the board")
        if len(leaves[n]) > 1:
            # nets are told apart by their LEAF name in here; two sheets with an
            # "EN" each would be routed as one net
            sys.exit(f"{n}: {len(leaves[n])} nets on the board end in that name "
                     f"({', '.join(leaves[n])}). Rename one in the schematic, or route them by hand.")

    pads, tracks, bvias, padbox = [], [], [], {}
    for ref, f in B.fps.items():
        for p in f.Pads():
            bb = p.GetBoundingBox()
            rec = dict(ref=ref, pad=p.GetNumber(), net=p.GetNetname().rsplit("/", 1)[-1],
                       tht=p.GetDrillSizeX() > 0, round=p.GetShape() == pcbnew.PAD_SHAPE_CIRCLE,
                       cu="B.Cu" if f.IsFlipped() else "F.Cu",          # an SMD pad's own side
                       L=B.mm(bb.GetLeft()), T=B.mm(bb.GetTop()), R=B.mm(bb.GetRight()), B=B.mm(bb.GetBottom()),
                       x=B.mm(p.GetPosition().x), y=B.mm(p.GetPosition().y))
            pads.append(rec)
            padbox[(ref, p.GetNumber())] = rec
    for t in B.b.GetTracks():
        n = t.GetNetname().rsplit("/", 1)[-1]
        if t.Type() == pcbnew.PCB_VIA_T:
            bvias.append(dict(net=n, x=B.mm(t.GetPosition().x), y=B.mm(t.GetPosition().y),
                              r=B.mm(t.GetWidth(pcbnew.F_Cu)) / 2))
        else:
            tracks.append(dict(net=n, layer=canon(t.GetLayer()), a=(B.mm(t.GetStart().x), B.mm(t.GetStart().y)),
                               b=(B.mm(t.GetEnd().x), B.mm(t.GetEnd().y)), w=B.mm(t.GetWidth())))
    # the spec's own extra copper (ground and supply vias, pad links) is on the board
    # as far as the router is concerned -- else it routes through where they will go
    for e in S.get("extra_tracks", []):
        for a_, b_ in zip(e["pts"], e["pts"][1:]):
            tracks.append(dict(net=e["net"].rsplit("/", 1)[-1], layer=e["layer"], a=tuple(a_), b=tuple(b_),
                               w=float(e["width"])))
    for e in S.get("extra_vias", []):
        bvias.append(dict(net=e["net"].rsplit("/", 1)[-1], x=e["at"][0], y=e["at"][1], r=float(e["pad"]) / 2))
    # a LATER group's copper, reserved: an obstacle here, never written to this plan
    # (reserve the later groups' tracks, not just their vias)
    for e in S.get("reserve_tracks", []):
        for a_, b_ in zip(e["pts"], e["pts"][1:]):
            tracks.append(dict(net=e["net"].rsplit("/", 1)[-1], layer=e["layer"], a=tuple(a_), b=tuple(b_),
                               w=float(e["width"])))
    for e in S.get("reserve_vias", []):
        bvias.append(dict(net=e["net"].rsplit("/", 1)[-1], x=e["at"][0], y=e["at"][1], r=float(e["pad"]) / 2))

    # On a slot grid (pitch = track + clearance) the next slot is EXACTLY at the
    # clearance distance, which a non-strict grid counts as blocked: say so with
    # "strict", or it is inferred from the numbers.
    STRICT = bool(S["strict"]) if "strict" in S else abs(PITCH - (W + maze.CLR)) < 1e-6
    G = maze.Grid(*S["window"], LAYERS, step=PITCH, strict=STRICT)
    G.via_sep = float(S.get("via_sep", 0.8))
    PADVIA = float(S.get("pad_via_gap", maze.PADCLR))
    AVOID = float(S.get("avoid_cost", 1e4))      # per cell of an "avoid" rectangle; negotiation's present
                                                 # cost climbs past 1e4, so a hint that must hold takes 1e8
    G.diagonal = True
    G.only45 = True                                  # the angle rule: 0 or 45 degrees only
    G.fix_exact = bool(S.get("fix_exact"))           # frozen bundles: see maze.Grid.fix_exact

    goals_of = {n: [tuple(l["to"]) for l in S["nets"][n]["legs"] if "to" in l] + ([tuple(S["nets"][n]["src_pad"])]
                                                                   if S["nets"][n].get("src_pad") else [])
                for n in nets}
    goal_net = {k: n for n, v in goals_of.items() for k in v}
    fixed = {n: dict(tracks=[], vias=[]) for n in nets}
    FO = S.get("field")                  # {"ref", "open": [inner layers], "clr", "width"}
    FREF = FO["ref"] if FO else None
    if FO and FREF not in B.fps:
        sys.exit(f"field: {FREF} is not on the board")
    for p in pads:
        k = (p["ref"], p["pad"])
        lays = LAYERS if p["tht"] else [L_ for L_ in (p["cu"],) if L_ in LAYERS]
        owner = goal_net.get(k) or (p["net"] if p["net"] in fixed and p["ref"] != FREF else None)
        if owner:                         # a pad of a net routed here is that net's copper (no via in it)
            # "pad_via_gap": how far a via's ring stays from its own net's pad (default the foreign
            # clearance) -- a pull-up whose only legal via site is 0.15-0.3 mm from its pad.
            G.block(["via"], G.rect(p["L"], p["T"], p["R"], p["B"], PADVIA + maze.VIA_R))
            if p["round"]:
                d = p["R"] - p["L"]
                for L_ in lays:
                    fixed[owner]["tracks"].append((L_, (p["x"], p["y"]), (p["x"], p["y"]), d))
            else:
                h, wd = p["B"] - p["T"], p["R"] - p["L"]
                if wd >= h:
                    seg = ((p["L"] + h / 2, p["y"]), (p["R"] - h / 2, p["y"]), h)
                else:
                    seg = ((p["x"], p["T"] + wd / 2), (p["x"], p["B"] - wd / 2), wd)
                for L_ in lays:
                    fixed[owner]["tracks"].append((L_, *seg))
            continue
        G.static_pad(p["L"], p["T"], p["R"], p["B"], lays)
    if FO:
        bx = [p for p in pads if p["ref"] == FREF]
        fL, fT, fR, fB = min(p["L"] for p in bx), min(p["T"] for p in bx), max(p["R"] for p in bx), max(p["B"] for p in bx)
        in_field = lambda x, y: fL - 0.3 <= x <= fR + 0.3 and fT - 0.3 <= y <= fB + 0.3
    else:
        in_field = lambda x, y: False
    fclr = float(FO.get("clr", maze.PADCLR)) if FO else maze.PADCLR
    # "width": the track the escapes inside the field really are (thinner than the group's).
    # The grid carries one track width, so inside the field every obstacle's halo is shrunk by
    # the difference instead: a lane between two dog-bones that a thin track clears at the
    # field's clearance is walled shut for a modelled full-width one.
    fshrink = (maze.W - float(FO.get("width", maze.W))) / 2 if FO else 0.0
    for t in tracks:
        if t["net"] in fixed:
            fixed[t["net"]]["tracks"].append((t["layer"], t["a"], t["b"], t["w"]))
        else:
            # a track on a layer this group does not route on still blocks VIAS:
            # a through via lands on every layer (static_track skips the layer block itself)
            inside = FO and t["layer"] in FO.get("open", []) and in_field(*t["a"]) and in_field(*t["b"])
            G.static_track(t["layer"], t["a"], t["b"], t["w"], clr=fclr - fshrink if inside else maze.PADCLR)
    board_vias = collections.defaultdict(list)
    for v in bvias:
        board_vias[v["net"]].append((v["x"], v["y"]))
    for v in bvias:
        if v["net"] in fixed:
            r_ = v["r"]
            if FO and in_field(v["x"], v["y"]):
                r_ -= maze.CLR - fclr + fshrink   # the halo is r + CLR: inside the field the field's rule applies
            fixed[v["net"]]["vias"].append((v["x"], v["y"], r_, v["r"] + maze.CLR + maze.VIA_R))
        elif FO and in_field(v["x"], v["y"]):
            # a dog-bone: the field's clearance on its open layers, the full one on the rest
            for L_ in LAYERS:
                cl = fclr - fshrink if L_ in FO.get("open", []) else maze.PADCLR
                G.block([L_], G.disc(v["x"], v["y"], v["r"] + cl + maze.W / 2))
            G.block(["via"], G.disc(v["x"], v["y"], v["r"] + maze.PADCLR + maze.VIA_R))
        else:
            G.static_via(v["x"], v["y"], v["r"])

    # the field's pad area: escapes only, and they are laid -- unless the spec opens
    # its inner layers, where the balls have no lands and the dog-bones are the
    # only obstacles
    if FO:
        shut = [L_ for L_ in LAYERS if L_ not in FO.get("open", [])]
        G.block(shut, G.rect(fL, fT, fR, fB, 0.30))
        G.block(["via"], G.rect(fL, fT, fR, fB, 0.30 + maze.VIA_R))
    # the board edge
    o = B.outline
    band = set()
    for c in range(G.nx * G.ny):
        x, y = G.xy(c)
        dd = min(x - o[0], o[2] - x, y - o[1], o[3] - y)
        if dd < EDGE + W / 2 + 1e-6:
            band.add(c)
    G.block(LAYERS, band)
    G.block(["via"], {c for c in range(G.nx * G.ny)
                      if min(G.xy(c)[0] - o[0], o[2] - G.xy(c)[0], G.xy(c)[1] - o[1], o[3] - G.xy(c)[1]) < EDGE + maze.VIA_R + 1e-6})
    for r in S.get("block", []):
        G.block(r.get("layers", LAYERS), G.rect(*r["rect"]))
        if r.get("vias", True):
            G.block(["via"], G.rect(*r["rect"], maze.VIA_R))

    # sources: the free end of each net's own copper, and the heading it arrives with
    src = {}
    for n in nets:
        q = S["nets"][n]
        s, lay = tuple(q["src"]), q["layer"]
        hd = q.get("dir")
        if hd is None:
            hd = 0
            for L_, a_, b_, _w in fixed[n]["tracks"]:
                if L_ == lay and math.dist(b_, s) < 0.02 and math.dist(a_, s) > 0.02:
                    hd = heading(a_, b_)
                elif L_ == lay and math.dist(a_, s) < 0.02 and math.dist(b_, s) > 0.02:
                    hd = heading(b_, a_)
        if math.dist(G.xy(G.cell(*s)), s) > 1e-4:      # a pad at y = 122.4375 is on the grid
            sys.exit(f"{n}: source {s} is not a grid point -- choose the window origin so it is")
        src[n] = (s, lay, hd)

    # every OTHER pour on a routing layer is copper the router must keep off: a track
    # through it would simply be refilled around, and the pour cut in two
    pln = S.get("plane") or {}
    zpoly = None

    def zone_file(rel):
        """The zones of a plan file -- or of a bare list, which decap.py and pour.py also take."""
        zf = json.loads(P.boardsel.in_board(a.board, rel).read_text())
        return zf.get("zones", []) if isinstance(zf, dict) else zf
    if pln and pln["layer"] not in LAYERS:
        sys.exit(f"spec \"plane\": {pln['layer']} is not one of this group's routing layers "
                 f"({', '.join(LAYERS)}). A gates.json plane layer is left out by default: name "
                 "it in the spec's \"layers\" if this group really routes on it.")
    if pln:
        zq = [z for z in zone_file(pln["zone"])
              if z.get("net") == pln["net"] and z.get("layer") == pln["layer"]]
        zpoly = [tuple(q) for q in zq[0]["poly"]] if zq else None
    nz = 0
    for z in B.b.Zones():
        if z.GetIsRuleArea():
            continue
        zn = z.GetNetname().rsplit("/", 1)[-1]
        if zn in fixed:
            continue
        for L_ in LAYERS:
            lid = B.b.GetLayerID(L_)
            if not z.IsOnLayer(lid):
                continue
            ol = z.Outline()
            pts = [(B.mm(ol.CVertex(k).x), B.mm(ol.CVertex(k).y)) for k in range(ol.TotalVertices())]
            if zpoly and zn == pln["net"] and L_ == pln["layer"] and \
                    all(min(math.dist(q, r) for r in zpoly) < 0.05 for q in pts):
                continue                  # the plane itself: handled below
            bb = z.GetBoundingBox()
            acc = int((maze.PADCLR + W / 2) * 1e6)
            cells = G.rect(B.mm(bb.GetLeft()), B.mm(bb.GetTop()), B.mm(bb.GetRight()), B.mm(bb.GetBottom()),
                           maze.PADCLR + W / 2)
            hit = {c for c in cells if z.HitTestFilledArea(lid, pcbnew.VECTOR2I(*(int(v * 1e6) for v in G.xy(c))), acc)}
            G.block([L_], hit)            # a via through it is only an antipad: vias stay legal
            nz += 1
    print(f"{nz} other pour(s) blocked on the routing layers")

    # the plane: blocked on its layer except an apron round each net's own escape end there
    pl = S.get("plane")
    plane_bias = {}
    if pl:
        zs = [z for z in zone_file(pl["zone"])
              if z.get("net") == pl["net"] and z.get("layer") == pl["layer"]]
        poly = zs[0]["poly"]

        def inside(x, y):
            c = False
            for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
                if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                    c = not c
            return c
        aprons = {n: (src[n][0], float(pl.get("apron", 1.0))) for n in nets if src[n][1] == pl["layer"]}
        opened = [tuple(r) for r in pl.get("open", [])]   # where a signal may cut the plane, at a price
        ocost = float(pl.get("open_cost", 3.0))
        plane, cells, ocells = set(), set(), set()
        for c in range(G.nx * G.ny):
            x, y = G.xy(c)
            if inside(x, y):
                plane.add(c)
                if any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in opened):
                    ocells.add(c)
                elif not any(math.dist((x, y), q) <= r for q, r in aprons.values()):
                    cells.add(c)
        G.block([pl["layer"]], cells)
        li = LAYERS.index(pl["layer"])
        for n, (q, r) in aprons.items():             # an apron is its own net's, nobody else's
            arr = array("f", bytes(4 * G.nx * G.ny))
            for c in plane - cells:
                if math.dist(G.xy(c), q) > r:
                    arr[c] = 1e4
            plane_bias[n] = arr
        for n in nets:
            if n not in plane_bias:
                arr = array("f", bytes(4 * G.nx * G.ny))
                for c in plane - cells:
                    arr[c] = 1e4
                plane_bias[n] = arr
        for n in nets:
            for c in ocells:
                plane_bias[n][c] = ocost
        print(f"{pl['net']} plane on {pl['layer']}: {len(cells)} cells blocked, {len(ocells)} open at "
              f"{ocost}/cell, aprons for " + ", ".join(aprons))

    for n in nets:
        G.register(n, "fixed", tracks=fixed[n]["tracks"], vias=fixed[n]["vias"])
        for x, y, _r, _s in fixed[n]["vias"]:
            # its own dog-bone is a legal layer change for it, and so is the via it starts on
            if (FO and in_field(x, y)) or math.dist((x, y), tuple(S["nets"][n]["src"])) < 0.05:
                G.unblock(["via"], {G.cell(x, y)})

    def goal_cells(ref, pad):
        p = padbox[(ref, pad)]
        if p["round"]:
            r = (p["R"] - p["L"]) / 2 - 0.15
            cells = {c for c in G.disc(p["x"], p["y"], r)}
        else:
            hw, hh = max(0.0, (p["R"] - p["L"]) / 2 - 0.12), max(0.0, (p["B"] - p["T"]) / 2 - 0.12)
            cells = G.rect(p["x"] - hw, p["y"] - hh, p["x"] + hw, p["y"] + hh)
        return cells or {G.cell(p["x"], p["y"])}

    def leg_goals(leg):
        """A leg ends on a pad ("to": [ref, pad]), on a point ("to_xy": [x, y]) or anywhere along a
        polyline ("to_path": [[x, y], ...]) -- a point or a path is copper laid for the net
        beforehand (a connector's escape stubs; a link drawn into a column of pull-ups)."""
        if "to_path" in leg:
            cells = set()
            for (ax, ay), (bx, by) in zip(leg["to_path"], leg["to_path"][1:]):
                m = max(1, int(math.dist((ax, ay), (bx, by)) / (PITCH / 2)))
                for k in range(m + 1):
                    # the path's copper, not its rounded centre-line: a row off the grid (a pull
                    # pad's y) has no cell on it, and the via cell beside it is one the halo of a
                    # neighbour may leave free
                    cells |= G.disc(ax + (bx - ax) * k / m, ay + (by - ay) * k / m, 0.151)
            return cells
        if "to_xy" in leg:
            return {G.cell(*leg["to_xy"][:2])}
        return goal_cells(*leg["to"])

    def leg_layer(leg):
        """The layer a leg ENDS on: what the spec says, else the goal pad's own
        side (an SMD pad on the back is reached on B.Cu, not F.Cu)."""
        if leg.get("goal_layer"):
            return leg["goal_layer"]
        if "to" in leg and "to_path" not in leg and "to_xy" not in leg:
            q = padbox[tuple(leg["to"])]
            return "F.Cu" if q["tht"] else q["cu"]
        return "F.Cu"

    def leg_xy(leg):
        if "to_path" in leg:
            return tuple(leg["to_path"][0][:2])
        if "to_xy" in leg:
            return tuple(leg["to_xy"][:2])
        q = padbox[tuple(leg["to"])]
        return q["x"], q["y"]

    # "src_candidates": [[x, y, layer], ...] -- the first one from which the net's first goal
    # is reachable becomes its source (a tee at one of its own vias; the nearest may be walled in)
    for n in nets:
        cands = S["nets"][n].get("src_candidates")
        if not cands:
            continue
        leg0 = S["nets"][n]["legs"][0]
        chosen = None
        for x, y, lay in cands:
            if lay != "F.Cu":                 # a via of its own (an F.Cu candidate is a pad: no via in it)
                G.unblock(["via"], {G.cell(x, y)})
            job = dict(net=n, src=(x, y), src_layer=lay, goals=leg_goals(leg0), start_dir=0)
            r, hit = reach(G, job, LAYERS)
            if hit:
                chosen = (x, y, lay)
                break
        if chosen:
            src[n] = ((chosen[0], chosen[1]), chosen[2], 0)
            print(f"   {n}: starts at {chosen[:2]} on {chosen[2]} (candidate {cands.index(list(chosen)) + 1} of {len(cands)})")
        else:
            print(f"   {n}: no candidate source reaches {leg0.get('to', leg0.get('to_xy'))}")

    # a leg's "stage" (default: its index) is the negotiation round it is routed in: legs of
    # the same round are negotiated together, then frozen before the next round
    def st_(n, i):
        return int(S["nets"][n]["legs"][i].get("stage", i))
    nstage = max(st_(n, i) for n in nets for i in range(len(S["nets"][n]["legs"]))) + 1
    out, own = {}, {}
    li_plane = LAYERS.index(pl["layer"]) if pl else None
    for stage in range(nstage):
        jobs = []
        for n in nets:
            legs = S["nets"][n]["legs"]
            idx = [i for i in range(len(legs)) if st_(n, i) == stage]
            if not idx:
                continue
            li = idx[0]
            leg = legs[li]
            if (li == 0 and leg.get("from") not in ("trunk", "via")) or leg.get("from") == "src":
                s, lay, hd = src[n]
            elif leg.get("from") == "trunk":
                # a tee off the net's own earlier copper, anywhere along it: the branch leaves
                # at 0/45 degrees to the trunk (a tee at 45/135 passes the angle rule), so its start heading
                # is the trunk's, either way along it.  Nearest the goal first; the first start
                # from which a real route exists wins; the previous pad is the fallback.
                gx_, gy_ = leg_xy(leg)
                gp = dict(x=gx_, y=gy_)
                seen, cands = [], []
                trunks_ = [(layer_, G.polyline(cells_)) for runs_, _vv in out.get(n, []) for layer_, cells_ in runs_]
                # ... and its own fixed copper, but only what the net is already joined to: a pad or a
                # stub that a leg from here on still has to REACH is its copper too, and a tee from it
                # would be a route from the goal to itself
                ahead_ = []
                for lg_ in legs[li:]:
                    if "to_xy" in lg_ or "to_path" in lg_:
                        ahead_.append(leg_xy(lg_))
                    else:
                        q_ = padbox[tuple(lg_["to"])]
                        ahead_.append((q_["x"], q_["y"]))
                def joined_(a_, b_):
                    for g_ in ahead_:
                        if min(math.dist(a_, g_), math.dist(b_, g_)) < 0.05:
                            return False
                        for pk_ in [tuple(lg_["to"]) for lg_ in legs[li:] if "to" in lg_]:
                            pb_ = padbox[pk_]
                            if pb_["L"] - 0.01 <= a_[0] <= pb_["R"] + 0.01 and pb_["T"] - 0.01 <= a_[1] <= pb_["B"] + 0.01:
                                return False
                    return True
                stubs_ = {}
                for l_, a_, b_, _w in fixed[n]["tracks"]:
                    stubs_.setdefault((round(a_[0], 3), round(a_[1], 3)), []).append(b_)
                def on_stub_to_goal(a_, b_):
                    # a stub laid to a goal point: walk it from the goal end back
                    for g_ in ahead_:
                        if math.dist(b_, g_) < 0.05 or math.dist(a_, g_) < 0.05:
                            return True
                    return False
                allfx_ = [(l_, a_, b_) for l_, a_, b_, _w in fixed[n]["tracks"] if math.dist(a_, b_) > 0.3]
                fx_ = [q_ for q_ in allfx_ if joined_(q_[1], q_[2])]
                # drop every piece of a polyline that leads to a goal still ahead (escape stubs):
                # walk back from the goals, through the segments joined_ already dropped as well
                bad_ = set()
                changed_ = True
                ends_ = set(ahead_)
                for l_, a_, b_ in allfx_:
                    if not joined_(a_, b_):
                        ends_.add(a_); ends_.add(b_)
                while changed_:
                    changed_ = False
                    for i_, (l_, a_, b_) in enumerate(fx_):
                        if i_ in bad_:
                            continue
                        if any(math.dist(a_, e_) < 0.05 or math.dist(b_, e_) < 0.05 for e_ in ends_):
                            bad_.add(i_); ends_.add(a_); ends_.add(b_); changed_ = True
                trunks_ += [(l_, [a_, b_]) for i_, (l_, a_, b_) in enumerate(fx_) if i_ not in bad_]
                # fixed copper is stored segment by segment: its bends are the points two share
                deg_ = collections.Counter()
                for l_, a_, b_, _w in fixed[n]["tracks"]:
                    deg_[(l_, round(a_[0], 2), round(a_[1], 2))] += 1
                    deg_[(l_, round(b_[0], 2), round(b_[1], 2))] += 1
                joints_ = [(x_, y_) for (l_, x_, y_), c_ in deg_.items() if c_ > 1]
                for layer_, pts_ in trunks_:
                        # never AT a bend: a branch there meets two segments at once, and no
                        # heading is 45 degrees to both of a 45-degree bend
                        bends_ = [tuple(q) for q in pts_[1:-1]] + joints_
                        for a_, b_ in zip(pts_, pts_[1:]):
                            m_ = max(1, int(round(math.dist(a_, b_) / PITCH)))
                            for k_ in range(m_ + 1):
                                q_ = G.xy(G.cell(a_[0] + (b_[0] - a_[0]) * k_ / m_, a_[1] + (b_[1] - a_[1]) * k_ / m_))
                                if FO and in_field(*q_):
                                    continue
                                if any(math.dist(q_, e_) < 0.15 for e_ in bends_):
                                    continue
                                cands.append((math.dist(q_, (gp["x"], gp["y"])), q_, layer_, heading(a_, b_)))
                                cands.append((math.dist(q_, (gp["x"], gp["y"])), q_, layer_, heading(b_, a_)))
                cands.sort()
                picked = []
                for d_, q_, l_, h_ in cands:
                    if any(math.dist(q_, r_[0]) < 0.6 and r_[2] == h_ for r_ in picked):
                        continue
                    picked.append((q_, l_, h_))
                    if len(picked) >= 30:
                        break
                px_, py_ = leg_xy(legs[li - 1])
                pk = dict(x=px_, y=py_)
                s, lay, hd = G.xy(G.cell(pk["x"], pk["y"])), leg_layer(legs[li - 1]), 0
                if li == 0:                       # no earlier leg: the fallback is the net's own source
                    s, lay, hd = src[n]
                gc = leg_goals(leg)
                hm = G.hmap(gc)
                for q_, l_, h_ in picked:
                    if G.route(n, q_, l_, gc, leg_layer(leg), set(LAYERS), pres=1.0, hmap=hm, limit=a.limit,
                               start_dir=h_) is not None:
                        s, lay, hd = q_, l_, h_
                        break
                print(f"   {n}: tees off at {s} on {lay} heading {hd}")
            elif leg.get("from") == "via":
                # a tee at one of the net's own vias, nearest the next pad first: a via entry is
                # free under the angle rule, and the pad itself may be walled in by the other nets'
                # first legs.
                # The first candidate from which the goal is reachable wins; the pad is the last.
                gx_, gy_ = leg_xy(leg)
                gp = dict(x=gx_, y=gy_)
                vs = [G.xy(c) for _r, vv in out.get(n, []) for c in vv if not (FO and in_field(*G.xy(c)))]
                vs += [(x, y) for x, y, _r, _s in fixed[n]["vias"] if not (FO and in_field(x, y))]
                vs = sorted(set(vs), key=lambda q: math.dist(q, (gp["x"], gp["y"])))
                px_, py_ = leg_xy(legs[li - 1])
                pk = dict(x=px_, y=py_)
                cands = [(q, "B.Cu" if "B.Cu" in LAYERS else LAYERS[-1]) for q in vs] + \
                        [(G.xy(G.cell(pk["x"], pk["y"])), leg_layer(legs[li - 1]))]
                s, lay = cands[-1]
                gc = leg_goals(leg)
                hm = G.hmap(gc)
                for q, ly in cands:
                    if ly != "F.Cu":
                        G.unblock(["via"], {G.cell(*q)})
                    # a real route (the 45-degree rule included; the other legs' copper priced, not
                    # hard) -- a flood fill ignores turns and passed a start that was then walled in
                    if G.route(n, q, ly, gc, leg_layer(leg), set(LAYERS), pres=1.0, hmap=hm, limit=a.limit,
                               start_dir=0) is not None:
                        s, lay = q, ly
                        break
                hd = 0
            else:
                s, lay, hd = leg_xy(legs[li - 1]), leg_layer(legs[li - 1]), 0
                s = G.xy(G.cell(*s))
            bias = array("f", bytes(4 * G.nx * G.ny))
            for c in own.get(n, ()):
                bias[c] = 3.0
            per = [array("f", bias) for _ in LAYERS]
            # the net's own frozen copper is free to stand on, and a route that runs back along it
            # lays copper on copper and a 180-degree joint where it leaves -- priced, except round
            # the leg's start
            near_ = G.disc(s[0], s[1], 0.5)
            for L_, a_, b_, _w in fixed[n]["tracks"]:
                if L_ not in LAYERS:
                    continue
                arr_ = per[LAYERS.index(L_)]
                m_ = max(1, int(math.dist(a_, b_) / (PITCH / 2)))
                for k_ in range(m_ + 1):
                    c_ = G.cell(a_[0] + (b_[0] - a_[0]) * k_ / m_, a_[1] + (b_[1] - a_[1]) * k_ / m_)
                    if c_ not in near_:
                        arr_[c_] = max(arr_[c_], RETRACE)
            # "avoid" (the net's, every leg) and a leg's own "avoid": rectangles priced out, on every
            # layer or on the one named -- a field that belongs to other nets (a connector's pins,
            # a lane kept for the stubs into a pull block)
            for r_ in S["nets"][n].get("avoid", []) + leg.get("avoid", []):
                lays_ = [LAYERS.index(r_[4])] if len(r_) > 4 else range(len(LAYERS))
                cells_ = G.rect(*r_[:4])
                for li_ in lays_:
                    arr_ = per[li_]
                    for c in cells_:
                        arr_[c] += AVOID
            if pl:
                for c in range(G.nx * G.ny):
                    if plane_bias[n][c]:
                        per[li_plane][c] += plane_bias[n][c]
            jobs.append(dict(net=full[n] if False else n, src=s, src_layer=lay, goals=leg_goals(leg),
                             bias=per, goal_layer=leg_layer(leg),
                             allowed=set(S["nets"][n].get("layers", LAYERS)) | {lay, leg_layer(leg)},
                             start_dir=hd, goal_dirs=leg.get("goal_dirs")))
        if not jobs:
            continue
        print(f"\n--- leg {stage + 1}: {' '.join(j['net'] for j in jobs)}")
        G.last = None                     # a stage that walls on its first pass leaves no "last" of its own
        paths = G.negotiate(jobs, iters=a.iters, pres0=a.pres0, pres_mul=a.pres_mul, hist_inc=a.hist,
                            hist_decay=a.hist_decay, seed=a.seed, layer_cost=S.get("layer_cost"),
                            limit=a.limit)
        if paths is None and getattr(G, "last", None) and a.repair:
            # REPAIR: negotiation left a few overlaps.  Re-route the nets involved one at a time
            # with everything else HARD (pres=None) -- a stall is often one net that simply has
            # another way, which the ever-rising price no longer lets it try.
            last, bad = G.last
            byjob = {jb["net"]: jb for jb in jobs}
            for _round in range(3):
                culprits = [n for n, v in bad.items() if v]
                if not culprits:
                    break
                for n in sorted(culprits, key=lambda k: len(bad[k])):
                    jb = byjob[n]
                    G.unroute(n)
                    res = G.route(n, jb["src"], jb["src_layer"], jb["goals"], jb["goal_layer"], jb["allowed"],
                                  pres=None, hmap=G.hmap(set(jb["goals"])), bias=jb.get("bias"),
                                  start_dir=jb.get("start_dir", 0), limit=a.limit,
                                  layer_cost=S.get("layer_cost"), goal_dirs=jb.get("goal_dirs"))
                    if res is None:
                        G.lay(n, *last[n])            # put its old copper back
                        continue
                    last[n] = res
                    G.lay(n, *res)
                bad = {n: G.conflicts(n, *last[n]) for n in last}
                print(f"   repair round {_round + 1}: {sum(len(v) for v in bad.values())} contested cell(s) left")
            if not any(bad.values()):
                paths = dict(last)
                G.last = (dict(last), bad)
            else:
                G.last = (last, bad)
        if paths is None:
            last, bad = getattr(G, "last", None) or ({}, {})
            for n, v in bad.items():
                if v:
                    print(f"   {n}: {len(v)} contested cell(s): " + " ".join(f"{k}{G.xy(c)}" for k, c in v[:10]))
            for j in jobs:
                r, hit = reach(G, j, LAYERS)
                print(f"   {j['net']:12s} reaches {r} cells; {hit} of {len(j['goals'])} goal cells")
            print("NO SOLUTION at this leg")
            if last or out:                   # frozen legs + the last iteration, for a look (never for a gate)
                part = dict(group=S["group"] + "-partial", why="legs that closed, then the last iteration "
                            "of the one that did not", tracks=[], vias=[], zones=[], closed_legs=stage)
                done = [(n, rv, "closed") for n, legs in out.items() for rv in legs]
                for n, (runs, vias), tag in done + [(n, rv, "open") for n, rv in last.items()]:
                    for layer, cells in runs:
                        pts = [list(q) for q in G.polyline(cells)]
                        if len(pts) > 1:
                            part["tracks"].append(dict(net=full[n], layer=layer, width=W, pts=pts, leg=tag))
                    for c in vias:
                        part["vias"].append(dict(net=full[n], at=list(G.xy(c)), pad=VIA, drill=DRILL, leg=tag))
                pp = Path(S["plan"])
                pp = (pp if pp.is_absolute() else Path(a.board) / pp).with_suffix(".partial.json")
                pp.write_text(json.dumps(part, indent=1) + "\n")
                print(f"wrote {pp} (the last iteration, overlaps and all)")
            return 1
        for n, (runs, vias) in paths.items():
            out.setdefault(n, []).append((runs, vias))
            own.setdefault(n, set())
            for _l, cells in runs:
                own[n] |= set(cells)
        for n, (runs, vias) in paths.items():
            tr = []
            for layer, cells in runs:
                pts = G.polyline(cells)
                tr += [(layer, pts[k], pts[k + 1], W) for k in range(len(pts) - 1)]
            vv = [(*G.xy(c), maze.VIA_R, G.via_sep) for c in vias]
            G.unroute(n)
            G.register(n, "fixed", tracks=fixed[n]["tracks"] + tr, vias=fixed[n]["vias"] + vv)
            fixed[n]["tracks"] += tr
            fixed[n]["vias"] += vv
            for x, y, _r, _s in vv:
                G.block(["via"], G.disc(x, y, 0.8))

    plan = dict(group=S["group"], why=S["why"], tracks=[], vias=[], zones=[], necks=S.get("necks", []))
    for n, legs in out.items():
        for runs, vias in legs:
            for layer, cells in runs:
                pts = straighten([list(q) for q in G.polyline(cells)], W)
                if len(pts) > 1:
                    plan["tracks"].append(dict(net=full[n], layer=layer, width=W, pts=pts))
            for c in vias:
                x, y = G.xy(c)
                if any(math.dist((x, y), (e["at"][0], e["at"][1])) < 0.05 for e in S.get("extra_vias", [])
                       if e["net"].rsplit("/", 1)[-1] == n):
                    continue              # a layer change on its own prelaid dog-bone
                if any(math.dist((x, y), (vx, vy)) < 0.05 for vx, vy, *_ in board_vias.get(n, [])) or \
                        any(math.dist((x, y), tuple(q["at"])) < 0.05 for q in plan["vias"] if q["net"] == full[n]):
                    continue              # ... or on one it already has (a board via, an earlier leg's)
                plan["vias"].append(dict(net=full[n], at=[x, y], pad=VIA, drill=DRILL))
    for extra in S.get("extra_tracks", []):
        plan["tracks"].append(dict(extra, net=full.get(extra["net"], extra["net"])))
    for extra in S.get("extra_vias", []):
        plan["vias"].append(dict(extra, net=full.get(extra["net"], extra["net"])))
    p = Path(S["plan"])
    p = p if p.is_absolute() else Path(a.board) / p
    p.write_text(json.dumps(plan, indent=1) + "\n")
    print(f"\nwrote {p}: {len(plan['tracks'])} track runs, {len(plan['vias'])} vias")
    return 0


def reach(G, job, LAYERS):
    """Flood fill from a job's source ignoring the other routed nets and the turn
    rule: separates 'contested' from 'walled in'."""
    li = {k: i for i, k in enumerate(LAYERS)}
    own = G._own(job["net"])
    start = (G.cell(*job["src"]), li[job["src_layer"]])
    seen, dq, nx, ny = {start}, collections.deque([start]), G.nx, G.ny

    def free(c, key):
        if G.static[key][c]:
            return False
        f = G.fix[key][c]
        return not (f and not (f == 1 and c in own["fixed"][key]))

    while dq:
        c, l = dq.popleft()
        i, j = c % nx, c // nx
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            ni, nj = i + di, j + dj
            if 0 <= ni < nx and 0 <= nj < ny:
                nc = nj * nx + ni
                if free(nc, LAYERS[l]) and (nc, l) not in seen:
                    seen.add((nc, l)); dq.append((nc, l))
        if free(c, "via"):
            for l2 in range(len(LAYERS)):
                if l2 != l and free(c, LAYERS[l2]) and (c, l2) not in seen:
                    seen.add((c, l2)); dq.append((c, l2))
    gl = li[job.get("goal_layer", "F.Cu")]
    return len(seen), sum(1 for c in job["goals"] if (c, gl) in seen)


if __name__ == "__main__":
    sys.exit(main())
