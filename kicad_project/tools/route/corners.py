#!/usr/bin/env python3
"""The angle rule for polylines: every joint 0 or 45 degrees (gates.json "angles").

    from corners import fix45, joints, straighten

`fix45(pts, width)` rewrites the joints of a polyline whose segments all run at
multiples of 45 degrees: a 90-degree joint becomes two 45-degree joints with a
diagonal between them, a 135-degree one becomes three with two segments between,
and each inserted segment is `cut` long -- at least one track width, which is
what the angle rule asks of the diagonal of a right-angle turn.  A segment at any OTHER
angle cannot be repaired locally (the polyline has to be re-drawn) and raises.

`free` lists points where a joint needs no repair: vias and pads ("entries into
a pad or a via are free; the pad edge is not a joint").  The first and last
points of a polyline are never joints of that polyline.

`straighten(pts, width)` turns a grid router's one-cell stairs back into the
diagonals they approximate (netgroup.py runs every route through it).

Generate at 45 degrees; do not chamfer afterwards.  A pass that cuts the
corners of a router's output also "cuts" the router's own 45-degree joints,
into 22.5-degree ones.  maze.py turns by 45 degrees at most (`only45`), and
these helpers only touch polylines drawn on 45-degree headings.
"""

import math

TOL = 0.5          # degrees


def _unit(a, b):
    d = math.dist(a, b)
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def _turn(u, v):
    """signed turn from heading u to heading v, degrees, (-180, 180]"""
    return math.degrees(math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]))


def _rot(u, deg):
    r = math.radians(deg)
    return (u[0] * math.cos(r) - u[1] * math.sin(r), u[0] * math.sin(r) + u[1] * math.cos(r))


def joints(pts, free=()):
    """[(point, |turn|)] for every interior vertex that is not free."""
    out = []
    for k in range(1, len(pts) - 1):
        if any(math.dist(pts[k], f) < 1e-6 for f in free):
            continue
        out.append((tuple(pts[k]), abs(_turn(_unit(pts[k - 1], pts[k]), _unit(pts[k], pts[k + 1])))))
    return out


def fix45(pts, width, cut=None, free=(), nd=6):
    cut = max(width, cut or 0.0)
    pts = [tuple(p) for p in pts]
    out = [pts[0]]
    for k in range(1, len(pts) - 1):
        a, v, c = out[-1], pts[k], pts[k + 1]
        if any(math.dist(v, f) < 1e-6 for f in free) or math.dist(a, v) < 1e-9:
            out.append(v)
            continue
        u_in, u_out = _unit(a, v), _unit(v, c)
        t = _turn(u_in, u_out)
        if abs(t) < TOL or abs(abs(t) - 45.0) < TOL:
            out.append(v)
            continue
        steps = abs(t) / 45.0
        if abs(steps - round(steps)) > TOL / 45.0 or round(steps) > 3:
            raise ValueError(f"joint at {v}: a {abs(t):.1f}-degree turn between segments that are not on "
                             f"45-degree headings -- re-draw it, it cannot be chamfered")
        n = int(round(steps)) - 1                      # segments to insert
        sgn = 1.0 if t > 0 else -1.0
        mids = [_rot(u_in, sgn * 45.0 * (i + 1)) for i in range(n)]
        sx, sy = sum(m[0] for m in mids) * cut, sum(m[1] for m in mids) * cut
        # back off p along u_in and q along u_out so that  p*u_in + q*u_out = cut * sum(mids)
        det = u_in[0] * u_out[1] - u_in[1] * u_out[0]
        p = (sx * u_out[1] - sy * u_out[0]) / det
        q = (u_in[0] * sy - u_in[1] * sx) / det
        if p > math.dist(a, v) + 1e-9 or q > math.dist(v, c) / (1.0 if k == len(pts) - 2 else 2.0) + 1e-9:
            raise ValueError(f"joint at {v}: no room for a {cut} mm cut (legs {math.dist(a, v):.3f} / "
                             f"{math.dist(v, c):.3f} mm)")
        cur = (v[0] - p * u_in[0], v[1] - p * u_in[1])
        if math.dist(cur, out[-1]) > 1e-9:
            out.append((round(cur[0], nd), round(cur[1], nd)))
        for m in mids:
            cur = (cur[0] + cut * m[0], cur[1] + cut * m[1])
            out.append((round(cur[0], nd), round(cur[1], nd)))
    out.append(pts[-1])
    return [list(p) for p in out]


def straighten(pts, w):
    """Turn a grid router's one-cell stairs back into the diagonals they approximate.

    On a fine grid a 45-degree run is sometimes reached by a stair whose treads
    are one cell -- and the angle rule wants the leg between two turns of the
    same sense at least a track width long.  A vertex (or a tread and its riser)
    is dropped only when the result breaks no more of the rule than before, is
    still on the 0/45 lattice, and leaves the FIRST and LAST headings untouched:
    those meet copper already on the board, and the joints there were gated on
    them.  ⚠ Without that last condition it once "simplified" an exit into a
    135-degree doubling-back.  `route.py check` re-measures every clearance
    afterwards, so a corner this cuts is caught there and not assumed away.
    """
    def head(a, b):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        return (round((b[0] - a[0]) / d, 4), round((b[1] - a[1]) / d, 4)) if d else None

    def turn(u, v):
        return round(math.degrees(math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])), 1)

    def bad(q):
        """Violations in this polyline: a joint over 45 degrees, a leg between two
        same-sense turns under a track width, a segment off the lattice."""
        n = 0
        hs = [head(q[i], q[i + 1]) for i in range(len(q) - 1)]
        for h in hs:
            if h and abs(abs(h[0]) - abs(h[1])) > 1e-3 and abs(h[0]) > 1e-3 and abs(h[1]) > 1e-3:
                n += 1                                   # not on the 0/45 lattice
        ts = [turn(hs[i], hs[i + 1]) for i in range(len(hs) - 1)]
        for t in ts:
            if abs(t) > 45.5:
                n += 1
        for i in range(1, len(hs) - 1):
            if ts[i - 1] * ts[i] > 0 and math.hypot(q[i + 1][0] - q[i][0], q[i + 1][1] - q[i][1]) < w - 1e-9:
                n += 1
        return n

    pts = [tuple(q) for q in pts]
    if len(pts) < 3:
        return [list(q) for q in pts]
    first, last, best = head(pts[0], pts[1]), head(pts[-2], pts[-1]), bad(pts)
    again = True
    while again and len(pts) > 2 and best:
        again = False
        for k in (1, 2):                      # one tread, or a tread and its riser
            for i in range(1, len(pts) - k):
                trial = pts[:i] + pts[i + k:]
                if len(trial) < 2:
                    continue
                if head(trial[0], trial[1]) != first or head(trial[-2], trial[-1]) != last:
                    continue
                b = bad(trial)
                if b < best:
                    pts, best, again = trial, b, True
                    break
            if again:
                break
    return [list(q) for q in pts]
