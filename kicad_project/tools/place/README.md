# `tools/place` — placement checks, board-to-library sync, silk labels

**Yours**, like `tools/fp` and `tools/board_setup`. Placement itself is done
with Konnect (`set_component_placements`: one KiCad undo step live, or a
revision-checked file edit when KiCad is closed). These tools answer the
questions every placement round would otherwise re-derive by hand: is the plan
legal, did it get shorter, does the board still match the library, and where do
the reference designators go.

    tools/kpy tools/place/place.py gaps   --board board1 [--refs U2,C10]
    tools/kpy tools/place/place.py check  --board board1 plan.json
    tools/kpy tools/place/place.py apply  --board board1 plan.json     # KiCad closed
    tools/kpy tools/place/place.py auto   --board board1 spec.json
    tools/kpy tools/place/place.py sync   --board board1
    tools/kpy tools/place/place.py labels --board board1 [--dry-run [--json]]
    tools/kpy tools/place/place.py park   --board board1 [--refs H1,H2]  # KiCad closed; --refs are the parts LEFT in place -- every other unfixed part is parked
    tools/kpy tools/place/place.py drc    --board board1                  # DRC cached by content (drccache.py)
    tools/kpy tools/place/place.py drc    --board board1 --out drc.json   # ... and keep the full report (item positions)
    tools/kpy tools/place/place.py render --board board1 --refs U2,C10 [--side B]   # the area, as a PNG
    tools/kpy tools/place/place.py silk   --board board1 [--dry-run]       # KiCad closed
    tools/kpy tools/place/sites.py        --board board1 --ref C13 --box x0,y0,x1,y1
    tools/kpy tools/place/pack.py         --board board1 --spec pack.json
    tools/kpy tools/place/addfp.py        --board board1 [REF ...] [--dry-run]   # KiCad closed
    tools/kpy tools/place/schupdate.py    --board board1 [--dry-run]             # KiCad closed
    tools/kpy tools/place/refresh.py      --board board1 J1 J2                   # KiCad closed

A plan is `{"REF": [x, y, rot]}`, or `[x, y, rot, "B"]` for the back side
(absolute board mm, rotation in degrees as KiCad shows it). `--board` takes `boardN`, the
folder name or a path; left out, it is the board folder you are in, else the
board this session owns (`HW_BOARD`, set by `bin/board`). Another session's
board is refused. A plan path is taken as typed, else inside the board folder.

`tools/kpy` only: these import `pcbnew`, which is built for KiCad's own
interpreter. `gaps`, `check` and `sync` read the **saved** board: run Konnect
`save_project` first when KiCad is open. Anything that writes the board needs
KiCad closed and refuses while a lock file exists.

