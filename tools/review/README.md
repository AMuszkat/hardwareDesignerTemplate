# `tools/review` — a board change, frozen and gated for its reviewer

**Concept's.** Every schematic change and every PCB change a board session
finishes is reviewed by the concept session's reviewer agents
(`.claude/agents/schematic-reviewer.md`, `pcb-reviewer.md`) before the board
builds on it. The `review-board` skill is the procedure; the board's side is
its `REVIEW.md` and `handoff/15_reviews.md`
(`kicad_project/RELAY_PROTOCOL.md`, "Reviews").

    python3 tools/review/snapshot.py --board board1 --kind schematic --id RV3
    python3 tools/review/snapshot.py --board board1 --kind pcb       --id RV4 [--scope R3]

**Measurements** (`measure.py`, a PCB snapshot's `measurements.json`): the
frozen board measured with pcbnew, facts only — parts, `placement.json`
groups with each member's gap to its nearest member, per-net lengths, widths
and vias, pairs by name (`_P`/`_N`, `+`/`-`, `P`/`N`: lengths, skew,
edge-to-edge gap, coupled length), pours and their islands, silk over mask
openings, and per `10_layout.md` row the parts it names, the points it gives
(nearest part, as board and as outline-relative coordinates), its stated
distances and its parts' pairwise centre distance and pad gap. The
pcb-reviewer reads it instead of measuring.

**Scope and changes** (`changes.py`). `--scope` names what a PCB review is
about — a routing group (the copper its `routing/<G>.applied.json` names, by
UUID), a `placement.json` group, `refs:A,B`, or `box:X0,Y0,X1,Y1` — and the
PCB views are tiled only inside it plus 3 mm, the sheet tiles skipped;
`all` / `fab` (the default) tiles everything. Every snapshot rasterises each
page and view at 72 dpi into `compare/` and diffs it, in 2.8 mm cells,
against the board's previous snapshot of the same kind: `changed.md` lists
the changed regions and the tiles that show them, and flags a region outside
every tile in scope. An unchanged board lists none.

## What it does

1. **Freezes the board**: copies `kicad_project/<board>/` to
   `out/reviews/<board>/<id>-<kind>/snap/<board>/` (generated plans' previews,
   renders, backups and lock files left out), links `snap/tools` to
   `kicad_project/tools` so `${KIPRJMOD}/../tools` resolves, and records the
   sha256 of every copied file in `manifest.json`. The review is of that copy:
   the board session may go on with independent work, and every finding names
   the files it came from.
2. **Runs the board session's own gates on the copy**, exactly as its skills
   run them — schematic: ERC, `sch.py check`, `sch.py parity` (when a plan
   exists), `sch.py parity --handoff`; PCB: those plus `place.py drc` (DRC
   with schematic parity), `board_setup.py check`, `fp.py check`,
   `route.py status` / `edge`. Each gate's whole output is `gates/<name>.txt`.
3. **Exports what a reviewer reads, as files it reads without a shell**:
   `netlist.xml`, `bom.csv` (Value, Footprint, MPN, DNP, excluded, quantity;
   a missing column fails the row), `schematic.pdf`; `parts.csv` (every
   placed part: KiCad's DNP flag and a DNP field apart, the exclude flags),
   `pins.csv` (every pin: name, type, no-connect flag, net) and `nets.csv`; every sheet whole
   (`sheets/sheet-N.png`) and in overlapping 300 dpi tiles
   (`sheets/tiles/`, indexed by `tiles.md` — which parts each tile shows —
   and `words.csv` — where every text is); for a PCB, `pcb/top.png`,
   `bottom.png`, `inner.png` and the same views in 600 dpi tiles
   (`pcb/tiles/`, `tiles.md`, `words.csv`).
4. Makes `tmpWork/`, the reviewer's only scratch folder: temporary, anything
   in it may be deleted, all of it is when the snapshot is retaken.
5. Writes `SUMMARY.md`. Exit 0: every gate passed; 1: a gate failed (the
   review starts from it); 2: nothing to review (no KiCad project, no PCB, no
   `kicad-cli`).

It never writes a board folder. `out/` is disposable; the reviewer's report,
the record, goes to `history/reviews/`. Tiles and the word index need
poppler-utils (`pdftoppm`, `pdftotext`, `pdfinfo`); without them the row says
"not run".

## Traps

- **Datasheets and 3D models are hard-linked, not copied** (a handoff is
  100+ MB of PDFs). That freezes them only because `publish.py` and the
  footprint tools replace such files rather than rewriting them; the KiCad
  files are copied, since an editor may rewrite them in place.
- **A gate that compares a path breaks on a copy.** `board_setup.py check`
  once called a copy's design rules stale because the generated block names
  `14_fab.md` by its repo path; it now compares the page by its sha256. A
  gate that FAILS on the snapshot and passes on the live board is a tool bug
  — prove it with the same command on the board (all gates are read-only),
  then fix the tool, not the review.
- **A picture larger than about 1.15 megapixels is scaled down before the
  reviewer sees it.** A whole A2 sheet at any dpi then blurs its 1.27 mm text,
  and a reviewer that cannot read a pin number renders and crops its own —
  in `/tmp`, where every read stops for the user's approval (board 2's RV1,
  2026-10-14). So every image here is at most 1280 × 896 px and the detail
  comes as tiles; the agents are told to work only in `tmpWork/`.
- **The BOM cannot tell KiCad's DNP flag from a field named DNP**: kicad-cli
  merges the field into its own `${DNP}` column, so asking for both writes
  one column, and either fills it with "DNP". Board 2's RV1 first lost the
  column silently, then needed the `.kicad_sch` to prove the flag missing.
  `parts.csv` reads both from the netlist, where the flag is
  `<property name="dnp"/>` and the field a `<field name="DNP">`.

## `rulecheck.py` — a custom DRC rule, proved before it is published

    python3 tools/review/rulecheck.py --board board1 [--rule pair]

Writes every custom rule the publisher would put in the board's `14_fab.md`
**now** (the derived area rules, the fab profile's and the board's own) into a
`.kicad_dru` beside a copy of the board's PCB in `out/rulecheck/<board>/`,
keeps the board's own non-generated rules, runs `kicad-cli pcb drc`, and
prints the violations grouped by rule. Run it after editing a board's
`FAB["rules"]` and before publishing: a rule that reads well can still miss
its nets (KiCad pairs only names ending P / N or + / -; a net name carries its
sheet path, so match `'*NAME'`) or, placed after the area rules, outrank them
(a board-wide pair rule once held a BGA's own balls to 0.15 mm). Never writes
the board.
