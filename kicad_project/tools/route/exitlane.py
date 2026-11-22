#!/usr/bin/env python3
"""A BGA's top-layer exit lanes: is each outer signal ball still able to leave on its own layer?

    tools/kpy tools/route/exitlane.py --board board1 --ref U1 [--plan FANOUT_PLAN.json] [--side S]
        [--width W --clearance C] [--power REGEX] [--rings 2] [--gate assign|plan|strict] [--all] [--out J]
    tools/kpy tools/route/exitlane.py --board board1        # every entry of routing/prelayout.json "exit_lanes"

THE TRAP.  A ring-1 ball leaves its land straight out on the part's own outer layer, a ring-2 ball between
two ring-1 lands (NXP AN10778; bga_fanout.py's rule).  Until those tracks are drawn, nothing on the board
says the lanes are taken: KiCad's DRC sees no violation (there is no track yet), and an escape bound counted
on the vias (fanout.py layers) counts the lanes as free when another net's via, pad or stub stands on one.
board1 RV60.1 / RV60.5: five capacitor vias on U1's ring-1 lines and a ball's GND stub on U300's A5 line,
with the escape count at 162 of 162.  This tool draws the lanes and measures the copper on them.

THE LANES (per side of the lattice: N = small y, S = large y, W = small x, E = large x):
  ring 1 signal ball: its centre straight out, perpendicular to its edge, to the courtyard line;
  ring 2 signal ball: the gap lines either side of it (+/- pitch/2 along the edge) -- and the line through
       an EMPTY ring-1 site in front of it -- from half way to ring 1 out to the courtyard line.
  A ring-2 ball in a corner square (ring 2 on two sides) takes the lanes of both; a ring-1 corner ball takes
  its via diagonally and has no lane (printed, not judged).  `--side S` (a part that escapes by one edge):
  only that side's lanes, balls not on it are not judged.
A lane is BLOCKED by copper of another net on the exit layer -- a via, a pad of another part, a track, a
net-tied copper polygon -- whose edge comes nearer its centre line than width/2 + clearance.  The part's own
lands are the lattice and are not obstacles.  Pours are not: they yield to the track.

THE GATE (--gate, default assign):
  assign  every ring-1 ball has its straight lane free, and every ring-2 ball a free lane of ITS OWN (two
          balls never share one: an exact bipartite matching).  The item list names what blocks the
          ring-1 lanes and the lanes the unmatched ring-2 balls could have used.
  plan    assign, and the ring-2 lanes the fan-out plan (--plan, bga_fanout.py's output: balls[*].lane)
          assigns must be free.
  strict  every candidate lane of every ring-1/2 signal ball is free (a spare gap included).
Every run prints all three counts; --gate picks the exit code.

THE NUMBERS (none in this file): the exit layer is the part's own side.  Width and clearance are, in order:
  --width / --clearance (or the prelayout entry's "width" / "clearance");
  the board's area class for the part -- board_setup.py's `fab: <CLASS> class inside <REF>` custom rule
     in <board>.kicad_dru, with <CLASS>'s track width and clearance from the .kicad_pro;
  else the lane net's class width (or its smallest custom-rule width on the layer) and the stricter of
     the two nets' class clearances (KiCad's rule).
Ball kinds: from --plan (balls[*].kind == "signal", and exit == "top" where the plan says) when given;
else a ball is a signal unless its net is unconnected, matches --power, is a plane layer's net in the
guide, or (no --power given) has two or more balls on the part.  Rings 1..--rings (default: the plan's
config rings_top, else 2) are judged.
A ball whose own track already leaves it on the exit layer is ROUTED: DRC judges it now, and it is dropped
(its track still blocks the others).  --all judges it too.

THE SPEC, <board>/routing/prelayout.json (each entry names its handoff line):
  "exit_lanes": [{"ref": "U1", "plan": "routing/U1_fanout_plan.json", "gate": "assign", "why": "10_layout #39"},
                 {"ref": "U300", "side": "S", "plan": "...", "gate": "plan", "why": "10_layout #19"}]
  Optional per entry: side, width, clearance, power, rings, gate.  With the key, `route.py check` runs every
  entry whose courtyard a plan's copper comes near, on the board before and with the plan, and fails a ball
  the plan leaves without a lane it had (RULE exitlane:); `route.py audit` prints an `exitlane` line.

Exit 0: PASS; 1: a ball is blocked (under the gate); 2: usage (no such part, not a lattice, a bad spec).
Reads only.
"""
import argparse
import collections
import json
import math
import re
import sys
from pathlib import Path

