# `tools/fp` — vendor footprints, verified, into this board's library

**Yours.** The concept session does not touch it: footprints derive from
datasheets, the datasheets are published in `<board>/handoff/datasheets/`, so
footprint work finishes on this side of the wall. Fix bugs in it yourself.

    tools/setup.sh                               # once: tools/.venv + easyeda2kicad
    python3 tools/fp/fp.py get <MPN> --lcsc C…   # or --url / --file
    python3 tools/fp/fp.py verify <MPN> --pins N # geometry + SVG
    python3 tools/fp/fp.py sign <MPN> --datasheet <MPN> --pages 5 --note "…"
    python3 tools/fp/fp.py install <MPN>
    python3 tools/fp/fp.py check                 # audit; exit 1 on any problem

Run it from `kicad_project/`. It works on the board this session owns
(`bin/board boardN` sets `HW_BOARD`); `--board boardN`, before or after the
subcommand, names another one — and is refused if that board belongs to
another session.
It is plain `python3` — it does not import `pcbnew` — so `tools/kpy` is not needed.

## Not for chip R, C and ferrite beads

0201 · 0402 · 0603 · 0805 · 1206 · 1210 · 1812 · 2010 · 2512 — resistors,
capacitors, ferrite beads, chip inductors, 0-ohm links: **KiCad's stock
`Resistor_SMD` / `Capacitor_SMD` / `Inductor_SMD` libraries, always.** The
land is IPC's, KiCad already draws it, and concept does not publish
datasheets for them. `get` refuses one — it measures the land against
KiCad's own footprints, so "standard" means whatever the installed KiCad
says — and `check` reports one that got into the library anyway.

