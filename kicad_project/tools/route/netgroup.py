#!/usr/bin/env python3
"""Route a group of signal nets from a spec file -- the negotiated router
(maze.py) driven by data instead of by a script per group.

    tools/kpy tools/route/netgroup.py --board board1 routing/R3_spec.json
    tools/kpy tools/route/netgroup.py --board board1 routing/R3_spec.json --only SPI_CLK,SPI_CS

It writes the spec's "plan" file, which `route.py check` then judges like any
other plan: KiCad's DRC on a scratch copy and the rules in general_routing_guide.md.

THE SPEC

  {"group": "R3", "why": "...", "plan": "routing/R3_spi.json",
   "window": [x0, y0, x1, y1], "pitch": 0.2, "width": 0.2,
   "clearance": 0.15,        # between the nets routed here (their class; raised to the
                             # strictest class among them, as KiCad holds the stricter of two)
   "own_clearance": 0.1016,  # optional: between the routed nets, NOT raised to their class -- only
                             # where a custom DRC rule grants it (a bus's own lines); check proves it
   "padclr": 0.2,            # to everything else: a FLOOR -- each static item is kept at the
                             # stricter of this, its own clearance (class, local override) and
                             # the routed nets' class
   "via": [0.6, 0.3], "via_sep": 0.8,
   "layers": ["F.Cu", "B.Cu"],          # default: every copper layer that is not a
                                        # a guide 'plane layer' row
   "edge": 0.5,              # default: the guide's 'copper to edge', else Board Setup
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
  * every drilled pad's hole (NPTH, or PTH where its pad is not), on every layer,
    at drill / 2 + Board Setup's copper-to-hole; a new via at the hole-to-hole;
  * the spec's "block": [{"rect": [x0, y0, x1, y1], "layers": [...], "vias": true}];
  * the spec's "extra_tracks"/"extra_vias" (the group's ground and supply
    vias and pad links), which go into the plan as they are;
  * the spec's "reserve_tracks"/"reserve_vias": a later group's copper, kept
    free here and NOT written to the plan.  Reserve the later groups' tracks,
    not just their vias.

OPTIONS
  "field": {"ref": "U1", "open": ["In2.Cu", "B.Cu"], "clr": 0.1016, "width": 0.127(, "box": [x0, y0, x1, y1])}
  (or "fields": [{...}, {...}] -- two BGAs one group joins, a SoC and its DRAM, each at its
  own area rule) names a fine-pitch part whose pad field is shut, and opens it on those
    layers (no lands there; the dog-bones are the obstacles, at the field's own
    clearance "clr", for a track of "width").  A net's prelaid dog-bone (an
    extra_via inside the field) is a legal layer change for it; no new via is
    laid inside the field.  "open": [] keeps it shut on every layer;
  a net's "avoid": [[x0, y0, x1, y1(, layer(, cost))], ...] prices those cells out for it
    ("avoid_cost", default 1e4 per cell -- soft: negotiation outbids it; 1e8
    makes it effectively hard for that net; a 6th element prices that rectangle
    alone, layer null for every layer) -- a connector's pin field that belongs
    to the nets ending there, a differential pair's corridor no other net may
    split while a soft courtyard price keeps necks short;
  a leg's "goal_layer" (default F.Cu) is the layer it must arrive on -- a
    through-hole pin walled in on F.Cu is reached on B.Cu; a list ("goal_layer":
    ["In2.Cu", "B.Cu"]) arrives on any of them -- a prelaid through via is a goal
    on every layer, and naming one layer made the router lay its own via a cell
    short of it to change to that layer (hole to hole 0.0-0.15 mm);
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
  "static_exact": true does the same for STATIC round obstacles (vias, the field's dog-bones):
    a via's halo grows by what a 45-degree step's chord can cut into it, and the corner cells
    no longer forbid the step -- the diagonal lane between two diagonal vias of a 0.5 mm BGA
    field (0.354 mm from each centre) is otherwise shut by a 0.333 halo (maze.Grid.static_exact);
  "retrace_cost": 20 prices each cell of a net's OWN frozen copper it would run
    along (not round its leg's start): standing on it is legal, running back
    along it lays copper on copper and a 180-degree joint where the route
    leaves;
  "plane": {..., "open": [[x0, y0, x1, y1], ...], "open_cost": 3} lets any
    net cross the plane inside those rectangles at a price per cell -- every
    such crossing is a slot, so `pour.py` judges the result; "vias": false
    keeps every new via out of it too ("open" included, the aprons not) -- a
    via in a pour is only an antipad, harmless in a solid inner plane, but a
    row of them across a 4 mm top-side rail strip cut it in two (board2 R5:
    J1's +5V pins left on a 6 mm2 island, `pour.py`'s find, not DRC's; vias
    in "open" sat by the pins the nets left and failed the via-link rule);
  "layer_cost": {"B.Cu": 1.5} prices a layer per step;
  a net's "guide": [[x, y], ...] is the corridor it is meant to take (`fanout.py layers --guides`):
    a cell farther than "guide_reach" (0.35) from it costs "guide_cost" (3.0) more for that net;
  "keep_pads": {"In5.Cu": ["+1V8", ...]} -- inside a field, a board via whose inner pad the fab
    removes (unconnected-layer mode "remove") is modelled on such a layer as its hole + the
    board's copper-to-hole, not its pad; the nets listed keep the pad there (a power layer's
    own pours, not poured yet, so pcbnew does not flash them).

A net that starts on a pad rather than on escape copper (the far side of a
series resistor) names it as "src_pad" and gives a grid point inside it as
"src".  A THROUGH-HOLE source pad is on every layer: give "layer" as a list
("layer": ["In2.Cu", "B.Cu"]) and the search starts on each of them -- with one
layer, the net must leave the pad on it, and the other layer is reachable only
through a via clear of the pad (board2's R5: every connector-row net forced
onto In2.Cu in a row band with no room for that via).

When negotiation stalls it prints, per net, how many cells it can reach at
all (`reach`): a net that reaches a few dozen cells against tens of
thousands for the others is WALLED IN -- a placement or fanout defect to fix,
not a route to keep negotiating for.  `--repair` re-routes the overlapping
nets one at a time with the rest hard.

<plan>.partial.json is written after EVERY iteration (whole, to a temp file
renamed over it): the legs that closed, the open leg's latest iteration, its
contested cells and, from iteration 3 of a leg, the `reach` table -- so a run
killed by a timeout keeps all of it.  "state" says how the run left it:
"running" (still going, or killed), "stalled", "stopped" (--timeout), "plateau" (--plateau),
"walled", "stuck", "blocked".  For a look with view.py --plan, never for a
gate; a run that writes its plan removes it.  --timeout S stops between
iterations when the next would not finish inside S seconds and leaves the
partial as "stopped"; use it instead of a `timeout` prefix.  The log is
line-buffered, so a killed run's log is complete to its last line.

A RUN THAT CANNOT END SAYS SO, AND WHY (exit codes: 0 a plan; 1 no solution,
or the --timeout; 3 stuck; 4 blocked):

  BLOCKED (exit 4), before any negotiation: one END of a net is shut in by
    what the run cannot move -- pads, vias, tracks, a field, the edge band,
    the spec's own block / reserve_* / extra_* -- so it reaches a few hundred
    cells and none of its goal.  The block names the net, the end, the cells
    it reaches and the obstacles of the wall, nearest first, with what each
    one is and where (maze.Grid.blocked_end; every obstacle is labelled as it
    is stamped, maze.Grid.mark).  A start point to move, not a route to
    negotiate: no option of this tool opens it.
  STUCK (exit 3), `--stall N` (default 20; 0: off): the overlaps have sat on
    the same cells for N iterations -- no new cell, no fewer of them -- and
    no net on them has any way round the others as they lie
    (maze.Grid.negotiate, "stuck").  The block names the nets and the cells
    as a box in board mm per layer: those nets need the same channel.  Give
    one another layer, move the via, widen the window.  Before this a run
    like that used every one of its --iters (board1's R2 on In2.Cu: two nets,
    six cells, 433 iterations).
  PLATEAU (exit 1), `--plateau N` (default 0: off): the best overlap count of
    the last N iterations is not --plateau-pct (2 %) below the best before
    them -- the overlaps move without shrinking.  The best iteration and both
    counts are printed and the partial written; repair still runs.
  A net with no path that is NOT shut in (the 45-degree rule at an end, or
    --limit) and a run out of --iters stay exit 1, as does the --timeout.

THE PLAN AT THE OUTPUT PATH IS THIS RUN'S, OR THERE IS NONE (origin.py).  A plan
already at the spec's "plan" path is moved aside as <plan>.stale.json when the
run STARTS -- so a run that fails, times out or is killed leaves no older plan
where its own would have been (board1's R2: a plan from before the widths were
corrected, still at the path after three minutes of "no solution") -- and
deleted when this run writes its plan.  Beside the plan the run writes
<plan>.origin.json: the hash of the spec (normalised) and of the board file it
was routed on, which `route.py check` and `apply` test before anything else.
"""

import argparse
import collections
import json
import math
import os
import sys
import time
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "place"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import place as P  # noqa: E402
import maze  # noqa: E402
import origin  # noqa: E402  (the plan's origin record, and the retiring of an older plan)
import budget  # noqa: E402  (the group's effort budget: counted here, refused when spent)
import par  # noqa: E402  (tools/par.py, on the path maze put it)
from corners import straighten  # noqa: E402


# blocked_end()'s flood stops at this many (cell, layer) states before a leg is negotiated: a closed
# region this small is a start point shut in, and the two capped floods cost milliseconds per net.
# A bigger closed room is found by the uncapped test after negotiate() reports the net walled in.
BLOCK_CAP = 4000