SPEC = "routing/prelayout.json"
OUTV = {"N": (0, -1), "S": (0, 1), "W": (-1, 0), "E": (1, 0)}
GATES = ("assign", "plan", "strict")


class Usage(Exception):
    pass


def short(n):
    return (n or "").split("/")[-1]


# ----------------------------------------------------------------------------- geometry
def pt_seg(px, py, s):
    x0, y0, x1, y1 = s
    vx, vy = x1 - x0, y1 - y0
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x0) * vx + (py - y0) * vy) / L2))
    return math.hypot(px - x0 - t * vx, py - y0 - t * vy)


def _cross(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def seg_seg(s, q):
    """Exact distance between two segments (0 when they cross)."""
    ax, ay, bx, by = s
    cx, cy, dx, dy = q
    d1, d2 = _cross(ax, ay, bx, by, cx, cy), _cross(ax, ay, bx, by, dx, dy)
    d3, d4 = _cross(cx, cy, dx, dy, ax, ay), _cross(cx, cy, dx, dy, bx, by)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4:
        return 0.0
    return min(pt_seg(cx, cy, s), pt_seg(dx, dy, s), pt_seg(ax, ay, q), pt_seg(bx, by, q))


def inside(px, py, pts):
    c = False
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        if (y0 > py) != (y1 > py) and px < x0 + (py - y0) * (x1 - x0) / (y1 - y0):
            c = not c
    return c


def edge_dist(it, s):
    """Lane centre line to the item's copper edge; negative = the line runs through the copper
    (minus the via's radius / the track's half width beyond the line)."""
    if it["k"] == "via":
        return pt_seg(it["x"], it["y"], s) - it["r"]
    if it["k"] == "track":
        return seg_seg(s, it["seg"]) - it["r"]
    best = math.inf
    for pts in it["polys"]:
        if inside(s[0], s[1], pts) or inside(s[2], s[3], pts):
            return 0.0
        for p0, p1 in zip(pts, pts[1:] + pts[:1]):
            best = min(best, seg_seg(s, (p0[0], p0[1], p1[0], p1[1])))
    return best


# ----------------------------------------------------------------------------- the rules' numbers
def _pro_classes(board_dir):
    pro = next(Path(board_dir).glob("*.kicad_pro"), None)
    j = json.loads(pro.read_text()) if pro else {}
    return {c.get("name"): c for c in j.get("net_settings", {}).get("classes", [])}


def area_class(board_dir, ref):
    """board_setup.py's area class for REF: (class name, width, clearance) or None."""
    dru = next(Path(board_dir).glob("*.kicad_dru"), None)
    if not dru:
        return None
    m = re.search(r'\(rule "fab: (\S+) class inside %s"' % re.escape(ref), dru.read_text())
    if not m:
        return None
    c = _pro_classes(board_dir).get(m.group(1))
    if not c or c.get("track_width") is None or c.get("clearance") is None:
        return None
    return m.group(1), float(c["track_width"]), float(c["clearance"])


class Rules:
    """width(lane net) and clearance(lane net, item net) on the exit layer, with their source."""

    def __init__(self, B, ref, layer, width=None, clearance=None):
        self.B, self.layer = B, layer
        self.w, self.c = width, clearance
        self.area = area_class(B.dir, ref)
        src = []
        if width is not None:
            src.append(f"width {width} (given)")
        elif self.area:
            src.append(f"width {self.area[1]} ({self.area[0]}, the area class inside {ref})")
        else:
            src.append("width: the lane net's class")
        if clearance is not None:
            src.append(f"clearance {clearance} (given)")
        elif self.area:
            src.append(f"clearance {self.area[2]} ({self.area[0]}, rule 'fab: {self.area[0]} class inside {ref}')")
        else:
            src.append("clearance: the stricter of the two nets' classes")
        self.source = "; ".join(src)
        self._cc = {}

    def width(self, net):
        if self.w is not None:
            return self.w
        if self.area:
            return self.area[1]
        ws = list(self.B.rule_widths(net, self.layer)) + [self.B.class_width(net) or 0.1]
        return min(ws)

    def _clr(self, net):
        if net not in self._cc:
            try:
                self._cc[net] = float(self.B.class_clearance(net) or 0.0) if net else 0.0
            except SystemExit:
                self._cc[net] = 0.0
        return self._cc[net]

    def clearance(self, net, other):
        if self.c is not None:
            return self.c
        if self.area:
            return self.area[2]
        return max(self._clr(net), self._clr(other))


def evaluate(lanes, blocked, routed):
    """The gates, from the lanes and the set of blocked (ball, lane key) pairs: ring-1 balls without their
    straight lane (fail1), ring-2 balls without a lane of their own under an exact matching (fail2), and
    each gate's failing balls.  judge() and a caller that asks "what if this copper were added" (sites.py)
    both count through here, so a search and route.py check cannot disagree on what shuts a ball."""
    by = collections.defaultdict(list)
    for L in lanes:
        by[L["ball"]].append(L)
    r1 = sorted({L["ball"] for L in lanes if L["ring"] == 1})
    r2 = sorted({L["ball"] for L in lanes if L["ring"] == 2})
    need1 = [b for b in r1 if b not in routed]
    need2 = [b for b in r2 if b not in routed]
    fail1 = [b for b in need1 if any((b, L["key"]) in blocked for L in by[b] if L["ring"] == 1)]
    opts = collections.defaultdict(list)
    for L in lanes:
        if L["ring"] == 2 and (L["ball"], L["key"]) not in blocked and L["key"] not in opts[L["ball"]]:
            opts[L["ball"]].append(L["key"])
    # a routed ring-2 ball keeps the lane its own track is in; the others match among the rest
    match = {}

    def aug(b, seen):
        for kk in opts[b]:
            if kk in seen:
                continue
            seen.add(kk)
            if kk not in match or aug(match[kk], seen):
                match[kk] = b
                return True
        return False

    fail2 = [b for b in need2 if not aug(b, set())]
    shut = [L for L in lanes if (L["ball"], L["key"]) in blocked and L["ball"] not in routed]
    plan_lane_fail = {L["ball"] for L in shut if L["ring"] == 1 or L["tag"] == "plan"}
    gate_balls = {"assign": sorted(set(fail1) | set(fail2)),
                  "plan": sorted(set(fail1) | set(fail2) | plan_lane_fail),
                  "strict": sorted({L["ball"] for L in shut})}
    return dict(ring1=r1, ring2=r2, fail1=fail1, fail2=fail2, assign={b: kk for kk, b in match.items()},
                opts=opts, gate_balls=gate_balls)


# ----------------------------------------------------------------------------- the judge
def judge(B, ref, plan=None, side=None, width=None, clearance=None, power=None, rings=None,
          gate="assign", all_balls=False):
    """B: a route.RBoard (or anything with .b, .pn, .dir, rule_widths, class_width, class_clearance,
    gates).  plan: a bga_fanout.py plan dict or None.  Returns a result dict (see main for the print)."""
    pn = B.pn
    if gate not in GATES:
        raise Usage(f"--gate takes {', '.join(GATES)}, not {gate!r}")
    if side is not None and side not in OUTV:
        raise Usage(f"side takes N, S, W or E, not {side!r}")
    U = B.b.FindFootprintByReference(ref)
    if U is None:
        raise Usage(f"{ref} is not on the board")
    mm = pn.ToMM
    layer = pn.B_Cu if U.GetLayer() == pn.B_Cu else pn.F_Cu
    lname = B.b.GetLayerName(layer)
    balls = {}
    for p in U.Pads():
        if not p.IsOnLayer(layer):
            continue
        c = p.GetPosition()
        balls[p.GetNumber()] = dict(x=mm(c.x), y=mm(c.y), net=p.GetNetname(), pad=p)
    if len(balls) < 9:
        raise Usage(f"{ref} has {len(balls)} pads on {lname}: not a ball array")
    xs = sorted({round(v["x"], 4) for v in balls.values()})
    ys = sorted({round(v["y"], 4) for v in balls.values()})
    steps = [q - p for p, q in zip(xs, xs[1:])] + [q - p for p, q in zip(ys, ys[1:])]
    P = round(min(steps), 4)
    X0, Y0, X1, Y1 = xs[0], ys[0], xs[-1], ys[-1]
    NX, NY = int(round((X1 - X0) / P)) + 1, int(round((Y1 - Y0) / P)) + 1
    site = {}
    for k, v in balls.items():
        fi, fj = (v["x"] - X0) / P, (v["y"] - Y0) / P
        i, j = int(round(fi)), int(round(fj))
        if abs(fi - i) > 0.02 or abs(fj - j) > 0.02:
            raise Usage(f"{ref} pad {k} at ({v['x']:.3f}, {v['y']:.3f}) is off the {P} mm lattice "
                        f"(a rotated or mixed-pitch part is not handled)")
        v["i"], v["j"] = i, j
        site[(i, j)] = k
    cy = None
    for cl in (pn.F_CrtYd, pn.B_CrtYd):
        poly = U.GetCourtyard(cl)
        if poly.OutlineCount():
            bb = poly.BBox()
            if bb.GetWidth() > 0:
                cy = (mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom()))
                break
    if cy is None:
        raise Usage(f"{ref} has no courtyard: the lanes have no end")
    pballs = (plan or {}).get("balls") or {}
    if rings is None:
        cfg = (plan or {}).get("config")
        rings = int(cfg.get("rings_top", 2)) if isinstance(cfg, dict) else 2
    pw = re.compile(power) if power else None
    planes = {short(q.get("net")) for q in (getattr(B, "gates", None) or {}).get("plane_layers", [])}
    nballs = collections.Counter(v["net"] for v in balls.values())
    kind_rule = ("the plan's kinds" if pballs else
                 (f"signal unless --power {power!r}" if power else "signal unless 2+ balls on the part")
                 + (f" or a plane net ({', '.join(sorted(planes))})" if planes else ""))

    def sides_of(i, j):
        d = {"W": i, "E": NX - 1 - i, "N": j, "S": NY - 1 - j}
        r = min(d.values())
        return r + 1, [s for s, x in d.items() if x == r]

    routed = set()
    if not all_balls:
        for t in B.b.GetTracks():
            if t.Type() == pn.PCB_VIA_T or t.GetLayer() != layer:
                continue
            for e in (t.GetStart(), t.GetEnd()):
                for k, v in balls.items():
                    if v["net"] == t.GetNetname() and v["pad"].HitTest(e):
                        routed.add(k)
    judged, corners, offside = [], [], []
    for k, v in balls.items():
        v["ring"], v["sides"] = sides_of(v["i"], v["j"])
        net = short(v["net"])
        if not net or net.startswith("unconnected"):
            v["kind"] = "none"
        elif k in pballs:
            pk = pballs[k]
            v["kind"] = "signal" if pk.get("kind") == "signal" and pk.get("exit", "top") == "top" else pk.get("kind")
            v["lane"] = pk.get("lane")
            if pk.get("side") in OUTV and v["ring"] > 1:
                v["sides"] = [pk["side"]]
        elif pballs:
            v["kind"] = "none"
        elif (pw and pw.match(net)) or net in planes or (not pw and nballs[v["net"]] > 1):
            v["kind"] = "power"
        else:
            v["kind"] = "signal"
        if v["kind"] != "signal" or v["ring"] > rings:
            continue
        if side:
            if side not in v["sides"]:
                offside.append(k)
                continue
            v["sides"] = [side]
        if v["ring"] == 1 and len(v["sides"]) > 1:
            corners.append(k)
            continue
        judged.append(k)
    rules = Rules(B, ref, lname, width, clearance)

    def edge(s, ext=0.0):
        return {"N": cy[1] - ext, "S": cy[3] + ext, "W": cy[0] - ext, "E": cy[2] + ext}[s]

    lanes = []
    for k in sorted(judged):
        v = balls[k]
        for s in v["sides"]:
            ux, uy = OUTV[s]
            ns = s in ("N", "S")
            along = v["x"] if ns else v["y"]
            if v["ring"] == 1:
                cands = [(0.0, "straight")]
                start = (v["x"], v["y"])
            else:
                pl = None
                if v.get("lane") is not None:
                    pl = (X0 if ns else Y0) + float(v["lane"]) * P
                cands = [(off, "plan" if pl is not None and abs(pl - along - off) < 1e-3 else "gap")
                         for off in (-P / 2, P / 2)]
                front = (v["i"] + ux, v["j"] + uy)
                if front not in site and 0 <= front[0] < NX and 0 <= front[1] < NY:
                    cands.append((0.0, "plan" if pl is not None and abs(pl - along) < 1e-3 else "site"))
                start = (v["x"] + ux * P / 2, v["y"] + uy * P / 2)
            for off, tag in cands:
                if ns:
                    seg = (v["x"] + off, start[1], v["x"] + off, edge(s))
                else:
                    seg = (start[0], v["y"] + off, edge(s), v["y"] + off)
                lanes.append(dict(ball=k, net=v["net"], ring=v["ring"], side=s, tag=tag,
                                  key=(s, round(along + off, 3)), seg=tuple(round(c, 4) for c in seg)))
    # ---- the copper on the exit layer near the courtyard
    box = (cy[0] - 1.0, cy[1] - 1.0, cy[2] + 1.0, cy[3] + 1.0)

    def near(x, y):
        return box[0] <= x <= box[2] and box[1] <= y <= box[3]

    items = []
    for t in B.b.GetTracks():
        if t.Type() == pn.PCB_VIA_T:
            if not t.IsOnLayer(layer):
                continue
            c = t.GetPosition()
            if not near(mm(c.x), mm(c.y)):
                continue
            try:
                w = t.GetWidth(layer)
            except TypeError:
                w = t.GetWidth()
            items.append(dict(k="via", net=t.GetNetname(), x=mm(c.x), y=mm(c.y), r=mm(w) / 2,
                              uuid=t.m_Uuid.AsString()))
        elif t.GetLayer() == layer:
            s, e = t.GetStart(), t.GetEnd()
            seg = (mm(s.x), mm(s.y), mm(e.x), mm(e.y))
            if not (near(seg[0], seg[1]) or near(seg[2], seg[3])):
                continue
            items.append(dict(k="track", net=t.GetNetname(), seg=seg, r=mm(t.GetWidth()) / 2,
                              uuid=t.m_Uuid.AsString()))

    def polys_of(shape):
        out = []
        for oi in range(shape.OutlineCount()):
            o = shape.Outline(oi)
            out.append([(mm(o.CPoint(i).x), mm(o.CPoint(i).y)) for i in range(o.PointCount())])
        return out

    for fp in B.b.GetFootprints():
        if fp.GetReference() == ref:
            continue
        for p in fp.Pads():
            if not p.IsOnLayer(layer):
                continue
            c = p.GetPosition()
            if not near(mm(c.x), mm(c.y)):
                continue
            items.append(dict(k="pad", net=p.GetNetname(), x=mm(c.x), y=mm(c.y),
                              polys=polys_of(p.GetEffectivePolygon(layer, pn.ERROR_INSIDE)),
                              ref=f"{fp.GetReference()}.{p.GetNumber()}"))
    for d in B.b.GetDrawings():
        if d.GetLayer() != layer or not hasattr(d, "GetNetCode") or not d.GetNetCode():
            continue
        sp = pn.SHAPE_POLY_SET()
        d.TransformShapeToPolygon(sp, layer, 0, B.b.GetDesignSettings().m_MaxError, pn.ERROR_INSIDE)
        ps = polys_of(sp)
        if ps and any(near(x, y) for x, y in ps[0]):
            items.append(dict(k="poly", net=d.GetNetname(), x=ps[0][0][0], y=ps[0][0][1], polys=ps,
                              uuid=d.m_Uuid.AsString()))
    # ---- what blocks which lane
    hits = []
    for L in lanes:
        s = L["seg"]
        lb = (min(s[0], s[2]) - 1, min(s[1], s[3]) - 1, max(s[0], s[2]) + 1, max(s[1], s[3]) + 1)
        half = rules.width(L["net"]) / 2
        for it in items:
            if it["net"] == L["net"]:
                continue
            ix, iy = (it["x"], it["y"]) if "x" in it else (it["seg"][0], it["seg"][1])
            if it["k"] == "track":
                q = it["seg"]
                if max(q[0], q[2]) < lb[0] or min(q[0], q[2]) > lb[2] or max(q[1], q[3]) < lb[1] or min(q[1], q[3]) > lb[3]:
                    continue
            elif not (lb[0] - 2 < ix < lb[2] + 2 and lb[1] - 2 < iy < lb[3] + 2):
                continue
            need = half + rules.clearance(L["net"], it["net"])
            d = edge_dist(it, s)
            if d < need - 1e-4:
                hits.append(dict(lane=L, item=it, edge=round(d, 4), need=round(need, 4)))
    blocked = {(h["lane"]["ball"], h["lane"]["key"]) for h in hits}
    drop = sorted(set(judged) & routed)
    ev = evaluate(lanes, blocked, routed)
    r1, r2, fail1, fail2, own, opts = (ev[k] for k in ("ring1", "ring2", "fail1", "fail2", "assign", "opts"))
    # the items that make each gate fail
    by = collections.OrderedDict()

    def add(gname, h):
        it = h["item"]
        key = (it["k"], it.get("uuid") or it.get("ref"))
        e = by.setdefault(key, dict(item=it, lanes=[], gates=set()))
        e["gates"].add(gname)
        if not any(x is h for x in e["lanes"]):
            e["lanes"].append(h)

    for h in hits:
        L = h["lane"]
        if L["ball"] in routed:
            continue
        if L["ring"] == 1 and L["ball"] in fail1:
            add("assign", h)
        if L["ring"] == 2 and L["ball"] in fail2:
            add("assign", h)
        if L["ring"] == 1 or L["tag"] == "plan":
            add("plan", h)
        add("strict", h)
    gate_balls = ev["gate_balls"]
    gate_items = {g: [e for e in by.values() if g in e["gates"]] for g in GATES}
    return dict(ref=ref, layer=lname, pitch=P, lattice=(NX, NY), courtyard=cy, rings=rings, kind_rule=kind_rule,
                rules=rules.source, balls=balls, judged=judged, ring1=r1, ring2=r2, routed=drop, corners=sorted(corners),
                offside=sorted(offside), lanes=lanes, hits=hits, fail1=fail1, fail2=fail2, assign=own, opts=opts,
                gate=gate, gate_balls=gate_balls, gate_items=gate_items, rule=rules, blocked=blocked,
                ok=not gate_balls[gate], keys={("ball", b) for b in gate_balls[gate]})


