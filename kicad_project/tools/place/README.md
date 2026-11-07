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
    tools/kpy tools/place/place.py labels --board board1 [--dry-run]
    tools/kpy tools/place/place.py park   --board board1 [--refs H1,H2]  # KiCad closed
    tools/kpy tools/place/place.py drc    --board board1
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

## `<board>/placement.json`

Board data the scripts must not hard-code (the skeleton comes with every new
board):

| key | meaning |
|---|---|
| `keepouts` | `{name, rect: [x0, y0, x1, y1], side?, allow?}`, board-relative mm from the outline's top-left, `null` = open side. Reservations from `handoff/11_floorplan.svg` for parts not placed yet (connectors with their mates, a big IC) and zones other groups may not enter. **Delete a reservation when its part is placed**: from then on the real courtyard is the obstacle |
| `groups` | name → references. `check` prints each group's extent |
| `fixed` | parts exempt from the outline/keep-out test (mounting holes: their standoff courtyard overhangs the edge by design) and expected as board-only footprints by `drc` |
| `overhang` | connectors whose courtyard may cross the outline (right-angle male pins mate off-board) |
| `ignore_nets` | nets left out of the connection-length report (GND pours) |
| `min_courtyard_gap` | smallest courtyard gap `check` accepts (0.03 mm: touching is legal in DRC, float noise is not) |
| `hole_to_edge` | minimum hole-to-edge distance, from `14_fab.md` (Board Setup has no field for it). Starts as `null`: `check` refuses until you transcribe it |
| `net_report_limit` | how many nets `check` lists in its span report |
| `labels.hide` | references whose designator never goes on silk |
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

## `labels`

1. Every designator gets the Board Setup minimum silk text (typed from
   `14_fab.md` by `board_setup.py`).
2. For each footprint on the board, candidate spots ring its courtyard: text at
   0° or 90°, up to 2 mm out, on a 0.25 mm step. A spot must clear pads (copper
   + mask expansion + silk clearance), holes, all other silk, **every
   courtyard** (a label under a part is unreadable), the board edge, and the
   labels already placed.
3. It places the **most constrained** label first, at the spot that leaves the
   others the most room. Pure greedy walls labels in.
4. A label with no legal spot is **hidden on silk** and listed. Stock and
   derived footprints carry `${REFERENCE}` on F.Fab, so the assembly drawing
   still has it.

Run it when a placement round is finished; labels are the last thing to move.
It writes `<board>.kicad_pcb.labels-bak` first.

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
- `addfp.py` adds footprints the schematic has and the board has not (from the
  schematic's own netlist; nets set **after** `Add`), parked off the board;
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