def heading(a, b):
    """maze's heading of the step a -> b: 1..8 clockwise from east (y down)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ang = math.degrees(math.atan2(dy, dx)) % 360
    return int(round(ang / 45.0)) % 8 + 1


def _clip(a, b, box, m=0.0):
    """The part of segment a-b inside an axis-aligned box (grown by m), as (t0, t1) along it; None when
    none of it is inside (Liang-Barsky)."""
    x0, y0, x1, y1 = box[0] - m, box[1] - m, box[2] + m, box[3] + m
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p_, q_ in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p_) < 1e-12:
            if q_ < 0:
                return None
            continue
        r_ = q_ / p_
        if p_ < 0:
            t0 = max(t0, r_)
        else:
            t1 = min(t1, r_)
    return (t0, t1) if t1 - t0 > 1e-9 else None


# ---------------------------------------------------------------------------
# non-functional pads: a plan via inside an "nfp_vias" courtyard carries "nfp": true
# ---------------------------------------------------------------------------

def nfp_rule(B):
    """prelayout.json "nfp_vias", read by route.py's own reader (route.nfp_rule): the courtyards
    `route.py check` refuses an untyped via in (RULE nfp) are the ones typed here, by one
    definition.  None when the board names none.  Read BEFORE the run: a bad entry stops it in
    seconds, not after the routing."""
    import route as R            # here, not at the top: route.py puts tools/copper on the path
    return R.nfp_rule(B)


def nfp_type(B, vias, rule):
    """Set "nfp": true on every plan via whose centre lies inside one of the rule's courtyards and
    that lacks it; a via already typed is left as it is.  Returns (typed, inside): the vias typed
    now, and the vias inside at all.  Called BEFORE the plan and its origin record are written: a
    flag typed afterwards edits the plan origin.py hashed, and `check` / `apply` then stop on a
    stale plan (exit 5) -- board1 R_W300, 2026-11-23: a board-local script typed it after the
    run, and the group went to check and apply with --accept-stale plan."""
    if not rule:
        return 0, 0
    pn = B.pn
    typed = inside = 0
    for v in vias:
        p = pn.VECTOR2I(pn.FromMM(v["at"][0]), pn.FromMM(v["at"][1]))
        if not any(q.Contains(p) for _r, polys in rule["areas"] for q in polys):
            continue
        inside += 1
        if not v.get("nfp"):
            v["nfp"] = True
            typed += 1
    return typed, inside


def nfp_line(rule, typed, inside, total):
    """The one line a run prints for it."""
    if not rule:
        return 'nfp: not run -- routing/prelayout.json has no "nfp_vias"'
    return (f"nfp: {typed} of {total} plan vias typed (refs {', '.join(rule['refs'])})"
            + (f"; {inside - typed} inside already carried it" if inside > typed else ""))


def nfp_selftest(board, at=None, off=None):
    """--selftest: nfp_type() alone, no routing, no budget, on the board READ-ONLY (nothing is
    saved).  Cases: no rule -> nothing changes; a via inside a courtyard -> typed; a via outside
    every courtyard -> no "nfp" key; a via inside that already carries it -> left exactly as it
    was.  The points: each nfp ref's courtyard centre and a point 0.5 mm outside it, plus --at
    (must be inside) and --off (must be outside).  The EXPECTED answer of every point is a ray
    cast over the courtyard's corners, not KiCad's Contains (which nfp_type uses): a test that
    asked the function under test for its answers would prove nothing.

    Hand-checked on board1 (2026-11-23, the board read back): U300's F.CrtYd is the rectangle
    x 7.455..19.945, y 5.405..19.395; U1's x 21.755..36.245, y 14.255..28.745.  So
      --at 16.9,7.65   inside U300 (3.045 from its east edge, 2.245 below its top) -> typed;
      --off 20.5,10.0  between the two (0.555 past U300's east edge, 1.255 short of U1's west)
                       -> not typed;
    and (10.0, 10.0) is INSIDE U300 (2.545 in from its west edge): not an outside case.
        tools/kpy tools/route/netgroup.py --board board1 --selftest --at 16.9,7.65 --off 20.5,10.0"""
    B = P.Board(board)
    fails = []

    def ok(cond, what):
        print(f"  {'PASS' if cond else 'FAIL'}  {what}")
        if not cond:
            fails.append(what)

    v0 = [dict(net="SELFTEST", at=[1.0, 1.0], pad=0.6, drill=0.3)]
    ok(nfp_type(B, v0, None) == (0, 0) and "nfp" not in v0[0], "no nfp_vias: no via is touched")
    rule = nfp_rule(B)
    if not rule:
        print(f"SELFTEST NOT RUN: {B.dir}/routing/prelayout.json has no \"nfp_vias\" -- name a board that has")
        return 2
    mm = B.pn.ToMM
    rings = [[(mm(o.CPoint(i).x), mm(o.CPoint(i).y)) for i in range(o.PointCount())]
             for _r, polys in rule["areas"] for q in polys for o in (q.Outline(k) for k in range(q.OutlineCount()))]

    def raycast(x, y):
        """Inside any courtyard outline, by crossings of a ray towards +x."""
        for r in rings:
            c = False
            for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]):
                if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                    c = not c
            if c:
                return True
        return False

    pts = []
    for ref, polys in rule["areas"]:
        o = polys[0].Outline(0)
        xs = [mm(o.CPoint(i).x) for i in range(o.PointCount())]
        ys = [mm(o.CPoint(i).y) for i in range(o.PointCount())]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        pts.append((f"{ref} courtyard centre", (cx, cy), True))
        out = next(((x, y) for x, y in ((min(xs) - 0.5, cy), (max(xs) + 0.5, cy), (cx, min(ys) - 0.5),
                                        (cx, max(ys) + 0.5)) if not raycast(x, y)), None)
        if out:
            pts.append((f"0.5 mm outside {ref}", out, False))
    for label, s, want in (("--at", at, True), ("--off", off, False)):
        if s:
            xy = tuple(float(t) for t in s.split(","))
            if raycast(*xy) != want:
                print(f"SELFTEST BAD POINT: {label} {s} is {'outside every' if want else 'inside an'} nfp courtyard "
                      "by the ray cast -- give a point that is where the case says")
                return 2
            pts.append((f"{label} {s}", xy, want))
    vias, pre = [], []
    for label, (x, y), want in pts:
        if raycast(x, y) != want:
            print(f"SELFTEST BAD POINT: {label} ({x:.3f}, {y:.3f}): the ray cast disagrees with its case")
            return 2
        vias.append((label, want, dict(net="SELFTEST", at=[x, y], pad=0.6, drill=0.3)))
        if want:                  # the same place, already typed: must come back exactly as it went in
            pre.append((label, dict(net="SELFTEST", at=[x, y], pad=0.6, drill=0.3, nfp=True, leg="pre")))
    plan = [v for _l, _w, v in vias] + [v for _l, v in pre]
    before = [dict(v) for _l, v in pre]
    typed, inside = nfp_type(B, plan, rule)
    for label, want, v in vias:
        ok(v.get("nfp") is True if want else "nfp" not in v,
           f"{label} ({v['at'][0]:.3f}, {v['at'][1]:.3f}): {'typed' if want else 'not typed'}")
    for (label, v), b in zip(pre, before):
        ok(v == b, f"{label}, already typed: left as it was")
    n_in = sum(w for _l, w, _v in vias)
    ok((typed, inside) == (n_in, n_in + len(pre)),
       f"counts: typed {typed} (want {n_in}), inside {inside} (want {n_in + len(pre)})")
    print(nfp_line(rule, typed, inside, len(plan)))
    print("SELFTEST " + ("PASS" if not fails else f"FAIL ({len(fails)})"))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("spec", nargs="?")
    ap.add_argument("--selftest", action="store_true",
                    help="test the \"nfp\" typing of plan vias alone (no routing, no budget) on the board, "
                         "read-only; --at X,Y a point inside an nfp_vias courtyard, --off X,Y one outside")
    ap.add_argument("--at", help="--selftest: a point that must be typed")
    ap.add_argument("--off", help="--selftest: a point that must not be typed")
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
                    help="A* expansions per route; a big window OR a fine pitch (0.025 in 11.5 x 9.5 mm) hits the default "
                         "and reads as 'walled in' / 'the 45-degree rule'")
    ap.add_argument("--full", action="store_true",
                    help="re-route every net every iteration (classic PathFinder, the pre-R2 result); default: "
                         "only the nets that overlap, all of them when the overlap stalls")
    ap.add_argument("--jacobi", action="store_true",
                    help="route an iteration's nets in parallel, each against the iteration's starting "
                         "congestion (a different convergence; HW_JOBS=1 gives the same plan)")
    ap.add_argument("--seeds", type=int, default=1,
                    help="negotiate each leg with seeds SEED..SEED+N-1 in parallel; the lowest seed that "
                         "converges wins (the same plan with HW_JOBS=1)")
    ap.add_argument("--corridor", type=float, default=0,
                    help="mm: each net searches the box round its ends and last path, grown by this (or 30%% "
                         "of its span), widened x2 until it routes; 0 (default): the whole window")
    ap.add_argument("--timeout", type=float, default=0,
                    help="seconds: stop between iterations when the next would not end inside it, "
                         "partial written (use this, not a `timeout` prefix)")
    ap.add_argument("--stall", type=int, default=20,
                    help="stop a leg as STUCK (exit 3) when its overlaps have sat on the same cells for this "
                         "many iterations -- no new cell, no fewer -- and no net on them has a way round the "
                         "others; the nets and the place are printed.  0: never (run to --iters)")
    ap.add_argument("--plateau", type=int, default=0,
                    help="stop a leg (exit 1, state 'plateau') when the best overlap count of the last N "
                         "iterations is not --plateau-pct below the best before them -- overlaps that move "
                         "without shrinking, which --stall lets run.  0 (default): off; try 40-60 on a long leg")
    ap.add_argument("--plateau-pct", type=float, default=2.0,
                    help="percent the last --plateau iterations must improve the best count by (default 2)")
    a = ap.parse_args()
    if a.selftest:
        return nfp_selftest(P.board_dir_arg(a.board), a.at, a.off)
    if not a.spec:
        ap.error("the spec is required (or --selftest)")
    sys.stdout.reconfigure(line_buffering=True)   # a killed run's log ends at its last line, not its last buffer
    print(f"router core: {maze.CORE}")
    t0 = time.monotonic()
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
    # The group's budget, BEFORE anything is retired or routed: a spent group is refused here
    # (exit 76, budget.py), and --timeout never runs past what the group has left.
    only_ = {n.strip() for n in a.only.split(",") if n.strip()}
    tok = budget.gate(a.board, S.get("group") or spec_path.stem,
                      [n for n in S["nets"] if not only_ or budget.short(n) in only_ or n in only_],
                      kind="run", matched=bool(S.get("matched")), limit=a.timeout,
                      label=spec_path.name + (f" --only {a.only}" if a.only else ""))
    if tok and (not a.timeout or tok.remaining < a.timeout):
        a.timeout = max(1.0, tok.remaining)
        print(f"budget: --timeout {a.timeout:.0f} s -- what the group has left")
    # Until the first negotiation starts, whatever ends this run ended it before anything was
    # measured: the ledger says so (`free`) and the group's round is not counted (budget.py).
    budget.note("ended before any negotiation (the set-up stopped it: the run's output says why)", ok=False,
                free="nothing was negotiated")
    # An older plan at the output path goes aside NOW, before anything can fail or be killed:
    # whatever ends this run, the path holds this run's plan or nothing (origin.py, 3).  Not
    # when the spec's "plane" reads its zone from that very file -- then the file is an input.
    plan_path = Path(S["plan"])
    plan_path = plan_path if plan_path.is_absolute() else Path(a.board) / plan_path
    zone_in = (S.get("plane") or {}).get("zone")
    retired = None
    if not (zone_in and P.boardsel.in_board(a.board, zone_in).resolve() == plan_path.resolve()):
        retired = origin.retire(plan_path)
        if retired:
            print(f"retired {plan_path.name} -> {retired.name}: an earlier run's plan, moved aside until "
                  "this run has its own (route.py refuses a .stale.json; a plan from this run deletes it)")

    def no_plan():
        """The last line of a run that ends without a plan."""
        st = origin.stale_path(plan_path)
        print(f"no plan at {plan_path}" + (f": the earlier one is {st.name}, which route.py refuses "
                                           "(rename it and its .origin.json back to use it deliberately)"
                                           if st.exists() else ""))

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
    board_sha = origin.sha_file(P.boardsel.one_pcb(a.board))   # BEFORE it is read: the plan is true of this content
    B = P.Board(a.board)
    NFP = nfp_rule(B)            # prelayout.json "nfp_vias": typed on the plan's vias before it is written
    import pcbnew
    import guide
    gates = guide.load(a.board)                       # the board's general_routing_guide.md
    planes = {q["layer"] for q in gates.get("plane_layers", []) or []}
    canon = lambda lid: pcbnew.BOARD.GetStandardLayerName(lid)    # a board may rename its layers
    planes = {canon(B.b.GetLayerID(n)) for n in planes}
    LAYERS = [canon(B.b.GetLayerID(n)) for n in S["layers"]] if S.get("layers") else \
        [canon(l) for l in B.b.GetEnabledLayers().CuStack() if canon(l) not in planes]
    EDGE = float(S["edge"]) if "edge" in S else \
        float((gates.get("edge") or {}).get("min", B.edge_clr))
    print(f"layers {', '.join(LAYERS)}; copper kept {EDGE} mm off the edge")
    # for the ledger, and the wall that may quote it: what this run could use, and the board's rest
    budget.fact(layers=list(LAYERS),
                closed=[l for l in (canon(l) for l in B.b.GetEnabledLayers().CuStack()) if l not in LAYERS])

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

    def own_clr(item, lid):
        """mm: the item's OWN clearance as KiCad's DRC resolves it on that layer -- its net class, a
        pad's or footprint's local override, a custom rule on it alone.  KiCad holds two items apart by
        the stricter of their two clearances; 0 if pcbnew cannot say."""
        try:
            return B.mm(item.GetOwnClearance(lid))
        except Exception:
            return 0.0

    pads, tracks, bvias, padbox = [], [], [], {}
    HOLECLR = B.mm(B.b.GetDesignSettings().m_HoleClearance)      # copper to hole (14_fab), every layer
    H2H = B.mm(B.b.GetDesignSettings().m_HoleToHoleMin)          # a new via's hole to a hole
    holes = []          # every drilled pad's hole: an obstacle on EVERY copper layer, pad or no pad
    for ref, f in B.fps.items():
        for p in f.Pads():
            drilled = p.GetDrillSizeX() > 0 and p.GetDrillSizeY() > 0
            if drilled:
                # the hole, whatever copper the pad has: an NPTH (no net, often no copper layer at
                # all) or a PTH on a layer it is not flashed on is still a hole there, and DRC holds
                # every other net's copper the board's copper-to-hole from its wall
                c_, sz_ = p.GetPosition(), p.GetDrillSize()
                rx, ry = B.mm(sz_.x) / 2, B.mm(sz_.y) / 2
                a_ = b_ = (B.mm(c_.x), B.mm(c_.y))
                if abs(rx - ry) > 1e-6:                  # a slot: a capsule along its long axis
                    ang = math.radians(p.GetOrientation().AsDegrees())
                    h_ = abs(rx - ry)
                    ux, uy = ((math.cos(ang), -math.sin(ang)) if rx > ry else (math.sin(ang), math.cos(ang)))
                    a_ = (a_[0] - ux * h_, a_[1] - uy * h_)
                    b_ = (b_[0] + ux * h_, b_[1] + uy * h_)
                holes.append(dict(ref=ref, pad=p.GetNumber(), net=p.GetNetname().rsplit("/", 1)[-1],
                                  npth=p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH, a=a_, b=b_, r=min(rx, ry)))
            if not list(p.GetLayerSet().CuStack()):
                # a paste / mask aperture with no copper (a BergStak plug's stencil pads stacked on
                # its real lands): not an obstacle -- taken as copper it sealed every J2 land.  A
                # copperless pad WITH a drill (an NPTH defined on the mask layers only) is its hole
                # alone, stamped from `holes` below
                continue
            bb = p.GetBoundingBox()
            cu_ = list(p.GetLayerSet().CuStack())
            lid_ = (pcbnew.B_Cu if f.IsFlipped() else pcbnew.F_Cu) if (pcbnew.F_Cu in cu_ or pcbnew.B_Cu in cu_) \
                else cu_[0]
            rec = dict(ref=ref, pad=p.GetNumber(), net=p.GetNetname().rsplit("/", 1)[-1], obj=p,
                       clr=own_clr(p, lid_),
                       tht=p.GetDrillSizeX() > 0, round=p.GetShape() == pcbnew.PAD_SHAPE_CIRCLE,
                       cu="B.Cu" if f.IsFlipped() else "F.Cu",          # an SMD pad's own side
                       L=B.mm(bb.GetLeft()), T=B.mm(bb.GetTop()), R=B.mm(bb.GetRight()), B=B.mm(bb.GetBottom()),
                       x=B.mm(p.GetPosition().x), y=B.mm(p.GetPosition().y))
            pads.append(rec)
            padbox[(ref, p.GetNumber())] = rec
    for t in B.b.GetTracks():
        n = t.GetNetname().rsplit("/", 1)[-1]
        if t.Type() == pcbnew.PCB_VIA_T:
            # a via whose unconnected inner pads the fab removes (UNCONNECTED_LAYER_MODE_REMOVE_*,
            # 14_fab's non-functional pads) is only its HOLE on an inner layer it is not flashed on:
            # "hole" carries drill / 2 + the board's copper-to-hole, and the field loop below uses it
            # there instead of the 0.4 pad (board1 R2: the 1.0 mm gap between two U1 vias held two
            # In2 escapes in KiCad's DRC but not in the grid)
            unfl = {canon(L_) for L_ in B.b.GetEnabledLayers().CuStack()
                    if L_ not in (pcbnew.F_Cu, pcbnew.B_Cu) and not t.FlashLayer(L_)}
            bvias.append(dict(net=n, x=B.mm(t.GetPosition().x), y=B.mm(t.GetPosition().y),
                              r=B.mm(t.GetWidth(pcbnew.F_Cu)) / 2, o="on the board", unfl=unfl,
                              clr=own_clr(t, pcbnew.F_Cu),
                              hole=B.mm(t.GetDrillValue()) / 2 + B.mm(B.b.GetDesignSettings().m_HoleClearance)))
        else:
            tracks.append(dict(net=n, layer=canon(t.GetLayer()), a=(B.mm(t.GetStart().x), B.mm(t.GetStart().y)),
                               b=(B.mm(t.GetEnd().x), B.mm(t.GetEnd().y)), w=B.mm(t.GetWidth()), o="on the board",
                               clr=own_clr(t, t.GetLayer())))
    # each net's class clearance, off its board copper (the smallest own clearance of its pads and
    # tracks: a pad's local override, a mounting hole's 0.6, only raises its own pad) -- for the
    # spec's copper below, which has no pcbnew item to ask
    NETCLR = {}
    for it_ in pads + tracks + bvias:
        if it_.get("clr", 0) > 1e-9 and it_["net"]:
            NETCLR[it_["net"]] = min(NETCLR.get(it_["net"], 9.0), it_["clr"])
    # the spec's own extra copper (ground and supply vias, pad links) is on the board
    # as far as the router is concerned -- else it routes through where they will go
    for e in S.get("extra_tracks", []):
        for a_, b_ in zip(e["pts"], e["pts"][1:]):
            tracks.append(dict(net=e["net"].rsplit("/", 1)[-1], layer=e["layer"], a=tuple(a_), b=tuple(b_),
                               w=float(e["width"]), o="the spec's extra_tracks",
                               clr=NETCLR.get(e["net"].rsplit("/", 1)[-1], 0.0)))
    for e in S.get("extra_vias", []):
        if "pad" not in e or "at" not in e:       # board1 NORTHGAP r2: "size" gave a KeyError, not a refusal
            sys.exit(f"spec: an extra_vias entry needs 'net', 'at' [x, y] and 'pad' (the via's diameter, mm); got {sorted(e)}")
        bvias.append(dict(net=e["net"].rsplit("/", 1)[-1], x=e["at"][0], y=e["at"][1], r=float(e["pad"]) / 2,
                          o="the spec's extra_vias", clr=NETCLR.get(e["net"].rsplit("/", 1)[-1], 0.0)))
    # a LATER group's copper, reserved: an obstacle here, never written to this plan
    # (reserve the later groups' tracks, not just their vias)
    for e in S.get("reserve_tracks", []):
        for a_, b_ in zip(e["pts"], e["pts"][1:]):
            tracks.append(dict(net=e["net"].rsplit("/", 1)[-1], layer=e["layer"], a=tuple(a_), b=tuple(b_),
                               w=float(e["width"]), o="reserved by the spec's reserve_tracks",
                               clr=NETCLR.get(e["net"].rsplit("/", 1)[-1], 0.0)))
    for e in S.get("reserve_vias", []):
        bvias.append(dict(net=e["net"].rsplit("/", 1)[-1], x=e["at"][0], y=e["at"][1], r=float(e["pad"]) / 2,
                          o="reserved by the spec's reserve_vias", clr=NETCLR.get(e["net"].rsplit("/", 1)[-1], 0.0)))

    # every copper PCB_SHAPE drawn on the board (a net-tied polygon: a bus head, a star, a taper,
    # L105.2 / C105.1's NVCC_DRAM patch) is copper like a track -- not a zone, so the pour loop below
    # never saw it, and a new via landed inside one (board1 R_ETHLED: SPEED's via at (35.3, 6.6) in
    # the F.Cu NVCC_DRAM polygon, DRC shorting_items).  Read as padvias.py reads them, outline as
    # pcbnew makes it (TransformShapeToPolygon), its distance from a point by SquaredDistance.
    ERR = pcbnew.FromMM(0.001)
    shapes = []
    for d in B.b.GetDrawings():
        try:
            if d.Type() != pcbnew.PCB_SHAPE_T or not d.IsOnCopperLayer():
                continue
        except Exception:
            continue
        ps = pcbnew.SHAPE_POLY_SET()
        d.TransformShapeToPolygon(ps, d.GetLayer(), 0, ERR, pcbnew.ERROR_INSIDE)
        if ps.OutlineCount() == 0:
            continue
        bb = d.GetBoundingBox()
        shapes.append(dict(net=d.GetNetname().rsplit("/", 1)[-1], layer=canon(d.GetLayer()), ps=ps,
                           clr=own_clr(d, d.GetLayer()),
                           bb=(B.mm(bb.GetLeft()), B.mm(bb.GetTop()), B.mm(bb.GetRight()), B.mm(bb.GetBottom()))))

    def shape_dist(s_, a_, b_=None):
        """mm from a point (or a segment a-b) to a shape's copper; 0 inside it."""
        pa = pcbnew.VECTOR2I(pcbnew.FromMM(a_[0]), pcbnew.FromMM(a_[1]))
        if b_ is None or math.dist(a_, b_) < 1e-9:
            return B.mm(int(round(s_["ps"].SquaredDistance(pa) ** 0.5)))
        pb = pcbnew.VECTOR2I(pcbnew.FromMM(b_[0]), pcbnew.FromMM(b_[1]))
        return B.mm(int(round(s_["ps"].SquaredDistanceToSeg(pcbnew.SEG(pa, pb)) ** 0.5)))

    def shape_name(s_):
        L_, T_, R_, B_ = s_["bb"]
        return (f"the net-tied copper polygon of {s_['net'] or 'no net'} on {s_['layer']} "
                f"(x {L_:.3f}..{R_:.3f}, y {T_:.3f}..{B_:.3f})")

    # the spec's own copper inside ANOTHER net's polygon is a short the router cannot route round:
    # refuse it now, by name (a through via is copper on every layer, whatever layer the polygon is on)
    bad_ = []
    for key_ in ("extra_vias", "reserve_vias"):
        for e in S.get(key_, []):
            n_ = e["net"].rsplit("/", 1)[-1]
            for s_ in shapes:
                if s_["net"] == n_:
                    continue
                g_ = shape_dist(s_, e["at"]) - float(e["pad"]) / 2
                if g_ < 1e-6:
                    bad_.append(f"{key_}: {n_}'s via at ({e['at'][0]:.3f}, {e['at'][1]:.3f}) (pad {e['pad']}) "
                                f"{'lies inside' if g_ < -float(e['pad']) / 2 + 1e-6 else 'touches'} {shape_name(s_)}")
                elif g_ < maze.PADCLR - 1e-6:
                    print(f"warning: {key_}: {n_}'s via at ({e['at'][0]:.3f}, {e['at'][1]:.3f}) is {g_:.3f} mm "
                          f"from {shape_name(s_)}, under the spec's padclr {maze.PADCLR} (DRC decides)")
    for key_ in ("extra_tracks", "reserve_tracks"):
        for e in S.get(key_, []):
            n_ = e["net"].rsplit("/", 1)[-1]
            lay_ = canon(B.b.GetLayerID(e["layer"]))
            for s_ in shapes:
                if s_["net"] == n_ or s_["layer"] != lay_:
                    continue
                for a_, b_ in zip(e["pts"], e["pts"][1:]):
                    if shape_dist(s_, tuple(a_), tuple(b_)) - float(e["width"]) / 2 < 1e-6:
                        bad_.append(f"{key_}: {n_}'s track {a_} -> {b_} on {lay_} touches {shape_name(s_)}")
    if bad_:
        print("\n".join(bad_))
        sys.exit(f"{spec_path.name}: {len(bad_)} piece(s) of the spec's own copper short into another net's "
                 "net-tied copper polygon (above) -- move them out of it (a PCB_SHAPE with a net is copper, "
                 "not a zone: nothing refills round it)")

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
    G.static_exact = bool(S.get("static_exact"))     # a BGA field's diagonal lanes: maze.Grid.static_exact

    # KiCad holds two items apart by the STRICTER of their two clearances.  Between the nets routed
    # here: the spec's "clearance", raised to the strictest class among them.  To a static item (a
    # foreign pad, track, via, polygon): the spec's "padclr", raised to that item's own clearance and
    # to the routed nets' -- one padclr for all of them took an SE_50 pad (0.2032) at 0.1016, and
    # PHY_LED1 came 0.149 from U200.19 (board1 P_PHY2).  Inside a field its area rule holds, as before.
    RCLR = max([NETCLR.get(n, 0.0) for n in nets] + [0.0])
    if S.get("own_clearance") is not None:
        # a custom DRC rule sets the group's OWN nets apart at less than their class (board1 14_fab's
        # 'fab: eMMC bus, own lines', 0.1016 between two EMMC_* nets): the spec says so and the raise to
        # the class is skipped between the routed nets only -- to foreign copper RCLR still holds (SC),
        # and route.py check's DRC is the proof that the rule exists
        maze.CLR = float(S["own_clearance"])
        print(f"clearance between the routed nets {maze.CLR} (spec 'own_clearance'; their class {RCLR} "
              "holds to everything else)")
    elif RCLR > maze.CLR + 1e-9:
        print(f"clearance between the routed nets {maze.CLR} -> {RCLR}: the strictest class among them "
              "(KiCad holds the stricter of two nets' clearances)")
        maze.CLR = RCLR

    def SC(c):
        """The clearance a routed track or via keeps from a static item whose own clearance is c."""
        return max(maze.PADCLR, c or 0.0, RCLR)

    goals_of = {n: [tuple(l["to"]) for l in S["nets"][n]["legs"] if "to" in l] + ([tuple(S["nets"][n]["src_pad"])]
                                                                   if S["nets"][n].get("src_pad") else [])
                for n in nets}
    goal_net = {k: n for n, v in goals_of.items() for k in v}
    fixed = {n: dict(tracks=[], vias=[]) for n in nets}
    # "field" (one) or "fields" (a list): {"ref", "open": [inner layers], "clr", "width"} each --
    # two BGAs joined by one group (a SoC and its DRAM) each have their own area rule
    FO = ([S["field"]] if S.get("field") else []) + list(S.get("fields", []))
    FREFS = {F["ref"] for F in FO}
    # each field's courtyard box, before the pads are laid: another part's pad INSIDE it (a bottom
    # cap under the BGA) is judged at the field's clearance, as KiCad's area rule does
    FCY = []
    for F in FO:
        cy_ = (list(F["box"]) if F.get("box") else B.courtyard(B.fps[F["ref"]])) if F["ref"] in B.fps else None
        if cy_:
            FCY.append((tuple(cy_[:4]), float(F.get("clr", maze.PADCLR)), float(F.get("width", maze.W))))
    for F in FO:
        if F["ref"] not in B.fps:
            sys.exit(f"field: {F['ref']} is not on the board")
    # every static obstacle is LABELLED as it is stamped (G.mark): a net that turns out to be shut
    # in is then told by what -- "via of GND at (23.4, 17.4), reserved by the spec's reserve_vias"
    # -- instead of being left to find it on the board
    def at_(x, y):
        return f"({x:.3f}, {y:.3f})"

    def pad_shape(p, lay_):
        """A pad's copper on a layer as pcbnew draws it (rect, roundrect, chamfered, custom, rotated),
        in the form shape_dist() reads; None for a pad with no copper there."""
        ps = pcbnew.SHAPE_POLY_SET()
        try:
            p["obj"].TransformShapeToPolygon(ps, B.b.GetLayerID(lay_), 0, ERR, pcbnew.ERROR_INSIDE)
        except Exception:
            return None
        return dict(ps=ps, bb=(p["L"], p["T"], p["R"], p["B"]), net=p["net"], layer=lay_) \
            if ps.OutlineCount() else None

    def poly_rows(s_, lay_):
        """A polygon as zero-width tracks: one per grid row through the cells inside it, plus its
        outline -- the form a routed net's fixed copper takes, which the grid grows by the clearance."""
        rows = collections.defaultdict(list)
        for c in G.rect(*s_["bb"], G.step):
            if shape_dist(s_, G.xy(c)) < 1e-9:
                rows[c // G.nx].append(c % G.nx)
        segs = []
        for j_, ii in sorted(rows.items()):
            ii.sort()
            st_ = pv_ = ii[0]
            for i_ in ii[1:] + [None]:
                if i_ is not None and i_ == pv_ + 1:
                    pv_ = i_
                    continue
                y_ = round(G.y0 + j_ * G.step, 6)
                segs.append((lay_, (round(G.x0 + st_ * G.step, 6), y_), (round(G.x0 + pv_ * G.step, 6), y_), 0.0))
                if i_ is not None:
                    st_ = pv_ = i_
        for k_ in range(s_["ps"].OutlineCount()):
            ol = s_["ps"].Outline(k_)
            pts_ = [(B.mm(ol.CPoint(k).x), B.mm(ol.CPoint(k).y)) for k in range(ol.PointCount())]
            segs += [(lay_, a_, b_, 0.0) for a_, b_ in zip(pts_, pts_[1:] + pts_[:1])]
        return segs

    # a pad's REAL outline, for the plan writer's test (cuts_round) and, for a pad of a net routed
    # here, as that net's fixed copper: modelled as the stadium inscribed in its box alone, its
    # rectangle's corners were invisible to the group's other nets (board1 P_PHY2: PHY_CLK125 shorted
    # into Y200.3, LED1 0.067 from R214.1, INT_N 0.076 from R215.1)
    PADSH = collections.defaultdict(list)        # layer -> (shape, track halo): every pad, the writer's test
    pad_poly = collections.defaultdict(list)     # a routed net's own pads' outlines, as fixed copper
    gx0_, gy0_ = G.x0, G.y0
    gx1_, gy1_ = G.x0 + (G.nx - 1) * G.step, G.y0 + (G.ny - 1) * G.step

    def near_window(p, m_):
        return not (p["R"] < gx0_ - m_ or p["L"] > gx1_ + m_ or p["B"] < gy0_ - m_ or p["T"] > gy1_ + m_)
    for p in pads:
        k = (p["ref"], p["pad"])
        lays = LAYERS if p["tht"] else [L_ for L_ in (p["cu"],) if L_ in LAYERS]
        owner = goal_net.get(k) or (p["net"] if p["net"] in fixed and p["ref"] not in FREFS else None)
        G.mark(f"pad {p['ref']}.{p['pad']} ({p['net'] or 'no net'}) at {at_(p['x'], p['y'])}"
               + (": no via in a pad of a net routed here" if owner else ""))
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
                # the stadium stays the net's track end (a source's heading, a trunk to join); the
                # pad's own outline is added as its fixed copper, so the others keep the clearance
                # from its corners too
                for L_ in lays:
                    fixed[owner]["tracks"].append((L_, *seg))
                    sh_ = pad_shape(p, L_) if near_window(p, maze.CLR + maze.W + G.step) else None
                    if sh_ is not None:
                        pad_poly[owner].extend(poly_rows(sh_, L_))
                        PADSH[L_].append((dict(sh_, net=owner), maze.CLR + maze.W / 2))
            continue
        Fp = next((F for F in FO if F["ref"] == p["ref"]), None)
        if Fp is not None:
            # a field's own land: at the field's clearance, for a track of the field's width --
            # the escape between two lands is the field's thin track (U500's 0.1016 between
            # 0.27 mm lands at 0.65 mm pitch), not the group's
            cl = float(Fp.get("clr", maze.PADCLR))
            wf = float(Fp.get("width", maze.W))
            G.block(lays, G.rect(p["L"], p["T"], p["R"], p["B"], cl + wf / 2))
            G.block(["via"], G.rect(p["L"], p["T"], p["R"], p["B"], SC(p["clr"]) + maze.VIA_R))
            continue
        inf = next(((c_, w_) for (bx_, c_, w_) in FCY
                    if bx_[0] <= p["L"] and p["R"] <= bx_[2] and bx_[1] <= p["T"] and p["B"] <= bx_[3]), None)
        if inf:
            G.block(lays, G.rect(p["L"], p["T"], p["R"], p["B"], inf[0] + inf[1] / 2))
            G.block(["via"], G.rect(p["L"], p["T"], p["R"], p["B"], SC(p["clr"]) + maze.VIA_R))
            continue
        c_ = SC(p["clr"])
        G.block(lays, G.rect(p["L"], p["T"], p["R"], p["B"], c_ + maze.W / 2))
        G.block(["via"], G.rect(p["L"], p["T"], p["R"], p["B"], c_ + maze.VIA_R))
        if near_window(p, c_ + maze.W + G.step):
            for L_ in lays:
                sh_ = pad_shape(p, L_)
                if sh_ is not None:
                    PADSH[L_].append((sh_, c_ + maze.W / 2))
    for F in FO:
        bx = [p for p in pads if p["ref"] == F["ref"]]
        # the field is the part's COURTYARD: KiCad's area rule holds where both items intersect it
        # (intersectsCourtyard), so a field grown past it lets the thin clearance out where DRC
        # applies the class's.  No courtyard: the pads' extent + 0.3, as before.
        # "box": [x0, y0, x1, y1] narrows the field to the ball array when the courtyard is far larger
        # (an eMMC's 11.5 x 13 mm body round a 5 x 5 mm array): inside it the thin escape, outside it the
        # group's width at its class clearance -- else the whole run inside the courtyard is written thin
        # and breaks a neck-length cap (board 1's R2: 9.3 mm against #39's 4 mm)
        cy = list(F["box"]) if F.get("box") else B.courtyard(B.fps[F["ref"]])
        if cy:
            F["_box"], F["_m"] = tuple(cy[:4]), 0.0
        else:
            F["_box"], F["_m"] = (min(p["L"] for p in bx), min(p["T"] for p in bx),
                                  max(p["R"] for p in bx), max(p["B"] for p in bx)), 0.3
        F["_clr"] = float(F.get("clr", maze.PADCLR))
        # "width": the track the escapes inside the field really are (thinner than the group's).
        # The grid carries one track width, so inside the field every obstacle's halo is shrunk by
        # the difference instead: a lane between two dog-bones that a thin track clears at the
        # field's clearance is walled shut for a modelled full-width one.
        F["_shrink"] = (maze.W - float(F.get("width", maze.W))) / 2

    def field_of(x, y):
        for F in FO:
            fL, fT, fR, fB = F["_box"]
            m = F["_m"]
            if fL - m <= x <= fR + m and fT - m <= y <= fB + m:
                return F
        return None

    def in_field(x, y):
        return field_of(x, y) is not None

    if FO:
        # between the nets routed here, inside a field its own rule holds too (BGA_ESCAPE_U1's
        # 0.0889 between two escapes, not the group's 0.2032): the grid's track is W, the
        # field's is narrower, so the halo shrinks by the difference as well
        def clr_at(x, y, kind):
            F = field_of(x, y)
            if F is None:
                return maze.CLR
            return F["_clr"] - (2 if kind == "track" else 1) * F["_shrink"]
        G.clr_at = clr_at
    for t in tracks:
        if t["net"] in fixed:
            fixed[t["net"]]["tracks"].append((t["layer"], t["a"], t["b"], t["w"]))
        else:
            # a track on a layer this group does not route on still blocks VIAS:
            # a through via lands on every layer (static_track skips the layer block itself)
            G.mark(f"track of {t['net'] or 'no net'} on {t['layer']}, {at_(*t['a'])} to {at_(*t['b'])}, {t['o']}")
            # a track that CROSSES a field's edge (another net's escape, laid out to the ring) is cut there:
            # the part inside keeps the field's clearance, the part outside the class's.  Judged whole by its
            # two ends it took the class's clearance all the way in, and shut the one-site gap in a via column
            # that it shares with a second escape (board 1: BT_PCM_CLK's 0.343 halo across the 0.319 mm lane
            # beside it at U1's row 17.5, which DRC passes at the courtyard's 0.1016)
            pieces = [(t["a"], t["b"], None)]
            for F in FO:
                if t["layer"] not in F.get("open", []):
                    continue
                nxt = []
                for a_, b_, f_ in pieces:
                    cut = None if f_ is not None else _clip(a_, b_, F["_box"], F["_m"])
                    if cut is None:
                        nxt.append((a_, b_, f_))
                        continue
                    u0, u1 = cut
                    pt = lambda u: (a_[0] + (b_[0] - a_[0]) * u, a_[1] + (b_[1] - a_[1]) * u)
                    if u0 > 1e-9:
                        nxt.append((a_, pt(u0), None))
                    nxt.append((pt(u0), pt(u1), F))
                    if u1 < 1 - 1e-9:
                        nxt.append((pt(u1), b_, None))
                pieces = nxt
            for a_, b_, f_ in pieces:
                G.static_track(t["layer"], a_, b_, t["w"], clr=f_["_clr"] - f_["_shrink"] if f_ is not None
                               else SC(t.get("clr")))
    board_vias = collections.defaultdict(list)
    for v in bvias:
        board_vias[v["net"]].append((v["x"], v["y"]))
    # "cross_square": [{"layer", "pts", "width"(, "allow": "nonparallel" | "any")}] -- copper on ANOTHER layer one core away that
    # this group's tracks on "layer" may cross only at 90 degrees, never run along (10_layout
    # #31 for board1's In4 / In5): every cell within the two half-widths of a segment admits only
    # the two headings perpendicular to it; at a bend the sets intersect, so nothing crosses there
    HEAD = {(1, 0): 1, (1, 1): 2, (0, 1): 3, (-1, 1): 4, (-1, 0): 5, (-1, -1): 6, (0, -1): 7, (1, -1): 8}
    for e in S.get("cross_square", []):
        lay = canon(B.b.GetLayerID(e["layer"]))
        if lay not in LAYERS:
            continue
        # "nets": [names] -- the mask constrains only those nets (each keeps its own map, merged
        # with the layer's general one); without it, every net routed on "layer"
        if e.get("nets"):
            maps = []
            for nn in e["nets"]:
                m_ = G.xdir_net.setdefault(lay, {}).setdefault(nn, None)
                if m_ is None:
                    m_ = G.xdir_net[lay][nn] = {c_: set(v_) for c_, v_ in G.xdir.get(lay, {}).items()}
                maps.append(m_)
        else:
            maps = [G.xdir.setdefault(lay, {})] + [m_ for m_ in G.xdir_net.get(lay, {}).values() if m_ is not None]
        for a_, b_ in zip(e["pts"], e["pts"][1:]):
            dx_, dy_ = b_[0] - a_[0], b_[1] - a_[1]
            if abs(dx_) < 1e-9 and abs(dy_) < 1e-9:
                continue
            key = (0 if abs(dx_) < 1e-6 else (1 if dx_ > 0 else -1), 0 if abs(dy_) < 1e-6 else (1 if dy_ > 0 else -1))
            h_ = HEAD.get(key)
            if h_ is None or (key[0] and key[1] and abs(abs(dx_) - abs(dy_)) > 1e-3):
                sys.exit(f"cross_square: a segment off the 0/45 lattice {a_} -> {b_}")
            ok_ = {(h_ + 1) % 8 + 1, (h_ + 5) % 8 + 1}  # perpendicular
            if e.get("allow") == "any":
                # a granted overlap (board1 D62): this copper does not constrain the heading at all;
                # how much is shared is the board's own check's business
                continue
            if e.get("allow") == "nonparallel":
                # a granted exception (board1 D61): any heading but along the segment -- a 45-degree
                # crossing is allowed, running over it is not
                ok_ = {k for k in range(1, 9) if k not in (h_, (h_ + 3) % 8 + 1)}
            for c_ in G.seg(tuple(a_), tuple(b_), (float(e["width"]) + W) / 2 - 1e-6):
                for xmap in maps:
                    xmap[c_] = ok_ if c_ not in xmap else (xmap[c_] & ok_)

    # every round static obstacle's exact clearance radius per layer (centre to the routed track's
    # centreline, as its halo was built): the plan writer tests straighten()'s result against them
    ROUND = collections.defaultdict(list)
    for v in bvias:
        if v["net"] in fixed:
            r_ = v["r"]
            F = field_of(v["x"], v["y"])
            if F is not None:
                r_ -= maze.CLR - F["_clr"] + F["_shrink"]   # the halo is r + CLR: inside the field its rule applies
            fixed[v["net"]]["vias"].append((v["x"], v["y"], r_, v["r"] + maze.CLR + maze.VIA_R))
        elif in_field(v["x"], v["y"]):
            # a dog-bone: the field's clearance on its open layers, the full one on the rest
            F = field_of(v["x"], v["y"])
            G.mark(f"via of {v['net'] or 'no net'} at {at_(v['x'], v['y'])}, {v['o']} (in field {F['ref']})")
            for L_ in LAYERS:
                cl = F["_clr"] - F["_shrink"] if L_ in F.get("open", []) else SC(v.get("clr"))
                rr = v["r"] + cl + maze.W / 2
                if (L_ in v.get("unfl", ()) and L_ in F.get("open", [])
                        and v["net"] not in S.get("keep_pads", {}).get(L_, ())):
                    # no pad here, only the hole: copper keeps the board's copper-to-hole from it
                    rr = min(rr, v["hole"] - F["_shrink"] + maze.W / 2)
                G.block_disc([L_], v["x"], v["y"], rr)
                ROUND[L_].append((v["x"], v["y"], rr, v["net"]))
                # a via near the field's edge: past the edge the track is the group's width, so the
                # cells outside the field keep the full halo (board1: a 0.181 B.Cu run 0.066 from a
                # dog-bone 0.3 inside U1's courtyard)
                G.block([L_], {c_ for c_ in G.disc(v["x"], v["y"], v["r"] + SC(v.get("clr")) + maze.W / 2)
                               if not in_field(*G.xy(c_))})
            G.block(["via"], G.disc(v["x"], v["y"], v["r"] + SC(v.get("clr")) + maze.VIA_R))
        else:
            G.mark(f"via of {v['net'] or 'no net'} at {at_(v['x'], v['y'])}, {v['o']}")
            G.static_via(v["x"], v["y"], v["r"], clr=SC(v.get("clr")))
            for L_ in LAYERS:
                ROUND[L_].append((v["x"], v["y"], v["r"] + SC(v.get("clr")) + maze.W / 2, v["net"]))

    # every drilled pad's HOLE, on every layer routed here: the board's copper-to-hole from its wall
    # (DRC's hole_clearance, every copper layer, pad or no pad).  An NPTH whose pad lists no copper
    # layer was skipped whole above, and a PTH's pad halo covers its hole only where padclr plus
    # its annular ring reaches the copper-to-hole; this stamps the hole itself, so neither depends
    # on how the footprint drew the pad.  A routed net's own hole is its own (DRC does not test it).
    # A new via keeps the hole-to-hole from every hole, its own net's included.
    for h in holes:
        G.mark(f"{'NPTH' if h['npth'] else 'PTH'} hole of {h['ref']}.{h['pad'] or '(no number)'}"
               f" ({h['net'] or 'no net'}) at {at_(*h['a'])}")
        if h["net"] not in fixed or not h["net"]:
            F = field_of(*h["a"])
            rr = h["r"] + HOLECLR + maze.W / 2 - (F["_shrink"] if F is not None else 0.0)
            if h["a"] == h["b"]:
                G.block_disc(LAYERS, h["a"][0], h["a"][1], rr)
                for L_ in LAYERS:
                    ROUND[L_].append((h["a"][0], h["a"][1], rr, h["net"]))
            else:
                G.block(LAYERS, G.seg(h["a"], h["b"], rr))
            rv = h["r"] + max(HOLECLR + maze.VIA_R, H2H + float(DRILL) / 2)
        else:
            rv = h["r"] + H2H + float(DRILL) / 2
        G.block(["via"], G.seg(h["a"], h["b"], rv) if h["a"] != h["b"] else G.disc(h["a"][0], h["a"][1], rv))

    # the board's copper polygons (read above): another net's is static copper -- on its own layer a
    # track keeps padclr from it (a field's rule inside a field, on the layers the field opens, as a
    # track there does), and a NEW VIA keeps padclr from it whatever layer it is on (a through via is
    # copper on every layer); a routed net's own is its fixed copper, hard for the others and free
    # for it, laid as rows of zero-width tracks one cell apart plus its outline
    PSHAPE = collections.defaultdict(list)       # layer -> (shape, track halo, net): the writer's test
    fixed_poly = collections.defaultdict(list)   # a routed net's own polygons, as fixed copper
    gx0, gy0 = G.x0, G.y0
    gx1, gy1 = G.x0 + (G.nx - 1) * G.step, G.y0 + (G.ny - 1) * G.step
    ns_ = 0
    for s_ in shapes:
        L_, T_, R_, B_ = s_["bb"]
        m_ = SC(s_["clr"]) + max(maze.W / 2, maze.VIA_R) + G.step
        if R_ < gx0 - m_ or L_ > gx1 + m_ or B_ < gy0 - m_ or T_ > gy1 + m_:
            continue
        ns_ += 1
        lay_ = s_["layer"]
        cand = G.rect(L_, T_, R_, B_, m_)
        dist = {c: shape_dist(s_, G.xy(c)) for c in cand}
        if s_["net"] in fixed and s_["net"]:
            rows = collections.defaultdict(list)
            for c, d_ in dist.items():
                if d_ < 1e-9:
                    rows[c // G.nx].append(c % G.nx)
            segs = []
            for j_, ii in rows.items():
                ii.sort()
                st_ = pv_ = ii[0]
                for i_ in ii[1:] + [None]:
                    if i_ is not None and i_ == pv_ + 1:
                        pv_ = i_
                        continue
                    y_ = round(G.y0 + j_ * G.step, 6)
                    segs.append((lay_, (round(G.x0 + st_ * G.step, 6), y_), (round(G.x0 + pv_ * G.step, 6), y_), 0.0))
                    if i_ is not None:
                        st_ = pv_ = i_
            for k_ in range(s_["ps"].OutlineCount()):
                ol = s_["ps"].Outline(k_)
                pts_ = [(B.mm(ol.CPoint(k).x), B.mm(ol.CPoint(k).y)) for k in range(ol.PointCount())]
                segs += [(lay_, a_, b_, 0.0) for a_, b_ in zip(pts_, pts_[1:] + pts_[:1])]
            # kept apart from fixed[...]["tracks"]: those are read as the net's track ends too (a
            # source's heading, a trunk to join), which a polygon's fill rows are not
            fixed_poly[s_["net"]].extend(segs)
            continue
        G.mark(f"{shape_name(s_)}, on the board -- copper, not a zone: no track through it on its layer, "
               "no via in it on any layer")
        rv_ = SC(s_["clr"]) + maze.VIA_R
        G.block(["via"], {c for c, d_ in dist.items() if d_ * d_ <= rv_ * rv_ + G.eps})
        if lay_ in LAYERS:
            def rt_(c):
                F = field_of(*G.xy(c))
                cl = F["_clr"] - F["_shrink"] if (F is not None and lay_ in F.get("open", [])) else SC(s_["clr"])
                return cl + maze.W / 2
            G.block([lay_], {c for c, d_ in dist.items() if d_ * d_ <= rt_(c) ** 2 + G.eps})
            Fc = field_of((L_ + R_) / 2, (T_ + B_) / 2)
            PSHAPE[lay_].append((s_, (Fc["_clr"] - Fc["_shrink"] if (Fc is not None and lay_ in Fc.get("open", []))
                                      else SC(s_["clr"])) + maze.W / 2, s_["net"]))
    if ns_:
        print(f"{ns_} copper polygon(s) (net-tied PCB_SHAPEs) in the window")

    # the field's pad area: escapes only, and they are laid -- unless the spec opens
    # its inner layers, where the balls have no lands and the dog-bones are the
    # only obstacles
    for F in FO:
        fL, fT, fR, fB = F["_box"]
        shut = [L_ for L_ in LAYERS if L_ not in F.get("open", [])]
        G.mark(f"field {F['ref']}: its pad area, shut on the layers its \"open\" does not list, no new via in it")
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
    vband = {c for c in range(G.nx * G.ny)
             if min(G.xy(c)[0] - o[0], o[2] - G.xy(c)[0], G.xy(c)[1] - o[1], o[3] - G.xy(c)[1]) < EDGE + maze.VIA_R + 1e-6}
    # ... and round every cut-out: the bounding box above is the OUTER outline only, and a
    # route straight across a board's hole is otherwise legal to the grid (board2's R5: the
    # LED and GPIO nets cut across the 48 x 25 mm cut-out). Only cells near a hole are tested.
    for hole in (B.rings or [])[1:]:
        hx0, hy0 = min(p[0] for p in hole), min(p[1] for p in hole)
        hx1, hy1 = max(p[0] for p in hole), max(p[1] for p in hole)
        m = EDGE + max(W / 2, maze.VIA_R) + G.step
        for c in G.rect(hx0, hy0, hx1, hy1, m):
            x, y = G.xy(c)
            d = B.edge_dist(x, y)                       # negative inside the hole
            if d < EDGE + W / 2 + 1e-6:
                band.add(c)
            if d < EDGE + maze.VIA_R + 1e-6:
                vband.add(c)
    G.mark(f"the board edge band (copper {EDGE} mm off the edge and every cut-out)")
    G.block(LAYERS, band)
    G.block(["via"], vband)
    for r in S.get("block", []):
        G.mark(f"the spec's \"block\" rectangle {list(r['rect'])}")
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
                 f"({', '.join(LAYERS)}). A plane layer of the guide is left out by default: name "
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
            G.mark(f"pour of {zn or 'no net'} on {L_} (the board's filled zone)")
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
        G.mark(f"the {pl['net']} plane on {pl['layer']} (the spec's \"plane\": open only in each net's apron "
               "and its \"open\" rectangles)")
        G.block([pl["layer"]], cells)
        nv = 0
        if pl.get("vias") is False:
            # a narrow pour (a top-side rail strip) is cut by a row of antipads as surely as by a
            # track: no new via anywhere in it, its "open" rectangles included (a hop crosses on the
            # plane's layer and changes layer outside); the aprons and the prelaid vias stay usable
            keep = set(plane) - set(cells) - set(ocells)
            for x_, y_, *_r in (v_ for n_ in nets for v_ in fixed[n_]["vias"]):
                keep |= G.disc(x_, y_, 0.151)
            G.block(["via"], set(plane) - keep)
            nv = len(set(plane) - keep)
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
        print(f"{pl['net']} plane on {pl['layer']}: {len(cells)} cells blocked"
              + (f" (vias too: {nv})" if nv else "") + f", {len(ocells)} open at "
              f"{ocost}/cell, aprons for " + ", ".join(aprons))

    for n in nets:
        G.register(n, "fixed", tracks=fixed[n]["tracks"] + fixed_poly.get(n, []) + pad_poly.get(n, []),
                   vias=fixed[n]["vias"])
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
            r, hit, _cap = reach(G, job, LAYERS, cap=0)     # only "does it reach": stop at the first goal cell
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
    pp = Path(S["plan"])
    pp = (pp if pp.is_absolute() else Path(a.board) / pp).with_suffix(".partial.json")

    def write_partial(last, bad, stage, iteration, reach_rows, state, **found):
        """Frozen legs + one iteration of the open leg, for a look (never for a gate).  Written
        whole and renamed over the old one, so a run killed mid-write leaves the previous file.
        found: the diagnosis a failed run ends on ("stuck": maze.Grid.stuck, "blocked":
        [maze.Grid.blocked_end reports]), as data beside the printed block."""
        part = dict(group=S["group"] + "-partial", why="legs that closed, then the last iteration "
                    "of the one that did not", state=state, leg=stage + 1, iteration=iteration,
                    elapsed_s=round(time.monotonic() - t0, 1), closed_legs=stage,
                    contested={n: [[k, *G.xy(c)] for k, c in v] for n, v in bad.items() if v},
                    reach=reach_rows, **found, tracks=[], vias=[], zones=[])
        done = [(n, rv, "closed") for n, legs in out.items() for rv in legs]
        for n, (runs, vias), tag in done + [(n, rv, "open") for n, rv in last.items()]:
            for layer, cells in runs:
                pts = [list(q) for q in G.polyline(cells)]
                if len(pts) > 1:
                    part["tracks"].append(dict(net=full[n], layer=layer, width=W, pts=pts, leg=tag))
            for c in vias:
                part["vias"].append(dict(net=full[n], at=list(G.xy(c)), pad=VIA, drill=DRILL, leg=tag))
        tmp = pp.with_name(pp.name + ".tmp")
        tmp.write_text(json.dumps(part, indent=1) + "\n")
        os.replace(tmp, pp)

    def reach_table(jobs):
        rows = {}
        for j in jobs:
            r, hit, capped = reach(G, j, LAYERS)
            rows[j["net"]] = dict(cells=r, goals_hit=hit, goals=len(j["goals"]), capped=capped)
        return rows

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
                if "to_xy" in legs[li - 1] and isinstance(lay, str):
                    # a waypoint is a joint, not a pad: the leg after it leaves with the heading the leg before
                    # arrived with, so the turn rule holds across it (board1 R1 g1: a 90-degree joint at a to_xy)
                    for L_, a_, b_, _w in fixed[n]["tracks"]:
                        if L_ == lay and math.dist(b_, s) < 0.02 and math.dist(a_, s) > 0.02:
                            hd = heading(a_, b_)
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
                lays_ = [LAYERS.index(r_[4])] if len(r_) > 4 and r_[4] else range(len(LAYERS))
                cost_ = float(r_[5]) if len(r_) > 5 else AVOID      # a rectangle's own price
                cells_ = G.rect(*r_[:4])
                for li_ in lays_:
                    arr_ = per[li_]
                    for c in cells_:
                        arr_[c] += cost_
            # "guide": [[x, y], ...] -- the corridor this net is meant to take (fanout.py layers
            # --guides: the capacity flow's own path, gap by gap).  Every cell farther than
            # "guide_reach" from it costs "guide_cost" more for this net, on every layer: the
            # negotiation then settles the geometry inside a topology that is known to fit,
            # instead of searching for the topology (board1's U1: 139 nets at zero slack).
            gd_ = S["nets"][n].get("guide")
            if gd_ and len(gd_) > 1:
                near_g = set()
                for a_, b_ in zip(gd_, gd_[1:]):
                    near_g |= G.seg(tuple(a_), tuple(b_), float(S.get("guide_reach", 0.35)))
                gc_ = float(S.get("guide_cost", 3.0))
                for arr_ in per:
                    for c in range(G.nx * G.ny):
                        if c not in near_g:
                            arr_[c] += gc_
            if pl:
                for c in range(G.nx * G.ny):
                    if plane_bias[n][c]:
                        per[li_plane][c] += plane_bias[n][c]
            jobs.append(dict(net=full[n] if False else n, src=s, src_layer=lay, goals=leg_goals(leg),
                             bias=per, goal_layer=leg_layer(leg),
                             allowed=set(S["nets"][n].get("layers", LAYERS)) | set([lay] if isinstance(lay, str) else lay) | set(aslist(leg_layer(leg))),
                             start_dir=hd, goal_dirs=leg.get("goal_dirs")))
        if not jobs:
            continue
        print(f"\n--- leg {stage + 1}: {' '.join(j['net'] for j in jobs)}")
        G.last = None                     # a stage that walls on its first pass leaves no "last" of its own
        reach_rows, clock, stopped, itn = {}, [time.monotonic(), 0.0], [], [0]

        def on_iter(it, paths_, bad_, stage=stage, jobs=jobs, reach_rows=reach_rows):
            # reach depends only on the leg's fixed copper, so it is the same every iteration:
            # taken once, from iteration 3 -- a leg that converges sooner never pays for it
            if it >= 3 and not reach_rows and any(bad_.values()):
                reach_rows.update(reach_table(jobs))
            itn[0] = it
            write_partial(paths_, bad_, stage, it, reach_rows, "running")
            now = time.monotonic()
            clock[1], clock[0] = max(clock[1], now - clock[0]), now
            if a.timeout and now + 1.2 * clock[1] > t0 + a.timeout:
                stopped.append(it)
                return f"--timeout {a.timeout:g} s: the next iteration (up to {clock[1]:.0f} s) would not end inside it"
            return None

        # BLOCKED: an end shut in by what this run cannot move is found here, in milliseconds, and
        # named with its wall -- not after a negotiation that can only say "walled in".  Each flood
        # stops at BLOCK_CAP states: a closed region smaller than that is a start point, not a room.
        shut_in = [d_ for d_ in (G.blocked_end(jb_, cap=BLOCK_CAP) for jb_ in jobs) if d_]
        if shut_in:
            for d_ in shut_in:
                print("\n".join(maze.Grid.blocked_lines(d_)))
            print(f"BLOCKED at this leg: {', '.join(d_['net'] for d_ in shut_in)} -- nothing was negotiated")
            write_partial({}, {}, stage, 0, {}, "blocked", blocked=shut_in)
            print(f"wrote {pp} (the legs that closed, and the diagnosis)")
            no_plan()
            # at the first leg the whole run measured nothing: the ledger says so and the round is
            # not counted (budget.py, FREE an epoch); at a later leg the earlier ones were a round
            budget.note(f"blocked (exit 4) at leg {stage + 1} before its negotiation: "
                        f"{', '.join(d_['net'] for d_ in shut_in)} shut in", ok=False,
                        **(dict(free="nothing was negotiated") if stage == 0 else {}))
            return 4
        budget.note(None)                                 # from here on the run is a round

        opts = dict(iters=a.iters, pres0=a.pres0, pres_mul=a.pres_mul, hist_inc=a.hist,
                    hist_decay=a.hist_decay, layer_cost=S.get("layer_cost"), limit=a.limit,
                    rip="full" if a.full else "conflict", corridor=a.corridor or None, jacobi=a.jacobi,
                    stuck=a.stall, plateau=a.plateau, plateau_pct=a.plateau_pct)
        won = None
        if a.seeds > 1:
            # each seed negotiates in a forked child against the same frozen copper; the lowest seed
            # that converges wins, whichever finished first -- so HW_JOBS=1 gives the same plan
            def try_seed(sd, jobs=jobs):
                # in-process (HW_JOBS=1) the seeds share G: each leaves it as it found it -- history
                # costs restored, its nets unrouted -- so serial and forked runs see the same start
                saved = {k: array("f", v) for k, v in G.hist.items()}
                lines = []
                p_ = G.negotiate(jobs, seed=sd, log=lines.append, **opts)
                for jb_ in jobs:
                    G.unroute(jb_["net"])
                for k, v in saved.items():
                    G.hist[k][:] = v
                return p_, sum(1 for ln in lines if "iteration" in ln)
            got = par.pmap(try_seed, [a.seed + k for k in range(a.seeds)], note=f"leg {stage + 1}: seeds")
            for k, (p_, n_it) in enumerate(got):
                print(f"   seed {a.seed + k}: {'converged' if p_ else 'no solution'} after {n_it} iteration(s)")
            won = next(((a.seed + k, p_) for k, (p_, _n) in enumerate(got) if p_), None)
        if won:
            print(f"   seed {won[0]} taken (the lowest that converged)")
            paths = won[1]
            for n_, rv_ in paths.items():
                G.unroute(n_)
                G.lay(n_, *rv_)
            G.last = (dict(paths), {})
            write_partial(paths, {}, stage, 0, reach_rows, "running")
        else:
            if a.seeds > 1:
                print(f"   no seed converged: seed {a.seed} again, with its partial and its stall report")
            paths = G.negotiate(jobs, seed=a.seed, on_iter=on_iter, **opts)
        if paths is None and getattr(G, "last", None) and a.repair and not stopped and not G.stuck:
            # (not when STUCK: the stop is the proof that none of these nets has a way with the
            # rest hard, which is all a repair round tries)
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
            if not reach_rows:
                reach_rows.update(reach_table(jobs))
            for j in jobs:
                r_ = reach_rows[j["net"]]
                print(f"   {j['net']:12s} reaches {r_['cells']}{'+' if r_.get('capped') else ''} cells; "
                      f"{r_['goals_hit']}{'+' if r_.get('capped') else ''} of {r_['goals']} goal cells")
            state, code, found = "stalled", 1, {}
            if stopped:
                state = "stopped"
                print("STOPPED at the timeout")
            elif getattr(G, "plateau", None):
                state, found = "plateau", dict(plateau=G.plateau)
                pl = G.plateau
                print(f"PLATEAU at this leg: best {pl['best']} contested cell(s) at iteration {pl['best_it']}; "
                      f"the last {pl['n']} iterations' best {pl['window_best']} is not {pl['pct']:g} % below it "
                      f"(--plateau {a.plateau}; --plateau 0 runs to --iters / --timeout)")
            elif G.stuck:
                state, code, found = "stuck", 3, dict(stuck=G.stuck)
                print("\n".join(G.stuck_lines()))
                print(f"STUCK at this leg (--stall {a.stall}; --stall 0 runs to --iters)")
            elif G.walled:
                # no path even through the others.  Shut in (a region too big for the test before
                # the negotiation, or closed by an earlier leg's via spacing)?  Then say by what.
                state = "walled"
                d_ = G.blocked_end(next(j for j in jobs if j["net"] == G.walled))
                if d_:
                    state, code, found = "blocked", 4, dict(blocked=[d_])
                    print("\n".join(maze.Grid.blocked_lines(d_)))
                    print(f"BLOCKED at this leg: {G.walled}")
                else:
                    print(f"   {G.walled}: not shut in -- its goal is reachable cell by cell, and the search "
                          + ("ran once more over the whole grid with no expansion limit"
                             if G.walled in G.full else f"ended inside --limit {a.limit}")
                          + " and found no route: it is the 45-degree rule at one of its ends (\"dir\", "
                          "\"goal_dirs\", a start hemmed to one heading).  A higher --limit changes nothing here: a "
                          "net that only lacked expansions is searched without the limit before it is called walled "
                          "(maze.py, _search_full)")
                    print("NO SOLUTION at this leg")
            else:
                print("NO SOLUTION at this leg")
            if last or out or found:          # frozen legs + the last iteration, for a look (never for a gate)
                write_partial(last, bad, stage, itn[0], reach_rows, state, **found)
                print(f"wrote {pp} (the last iteration, overlaps and all)")
            no_plan()
            over = sorted(n for n, v in bad.items() if v)
            budget.note(f"{state} (exit {code}) at leg {stage + 1}, iteration {itn[0]}: {len(over)} net(s) still "
                        f"overlap" + (f" ({', '.join(over[:6])}{' ...' if len(over) > 6 else ''})" if over else ""),
                        ok=False)
            return code
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
                G.mark(f"via spacing round {n}'s via at {at_(x, y)} (laid by leg {stage + 1})")
                G.block(["via"], G.disc(x, y, 0.8))

    def width_runs(cells):
        """A run's cells split by the width they were routed at: a field's own "width" inside it,
        W outside.  The cell where the two meet -- always the one OUTSIDE the field, modelled at
        W -- ends the one and starts the other, so the wide copper never reaches into the field
        (where the grid shrank every halo for the thin track) and the thin one overshoots by a
        cell at most.  Written at W throughout, a field's escapes came 0.080 apart at 0.0889."""
        out, cur, cw = [], [], None
        for c in cells:
            F = field_of(*G.xy(c))
            w = float(F.get("width", W)) if F else W
            if cur and w != cw:
                if cw < w:                     # leaving the field: the thin run takes this first outside cell
                    out.append((cw, cur + [c])); cur = [c]
                else:                          # entering it: the thin run starts on the last outside cell
                    out.append((cw, cur)); cur = [cur[-1], c]
            else:
                cur.append(c)
            cw = w
        if cur:
            out.append((cw, cur))
        return out

    def cuts_round(pts, layer, net):
        """True if a straightened polyline comes nearer a round static obstacle (another net's
        via or round pad) than the radius its halo was built at (0.1 um of rounding)."""
        obs = [o for o in ROUND.get(layer, ()) if o[3] != net]
        for a, b in zip(pts, pts[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
            lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])
            for x, y, r, _n in obs:
                if x < lo_x - r or x > hi_x + r or y < lo_y - r or y > hi_y + r:
                    continue
                t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / L2)) if L2 else 0.0
                if math.hypot(a[0] + t * dx - x, a[1] + t * dy - y) < r - 1e-4:
                    return True
            # ... or another net's copper polygon or pad on this layer nearer than its track halo
            for s_, r, n_ in list(PSHAPE.get(layer, ())) + [(q_, r_, q_["net"]) for q_, r_ in PADSH.get(layer, ())]:
                if n_ == net:
                    continue
                L_, T_, R_, B_ = s_["bb"]
                if max(a[0], b[0]) < L_ - r or min(a[0], b[0]) > R_ + r or \
                        max(a[1], b[1]) < T_ - r or min(a[1], b[1]) > B_ + r:
                    continue
                if shape_dist(s_, tuple(a), tuple(b)) < r - 1e-4:
                    return True
        return False

    def widen_short_legs(pts, w, layer, net):
        """A leg shorter than the track width between two same-sense 45-degree turns is a
        90-degree joint (the angle rule's 'diagonal of a right-angle turn under one track
        width'): a one-cell flat over a via, a one-cell chamfer. Its ends slide back along the
        two legs either side until it is w long -- B' = B - u*s, C' = C + z*s -- kept only
        where no via halo is cut (cuts_round); else the joint stays for the gate to report."""
        q = [list(p_) for p_ in pts]
        unit = lambda a, b: ((b[0] - a[0]) / math.dist(a, b), (b[1] - a[1]) / math.dist(a, b))
        cr = lambda a, b: a[0] * b[1] - a[1] * b[0]
        changed = True
        while changed:
            changed = False
            for i in range(1, len(q) - 2):
                A, B, C, D = q[i - 1], q[i], q[i + 1], q[i + 2]
                L = math.dist(B, C)
                if L >= w - 1e-9 or min(math.dist(A, B), math.dist(C, D)) < 1e-9:
                    continue
                u, v, z = unit(A, B), unit(B, C), unit(C, D)
                c45 = math.sqrt(0.5)
                if abs(u[0] * v[0] + u[1] * v[1] - c45) > 1e-3 or abs(v[0] * z[0] + v[1] * z[1] - c45) > 1e-3 \
                        or cr(u, v) * cr(v, z) <= 0:
                    continue
                s_ = (w - L) / math.sqrt(2) + 1e-4
                if s_ >= math.dist(A, B) - 1e-6 or s_ >= math.dist(C, D) - 1e-6:
                    continue
                B2 = [round(B[0] - u[0] * s_, 4), round(B[1] - u[1] * s_, 4)]
                C2 = [round(C[0] + z[0] * s_, 4), round(C[1] + z[1] * s_, 4)]
                cand = q[:i] + [B2, C2] + q[i + 2:]
                # every segment still on the 0/45 lattice (a rounding or an asymmetric pair of legs
                # once left a 38-degree joint -- the gate's finding, not a fix)
                on45 = all(abs((math.degrees(math.atan2(b_[1] - a_[1], b_[0] - a_[0])) + 360) % 45) < 0.5
                           or abs((math.degrees(math.atan2(b_[1] - a_[1], b_[0] - a_[0])) + 360) % 45 - 45) < 0.5
                           for a_, b_ in zip(cand, cand[1:]) if math.dist(a_, b_) > 1e-6)
                if on45 and not cuts_round(cand, layer, net):
                    q, changed = cand, True
                    n_wide[0] += 1
                    break
        return q

    n_raw, n_wide = [0], [0]
    plan = dict(group=S["group"], why=S["why"], tracks=[], vias=[], zones=[], necks=S.get("necks", []))
    for n, legs in out.items():
        for runs, vias in legs:
            for layer, cells in runs:
                for w, part in width_runs(cells):
                    raw = [list(q) for q in G.polyline(part)]
                    pts = straighten(raw, w)
                    if pts != raw and cuts_round(pts, layer, n):
                        # straighten() drops vertices without seeing obstacles: a jog pulled straight
                        # past a via's halo (board1 RV4.4: 0.1737 from a hole against 0.1778) -- the
                        # router's own polyline is written instead
                        pts = raw
                        n_raw[0] += 1
                    if len(pts) > 3:
                        pts = widen_short_legs(pts, w, layer, n)
                    if len(pts) > 1:
                        plan["tracks"].append(dict(net=full[n], layer=layer, width=w, pts=pts))
            for c in vias:
                x, y = G.xy(c)
                if any(math.dist((x, y), (e["at"][0], e["at"][1])) < 0.05 for e in S.get("extra_vias", [])
                       if e["net"].rsplit("/", 1)[-1] == n.rsplit("/", 1)[-1]):
                    continue              # a layer change on its own prelaid dog-bone (short names both
                                          # sides: a spec keyed by '/Sheet/NET' wrote a second via on
                                          # it -- board2 R5, hole_to_hole 0.0 mm)
                if any(math.dist((x, y), (vx, vy)) < 0.05 for vx, vy, *_ in board_vias.get(n, [])) or \
                        any(math.dist((x, y), tuple(q["at"])) < 0.05 for q in plan["vias"] if q["net"] == full[n]):
                    continue              # ... or on one it already has (a board via, an earlier leg's)
                plan["vias"].append(dict(net=full[n], at=[x, y], pad=VIA, drill=DRILL))
    for extra in S.get("extra_tracks", []):
        plan["tracks"].append(dict(extra, net=full.get(extra["net"], extra["net"])))
    for extra in S.get("extra_vias", []):
        plan["vias"].append(dict(extra, net=full.get(extra["net"], extra["net"])))
    # "nfp" on every via inside an nfp_vias courtyard, BEFORE the plan and its origin record: typed
    # afterwards, it makes the plan stale (board1 R_W300: --accept-stale plan on check and apply)
    print(nfp_line(NFP, *nfp_type(B, plan["vias"], NFP), len(plan["vias"])))
    p = plan_path
    p.write_text(json.dumps(plan, indent=1) + "\n")
    sc = origin.write(p, plan, spec_path, S, B.pcb, board_sha, a.board,
                      by="netgroup.py " + " ".join(sys.argv[1:]))
    print(f"\nwrote {p}: {len(plan['tracks'])} track runs, {len(plan['vias'])} vias"
          + (f" ({n_raw[0]} run(s) left unstraightened: straightening cut a via's halo)" if n_raw[0] else "")
          + (f" ({n_wide[0]} short leg(s) widened to a track width)" if n_wide[0] else ""))
    print(f"wrote {sc.name}: spec {origin.spec_sha(S)[:12]}, board {board_sha[:12]} (route.py tests both before it applies)")
    gone = origin.drop_retired(p)
    if gone:
        print(f"removed {gone.name}: the plan supersedes it")
    if pp.exists():
        pp.unlink()                    # an earlier run's partial would read as this run's state
        print(f"removed {pp.name}: the plan supersedes it")
    budget.note(f"plan written: {len(plan['tracks'])} track run(s), {len(plan['vias'])} via(s)", ok=True)
    return 0


