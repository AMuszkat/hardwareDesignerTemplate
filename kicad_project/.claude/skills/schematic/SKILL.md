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

- **A sheet is a set of titled sections.** Every functional block sits in a
  frame with its title in the corner — `U1 — MIMX8MM6CVTKZAA`, `U1
  decoupling`, `U1 pull-ups and pull-downs`, `U1: Y1 24MHz`, `Power sources`
  — so a reader sees at a glance what each group is and whose it is. Nothing
  is drawn outside a frame, frames never overlap, and no wire crosses one:
  sections talk through labels and power symbols (`section`,
  `section-title`, `section-wire`). An IC's sections stand together as one
  cluster (`home`).
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
- **Nothing collides**: no text on text, no wire through text or a body
  (`text`, `text-wire`, `body`).
- **The title block names the board, the revision and the date** (`title`).

### Symbols — yours, in `lib/`

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
not 0, `delete_symbol` and create again with every pin's x shifted by
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
kicad-cli sch export pdf -o /tmp/plan.pdf <board>/sch_plan/preview/<board>/<board>.kicad_sch
```

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
`add_component_annotation` for a field the symbol does not have yet (MPN,
DNP), and `import_sheet_pins` + `edit_sheet_pin` for a root sheet's
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
parts, tell the user before any PCB work: Update PCB from Schematic with
*Re-link footprints to schematic symbols based on their reference
designators*. KiCad's own DNP / in_bom flags are not set by any Konnect
call; they stay a task for the user in eeschema, and a redraw does not lose
ones already set (check `(dnp yes)` counts before and after).

## 5. Stop for review

With `check`, both `parity` runs and ERC clean, the schematic is requested for
review: write the request in `REVIEW.md` and ring concept (`boardN RVn ready — schematic: <title>`; relay off: tell the user that line). Scope: the sheets this change touched ("all"
for a first drawing or a redraw); Gates: each run's last line. Then stop — the
concept session's reviewer checks a frozen copy against the handoff and the
datasheets, and the verdict comes back in `handoff/15_reviews.md`. CHANGES:
fix or dispute each must-fix finding, run the gates again, request again
("fixes for RVn"). PASS: the schematic is done; the PCB may use it. A handoff-parity difference
is never "fixed" by editing the handoff's numbers into agreement with the
drawing: either the drawing is wrong, or it is a question.
