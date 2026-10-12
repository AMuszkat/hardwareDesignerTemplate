# `tools/board_setup` — Board Setup from `handoff/14_fab.md`

**Yours**, like `tools/fp`. It types the fab's rule page into KiCad so nobody
has to, and checks that the board still matches after every republish.

    tools/kpy tools/board_setup/board_setup.py plan  --board board1   # read-only
    tools/kpy tools/board_setup/board_setup.py apply --board board1   # KiCad closed
    tools/kpy tools/board_setup/board_setup.py check --board board1   # exit 1 on a difference

`tools/kpy` runs it under the interpreter KiCad's `pcbnew` module is built
for. A conda or venv python cannot import it.

## What it reads

`14_fab.md` itself, every run. There is no transcribed copy to go stale. The
parser is strict: a table whose columns changed, a Constraints row it does not
know, a class whose via cell it cannot read, or a net name that does not match
exactly **one** board net all stop it with the reason. Teach it the new row;
do not make it skip one.

A cell reading **UNSET** is a value the concept session has not decided.
`plan` lists them, `check` reports each as a QUESTION, and `apply` refuses the
whole page — an unfilled field must stop Board Setup, not become a silent
KiCad default. A cell marked **UNVERIFIED** is applied as written and printed
as a note: confirm it with the fab before ordering.

## What `apply` writes, and how

The board is loaded, modified and saved by **`pcbnew` itself**
(`LoadBoard` / `SaveBoard`), the same serializer the GUI uses. No text edit
touches `.kicad_pcb` or `.kicad_pro`.

| 14_fab.md | KiCad | where it lands |
|---|---|---|
| order form: Layers | copper layer count | `.kicad_pcb` |
| order form: Via process (tenting / untented) | tent vias front + back | `.kicad_pcb` |
| Constraints: clearance, track, annular, via, copper-to-hole, copper-to-edge, through hole, hole-to-hole | Board Setup → Constraints | `.kicad_pro` |
| Constraints: mask expansion, mask minimum web | Board Setup → Solder Mask/Paste | `.kicad_pcb` |
| Constraints: allow bridged solder mask apertures between pads within footprints (yes / no; optional row) | Board Setup → Solder Mask/Paste, board-wide | `.kicad_pcb` |
| Constraints: silk height and stroke | minimum text height / thickness | `.kicad_pro` |
| Constraints: "keep silk ≥ X mm off every pad" | silk clearance **X − mask expansion** | `.kicad_pro` |
| Constraints: hole to board edge | custom rule `physical_hole_clearance` against `Edge.Cuts` | `.kicad_dru` |
| Net classes: values | net classes (Default edited, the rest created) | `.kicad_pro` |
| Net classes: nets | one exact-name pattern per net | `.kicad_pro` |
| Net classes: "inside \<REF\>'s courtyard" | class with no nets + custom clearance rule inside that courtyard | `.kicad_pro`, `.kicad_dru` |
| Net classes: via "none" | custom rule `disallow via` for the class | `.kicad_dru` |
| every class's track and via | Track / Via dropdowns | `.kicad_pro` |
| Physical Stackup + order form (mask, silk colour, finish) | physical stackup, board thickness | `.kicad_pcb` (see below) |

Three are derived, and `plan` prints why:

- **Silk clearance.** KiCad measures silk against the *mask opening*, which is
  the pad plus the mask expansion, so "0.2 mm off the pad" with 0.05 expansion
  is 0.15 to the opening.
- **An area class.** A net class cannot say "inside U1", so the class is
  created without nets (its values are there for reference and for the
  dropdowns) and its clearance is a custom rule where *both* items touch that
  footprint's courtyard. Custom rules outrank net classes, so a wider class
  still drops to the area's value inside it.
- **Silk line width** has no DRC in KiCad. Nothing checks it.

Custom rules live between `# >>> board_setup` and `# <<< board_setup` in
`<board>.kicad_dru`, copied **verbatim** from the page's fenced blocks; the
rules the rest of the page implies are re-derived only to catch a page that
dropped one. Anything outside the block is left alone, and rules after it win
over the generated ones.

Each apply first copies the three files to
`<board>/.board_setup_backups/<timestamp>/`, and refuses if a KiCad lock file
is present (`~<board>.kicad_pcb.lck`, `~<board>.kicad_pro.lck`). Close the
**project**, not just the PCB editor: KiCad rewrites `.kicad_pro` when it
closes, which would undo the net classes. `--ignore-lock` exists for a lock
left behind by a crash, and for nothing else.

## The stackup: written by `apply`, proved by a diff

KiCad 10 has no direct way to write a physical stackup from outside the GUI:
the SWIG bindings do not wrap `BOARD_STACKUP`, and the IPC API answers
`UpdateBoardStackup` with *no handler available*. SWIG does expose
`BOARD_DESIGN_SETTINGS.CloneFrom`, and `apply` runs a `stackup` step (in its
own process) that goes through it:

1. copy the board to a temporary folder and put the `(stackup …)` block from
   the page's *Physical Stackup* and order-form rows into **that copy's**
   `(setup)`;
2. `pcbnew.LoadBoard` the copy. KiCad's own parser rejects a malformed block
   before anything real is touched;
3. `CloneFrom` the copy's design settings onto the real board (identical
   apart from the stackup, because the copy *was* the real board) and
   `pcbnew.SaveBoard` it;
4. **prove it**: the saved board with its stackup block and thickness line
   removed must equal the board before, and the `.kicad_pro` must parse to the
   same JSON. Otherwise both files are restored and it stops.

The real board is only ever written by KiCad. Masks get KiCad's default 0.01
mm; the loss tangent is KiCad's default (the page gives none). The board thickness is
set to the sum, and `check` reads the stackup back from the saved file and
compares each thickness, type, εr, material, colour and the finish against
the page's total and tolerance.

If a KiCad release implements `UpdateBoardStackup`, that is the simpler route
and this step should move to it.

## What `check` reports, and who fixes it

| tag | meaning | who |
|---|---|---|
| `apply` | the board differs from the page in something this script writes | run `apply` |
| `QUESTION` | the page is wrong or incomplete: UNSET (in ANY cell of any table), a net name that matches 2 board nets, a stackup that does not add up | `QUESTIONS.md`, quoting the MANIFEST hashes |
| `board` | your own drawing work: no outline, an outline of another size, the footprint an area class names not yet on the board, a class net not on the board yet, copper on a layer the page no longer has | you |

`check` must exit 0 before routing and before fabrication output.

## Not its job

- **Router edge clearance.** The Specctra export drops it; give an autorouter
  *Copper to edge clearance* yourself (Freerouting: `router.copperToEdgeClearanceUm`
  = value × 1000).
- **Footprint lands.** That is `tools/fp`.
- **Choosing numbers.** A rule that blocks the layout is a `QUESTIONS.md`
  item. Neither the page nor the output of `apply` gets edited to make DRC
  pass.