REACH_CAP = 20000


def aslist(layer):
    """A layer, or a list of layers (a through-hole source or goal), as a list."""
    return [layer] if isinstance(layer, str) else list(layer)


def reach(G, job, LAYERS, cap=REACH_CAP):
    """Flood fill from a job's source ignoring the other routed nets and the turn
    rule: separates 'contested' from 'walled in'.  (cells, goal cells hit, capped):
    the flood stops once it holds a goal cell and `cap` cells -- an open net is
    told by "20000+" as well as by its whole window (a big window's flood was most
    of a converging run's time: 12 of 16 s on tests/fixture_pcb.py --kind sparse)."""
    li = {k: i for i, k in enumerate(LAYERS)}
    own = G._own(job["net"])
    sl = job["src_layer"]
    starts = [(G.cell(*job["src"]), li[k]) for k in ([sl] if isinstance(sl, str) else sl)]
    seen, dq, nx, ny = set(starts), collections.deque(starts), G.nx, G.ny
    gls = aslist(job.get("goal_layer", "F.Cu"))
    goals = {(c, li[k]) for c in job["goals"] for k in gls}
    hit = len(goals & seen)

    def free(c, key):
        if G.static[key][c]:
            return False
        f = G.fix[key][c]
        return not (f and not (f == 1 and c in own["fixed"][key]))

    while dq:
        if hit and len(seen) >= cap:          # reaches its goal (some goal cells sit where no flood goes) and is open
            return len(seen), hit, True
        c, l = dq.popleft()
        i, j = c % nx, c // nx
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            ni, nj = i + di, j + dj
            if 0 <= ni < nx and 0 <= nj < ny:
                nc = nj * nx + ni
                if free(nc, LAYERS[l]) and (nc, l) not in seen:
                    seen.add((nc, l)); dq.append((nc, l))
                    hit += (nc, l) in goals
        if free(c, "via"):
            for l2 in range(len(LAYERS)):
                if l2 != l and free(c, LAYERS[l2]) and (c, l2) not in seen:
                    seen.add((c, l2)); dq.append((c, l2))
                    hit += (c, l2) in goals
    return len(seen), hit, False


if __name__ == "__main__":
    sys.exit(main())
