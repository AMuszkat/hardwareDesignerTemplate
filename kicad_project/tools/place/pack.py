#!/usr/bin/env python3
"""Pack a handful of parts into the space the board leaves, against real rules.

    tools/kpy tools/place/pack.py --board board1 --spec routing/pack_U3.json [--out plan.json]

`place.py check` JUDGES a placement; it does not find one.  When a block has to
be re-arranged -- a regulator that must move closer to its load while three
capacitors still arrive within 2 mm of their pins -- the packing is a search,
and doing it by hand is how a session burns an hour and still lands 0.04 mm
gaps.

The spec is JSON:

    {"movable": {"U3": {"rot": [0, 90], "box": [124, 105, 128.5, 110],
                        "allow_keepout": ["analog zone"]}, ...},
     "gap": 0.2,                                        # courtyard gap, every pair
     "near": [["C25.1", "U3.2", 2.0], ...],             # pad within N mm of pad
     "span": [["U3", "U4", 1.5], ...],                  # courtyard gap at least
     "gapto": [["U3", ["R1", "R2", "R3"], 1.0, 3.0]],   # courtyard gap to the nearest
                                                        # of a set, inside [lo, hi]
     "keepout": true}                                   # honour placement.json keep-outs

Every part not in `movable` is fixed, courtyards and keep-outs come from the
board and `placement.json`, and the objective is the sum of the violations, so
a run that reaches 0 has satisfied all of them at once.  It anneals from the
current positions; `--seed` and `--iters` make a run reproducible, and the
result is a plain placement plan for `place.py check` to judge independently --
this file never writes the board.
"""

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import place as P  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", type=P.board_dir_arg, default=None,
                    help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out")
    ap.add_argument("--iters", type=int, default=120000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--restarts", type=int, default=6)
    a = ap.parse_args()

    a.board = a.board or P.board_dir_arg(None)
    B = P.Board(a.board)
    spec = json.loads((Path(a.spec) if Path(a.spec).exists() else Path(a.board) / a.spec).read_text())
    GAP = spec.get("gap", 0.2)
    movable = spec["movable"]

    # --- geometry, all in mm, all from the board -----------------------------
    def local(ref):
        """Courtyard and pads as offsets from the footprint's own origin, per rotation."""
        f = B.fps[ref]
        ox, oy = B.mm(f.GetPosition().x), B.mm(f.GetPosition().y)
        rot = f.GetOrientationDegrees() % 360
        c = B.courtyard(f)
        pads = {p.GetNumber(): (B.mm(p.GetPosition().x) - ox, B.mm(p.GetPosition().y) - oy)
                for p in f.Pads()}
        return rot, (c[0] - ox, c[1] - oy, c[2] - ox, c[3] - oy), pads

    def spin(v, deg):
        x, y = v
        for _ in range(int(deg // 90) % 4):
            # KiCad turns a footprint's own offsets (x, y) -> (y, -x) per +90
            # degrees.  Getting the sense wrong mirrors every rotated part's
            # PADS while its (symmetric) courtyard stays right, so `place.py
            # check` passes and the near-constraints are quietly measured
            # against the wrong point.  A 0603 with pad 1 at (-0.825, 0) at
            # rot 0 has it SOUTH of centre at rot 90.
            x, y = y, -x
        return x, y

    def place(ref, x, y, rot):
        base_rot, box, pads = LOC[ref]
        d = (rot - base_rot) % 360
        cs = [spin((box[0], box[1]), d), spin((box[2], box[1]), d),
              spin((box[0], box[3]), d), spin((box[2], box[3]), d)]
        xs = [c[0] for c in cs]; ys = [c[1] for c in cs]
        rect = (x + min(xs), y + min(ys), x + max(xs), y + max(ys))
        pd = {k: (x + spin(v, d)[0], y + spin(v, d)[1]) for k, v in pads.items()}
        return rect, pd

    back = sorted(r for r in movable if r in B.fps and B.fps[r].IsFlipped())
    if back:
        P.die(f"{', '.join(back)} on the back side: this packer places FRONT-side parts only "
              "(a plan it wrote would flip them to the front). Leave them out, or place "
              "them with a hand-written plan ([x, y, rot, \"B\"]).")
    LOC = {r: local(r) for r in movable}
    fixed, fixed_rects = [], []
    for ref, f in B.fps.items():
        if ref in movable:
            continue
        c = B.courtyard(f)
        if c:
            c = c[:4]
            fixed.append((ref, c))
            if f.IsFlipped():
                continue                      # a back-side courtyard is no obstacle on the front
            # overlaps are judged against the courtyard's own strips, not its box:
            # an angled connector's pin field is narrower than its body
            fixed_rects += [(ref, q[:4]) for q in B.courtyard_rects(f)]
    keepouts = []
    if spec.get("keepout", True):
        o = B.outline
        for k in B.cfg.get("keepouts", []):
            if k.get("side", "F") not in ("F", "both"):
                continue                      # this packer places front-side parts
            r = k["rect"]                     # board-relative; null = open on that side
            keepouts.append((k["name"], set(k.get("allow", [])),
                             (o[0] + r[0] if r[0] is not None else -1e9,
                              o[1] + r[1] if r[1] is not None else -1e9,
                              o[0] + r[2] if r[2] is not None else 1e9,
                              o[1] + r[3] if r[3] is not None else 1e9)))

    def pad_xy(ref, num, pads):
        if ref in pads:
            return pads[ref][num]
        p = next(q for q in B.fps[ref].Pads() if q.GetNumber() == num)
        return B.mm(p.GetPosition().x), B.mm(p.GetPosition().y)

    def overlap(p, q, g=0.0):
        dx = min(p[2] + g, q[2]) - max(p[0] - g, q[0])
        dy = min(p[3] + g, q[3]) - max(p[1] - g, q[1])
        return max(0.0, min(dx, dy)) if dx > 0 and dy > 0 else 0.0

    def rect_gap(p, q):
        dx = max(p[0] - q[2], q[0] - p[2], 0.0)
        dy = max(p[1] - q[3], q[1] - p[3], 0.0)
        return math.hypot(dx, dy)

    def cost(state):
        rects, pads, c = {}, {}, 0.0
        for ref, (x, y, rot) in state.items():
            r, pd = place(ref, x, y, rot)
            rects[ref], pads[ref] = r, pd
            box = movable[ref].get("box")
            if box:
                c += 10 * (max(0.0, box[0] - r[0]) + max(0.0, box[1] - r[1])
                           + max(0.0, r[2] - box[2]) + max(0.0, r[3] - box[3]))
            for name, allow, k in keepouts:
                if name not in movable[ref].get("allow_keepout", []) and ref not in allow:
                    c += 8 * overlap(r, k)
            for _, fr in fixed_rects:
                c += 12 * overlap(r, fr, GAP)
        refs = list(state)
        for i in range(len(refs)):
            for j in range(i + 1, len(refs)):
                c += 12 * overlap(rects[refs[i]], rects[refs[j]], GAP)
        for ref, a_, b_ in spec.get("span", []):
            ra = rects.get(ref) or dict(fixed)[ref]
            rb = rects.get(a_) or dict(fixed)[a_]
            c += 6 * max(0.0, b_ - rect_gap(ra, rb))
        for pa, pb, lim in spec.get("near", []):
            (r1, p1), (r2, p2) = (s_.split(".", 1) for s_ in (pa, pb))
            g1, g2 = pad_xy(r1, p1, pads), pad_xy(r2, p2, pads)
            d = math.hypot(g1[0] - g2[0], g1[1] - g2[1])
            c += 4 * max(0.0, d - lim)
        for ref, others, lo, hi in spec.get("gapto", []):
            ra = rects.get(ref) or dict(fixed)[ref]
            g = min(rect_gap(ra, rects.get(o_) or dict(fixed)[o_]) for o_ in others)
            c += 5 * (max(0.0, lo - g) + max(0.0, g - hi))
        return c

    best = None
    for rs in range(a.restarts):
        rnd = random.Random(a.seed + rs * 977)
        state = {}
        for ref, m in movable.items():
            f = B.fps[ref]
            rot = f.GetOrientationDegrees() % 360
            allowed = m.get("rot")
            if allowed and rot not in [v % 360 for v in allowed]:
                rot = allowed[0]              # the spec's list binds the START too
            state[ref] = (B.mm(f.GetPosition().x), B.mm(f.GetPosition().y), rot)
        cur = cost(state)
        T0, T1 = 1.5, 0.01
        for it in range(a.iters):
            T = T0 * (T1 / T0) ** (it / a.iters)
            ref = rnd.choice(list(movable))
            x, y, rot = state[ref]
            step = 2.0 * T + 0.05
            if rnd.random() < 0.12 and len(movable[ref].get("rot", [rot])) > 1:
                nx, ny, nr = x, y, rnd.choice(movable[ref]["rot"])
            else:
                nx = round(x + rnd.gauss(0, step), 2)
                ny = round(y + rnd.gauss(0, step), 2)
                nr = rot
            old = state[ref]
            state[ref] = (nx, ny, nr)
            c = cost(state)
            if c <= cur or rnd.random() < math.exp((cur - c) / max(T, 1e-6)):
                cur = c
            else:
                state[ref] = old
            if cur < 1e-9:
                break
        if best is None or cur < best[0]:
            best = (cur, dict(state))
        print(f"  restart {rs}: cost {cur:.3f}")
        if cur < 1e-9:
            break

    cur, state = best
    print(f"best cost {cur:.4f}")
    plan = {r: [round(v[0], 2), round(v[1], 2), int(v[2])] for r, v in state.items()}
    out = Path(a.out) if a.out else None
    if out:
        (out if out.is_absolute() else Path(a.board) / out).write_text(json.dumps(plan, indent=1) + "\n")
        print("wrote", out)
    else:
        print(json.dumps(plan, indent=1))
    return 0 if cur < 1e-9 else 1


if __name__ == "__main__":
    sys.exit(main())
