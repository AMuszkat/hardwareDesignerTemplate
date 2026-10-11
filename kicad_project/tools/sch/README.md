# `tools/sch` — the schematic, drawn to a standard and gated

**Yours**, like `tools/place`. Konnect still draws every sheet; these tools say
*where*, prove the layout before a single call is made, and gate the result.
The `schematic` skill is the workflow; this is the tool.

    tools/.venv/bin/python tools/sch/sch.py plan   --board board1 --netlist netlist.json   # a new board
    tools/.venv/bin/python tools/sch/sch.py plan   --board board1 --handoff                # a new board, complete handoff
    tools/.venv/bin/python tools/sch/sch.py plan   --board board1 --from-sch               # a redraw
    tools/.venv/bin/python tools/sch/sch.py check  --board board1 [--json]
    tools/.venv/bin/python tools/sch/sch.py frame  --board board1 [--remove]      # after drawing
    tools/.venv/bin/python tools/sch/sch.py parity --board board1                       # drawn == the plan
    tools/.venv/bin/python tools/sch/sch.py parity --board board1 --handoff             # drawn == the handoff
    tools/.venv/bin/python tools/sch/sch.py rules
    python3 tools/sch/hw_sch.py                    # regenerates hw_sch.kicad_sym

Plain Python 3 and `kicad-cli` — no `pcbnew`, so not `tools/kpy`. `--board`
works as everywhere in `tools/` (`boardsel.py`). `plan` writes under
`<board>/sch_plan/` only (gitignored); the one command that touches a board's
KiCad file is `frame`, and it writes graphics only (below).

## Why

A schematic that nets correctly can still be unreadable: a 120-pin unit on an
A1 sheet with its 30 capacitors in a refdes-ordered grid half a metre away,
every pin a label, no title, a root sheet of empty boxes. ERC passes all of
that. The standard below is the part ERC does not check, and `check` makes it
a number.

## The rules (`check`)

