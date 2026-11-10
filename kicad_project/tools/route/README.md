# `tools/route` — routing, one reviewed group at a time

**Yours**, like `tools/place` and `tools/fp`. A board is routed in the groups
`<board>/ROUTING_PLAN.md` lists, **reviewed at milestones** — fanout and
escape, buses, power, pours, close-out — and a group alone on request (the
board's `REVIEW.md`; the verdict in `handoff/15_reviews.md`). These tools make a group cheap to propose, judge and undo.
The workflow is the `routing` skill.

    tools/kpy tools/route/route.py status --board board1 [--nets]
    tools/kpy tools/route/route.py check  --board board1 routing/R1_power.json
    tools/kpy tools/route/route.py apply  --board board1 routing/R1_power.json   # KiCad closed
    tools/kpy tools/route/route.py rip    --board board1 R1                      # KiCad closed
    tools/kpy tools/route/route.py render --board board1 [--out DIR]
    tools/kpy tools/route/route.py angles --board board1 [--nets]
    tools/kpy tools/route/route.py edge   --board board1
    tools/kpy tools/route/route.py audit  --board board1                      # every rule of the guide, on the board
    tools/kpy tools/route/route.py rules  --board board1                      # what the guide switches on
    tools/kpy tools/route/netgroup.py --board board1 routing/R3_spec.json
    tools/kpy tools/route/decap.py    --board board1 --group R2 --refs C9,C10 --plan routing/R2_decap.json
    tools/kpy tools/route/fanout.py plan   --board board1 --ref U1 --via 0.4,0.2 --clearance C --width W ...
    tools/kpy tools/route/fanout.py layers --board board1 --ref U1 --vias placement/vias_U1.json --layers L.json
    tools/kpy tools/route/pour.py     --board board1 [--try NET:LAYER=zone.json] [--over 'GLOB:LAYER'] [--section MM --section-net NET --from .. --to ..]
    tools/kpy tools/route/stitch.py   --board board1 --plan routing/R6_pours.json --net GND --pitch 5 [--pads] --out routing/R6_gnd.json
    tools/kpy tools/route/prelayout.py --board board1 [--spec routing/prelayout.json]   # before the first group
    tools/kpy tools/route/view.py     --board board1 X0 Y0 X1 Y1 OUT [--plan plan.json] [--vias model.json] [--contested netgroup.log]

`tools/kpy` only (they import `pcbnew`). `apply` and `rip` need KiCad
**closed**; everything else reads the saved file. `--board` takes `boardN`, the
folder name or a path; left out, it is this session's board (`HW_BOARD`), and
another session's board is refused. A plan, spec or zone file is taken as
typed, else inside the board folder (`routing/R1.json`).

## The gate is KiCad's DRC, not arithmetic of ours

`check` writes the plan into a **scratch copy** of the board and runs KiCad's
own DRC on it, schematic parity on. Any violation that is not already in the
baseline fails the plan. The real board is never touched.

**One kicad-cli run per check.** The baseline (the board as it is) is
`tools/place/drccache.py`'s: a report cached under `routing/.drc_cache/`,
keyed by the sha256 of every file the DRC reads (the board, project, rules,
every schematic sheet, the library tables, `lib/`'s symbols and footprints,
kicad-cli itself). The same board state is never run twice — the second
`check` of a board, `apply`'s "before" count and `audit` all read it — and a
miss runs concurrently with the scratch copy's DRC. stderr says which
(`drc: ... from the cache`); stdout is what it was. `HW_DRC_CACHE=0` turns it
off; the folder is safe to delete. `audit` runs `place.py drc`'s summary and
`pour.py`'s report in-process, on the board it already loaded.

**`check` counts every pour's UNTIED islands** on the scratch copy against the board: a piece
beside the largest that holds no via or through-hole pin of its net (`pour.py` prints them).
A plan that adds one fails (`RULE pour: ... untied island(s)`). A GND pour that tracks split is
legal when every piece is stitched, so raw island counts failed a sound R6. DRC
passes a pour in pieces, and before this only `audit` saw it -- after `apply`
(board2 R5: a CHECK PASS plan cut the +5V strip; ripped and re-routed).

⚠ **The open count is KiCad's connectivity, not the DRC report.** `kicad-cli
pcb drc` lists unconnected items only up to a cap (499 on board 1 with 1264
open), so a count read from it stops moving on a big board: 22 nets routed end
to end once read "0 closed". `check` and `apply` count with pcbnew
(`BuildConnectivity`, `GetUnconnectedCount`). A gate plan that lays a fanout
model's vias unconnected (board 1's `esc_gate.py`) raises the count by those
vias: compare a plain copy when the number matters.

⚠ **One thing KiCad's DRC cannot see, and the gate checks itself: a short that
KiCad renames away.** When a board is saved, `pcbnew` gives a track or via
that touches a pad of *another* net — with no pad of its own net in the same
piece of copper — that pad's net. A GND stub landed on a +3V3 pad is saved as
+3V3: one net, no `shorting_items`, the open count unmoved. So `check`,
`apply` and `rip` read the saved file back and compare every track's and via's
net with what was written; a difference is reported as `RULE short:` and fails
the plan (`apply` restores the board).

That indirection is the whole point. A hand-written clearance test that uses
*the track's own* net class misses that KiCad enforces the **stricter of the two
nets'** clearances — a Default track beside a Power pad needs the Power
clearance. Ask KiCad.

## The rules: `<board>/general_routing_guide.md`

DRC cannot express some rules a board lives by — a plane layer that must stay
unbroken, a class that takes no via, a via linked straight and centred, a
track that must not cross a pad's corner. Those live in ONE file per board,
`general_routing_guide.md` (every new board gets the scaffold): prose for the
eye, and a table — `rule | setting | source` — that `tools/route/guide.py`
reads. **Every row names the handoff line it came from**, because a rule
nobody can trace is a rule nobody can retire. **A row left out is simply not
run.** `route.py rules` prints what is active; a misspelt row name is an error
listing the names the tools know.

| row | setting | what `check` fails / `audit` reports |
|---|---|---|
| `joints` | `0/45` | a joint between two track segments that is not 0 or 45°; a right-angle turn whose diagonal is shorter than one track width; an arc. Entries into a pad or a via are free; a tee is a joint, so a branch leaves at 45° |
| `copper to edge` | `0.5 mm` | new copper (tracks, vias, zone outlines) closer than that to Edge.Cuts. A number STRICTER than Board Setup's copper-to-edge — the fab's minimum plus its outline tolerance — since DRC enforces Board Setup's |
| `via to pad gap` | `0.15 mm` | a via nearer any pad than the gap (its own net's included — DRC never tests that), or linked to its pad other than straight, centre to centre, on the pad's axis (`tools/copper/vialink.py`) |
| `45-degree via link` | `last resort` / `allowed` / `never` | a straight 45° link is listed as a choice, free, or a site |
| `copper shape` | `on` | a plan that adds a shape site: corner / wide / offset pad entry, overshoot, bulb, stacked track, wedge, notch, polygon neck, polygon solid into a pour (`tools/copper/shape.py`) |
| `class width` | `on` | a track whose width is not its class's (outside a via field with `widths`) |
| `thermals` | `outer: all pads; inner: THT only` | the thermal policy a plan's `"thermals": true` types into every pour |
| `plane layer` | `In1.Cu: GND` | any track on that layer; a zone there not of the plane's net |
| `no vias` | `Class (NET: 1)` | a net of the class with more vias on the whole board than its cap (0 for the class, the named exceptions' own) |
| `class layers` | `Class: F.Cu (except NET)` | a track of the class on any other layer |
| `via clear of class` | `PAIRS: 0.625 mm (except GND)` | a via of another net (the plan's in `check`, every one on the board in `audit`) whose centre is nearer a class track's centreline than that. The case: tracks may run beneath a pair on a layer below its reference plane, but a via's antipad in the plane opens it under the pair. DRC cannot see it, because the antipad is on another layer and the nets never touch. Set it to via pad / 2 + plane clearance + the class track's half width. board2 Q10: 0.3 + 0.2 + 0.125; R5's routes had been checked by hand until RV21.5 asked for it as a gate |
| `keep-out` | `name: Class` | a track of that class touching the `placement.json` keep-out of that name |
| `via field` | `U1: 0.45/0.15 lattice, widths 0.1016-0.127, margin 0.4` | inside the part's pad field: a via not exactly that size, or off the pad lattice; a track whose width is outside the range. Outside it: a via of exactly that size. The copper tools skip the field |
| `ground beside` | `Fast: GND within 0.6 mm` | reported by `audit` and `check`: mm of the class's track with ground on 2 / 1 / 0 sides, before → after |
| `frozen` | `class X, net Y` | copper `viafix.py --push` never moves |
| `thermal pad` | `U5.5: solid` | one pad's own connection to the pour |

A plan declares its exceptions, each with a reason printed every run and
reviewed with the group:

- `"necks": [{"net", "width", "why"}]` — a width the class does not give;
- `"edge_ok": [{"net", "layer"?, "min", "why"}]` — a closer edge distance;
- `"accept": [{"type", "why"}]` — a DRC violation type the group produces on
  purpose (a fanout group ends every escape in free board, which DRC reports
  as unconnected ends). Never a way to wave away a real error; a declared type
  that does not occur is reported so it gets deleted.
- a via that cannot be linked by the rule: `routing/vialink_exceptions.json`
  (`{"via": [x, y], "why"}`), listed by every audit, never counted.

Some rows are *also* expressed as `.kicad_dru` rules by `board_setup.py` (a
no-via class, an area clearance). Then DRC and the guide agree independently,
and if they ever disagree, something moved.

`route.py audit` runs every active rule on the board as it is — DRC through
`place.py drc`, joints, edge, shape, via links, ground beside, pour islands —
and ends `AUDIT PASS` or `AUDIT FAIL (...)`. It is the line a group report
quotes, before and after.

## The plan file

```json
{"group": "R1", "why": "one line for the reviewer",
 "tracks": [{"net": "+3V3", "layer": "F.Cu", "width": 0.4,
             "pts": [[151.0, 128.0], [151.0, 134.0], [153.5, 136.5]]}],
 "vias":   [{"net": "+3V3", "at": [151.0, 130.0], "pad": 0.6, "drill": 0.3}],
 "zones":  [{"net": "GND", "layer": "In1.Cu", "poly": [[100, 100], [174, 100], [174, 140], [100, 140]],
             "clearance": 0.2, "min_width": 0.15, "priority": 1, "pad_connection": "solid"}],
 "necks":  [{"net": "Net-(U2-FB)", "width": 0.15,
             "why": "0.5 mm between U2 pad 4 (Power) and R9's GND pad"}]}
```

Copper a plan takes away or draws beside tracks (the copper fixers write
these; a hand plan may too):

```json
 "rips":     [{"net": "/BUS0", "layer": "B.Cu", "pts": [[11.5, 7.6], [18.6, 7.6]]}],
 "ripvias":  [{"net": "/BUS0", "at": [11.5, 7.6]}],
 "polyrips": [{"net": "/BUS1", "layer": "F.Cu", "at": [20.5, 20.5]}],
 "polys":    [{"net": "/BUS1", "layer": "F.Cu", "pts": [[20.5, 19.5], [21.3, 19.5], [21.3, 20.9], [20.9, 21.3], [20.5, 20.9]]}],
 "thermals": true
```

`rips` names a segment by its two ends (either order), `ripvias` a via by its
centre, `polyrips` a net-tied polygon by its first vertex; a rip that matches
nothing fails the plan. `polys` are filled, net-tied copper polygons (the
guide's rule; `tools/copper/poly.py --plan` dry-runs them); `thermals` re-types
every pour's connection to the guide's policy. The order on apply: rips, then
polygons, tracks, vias, zones, thermals, refill, save.

A `tracks` entry is a polyline: consecutive points become segments. Net names
are exactly as KiCad holds them, hierarchical ones included —
`route.py status --nets` prints every net with its class. A zone's
`clearance` defaults to its net's class clearance (never KiCad's 0.5 mm zone
default, which strands caps on islands of their own); `pad_connection` is
`solid`, `thermal`, `tht_thermal` or `none`; `holes` cuts polygons out of it.

## `apply` and `rip`

`apply` re-runs `check`, backs the board up, writes, saves, then **reloads the
saved file and confirms every item is there** before recording the group in
`<board>/routing/<group>.applied.json`. That manifest holds each item's UUID,
which is what makes `rip <group>` exact: it deletes that group's copper and
nothing else, then renames the manifest to `.ripped`. What the group REMOVED
(`rips`, `ripvias`, `polyrips`) is not brought back by `rip` — it is in the
backup `apply` made, and `rip` says so.

`route.py angles` audits the copper already on the board with the same code
`check` runs on the scratch copy (the short-diagonal test no longer depends on
the order segments are stored in, so the two agree). It is a **report** unless
the guide has a `joints` row: without one it prints what it finds and
exits 0. `apply --force` replaces a group: the previous copy's copper is
removed first, by its manifest.

## A plan's origin: a stale plan is refused (`origin.py`)

A plan file does not say which spec it was routed from or which board it was routed
on, so a plan left at its path by an earlier run reads exactly like the result of the
run that just failed (board1's R2: "no solution" after three minutes, and at the
output path a plan from before the spec's track widths were corrected). Three
mechanisms, all in `origin.py` (board-free, standard library):

**The record.** `netgroup.py` writes `<plan>.origin.json` beside its plan: the
spec's path and hash, the board file's hash, the time, the command. A sidecar, not
keys in the plan — the plan's bytes are what they always were.

**The test.** `route.py check` and `apply` run it before any gate, print one line
each for plan, spec and board, and exit **5** on anything not accepted:

    STALE PLAN R2_emmc_In2.json
       plan   R2_emmc_In2.json: as written 2026-11-10 19:02:11
       spec   routing/R2_emmc_In2_spec.json CHANGED since the plan was made (05487e99c9df then, 460bc6ae62ce now): width 0.2 -> 0.25
       board  board1.kicad_pcb: unchanged (8cadc3fc7fc8)
       planned 2026-11-10 19:02:11 by: netgroup.py routing/R2_emmc_In2_spec.json
       nothing applied. Run the planner again on the spec (it replaces the plan), or pass --accept-stale spec to have this file applied as it is

| what | stale when | not stale when |
|---|---|---|
| `spec` | any key that decides the route differs (the refusal names the keys, with both values for a number), or the spec file is gone | it was reformatted, its keys reordered, `1` written `1.0`, its `"why"` reworded, its `"plan"` path or a `"_..."` comment changed |
| `board` | the `.kicad_pcb`'s content differs and route.py did not record how | the file was copied, touched, checked out or restored (same content); or it moved only by recorded `apply`/`rip` steps |
| `plan` | a key the planner wrote differs (a moved point, another file copied over the path) | a key was ADDED by hand (`accept`, `edge_ok`, `marks`) |
| `retired` | the file is a `<plan>.stale.json` | — |

`--accept-stale WHAT` (`spec`, `board`, `plan`, `retired`, comma separated, or
`all`) takes the file all the same; the refusal prints the exact flag. A plan with
no record (hand-written, from `fanout.py` / `pour.py` / `stitch.py` / `decap.py`,
or older than this) gets one `note:` line and is checked as before.

*Why a content hash for the board, not time and size:* a copy to a scratch folder, a
`touch`, a git checkout and a restore from a backup all change the time and keep the
content — false refusals, which teach the flag — and a save that moves one coordinate
can keep the size. Hashing a board file costs milliseconds.

*Why recorded steps are not a refusal:* several plans routed on one board and
applied one after the other is how a bus on three layers is done. Every `apply` and
`rip` appends the board's hash before and after to
`<board>/routing/board_steps.jsonl`; a plan whose board hash leads to the present one
through those steps is taken, the gate names the steps, and `check` judges it against
that copper on the scratch copy — the test that matters. Plans gated one at a time
are still not gated against each other until the first is on the board.

**Retiring.** `netgroup.py` moves a plan already at its output path aside as
`<plan>.stale.json` (its record as `<plan>.stale.origin.json`) when it **starts**, so
a run that fails, hits `--timeout` or is killed leaves **no plan at its path** — its
last line says so — and a run that writes its plan deletes the retired one. Moved,
not deleted: it may be the only copy of a route that took an hour, and nothing has
replaced it yet. Unchanged: rename both files back and it is the plan it was, judged
by its record like any other. (Not retired: a plan the spec's own `plane.zone`
reads.) Two runs on ONE output path: each retires only when it starts, so a run that
fails later never removes the plan another wrote meanwhile.

A plan routed on a **copy** of the board (a what-if in the scratchpad, a race of
variants) keeps a record that fits the real board — the same content is the same
hash — provided its `.origin.json` is copied back WITH it and the spec is at the same
path inside the board folder. Copied back alone it is a plan with no record.

Another planner gets the same guard with two calls: `origin.retire(plan_path)` when
it starts, `origin.write(plan_path, plan, spec_path, spec, pcb, board_sha,
board_dir)` when it writes. `tests/test_plan_guard.py` holds all of it on the fixture
board.

## `netgroup.py` + `maze.py` — the negotiated router, driven by a spec

A group of signal nets from a JSON spec: legs to pads, points or prelaid copper
(`to_path`), escapes and links laid beforehand as `extra_tracks`/`extra_vias`, a
later group's copper kept free as `reserve_*`, an optional `field` (a BGA's pad
field, shut or opened on inner layers), a `plane` with aprons. It writes a plan;
`route.py check` judges it like any other. Every option is in the module
docstring; the layers default to every copper layer that is not a guide
`plane layer`, and the edge band to the guide's `copper to edge` (else Board Setup).

`maze.py` is a negotiated-congestion router (PathFinder) with 45° moves: A* over
(cell, layer, heading); obstacles are *static* (foreign copper, pads, zoning),
*fixed* (a net's own board copper: hard for the others, free for it) or
*routed* (priced, not forbidden, while negotiating). What made it converge is in
its docstring. What the spec has to be told:

- **A differential pair's merge at a 0.5 mm BGA edge is drawn, not negotiated.**
  The grid carries ONE track width, puts the group's clearance between a pair's
  own two lines (no pair gap), and lays no new via inside a `field` -- where the
  courtyard's thinner rule is what lets a merge via stand. Board 1's 14 pairs
  straddling U1's rows A / B (P down its in-pad via, N on top, one via to merge
  each): every pessimistic bound stalled at the courtyard edge, all 28 nets and
  each class alone, even at 0.15 to everything. Drawn by a script at the real
  widths (`board1_module/build/pair_merge.py`: lanes as mitred offsets of one
  spine, `vialink_exceptions` for the merge vias) and gated by `check` with the
  fanout model laid beside (`esc_gate.py`), it closed in a dozen 6-second checks.
  Netgroup still settles the order: count the inversions between the two ends
  first (the routing skill), and fix it with `to_xy` lane legs when a crossing
  needs a layer swap -- negotiation does not find one.
- **A series part on a pair's lane (an AC cap) is measured by its pad span ACROSS the lane.**
  An 0402 turned to sit on a vertical lane presents its pads' LENGTH sideways: 0.46 mm across,
  not the 0.25-0.30 of a straight line, so its centre needs half that plus the clearance plus
  the neighbour's half width from a foreign line (board 1: 0.523 at 0.2032 to a 0.18 line) and
  0.47 from its own partner. Space the verticals from the caps' span first, then draw the lines;
  board 1's PCIe channel was spaced for the lines, the caps failed DRC, and U400 moved 1.5 mm to
  make room (Q32). A cap's courtyard thinner rule applies only when BOTH items are inside it.
- **A pair's P / N tune: survey the room before drawing a bump.** At 45 degrees only, a bump of
  height h gains 2h(sqrt2 - 1) = 0.83h over 2h + top of line, and its top is the diagonal of a
  right-angle turn, so it is at least one track width long (`check`'s angle rule). Each via transit
  counts as the board's thickness, so a P line with two vias more than its N needs about 2.4 mm of
  bumps, and a corridor at the class spacing rarely has it. Board 1 measured each line's outer-side
  room per 0.05 mm (`board1_module/build/pair_tune_room.py --profile NET`). A rise that starts at a
  joint and extends the diagonal before it costs no flat. Two lines' bumps often want the SAME gap
  (DSI_CLK_N's and DSI_D2_N's): one of them bumps the other way, toward a partner still on the
  other layer. Then it asked for a skew budget for the rest (D76) rather than re-laying reviewed copper.
- **A bundle that jogs sideways at 45 degrees jogs staggered.** Lines d apart that all start a 45-degree
  jog at the same x come 0.71 d apart on the diagonal, which breaks the pair gap and the class clearance.
  Start each line's jog 0.414 d (d(sqrt2 - 1)) beyond its neighbour's, on the side the jog runs toward, so
  the diagonals stay d apart. Board 1's PCIe lanes moved 0.1 mm north this way (RV12.2), and DRC failed
  the unstaggered try 8 times.
- **Draw the three-way meetings, route the rest.** A net that must reach an IC,
  a connector pin AND a pull-up does not converge in negotiation when dozens of
  others press on the same corner; lay the pin-to-pull link and route one leg to
  it.
- **Frozen escapes carry the placement they were cut for.** Cut them where the
  parts have not changed, and gate the cut pieces alone before routing on them.
- **Use a slot grid**: pitch = track + clearance. On a finer grid a one-cell
  sidestep is a diagonal shorter than the track width the angle rule asks for.
  The grid is then *strict* (a cell exactly at the clearance distance is
  free) — inferred from `pitch == width + clearance`, or set `"strict"` in
  the spec.
- **A 0.5 mm BGA field with a via in every pad** (board1's U1, in-pad vias at
  0.0889 mm): its only lanes are the diagonals between diagonal vias, 0.354 mm
  from each centre against a 0.333 halo -- and the corner-cell test of a
  45-degree step shuts every one of them, on any grid pitch. Set
  `"static_exact": true` (and `"fix_exact": true` for the routed nets' own
  vias): a round obstacle's halo grows by what a step's chord can cut into it
  and the step is judged by its end cells. The symptom without it: every net
  `reaches` tens of thousands of cells and its goal, and iteration 1 still
  says "walled in" (`reach()` floods without the turn rule).
- **`fix_exact` grew a fixed halo by the sagitta of its clearance part alone**
  (CLR + W/2) -- at a 0.0889 mm field rule three times too much (0.034 mm), which
  shut the 0.354 mm lanes between other nets' in-pad vias ("walled in" at
  iteration 1 only when the spec's clearance was made small). Fixed: the growth
  is `sqrt(R^2 + step^2/2) - R` on each obstacle's whole halo R (`Grid._grow`).
- **Inside a field the nets routed here keep its rule between each other too**:
  netgroup sets `Grid.clr_at` from the fields, so two escapes in a BGA courtyard
  are held 0.0889 apart (BGA_ESCAPE_U1), not the group's 0.2032. Before it, an
  escape group was judged at the class clearance inside the field and could
  never converge.
- **Measure a BGA's escape on its own first**: a copy of the spec whose legs end
  `to_path` on a ring just outside the courtyard (each net anywhere on it). It
  runs in minutes and says whether the field lets N nets out on a layer before
  an hour of routing to the far part says nothing clearer.
- **An escape that will not converge: bound it before re-splitting.**
  `escape_bound.py --ref U1 --layer In2.Cu --spec <escape spec> --vias <fanout
  model> --width W --clearance C [--nfp]` -- a planar cut relaxation over the
  vias' Delaunay triangulation (max-flow): `BOUND k of N`. Below N is a PROOF that no router
  fits those nets on that layer with that via model, and it prints the min cut
  (the balls the wall runs through, how many tracks each gap passes). board1's
  U1 (in-pad vias at 0.5 mm): In2 41 nets, bound 29; the 20-net B.Cu escape the
  router solved bounded at exactly 20 (calibrate against a solved one). Moving
  9 nets to another layer did not move the router's overlap (22 -> 21 nets):
  the CA block sat inside an 11-track cut on EVERY layer, which only the bound
  showed. `--nfp` when the fab removes non-functional pads (other nets' vias
  are holes + copper-to-hole on the layer); `--pad-nets A,B` for a power layer
  that carries a few signals -- its own pours' vias keep their pads there.
  To choose a split over several layers, run `fanout.py layers`: the exact
  most nets the layers take together (a matroid partition over this bound),
  not an anneal -- a pocket of 2-3 balls with one exit each makes per-layer
  capacities not add, which is where a greedy or annealed split stalls. The bound is necessary, not sufficient:
  a split it passes can still stall the grid router at one gap (board1: three
  layers each contested at the same depopulated site) -- move one net per hot
  gap to a layer with room and re-route, and sweep `--seed` / `--hist`.
  A layer confined to one region (board1's In4 / In5 carry signal only toward
  U500) gets `--exit east`: with exits on every side the bound passed In5's 7
  nets; east only it is 6 (two gaps of 3), which is what the router hit.
- **The pitch decides what fits through a gap of 3.** Two parallel 45-degree
  tracks k cells apart are k x pitch / sqrt(2) apart: at 0.0625 four cells
  give 0.1768 against 0.0889 + 0.0889 = 0.1778, so diagonals need five
  (0.221) and a gap the bound passes 3 through takes 2 on the grid. Pick a
  pitch where the straight AND the diagonal steps both land just over the
  track pitch, with the ball pitch a whole number of cells: 0.5 / 11 =
  0.04545 (4 cells straight 0.182, 6 diagonal 0.193) closed board1's In5 at
  6 nets where 0.0625 and 0.5 / 9 stalled at the same two gaps every seed.
  Do not round the pitch (sources must sit on grid points to 1e-4).
- **An escape-only spec keeps the class rules outside the field.** Only the
  BGA's courtyard has the area clearance; a leg to a ring just outside it runs
  at the layer's class width and clearance, or KiCad's DRC fails the ring
  parts (board1: two DQS tracks 0.144 from bottom caps against DDR_DIFF's
  0.2032 that the grid had passed at 0.0889). Gate every layer's escape with
  `route.py check` -- `track_dangling` at the ring is expected, any other DRC
  finding is not.
- **`straighten()` cannot see obstacles; the writer now checks it.** Pulling a
  one-cell jog straight past a via cut its halo by 4 um (board1: 0.1737 from a
  hole against 0.1778, DRC's find, not the grid's). `netgroup` records each
  round obstacle's exact radius as it builds the halo and writes a run's own
  cell polyline where the straightened one comes nearer ("N run(s) left
  unstraightened"). It also widens a leg shorter than the track width between
  two same-sense 45-degree turns -- a one-cell flat or chamfer the angle rule
  reads as a right angle -- by sliding its ends back along the two diagonals,
  kept only where no halo is cut ("N short leg(s) widened").
- **Fixed copper inside a field keeps the field's rule.** A prelaid escape stub (a
  ring ball's top-layer stub, `extra_tracks`) beside another net's took the group's
  clearance (board1 F.Cu: #30's 0.356) even inside U1's courtyard, so two stubs
  0.5 mm apart shut the lane between them; maze now builds every track's halo with
  the field's clearance where it lies (fixed vias were already pre-shrunk).
- **`"cross_square"`: two layers one core apart that may cross only at 90 degrees**
  (`[{"layer", "pts", "width"}]`, the other layer's finished tracks relabelled
  to this one): each cell within the two half-widths of a segment admits only
  the two headings perpendicular to it (a bend admits none), checked on entry,
  exit and a diagonal step's corner cells. Pricing the other layer's copper
  instead does not work -- a square crossing costs more cells than a 38 mm loop
  round the field. Two NEIGHBOURING balls on the two layers wall each other in
  whichever routes first (board1 In4 / In5): keep neighbour pairs on one layer.
- **Gate an escape against the WHOLE fanout.** `route.py check` judges what the
  plan holds; an escape plan holds its own nets' vias, so DRC never measures it
  against the field's other vias (board1 RV4.4: 331 of 358 missing, three
  real clearances at one GND ball). Lay every fanout via in a copy of the plan,
  with `"nfp": true` on a via whose inner pads the fab removes (KiCad's
  "remove unconnected layers", start and end kept), `accept` track_dangling and
  via_dangling with reasons, and gate that copy.
- **`pourreach.py`: a rail via must still reach its pour.** A signal track
  through a power layer's via field can close the last gap between a rail via
  and its pour, and DRC cannot see it (there is no pour yet). `pourreach.py
  --layer --box <field> --vias <models> --pad-nets <the layer's pours> --nfp
  --plans <escapes> --min-width 0.1` floods each pour net's free space from
  outside the field: `POURREACH PASS|FAIL`, one line per via cut off. Run it
  with and without `--plans` -- a via already walled in by other nets' holes is
  the fanout's, not the escape's (board1 In5: 3 of 33 without tracks, 8 with).
  `--corridors --json` gives each via's reach path in the no-plan geometry, to
  reserve (as the rail's `reserve_tracks`) for the vias a route cut off --
  reserving all of them walls the field, every corridor crossing every escape.
- **`bga field: U1, U300` in the guide** makes the copper rules (shape, via
  links) skip the inside of those courtyards, where the neck-down and the
  via-in-pad are the fab rules' business, without `via field`'s refusal of the
  same via outside. And `vialink` never judges a via whose centre is in a pad
  of its own net (via-in-pad: no link).
- **A field is its part's courtyard, and its tracks are written at its width.**
  KiCad's area rule holds only where both items intersect the courtyard, so
  `netgroup` takes the field from the courtyard (the pads + 0.3 only for a
  part with none) and writes each run split at it: the field's `width`
  inside, the group's outside, joined on the first cell outside. Written at
  the group's width throughout, board1's U1 escapes came 0.080-0.085 apart
  against 0.0889; with a field 0.2 past the courtyard the grid gave 0.0889
  where DRC wants the class's 0.2032. A spec's `padclr` is the LARGER of the
  two classes' clearances (SE_50's 0.2032 against a Default pad), not the
  pad's own.
- **Two BGAs in one group** (a SoC and its DRAM): `"fields": [...]`, one per
  part, each at its own area clearance. With one field, the other part's
  dog-bones keep the group's class clearance between them (SE_50's 0.2032
  where U500's courtyard allows 0.1016) and its goals sit in pockets no net
  reaches ("0 of 4 goal cells").
- **A net that starts on a field part's land names it: `"src_pad": [REF, PAD]`** (or ends with
  `"to": [REF, PAD]`). A field's lands are obstacles to every net, its own included, unless the spec says
  which pad is the net's; a bare `"src"` on the land reads "reaches 1 cell -- walled in" (board 1's R2:
  eMMC D0 / D2 out of U300's row A).
- **A field whose courtyard is far larger than its ball array takes `"box"`** (the array + about 0.35).
  Inside the field box the escape is modelled thin and written thin. Without it the whole run inside the
  courtyard came out at the neck width: 9.3 mm against #39's 4 mm cap on board 1's eMMC, whose
  11.5 x 13 mm body surrounds a 6.5 x 6.5 mm array.
- **A goal via off the grid**: end the leg with `"to_path": [[x, y], [x, y]]`
  (the cells within 0.151 of the via), not `"to_xy"` -- that rounds to one
  cell, which may lie in a neighbour's halo.
- **A through via (or a THT pin) is a goal on every layer: say so.** A leg
  with one `"goal_layer"` that arrives on another lays its own via a cell short
  of the prelaid one to change layer -- DRC `hole_to_hole` at 0.0-0.15 mm and a
  via-link site per leg (board2 R5: five of twenty). `"goal_layer": ["In2.Cu",
  "B.Cu", "F.Cu"]` arrives on any; the source side takes a list the same way
  (`"layer"`). Proof: no new `hole_to_hole` and no new vialink site in `check`.
- **The edge band runs round every Edge.Cuts hole too**, not only the outer
  outline (board2's R5: before it, the LED and GPIO nets went straight across
  a 48 x 25 mm cut-out, legal to the grid). Prove it with `route.py check`:
  no `RULE edge` line on a route that passes a cut-out.
- **A plane is blocked on its layer** except an apron round each net's own
  escape end there; priced instead, a net rides through its neighbours' aprons.
- **A narrow pour is cut by vias, not only by tracks: `"vias": false`.** The
  plane block stops tracks on its layer; a via of another net there is only an
  antipad, harmless in a solid inner plane -- but a row of four across a 4 mm
  top-side rail strip cut it in two, J1's 5 V pins on a 6 mm2 island of their
  own, and DRC passed it (board2 R5). `"plane": {..., "vias": false}` keeps
  every new via out of the pour, its `open` strips included (a hop crosses on
  the plane's layer and changes layer outside), the aprons and prelaid vias
  excepted. `route.py check` now fails a plan that adds an island to any pour;
  `pour.py --section` measures what is left across it.
- **`reach()` separates contested from walled in.** When negotiation stalls it
  floods each net's reachable cells; a net that reaches a few dozen against tens
  of thousands for the others is a placement or fanout defect to report, not a
  route to keep negotiating for. The flood stops once it holds a goal cell and
  20 000 cells (printed "20000+", `capped` in the partial): an open net needs
  no more, and flooding a 100 × 70 mm window per net was most of a converging
  run's time (12 of 16 s on `tests/fixture_pcb.py --kind sparse`).
- **"walled in" with a big `reach` is the turn rule, not a wall.** `reach()`
  ignores the 0/45 joint rule; A* does not. A via that reaches 80 000 cells and
  still has "no path even through the others" is boxed in by fixed copper
  through which the only exits need a 90-degree turn (board2's R5: a
  pre-routed lane hard against the via row). Draw the fixed copper with
  `view.py` and free the lane; more `--limit` does not help.
- **The search runs compiled when it can.** `route/_maze.c` is the same A*
  (built by `tools/setup.sh` with the system C compiler into
  `tools/.native/`, loaded through ctypes over the grid's own buffers): the
  SAME routes, bit for bit — the agentic test routes its fixture both ways
  and compares — about ten to forty times faster (16 nets: 52.5 s → 4.5 s
  with `--full`, 21 s → 1.0 s by default; the PCB fixture 20 s → 2.2 s).
  Every run's first line says which core is active (`router core: compiled
  ...` or `python (...)`), and so does `route.py rules`; `HW_MAZE_CORE=python`
  forces the Python one. A route with cross-square headings (`xdir`) uses the
  Python search. No C compiler: the same routes, slower. Halo stamping and
  the overlap count stay in Python: the profile puts the time in the search.
- **Only the overlapping nets are ripped up** after the first iteration
  (conflict-only PathFinder): the others keep their paths, and the rising
  cost of the contested cells moves the overlapping nets round them. When
  the overlap has not fallen for 4 iterations, one iteration re-routes every
  net. `--full` re-routes every net every iteration — the router's result
  before this, for a run that must reproduce an old plan. On the agentic
  fixtures: 16 nets 52.5 s → 21 s in the same 14 iterations; the PCB
  fixture's plan identical, 29 s → 21 s. Deterministic per seed either way.
- **`--seeds N` tries N seeds at once** (forked, `tools/par.py`): every
  seed negotiates the leg against the same frozen copper and the LOWEST seed
  that converges is taken, whichever finished first — the same plan with
  `HW_JOBS=1`, and seed 1's plan when seed 1 converges. When none does, seed
  `--seed` runs again with its partial and stall report. On the PCB fixture
  four seeds cost one run's wall clock (82.7 s serial, 27.4 s forked).
- **`--jacobi` routes an iteration's nets in parallel**, each against the
  congestion the iteration started with. A different convergence, not a
  faster same one: two nets can take the same free lane and meet again next
  iteration. On the 16-net router fixture it took 30 iterations (14
  default) yet finished in 11.8 s on 8 cores (21 s default); on the PCB
  fixture 41 iterations (3 default), 61 s against 21 s. Try it on a big
  group that converges slowly; the same plan with `HW_JOBS=1`.
- **`--corridor MM` bounds each net's search** to the box round its ends and
  its last path, grown by MM (or 30 % of its span), doubled until it routes,
  up to the whole window — so a corridor never walls in a net the window
  would not. It is OFF by default: on the agentic fixtures (16 nets across a
  funnel; the same bundle in a 90 x 70 mm window) A* already searched
  tightly, the plans were identical and the time within noise. Use it when a
  big window's nets hit `--limit` and read as walled in; a corridor can also
  trade a better detour outside it for a worse path inside, so compare.
- **Wait on a background run by its pid, never with `pgrep -f <its command line>`.** A wait loop written
  as `while pgrep -f "netgroup.py ... spec"; do sleep 20; done` carries that string in its own shell's
  command line, matches itself and never ends: board1's three R2 runs were over in 3 minutes and the loop
  sat for 4 h 50. Start it with `tools/bgwait.py run --name N -- ...` and wait with
  `tools/bgwait.py wait N --timeout 100` (below): by pid, bounded, and it prints the CPU and the log's tail.
- **Plans gated one at a time against the fanout are not gated against each other.** `route.py check`
  lays only what its plan holds on the scratch copy, so two plans that each pass can still collide: board1's
  In2 escape proof ran two tracks through a through via of the pairs' plan, and only `apply` (which checks
  on the board as it stands) saw the short. Before a milestone, lay every passed plan on one scratch copy
  -- a merged plan through `check` -- or put the earlier plans' vias and tracks in the later spec's `reserve_*`.
- **Bound a run with `--timeout S`, not a `timeout` prefix.** It stops
  between iterations when the next one (sized by the slowest so far) would not
  end inside S seconds, prints the contested cells and `reach` per net, and
  leaves `<plan>.partial.json` with `"state": "stopped"`. The log is
  line-buffered, so even a killed run's log ends at its last line.
- **Every iteration writes `<plan>.partial.json`** (whole, renamed over the
  old one): the closed legs, the open leg's latest iteration, `contested`
  cells per net, `reach` per net from iteration 3, and `state` — `running`
  (still going, or killed: board2's R5 lost four 90-minute runs before this),
  `stopped`, `stalled`, `walled`. A run that writes its plan removes it. For
  `view.py --plan`, never for a gate.
- **What-if on a scratch board: copy it OUTSIDE kicad_project/** (the session
  scratchpad). `--board` takes a path, but `boardsel` refuses any folder
  inside kicad_project/ that is not a board's (tmpWork/ included). Copy the
  `.kicad_pcb`, `.kicad_pro`, `.kicad_dru`, `fp-lib-table`, `lib/` and
  `routing/`, change the copy with pcbnew (delete a via, move a part), run
  the same spec with `--board <copy>`: the cheapest proof that one obstacle,
  not the router, is what fails a group.
- **A fixed piece's free end goes ON the grid** (`window` origin + k x `pitch`;
  board2: x = 0.05 + 0.2k, y = 0.2k). The leg ending there reaches the nearest
  cell, and an end 0.15 mm off it joins with a jog the shape gate rejects.
- **Where a gap is narrower than the grid can see, draw that piece and route
  the rest.** A 45-degree slot between a board cut-out's corner arc and a
  pair (board2: 1.27 mm between the arc and USB_DN's diagonal, 0.975 needed)
  fits one track exactly, but not on a 0.2 grid with the pair's blocks: the
  router went 120 mm round the board instead. Measure the slot from the arc's
  centre (distance to the line minus the radius), put the track at a
  distance between the two limits, and give the leg its grid-aligned end.
- **No via inside a goal pad** — an 0402's land with a drill in it is a solder
  thief at assembly.

## When `netgroup.py` cannot end: BLOCKED, STUCK, and the exit codes

A run that cannot produce a plan stops early and prints which nets, where, and by
what. Read the block; do not re-run with more `--iters` or another `--seed` first.

| exit | last line | what it means | what to do |
|---|---|---|---|
| 0 | `wrote <plan>` | a plan | `route.py check` |
| 4 | `BLOCKED at this leg: NET` | one END of a net is shut in by what the run cannot move | move or drop the nearest blocker in the list, start the net elsewhere, open a layer to it, widen the window |
| 3 | `STUCK at this leg` | the nets named need the same channel and none has a way round the others | give one another layer, move the via, widen the window |
| 1 | `NO SOLUTION at this leg` / `STOPPED at the timeout` | out of `--iters`, the `--timeout`, or a net with no path that is not shut in (the 45° rule at an end, `--limit`) | the contested cells and the `reach` table, as before |

**BLOCKED** is found before anything is negotiated, in milliseconds per net
(`maze.Grid.blocked_end`): a flood from the net's source and one back from its goal,
over static obstacles and the other nets' fixed copper only. A flood that ends in a
small closed region without meeting the other end has found a blocked start (or end)
point. The block lists the wall, nearest first, and says what each piece IS, because
`netgroup.py` labels every obstacle as it stamps it (`maze.Grid.mark`):

    BLOCKED SOURCE: EMMC_D3 is shut in at its source, near (16.450, 15.150) mm
       from its source it reaches 419 cell(s) (419 on B.Cu) and none of its 29 goal cell(s)
       what closes it, nearest first (distance from the source; cells of the wall):
           0.200 mm  B.Cu    EMMC_D4's own copper  -- 25 cell(s), nearest at (16.250, 15.150)
           0.200 mm  B.Cu    via of EMMC_VDDI at (16.450, 14.650), reserved by the spec's reserve_vias (in field U300)  -- 11 cell(s), ...
           0.354 mm  B.Cu    pad C300.1 (EMMC_VDDI) at (16.430, 14.500)  -- 8 cell(s), ...

(board1's R2 on B.Cu: the reserved via beside the start point, which the session had
found by hand.) The origin of a track or via is one of `on the board`, `the spec's
extra_*`, `reserved by the spec's reserve_*`; the other labels are pads, a field's
shut pad area, the edge band, a `block` rectangle, a pour, the `plane`, and the via
spacing round a via an earlier leg laid. The pre-check floods at most 4000 cells per
end; a net that `negotiate` then finds walled in gets the same test without the cap.

**STUCK** (`--stall N`, default 20; `--stall 0` turns it off) stops a negotiation
that no price can settle. Three conditions, all needed (`maze.Grid.negotiate`,
`stuck`):

1. for N iterations every contested cell had already been contested in the N
   iterations before it — the overlaps recycle the same cells (the same ones every
   time when two nets want one lane; lane after lane when three want two);
2. the overlap count made no new minimum in those N iterations;
3. no net that overlaps now can be laid clear of all the others as they lie — a
   flood with no turn rule and no expansion limit, so "no way" is certain.

1 and 2 alone would stop a net whose clean detour is merely long (the rising price
reaches it at iteration 25–40); a flat overlap COUNT is no test at all (it sits flat
in runs that converge); 3 alone fires in the middle of a reshuffle. The block:

    STUCK at iteration 30: the same 20 cell(s) have been contested for 20 iterations with no new cell and no fewer overlaps, and no net on them has a way round the others
       nets: EMMC_RST_N (9 cell(s)), EMMC_STROBE (11 cell(s))
       In2.Cu: 20 cell(s) in x 22.850 .. 23.100, y 17.350 .. 17.600 mm
       these nets need the same channel: give one another layer / move the via / widen the window

(board1's R2 on In2.Cu: 9 s and 30 iterations; before, 433 iterations and three
minutes to a bare "no solution".) The last line of the block says whether the nets
would pass with the nets that are NOT in conflict lifted — then another `--seed` or
`--full` may still open it — or not even then: the board has too few lanes there.
`--repair` is skipped after a STUCK stop (the stop is the proof it cannot help). The
detector reads the grid and changes nothing: a run that converges gives the same plan
with it on or off.

`<plan>.partial.json` carries the same diagnosis as data: `"state": "blocked"` with
a `"blocked"` list, or `"state": "stuck"` with `"stuck"` (nets, boxes per layer in
mm, every cell). `tests/test_router_stall.py` holds all of this on board-free
fixtures with both search cores.

## The search core says when it is the slow one

`maze.py` runs its search in C (`_maze.c`, built by `kicad_project/tools/setup.sh`
into `kicad_project/tools/.native/`) and falls back to the same algorithm in Python
when the library is missing, was built from an older ABI, or cannot be loaded. The
results are identical; the time is not. So the fallback is never silent: every tool
that imports `maze` prints ONE line on stderr,

    router core: python (about 10x slower) - run kicad_project/tools/setup.sh

and, when the library loads but `_maze.c` is newer than it (a pull that changed the
core, no rebuild),

    router core: compiled, but route/_maze.c is newer than .native/libmaze.so (a stale build) - run kicad_project/tools/setup.sh

`HW_MAZE_CORE=python` (asking for the Python core, as the tests that compare the two
do) prints nothing. `maze.CORE_NOTE` holds the line, or None. `./setup.sh check`
reports the compiled core as REQUIRED on a machine with a C compiler.

## `prelayout.py` — can it be routed, before any copper

The tests the boards learned to run late, run at placement:
`fanout.py` plan and layers per BGA, `escape_bound.py` per BGA and layer,
`pourreach.py` for the rail vias, each fine-pitch row's via sites, and each
connector pair's order inversions. One `PRELAYOUT <test> <target>: PASS |
FAIL | TODO | WAIVED` line each; exit 1 on a FAIL or a TODO. It FINDS the
candidates itself (36+ round pads on a grid; 8+ pads at <= 0.5 mm pitch;
connectors joined by 4+ point-to-point nets), so leaving one out of
`routing/prelayout.json` makes it TODO, not skipped. The spec is in the
docstring.

- **The via-site model** (a fine-pitch row): the links to a deeper via row
  pass between the vias above, so k rows space a row's vias
  `max(pad + clearance, pad + (k-1) * width + k * clearance)` apart, one via
  per that / k along the row, adjacent rows
  `sqrt((pad + clearance)^2 - pitch^2)` apart; every pin's via within
  `lateral` of it. On board2's J1 (0.6 / 0.3 via, 0.2 gap and clearance, a
  0.2 track, a 2.1 mm strip less 0.45 to the cut-out) that is two rows, one
  via per 0.60 mm, and 9 sites for the 11 used pins over 4.0 mm — what Q8
  found at R5.
- **Inversions** are counted across the bundle (perpendicular to the line
  between the connectors' centres) over the nets with exactly one pad on
  each connector and none elsewhere: a net through a series part is not in
  the count.
- `tests/fixture_pcb.py --kind prelayout` is the three historical failures;
  each must FAIL.
- A waiver's `target` is a part (every check on it) or, for `rows`, `REF:SIDE`
  (`"U400:S"`): one side waived, the part's other `rows` entries still run.

## `fanout.py` — where a BGA's vias go, and how many nets leave over several layers

**Plan the fanout before the escape, and plan it for the escape.** board1's U1
(0.5 mm) spent seven rounds of per-net allowances (crossings, overlaps, rail
feeds) on a fanout that put every via in its ball's pad; moving the signal and
GND vias into the depopulated ball sites, as NXP's EVK does, let every DRAM
net out on three layers. `fanout.py plan` proposes that model;
`fanout.py layers` says how many nets it lets out, all layers at once.

- **The pitch decides which rules apply, and `plan` prints the arithmetic.**
  A via fits between four balls when `hypot(px, py) / 2 >= land/2 + clearance
  + via/2` (and the pitch >= via + clearance). Then the field is **dog-bone**:
  every ball that needs a via gets one at an interstitial point on a short
  diagonal stub, as on any 0.8 mm (often 0.65 mm) BGA. Nothing about
  depopulated sites or via-in-pad applies, no via goes in a pad, and the tool
  is just a corner picker -- use it there as well, then `layers` for the split.
- **Fine pitch** (no via between four balls -- 0.5 mm with a 0.4 mm via): a
  via goes in a depopulated site next to its ball, on an orthogonal stub or a
  diagonal one where the stub clears the two balls it passes, else in the
  pad (filled and capped, a fab option). A maximum matching (`--priority`
  nets first) gives as many balls as possible a site. **In-pad only**
  (0.4 mm): not even a site fits; every via in the pad, or HDI.
- **Which balls take a via is by net**: `--skip` (leave on the footprint's
  side; `--skip-rings K` for the outer rings' signals), `--in-pad` (rails
  whose vias feed a pour), `--share` (GND: its own site where free -- lower
  inductance -- or `--share-mode cluster`, joined on top to a neighbour that
  has one), the rest their own via. Moving the signal vias alone can be worse
  than in-pad everywhere: the GND vias left in their pads wall the lanes.
- **The plan is capacity, not routing.** The greedy matching can box in a
  ball one ring in from the edge (board1: a DQS ball lost its lane; `layers`
  reports it unplaced). Pin a site with `--fixed FILE` (the output's format)
  and re-plan. The stub's inductance (about 0.3 nH per GND ball at 0.5 mm) is
  a PDN cost the tool does not judge.
- **Two vias that keep a pad on the same inner layer are a pair rule, not a
  site rule.** At 0.5 mm with a 0.4 mm via, orthogonal neighbours leave 0.1 mm
  of copper: under an inner clearance above that (board1 rev 37: 0.1016 inside
  U1) two nets escaping on one inner layer can never take adjacent sites (or
  pads) -- a non-functional pad is dropped only where the via has no track on
  that layer. Neither `plan` nor `layers` knows a net's layer while it places
  vias, and the router does not see it either (each source keeps its own pad):
  KiCad's DRC on the routed escape found 14 such pairs. What worked on board1,
  in this order:
  1. the sites as ONE integer program (scipy `milp`): options = each ball's
     adjacent empty sites + its pad; one via a site; one diagonal stub a
     square; each same-inner-layer orthogonal pair a soft constraint at a large
     cost (pairs left are printed -- a layer move);
  2. **anchored** to the model the router already escaped (leaving a ball's
     site costs 20 against 1-1.3 for a site, 50 for the pad), so only the vias
     the rule forces move. Unanchored, the program met the rule by scattering
     the field (In2 went from 0 to 10-17 nets contested); a greedy re-solve
     that bars one site at a time put 45 vias back in their pads;
  3. a net's layer as the last lever, inside the handoff's permissions (a CA
     net to B.Cu, an outer-ring byte out on the top with no via): try the
     candidates in parallel model folders, each gated against its own model --
     the obvious move (the most contested nets) gave B.Cu necks over its 4 mm
     cap, because a net deep in a via block that is free on an inner layer is
     walled on an outer one, where every via keeps its pad.
- **An outer-ring ball that leaves on the top layer needs the site in front of
  it.** A via another ball's matching put one step outside the array, straight
  out of a via-free outer ball, walls that ball's top-layer exit (board1: F.Cu
  "no path even through the others"). Bar those sites before the matching.
- **`layers`: the exact maximum over several layers.** Each layer is
  `escape_bound.py`'s relaxation; the source sets one layer can escape form a
  matroid, so a matroid partition places the most nets possible over all the
  layers together. The footprint's own side (`"side": "component"`) takes
  nets from their balls past every land; other layers from their vias. Inner
  layers are partitioned with every non-pour via a hole (optimistic), then
  each layer is re-checked with its sources' own pads and a trapped net is
  moved off it. It writes `{net: layer}`; `"together"` groups (pairs) are
  reported when split, not enforced. Exit 1 when a net is left over: the
  model or the rules must change, no router will find it. A pass is still a
  bound -- the router has to find it.
  A layer may carry `"only": [regex, ...]`: the nets it may take -- a pour layer
  opened to a few low-speed signals (board 1's D78: 22 nets In2 + B could not take
  went to In5 / In4). "This layer first" is two runs: without the second layer,
  then with it for what is left.
  **It is quick, and it stops when told.** The partition asks thousands of
  sets that differ by one or two sources; each layer keeps ONE flow and moves
  it to the set asked for (a source dropped gives its unit back, a source
  added is one search) instead of building every flow from nothing: the same
  answer by construction (`tests/test_fanout_flow.py` holds the two against
  each other), 35 s -> 4 s on a 296-net fixture and 129 s -> 3 s on a real
  six-layer BGA. `--timeout S` stops at the next question and prints
  `STOPPED at --timeout`: the sets so far are printed and written as
  `<out>.partial.json` -- never under `--out`'s own name, which a later step
  reads as the answer -- and the exit is 1. Use it, never a `timeout` prefix.

## `decap.py` — the decoupling ring as a plan

A via beside every pad, a stub to it (turning only by 45°), and the pour. The
via sites come from `tools/place/sites.py` (board's own via and clearance);
`--pin REF.PAD=x,y` overrides one, which a COLUMN of caps needs — their vias
have to stand in a row. The zone file is hand-drawn: the shape of a power pour
is a judgement about what it must not run under, and no search makes that call.

**A pour in a fine-pitch BGA field needs `min_width` at the fab's minimum**,
not KiCad's default 0.25 mm: the antipads leave narrower bridges and the pour
otherwise fragments.

## `pour.py` — what a pour actually does

A zone's outline is a wish; its **fill** is the copper, and DRC does not read
the difference. It passes a pour that fell into islands, a pour whose one via
sits in a pocket of its own, and a power pour laid over a bus that must see
ground. For every filled zone `pour.py` refills in memory and prints, from the
fill: islands and their areas; same-net vias and through-hole pins per island
and the vias the fill does **not** reach; the same-net SMD pads standing over
it; with `--over NETGLOB:LAYER`, the length of those tracks lying **under** the
fill (split by `--field`'s courtyard when named). `--try NET:LAYER=zone.json`
swaps an outline in memory and measures; nothing is written.
`--section MM [--section-net NET] --from REF[.PIN],... --to REF[.PIN],...` cuts the fill across its
long axis every 0.05 mm between the sources and the loads and sums the copper on
each cut (the fill and the net's lands): PASS when the narrowest is >= MM, and
where it is. It is the "every cross-section of the pour on the load path keeps
N mm" a handoff gives once signal hops cross a rail pour (board2 D67(b): 1.0 mm;
1.71 before R5, 1.20 after). A shrink-the-fill test (erode by MM / 2, ask if
the pins still share a piece) asks a different question -- the narrowest
channel, not the copper across -- and failed a pour the handoff's rule passes.
The window runs from the FARTHEST load pin to the nearest source: every load
draws through the cuts between it and the sources (board2 RV22.4: a window
starting at J1's first +5V pin never cut the pour where pins 79-87 draw, where
a signal hop crossed it).

**Measure the fill, do not assume it.** `GetFilledPolysList(...).OutlineCount()`
is the island count and `.Contains(pt)` says whether a via is really in the
copper.

- **A round through-hole pad takes its spokes at 45 degrees** (KiCad's default
  for a circle; a square pad's are at 0 / 90). At a pour's corner, or where two
  pours meet beside it, two of its four diagonal quadrants must be this pour's
  fill, or DRC reports `starved_thermal` (min 2 spokes). Give the pour those
  quadrants (a higher `priority` over the neighbour where they overlap), not a
  track into the pad (board2 R4: J3 pin 2 between the +5V and VBUS pours).

## `stitch.py` — stitching vias and GND pad drops, as a plan

    tools/kpy tools/route/stitch.py --board board2 --plan routing/R6_pours.json --net GND --pitch 5 --merge --out routing/R6_gnd.json
    tools/kpy tools/route/stitch.py --board board2 --net GND --only-pads [--diag] [--pad-list REF.PAD,..] --out routing/R6_pads.json

A layout's "stitching vias every 5 mm along the pours' edges and round the cut-out" is a few hundred
placements that each have to clear every other net's copper on every layer. `stitch.py` lays the
plan's zones in memory, fills them, and places vias where one is legal:
- inside the net's fill on every layer it pours;
- clear of other nets' copper on every layer by the clearance;
- the via-to-pad gap from every pad;
- `--sep` from every via;
- copper-to-edge;
- `--own-gap` from the net's own copper where it does not pour.

It places them in three passes:
- **tie**: every island holding no via or THT pin of the net;
- **ring**: every Edge.Cuts contour, at `--pitch`;
- **grid**: optional, with `--grid`.

`--pads` drops every SMD pad of the net that no track touches to a via on one of its axes, linked
straight out of its centre; `--diag` falls back to a straight 45° link. A spot or pad with no site is
listed (`MISSED`), never forced. Its output is an ordinary plan for `route.py check` / `apply`.

- **Drop the GND pads before the signal groups that wall them in.** board2 routed R5 (In2.Cu / B.Cu)
  first, and eight GND pads had no via site left. Two of them had none even against the power group,
  because an F.Cu rail and a pair boxed them. `stitch.py --only-pads --plan <the signal spec's fixed
  copper>` on the earlier board found every site; those vias went in as their own group, and the
  signal router routed round them. Check every hand-placed via against the later group's fixed copper
  too: `check` judges only what the board and the plan hold.
- **A via near its own net's copper on a layer the net does not pour leaves a notch** (the shape
  rule's slot under 2 x CLOSE): `--own-gap 0.6`.
- **A fill-based clearance is not tight enough alone.** A via inside the fill shrunk by its radius
  stood 0.166 mm from a track, because a 45° fill edge is approximate. Every layer is also tested
  against the copper itself.
- **`--fill-gaps` aims at the gaps.** The ring pass tries evenly spaced spots and skips one
  near a via already there, so a gap between two vias 7.5 mm apart was never aimed at
  (board2 RV23.3: two bottom-edge gaps had legal sites). After the ring, it puts a via at the
  nearest legal site to each gap's farthest point, and repeats until none fits (board2: 27
  vias, the farthest ring point 3.87 -> 2.95 mm from GND).
- **Every gap is a number.** After placing, `stitch.py` prints a `GAP` line for each stretch of
  the ring path farther than pitch / 2 from a via or a through-hole pin of the net, with its length
  and the farthest point. A report quotes those lines; "no legal site within 1.5 mm" is not a
  measurement (board2 RV22.1: four 10-12 mm stretches; a 3 mm `--reach` halved them).
- **Starved spokes** (DRC `starved_thermal`): a THT pin left one spoke into the pour, or spokes into a
  pocket with nothing else, between tracks. There is no via site, so a zone `holes` entry round the pin
  and its pocket removes the pour there; the pin reaches the net on the other layers. Each hole changes
  the fill beside it, so gate again until DRC is quiet (board2: J5 S1, J_MAIN1 21 / 23 / 27 / 29).

## Background runs: `tools/bgwait.py` — started and waited on by pid, with a time limit

A run that takes minutes is started in the background and waited on with `tools/bgwait.py` (board-free,
plain `python3`; its module docstring is the manual). It is not a routing tool -- any long command of
either session goes through it -- but the router is where the waiting happens:

    python3 tools/bgwait.py run --name R2_a -- tools/kpy tools/route/netgroup.py --board board1 routing/R2_a.json --timeout 900
    python3 tools/bgwait.py wait R2_a R2_b --timeout 100     # 0 all ended with exit 0 | 1 one failed | 124 still running
    python3 tools/bgwait.py status                           # every job of this session, without waiting
    python3 tools/bgwait.py kill R2_b                        # its whole process group

- **`run` makes the job a (pid, start time) pair with a recorded exit code.** The command runs detached in
  its own session, stdout and stderr in `<jobs>/NAME.log` (or `--log`), Python unbuffered; a wrapper
  records the exit code in `<jobs>/NAME.json`, so `wait` reports it long after the process is gone. The
  job folder is git-ignored and per session: `kicad_project/bgwait.tmp/<boardN>/` for a board session,
  `out/bgwait/` for the concept session, the system temp folder when neither can be written.
- **`wait` always has a limit and always comes back with evidence.** `--timeout` defaults to 100 s (inside
  the Bash tool's default 120 s; the cap is 3600 s -- give the Bash call a longer timeout too). It returns
  the moment ALL jobs ended, or at the limit with exit 124, and each time prints per job: state, exit code,
  elapsed time, CPU seconds, CPU in the last interval (`0.0 s CPU in the last 60 s: idle or hung`) and the
  log's last lines with the log's age. Read them before waiting again: a job at 0 CPU will not end by
  itself. A bare PID (a process started some other way) is waited on too; only its exit code is unknown.
- **A wait by NAME is refused, by a hook.** `while pgrep -f "netgroup.py ... R2_emmc_"; do sleep 20; done`
  matched its own shell and sat 4 h 52 min at 0 CPU on three runs that had ended within 3 minutes. The
  PreToolUse hook `tools/session/bashguard.py` blocks a `while` / `until` loop that polls `pgrep` with a
  pattern, `ps | grep` or `pidof` and sleeps -- the bracket form `pgrep -f '[n]etgroup.py'` included: it
  still ends on a name any other process can hold, and still has no limit. A one-shot `pgrep`, and a loop
  on a pid (`kill -0 $pid`, `tail --pid=$pid`) or on a file, pass. The hook is declared in both
  sessions' `.claude/settings.json`, beside the wall.
- **Several specs of one group at once are a race, not three background runs:** `race.py`, below.

## `race.py` — spec variants raced as processes, one table, one winner

When a group does not converge, or converges badly, the next tries -- the other layer first, a wider
window, another seed, `--repair` -- do not depend on each other. `race.py` runs them at once:

    tools/kpy tools/route/race.py --board board1 routing/R3_a.json routing/R3_b.json "routing/R3_a.json --seed 2"
    tools/kpy tools/route/race.py --board board1 --timeout 900 a.json b.json c.json -- --repair

A variant is ONE argument: a spec, optionally followed inside the same quotes by netgroup options for that
variant alone; options after a bare `--` go to every variant. Each variant gets its own process group, its
own copy of the board folder under `<race>/<variant>/` (the system temp folder, or `--out DIR` outside
`kicad_project/`), `netgroup.py --timeout S` (default 600 s; killed at 1.5 x S + 60 s) and, when it
converged, its own `route.py check`. It prints one table -- variant, converged, iterations, route seconds,
check, new DRC, rule failures, vias, track length -- and `<race>/race.json` holds it as data.

- **Nothing is written to the real board.** Plans, partials and logs stay in `<race>/<variant>/`
  (`plan.json`, `partial.json`, `netgroup.log`, `check.log`, `spec.json`); the board copies are deleted
  unless `--keep`. Under the table are the three commands that take the winner over: `cp` its plan into
  the board, `route.py check` it THERE (the gate of record is the real board's), `apply`.
- **The winner's order is fixed, and printed:** converged; the check ran to a verdict; fewest new DRC;
  fewest rule failures; fewest vias; shortest track length; command-line order. It is the best of what
  ran, not a pass -- the line says `CHECK PASS` or `NOT a pass`, and the exit code is 0 only for a
  converged winner that passed (1: none did; 2: usage).
- **A raced plan is the plan the same command writes on the real board**, byte for byte: the copy and the
  neighbours change nothing (`tests/test_race.py` holds three variants to the same sha as three runs one
  after another). So the last printed line -- route it there again -- is an equal way to take a winner.
- **How many at a time:** `--jobs`, else `HW_JOBS`, else one per variant up to the cores less one. Each
  child gets `HW_JOBS` = cores / jobs, so a variant with `--seeds` or `--jacobi` does not oversubscribe.
  The checks are what load the machine (two `kicad-cli` each, many threads): on the fixture three variants
  took 6.4 s raced against 12.1 s one after another (a 24-core machine busy with other work), and eight
  at a time took 18 s for 123 s of route + check summed. A copy keeps `routing/.drc_cache/`, so a board that has been checked once does not
  pay its baseline again in every variant.
- **Race variants that differ in the SPEC before variants that differ in the seed.** When every variant
  stops at the same contested cells, the group lacks capacity there and no seed will find it (`prelayout.py`,
  the partial's `contested` and `reach`); when they stop in different places, the order is the problem
  and a seed or `--repair` is a fair try.
- **A long race goes through `bgwait.py`** (`run --name R3_race -- tools/kpy tools/route/race.py ...`,
  then `wait R3_race --timeout ...`). `bgwait.py kill` -- or any SIGTERM to `race.py` -- ends every
  variant's processes with it.
- A spec whose `"plan"` is an absolute path, or leaves the board folder, is refused: a variant writes only
  into its own copy.

## `corners.py` — the angle rule for a polyline

`fix45(pts, width, cut)` rewrites the joints of a polyline whose segments run at
multiples of 45°: a 90° joint becomes two 45° joints with a `cut`-long segment
between them (never under one track width), a 135° one three. A segment at any
other angle cannot be repaired locally — it raises, and the polyline has to be
re-drawn. `straighten(pts, width)` turns a grid router's one-cell stairs back
into diagonals, keeping the first and last headings.

**Generate at 45°, do not chamfer afterwards.** A pass that cuts the corners of
a router's output also "cuts" the router's own 45° joints, into 22.5° ones.

## `view.py` and `render`

`route.py render` is the picture for the **review**: KiCad's own plot of the
top, bottom and inner layers, rasterised with PyMuPDF from `tools/.venv`
(written to `<board>/routing/renders/` unless `--out`). `view.py` is the picture
for the **work**: one window of the board, one colour per copper layer, zone
fills shaded, an optional plan (or a stalled router's `.partial.json`) on top.

- **A stalled escape is read off one picture, not off coordinates.** `--vias
  <fanout model>` draws vias that are not on the board yet (bga_decap / `fanout.py
  plan` output) with their top stubs and net names, and `--contested <netgroup log>`
  marks every contested cell the log lists. Board1's In2 escape (D64) stalled at
  three gaps each at exactly its counted capacity (3 tracks through a 1.118 mm
  diagonal, 2 through a 1 mm row) -- the bound passed them, the 0.5/12 grid did not.
  A gentler negotiation (`--pres-mul 1.03 --iters 800`) and a finer pitch (0.5/14,
  0.5/16: worse or out of the hour) did not help; changing the fanout model did
  (the bullets under `fanout.py`).
