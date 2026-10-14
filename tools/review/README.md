# `tools/review` — a board change, frozen and gated for its reviewer

**Concept's.** Every schematic change and every PCB change a board session
finishes is reviewed by the concept session's reviewer agents
(`.claude/agents/schematic-reviewer.md`, `pcb-reviewer.md`) before the board
builds on it. The `review-board` skill is the procedure; the board's side is
its `REVIEW.md` and `handoff/15_reviews.md`
(`kicad_project/RELAY_PROTOCOL.md`, "Reviews").

    python3 tools/review/snapshot.py --board board1 --kind schematic --id RV3
    python3 tools/review/snapshot.py --board board1 --kind pcb       --id RV4

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
3. **Exports what a reviewer reads**: `netlist.xml`, `bom.csv` (Value,
   Footprint, MPN, the DNP field and KiCad's DNP flag), `schematic.pdf` and
   one PNG per sheet, and for a PCB `pcb/top.png`, `bottom.png`, `inner.png`.
4. Writes `SUMMARY.md`. Exit 0: every gate passed; 1: a gate failed (the
   review starts from it); 2: nothing to review (no KiCad project, no PCB, no
   `kicad-cli`).

It never writes a board folder. `out/` is disposable; the reviewer's report,
the record, goes to `history/reviews/`.

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
