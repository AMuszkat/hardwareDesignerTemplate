"""Rules a review found broken once, as checks a tool runs before the next review does.

A finding that reads "this cap moved away from its ball again" is not a judgement: it is a
distance, and it came back in five consecutive reviews of one board because nothing but a
reviewer measured it.  These are the rules of that kind, as rows of the routing guide
(`guide.py`; the handoff publishes them in `rules.json`) and as the `check` a review finding
carries (`handoff/findings.json`, `route.py audit --findings`):

  | cap reach     | U1: 1.27 mm (C12, C13)        | every pad of each listed part lies within the
                                                     distance of a same-net pad of U1 or a same-net
                                                     via (centre to centre) -- decoupling at its pin |
  | layers apart  | In4.Cu / In5.Cu: 0 mm         | tracks of two nets, one on each layer, do not lie
                                                     over each other for more than this per net pair;
                                                     a square crossing is free |
  | stitch via    | Pairs: GND within 1.27 mm     | every via of a net in the class has a via of the
                                                     named net within the distance |
  | neck length   | 4 mm   or   Fast: 4 mm        | a run of track narrower than the net's width (its
                                                     class's, or the narrowest its custom rules give
                                                     on that layer) is no longer than this |
  | distance      | C12.1 to U1.A5 <= 1.27 mm     | two named objects, `REF.PAD` or `REF` (its nearest
                                                     pad), centre to centre; `<=` or `>=` |

Everything here is plain geometry on lists (`geometry(B)` reads them from the board once), so
`tests/test_findings.py` drives it without KiCad.  An inner-layer pair rule on padded vias is
`fanout.py layers`' `pad_gap`; copper under the minimum is KiCad's DRC -- neither is repeated.
"""
import math
import re
from collections import defaultdict

RULES = ("cap reach", "layers apart", "stitch via", "neck length", "distance")
KEYS = {"cap reach": "cap_reach", "layers apart": "layers_apart", "stitch via": "stitch",
        "neck length": "neck", "distance": "distance"}
EPS = 1e-6


class RuleError(ValueError):
    pass


def parse(rule, val, s, G):
    """One guide row into G (guide._parse calls this for the rules above)."""
    mmnum = r"(\d+(?:\.\d+)?)\s*mm"
    if rule == "cap reach":
        m = re.fullmatch(r"\s*([A-Za-z]+\d+)\s*:\s*" + mmnum + r"\s*\(([^)]+)\)\s*", val)
        caps = [x.strip() for x in m.group(3).split(",") if x.strip()] if m else []
        if not m or not caps or not all(re.fullmatch(r"[A-Za-z]+\d+", c) for c in caps):
            raise RuleError("'cap reach' takes 'U1: 1.27 mm (C12, C13)' -- the parts are named: a part "
                            "that leaves a courtyard must not leave the rule")
        G.setdefault("cap_reach", []).append(dict(s, ref=m.group(1), max=float(m.group(2)), caps=caps))
    elif rule == "layers apart":
        m = re.fullmatch(r"\s*([A-Za-z0-9_.]+)\s*/\s*([A-Za-z0-9_.]+)\s*:\s*" + mmnum + r"\s*", val)
        if not m or m.group(1) == m.group(2):
            raise RuleError("'layers apart' takes 'In4.Cu / In5.Cu: 0 mm' (the shared length a net pair may have)")
        G.setdefault("layers_apart", []).append(dict(s, a=m.group(1), b=m.group(2), max=float(m.group(3))))
    elif rule == "stitch via":
        m = re.fullmatch(r"\s*([A-Za-z0-9_]+)\s*:\s*(\S+)\s+within\s+" + mmnum + r"\s*", val)
        if not m:
            raise RuleError("'stitch via' takes 'Pairs: GND within 1.27 mm'")
        G.setdefault("stitch", []).append(dict(s, **{"class": m.group(1)}, net=m.group(2), within=float(m.group(3))))
    elif rule == "neck length":
        m = re.fullmatch(r"\s*(?:([A-Za-z0-9_]+)\s*:\s*)?" + mmnum + r"\s*", val)
        if not m:
            raise RuleError("'neck length' takes '4 mm' or 'Fast: 4 mm'")
        G.setdefault("neck", []).append(dict(s, **{"class": m.group(1)}, max=float(m.group(2))))
    elif rule == "distance":
        obj = r"([A-Za-z]+\d+(?:\.[A-Za-z0-9]+)?)"
        m = re.fullmatch(r"\s*" + obj + r"\s+to\s+" + obj + r"\s*(<=|>=)\s*" + mmnum + r"\s*", val)
        if not m:
            raise RuleError("'distance' takes 'C12.1 to U1.A5 <= 1.27 mm' (REF.PAD or REF; <= or >=)")
        G.setdefault("distance", []).append(dict(s, a=m.group(1), b=m.group(2), op=m.group(3), mm=float(m.group(4))))
    else:
        raise RuleError(f"not a check rule: {rule!r}")


