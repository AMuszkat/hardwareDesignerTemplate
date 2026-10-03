# `tools/handoff` — the concept → board channel

Each board session builds from ONE folder, `kicad_project/<board>/handoff/`,
and that folder is generated here. A hand copy diverges silently; a generated
one carries a revision and the hash of every page, so a stale brief is caught
in conversation rather than in copper.

    python tools/handoff/publish.py --all              # every board
    python tools/handoff/publish.py --board board1     # one board
    python tools/handoff/publish.py --check --all      # exit 1 if any board is stale
    python tools/handoff/new_board.py 2 power "Power stage" --fab example_standard

## What lives where

| path | holds |
|---|---|
| `publish.py` | the engine: render, hash, changelog + amend rule, datasheet sync, lint. Board-blind. A publish builds the whole folder and swaps it in; `--check` re-renders and compares every page with the disk |
| `boards/boardN.py` | one board's DATA: nets, connectors, layout, deliberate, open, release note, fab choices. **This directory is the board registry** |
| `board_template.py` | every attribute a board module may set, documented, with the handoff file it feeds |
| `new_board.py` | creates a board on both sides of the wall and publishes rev 1 |
| `fabs/<profile>.py` | what a fab can build, each value with its source page. `example_standard.py` is an illustrative example (no fab's real numbers), `_blank.py` the shape to fill |
| `answers/boardN.md` | concept's answers to that board's `QUESTIONS.md` — the only place they are written |
| `studies/components_boardN.py` | the value bus: `(refdes, function, value, status, note)`, status `set` / `proposed` / `tbd` |

The handoff itself: `00_READ_FIRST`, `01_components`, `02_nets`, `03_pinout.csv`
and `08_pin_accounting` (only with a `PINOUT`), `04_deliberate`, `05_open`,
`06_block_diagram.svg` and `11_floorplan.svg` (when the board names them),
`07_answers`, `09_connectors`, `10_layout`, `12_boot` (with `BOOT`),
`13_datasheets` + `datasheets/`, `14_fab` (with `FAB`), `CHANGELOG`, `MANIFEST.json`.
Numbering is stable; a missing section leaves a gap, and a file that stops
being published is deleted, not left to read as current.

## Adding a board

`new_board.py N slug "Title" [--fab profile]`. It refuses to overwrite
anything, copies `kicad_project/tools/board_scaffold/` into the new board
folder (the one time concept writes there outside `handoff/`), and publishes.
Then fill the value bus and the board module and republish. The board session
creates its KiCad project itself, through Konnect.

## Publishing discipline

- **Set or clear `RELEASE_NOTE` on every publish.** It is the *What actually
  changed* section of the CHANGELOG — which of the board session's drawn work
  is now wrong. An unchanged note is warned about and not repeated; a missing
  one leaves the board session a bare list of moved files.
- **The amend rule.** A republish within 20 minutes of a revision's FIRST
  publish that changes no NEW page amends that revision instead of appending a
  near-identical one — so the amended note must describe EVERY change since
  the previous revision. The window does not slide: after 20 minutes the next
  publish is a new revision.
- **The CHANGELOG names pages, not sources.** The board session can open
  `14_fab.md`; it cannot open `fabs/<profile>.py`.
- **Fresh means identical.** `--check` renders the handoff again and compares
  it with the folder, so a moved source, a hand-edited page and a deleted file
  all read STALE — and a source edit that changes no page does not.
- **A durable fact goes into the generated tables** — the value bus, `NETS`,
  `LAYOUT`, `PIN_TREATMENT` — not only into `answers/boardN.md`. An answer
  is read once; a table is read every session.
- **Placeholders get no part at all.** A `tbd` line renders as PLACEHOLDER and
  the board session places nothing for it. A plausible generic part is worse
  than a hole, because it stops looking like an open question.
- **Classify pins by PIN NAME, never by the Function column.** The Function
  column is the vendor's description; two pins with the same description can
  need different answers. `SPARE_FUNCTION` exists for vendors whose names say
  nothing, and is the weaker option.
- **Their questions measure the handoff.** Every question is a located defect
  in how the handoff *specifies*. The fix goes into the board module, the bus
  or this engine — then republish, then answer. Never quote pad dimensions into
  an answer: publish the datasheet, and the numbers are theirs to read.
- **Run `--check` before answering any question.** An answer written against a
  stale handoff answers a board that no longer exists.

## Datasheets cross the wall, passives do not

`publish.py` copies every datasheet the handoff NAMES (an MPN that appears
anywhere in the generated text and is in `reference/vendor/datasheets.json`),
plus the board's `DATASHEETS_EXTRA`, into `handoff/datasheets/` under the filed
PDF's own name, and hashes each into the MANIFEST. The board session's footprint tool verifies lands
against those files and is told through the CHANGELOG when one is revised.
Chip resistors, capacitors and ferrite beads in 0201–2512 are excluded on
purpose: their lands are IPC's and KiCad's stock library already draws them.
Inductors are not excluded: a power inductor in a "1210" body has its own land.

## The fab page

`14_fab.md` is rendered from the board's `FAB` block over its profile: order
form, Constraints, Physical Stackup, Net classes, Custom DRC rules (derived
ones for hole-to-edge, area classes and no-via classes, then the profile's and
the board's own — a board rule with a derived rule's name replaces it), the
arithmetic, and the sources. `kicad_project/tools/board_setup/board_setup.py`
parses exactly that shape, so a new row name is a change on both sides. A
value nobody has filled renders as **UNSET**, and Board Setup refuses the page
until it is filled.
