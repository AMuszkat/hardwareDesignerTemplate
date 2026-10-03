---
name: footprint
description: >
  Get a real, non-generic footprint (and its 3D model) for a part and put it in
  this board's library with provenance. Triggers on "footprint for <MPN>",
  "download a footprint", "3D model", "land pattern", "STEP model",
  "SnapEDA/Ultra Librarian/SamacSys/LCSC part", "U1 has no footprint",
  "assign footprints", "check the footprints".
---

# Footprints for real parts

⚠ **First, the parts this does NOT apply to: resistors, capacitors and
ferrite beads in standard chip packages** — 0201, 0402, 0603, 0805, 1206,
1210, 1812, 2010, 2512. Those use KiCad's stock footprints and nothing else:
`Resistor_SMD:R_0603_1608Metric`, `Capacitor_SMD:C_0805_2012Metric`,
`Inductor_SMD:L_…` (chip ferrites and 0-ohm links live there too), or the
`_HandSolder` variants where hand assembly matters. Do not fetch one, do not
verify one, do not ask concept for its datasheet — those datasheets are
deliberately absent from `handoff/datasheets/`, and the absence is not a
hole. `fp.py get` refuses one (it measures the land against KiCad's own) and
`fp.py check` reports one that got in anyway.

**Everything else is normal work, two pads or not.** A diode is fetched like
anything else (if its land happens to measure like a chip package, `get` and
`derive` say so and take `--anyway`; `check` then notes it instead of failing) — SOD-323 sits within 0.1 mm of an 0805's span and has nothing
to do with an 0805. Where a stock KiCad footprint genuinely matches the
datasheet's land (SOIC-8, SOT-23), use it and move on; where the part draws
its own, this workflow is how it gets in: oscillators, connectors, arrays,
BGAs, modules, diodes with a vendor land.

The tool is `tools/fp/fp.py` (read `tools/fp/README.md` once; `tools/setup.sh`
once per machine for its venv). It is plain `python3`, not `tools/kpy`.
Everything below is about the judgement calls it deliberately does **not**
make.

## Before anything: is the datasheet here?

`handoff/13_datasheets.md` lists every vendor document filed in
`handoff/datasheets/`. If the part's datasheet is not there, that is a
**QUESTIONS.md line** ("please file the datasheet for X"), not a reason to
download one yourself — a PDF you fetch has no hash concept can move, so
`fp.py check` can never tell you it was revised. Do the rest of the board
while you wait; do not guess a land pattern.

## 1. Get it

```
python3 tools/fp/fp.py get <MPN> --lcsc C123456     # first choice
python3 tools/fp/fp.py get <MPN> --url <URL>        # manufacturer's own
python3 tools/fp/fp.py get <MPN> --file <PATH> --origin "<where from>"
```

**The LCSC id** is the one lookup the script cannot do. Get it from
Konnect: `load_toolset('integration')`, then `search_jlcpcb_parts(<MPN>)`.
No result is not a dead end — it means this part is not an LCSC part; go on
to `--url`.

**For `--url`, prefer the manufacturer's own CAD download** over a portal's
redraw, and prefer a portal that publishes a real `.kicad_mod` over one that
exports through an intermediate format. If a page needs a login or a click
(SnapEDA, Ultra Librarian, SamacSys, TraceParts): use the Chrome MCP — call
`list_connected_browsers` first and `select_browser` the one with
`isLocal: true`, or the file lands on a different machine — save the file,
then `--file` it with `--origin` saying where it came from. (A browser
download is the one file you may read from outside `kicad_project/`.)

## 2. Verify — this is the part that matters

```
python3 tools/fp/fp.py verify <MPN> --pins <count from the datasheet>
```

It prints the geometry the file actually has and renders an SVG. Then **you**
open `handoff/datasheets/<MPN>.pdf` with the Read tool's `pages` argument and
compare, number by number:

- pad count and **numbering** (pin 1 marking, and which physical lead each
  number is — an array or a dual part is where this goes wrong),
- pad size, pitch, row/column gap, overall span,
- **the recommended-land table, never the body/package drawing.** They are
  different numbers; the body drawing is the one that fits and does not
  solder,
- anything the note says about SMD vs NSMD mask, paste reduction, or keepout
  under the body.

A `WARN` is information; a `FAIL` blocks signing — including the `--pins`
count you gave `verify`, which `sign` remembers. "Pads stick out of the
courtyard" is common in converted footprints and is a real placement bug —
fix the courtyard rather than override it.

## 3. Sign, then install

```
python3 tools/fp/fp.py sign <MPN> --datasheet <MPN> --pages 5 \
    --note "pads 0.64 x 0.70, inner gap 0.70, span 2.10 — Fig 3"
python3 tools/fp/fp.py install <MPN>
```

The note must carry **the datasheet's own numbers**, not "checked" or "looks
right". It is what the next session re-reads when the vendor revises the PDF
and `check` flags it.

## 3b. Silk and courtyard: the house convention

The default below is the one these tools were built around; `handoff/10_layout.md`
or `14_fab.md` may set a different one, and then that wins.

**No body outlines on silkscreen.** The body is on F.Fab; silk carries a
pin-1 mark (KiCad's triangle or corner square, or a dot) and a diode's
cathode bar, nothing more. Courtyards are IPC nominal: 0.25 mm beyond pads
or body, whichever is larger. Every footprint entering the library gets
this before it is called done, and the land is never touched by it:

```
python3 tools/fp/fp.py tidy <name> --dry-run           # numbers the silk items
python3 tools/fp/fp.py tidy <name> --silk-keep 0 --courtyard 0.25 --note "…"
```

**A KiCad stock footprint is fine for the land and wrong for the silk** —
SOT-23-5, SOIC-8, TSSOP, WSON, SOD-323 all draw the body on silk. Do not
copy the file by hand and do not draw the pads again; `derive` copies it
into the library with the silk and courtyard fixed and a `stock` record that
`check` re-verifies against KiCad's file forever (land byte-identical, no
datasheet needed because the land is KiCad's):

```
python3 tools/fp/fp.py derive Package_SO:SOIC-8_3.9x4.9mm_P1.27mm \
    --courtyard 0.25 --used-by "U2 <MPN>, U3 <MPN>"
```

Then point the symbol's Footprint field at `<stem>:<name>` (the board's library,
e.g. `board1:<name>`) and Update PCB from Schematic with "replace footprints"
ticked. Chip R/C/L stay KiCad's
stock footprints, dashes and all: `derive` refuses them like `get` does.

## 4. When nothing can be downloaded

`get` exits 2 and prints the fork. **Ask the user which way to go** — use
AskUserQuestion, do not pick:

- **Author it here from the datasheet**, with Konnect's library toolset:
  `load_toolset('library')`, then `create_footprint`, `edit_footprint_pad`,
  `set_footprint_graphics`, `set_footprint_models`. Reconcile three counts
  before you finish: datasheet leads, symbol pins, footprint pads.
- **The user draws it** in the footprint editor, or pulls it from a portal
  behind their login and hands you the file.

Either way, file it so the audit sees it like anything else:

```
python3 tools/fp/fp.py adopt <FootprintName> --mpn <MPN> --source authored \
    --datasheet <MPN> --pages 4 --note "..."
```

## Rules

1. **A chip resistor, capacitor or ferrite bead never enters
   `lib/*.pretty` at all** — fetched, drawn, or adopted. It comes from
   KiCad's stock library.
2. **Never drop a `.kicad_mod` into `lib/*.pretty` by hand without
   `adopt`.** An unrecorded footprint is one nobody can trace, and `check`
   will keep failing until it is filed.
3. **`install` (a vendor footprint) and `derive` (a KiCad stock one, land
   unchanged) are the only ways in.** If one refuses, the reason is the point.
4. **Run `python3 tools/fp/fp.py check` before calling footprint work
   done**, and again before layout. It exits 1 with a reason.
5. A footprint for a line marked **PLACEHOLDER** in `01_components.md` is
   still forbidden — no part, no footprint, no exceptions.
6. The 3D model is not decoration: it is what catches a connector fouling a
   heatsink at review. Fetch it when it exists (`--lcsc` gives STEP + WRL
   free), and say so in QUESTIONS.md when it does not.