def leaf(net):
    return net.rsplit("/", 1)[-1]


# -- the board, as lists ---------------------------------------------------------------------

def geometry(B):
    """{pads: [(ref, pad, net, x, y)], vias: [(net, x, y)], tracks: [(net, layer, a, b, w)]} in mm;
    layer is KiCad's canonical name.  An arc is its chord."""
    pn, mm = B.pn, B.pn.ToMM
    pads, vias, tracks = [], [], []
    for f in B.b.GetFootprints():
        ref = f.GetReference()
        for p in f.Pads():
            if p.GetNumber() and p.IsOnCopperLayer():
                pads.append((ref, str(p.GetNumber()), p.GetNetname(), mm(p.GetPosition().x), mm(p.GetPosition().y)))
    for t in B.b.GetTracks():
        if t.Type() == pn.PCB_VIA_T:
            vias.append((t.GetNetname(), mm(t.GetPosition().x), mm(t.GetPosition().y)))
        else:
            tracks.append((t.GetNetname(), pn.LayerName(t.GetLayer()) if hasattr(pn, "LayerName") else B.b.GetLayerName(t.GetLayer()),
                           (mm(t.GetStart().x), mm(t.GetStart().y)), (mm(t.GetEnd().x), mm(t.GetEnd().y)),
                           mm(t.GetWidth())))
    return {"pads": pads, "vias": vias, "tracks": tracks}


# -- the rules, each -> [site line] ----------------------------------------------------------

def cap_reach(geo, rule):
    ref_pads = defaultdict(list)
    for r, _n, net, x, y in geo["pads"]:
        if r == rule["ref"]:
            ref_pads[net].append((x, y, "pad"))
    vias = defaultdict(list)
    for net, x, y in geo["vias"]:
        vias[net].append((x, y, "via"))
    if not ref_pads:
        return [f"{rule['ref']} is not on the board"]
    out = []
    for c in rule["caps"]:
        mine = [p for p in geo["pads"] if p[0] == c and p[2]]
        if not mine:
            out.append(f"{c} is not on the board (or has no net): the rule names it")
            continue
        for _r, num, net, x, y in mine:
            to = ref_pads.get(net, []) + vias.get(net, [])
            if not to:
                out.append(f"{c}.{num} ({leaf(net)}): no {leaf(net)} pad of {rule['ref']} and no {leaf(net)} via to reach")
                continue
            d, kind = min((math.hypot(x - tx, y - ty), k) for tx, ty, k in to)
            if d > rule["max"] + EPS:
                out.append(f"{c}.{num} ({leaf(net)}) at ({x:.3f}, {y:.3f}): {d:.3f} mm from the nearest {leaf(net)} "
                           f"{kind}, over {rule['max']} mm")
    return out


