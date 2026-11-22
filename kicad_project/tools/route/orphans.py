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
  * the peel takes a VIA only when nothing holds it: a via that copper still reaching a pad holds, or that stands on
    a pad's land, is that pad's fan-out via -- printed `kept`, neither a rip nor a decision (board1 RV59: a capacitor
    under U1 sat on four GND balls' dog-bone vias, and each was listed `stub of C10.2` with the ball's track left to
    dangle);
  * a via the board names as stitching -- the guide's `stitch via` row (of its net, within the distance of a via of
    the class this plan does not rip), or the group that laid it (the via's own `why` in the plan its
    `*.applied.json` names says stitch, or the plan is stitch.py's) -- is printed `stitch -- decide by hand`, never
    written as a rip (board1 RV59: the pairs' GND stitching via of R3head, listed `unlinked, beside C54.2`);
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
With a ROUTE PLAN (--plan, or --merge's PLAN), what that plan's own rips leave behind as well: after the move, the
plan's rips, ripvias and polyrips and the rips listed here are taken off and its tracks, vias and polygons laid, in
memory (no refill), and every OTHER track whose end was held (by a pad, a track, a via) before and is held by nothing
now, or a via that something held before and nothing holds now outside a zone of its net, is printed `dangles --
relink or rip`, with what held it -- never written as a rip: the link may be another pad's, to relink (board1 RV60.5:
a plan ripped U300 A6's GND via (14.95, 16.1), and C305.2's B.Cu link to it showed only as track_dangling in
`route.py check`).

One line an item and a total; --out writes {"rips": [...], "ripvias": [...]} in route.py's form, each with its
`why`; --merge writes PLAN.json with them appended as OUT.json (an item PLAN already rips is not added twice).
The group named is the one whose `<board>/routing/*.applied.json` holds the item's UUID.  Exit 0 nothing
orphaned, 1 items listed, 2 usage or load error.
"""
import argparse
import json
import math
import re
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
    ap.add_argument("--plan", default=None, metavar="ROUTE.json",
                    help="the route plan that goes with the move (default: --merge's PLAN): its rips and ripvias, with the ones "
                         "listed here, are taken off and its new copper laid, in memory, and every OTHER track or via left hanging "
                         "by them is named -- 'dangles -- relink or rip'")
    ap.add_argument("--reach", type=float, default=1.27, metavar="MM",
                    help="a via that touches no pad and no pad's track stands BESIDE the pads of its net whose centre is within "
                         "MM of its own (the handoff's distance for a capacitor's own via; default 1.27, 0 = do not look)")
    a = ap.parse_args()
    a.board = R.P.board_dir_arg(a.board)
    try:
        moves = R.P.load_moves(R.P.boardsel.in_board(a.board, a.place))
        plan = json.loads(Path(a.merge[0]).read_text()) if a.merge else None
        rp = a.plan or (a.merge[0] if a.merge else None)
        rplan = json.loads(Path(rp).read_text()) if rp else None
    except (OSError, ValueError) as e:
        R.P.die(f"cannot read {e}")
    B = R.RBoard(a.board)
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
    group, plan_of = {}, {}
    for f in sorted((a.board / "routing").glob("*.applied.json")):
        man = json.loads(f.read_text())
        for v in man.values():
            for u in (v if isinstance(v, list) else []):
                if isinstance(u, str):
                    group[u], plan_of[u] = f.name[:-len(".applied.json")], man.get("plan")
    plans = {}

    def laid_by(u):
        """(the plan's why, the via's own why) in the plan whose manifest holds the via's UUID, or None"""
        rel, t = plan_of.get(u), items[u]
        if not rel:
            return None
        if rel not in plans:
            plans[rel] = None
            for f in (a.board / "routing" / Path(rel).name, a.board.parent / rel):   # the board's own copy first: a scratch copy too
                try:
                    plans[rel] = json.loads(f.read_text())
                    break
                except (OSError, ValueError):
                    continue
        d, c = plans[rel], t.GetPosition()
        if not isinstance(d, dict):
            return None
        own = next((v.get("why", "") for v in d.get("vias", []) if isinstance(v, dict) and len(v.get("at") or ()) == 2
                    and R.short(v.get("net", "")) == R.short(t.GetNetname())
                    and abs(pn.FromMM(v["at"][0]) - c.x) <= 5000 and abs(pn.FromMM(v["at"][1]) - c.y) <= 5000), "")
        return str(d.get("why", "")), str(own)

    def stitching(u, ripped):
        """Why the via is a known stitching via, or None: the guide's `stitch via` row (a via of its named net within the
        distance of a via of a net in the class that this plan does not rip too), or the record of the group that laid it
        (its own `why` in that plan says stitch, or the plan is stitch.py's)"""
        t = items[u]
        c, net = t.GetPosition(), R.short(t.GetNetname())
        for r in B.gates.get("stitch") or ():
            if net != R.short(r["net"]):
                continue
            for w, o in items.items():
                if is_via[w] and w not in ripped and R.short(o.GetNetname()) != net and r["class"] in B.netclasses(o.GetNetname()):
                    q = o.GetPosition()
                    if math.hypot(q.x - c.x, q.y - c.y) <= pn.FromMM(r["within"]) + 1:
                        return (f"the guide's 'stitch via' row ({r['class']}: {r['net']} within {r['within']:g} mm) holds it for "
                                f"{R.short(o.GetNetname())} via ({mm(q.x):.3f}, {mm(q.y):.3f})")
        rec = laid_by(u)
        if rec and re.search(r"stitch", rec[1], re.I):
            return f"group {group[u]} laid it as {rec[1]!r}"
        if rec and re.search(r"stitching \(stitch\.py\)", rec[0]):
            return f"group {group[u]} is stitch.py's: {rec[0][:80]!r}"
        return None

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

    def goes(u, dead):
        """The peel takes a track held at one end at most, and a via only when NOTHING holds it: a via that copper still
        reaching a pad holds is that pad's own fan-out via (a BGA ball's dog-bone that a moved capacitor's pad sat on)"""
        h = held(u, dead)
        return h is not None and h <= (0 if is_via[u] else 1)

    def reaches(u, dead):
        """the pads the item reaches after the move through the copper the peel left, itself included"""
        got, seen_, st = set(), set(), [u]
        while st:
            w = st.pop()
            if w not in seen_:
                seen_.add(w)
                got |= set(after[w][0])
                st += [o for o in nb[w] if o not in dead]
        return sorted(got)

    out, shared, byhand, seen, kept_v, stitch = [], [], [], set(), [], []
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
            if u not in dead and not after[u][0] and goes(u, dead):
                dead.add(u)
                todo += nb[u] - dead
        # not decided: what lost a pad and stands, and where the peel stopped at a dead end on a pad or a body joint
        left = sorted({u for u in mem if lost[u]} - dead | {u for u in asked - dead if (held(u, dead) or 0) <= 1})
        for u in [u for u in left if is_via[u] and reaches(u, dead)]:      # a pad's own fan-out via: kept, not a decision
            left.remove(u)
            r = reaches(u, dead)
            kept_v.append(dict(net=items[u].GetNetname(), at=[round(mm(q), 3) for q in items[u].GetPosition()], lost=lost[u],
                               on=after[u][0], pads=r, group=group.get(u, "?")))
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

    ripped = {e["uuid"] for e in out}
    for e in [e for e in out if "at" in e]:              # a known stitching via: a decision, never a rip
        sw = stitching(e["uuid"], ripped)
        if sw:
            out.remove(e)
            stitch.append(dict(e, why=sw))
    dangles = plan_dangles(B, rplan, items, is_via, after, {e["uuid"] for e in out}, in_zone, group) if rplan is not None else []
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
    for s in stitch:
        print(f"  stitch -- decide by hand: {R.short(s['net'])} via {tuple(s['at'])} group {s['group']} "
              f"({'unlinked, beside' if s['unlinked'] else 'would go with'} {','.join(s['hung'])}) is a stitching via: {s['why']}; "
              f"not written as a rip")
    for s in kept_v:
        print(f"  kept: {R.short(s['net'])} via {tuple(s['at'])} group {s['group']} loses {','.join(s['lost']) or 'nothing'} and "
              f"still reaches {','.join(s['pads'][:4])}{' ...' if len(s['pads']) > 4 else ''} "
              f"{'on its land' if s['on'] else 'through its track'}: a fan-out via of a pad that stays, not a rip")
    rips = [{k: e[k] for k in ("net", "layer", "pts", "width", "why")} for e in out if "pts" in e]
    ripvias = [{k: e[k] for k in ("net", "at", "why")} for e in out if "at" in e]
    print(f"ORPHANS: {len(rips)} segments, {len(ripvias)} vias ({sum(e.get('in_zone', False) for e in out)} in a zone of their net, "
          f"{sum(e['unlinked'] and 'at' in e for e in out)} unlinked beside a moved pad) "
          f"in {len({e['cluster'] for e in out})} clusters; "
          f"{len(shared)} shared, {len(byhand)} beside, {len(stitch)} stitching, left to you; {len(kept_v)} fan-out vias kept")
    if rplan is not None:
        for d in dangles:
            print(f"  dangles -- relink or rip: {d['line']}")
        print(f"DANGLES: {sum('pts' in d for d in dangles)} tracks, {sum('at' in d for d in dangles)} vias left hanging by "
              f"{Path(rp).name}'s rips and the {len(out)} listed above, with its new copper laid; never written as a rip")
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
    return 1 if out or shared or byhand or stitch or dangles else 0


def plan_dangles(B, rplan, items, is_via, after, listed, in_zone, group):
    """What the route plan's rips (with the rips listed here) leave hanging, the plan's new copper laid: every other
    track with an end that was held after the move and is held by nothing now, and every via that something held and
    nothing holds now, outside a zone of its net.  Changes the in-memory board: call it after every other judgement."""
    pn, b, mm = B.pn, B.b, B.pn.ToMM
    xy = lambda p: (round(mm(p.x), 3), round(mm(p.y), 3))  # noqa: E731

    def desc(u):
        t = items[u]
        return f"via {R.short(t.GetNetname())} {xy(t.GetPosition())}" if is_via[u] else \
            f"track {R.short(t.GetNetname())} {t.GetLayerName()} {xy(t.GetStart())}-{xy(t.GetEnd())}"

    def holders(t, others):
        """{end index: [the others whose copper holds that end of track t]} -- the end in o's copper, or o's end in t's cap"""
        d = {}
        for i, p in enumerate((t.GetStart(), t.GetEnd())):
            for k, o in others:
                if o.HitTest(p, 0) or any(math.hypot(q.x - p.x, q.y - p.y) <= t.GetWidth() / 2
                                          for q in ((o.GetStart(), o.GetEnd()) if hasattr(o, "GetStart") else ())):
                    d.setdefault(i, []).append(k)
        return d

    # before the plan (after the move): what holds each item, by name, while every item is still on the board
    names = {}
    held0 = {}
    for u, t in items.items():
        if u in listed:
            continue
        others = [(o, items[o]) for o in after[u][1]] + [(f"pad {n}", p) for n, p in after[u][0].items()]
        held0[u] = len(others) if is_via[u] else holders(t, others)
        names.update({o: desc(o) for o in after[u][1] if o not in names})
    B.rip_plan(rplan)                                     # a rip that matches nothing stops here, as in route.py check
    present = {t.m_Uuid.AsString() for t in b.GetTracks()}
    for u in listed & present:
        b.RemoveNative(items[u])
    gone = (set(items) - present) | listed
    B.retype_plan(rplan)
    import poly as POLY
    new = [("the plan's polygon", POLY.add(b, pn, q)) for q in rplan.get("polys", [])]
    for t in rplan.get("tracks", []):
        for p, q in zip(t["pts"], t["pts"][1:]):
            B.add_track(t["net"], t.get("layer", "F.Cu"), t["width"], p, q)
    for v in rplan.get("vias", []):
        B.add_via(v["net"], v["at"], v.get("pad", 0.6), v.get("drill", 0.3), v.get("top", "F.Cu"), v.get("bottom", "B.Cu"),
                  nfp=bool(v.get("nfp")))
    b.BuildConnectivity()
    c = b.GetConnectivity()
    out = []
    for u in sorted({o for g in gone if g in after for o in after[g][1]} - gone):
        t = items[u]
        pads = {f"{p.GetParentFootprint().GetReference()}.{p.GetNumber()}": p for p in c.GetConnectedPads(t)}
        others = [("track", o) for o in c.GetConnectedTracks(t)] + [(f"pad {n}", p) for n, p in pads.items()] + \
            [(k, s) for k, s in new if s.GetNetCode() == t.GetNetCode() and any(s.IsOnLayer(l) for l in t.GetLayerSet().CuStack())]
        was = [names.get(o, o) for o in after[u][1] if o in gone]
        on = f"on {','.join(sorted(pads))}" if pads else "on no pad"
        if is_via[u]:
            if held0[u] and not others and not in_zone(t):
                out.append(dict(at=list(xy(t.GetPosition())), line=f"{desc(u)} group {group.get(u, '?')}: held by "
                                f"{'; '.join(was)} before, by nothing now (not in a zone of its net)"))
            continue
        now = holders(t, others)
        for i in sorted(set(held0[u]) - set(now)):
            end = xy((t.GetStart(), t.GetEnd())[i])
            hung = [names.get(o, o) for o in held0[u][i]]
            out.append(dict(pts=[list(xy(t.GetStart())), list(xy(t.GetEnd()))], line=f"{desc(u)} w {mm(t.GetWidth()):g} group "
                            f"{group.get(u, '?')} {on}: its end {end} was held by {'; '.join(hung)}, the plan takes it off "
                            f"and nothing holds that end now"))
    return out


if __name__ == "__main__":
    sys.exit(main())
