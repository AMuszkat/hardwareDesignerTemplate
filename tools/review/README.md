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

**Object changes** (`changes.py`, a PCB snapshot's `CHANGES.md` and
`changes.json`): what changed on the board object by object — parts moved
(from, to, rotation, side), added, removed, changed (value, footprint, DNP,
pad count); pads that changed
net; copper added and removed per net and layer (segments, length, the box
it lies in); vias; zones (outline, layers, net, priority, settings, filled
area); silk and fab texts moved, reworded, hidden, added, removed (a text
that only followed its moved part is counted, not listed); the outline; the
board's rules and net classes. Objects are matched by net, layer and
geometry to 1 µm, not by UUID, so a group ripped up and re-applied
unchanged is no change. **The base is the newest earlier PCB snapshot of
that board whose id has a verdict** — a `## <id> — … pcb …: … — PASS` or
`— CHANGES (…)` heading in `tools/handoff/reviews/boardN.md` (or the file
the board module's `REVIEWS` names); a snapshot that was never reviewed, or
was superseded, is passed over and named. No such snapshot: `changes.json`
has `"base": null` with an inventory, and `CHANGES.md` says everything is
new. A base taken before snapshots carried `geometry.json` is dumped from
its frozen board on the spot. A change wholly outside `--scope` is marked.
Any two snapshots (or two `geometry.json` files) by hand:

    python3 tools/review/changes.py <old snapshot> <new snapshot> [--json]

**Geometry** (`dump.py` → `geometry.json`, read through `geom.py`). A PCB
snapshot dumps the frozen board ONCE, through pcbnew with the zones refilled
(the `geometry` row; 1 s and 1.1 MB for an 8-layer board of 2,400 pads,
870 tracks and 660 vias): every track,
arc, via (span, and the layers its pad is flashed on), pad (net, layers,
shape, size, position, hole), zone (outline and each filled island per
layer), text (what, where, its box, whether it is visible), the outline, the
board's rules and net classes — all mm, KiCad board coordinates.
`geom.py` answers a reviewer's measurements from that file in plain python3,
read only:

    python3 tools/review/geom.py <snapshot | geometry.json> <query> [SEL ...] [--layer L] [--json]

| query | answers | definition |
|---|---|---|
| `nearest SEL [--within G] [--ignore NET] [--no-pours]` | the nearest foreign copper of each selected object | edge to edge, on a shared copper layer; foreign = another net, or no net |
| `gap SEL SEL` | the smallest gap between two selections | edge to edge; also the centre distance of the two closest objects |
| `label [REF ...]` | a reference label against its own part's nearest pad and any other part's | label box centre to pad copper-box centre, pads on the label's side; margin = other − own |
| `joints NET` / `--all-nets` | the turn at every joint of a net's tracks and arcs; not 0 / 45 / 90 (± 0.5°) flagged | ends meeting within 2 µm, or an end on another segment (a tee); a joint inside its own pad or via is not flagged |
| `via-pad [SEL] [--below G]` | each via against the nearest pad of its own net and of a foreign one | via pad edge to pad copper edge |
| `holes [--below G]` | hole-to-hole gaps: vias, plated and non-plated pad holes | wall to wall |
| `copper NET [--layer L]` | a net's copper per layer: length, segments, arcs, length per width, vias | centre-line length |
| `box X0,Y0,X1,Y1` | every copper object, part, text and zone touching a rectangle | copper edge, not centre |
| `show SEL`, `info` | what an object is; counts, layers, rules, net classes | |

`SEL`: `U1.A5`, `U1`, `net:NAME`, `pads:NAME`, `via:X,Y`, `track:X,Y`,
`uuid:PREFIX`, `box:…` (`--help`). The model: every copper object is a core
(a point, a line, a polygon) grown by a radius — exact for round, oval,
rectangular and rounded-rectangle pads, tracks and vias; KiCad's own outline
polygon (within 0.002 mm) for custom, chamfered and trapezoid pads; chords
within 0.001 mm for arcs; the refilled islands for pours. `dump.py` checks
each exact pad against KiCad's outline and falls back to the polygon when
they differ by more than 5 µm (`source.pad_models` counts both). The numbers
are geometry, not verdicts: DRC is the gate. `tests/test_review_geom.py`
holds every query to hand-computed values and to pcbnew's own collision
engine on the fixture board; `tests/test_review_changes.py` holds the diff
to a board with one part moved, one track and one via added.

**The helper grows by what reviewers still script.** The pcb-reviewer writes
a script only for a measurement `measurements.json` and `geom.py` cannot
answer, and names it under *Measured by hand*; when a verdict is published,
a measurement named there becomes a `geom.py` query (with a hand-computed
case in the test), not a note.

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
   `route.py status` / `edge`, `route.py audit` (every rule of the routing
   guide) and `route.py audit --findings` (the check each earlier finding
   carries, `handoff/findings.json`: a FAIL is a finding open again). Each
   gate's whole output is `gates/<name>.txt`.
3. **Exports what a reviewer reads, as files it reads without a shell**:
   `netlist.xml`, `bom.csv` (Value, Footprint, MPN, DNP, excluded, quantity;
   a missing column fails the row), `schematic.pdf`; `parts.csv` (every
   placed part: KiCad's DNP flag and a DNP field apart, the exclude flags),
   `pins.csv` (every pin: name, type, no-connect flag, net) and `nets.csv`; every sheet whole
   (`sheets/sheet-N.png`) and in overlapping 300 dpi tiles
   (`sheets/tiles/`, indexed by `tiles.md` — which parts each tile shows —
   and `words.csv` — where every text is); for a PCB, `pcb/top.png`,
   `bottom.png`, `inner.png` and the same views in 600 dpi tiles
   (`pcb/tiles/`, `tiles.md`, `words.csv`), `measurements.json`,
   `geometry.json`, `CHANGES.md` and `changes.json`.
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
- **A padstack with different pads per layer is dumped as its front (or
  only) layer's shape.** `geometry.json` records where a via or a through pad
  is flashed, not a different size per layer; a board that uses one needs
  `dump.py` taught, and until then that pad's gaps on inner layers are the
  outer pad's.
- **A hidden reference text has no position worth measuring** (KiCad leaves
  it at the footprint's origin or wherever it last was): `geom.py label`
  lists hidden labels by name and measures none of them, and the object diff
  reports a text hidden or shown, never as moved.
- **The object diff's base is decided by the verdict file, not by the
  folder.** A review whose verdict was never appended under its request id
  (`review-board` step 5) leaves the next snapshot diffing against an older
  state — correctly, but with more to read.
- **The BOM cannot tell KiCad's DNP flag from a field named DNP**: kicad-cli
  merges the field into its own `${DNP}` column, so asking for both writes
  one column, and either fills it with "DNP". Board 2's RV1 first lost the
  column silently, then needed the `.kicad_sch` to prove the flag missing.
  `parts.csv` reads both from the netlist, where the flag is
  `<property name="dnp"/>` and the field a `<field name="DNP">`.

## `rulecheck.py` — a custom DRC rule, proved before it is published

    python3 tools/review/rulecheck.py --board board1 [--rule pair]

**The publisher asks for it**: once a board has a PCB, `publish.py` refuses a
custom DRC rule that is new or changed until this has run on exactly that rule
text (`tools/handoff/README.md`, *Two things stop a publish*). It writes every
custom rule the publisher would put in the board's `14_fab.md` **now** (the
derived area rules, the fab profile's and the board's own) into a `.kicad_dru`
beside a copy of the board's PCB in `out/rulecheck/<board>/`, keeps the
board's own non-generated rules, gives the copy the page's net classes, runs
`kicad-cli pcb drc`, and judges each rule:

- **KiCad can read it.** One rule with an unknown property, an unknown
  function or a syntax error makes KiCad drop the whole `.kicad_dru`,
  silently — every rule then reads "0 violations". A canary rule that must
  fire says whether the file loaded; when nothing fired, each rule is run
  alone and the one that kills the file is named.
- **Every name it tests is on the board**: a net (`A.NetName == '*CK_T'`), a
  net class, a footprint's courtyard, a rule area, a pair. Each is a probe —
  an assertion rule that fires once per item its condition matches — and a
  name that matches nothing FAILS the rule. A rule that reads well can still
  miss its nets: KiCad pairs only names ending P / N or + / -, and a net name
  carries its sheet path, so match `'*NAME'`.
- **A rule about a BGA's escape carries the bound.** A clearance or a width
  scoped to a BGA's courtyard decides what can leave its ball field. The
  board module's `FAB["escape"]` gives
  `kicad_project/tools/route/escape_bound.py`'s arguments per BGA and layer
  (or a `waive` with the reason); it is run here, and no row, or a bound that
  is PROVEN SHORT, FAILS the BGA's rules. `14_fab.md` states the bounds.

It then lists the violations of the real rules, grouped by rule (`--rule`
filters the list, never the judging): on a routed board they are the other
half — a rule placed after the area rules can outrank them (a board-wide pair
rule once held a BGA's own balls to 0.15 mm).

The result goes to `tools/handoff/rulechecks/<board>.json` — generated, never
typed, committed with the publish — and the last line is `RULECHECK PASS` or
`RULECHECK FAIL`. Exit 0 every rule proven (or waived in `FAB["unproven"]`,
printed `WAIVED`), 1 a rule is not, 2 nothing could be checked (no PCB with a
footprint yet, no kicad-cli). Never writes the board. About 3 s per DRC run
on a 1.7 MB board; a clean run is one to three.

Facts about KiCad's DRC that the probes rest on (10.0.7), each of which read
as a wrong verdict before it was known:

- **KiCad reports at most 199 violations of one type in a run**, and every
  probe is an `assertion_failure`. On a real board the tracks and pads
  visited first use the limit up and a probe further down reads 0 without
  having been asked (the first run on a routed 17-rule board read as "no rule
  loaded"). So a run that hit the limit settles only the probes that fired;
  the ones at zero run again, without the others, until a run stays under it.
  A count from a run that hit the limit is a lower bound, printed with a `+`.
  `--all-track-errors` lifts the limit for clearance and unconnected items
  only.
- An assertion is about ONE item: a `B.` term is never true in it (the probe
  asks the name of `A`), and `A.isCoupledDiffPair()` is always false —
  `A.inDiffPair('*')` is the same question of one item.
- A net test is probed on pads only (every net has one): the count stays
  small on a routed board.
- A rule on one item (no `B.`) also has its WHOLE condition probed; zero is a
  WARNING, not a failure — a rule about tracks is right on a board with none
  yet, wrong if its names can never be true of one item together.
