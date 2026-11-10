# `tools/fab` — the fabrication package

    tools/kpy tools/fab/fabpkg.py --board board2 [--out DIR] [--force-unreleased]

The board session's last deliverable: Gerbers, drill files, outline, BOM, placement and the
order notes quoted from `handoff/14_fab.md`, in `<board>/fab/<pcb>-<YYYYMMDD>/`. The order is
the user's; nothing is uploaded. The module docstring is the full story.

- **It refuses an unreleased board.** The last `## ... fab release ...` heading in
  `handoff/15_reviews.md` must end `PASS`, and `place.py drc` must exit 0. With
  `--force-unreleased` it writes a draft whose ORDER_NOTES.md says so in its first line.
- **Every enabled copper layer** goes out (a 4-layer board's In1 / In2 too), with mask, silk,
  paste and Edge.Cuts; the drills are Excellon, PTH and NPTH apart, with a Gerber X2 map.
  Zones are refilled (`--check-zones`).
- **The BOM spells out every designator.** kicad-cli writes "H1-H4" by default, which a
  parser (and an assembler's) reads as one designator. `--ref-range-delimiter ""` spells them
  all out.
- **It checks what it wrote, and fails on any of these:** a layer Gerber missing (the
  `.gbrjob` is not a layer); a PTH or NPTH drill file missing; a BOM line with no MPN (the
  assembler would choose the part: board2 RV23.4); a fitted footprint missing from the BOM or
  from the placement file.
- ORDER_NOTES.md quotes 14_fab.md's page head and its "What to choose on the order form"
  section verbatim (the table and the order-notes block). It also gives the handoff revision,
  14_fab's hash, the release line and the `drc` lines.