| rule | what | number |
|---|---|---|
| `paper` | the paper is the smallest ISO size the content fits; more than A2 means split the sheet | A4 … A2 |
| `frame` | nothing outside the frame or under the title block | — |
| `title` | the title block names the board, its revision and the date | — |
| `text` | no text overlaps another text (KiCad's own SVG gives every text's real extent) | — |
| `text-wire` | no wire runs through a text | — |
| `body` | no wire crosses a symbol body (a wire leaving that part's own pin excepted); no two bodies overlap | — |
| `rail-label` | a power net is drawn with power symbols, never a label — a local label named `+3V3` is a *sheet-local* net, not the rail | — |
| `global` | below the root, a signal leaves its sheet through a hierarchical label (so the root shows every block's interface) | — |
| `sheet-pin` | every sheet pin on the root is connected | — |
| `stitch` | two pieces of one net closer than 12.7 mm are one wire, not two labels | 12.7 mm |
| `section` | below the root, every symbol, wire and label sits inside a titled section frame (a KiCad text box, or a rectangle with a text in its top-left corner); frames never overlap; no section is empty | — |
| `section-title` | every frame has a title | — |
| `section-wire` | no wire crosses a frame: sections connect through labels and power symbols | — |
| `home` | a small part's section stands with the sections of an IC on its nets, as one cluster no more than 25.4 mm apart (a sheet without sections: the part itself within 101.6 mm of the IC) | 25.4 / 101.6 mm |
| `pins-side` | at most 40 pin rows on one side of one unit | 40 |
| `field-far` | a symbol's visible Reference and Value sit within 12.7 mm of its body | 12.7 mm |

KiCad's ERC is printed beside the result; it is the separate, older gate.

A finding that is right to keep is waived in `<board>/schematic.json`, with a
reason, exactly as a DRC exclusion is recorded:

    {"waive": [{"rule": "pins-side", "key": "board1:BergStak_10164228-1001:1",
                "why": "the CM5 connector is one 2 x 50 row; splitting it hides the pinout"}]}

`key` is printed with every finding (`--json` shows it). A waiver without a
`why` is refused.

## The plan (`plan`)

Per sheet:

- **Anchors** are the units with more than four pins (ICs, connectors), drawn
  unrotated. Every pin gets a 2.54 mm stub and its terminator in its own lane:
  a hierarchical label if the net leaves the sheet, a net label if not, a
  power glyph for a rail, a no-connect flag for a decided no-connect.
  Adjacent pins of one net share a spine (a stub per pin first — a spine along
  the pin ends would cut through the pin numbers) and one terminator.
- **Small parts** (≤ 4 pins) are grouped by function into blocks drawn with
  wires: a **bank** per rail (capacitors between the rail's wire and the
  ground wire; an inductor or bead that feeds the rail joins it, so a buck
  output reads LX → L → rail with its capacitors), a **pull bank** per rail
  (resistors hanging from one rail wire, each ending in its signal's label —
  straps, pull-ups), a **series stack**, and a **ladder** for anything else:
  a row per net two parts share, each part a column between its rows, the
  column order searched to keep bodies off rows (a crystal with its load
  capacitors and feedback resistor comes out as one wired block).
- **Sections.** Every anchor unit is a section of its own (`U1 unit 7 —
  MIMX8MM6CVTKZAA`); its blocks become one section per kind — `U1
  decoupling` (or `output filters and decoupling` when an inductor feeds a
  rail), `U1 pull-ups and pull-downs`, `U1 series parts`, `U1 test points`,
  a wired cluster by its lead part (`U1: Y1 24MHz`) or its parts — sized to
  stand beside the anchor. Parts no anchor on the sheet claims are `Other
  parts` (a cluster led by a regulator or transistor gets its own); the
  PWR_FLAGs are `Power sources`. Sections are packed bottom-left, the
  biggest IC first and each satellite next to a section of its own IC, onto
  the smallest ISO paper (A4 up) that holds them.
- The **root** gets a sheet symbol per sheet, a pin per hierarchical label,
  grouped by the sheet it goes to.

Then it **proves itself**: it writes a scratch KiCad project of the plan
(`sch_plan/preview/`), has `kicad-cli` net it up and compares the connectivity
pin for pin with the source (`netlist parity`), runs ERC on it and runs
`check` on it. `PLAN PASSES` means all three are clean. Look at the preview
(`kicad-cli sch export pdf sch_plan/preview/<board>/<board>.kicad_sch`) before
drawing anything.

Outputs: `sch_plan/<sheet>.ops.json` — the Konnect calls for that sheet, in
order, with complete arguments; `plan.json` (parts, sections and the title
block, what `frame` writes); `netlist.json` (what `parity` compares the
drawn board with); `preview/`.

## The frames (`frame`)

Konnect has no call for a graphic rectangle, a text box or the title block.
After Konnect has drawn a sheet's calls, `frame` writes each planned section
as a KiCad text box — one object: the frame, with its title in the top-left
corner — and sets the title block (Title, Date, Rev) on every sheet. It is
the one exception to "nothing here writes a board's file", and a narrow one:
it refuses unless every planned part already sits where the plan put it, it
touches no electrical item, it exports the netlist before and after and
fails if they differ, and `--remove` takes its frames out again (their UUIDs
are kept in `plan.json`). Run it with KiCad's sheets saved and closed, or
revert them afterwards, as with any `tools/` script.

### Power glyphs: `hw_sch`

A stock power symbol turned sideways draws its name vertically, and Konnect
cannot turn a field — on a dense IC side the names collide. `hw_sch.kicad_sym`
(made by `hw_sch.py`) has power glyphs pointing left, right, up and down,
positive and ground, whose name always reads horizontally, so a rail reads
like a label in its pin's lane. They are KiCad power symbols — the Value is
the net — and ordinary library symbols, so one `batch_place_components` places
a whole sheet's. The board's `sym-lib-table` needs the library once:
`hw_sch` → `${KIPRJMOD}/../tools/sch/hw_sch.kicad_sym` (`plan` says so when it
is missing).

### The handoff's netlist (`--handoff`)

`handoff/netlist.json` is the concept session's connectivity as data: every
part, and pin -> net for every pin the handoff fixes (the pinout, the
connector tables in the PART's pin numbers, and whatever else concept has
given as data). Two uses:

- **`parity --handoff`**, every board, after drawing: every handoff part is
  drawn and no placeholder is; every pin the handoff fixes sits on its net
  BY NAME, each net drawn as one; every no-connect and OPEN pin is left
  unconnected. It says how many pins it checked and how many parts are still
  only prose in the handoff. A difference is fixed in the drawing — or, when
  the handoff is what is wrong, is a question.
- **`plan --handoff`**, a new board, once the handoff marks the netlist
  `complete`: the layout comes from it directly. What is the board's own —
  the sheets, and per refdes (a glob is fine) the symbol, footprint, units
  and, if the handoff's value is too long for a Value, a short one — goes in
  `<board>/schematic.json`:

        {"sheets": [{"name": "Power", "file": "power.kicad_sch"}, {"name": "MCU", "file": "mcu.kicad_sch"}],
         "symbols": {"U2": {"lib_id": "MCU_ST_STM32F0:STM32F030F4Px", "footprint": "...", "units": {"1": "MCU"}},
                     "C*": {"lib_id": "Device:C", "footprint": "Capacitor_SMD:C_0402_1005Metric", "sheet": "Power"}},
         "pwr_flags": ["+5V", "GND"],
         "waive": []}

### The netlist format (`--netlist`)

For a board drawn from scratch, the board session writes its schematic as data
— the parts it will draw from `01_components`, the nets from `02_nets` and the
pin tables — once, in this form:

    {"title": "Compute module", "rev": "handoff rev 17",
     "sheets": [{"name": "Power", "file": "power.kicad_sch"}, ...],
     "parts": {"U1": {"lib_id": "board1:MIMX8MM6CVTKZAA", "value": "MIMX8MM6CVTKZAA",
                      "footprint": "board1:...", "fields": {"Datasheet": "..."},
                      "units": {"1": "Power", "2": "SoC"}, "dnp": false}, ...},
     "nets": {"EMMC_CLK": [["U1", "W22"], ["U300", "M6"]], ...},
     "rails": ["GND", "+3V3", ...],
     "nc": [["U1", "A2"], ...],
     "pwr_flags": ["+1V8", ...]}

Symbols come from the board's `sym-lib-table` and KiCad's own libraries.
`--from-sch` reads the same from a drawn schematic (and KiCad's netlist of it).

## What Konnect cannot do — and what that costs

- **The title block and graphic frames.** No call sets or draws them;
  `frame` does, as above.
- **Field positions.** No call moves or turns a Reference or Value. A
  library field position is shared by every unit of a symbol, so a big
  multi-unit symbol whose Value suits its tallest unit lands it far from the
  small ones (`field-far`, and the text collisions that follow). Fix it in the
  library: each unit's body with its top-left corner at the origin, Reference
  and Value just above the origin.
- **A redraw changes every symbol's UUID** (parts are deleted and placed again:
  Konnect moves a multi-unit part only as a whole). On a board whose PCB
  already holds the parts, the PCB must be updated from the schematic with
  *Re-link footprints to schematic symbols based on their reference
  designators* — in KiCad, by the user — before any further PCB work.
- **The DNP flag**, as before: a field carries it; KiCad's own flag is set by
  hand.

## Limits

A plan is one feasible readable layout, not the only one: blocks are placed
by rule, not by taste. When `check` still reports something after drawing,
the fix is a Konnect edit (move, rotate) and `check` again — never a waiver
for a collision. The rules' numbers are constants at the top of `check.py`.
