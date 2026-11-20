#!/usr/bin/env python3
"""The angle rule for polylines: every joint 0 or 45 degrees (the guide's `joints` row).

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

`short_turns(pts, width)` lists the pieces `route.py check` fails as "the
diagonal of a right-angle turn under one track width" (a piece under the width
between two turns of one sense); `square_turns(pts, width)` re-draws each such
run -- the short pieces dropped, the legs either side met where their lines
cross, and that joint re-chamfered by `fix45`'s rule with every inserted piece
one width long -- growing the run by one piece at a time while the remnant of
a leg would itself be short (a widening that leaves a 0.010 mm flat before the
next turn of the same sense: board1 R1 l1, DRAM_NCS1_A).

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


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])


def _clean(pts):
    """repeated points dropped, and the middle of a straight run (a collinear vertex is not a joint)"""
    q = []
    for x in pts:
        if not q or math.dist(x, q[-1]) > 1e-6:
            q.append(tuple(x))
    k = 1
    while k < len(q) - 1:
        if abs(_turn(_unit(q[k - 1], q[k]), _unit(q[k], q[k + 1]))) < TOL:
            del q[k]
        else:
            k += 1
    return q


def _short_idx(P, w, tol):
    out = []
    for k in range(1, len(P) - 2):
        A, B, C, D = P[k - 1], P[k], P[k + 1], P[k + 2]
        if math.dist(B, C) < w - tol and _cross(A, B, C) * _cross(B, C, D) > 0:
            out.append(k)
    return out


def short_turns(pts, w, tol=1e-3):
    """[(B, C, length)]: the straight runs (collinear pieces taken as one) under the width w, less tol --
    route.py check's slack -- whose two joints turn the same way: a right angle whose diagonal (or flat) is too
    short for the angle rule"""
    P = _clean(pts)
    return [(P[k], P[k + 1], math.dist(P[k], P[k + 1])) for k in _short_idx(P, w, tol)]


def _over45(pts):
    n = 0
    for k in range(1, len(pts) - 1):
        if math.dist(pts[k - 1], pts[k]) > 1e-9 and math.dist(pts[k], pts[k + 1]) > 1e-9 and \
                abs(_turn(_unit(pts[k - 1], pts[k]), _unit(pts[k], pts[k + 1]))) > 45.0 + TOL:
            n += 1
    return n


def _on_lattice(a, b):
    g = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 45.0
    return g < TOL or g > 45.0 - TOL


def square_turns(pts, w, fixed=None, margin=0.002, reach=None, nd=5):
    """Re-draw every short_turns() piece as a full one: the run of pieces pts[i] .. pts[j] around it is
    dropped, the legs pts[i-1] -> pts[i] and pts[j] -> pts[j+1] are extended to where their lines cross (X),
    and X is chamfered like fix45 (a 90-degree joint gets one piece, a 135-degree one two, each w + margin
    long).  The run starts at the short piece and grows one piece at a time on either side (at most 3 pieces
    dropped): a widening that slides a diagonal along the next leg can leave that leg's remnant short beside a
    turn of the same sense, and only the longer run has room.  A candidate is kept only when every segment
    stays on the 0/45 lattice, no joint over 45 degrees is added, the short count drops, X lies within
    `reach` (default 4 w) of the piece, and no point `fixed(p)` is true for is moved.  The copper moves into
    the corner; clearances are route.py check's to judge.  Returns (pts, number of runs re-drawn)."""
    reach = 4 * w if reach is None else reach
    fixed = fixed or (lambda p: False)
    cut = w + margin
    P = _clean(pts)
    done = 0
    for _ in range(4 * len(P) + 4):
        sh = _short_idx(P, w, 1e-3)
        if not sh:
            break
        best = None
        for k in sh:
            for span in range(1, 4):                       # pieces dropped: k .. k+span-1, shifted left 0 .. span-1
                for i in range(k - span + 1, k + 1):
                    j = i + span                           # pts[i] .. pts[j] replaced; legs end at pts[i], start at pts[j]
                    if i < 1 or j > len(P) - 2:
                        continue
                    a, b, c, d = P[i - 1], P[i], P[j], P[j + 1]
                    if math.dist(a, b) < 1e-9 or math.dist(c, d) < 1e-9 or not _on_lattice(a, b) or not _on_lattice(c, d):
                        continue
                    if any(fixed(P[m]) for m in range(i, j + 1)):
                        continue
                    sg = [_cross(P[m - 1], P[m], P[m + 1]) for m in range(i, j + 1) if
                          math.dist(P[m - 1], P[m]) > 1e-9 and math.dist(P[m], P[m + 1]) > 1e-9]
                    if not sg or not (all(x > 0 for x in sg) or all(x < 0 for x in sg)):
                        continue                           # an S-jog is not a turn to square
                    u_in, u_out = _unit(a, b), _unit(c, d)
                    det = u_in[0] * u_out[1] - u_in[1] * u_out[0]
                    if abs(det) < 1e-9:
                        continue
                    tt = ((c[0] - a[0]) * u_out[1] - (c[1] - a[1]) * u_out[0]) / det
                    X = (a[0] + tt * u_in[0], a[1] + tt * u_in[1])
                    if tt <= 1e-6 or min(math.dist(X, P[m]) for m in range(i, j + 1)) > reach:
                        continue
                    t = _turn(u_in, u_out)
                    steps = round(abs(t) / 45.0)
                    if steps < 1 or steps > 3 or abs(abs(t) - 45.0 * steps) > TOL:
                        continue
                    sgn = 1.0 if t > 0 else -1.0
                    mids = [_rot(u_in, sgn * 45.0 * (m + 1)) for m in range(steps - 1)]
                    sx, sy = sum(m[0] for m in mids) * cut, sum(m[1] for m in mids) * cut
                    p_ = (sx * u_out[1] - sy * u_out[0]) / det
                    q_ = (u_in[0] * sy - u_in[1] * sx) / det
                    la, lc = math.dist(a, X), (d[0] - X[0]) * u_out[0] + (d[1] - X[1]) * u_out[1]
                    if p_ >= la - 1e-6 or q_ >= lc - 1e-6:
                        continue                           # the chamfer would eat a whole leg
                    cur = (X[0] - p_ * u_in[0], X[1] - p_ * u_in[1])
                    new = [cur]
                    for m in mids:
                        cur = (cur[0] + cut * m[0], cur[1] + cut * m[1])
                        new.append(cur)
                    new = [(round(x, nd), round(y, nd)) for x, y in new]
                    Q = P[:i] + new + P[j + 1:]
                    if not all(_on_lattice(Q[m], Q[m + 1]) for m in range(len(Q) - 1) if math.dist(Q[m], Q[m + 1]) > 1e-6):
                        continue
                    if _over45(Q) > _over45(P) or len(_short_idx(_clean(Q), w, 1e-3)) >= len(sh):
                        continue
                    key = (span, max(math.dist(X, P[m]) for m in range(i, j + 1)))
                    if best is None or key < best[0]:
                        best = (key, Q)
                if best is not None:
                    break
            if best is not None:
                break
        if best is None:
            break
        P = _clean(best[1])
        done += 1
    if not done:
        return [list(p) for p in pts], 0
    return [list(p) for p in P], done
