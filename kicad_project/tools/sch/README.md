# `tools/sch` — the schematic, drawn to a standard and gated

**Yours**, like `tools/place`. Konnect still draws every sheet; these tools say
*where*, prove the layout before a single call is made, and gate the result.
The `schematic` skill is the workflow; this is the tool.

    tools/.venv/bin/python tools/sch/sch.py plan   --board board1 --netlist netlist.json   # a new board
    tools/.venv/bin/python tools/sch/sch.py plan   --board board1 --handoff                # a new board, complete handoff
    tools/.venv/bin/python tools/sch/sch.py plan   --board board1 --from-sch               # a redraw
    tools/.venv/bin/python tools/sch/sch.py check  --board board1 [--json]
    tools/.venv/bin/python tools/sch/sch.py drawn  --board board1 [SHEET ...]     # after EACH sheet
    tools/.venv/bin/python tools/sch/sch.py frame  --board board1 [--remove]      # after drawing
    tools/.venv/bin/python tools/sch/sch.py parity --board board1                       # drawn == the plan
    tools/.venv/bin/python tools/sch/sch.py parity --board board1 --handoff             # drawn == the handoff
    tools/.venv/bin/python tools/sch/sch.py rules
    python3 tools/sch/hw_sch.py                    # regenerates hw_sch.kicad_sym
    tools/.venv/bin/python tools/sch/symfields.py --board board1 SYMBOL ...    # fields above the top-left corner
    tools/.venv/bin/python tools/sch/symfields.py --board board1 --derive Device:R   # stock symbol in, Value beside
    tools/.venv/bin/python tools/sch/symfields.py --board board1 --delete SYMBOL ... # out, to rebuild with Konnect

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
| `text-graphic` | no text runs into a symbol's drawn lines — a body outline, a power glyph. A ground glyph's triangle reaches 1.27 mm into the next lane, where KiCad draws the neighbouring label's text (above its wire); a pin number wider than its pin (`100`, `A10` on a 2.54 mm pin) crosses the body outline. `text` cannot see either: it compares texts with texts | — |
| `body` | no wire crosses a symbol body (a wire leaving that part's own pin excepted); no two bodies overlap | — |
| `rail-label` | a power net is drawn with power symbols, never a label — a local label named `+3V3` is a *sheet-local* net, not the rail | — |
| `global` | below the root, a signal leaves its sheet through a hierarchical label (so the root shows every block's interface) | — |
| `sheet-pin` | every sheet pin on the root is connected | — |
| `stitch` | two pieces of one net closer than 12.7 mm are one wire, not two labels | 12.7 mm |
| `section` | below the root, every symbol, wire and label sits inside a titled section frame (a KiCad text box, or a rectangle with a text in its top-left corner); frames never overlap; no section is empty | — |
| `section-title` | every frame has a title, and no two frames on a sheet share one | — |
| `section-wire` | no wire crosses a frame: sections connect through labels and power symbols | — |
| `home` | a small part's section stands with the sections of an IC on its nets, as one cluster no more than 25.4 mm apart (a sheet without sections: the part itself within 101.6 mm of the IC). A part whose only nets are rails counts only if it is decoupling (C, L, a bead); a connector never counts — it is an interface, not a satellite, however few pins it has | 25.4 / 101.6 mm |
| `pins-side` | at most 40 pin rows on one side of one unit | 40 |
| `field-far` | a symbol's visible Reference and Value sit within 12.7 mm of its body | 12.7 mm |

KiCad's ERC is printed beside the result; it is the separate, older gate.

A finding that is right to keep is waived in `<board>/schematic.json`, with a
reason, exactly as a DRC exclusion is recorded:

    {"waive": [{"rule": "pins-side", "key": "board1:BergStak_10164228-1001:1",
                "why": "the CM5 connector is one 2 x 50 row; splitting it hides the pinout"}]}

`key` is printed with every finding (`--json` shows it). A waiver without a
`why` is refused. `plan` applies the same waivers when it checks its preview.

## The plan (`plan`)

Per sheet:

- **Anchors** are the units with more than four pins (ICs, connectors), drawn
  unrotated. Every pin gets a 2.54 mm stub and its terminator in its own lane:
  a hierarchical label if the net leaves the sheet, a net label if not, a
  power glyph for a rail, a no-connect flag for a decided no-connect.
  Adjacent pins of one net share a spine (a stub per pin first — a spine along
  the pin ends would cut through the pin numbers) and one terminator.
  KiCad draws a label's text on one side of its wire (above it; left of it
  when vertical) — into the next lane. Where a power glyph stands in that
  lane the label's stub runs on past the glyph and its name
  (`Ctx.terminate_all`), on an anchor and on a small connector alike.
- **Small parts** (≤ 4 pins) are grouped by function into blocks drawn with
  wires: a **bank** per rail (capacitors between the rail's wire and the
  ground wire; an inductor or bead that feeds the rail joins it, so a buck
  output reads LX → L → rail with its capacitors), a **pull bank** per rail
  (resistors hanging from one rail wire, each ending in its signal's label —
  straps, pull-ups), a **series stack**, and a **ladder** for anything else:
  a row per net two parts share, each part a column between its rows, the
  column order searched to keep bodies off rows (a crystal with its load
  capacitors and feedback resistor comes out as one wired block).
- **Sections are functional groups.** A frame holds one function: the
  anchor unit(s) it is named after and every block that serves them — the
  connector with its decoupling, its ESD diodes, its pull-downs, its
  buttons; one frame, not one per part and one per block. Inside, each
  anchor stands with its blocks beside it as a row (decoupling first, then
  tallest first, fitted to the anchor's height); the rows of a group, and
  the loose blocks the board puts in it, are packed about square. The title
  is the board's — `"group": "Module connector"` on each part, the same
  name on two parts making one frame (J1 + J2 + the standoffs) — else the
  anchor's Value (`USB-C`, `AMP LINK`); a title never carries a refdes. All
  units of a part share a frame unless `group` is `{"1": "…", "2": "…"}`.
  A connector is an interface of its own whatever its pin count (a 3-pin
  console header is a group, not a satellite of the IC it wires to); a
  strictly local header is kept with its circuit by `group`. A block inside
  a frame is captioned — plain text above it — only where the board says
  what it is for: `"caption": "CC pull-downs (Rd)"` on each of its parts
  (`I2C pull-ups`, `MagJack LED resistors`, `USB ID`); a plan knows what
  parts *are* (`role()`: decoupling, pull-ups, ESD protection, buttons,
  headers — it keeps a block to one role so a caption never lies), not what
  they are *for*, and writes no caption of its own. Parts no anchor on the
  sheet claims are the group the board names, else a lone connector is a
  group titled by its Value, a cluster led by a regulator or transistor by
  its lead's Value, and the rest share `Other parts`; the PWR_FLAGs are
  `Power sources`. Groups are packed bottom-left, the biggest anchor's
  first, onto the smallest ISO paper (A4 up) that holds them — a few
  orders tried per paper (as given, tallest, biggest, widest first), since
  bottom-left packing is greedy (`SCH_PACK_DEBUG=1` prints each paper's
  best).
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

## The sheet gate (`drawn`)

After each sheet is drawn, before the next: the drawn sheet against the
plan's preview as sets — every symbol (reference, unit, position, rotation,
lib_id, Value), wire, label, junction, no-connect, sheet and sheet pin — and
every field the sheet's ops.json asked for (Footprint, Datasheet, MPN)
with its value — and KiCad's own DNP flag on exactly the parts the plan
marks DNP. No Konnect call sets that flag, so right after drawing it is the
one difference left, by name, until it is ticked in eeschema. It prints what is planned and not drawn (make it with the
same call) and what is drawn and not planned (delete it), and exits 1. A
Konnect call that fails half-way, a list split with an element dropped, a
field the batch edit silently skipped: this is where they show, one sheet at
a time, instead of as a parity puzzle at the end. After a hand fix made on
purpose (a jog moved to clear a text) it reports that spot as a difference;
`parity` is then the word that counts.

## The frames (`frame`)

Konnect has no call for a graphic rectangle, a text box or the title block.
After Konnect has drawn a sheet's calls, `frame` writes each planned section
as a KiCad text box — one object: the frame, with its title in the top-left
corner — and sets the title block (Title, Date, Rev) on every sheet. It is
the one exception to "nothing here writes a board's file", and a narrow one:
it refuses unless every planned part already sits where the plan put it, it
touches no electrical item, it exports the netlist before and after and
fails if they differ, and `--remove` takes its frames out again. Every text
box on a planned sheet is a section frame (`check` reads it so), so `frame`
replaces all of them, not only the ones `plan.json` recorded: a re-plan
starts `plan.json` afresh, and the frames the earlier plan wrote would
otherwise stay beside the new ones. Run it with KiCad's sheets saved and closed, or
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

  A small part goes to the anchor with the most pins on its nets, and a tie
  goes to the first anchor by refdes, so on a sheet where two connectors share
  a rail or a bus the decoupling the handoff puts "at J4" can land beside J1.
  `"home": "J4"` on that part's entry (the handoff's own words decide it)
  puts its block in J4's row; `plan` stops if J4 has no pin on the
  part's nets. `"group": "USB-C"` titles the frame a part stands in — on an
  anchor its frame (two anchors naming one group share it; a multi-unit
  part may give `{"1": "…", "2": "…"}`), on a small part the frame it
  joins, wherever its nets would have put it (standoffs with the module
  connector; a local header with its circuit). `"caption": "CC pull-downs
  (Rd)"` is drawn above the part's block, saying what it is for; every part
  in a block names the same caption, or none does (`plan` stops otherwise;
  the old key `section` is still read as `caption`). `fields` adds MPN and
  the like. `dnp: true` marks it DNP: the preview carries KiCad's flag, and
  `drawn`, `parity` and `parity --handoff` (a part whose handoff line says
  DNP) each fail until the drawn part carries it too. A *field* named DNP is
  not the flag — the BOM shows both the same way — so the plan writes none.

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
  and Value just above the origin. `symfields.py` moves them there (the parsed
  tree, proven by a kicad-cli resave that differs in those two fields only,
  backup in `<board>/.sch_backups/`); `--derive LIB:NAME` copies a stock symbol
  into the board's library with its fields beside the body -- for Device:R,
  whose Value sits inside the body, so a Value longer than the body ("33 1%
  (PROPOSED)") runs over the resistor's own wires in any orientation.
- **Deleting or replacing a library symbol once KiCad has saved it.**
  `delete_symbol` finds only symbols in Konnect's own two-space layout;
  kicad-cli and KiCad's Symbol Editor save with tabs, so after one
  `symfields.py` run every symbol in the library answers "not found". And
  `create_symbol` on a name already there appends a second copy instead of
  failing. A rebuild (a longer pin, a split unit) is therefore
  `symfields.py --delete SYMBOL ...` (parsed tree, kicad-cli resave proven to
  differ only by those symbols, backup), then `create_symbol`, then
  `symfields.py SYMBOL` for the fields -- and a pin-by-pin comparison with the
  backup (number, name, type, unit, side) before any sheet uses it. Prove it
  first: `delete_symbol` on a scratch copy of the library says "not found".
- **Library symbols a sheet already embeds.** Placing a part copies its
  definition into the sheet only if the sheet has none: a symbol rebuilt in the
  library keeps its old pins on every sheet that used it. After the parts of a
  redraw are placed and before any wire, `update_symbols_from_library`
  (`allow_pin_moves`) and `reset_schematic_field_positions` on that sheet.
- **Hierarchy instance paths.** A part placed on a sub-sheet gets an instance
  path of that sheet alone; the root's `edit_sheet` calls, made last, re-link
  them (check `kicad-cli sch export netlist` for annotation errors).
- **Sheet-pin sides.** `add_sheet_pin` writes every pin facing right, and KiCad
  then puts a left-edge pin on the right edge, shorting it to the right-edge pin
  at its height (`parity` shows two interfaces merged). The root's calls add the
  right-edge pins, then `import_sheet_pins` (side left) and `edit_sheet_pin` to
  each left pin's spot.
- **New fields.** `batch_edit_schematic_components` only edits a field the
  symbol already has ("Field 'MPN' not found"); a new one (MPN, DNP) goes in
  with `add_component_annotation`, hidden.
- **A redraw changes every symbol's UUID** (parts are deleted and placed again:
  Konnect moves a multi-unit part only as a whole). On a board whose PCB
  already holds the parts, the PCB must be updated from the schematic with
  *Re-link footprints to schematic symbols based on their reference
  designators* — in KiCad, by the user — before any further PCB work.
- **The DNP flag.** No call sets KiCad's own `(dnp yes)`; it is ticked by
  hand in eeschema (Symbol Properties → *Do not populate*) after the last
  redraw — a redraw places the parts again without it. `drawn`, `parity` and
  `parity --handoff` name every part still missing it, so a sheet with the
  flag unset never reaches review looking clean.

## Limits

A plan is one feasible readable layout, not the only one: blocks are placed
by rule, not by taste. When `check` still reports something after drawing,
the fix is a Konnect edit (move, rotate) and `check` again — never a waiver
for a collision. The rules' numbers are constants at the top of `check.py`.
