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
    tools/kpy tools/place/place.py park   --board board1 [--refs H1,H2]  # KiCad closed
    tools/kpy tools/place/place.py drc    --board board1                  # DRC cached by content (drccache.py)
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