def seg_dist(p, q, r, s):
    """Minimum distance between segments pq and rs; 0 where they cross."""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1, d2, d3, d4 = cross(r, s, p), cross(r, s, q), cross(p, q, r), cross(p, q, s)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return 0.0

    def pt(a, b, c):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        t = max(0.0, min(1.0, ((c[0] - a[0]) * dx + (c[1] - a[1]) * dy) / L2)) if L2 else 0.0
        return math.hypot(a[0] + t * dx - c[0], a[1] + t * dy - c[1])
    return min(pt(p, q, r), pt(p, q, s), pt(r, s, p), pt(r, s, q))


def shared_len(a, wa, b, wb, step=0.005):
    """Length of segment a whose centreline lies within (wa + wb) / 2 of segment b: the copper
    the two share in projection."""
    L = math.dist(a[0], a[1])
    n = max(2, int(L / step))
    lim, hit = (wa + wb) / 2 - 1e-9, 0
    for k in range(n + 1):
        t = k / n
        p = (a[0][0] + (a[1][0] - a[0][0]) * t, a[0][1] + (a[1][1] - a[0][1]) * t)
        if seg_dist(p, p, b[0], b[1]) < lim:
            hit += 1
    return hit / (n + 1) * L


def layers_apart(geo, rule, cell=1.0):
    A = [(n, a, b, w) for n, lay, a, b, w in geo["tracks"] if lay == rule["a"] and math.dist(a, b) > 1e-9]
    Bt = [(n, a, b, w) for n, lay, a, b, w in geo["tracks"] if lay == rule["b"] and math.dist(a, b) > 1e-9]

    def cells(a, b, w):
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        return [(i, j) for i in range(int((x0 - w) // cell), int((x1 + w) // cell) + 1)
                for j in range(int((y0 - w) // cell), int((y1 + w) // cell) + 1)]
    grid = defaultdict(list)
    for k, (_n, a, b, w) in enumerate(Bt):
        for c in cells(a, b, w):
            grid[c].append(k)
    per, at = defaultdict(float), {}
    for na, a0, a1, wa in A:
        seen = set()
        for c in cells(a0, a1, wa):
            for k in grid.get(c, ()):
                if k in seen:
                    continue
                seen.add(k)
                nb, b0, b1, wb = Bt[k]
                if na == nb or seg_dist(a0, a1, b0, b1) >= (wa + wb) / 2 - 1e-9:
                    continue
                ang = abs(math.degrees(math.atan2(a1[1] - a0[1], a1[0] - a0[0])
                                       - math.atan2(b1[1] - b0[1], b1[0] - b0[0]))) % 180
                ang = min(ang, 180 - ang)
                ln = shared_len((a0, a1), wa, (b0, b1), wb)
                if abs(ang - 90) <= 1.0 and ln <= wa + wb + 0.01:
                    continue                                   # a square crossing
                per[(na, nb)] += ln
                at.setdefault((na, nb), ((a0[0] + a1[0]) / 2, (a0[1] + a1[1]) / 2))
    return [f"{leaf(na)} ({rule['a']}) over {leaf(nb)} ({rule['b']}) near ({at[na, nb][0]:.3f}, {at[na, nb][1]:.3f}): "
            f"{v:.3f} mm shared, over {rule['max']} mm"
            for (na, nb), v in sorted(per.items()) if v > rule["max"] + EPS]


def stitch(geo, rule, classes_of):
    gnd = [(x, y) for net, x, y in geo["vias"] if leaf(net) == leaf(rule["net"])]
    out = []
    for net, x, y in geo["vias"]:
        if rule["class"] not in classes_of(net) or leaf(net) == leaf(rule["net"]):
            continue
        d = min((math.hypot(x - gx, y - gy) for gx, gy in gnd), default=None)
        if d is None or d > rule["within"] + EPS:
            out.append(f"{leaf(net)} via at ({x:.3f}, {y:.3f}): " + ("no " + leaf(rule["net"]) + " via on the board" if d is None
                       else f"nearest {leaf(rule['net'])} via {d:.3f} mm, over {rule['within']} mm"))
    return out


def neck(geo, rule, classes_of, full_width):
    """`full_width(net, layer)` -> the width the net should have there, or None (not judged)."""
    runs = defaultdict(list)
    for net, lay, a, b, w in geo["tracks"]:
        if rule.get("class") and rule["class"] not in classes_of(net):
            continue
        W = full_width(net, lay)
        if W and w < W - EPS:
            runs[(net, lay)].append((a, b, w, W))
    out = []
    for (net, lay), segs in sorted(runs.items()):
        parent = {}

        def find(k):
            while parent.setdefault(k, k) != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k
        key = lambda p: (round(p[0], 3), round(p[1], 3))
        for a, b, _w, _W in segs:
            parent[find(key(a))] = find(key(b))
        chains = defaultdict(list)
        for s_ in segs:
            chains[find(key(s_[0]))].append(s_)
        for ch in chains.values():
            L = sum(math.dist(a, b) for a, b, _w, _W in ch)
            if L > rule["max"] + EPS:
                a, _b, w, W = ch[0]
                out.append(f"{leaf(net)} on {lay} from ({a[0]:.3f}, {a[1]:.3f}): {L:.3f} mm at {w:g} mm "
                           f"(its width is {W:g}), over {rule['max']} mm")
    return out


def distance(geo, rule):
    def pts(name):
        ref, _, pad = name.partition(".")
        return [(x, y) for r, n, _net, x, y in geo["pads"] if r == ref and (not pad or n == pad)]
    A, Bp = pts(rule["a"]), pts(rule["b"])
    for name, got in ((rule["a"], A), (rule["b"], Bp)):
        if not got:
            return [f"{name} is not on the board: the check names it"]
    d = min(math.hypot(ax - bx, ay - by) for ax, ay in A for bx, by in Bp)
    ok = d <= rule["mm"] + EPS if rule["op"] == "<=" else d >= rule["mm"] - EPS
    return [] if ok else [f"{rule['a']} to {rule['b']}: {d:.3f} mm, not {rule['op']} {rule['mm']} mm"]


# -- run ------------------------------------------------------------------------------------

def run(G, geo, classes_of=None, full_width=None):
    """Every check rule in G -> [(gate name, key, rule, sites)], in RULES order."""
    classes_of = classes_of or (lambda net: [])
    full_width = full_width or (lambda net, lay: None)
    out = []
    for name in RULES:
        key = KEYS[name]
        for rule in G.get(key, []):
            sites = {"cap_reach": lambda: cap_reach(geo, rule),
                     "layers_apart": lambda: layers_apart(geo, rule),
                     "stitch": lambda: stitch(geo, rule, classes_of),
                     "neck": lambda: neck(geo, rule, classes_of, full_width),
                     "distance": lambda: distance(geo, rule)}[key]()
            out.append((name, key, rule, sites))
    return out


def label(key, rule):
    """The rule in a few words, for a gate line."""
    if key == "cap_reach":
        return f"{rule['ref']}: {len(rule['caps'])} part(s) within {rule['max']} mm"
    if key == "layers_apart":
        return f"{rule['a']} / {rule['b']}, {rule['max']} mm a net pair"
    if key == "stitch":
        return f"{rule['class']}: {rule['net']} within {rule['within']} mm"
    if key == "neck":
        return (f"{rule['class']}: " if rule.get("class") else "") + f"{rule['max']} mm"
    return f"{rule['a']} to {rule['b']} {rule['op']} {rule['mm']} mm"


def for_board(B):
    """(geometry, classes_of, full_width) of an RBoard."""
    def classes_of(net):
        try:
            return B.netclasses(net)
        except SystemExit:
            return []

    def full_width(net, lay):
        ws = set(B.rule_widths(net, lay))
        w = B.class_width(net)
        if w:
            ws.add(round(float(w), 4))
        return min(ws) if ws else None
    return geometry(B), classes_of, full_width
