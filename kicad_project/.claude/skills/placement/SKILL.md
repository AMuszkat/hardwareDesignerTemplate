---
name: placement
description: >
  Place PCB components group by group from the floorplan, check the plan
  before moving anything, compact a group, refresh footprints on the board
  after a library change, and place reference designators. Triggers on
  "place U…", "place the … group", "move closer", "compact", "tighter",
  "placement", "floorplan", "labels", "reference designators", "silk
  overlaps", "refresh footprints", "update footprints from library".
---

# Placement, one group at a time

A board is placed **group by group, and each group is reviewed** before the
next: its request in `REVIEW.md`, the verdict in `handoff/15_reviews.md`. Tools: Konnect for moves and refreshes,
`tools/place/place.py` for checks (read `tools/place/README.md`), and
`<board>/placement.json` for keep-outs and group lists. Every `tools/place`
script runs as `tools/kpy tools/place/<script>.py` (it imports `pcbnew`).

## 0. Before a group

- The fab rules must be on the board: `board_setup.py check` exits 0 (the
  `board-setup` skill). Placement against default rules is placement twice.
- Read `handoff/10_layout.md` for the group (loop areas, "at the pin",
  sensitive zones, the deliberate choices in `04_deliberate.md`) and the
  group's area in `handoff/11_floorplan.svg` if the handoff has one. Put the
  floorplan's reservations into `placement.json` `keepouts` before the first
  part moves.
- `tools/kpy tools/place/place.py sync --board <board>` exits 0. If it does
  not, the board is not what the library says: fix that first (§4).

## 1. Plan in a file, check it, then move

Hand-place what carries a constraint: the ICs, the connectors on their edges,
anything whose pin order decides a topology. Let `place.py auto` do the
supporting passives — it scores every position and rotation in a region by
weighted distance to the pins the part connects to and takes the cheapest
legal spot. Order matters: big or critical parts first, because each placed
part becomes an obstacle.

Write the group as `{ "REF": [x, y, rot] }` in absolute board mm. Start from
the IC, then its pin-side passives, keeping loops short in the order
`10_layout.md` ranks them. Then:

```
tools/kpy tools/place/place.py check --board <board> plan.json
```

Iterate until `CHECKS PASS`, reading the net-span report: a move that makes a
critical loop longer is wrong even when it passes. Then apply:

```
set_component_placements(board, placements=[…])     # KiCad open: one undo step
save_project()
tools/kpy tools/place/place.py apply --board <board> plan.json   # KiCad closed; flips parts too ([x, y, rot, "B"])
```

Confirm the saved positions match the plan. Then run
`tools/kpy tools/place/place.py drc --board <board>` and compare violation
counts for the placed area before and after. ⚠ Never bare `kicad-cli pcb drc`:
it skips the schematic-parity test unless told otherwise, and an empty parity
list reads like a pass. Last, render the area and look at it:

```
kicad-cli pcb export pdf --layers F.Cu,F.SilkS,F.CrtYd,Edge.Cuts \
    --mode-single --black-and-white -o out.pdf <board>.kicad_pcb
tools/.venv/bin/python -c "import pymupdf as m; p=m.open('out.pdf')[0]; \
  b=[v[1] for v in p.get_bboxlog()]; \
  r=m.Rect(min(x[0] for x in b)-4, min(x[1] for x in b)-4, \
      max(x[2] for x in b)+4, max(x[3] for x in b)+4); \
  p.get_pixmap(matrix=m.Matrix(6,6), clip=r).save('out.png')"
```

(`pcb export pdf` has no fit-to-board option; the bbox crop above replaces it.
PyMuPDF lives in `tools/.venv`, made by `tools/setup.sh`.)

When the group's parts are placed, remove their reservation from
`placement.json` `keepouts` and add the group under `groups`.

## 1b. Two-sided placement

If the floorplan puts parts on the **back**, a plan entry `[x, y, rot, "B"]`
flips the part; only `place.py apply` can do that.

- **A part may not sit over a plated hole**, on either side: a back-side part
  under a through-hole connector's body still collides with its holes. `check`
  enforces it (`pth_inside_courtyard`).
- **Reserve a BGA's fanout before filling the back under it**: one dog-bone via
  per used ball, toward the nearest package edge, as `avoid` in the auto spec.
- Courtyards and silk only collide **within one side**; pads collide when they
  share a copper layer, and holes collide with everything.