def blocked_by(r, it):
    """The (ball, lane key) pairs of judge() result R that one more copper item IT would block -- an item
    dict as judge() builds them (k "via" with x, y, r; "track" with seg, r; "pad"/"poly" with x, y, polys),
    on R's exit layer, with its full net name.  The same lanes, bounding boxes, edge distance and
    width/2 + clearance as judge(); pairs already blocked on the board are left out (nothing new is shut)."""
    out = set()
    ix, iy = (it["x"], it["y"]) if "x" in it else (it["seg"][0], it["seg"][1])
    for L in r["lanes"]:
        k = (L["ball"], L["key"])
        if k in r["blocked"] or it["net"] == L["net"] or L["ball"] in r["routed"]:
            continue
        s = L["seg"]
        lb = (min(s[0], s[2]) - 1, min(s[1], s[3]) - 1, max(s[0], s[2]) + 1, max(s[1], s[3]) + 1)
        if it["k"] == "track":
            q = it["seg"]
            if max(q[0], q[2]) < lb[0] or min(q[0], q[2]) > lb[2] or max(q[1], q[3]) < lb[1] or min(q[1], q[3]) > lb[3]:
                continue
        elif not (lb[0] - 2 < ix < lb[2] + 2 and lb[1] - 2 < iy < lb[3] + 2):
            continue
        need = r["rule"].width(L["net"]) / 2 + r["rule"].clearance(L["net"], it["net"])
        if edge_dist(it, s) < need - 1e-4:
            out.add(k)
    return out


