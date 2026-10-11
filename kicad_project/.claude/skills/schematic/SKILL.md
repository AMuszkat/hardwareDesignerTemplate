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
- **Every unit's body has its top-left corner at the origin, and Reference
  and Value sit just above the origin.** KiCad shares one field position
  between all units; this is the only position that suits every unit
  (`field-far`). Konnect cannot move a placed field afterwards.
- **Value is what a reader needs at a glance**: `100n`, `10k 1%`, `0.47u`,
  plus `(PROPOSED)` where the handoff marks it. The part number goes in an
  `MPN` field. A long Value runs across its neighbours.

## 1. Before the first sheet

- The board's `sym-lib-table` has `hw_sch` →
  `${KIPRJMOD}/../tools/sch/hw_sch.kicad_sym` (Konnect, `library` toolset).
  Its power glyphs keep a rail's name horizontal whichever way they point.
- Your own symbols meet the symbol rules above (the library is yours, like
  the footprints).
- A new board: `handoff/netlist.json` is the connectivity as data. When it
  says `"complete": true`, plan from it (`--handoff`) with the sheets and
  your symbols in `<board>/schematic.json` (`tools/sch/README.md`). While it
  lists parts in `prose_only`, the handoff still owes you those connections
  as data: draw the settled parts and ask for the rest in `QUESTIONS.md` —
  never type a pin table the handoff already has.

## 2. Plan, and look at it

```
tools/.venv/bin/python tools/sch/sch.py plan --board <board> --handoff                # new
tools/.venv/bin/python tools/sch/sch.py plan --board <board> --from-sch               # redraw
```

It writes `<board>/sch_plan/`, then proves the plan on a scratch copy:
netlist parity with the source, ERC, and `check`. **`PLAN PASSES`** — or every
remaining finding is a symbol or Value issue from the list above, which you
fix in the library and plan again. Never draw a plan whose parity is not
`IDENTICAL`.

Render the preview and look at every sheet:

```
kicad-cli sch export pdf -o /tmp/plan.pdf <board>/sch_plan/preview/<board>/<board>.kicad_sch
```

## 3. Draw, then frame

`get_project_info` first: the open project must be yours. Then, sheet by
sheet, make the calls in `sch_plan/<sheet>.ops.json` **in order, with the
arguments as written** (a redraw's first call clears the sheet), and
`save_project` after each sheet. The root's file comes last.

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

plus Konnect's `run_erc`. A finding after drawing is fixed with a Konnect edit
(move, rotate) and `check` again. A waiver (`<board>/schematic.json`, with a
`why`) is for a choice that is right, such as a connector kept as one 2 × 50
row; it is never for a collision.

⚠ **A redraw gives every symbol a new UUID.** If the PCB already holds the
parts, tell the user before any PCB work: Update PCB from Schematic with
*Re-link footprints to schematic symbols based on their reference
designators*.

## 5. Stop

The schematic stops for the user's review, once, with `check`, both
`parity` runs and ERC clean — the PDF beside it. A handoff-parity difference
is never "fixed" by editing the handoff's numbers into agreement with the
drawing: either the drawing is wrong, or it is a question.
