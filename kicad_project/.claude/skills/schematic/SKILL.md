---
name: schematic
description: >
  Draw or redraw a board's schematic to the project's drawing standard: plan
  the layout from the netlist, prove it, draw it with Konnect, gate it.
  Triggers on "draw the schematic", "schematic", "redraw", "sheet", "make the
  schematic readable", "paper size", "title block", "schematic review",
  "check the schematic", "the sheets are hard to read".
---

# A schematic someone can read

ERC proves the schematic nets up. It does not prove anyone can read it. The
standard here is the other half, and `tools/sch/sch.py check` turns it into
numbers (`tools/sch/README.md` has each rule and why). Konnect's
`kicad-schematic` skill is the tool manual; this skill is the workflow and
wins where they disagree.

## The standard

- **A sheet is a set of functional groups, one frame each.** A frame holds
  one function — `USB-C`, `Module connector`, `Debug console`, `Ethernet`,
  `Power sources` — the main part(s) with every block that serves them
  (decoupling, ESD, pull-downs, buttons) inside it; not a frame per part
  and one per block. As many frames as functions you can name, and no
  more. A title names the function, never a refdes: the board's `"group"`
  on each part in `<board>/schematic.json` (the same name on J1, J2 and the
  standoffs makes one frame; a multi-unit part may give `{"1": …}` per
  unit), else the part's Value. A connector is a group of its own however
  few pins it has (a strictly local header stays with its circuit by
  `group`). Inside a frame a block is captioned — small plain text — only
  where the board says what it is for: `"caption": "CC pull-downs (Rd)"`,
  `I2C pull-ups`, `MagJack LED resistors`; the plan writes no caption of
  its own. Nothing is drawn outside a frame, frames never overlap, and no
  wire crosses one: groups talk through labels and power symbols
  (`section`, `section-title`, `section-wire`); a small part stands in
  the frame of the IC it serves (`home`). A reviewer reads a title and a
  caption as claims.
  The planner forms blocks itself (a rail's decoupling across ICs, a
  rail's pull-ups across functions), and every part of a block must name
  the same group or none (`plan` stops otherwise): **give `group` to
  anchors (ICs, connectors) and to small parts that only serve one of them;
  never to a decoupling capacitor or a pull-up on a shared rail** -- those
  join their IC's frame by themselves. Iterate: plan, read the stop, drop
  the group from the part it names.
- **One function per section, wired inside.** The decoupling bank, the
  pull-ups, the crystal with its load capacitors, the feedback divider —
  each a block drawn with wires between its parts. A label joins sections;
  it never joins two things a short wire could join (`stitch`).
- **Rails are power symbols, never labels** (`rail-label`). A local label named
  `+3V3` is a sheet-local net, not the rail.
- **A signal leaves its sheet through a hierarchical label**, and the root
  sheet carries every sheet's interface as sheet pins (`global`, `sheet-pin`).
- **The paper fits the drawing**: the smallest ISO size that holds it, A4 to
  A2; a sheet that needs more is two sheets (`paper`).
- **Nothing collides**: no text on text, no wire through text or a body, no
  text into a body outline or a power glyph (`text`, `text-wire`,
  `text-graphic`, `body`). A ground glyph reaches into the next pin's lane,
  where KiCad draws that pin's label text: the plan runs such a label on
  past the glyph.
- **The title block names the board, the revision and the date** (`title`).

### Symbols — yours, in `lib/`

- **A pin number wider than its pin crosses the body outline**
  (`text-graphic`): three characters (`100`, `A10`) need 3.81 mm, four
  (`AB10`, a big BGA's balls) 5.08 mm. No Konnect call changes a pin's
  length, so lengthen it in the library:
  `symfields.py --board <board> --pin-length SYMBOL ...` gives each side of
  each unit the shortest pin that holds its widest number, moves the
  connection points out and keeps the body ends, numbers, names and types
  (proven against a kicad-cli resave; backup first). A sheet then needs the
  plan's `update_symbols_from_library` (a redraw) -- every lengthened pin's
  connection point moved. A new symbol: give `create_symbol` the length and
  put left pins at x = -length (right pins at width + length) so the body
  still lands at the origin.
- **At most 40 pin rows on a side of a unit** (`pins-side`). Split a big IC
  into units by function — each bank's signals, each power domain, the
  grounds in their own unit — the way the datasheet's ball map groups them.
  A group longer than 80 pins becomes `NAME_1`, `NAME_2`, split across both
  sides.
- **Every unit's body has its top-left corner at the origin, and Reference
  and Value sit just above the origin.** KiCad shares one field position
  between all units; this is the only position that suits every unit
  (`field-far`). Konnect cannot move a placed field afterwards.
- **Value is what a reader needs at a glance**: `100n`, `10k 1%`, `0.47u`,
  plus `(PROPOSED)` where the handoff marks it. The part number goes in an
  `MPN` field. A long Value runs across its neighbours.
- **Two-pin parts whose stock Value sits inside the body** (`Device:R`) run
  any Value longer than the body over their own wires, in every orientation.
  Use the board's derived copy with the Value beside the body:
  `symfields.py --derive Device:R` → `<board>:R`, for every resistor.
- **A stock multi-unit symbol whose fields sit on a unit's pin numbers**
  (`74xGxx:74LVC2G07`'s power unit) is redrawn in the board library with the
  same pins and units as origin-anchored boxes.

**Making a unit land at the origin with Konnect's `create_symbol`** — it
takes a pin's x as its connection point and grows a body too narrow for the
names, centred on what you asked, so the body rarely lands where the pins
say. Two passes, no guessing: create with left pins at x = −2.54 and the
first row at y = −2.54 (the top edge is then exactly 0); read the response's
resolved left-pin x `lx`; the body's left edge is `lx + 2.54`; if that is
not 0, `delete_symbol` (it finds only a symbol Konnect wrote and nothing has
resaved since — after `symfields.py`, `symfields.py --delete`; `create_symbol`
on a name already there adds a second copy) and create again with every pin's x shifted by
−(lx + 2.54). Then:

```
tools/.venv/bin/python tools/sch/symfields.py --board <board> SYMBOL ...    # fields above the corner
```

Verify a rebuilt symbol against the old one pin by pin (number, name,
type) before any sheet uses it: a rebuild is where a ball silently changes
type.

## 1. Before the first sheet

- The board's `sym-lib-table` has `hw_sch` →
  `${KIPRJMOD}/../tools/sch/hw_sch.kicad_sym` (Konnect
  `register_symbol_library`, scope project; `plan` says so when missing).
- Your own symbols meet the symbol rules above (the library is yours, like
  the footprints).
- A new board: `handoff/netlist.json` is the connectivity as data. When it
  says `"complete": true`, plan from it (`--handoff`) with the sheets and
  your symbols in `<board>/schematic.json` (`tools/sch/README.md`). While it
  lists parts in `prose_only`, the handoff still owes you those connections
  as data: draw the settled parts and ask for the rest in `QUESTIONS.md` —
  never type a pin table the handoff already has.
- A redraw whose symbols change (units split, a ball retyped, Values
  shortened) does not plan `--from-sch`: write the source netlist as data
  (`--netlist`, the README's format) from the drawn schematic plus the
  changes, so the plan proves the new drawing against the intended one.
- Back up every sheet and the library before the first Konnect call
  (`<board>/.sch_backups/<name>/`); a redraw deletes everything on a sheet.

## 2. Plan, and look at it

```
tools/.venv/bin/python tools/sch/sch.py plan --board <board> --handoff                # new
tools/.venv/bin/python tools/sch/sch.py plan --board <board> --from-sch               # redraw, same symbols
tools/.venv/bin/python tools/sch/sch.py plan --board <board> --netlist <json>         # redraw, changed symbols
```

It writes `<board>/sch_plan/`, then proves the plan on a scratch copy:
netlist parity with the source, ERC, and `check` (the board's waivers
apply). **`PLAN PASSES`** — or every remaining finding is a symbol or Value
issue from the list above, which you fix in the library and plan again. A
finding the plan cannot clear by rule (a wire through a text the block
layout puts there) may be fixed after drawing with a Konnect move; note it
before drawing. Never draw a plan whose parity is not `IDENTICAL`.

Render the preview and look at every sheet:

```
tools/.venv/bin/python tools/sch/sch.py pdf --board <board>
```

It writes `sch_plan/preview.pdf` and one `preview-N.png` per sheet beside it.

## 3. Draw, gate each sheet, then frame

`get_project_info` first: the open project must be yours; the schematic
editor stays closed while Konnect writes. **Before spawning any agent, load
Konnect's `sch_components`, `sch_wiring` and `sch_batch` toolsets yourself**
(`load_toolset`): an agent's tool list is fixed when it is spawned, so one
started before them sees no `batch_add_wire` or `add_schematic_net_label`,
and its own `load_toolset` does not reach it (board2: the first agent drew
nothing; the one spawned after the load drew 720 of 720). Each sub-sheet is independent of
the others until the root, so **draw them in parallel, one agent per sheet**,
with the prompt in `draw_sheet.md` beside this skill (fill the placeholders).
The root sheet is drawn last, by you, from its ops.json.

Each agent makes the calls in `sch_plan/<sheet>.ops.json` **in order, with
the arguments as written**, splitting only a long list across consecutive
calls of the same tool, then gates its sheet:

```
tools/.venv/bin/python tools/sch/sch.py drawn --board <board> <sheet>.kicad_sch   # 0 missing, 0 extra, fields as planned
```

The plan already carries the three calls Konnect needs and does not do by
itself (the README's "What Konnect cannot do" has the whole list):
`update_symbols_from_library` + `reset_schematic_field_positions` right after
the parts are placed (a sheet keeps its old copy of a rebuilt symbol),
`add_component_annotation` for a field the symbol does not have yet (MPN),
and `import_sheet_pins` + `edit_sheet_pin` for a root sheet's
left-edge pins (`add_sheet_pin` makes every pin face right, and KiCad then
shorts a left pin to the right-edge pin at its height).

Konnect has no call for a section frame or the title block, so those are
written by the tool, graphics only, once every part is where the plan put it
(it checks, and proves the netlist unchanged):

```
tools/.venv/bin/python tools/sch/sch.py frame --board <board>
```

KiCad must not have the sheets open with unsaved edits when `frame` runs;
reopen the project (or File → Revert) afterwards so it reads the frames in.
**Run `frame` before any post-draw Konnect fix** (§4): it refuses while a
part is not where the plan put it, and a part moved to clear a finding is
exactly that. It also puts each root sheet box's "File:" field under its own
box (Konnect's `edit_sheet` leaves it where the old box ended).

While a redraw is half done, the board's sheets are not one consistent
netlist: a source re-read from them (`--from-sch`, a board build script)
then has the wrong nets. Read it from the last plan's preview
(`sch_plan/preview/<board>/`), which `plan` proved identical to its source.

## 4. Prove what was drawn

```
tools/.venv/bin/python tools/sch/sch.py parity --board <board>              # PARITY OK: drawn == the plan
tools/.venv/bin/python tools/sch/sch.py parity --board <board> --handoff    # HANDOFF PARITY OK: drawn == the handoff
tools/.venv/bin/python tools/sch/sch.py check  --board <board>              # CHECKS PASS
```

plus ERC (`check` prints kicad-cli's). `parity` is the one that catches a
root-sheet short (two interfaces merged); run it before `frame`. A finding
after drawing is fixed with a Konnect edit (move, rotate, re-route a jog)
and `check` again; `drawn` then reports that spot as planned-vs-drawn, which
is expected — parity is the final word. A waiver (`<board>/schematic.json`,
with a `why`) is for a choice that is right, such as a connector kept as one
2 × 50 row; it is never for a collision.

⚠ **A redraw gives every symbol a new UUID.** If the PCB already holds the
parts, update it before any PCB work, KiCad closed:
`tools/kpy tools/place/schupdate.py --board <board>` (Update PCB from
Schematic with *Re-link by reference*, for the parts already there), then
`board_setup.py apply` (net names may have changed) and `place.py drc`:
schematic parity 0.

⚠ **KiCad's own DNP and Exclude-from-BOM flags are set by no Konnect call,
and a redraw places every part again without them.** After the last redraw,
before the review request:

```
tools/.venv/bin/python tools/sch/sch.py flags --board <board>     # KiCad closed
```

sets DNP on exactly the plan's DNP parts and Exclude from BOM on
`schematic.json`'s `"exclude_from_bom"` globs (pad-only parts, fiducials,
plated holes, test pads), parsed tree, netlist proven unchanged, read back.
`drawn`, `parity` and `parity --handoff` fail, naming each part, until the
DNP flags are right. A field named DNP is not the flag: write none.

## 5. Stop for review

**A small edit after an answer is still a plan first.** For example, a pin taken off a net:
1. `sch.py plan --handoff` re-makes `sch_plan/` against the new revision.
2. Draw only what `drawn` then lists as missing or extra; the re-plan may move a neighbour's label too.
3. Run `sch.py frame` for the title block's revision.

A Konnect edit made straight on the sheet leaves `sch_plan/` describing the old drawing. `parity --handoff` passes,
but plain `parity` fails, and replaying the plan would undo the edit (board2's RV17).

With `check`, both `parity` runs and ERC clean, the schematic is requested for
review: write the request in `REVIEW.md` and ring concept (`boardN RVn ready — schematic: <title>`; relay off: tell the user that line). Scope: the sheets this change touched ("all"
for a first drawing or a redraw); Gates: each run's last line. Then stop — the
concept session's reviewer checks a frozen copy against the handoff and the
datasheets, and the verdict comes back in `handoff/15_reviews.md`. CHANGES:
fix or dispute each must-fix finding, run the gates again, request again
("fixes for RVn"). PASS: the schematic is done; the PCB may use it. A handoff-parity difference
is never "fixed" by editing the handoff's numbers into agreement with the
drawing: either the drawing is wrong, or it is a question.