def shuts(r, extra):
    """The balls that fail R's gate with the (ball, lane key) pairs EXTRA blocked as well, and did not
    before: what one more piece of copper costs, counted as route.py check counts it."""
    if not extra:
        return []
    g = evaluate(r["lanes"], r["blocked"] | set(extra), set(r["routed"]))["gate_balls"][r["gate"]]
    return sorted(set(g) - set(r["gate_balls"][r["gate"]]))


def item_text(it):
    at = (f"({it['x']:.3f}, {it['y']:.3f})" if it["k"] != "track" else
          "(%.3f, %.3f)-(%.3f, %.3f)" % it["seg"])
    return f"{it['k']} {short(it['net']) or '<no net>'} {at}" + (f" {it['ref']}" if it.get("ref") else "")


def lane_text(h):
    L = h["lane"]
    return (f"{L['ball']} {short(L['net'])} ring-{L['ring']} {L['side']} {L['tag']} "
            f"edge {h['edge']:.3f} < {h['need']:.3f}")


def report(r, layer_note=True, n=None):
    """The printed lines of one judge() result, the last one the verdict."""
    out = []
    nx, ny = r["lattice"]
    out.append(f"{r['ref']}: {len(r['balls'])} balls, lattice {nx}x{ny}, pitch {r['pitch']:.3f}, exit layer {r['layer']}, "
               f"courtyard ({', '.join(f'{c:.3f}' for c in r['courtyard'])}); rings 1-{r['rings']}, kinds: {r['kind_rule']}")
    out.append(f"   judged: ring-1 {len(r['ring1'])}, ring-2 {len(r['ring2'])} signal balls, {len(r['lanes'])} lanes; "
               f"routed (dropped) {len(r['routed'])}; ring-1 corners (diagonal, not judged) {len(r['corners'])}"
               + (f"; off the side, not judged {len(r['offside'])}" if r["offside"] else ""))
    out.append(f"   width / clearance: {r['rules']}")
    out.append(f"   assign: ring-1 without its straight lane {len(r['fail1'])} {r['fail1'][:12]}; "
               f"ring-2 without a lane of its own {len(r['fail2'])} {r['fail2'][:12]}")
    for g in GATES:
        out.append(f"   gate {g:6s}: {len(r['gate_balls'][g])} ball(s), {len(r['gate_items'][g])} item(s)")
    for e in r["gate_items"][r["gate"]][:n]:
        it = e["item"]
        ls = [h for h in e["lanes"]]
        out.append(f"  BLOCK {item_text(it)} on {r['layer']}" + (f" [{it['uuid']}]" if it.get("uuid") else "")
                   + ": " + "; ".join(lane_text(h) for h in ls[:6]) + (" ..." if len(ls) > 6 else ""))
    gb = r["gate_balls"][r["gate"]]
    out.append(f"EXITLANE {r['ref']} {'PASS' if r['ok'] else 'FAIL'} (--gate {r['gate']}): "
               f"{len(r['gate_items'][r['gate']])} item(s) block {len(gb)} ball(s)" + (f": {', '.join(gb[:12])}" if gb else ""))
    return out