**`check` judges a moved part's pads against the copper already on the board** (`COPPER R33.1(LED_PWR) over a via
at (..) of GND: gap -0.090 < 0.0889`): a via or a track of another net under a pad is a short, and saving the board
HIDES it -- pcbnew gives the touching copper the pad's net, so DRC is silent and parity is clean. Board 1 moved a
pull-up onto a decoupling capacitor's two vias after routing had begun; both vias came back as the resistor's net
and only the via-link audit of the next group named one. Placement plans made once copper exists are checked
against it; if a save has already renamed something, compare every via's net by position with the last backup
(`<board>.kicad_pcb.<step>-bak`, copied to a `.kicad_pcb` name -- `LoadBoard` returns None for any other suffix).

## `<board>/placement.json`

Board data the scripts must not hard-code (the skeleton comes with every new
board):

| key | meaning |
|---|---|
| `keepouts` | `{name, rect: [x0, y0, x1, y1], side?, allow?}`, board-relative mm from the outline's top-left, `null` = open side. Reservations for parts not placed yet (connectors with their mates, a big IC) and zones other groups may not enter — written by `place.py seed` from `handoff/floorplan.json` (`"from": "handoff/floorplan.json"`; `seeded_zones` remembers them). **Delete a reservation when its part is placed**: from then on the real courtyard is the obstacle, and `seed` never puts it back |
| `groups` | name → references. `check` prints each group's extent |
| `fixed` | parts exempt from the outline/keep-out test (mounting holes: their standoff courtyard overhangs the edge by design) and expected as board-only footprints by `drc` |
| `overhang` | connectors whose courtyard may cross the outline (right-angle male pins mate off-board) |
| `ignore_nets` | nets left out of the connection-length report (GND pours) |
| `min_courtyard_gap` | smallest courtyard gap `check` accepts (0.03 mm: touching is legal in DRC, float noise is not) |
| `hole_to_edge` | minimum hole-to-edge distance, from `14_fab.md` (Board Setup has no field for it). Starts as `null`: `check` refuses until `place.py seed` copies it from `handoff/fab.json` |
| `net_report_limit` | how many nets `check` lists in its span report |
| `labels.hide` | references whose designator never goes on silk |
| `labels.own_nearest` | fnmatch patterns (`["TP*"]`) whose label may never sit nearer another part's pad than its own, and which are placed first -- a test point's label is what a probe is set by (board1 RV8.4). Every other label only pays for it: a hard rule there hides labels a dense board needs |
| `labels.own_margin` / `labels.own_center` | for those parts: the label at least this much nearer its own pad than any other (0.5 mm default) and at most this far off its pad's axis (0.3 mm default) -- a probe user reads a label by eye, and 0.06 mm is no margin (board1 RV9.3). On a crowded edge 0.3 / 0.8 |
| `shared_pad` | `[{refs: [A, B], net, source}]`: two stock parts drawn as one selector, their `net` pads on one point and the parts at 90° (a 3-pad selector made of two 0402s). That pair skips the courtyard test -- their courtyards overlap by design -- and `check` proves the merge instead: the `net` pads coincide, one side, 90° apart. Everything else (foreign-net pads, holes, silk) is still tested. KiCad's own DRC reports the overlap: exclude it there with the handoff's comment |

A keep-out may carry `"side": "F" | "B" | "both"` (default `F`) and an
`"allow": [refs]` list for the parts that belong inside it. The routing guide's
`keep-out` row names one to keep a class of tracks out of it.

## `check moves.json`

The plan is applied in memory only, and the following is tested for every pair
that involves a moved part:

- **courtyards**: gap ≥ `min_courtyard_gap`, measured on the courtyard's own
  shape — a rectilinear polygon (an angled connector's body + narrower pin
  field, `fp.py tidy --courtyard-contour`) is cut into strips; anything else
  is its box. The same strips decide a plated hole inside a courtyard and a
  keep-out hit;
- **copper between different nets** on different footprints ≥ mask web +
  2 × mask expansion (from Board Setup). Below that, the fab merges the
  openings;
- **silk marks against the neighbour's mask opening** ≥ silk clearance. KiCad's
  own pin-1 triangles can sit *outside* an IPC courtyard, so courtyards alone
  let a neighbour's pad land on the mark;
- **a plated hole inside a courtyard**, either direction (KiCad's
  `pth_inside_courtyard`): this is the rule that catches a back-side part
  sitting on a connector's through-holes;
- **hole to hole** ≥ Board Setup's minimum;
- **the board edge**: courtyards inside it, pad copper ≥ copper-to-edge, holes
  ≥ `hole_to_edge`. Parts under `overhang` are exempt from the courtyard test
  only;
- **keep-outs**, per side, with the zone's `allow` list.

**A move that lands on copper a route plan is about to rip reads as a short.** `check` judges the
moved pads against the board as it is, old links included: a capacitor moved onto its old GND link
is a COPPER failure that the re-link would have removed. Split the route plan: its rips (and the
vias they free) as their own plan, checked and applied first, then `check` / `apply` the moves,
then the new copper -- three stages, applied in that order (board1 RV61_u1gap and RV61_links,
both hit it the same day).

It then prints the net span (MST of pad positions) before → after for every net
the plan touches, and each group's extent. Exit 1 on a failure. To audit a
whole board, write every placed part at its current position into a plan and
check that.

**Spacing convention** (a default; the floorplan and `10_layout.md` win):
courtyards are IPC nominal (0.25 mm). Parts whose pads connect straight across
may sit ~0.15 mm apart. Where a signal has to escape between two parts, leave
≥ track + 2 × clearance (plus margin) of copper channel and name it in the
report, so the next compaction keeps it. A router cannot invent a channel that
placement closed.

## `apply`

`check`, and on a pass, write the plan through `pcbnew.SaveBoard` and read the
board back to confirm every position, rotation and side. KiCad must be closed.
It is the only path that can **flip** a part to the back; Konnect's
`set_component_placements` is the one to use while KiCad is open, and it cannot
flip.

## `auto`

For supporting parts, `auto` searches every position on a grid and every
allowed rotation, scores each by weighted distance to what the part connects
to, and takes the cheapest spot that passes every rule above. Spec:

    {"step": 0.1,
     "base": "plan.json",
     "avoid": [[x0, y0, x1, y1], ...],
     "parts": [{"ref": "C30", "side": "B", "region": [x0, y0, x1, y1],
                "rots": [0, 90], "anchors": ["U3"],
                "pins": {"1": ["U3", "7"]}, "weights": {"GND": 0.25}}]}

`pins` names the exact pad to sit next to; otherwise each pad aims at the
nearest pad of its own net on the `anchors` (or anywhere already placed).
Parts are placed in the order listed, each becoming an obstacle for the next,
so put the big or critical ones first. It writes `<spec>.plan.json` for
`check` and `apply`; it never writes the board.

**`auto` sees footprints, not copper.** A spot it returns can lie on a via or under a top-layer track (board 1: three of five
spots). Run its plan through `check` -- which does report `COPPER <pad> over a via` -- and `drc` before believing it, and put
areas whose vias are still to come (under a regulator's exposed pad) in the spec's `avoid`.

The search stays exhaustive; the distance to a rail's nearest pad comes from a bucket grid
(`PointGrid.nearest`, the same minimum) -- under a BGA a rail is hundreds of pads, and asking
each of them at every grid position was the whole run time (42 s -> 6 s on the large fixture,
80 s -> 12 s on a real spec, the same plan).

**`avoid` is how a BGA's fanout keeps its room.** Before placing decoupling on
the back under a BGA, reserve one via per *used* ball (via pad + clearance).
Where the via goes depends on the pitch, and the model must prove it against
every pad and via of another net before anything is placed on it: at 0.8 mm a
dog-bone fits at the diagonal centre; at 0.5 mm a 0.4 mm via pad between four
0.25 mm balls leaves 0.029 mm of copper, so the via goes IN the pad (filled and
capped) unless that diagonal is clear of other-net balls, and two neighbouring
in-pad vias leave 0.100 mm -- under a 4 mil area rule they shift off centre,
the drill still inside the pad (board1: a dog-bone model 403 of 426 vias
unbuildable, found only in review). A ball that escapes on the top layer (an
outer-row signal) needs no via. `auto` does not know the model's vias unless
they are in `avoid`: a part placed by `auto` under a BGA lands on them.

## `sync`

Every project-library footprint on the board against its `.kicad_mod`: pads,
attributes, and SilkS / CrtYd / Fab geometry (back-side parts compared after
KiCad's flip). It agrees with KiCad where KiCad's own copy legitimately
differs: a board pad's angle includes the footprint rotation; pad `net`,
`pinfunction`, `pintype` and `property` are board-side data; stroke type
`default` equals `solid`; `exclude_from_bom`, `exclude_from_pos_files` and
`dnp` come from the symbol; coordinates are compared at 0.1 µm.

Run it after every library refresh. **Counting silk items is not
verification**: a body rectangle and a pin-1 dot are both "one item", and that
is exactly how a stale refresh hides.

## `drc`

KiCad's own DRC **with the schematic-parity test switched on**, summarised:
violations by type, the unconnected count, and every parity problem. Exit 1 on
any parity problem except `extra_footprint` for the board-only parts under
`fixed`.

⚠ **Never run bare `kicad-cli pcb drc` and read its `schematic_parity` list.**
Parity is only tested with `--schematic-parity`; without the flag the list is
empty, which reads exactly like a pass. A footprint swapped by script can come
out with pads that have no net — no ratsnest line, so the router would simply
never connect them — and only the parity test shows it.

The swap order that works: `board.Add(new)` first, then for each pad
`pad.SetNet(board.FindNet(name))` (nets set before `Add` are silently lost),
carry `Datasheet`, `Description` and custom fields with `HasField` /
`SetField`, `SaveBoard`, then **`place.py drc`** and **`place.py sync`**.

## `silk` -- the silk line width DRC never tests

KiCad's DRC checks silk TEXT thickness, never a stroke's width, and KiCad's
stock marks (every chip R/C, the pin headers' pin-1 corners) are 0.12 mm --
under a fab whose silk line minimum is 0.15 (board2 review RV5.5: 44
strokes). `drc` now counts every unfilled silk stroke narrower than the
project's default silk line (which `board_setup.py apply` sets from
14_fab's silkscreen row) and exits 1 on any. `silk` raises them on the
STOCK footprints (the chip R/C, which never enter the project library); a
project-library footprint is fixed at its source, `fp.py tidy <name>
--silk-keep ... --silk-width 0.15`, then `refresh.py --layers SilkS`, so
`sync` stays exact -- `silk` refuses those.

Raising a stroke 0.12 -> 0.15 grows it 0.015 mm each side, so **two
neighbours' silk lines close by 0.03 mm** and a pin-1 dot grows toward its pad:
run `drc` straight after. board1 got four `silk_overlap` (0.08-0.09 against
the 0.0992 silk clearance) between abutting chip R/Cs -- a 0.02-0.03 mm
nudge each, through `check`/`apply` -- and one `silk_over_copper`: a
converter's second, inner pin-1 dot on pad 1's mask opening, dropped at the
source with `fp.py tidy --silk-drop <i>` and `refresh.py --layers SilkS`.

**A footprint's silk against its OWN pads.** KiCad's DRC tests silk against other
footprints' copper, not against its own pads, and KiCad's stock 0402 bars sit 0.085 mm
off theirs, under 14_fab's "silk >= 0.15 mm off every pad" (board2 RV23.1: 24 bars on 12
parts, found by the reviewer and missed by every gate). `drc` now counts every silk stroke
nearer its own footprint's pads than the silk clearance + the mask expansion, and exits 1.
`silk` trims each one on a stock footprint to the longest stretch that clears, or drops it
when what is left is shorter than twice its width (an 0402's bar between its pads), after
the widening, which grows a stroke toward its pads. Edited stock footprints differ from
KiCad's copy and DRC reports `lib_footprint_mismatch`. `silk` records them in
`placement.json` `silk_edited` (refs and why), and `drc` sets exactly those aside and says
how many.
A stroke is sampled only against the pads whose box comes within reach of its own: a
296-ball BGA's outline was measured against every ball at every 5 um (8 of `drc`'s 10.5 s
on a 300-part board; now 0.1 s, the same strokes and gaps). What is left of `drc` is
`kicad-cli` itself, and the report cache skips that when the board has not changed.

## `labels`

1. Every designator gets the Board Setup minimum silk text (typed from
   `14_fab.md` by `board_setup.py`).
2. For each footprint on the board, candidate spots ring its courtyard: text at
   0° or 90°, up to 2 mm out, on a 0.25 mm step. A spot must clear pads (copper
   + mask expansion + silk clearance), holes, **vias** (a designator over a
   tented via's bump breaks up: board2 RV22.6), all other silk, **every
   courtyard** (a label under a part is unreadable), the board edge, and the
   labels already placed. So labels move after routing, as the last step.
3. It places the **most constrained** label first, at the spot that leaves the
   others the most room. Pure greedy walls labels in.
4. A label with no legal spot is **hidden on silk** and listed. `HW_LABEL_DEBUG=1`
   prints, for each `own_nearest` part, its candidate count and why the rest
   were refused (edge / obstacle / courtyard). A row of test points at about 2 mm
   pitch takes labels alternating above and below only where both sides are
   free; under a pad grid (above it nearer the grid's pads) it holds one label
   width per test point. Stock and
   derived footprints carry `${REFERENCE}` on F.Fab, so the assembly drawing
   still has it.

`placement.json` `labels`:
- `hide`: references that never go on silk.
- `own_nearest`: fnmatch patterns whose label is never nearer another part's pad than its
  own; `["*"]` for a fab release (board2 RV23.2: C4 / D5 / R2 read as their neighbours').
  A part added to it that has no legal site is HIDDEN, and the site it left is free for a
  neighbour that is not in the list -- which may then sit nearer a foreign pad (board1 RV64.3:
  C53 hidden, C34's label surfaced by U102). Pin every label the change pulls (`hide` or
  `own_nearest`) and diff the labels (labgate) on a copy before the rehearsal.
- `edge_min`: a label's distance from Edge.Cuts, the outline and every cut-out. Set it to
  the outline tolerance + the silk clearance, or a routed cut-out can nick it (board2
  RV23.2: 0.149 mm against +-0.2).

Run it when a placement round is finished; labels are the last thing to move -- after the
last via too (stitching added after the labels put four designators back on vias).
It writes `<board>.kicad_pcb.labels-bak` first.

`--dry-run --json` prints the result alone -- `{"placed": {ref: [angle, x, y]}, "hidden": [...]}`,
sorted -- so two runs compare by hash (the harness's `pcb-large` stage does). The search is
exhaustive and its result does not depend on how it is indexed: obstacles, courtyards and pads
are asked through bucket grids (`RectGrid`), the edge through a memoised bound (`Board.edge_clear`)
and the greedy step re-scores only the labels the last one could touch. 326 parts with a
536-ball BGA: 316 s before, 4 s now, the same placement byte for byte.

## `sites.py` — where can this part go so that every pad reaches a via?

`check` judges courtyards, pads, silk and holes. It does NOT know the copper,
and a position that passes it can still be useless: a decoupling cap whose pads
cannot reach a via decouples nothing. `sites.py` searches positions (or, with
`--plan`, reports a plan's) and prints the nearest point where a via is legal
ON EVERY LAYER — clearance to foreign pads, tracks and vias, outside its own
pad, and clear of the other via this part needs. The via and clearance default
to the board's own (the Default class's via; the largest class clearance);
`--via`, `--clr`, `--own`, `--reach` override them.

⚠ Its two traps: the part's OTHER pad is a different net and needs the full
clearance (only the pad the via belongs to may be close), and a via a plan has
not written yet is invisible to the next part — `tools/route/decap.py` feeds them back in as
it goes. It moves nothing.

- **A via site that shuts a BGA's exit lane is dropped, not ranked first.** `sites.py` (and `decap.py`, which
  takes its sites) read `<board>/routing/prelayout.json` `"exit_lanes"` by default (`--lanes FILE`: that key or
  a bare list; `--no-lanes`: off) and judge every candidate via, and every position's own pad copper on the
  BGA's exit layer, with `tools/route/exitlane.py`'s geometry and gate imported (`blocked_by`, `shuts` -- the
  count `route.py check`'s `RULE exitlane` makes): a site whose via newly leaves a ball without a lane, with
  what this part already put in lanes, is skipped for the next one, the pad line says `[nearer (x, y) dropped:
  shuts U1 P26]`, and a summary counts the dropped sites by ball. A position whose pads shut a lane is
  skipped (search) or fails (`--plan`); a `decap.py --pin` or stub that shuts one is a WARNING and exit 1.
  Paste-only pads (no copper layer) need no via and are skipped. board1 RV61_u1gap / RV61_c28: C28 at (23.18,
  21.5, 0) ranked first with its VDD_ARM via at (22.088, 21.5), which `check` then refused for U1 P26; the
  two-via positions in U1's west corridor all shut a ball. Prove it both ways: a part far from any BGA prints
  the same as with `--no-lanes`, and a known lane-shutting site is named with its ball.
- **Every via site is on its pad's axis, its link is clear, and a keep-out bars a position.** The guide's
  via-link rule (one straight segment out of the pad's centre into the via's, along an axis; 45 degrees as
  its "45-degree via link" row allows; never nearer a pad than its "via to pad gap") is what `route.py
  check`'s vialink gate judges, so the candidates are the pad's four axis rays (0.01 mm steps from the pad's
  box centre, vialink's `pad_frame`), then the four diagonals only when no axis site is legal (marked `[45]`,
  ranked behind every all-axis position; none when the row says "never"). The own-pad distance is at least
  via/2 + the gap (vialink's via-touch), and the link itself, at the caller's width (`decap.py`: the net's
  class width; else 0.15), keeps the clearance from every foreign pad, track and via -- a site whose via is
  legal but whose link crosses copper is no site. A position whose courtyard enters a `placement.json`
  keep-out of its side (B, F, both; `allow` honoured: `place.keepout_hits`) is barred like `check` bars it,
  and the barred positions that would have ranked ahead of the winner are printed with the keep-outs' names
  (`barred (...) in 'pair corridor south of U1 (bottom)'`). `--no-keepouts` drops that; `--bar "x0,y0,x1,y1;.."`
  adds the brief's own bars; `--extra-via x,y` a planned via; `--near REF:NET --rank near` ranks by distance
  to that BGA's balls of the net; `--json` writes the ranked positions; `--no-axis` is the old any-angle
  search. Distances use the largest class clearance, so it can find fewer sites than a net-aware search (board1
  C32: 809 vs axsearch.py's 1045) -- never a site the net-aware one would refuse. board1 RV61_c54b: the old
  search put C32's and C200's vias off their pads' axes and the vialink gate failed each after the plan was
  written; it now reproduces the hand-built axsearch.py's winner, C32 (22.5, 13.3, 270, B) with vias (22.5,
  12.02)/(22.5, 14.16), naming the two nearer sites it bars ('pair corridor south of U1 (bottom)' 6.97 mm,
  'DRAM channel U1-U500' 7.16 mm); with `--no-keepouts` the pair-corridor site (32.70, 29.15) wins. Prove it
  with vialink's own tests on what it emits: 86 sites on board1, 72 axis + 1 `via-45`, 0 off-axis.

## `pack.py` — a search, when a block must be re-arranged

`check` judges a placement; it does not find one. `pack.py` anneals a handful
of `movable` parts against courtyard gaps, keep-outs, "pad within N mm of pad",
"courtyard gap at least", and "gap to the nearest of a set within [lo, hi]".
A run that reaches cost 0 satisfied all of them at once; the result is a plain
plan for `check` to judge independently. The spec is in the module docstring.
Its restarts run on every core (`tools/par.py`), chosen as the serial loop
would choose them, so `HW_JOBS=1` gives the same plan.

**`tools/par.py` is how a board tool uses several cores**: load the board
once, fork, read in the children, return plain tuples, write only in the
parent (pcbnew objects are neither picklable nor thread-safe). Results come
back in job order; `HW_JOBS=1` forces serial; macOS is serial unless
`HW_JOBS` is set.

## `schupdate.py`, `addfp.py` and `refresh.py` — the closed-board paths

- ⚠ **A pad re-netted by `schupdate.py` takes its copper with it, silently.** KiCad's save gives
  every track and via touching the pad the pad's new net (same UUIDs), so `place.py drc` then
  shows 0 shorts and parity 0 -- on copper that may now stand at a site laid for the old net
  (board1 rev 155: 44 U500 balls swapped, 35 dog-bones and 35 vias renamed). Before the update,
  dump the copper by UUID with its net; after it, diff the two and treat every renamed item as
  copper to re-judge or rip, never as a pass.

Konnect's `update_pcb_from_schematic` and `update_footprints_from_library` are
the documented paths, and both need the board open in live KiCad with its IPC
API answering. When that path is closed:

- `schupdate.py` is Update PCB from Schematic, with *Re-link footprints by
  reference designator*, for the parts already on the board: symbol path,
  every pad's net (by the name KiCad's update and DRC parity use), Value,
  fields (the ones the symbol dropped removed), DNP / Exclude from BOM. It is
  the way after a schematic redraw (every symbol a new UUID): Konnect's
  `update_pcb_from_schematic` matches by path, so it refuses a board without
  paths, and once re-linked it moved only the pads whose connection changed --
  renamed nets, `unconnected-(…)` names and new fields stayed (board1: parity
  325 after it, 0 after this). Traps it handles: `kicad-cli`'s netlist names an
  open pin of a multi-unit part with its unit letter, the update without; a
  footprint field the symbol dropped is hidden, not removed (the wrapper's
  `Remove` corrupts the footprints after it; `RemoveNative` reports success
  and the field is still saved). The live Konnect update also strips pad
  properties (BGA, heatsink, fiducial -- they reach the Gerbers' aperture
  functions); KiCad then reports lib_footprint_mismatch: copy each pad's
  property back from the library footprint (`pad.SetProperty`).
- `rmfp.py REF…` removes footprints the schematic no longer has: refused while the reference is
  still in the schematic's netlist or while copper ends on one of its pads (rip that through its
  group first, so the manifests stay true); `schupdate.py` refuses a one-sided reference, so this
  runs before it;
- `addfp.py` adds footprints the schematic has and the board has not (from the
  schematic's own netlist; nets set **after** `Add`), parked off the board;
  `addfp.py --replace REF…` is KiCad's "replace footprints with those the symbols name" for a part
  already placed whose Footprint field changed (a new land for an inductor): the new footprint stands
  at the old one's position, side and rotation with its symbol link, and a pad with copper on it is
  refused (rip the group first). `rmfp.py` + `addfp.py` is not that route: `rmfp` refuses a reference
  the schematic still has. Proof: `place.py drc` parity 0 with the new id;
- `refresh.py --models REF…` copies the library footprint's 3D model list (path, offset, scale,
  rotate) onto the placed footprints, either side, and nothing else; `landsync.py` copies a pad's
  thermal-spoke angle with its shape (a circle made square kept 45 degrees, which KiCad saves as an
  override the library lacks: `sync` said "DIFFER: pads");
- `place.py drc` judges no silk stroke when `handoff/fab.json`'s "Silkscreen line / text" is
  "not used" (a board with no silkscreen printed); `board_setup.py` reads "Silkscreen | none" and
  that row the same way, and `fabpkg.py` leaves the silk Gerbers out;
- `refresh.py` replaces the shapes on chosen front layers of named footprints
  with the library's, after `fp.py tidy` changed a courtyard or silk. Pads,
  texts, fields, model and placement are untouched — a land change is a
  re-sign, then `landsync.py`.
- `landsync.py` copies a re-signed LAND (pad shape, size, the pads' own
  solder-mask and paste margins, pad property) from the library onto the
  named placed footprints, pad by pad number; nets, positions, graphics,
  fields and placement stay. Refused when a pad number or a footprint-local
  position differs (that is a re-place). Proof: its read-back, then
  `place.py sync` "all identical" and `place.py drc` (board1, D55: U1 / U300
  lands 0.25 NSMD -> 0.40 under a 0.35 opening, 639 pads). The library edit
  itself: `pcbnew.PCB_IO_KICAD_SEXPR()` `FootprintLoad` / `SetSize(F_Cu, ...)` /
  `SetLocalSolderMaskMargin` / `FootprintSave`, then `fp.py sign` (or `adopt`
  for a stock footprint whose land is now your own); `print` before
  `os._exit(0)` is lost unless stdout is flushed.

Each writes a backup first; `place.py drc` and `place.py sync` must pass after.

- **"KiCad has the board open" with no KiCad running is a stale lock.** A tool run that dies after it has started kicad-cli
  (a plan naming a net the board does not have, an interrupt) leaves `~<board>.kicad_pro.lck` in the board folder, and every
  later `apply` refuses. The message now says which it is: with no `kicad` / `kicad-cli` process alive the lock is stale --
  remove it and run again (board1's RV29: refused twice). Never remove a lock while a KiCad process is alive.
- **After a redraw, read the board's net names before writing a plan.** A net that now runs between two sheets takes the
  ROOT's name (`/BT_HOST_WAKE`, no sheet path); `schupdate.py` renames it on the board and the copper follows, but a plan
  or a build script that still spells the old path stops with "net not on the board".
- **A script that generates candidate spots for parts (a cap planner, a search) filters every spot with
  `place.py check`'s own tests BEFORE its solve -- import them, do not re-model them.** board1 D116: a cap
  planner with its own obstacle model left out the keep-outs of `placement.json`, the unmoved parts and the
  board's other copper; its FEASIBLE plan then failed `place.py check` 21 times and the solve was wasted. Pattern
  (board1 tmpWork/D116_comb/world.py + chkfilt.py): export from a scratch board what check judges against, run
  check's per-part tests (its `rgap` imported) on each candidate, and prove the filter by reproducing a known
  check log exactly on an old plan.
- **A candidate planner that does not decide: read the solver's own log and count, before giving it more time.**
  board1 D116, a CP-SAT cap planner (100 parts, 56,000 candidate spots): three runs, 76 minutes, nothing decided.
  What it was, each found by a count and none by waiting:
  1. *Generation ran on one core and was repeated in every run* (44 of the 76 minutes). Save the candidates (a
     pickle beside the plan, `--cands`) and generate them with `par.pmap`, one job a footprint/net group; prove the
     parallel result EQUAL to the serial one (583 s -> 119 s, the two pickles identical).
  2. *The candidates were cut to the nearest K legal spots a part.* Forty parts shared one patch, so the last
     five had no legal spot left against the others' picks and the hard model could never close. Keep every legal
     spot (thin by a coarser lattice when the count must drop), never the nearest K -- and before re-solving,
     probe each unplaced part: how many of its candidates are legal against the plan's picks (0 at K = 500,
     455-988 with every spot), and which parts block the rest.
  3. *The search did not move.* With `log_search_progress` on, 20 workers showed about 1,000 conflicts each in
     two minutes on ONE `AddNoOverlap2D` of 52,755 optional rectangles. That is a model to shrink, not a time
     limit to raise: pin what will not move (44 passives were 47 % of the bools; two moved), solve soft first,
     then place only the unplaced parts with every other pick fixed (`AddHint` + fixing all but a neighbourhood:
     12 free parts OPTIMAL in 232 s, 3 free in 1 s, where the whole model was UNKNOWN after 1500 s).
  4. *Boxes go to the solver as integers rounded OUTWARD* (floor the low edge, ceil the high). `round()` dropped
     a 0.1 um safety and left a pair at exactly the courtyard gap, which `place.py check` refused -- by float
     noise: exactly AT the gap passes since the nanometre bullet below, a pair `round()` leaves under it fails.
  5. *"No two parts overlap" goes in as BOOLEANS, not as one `AddNoOverlap2D`, when the boxes are optional
     candidates.* Tens of thousands of optional rectangles in one NoOverlap2D barely propagate (item 3). The
     same constraint as clauses decides: take lattice points (a period above the smallest box, e.g. 0.2 mm;
     per axis, count which residues the overlapping pairs' common rectangles contain and add residues
     greedily), one `AddAtMostOne` over the candidates whose box covers each point, and a two-literal clause
     for every overlapping pair no point catches. Prove the encoding by enumeration -- for a sample of
     candidates, every overlapping partner is in a common clique or a pair clause -- not by two solves agreeing.
     Measured (board1 tmpWork/D116_comb/capplan_clique.py, `--enc clique`): a 12-part neighbourhood OPTIMAL in
     8 s against 195 s; the whole model (48,505 bools, passives pinned, ring thinned to a 0.2 mm lattice)
     OPTIMAL in 27 s with no hint, where NoOverlap2D was UNKNOWN at 1500 s; and the nearest-K model of item 2
     proven INFEASIBLE in 11 s. More cliques and fewer pair clauses solved faster, at four times the literals.
  6. *A plan that adds vias is filtered with the route check's via tests too, at the clearance KiCad itself
     would ask: the net classes AND the board's custom rules.* Spots that pass `place.py check` are not yet
     spots a via can stand on, and each miss costs a whole solve plus the gates. board1 D116, two in a row:
     the via test judged every pair at Default's clearance (its copper dump took each class from
     `GetNetClassName()` on a board file not named like its project -- `tools/route/README.md`), so a cap's
     in-pad vias sat 0.169 mm from a pair that asks 0.2032; then, with the real classes, it lacked the
     `.kicad_dru`'s LAST clearance rule (the later rule wins), refused a spot KiCad passes, and the model
     was INFEASIBLE in 17 s for no reason of the board's. Pattern (board1 `tmpWork/D116_comb/cudump.py` +
     `viatest.py`): dump every layer's copper with its net and class, and each part's courtyard; per
     candidate test its vias against other-net copper on every layer and hole to hole, an in-pad via
     against the part's other pad, the same-net slot, the joints of a part that stays. Prove the filter
     BOTH ways before a solve: it flags the violation the DRC reported on an old plan, and nothing more
     (0 of 280 vias flagged on the plan that then passed `route.py check`).
  7. *A cache the planner cannot find is an error, never a reason to regenerate.* `--cands X` with X
     missing made the planner generate its own nearest-K set, save it under that name and prove
     INFEASIBLE a model nobody had asked about. X was missing because the script that writes it was
     piped into `head -1`: it prints its summary, THEN writes, and `head` closing the pipe killed it
     before the file. A step whose product is a file logs to a file; the next step stops unless the file
     is there AND the consumer's log says it loaded it.
- **`apply` keeps ONE backup, `<board>.kicad_pcb.place-bak`, and every apply overwrites it.** After a second
  apply on the same scratch copy it is the board before THAT apply, not the board you started from. A baseline
  a later gate compares against (the DRC before the plan, the board to restore for the next variant) is copied
  to its own folder before the first apply (board1 D116: a scratch's DRC baseline was its `.place-bak`, and the
  cap plan's apply replaced it).
- **A touch is not a hit, and `check` decides that on KiCad's nanometre, not by float noise.** The trap: a
  courtyard edge was the line's bounding box less half the line, a float sum (`19.53 - 0.025` is
  `19.505000000000003`), so a part planned exactly ON a keep-out's edge read as inside it -- board1 D116: C34
  and C58 against 'radio (bottom)', `KEEPOUT`, an overlap of 3.6e-15 mm, where the plan's own sum (18.805 +
  0.7) said touch. Swept over the 5 um grid, an exact touch was a KEEPOUT at 16 of 40 places and a pair at
  exactly `min_courtyard_gap` a COURTYARD at 40 of 40. The rule now: every computed edge is put back on the
  1 nm grid (`nm()`: courtyard edges, keep-out limits, a polygon courtyard's strips, which were on 0.1 um),
  and a distance within half a nanometre of its limit is AT the limit and passes (`below()`, `HALF_NM`:
  courtyard gap, pad gap, hole to hole, hole in a courtyard, silk, courtyard / copper / hole to edge, copper
  under a pad). Nothing moves by more than that half nanometre, so a planner may put a box exactly on a limit.
  Prove it: a plan with a part exactly on a keep-out's edge passes, and the same plan 1 um (or 1 nm) inside
  fails -- 0 of 40 and 40 of 40 (board1 `tmpWork/D116_placefix/syn_probe.py`, old against new, in memory).
  Still noisy, on purpose: `auto`'s own tests (noise only makes it stricter than `check`) and the COPPER
  test's `< 0.3` mm box width, its stand-in for "axis-aligned track" (a rule to settle, not rounding).

- **`sites.py --bar` is repeatable** (fixed 2026-11-23, board1 R_EMMC / D126). It used to keep only the last of several
  `--bar` options, without a warning, so a search with the router window and a keep-out barred ran with the window
  open. Every `--bar` now counts, and so does `--bar "a;b"`. **PROOF:** the summary line prints `N --bar box(es)`;
  check that N is the number of boxes you passed.
