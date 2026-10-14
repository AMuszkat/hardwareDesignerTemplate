---
name: via-links
description: >
  Audit and fix how vias are joined to pads: every via linked out of the pad's
  centre into the via's centre by one straight segment along the pad's axis
  (45 degrees only as the guide allows), and no via nearer any pad than the
  guide's gap -- a rule KiCad's DRC never tests. Triggers on "fix the vias",
  "via too close to the pad", "via touching a pad", "straight via link", "via
  not centred", "via rule", "vialink", "viafix", or a screenshot of a via
  joined to a pad at an angle or off-centre.
---

# Via links: straight, centred, never touching

The rule is in `<board>/general_routing_guide.md` ("Vias and their links"),
and its numbers in the guide's table: `via to pad gap` and `45-degree via link`
(last resort / allowed / never). If the table has no `via to pad gap` row, the
rule is not active on this board: say so and stop — a rule is the handoff's to
give, not yours.

1. The link runs **out of the pad's centre** and **into the via's centre**.
2. It is **one straight segment** along one of the pad's axes (0, 90, 180,
   270 degrees). A straight 45-degree segment only as the guide allows; any
   other angle, or a bent link, is not allowed.
3. A via **never touches a pad**: its copper keeps the gap from every pad, its
   own net's included.
4. **Exception** only when no track or part can be moved to make room:
   declare it in `<board>/routing/vialink_exceptions.json`
   (`{"via": [x, y], "why": "..."}`), and the user reviews it with the group.

KiCad's DRC never tests a via against a pad of its own net, so `place.py drc`
cannot see this rule: `vialink.py` is the only gate, and **every group report
states its count, before → after.** Do not add a `physical_clearance` DRC rule
for it on a board with copper already laid: it reports every site at once and
a DRC full of known reds hides new ones.

## The tools (`tools/copper/`, README there)

```
tools/kpy tools/copper/vialink.py                  # audit; exit 1 on any live site
tools/kpy tools/copper/vianeed.py                  # what blocks each via's places: tracks / via / part / edge
tools/kpy tools/copper/viafix.py [--push]          # a plan: vias moved onto the axis, links re-laid
tools/kpy tools/route/route.py check routing/viafix.json
tools/kpy tools/route/route.py apply routing/viafix.json
```

- **vialink.py** reports `via-touch`, `via-angle`, `via-45` (listed as a
  choice unless the guide says never) and `via-offset`.
- **vianeed.py** first: it ranks each via by what its cheapest place needs —
  free, tracks (a push opens it), a via, a part, the edge — so you know which
  are worth a push, which need a part moved, and which need the user.
- **viafix.py** works through each failing via on the board held in memory:
  finds its pad, tries places on the pad's axes (then the diagonals if
  allowed), checks each on every copper layer (other nets at the stricter
  clearance, holes, the gap to its own net's pads, the edge, rule areas),
  re-attaches the via's other tracks in 0/45 segments, lays the link straight
  and as wide as the widest it replaces, rebuilds connectivity and undoes
  anything that opened a connection. `--push` lets it shove other nets'
  tracks and plain vias aside (never the guide's `frozen` copper or a `via
  field` part). It writes ONE plan: `rips`, `ripvias`, `polyrips`, then the new
  tracks and vias.

## The loop (one reviewed group, like any routing group)

1. `route.py audit` — the counts as they are.
2. `vianeed.py`, then `viafix.py` (`--push` when the ranking says tracks).
   A full `--push` search over ~100 vias takes 20–30 minutes: run it in the
   background; `--only UUID,..` re-runs the accepted list in seconds.
3. `route.py check` on the plan: DRC (parity on), angles, edge, shape (0 new
   sites), vialink (no new site), the short test. Each regression points at
   a move: a new shape site → `--bar UUID:x,y` that place; a net losing
   ground beside → `--skip` that via; a starved thermal → `--avoid UUID:side`.
4. Pictures: `cudump.py` of the board, `viafix.py --save routing/trial.kicad_pcb`,
   `cudump.py --pcb routing/trial.kicad_pcb --guide .`, `crops.py moves`. Crop every fix.
5. `route.py apply` with KiCad closed (Konnect `save_project` first; rerun
   the fixer on the saved board to prove the plan is unchanged), then
   `route.py audit`. Report: vialink before → after, every exception, every
   via left and why. **Stop for the user's review.**
6. What is left is reported by reason. Next: move the blocking tracks, then
   parts (the `placement` skill, the user's call), by hand or in a further
   group. Only then declare an exception.

## Traps

- A via shared by two pads: `viafix` takes only a place on an axis of both;
  otherwise one pad gets its own via, or it is a hand fix.
- A net-tied polygon around a via is wrong once the via moves: `viafix` leaves
  such vias for a hand fix (`--rip-fill` rips the polygon with the link, and
  brings back the notch it filled — the shape gate will say so).
- A move that drags a re-attached track along a round through-hole pad makes
  a slot: `--skip` the via.
- Pushing beside a class with `ground beside` reshapes the pour: judge it on
  the refilled copper (`route.py check` and `audit` do).
- Keep moves short: `--maxmove 1.2` (default) and `--reach 1.0`.
- A decoupling ring whose capacitor rows are closer than via pad + two gaps +
  the clearance has no legal via site between them: that is a placement
  decision for the user, not an exception.