def load_spec(board_dir):
    """routing/prelayout.json "exit_lanes": a list of entries, or None when the key is absent."""
    p = Path(board_dir) / SPEC
    if not p.exists():
        return None
    e = json.loads(p.read_text()).get("exit_lanes")
    if e is None:
        return None
    if not isinstance(e, list) or not all(isinstance(x, dict) and x.get("ref") for x in e):
        raise Usage(f"{SPEC}: \"exit_lanes\" takes [{{\"ref\": \"U1\", \"plan\"?, \"side\"?, \"gate\"?, \"why\"}}]")
    for x in e:
        bad = set(x) - {"ref", "plan", "side", "width", "clearance", "power", "rings", "gate", "why"}
        if bad:
            raise Usage(f"{SPEC}: exit_lanes {x['ref']}: unknown key(s) {sorted(bad)}")
    return e


def load_plan(board_dir, path):
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = Path(board_dir) / p
    if not p.exists():
        raise Usage(f"no such fan-out plan: {p}")
    d = json.loads(p.read_text())
    if not isinstance(d.get("balls"), dict):
        raise Usage(f"{p}: not a fan-out plan (bga_fanout.py's output has \"balls\")")
    return d


def judge_entry(B, e, all_balls=False):
    return judge(B, e["ref"], plan=load_plan(B.dir, e.get("plan")), side=e.get("side"),
                 width=e.get("width"), clearance=e.get("clearance"), power=e.get("power"),
                 rings=e.get("rings"), gate=e.get("gate", "assign"), all_balls=all_balls)