Everything else with two pads is ordinary work: a diode's land is its own
(SOD-323 is within 0.1 mm of an 0805's span), so fetch it, verify it, sign
it. `--anyway` overrides the refusal when the measurement is a false match.

## The chain

| step | source | notes |
|---|---|---|
| 1 | already signed | `get` stops and says so |
| 2 | `--lcsc C…` | LCSC/EasyEDA via `easyeda2kicad`: symbol + footprint + **STEP and WRL**. No login. Widest coverage of anything scriptable |
| 3 | `--url` | a `.kicad_mod`, or a `.zip` holding one, that you found (a lone `.step` is not a footprint) — the **manufacturer's own** beats any portal's redraw. urllib first, then the local headless Chrome, because a host can refuse a script and answer a browser |
| 4 | `--file` | something you saved yourself: a login-walled portal (SnapEDA, Ultra Librarian, SamacSys) opened with the Chrome MCP, or a file the user hands you. `--origin` records where it came from |
| 5 | nothing | `get` prints the fork and exits 2 — **ask the user**: author it here with Konnect's `library` toolset, or they draw it. `adopt` files the result with the same record |

`--lcsc` is the one lookup this cannot do for you: LCSC and EasyEDA both
answer a script with 403 and headless Chrome with an empty page. Ask
Konnect's integration toolset (`search_jlcpcb_parts`) for the C-number.

## The gate

A downloaded footprint is a stranger's arithmetic, and a wrong land pattern
passes ERC, passes DRC, and is found at assembly. So `install` refuses
anything that is not **signed**, and a signature is not a checkbox: it
records the datasheet, the page, the dimensions you matched *in the
datasheet's own numbers*, and that PDF's sha256.

`check` then re-reads all of it and reports:

- a footprint **edited after signing** (its hash moved),
- a **datasheet revised** under a signed footprint (the PDF's hash moved —
  nothing about your drawing changes when a vendor moves a pad, which is
  exactly why no one catches this by eye),
- a footprint in the library with **no record at all**,
- a footprint **derived from KiCad's library** whose land drifted from
  KiCad's, or whose KiCad original changed underneath it (a KiCad update),
- a missing 3D model, and a project library missing from `fp-lib-table`.

Run it before you call layout ready. It is cheap and it exits 1.

## Silk and courtyard are yours: `tidy` and `derive`

The default convention of these tools is **no body outlines on silkscreen**
(`handoff/10_layout.md` or `14_fab.md` may set another, and then that wins): the body lives on
F.Fab, and silk carries a pin-1 mark (and a diode's cathode bar), nothing
else. Courtyards are IPC nominal, 0.25 mm beyond pads or body, whichever is
larger. Two commands enforce that without touching the land, and prove it:
the `(pad …)` nodes are hashed before and after, both sides run through
`kicad-cli fp upgrade` first so an old-format file is compared in one
dialect, and nothing is written if the hashes differ.

    python3 tools/fp/fp.py tidy <name> --dry-run           # numbers the F.SilkS items
    python3 tools/fp/fp.py tidy <name> [--silk-keep 0,1] [--silk-drop 10] \
        [--silk-dot X Y] [--fab-from-silk] [--courtyard 0.25] --note "…"

**`--courtyard-contour`** (with `--courtyard`) makes the courtyard follow the
part instead of boxing it: the pad field plus the margin, each closed F.Fab
body (rect or polygon — F.Fab *lines* are markers, not body) plus the margin,
and where the two do not touch, the band between them over their common extent
(an angled header's bent legs run there). One rectangle round all of it — the
default, and right for most parts — claims an angled box header's full body
length over a pin field that is bare legs; an angled header typically hands
back tens of mm² beside its pins this way. `place.py`
and `pack.py` judge a rectilinear polygon courtyard by its own strips, not its
box, so the contour counts in placement checks too; after the tidy,
`tools/place/refresh.py` puts it on the closed board and `place.py sync` proves
it.

What stays on silk by itself: every polygon (KiCad's pin-1 triangles and
corner squares) and any circle up to 0.25 mm radius. Every line, rect and
arc goes unless you `--silk-keep` it — a diode's cathode bar and a header's
pin-1 corner are the usual keeps; `--silk-drop` removes a converter's junk
dot that passes as a mark. A converted footprint with **no F.Fab at all**
gets its dropped outline re-drawn there with `--fab-from-silk`, so the
assembly drawing keeps the body. `tidy` carries the existing signature over
(the land it vouches for is unchanged) and appends a `tidy` entry to the
record.

Two more non-land edits live in `tidy`:

- `--allow-mask-bridges` sets KiCad's per-footprint *allow bridged solder
  mask apertures*. It is for a pitch the fab's minimum mask web cannot fit
  between (a 0.5 mm-pitch TSSOP, WSON or DSBGA against a typical 0.1–0.2 mm
  web in `14_fab.md`: gang that opening). It clears the in-footprint
  `solder_mask_bridge` DRC hits without lowering the web.
- `--drop-property NAME` removes a footprint field. easyeda2kicad's
  `LCSC Part`, for example, blocks Konnect's library refresh.

A change to the **land itself** (a BGA moved from SMD to NSMD pads, say) is
not a tidy. Edit the pads with KiCad's own API (`pcbnew.FootprintLoad`, `SetSize`,
`FootprintSave`), confirm that positions and graphics are unchanged, and
**re-sign** it against the datasheet page like any new land.

`derive` does the same to one of **KiCad's own** footprints and installs
the result as `<stem>:<name>`:

    python3 tools/fp/fp.py derive Diode_SMD:D_SOD-323 --silk-keep 0 \
        --courtyard 0.25 --used-by "D1, D2" --note "cathode bar kept"

Its record says `source: stock` and names no datasheet: the land is
KiCad's, and the promise is that it stays **byte-identical to KiCad's**.
`check` re-hashes the stock file and the copy on every run, so a KiCad
update that moves a stock pad fails the audit the way a revised datasheet
does, and a copy whose land drifted fails too. Chip R/C/L are refused here
as everywhere else. The stock 3D model stays referenced through
`${KICAD10_3DMODEL_DIR}`; where KiCad ships none (some WSON and X2SON
variants) `check` says so as a note, and the vendor's STEP can be added later with
Konnect's `set_footprint_models` followed by `tidy` (to re-hash).

After either command the board still holds its old copy. Same library id →
Konnect's `update_footprints_from_library` (dry run, then apply; it reads
the `.kicad_mod` files from disk). It refuses a library footprint that
carries a field it cannot copy losslessly, such as easyeda2kicad's
`LCSC Part`; `tidy --drop-property "LCSC Part"` removes it (the C-number
stays in the descr and in `footprints.json`). New library id, i.e. a
derived stock footprint → change the symbol's Footprint field, then **Update
PCB from Schematic** with *Replace footprints with those specified in the
schematic* ticked.

⚠ **Do not use KiCad's own Tools → Update Footprints from Library after
editing library files while KiCad is open.** KiCad keeps the library in
memory from when it first loaded it, and the GUI command writes that stale
copy back — silently restoring old silk on footprints Konnect had already
refreshed. Close and reopen the project first, or use
Konnect's refresh. A brand-new footprint name is not affected (F8 loads it
fresh). Verify by comparing geometry, not by counting items: a body rect
and a pin-1 dot are both "1 silk item".

## Files

    <board>/lib/<stem>.pretty/       the library (installed footprints)
    <board>/lib/<stem>.3dshapes/     STEP + WRL beside them
    <board>/lib/footprints.json      the provenance record — one entry per footprint
    <board>/lib/.fp/<MPN>/           staging: raw download, staged copy, SVG
    tools/.venv/                     easyeda2kicad + PyMuPDF (shared by the board tools; disposable —
                                     delete it and re-run tools/setup.sh)

Provenance also goes **inside** the installed `.kicad_mod`, in its `descr`:
MPN, source, datasheet and page. `footprints.json` can be lost; a footprint
that travels into another project cannot say where it came from otherwise.