## 1c. Can every pad reach a via?

`check` does not know the copper. For decoupling, run
`tools/kpy tools/place/sites.py --board <board> --plan plan.json` (or `--ref
--box` to search): it prints the nearest legal via site per pad, on every
layer. A cap whose pads cannot drop to their planes decouples nothing.

## 2. Spacing

- Courtyards are IPC nominal, 0.25 mm beyond pads or body (`fp.py tidy
  --courtyard 0.25`). A larger one (KiCad's rework courtyards on some BGA and
  DSBGA footprints) silently spreads a group; fix the footprint, not the plan.
- Parts whose pads connect straight across: ~0.15 mm courtyard gap.
- Keep a copper channel of at least track + 2 × clearance (plus margin) where
  a signal must escape between parts, and name the channel in the report so
  the next compaction keeps it.
- Different-net pads on different parts ≥ web + 2 × mask expansion. `check`
  enforces it.
- "Could be closer" has three usual causes: an oversized courtyard, slack left
  in an earlier plan (read `place.py gaps`), and silk outlines that made the
  group *look* crowded. Report which it was.
- A block that must be re-arranged against several constraints at once is a
  search: `tools/place/pack.py` (spec in its docstring), then `check`.

## 3. Labels last

Once a round of placement is agreed, close KiCad (see the `board-setup` skill
for closing it safely) and run:

```
tools/kpy tools/place/place.py labels --board <board> --dry-run
tools/kpy tools/place/place.py labels --board <board>
```

Report the hidden list: those designators are only on F.Fab. Labels next to
unplaced areas will move again when their neighbours arrive, so run it again at
the end of placement.

## 4. Library changed? Refresh the board, then prove it

After `fp.py tidy`, `derive` or a land change:

- **Same library id**, KiCad open: Konnect `update_footprints_from_library`
  (dry run, then apply with the plan revision) and `save_project`.
  ⚠ **Never KiCad's own Tools → Update Footprints from Library after the files
  changed while KiCad was open.** It writes back its stale in-memory copy.
  KiCad closed: `tools/kpy tools/place/refresh.py` (front-side parts only; it
  refuses while a lock file exists).
- **New library id** (a derived stock footprint, a renamed land): change the
  symbol's Footprint field with Konnect `batch_edit_schematic_components` (one
  call per sheet; the schematic editor closed). Then the user runs F8 with
  *Replace footprints* ticked, *Delete footprints with no symbols* unticked
  (mounting holes are board-only). With KiCad closed, a scripted `pcbnew` swap
  also works, **in this order**: `board.Add(new)` first, THEN
  `pad.SetNet(board.FindNet(name))` per pad (nets set before `Add` are silently
  lost), carry `Datasheet` / `Description` / custom fields with `SetField`,
  `SaveBoard`, then `place.py sync` AND `place.py drc`.
- **A part the schematic has and the board has not**, KiCad closed:
  `tools/kpy tools/place/addfp.py --board <board> REF …`.
- A footprint refresh Konnect refuses for an unsupported field (`LCSC Part`):
  `fp.py tidy <name> --drop-property "LCSC Part"`, then refresh.

Then `place.py sync` AND `place.py drc` must exit 0. `sync` proves the
geometry, `drc` proves the nets and the symbol links; neither implies the other.

## Closing KiCad safely

Konnect `save_project` first; there is no quit command over the IPC API, so
`kill -TERM` the `kicad` process (KiCad does not rewrite project files on
SIGTERM); confirm `pgrep -x kicad` is empty; only then remove the stale `~*.lck`
files. Never remove a lock file while any KiCad process runs. Whether you may
close KiCad yourself is the user's call — ask once, and record a standing
permission in `ROUTING_PLAN.md` §4.

## Rules

1. **No move without `check`.** A plan that passed yesterday is re-checked
   today: neighbours moved.
2. **One Konnect placement call per plan**, so one undo step.
3. **Placement never changes a rule or a land.** A clearance the layout cannot
   meet is a QUESTIONS.md item.
4. **Deliberate positions stay** (`04_deliberate.md`) even when a compaction
   would like them elsewhere.
5. **Each group stops for review**: with `place.py check` and `place.py drc`
   clean, write the request in `REVIEW.md` and ring concept (`boardN RVn ready — pcb: <title>`; relay off: tell the user that line). The next group starts on PASS;
   routing starts after the last placement group passes (the `routing` skill).