def courtyard_box(B, ref, margin=1.0):
    U = B.b.FindFootprintByReference(ref)
    if U is None:
        return None
    for cl in (B.pn.F_CrtYd, B.pn.B_CrtYd):
        q = U.GetCourtyard(cl)
        if q.OutlineCount():
            bb = q.BBox()
            m = B.pn.ToMM
            return (m(bb.GetX()) - margin, m(bb.GetY()) - margin, m(bb.GetRight()) + margin, m(bb.GetBottom()) + margin)
    return None


def plan_touches(B, ref, plan):
    """Does a route plan add or remove copper near REF's courtyard (within 1 mm)?"""
    bx = courtyard_box(B, ref)
    if bx is None:
        return False
    pts = []
    for t in plan.get("tracks", []) + plan.get("polys", []) + plan.get("rips", []):
        pts += t.get("pts", [])
    for v in plan.get("vias", []) + plan.get("ripvias", []):
        pts.append(v["at"])
    return any(bx[0] <= x <= bx[2] and bx[1] <= y <= bx[3] for x, y in pts)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", help="boardN, the folder name or a path (route.py's rule)")
    ap.add_argument("--ref", help="the BGA; without it, every entry of routing/prelayout.json \"exit_lanes\"")
    ap.add_argument("--plan", help="a bga_fanout.py plan (kinds, ring-2 lanes, rings_top)")
    ap.add_argument("--side", choices=sorted(OUTV), help="every ball leaves by this side only")
    ap.add_argument("--width", type=float, help="the escape track's width (default: the board's rules)")
    ap.add_argument("--clearance", type=float, help="the escape track's clearance (default: the board's rules)")
    ap.add_argument("--power", help="regex: nets that are not signals (without --plan)")
    ap.add_argument("--rings", type=int, help="rings judged (default: the plan's rings_top, else 2)")
    ap.add_argument("--gate", choices=GATES, help="which count sets the exit code (default assign)")
    ap.add_argument("--all", action="store_true", help="judge routed balls too")
    ap.add_argument("--out", help="write the result (items, lanes, assignment) as JSON")
    ap.add_argument("--max", type=int, default=None, help="print at most this many BLOCK lines a part")
    a = ap.parse_args()
    sys.path[:0] = [str(Path(__file__).resolve().parent)]
    import route as R
    a.board = R.P.board_dir_arg(a.board)
    try:
        B = R.RBoard(a.board)
        if a.ref:
            entries = [dict(ref=a.ref, plan=a.plan, side=a.side, width=a.width, clearance=a.clearance,
                            power=a.power, rings=a.rings, gate=a.gate or "assign")]
        else:
            entries = load_spec(B.dir)
            if not entries:
                raise Usage(f"no --ref, and {SPEC} has no \"exit_lanes\"")
            for e in entries:
                if a.gate:
                    e["gate"] = a.gate
        results, ok = [], True
        for e in entries:
            r = judge_entry(B, e, a.all)
            print("\n".join(report(r, n=a.max)))
            if e.get("why"):
                print(f"   ({e['why']})")
            ok &= r["ok"]
            results.append(r)
    except Usage as ex:
        print(f"exitlane: {ex}", file=sys.stderr)
        return 2
    if a.out:
        def js(r):
            return dict(ref=r["ref"], layer=r["layer"], gate=r["gate"], ok=r["ok"], rules=r["rules"],
                        fail1=r["fail1"], fail2=r["fail2"], gate_balls=r["gate_balls"],
                        assign={b: list(k) for b, k in r["assign"].items()},
                        items={g: [dict(item={k: v for k, v in e["item"].items() if k != "polys"},
                                        lanes=[dict(ball=h["lane"]["ball"], net=short(h["lane"]["net"]),
                                                    ring=h["lane"]["ring"], side=h["lane"]["side"],
                                                    tag=h["lane"]["tag"], seg=h["lane"]["seg"],
                                                    edge=h["edge"], need=h["need"]) for h in e["lanes"]])
                                   for e in r["gate_items"][g]] for g in GATES},
                        lanes=[dict(L, key=list(L["key"])) for L in r["lanes"]])
        Path(a.out).write_text(json.dumps([js(r) for r in results], indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
