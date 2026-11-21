#!/usr/bin/env python3
"""orphans.py -- the copper a placement plan orphans, named BEFORE the plan is applied, as a route plan's rips.

    tools/kpy tools/route/orphans.py --board board1 PLACE.json [--out RIPS.json] [--merge PLAN.json OUT.json] [--reach MM]

`place.py check` judges the parts and `route.py check` the plan's own copper: neither sees the link and the via
a moved capacitor leaves behind, joined to no pad (board1 D116: 4 segments and 3 vias, found only by a rehearsal
apply).  Read-only, on the board as it is before `place.py apply`.

The rule, exactly:
  * a part of PLACE.json MOVES when its x, y (KiCad's nanometre), rotation (0.001 degree) or side differs from
    the board's; a part listed at its own place does not;
  * the moving parts are moved IN MEMORY (place.py's own apply_moves) and KiCad's connectivity is read before and
    after: a track or via LOST a pad when it touched it before and not after.  A pad that lands where it stood,
    or another moved part's pad of the net that lands on the old copper (two parts swapped), is not lost;
  * copper is clustered by KiCad's GetConnectedTracks (tracks and vias that touch; never through a pad or a zone);
  * a cluster that lost a pad and touches NO pad after the move is orphaned whole -- its vias too, each marked
    `in_zone` when it stands in a zone of its net (a plane via with no pad: listed, and yours to judge);
  * a cluster that still touches a pad is peeled from each lost pad inward: an item goes when it touches no pad
    after the move and what is left of the cluster holds it at ONE of its two ends at most (an end is held when
    it lies in the other's copper; a via goes when one thing at most holds it, a zone of its net counted as
    one), then its neighbours are asked the same -- so the peel stops at the first junction, at a plane via
    another track still uses, and at the first item that reaches a pad, and that item stays;
  * a BARE cluster -- it touched no pad before the move either: a capacitor's power via set beside its pad at the
    via-to-pad gap, with no link, for a pour to join -- has no pad to lose, so it is judged by what it stands
    BESIDE: the pads of its net whose centre is within --reach (1.27 mm, centre to centre; 0 switches it off) of
    one of its vias, on a layer both have.  Beside a pad before the move and beside none after it, of any part,
    staying or arriving: named whole, `unlinked`.  A pad that stays within reach keeps it, so does a pad it
    touches after the move; one that stands in a zone of its net is a plane via the fill joins -- printed
    `beside -- decide by hand`, never written as a rip (board1 RV59.5: two such vias, 0.102 and 0.420 mm of
    copper gap from C48.1 and C11.1, were left on the board by a list that knew only what touches).
What it does NOT decide (printed as `shared -- decide by hand`, exit 1, never written as a rip): an item that still
stands on a pad and is left a dead end (the last track to a staying pin: it will dangle), an item on the way
that another joins along its body instead of at an end (short fat links in one blob), and anything behind them.
It never looks through a zone: a pour that loses its only pad is `pour.py`'s to find.

One line an item and a total; --out writes {"rips": [...], "ripvias": [...]} in route.py's form, each with its
`why`; --merge writes PLAN.json with them appended as OUT.json (an item PLAN already rips is not added twice).
The group named is the one whose `<board>/routing/*.applied.json` holds the item's UUID.  Exit 0 nothing
orphaned, 1 items listed, 2 usage or load error.
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import route as R  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board")
    ap.add_argument("place", help='the placement plan: {"REF": [x, y, rot] or [x, y, rot, "B"]}')
    ap.add_argument("--out", default=None, help="write the rips and ripvias here")
    ap.add_argument("--merge", nargs=2, metavar=("PLAN.json", "OUT.json"), help="PLAN with the rips appended, written as OUT")
    ap.add_argument("--reach", type=float, default=1.27, metavar="MM",
                    help="a via that touches no pad and no pad's track stands BESIDE the pads of its net whose centre is within "
                         "MM of its own (the handoff's distance for a capacitor's own via; default 1.27, 0 = do not look)")
    a = ap.parse_args()
    a.board = R.P.board_dir_arg(a.board)
    try:
        moves = R.P.load_moves(R.P.boardsel.in_board(a.board, a.place))
        plan = json.loads(Path(a.merge[0]).read_text()) if a.merge else None
    except (OSError, ValueError) as e:
        R.P.die(f"cannot read {e}")
    B = R.P.Board(a.board)
    pn, b, mm = B.pn, B.b, B.pn.ToMM
    if [r for r in moves if r not in B.fps]:
        R.P.die(f"{a.place} names parts that are not on the board: {sorted(r for r in moves if r not in B.fps)}")

    def differs(f, m):
        p = f.GetPosition()
        return (pn.FromMM(m[0]), pn.FromMM(m[1])) != (p.x, p.y) or (len(m) > 3 and str(m[3]).upper().startswith("B")) != f.IsFlipped() \
            or abs((f.GetOrientationDegrees() - m[2] + 180) % 360 - 180) > 1e-3

    moving = {r: m for r, m in moves.items() if differs(B.fps[r], m)}
    items = {t.m_Uuid.AsString(): t for t in b.GetTracks()}
    is_via = {u: t.Type() == pn.PCB_VIA_T for u, t in items.items()}

    def scan():
        """{uuid: ({REF.PAD: pad} it touches, {uuid of the tracks and vias it touches})}, by KiCad's connectivity"""
        b.BuildConnectivity()
        c = b.GetConnectivity()
        return {u: ({f"{p.GetParentFootprint().GetReference()}.{p.GetNumber()}": p for p in c.GetConnectedPads(t)},
                    {o.m_Uuid.AsString() for o in c.GetConnectedTracks(t)} - {u}) for u, t in items.items()}

    def pads_now():
        """{net code: [(REF.PAD, x, y, its copper layers)]} -- where every pad with copper and a net stands now"""
        d = {}
        for f in b.GetFootprints():
            for p in f.Pads():
                ls, c = set(p.GetLayerSet().CuStack()), p.GetBoundingBox().Centre()
                if ls and p.GetNetCode() > 0:
                    d.setdefault(p.GetNetCode(), []).append((f"{f.GetReference()}.{p.GetNumber()}", c.x, c.y, ls))
        return d

    def beside(mem, pads):
        """{REF.PAD: nm} -- the pads of its net a cluster's vias stand beside: centre to centre within --reach, on a layer both have"""
        d = {}
        for v in (items[u] for u in mem if is_via[u]):
            c = v.GetPosition()
            for name, x, y, ls in pads.get(v.GetNetCode(), ()):
                r = math.hypot(x - c.x, y - c.y)
                if r <= pn.FromMM(a.reach) and any(v.IsOnLayer(l) for l in ls):
                    d[name] = min(r, d.get(name, r))
        return d

    before, pads0 = scan(), pads_now()
    B.apply_moves(moving)
    after, pads1 = scan(), pads_now()
    nb = {u: v[1] for u, v in before.items()}
    group = {}
    for f in sorted((a.board / "routing").glob("*.applied.json")):
        for v in json.loads(f.read_text()).values():
            group.update({u: f.name[:-len(".applied.json")] for u in (v if isinstance(v, list) else []) if isinstance(u, str)})

    def in_zone(v):
        p = v.GetPosition()
        return any(z.GetNetCode() == v.GetNetCode() and not z.GetIsRuleArea()
                   and any(v.IsOnLayer(l) and (z.HitTestFilledArea(l, p, v.GetWidth(l) // 2) if z.IsFilled() else z.Outline().Contains(p))
                           for l in z.GetLayerSet().CuStack()) for z in b.Zones())

    def held(u, dead):
        """At how many of its ends is the item held, by the pads it touches after the move and the copper the peel
        left?  A via: by how many of them, a zone of its net one more.  None: something joins a track along its body."""
        t, live = items[u], [items[o] for o in nb[u] if o not in dead] + list(after[u][0].values())
        if is_via[u]:
            return len(live) + in_zone(t)

        def at(o, p):      # o reaches t's end p: p lies in o's copper, or an end of the track / via o in t's round cap
            return o.HitTest(p, 0) or any(math.hypot(q.x - p.x, q.y - p.y) <= t.GetWidth() / 2
                                          for q in ((o.GetStart(), o.GetEnd()) if hasattr(o, "GetStart") else ()))
        ends = [{i for i, p in enumerate((t.GetStart(), t.GetEnd())) if at(o, p)} for o in live]
        return len(set().union(*ends)) if all(ends) else None

    out, shared, byhand, seen = [], [], [], set()
    for u0 in items:
        if u0 in seen:
            continue
        mem, stack = set(), [u0]
        while stack:
            u = stack.pop()
            if u not in mem:
                mem.add(u)
                stack += nb[u] - mem
        seen |= mem
        lost = {u: sorted(set(before[u][0]) - set(after[u][0])) for u in mem}
        hung, bare = sorted({p for u in mem for p in lost[u]}), {}
        if not hung and a.reach > 0 and not any(before[u][0] or after[u][0] for u in mem):
            bare = beside(mem, pads0)          # a cluster no pad ever touched: what did it stand beside, and does it still?
            if bare and not beside(mem, pads1):
                v = [items[u] for u in sorted(mem) if is_via[u]]
                if any(in_zone(t) for t in v):
                    byhand.append(dict(net=v[0].GetNetname(), at=[round(mm(q), 3) for q in v[0].GetPosition()], items=len(mem),
                                       pads=[f"{p} ({mm(r):.3f} mm)" for p, r in sorted(bare.items(), key=lambda i: i[1])]))
                else:
                    hung = [p for p, r in sorted(bare.items(), key=lambda i: i[1])]
        if not hung:
            continue
        kept = sorted({p for u in mem for p in after[u][0]})
        dead, todo, asked = (set(mem), [], set()) if not kept else (set(), [u for u in mem if lost[u]], set())
        while todo:
            u = todo.pop()
            asked.add(u)
            if u not in dead and not after[u][0] and (held(u, dead) or 0) <= 1 and held(u, dead) is not None:
                dead.add(u)
                todo += nb[u] - dead
        # not decided: what lost a pad and stands, and where the peel stopped at a dead end on a pad or a body joint
        left = sorted({u for u in mem if lost[u]} - dead | {u for u in asked - dead if (held(u, dead) or 0) <= 1})
        if left:
            shared.append(dict(net=items[left[0]].GetNetname(), lost=hung, pads=kept, n=len(left),
                               items=len(mem), at=[round(mm(v), 3) for v in items[left[0]].GetPosition()]))
        for u in sorted(dead, key=lambda u: (is_via[u], u)):
            t = items[u]
            xy = lambda p: [round(mm(p.x), 6), round(mm(p.y), 6)]  # noqa: E731
            e = dict(net=t.GetNetname(), at=xy(t.GetPosition()), in_zone=in_zone(t)) if is_via[u] else \
                dict(net=t.GetNetname(), layer=t.GetLayerName(), pts=[xy(t.GetStart()), xy(t.GetEnd())], width=round(mm(t.GetWidth()), 6))
            why = f"orphaned by the move of {', '.join(hung)}" if not bare else \
                (f"unlinked: it touched no pad, stood {mm(bare[hung[0]]):.3f} mm (centre to centre) from {hung[0]} and after the move "
                 f"of {', '.join(hung)} no pad of its net is within {a.reach:g} mm")
            out.append(dict(e, group=group.get(u, "?"), hung=hung, whole=not kept, unlinked=bool(bare), cluster=min(mem), uuid=u,
                            why=f"{why}; laid by group {group.get(u, '?')}, uuid {u}"))

    print(f"{len(moves)} parts in {Path(a.place).name}, {len(moving)} move")
    for e in out:
        where = f"via {tuple(e['at'])}" + (" in_zone" if e["in_zone"] else "") if "at" in e else \
            f"{e['layer']} {tuple(e['pts'][0])}-{tuple(e['pts'][1])} w {e['width']:g}"
        print(f"  {'ripvia' if 'at' in e else 'rip   '} {R.short(e['net'])}  {where}  group {e['group']}  "
              f"{'unlinked, beside' if e['unlinked'] else 'whole cluster of' if e['whole'] else 'stub of'} {','.join(e['hung'])}")
    for s in shared:
        print(f"  shared -- decide by hand: {R.short(s['net'])} cluster of {s['items']} items loses {','.join(s['lost'])} and stays on "
              f"{','.join(s['pads'][:8])}{' ...' if len(s['pads']) > 8 else ''}; {s['n']} of them not decided, one at {tuple(s['at'])}")
    for s in byhand:
        print(f"  beside -- decide by hand: {R.short(s['net'])} via {tuple(s['at'])} ({s['items']} items) touched no pad, stood beside "
              f"{', '.join(s['pads'])}, has no pad of its net within {a.reach:g} mm after the move and stands in a zone of its net: "
              f"a plane via, not written as a rip")
    rips = [{k: e[k] for k in ("net", "layer", "pts", "width", "why")} for e in out if "pts" in e]
    ripvias = [{k: e[k] for k in ("net", "at", "why")} for e in out if "at" in e]
    print(f"ORPHANS: {len(rips)} segments, {len(ripvias)} vias ({sum(e.get('in_zone', False) for e in out)} in a zone of their net, "
          f"{sum(e['unlinked'] and 'at' in e for e in out)} unlinked beside a moved pad) "
          f"in {len({e['cluster'] for e in out})} clusters; "
          f"{len(shared)} shared, {len(byhand)} beside, left to you")
    if a.out:
        Path(a.out).write_text(json.dumps({"rips": rips, "ripvias": ripvias}, indent=1) + "\n")
        print(f"wrote {a.out}")
    if a.merge:
        def key(r):
            return (R.short(r["net"]), r.get("layer"), tuple(sorted((round(x, 2), round(y, 2)) for x, y in r.get("pts", [r.get("at")]))))
        have, dup = {key(r) for r in plan.get("rips", []) + plan.get("ripvias", [])}, 0
        for name, new in (("rips", rips), ("ripvias", ripvias)):
            dup += sum(key(r) in have for r in new)
            plan[name] = plan.get(name, []) + [r for r in new if key(r) not in have]
        Path(a.merge[1]).write_text(json.dumps(plan, indent=1) + "\n")
        print(f"wrote {a.merge[1]}: {a.merge[0]} + {len(rips) + len(ripvias) - dup} rips ({dup} it already had, not added twice)")
    return 1 if out or shared or byhand else 0


if __name__ == "__main__":
    sys.exit(main())
