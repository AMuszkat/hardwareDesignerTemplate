# ROADMAP — speeding up board design on this template

**Scope:** the improvements found in the 2026-11-08 review of where the two
sessions spend their time on a board's schematic and PCB, ordered by return
on effort, each with the files it touches and the test that says it is done.
**Not here:** the product (`design/`), the sessions' contracts (`CLAUDE.md`,
`kicad_project/CLAUDE.md`), the protocol (`kicad_project/RELAY_PROTOCOL.md`)
— those change only as an item here is done, and every item that changes a
contract, a protocol, a default or a machine requirement is marked as the
user's decision.

## The finding, in one paragraph

The two slowest steps share one shape: a tool already knows the answer and a
session re-derives it. The schematic planner writes a complete, proven KiCad
schematic, and the board session then redraws it call by call through
Konnect. Routing waits on a full review round trip per group, on a
pure-Python router that loses its work when killed, and on infeasibility
found after the copper instead of before it. Several handoff facts cross the
wall as prose or pictures and are typed again by hand on the board side.

The baseline, from the repo's own records:

| step | recorded cost | recorded in |
|---|---|---|
| drawing one schematic (board1) | 787 Konnect calls; 5–10 min a sheet with one agent per sheet; then the frame, flags and footprints passes | `kicad_project/.claude/skills/schematic/draw_sheet.md` |
| one negotiated-router iteration, 20 nets, 76 × 51 mm window | 18 s; 300 iterations by default | `kicad_project/tools/route/README.md` |
| runs lost to a timeout (board2 R5) | 4 × 90 min, no partial written | same |
| via fixer with push, about 100 vias | 20–30 min | `kicad_project/.claude/skills/via-links/SKILL.md` |
| one BGA fanout (board1) | reviews RV4.1 to RV4.12; seven rounds of per-net allowances before `fanout.py` existed | git log |
| bundle order inversions found at R5, under a pour (board2) | 48 | routing skill §0 |
| `route.py check` | two full kicad-cli DRC runs per call, no cache | `kicad_project/tools/route/route.py` |
| hand-typed at board start | the guide's rule table, placement keep-outs, hole-to-edge, the layer/class table | the scaffold files |

## How to read an item

**id · title.** What changes. **Where:** the files. **Evidence:** why it is
believed to pay. **Done when:** the test. **Size:** hours or days. **Gate:**
`tool` — a template change the sessions may make; `user` — it changes a
contract, a protocol, a default or a machine requirement, so the user decides
first and the PR description opens with that decision.

## Phase 0 — hours each, no behaviour change

**R1 · The router writes its partial every iteration.** `maze.Grid.negotiate`
already has an `on_iter` hook; `netgroup.py` writes `<plan>.partial.json` from
it, so a run killed by a timeout keeps its contested cells and `reach` table.
**Where:** `kicad_project/tools/route/netgroup.py`, its README (the
"timeout-killed run writes no partial" trap becomes history). **Evidence:**
board2 lost four 90-minute runs this way. **Done when:** a fixture run killed
after 5 s leaves a partial naming its last iteration. **Size:** 1–2 h.
**Gate:** tool.

**R6 · One DRC per check, in-process audit.** (a) `route.py check` caches the
baseline DRC report under `<board>/routing/.drc_cache/` keyed by the sha256 of
the `.kicad_pcb`, `.kicad_pro` and `.kicad_dru`; (b) on a cache miss the
baseline and the scratch DRC run as two concurrent kicad-cli processes; (c)
`route.py audit` runs the DRC summary and the pour summary in-process instead
of spawning `place.py drc` and `pour.py`, each of which loads pcbnew and the
board again. **Where:** `kicad_project/tools/route/route.py`. **Done when:**
the second `check` of the same board logs one kicad-cli run and its output is
byte-identical to today's. **Size:** half a day. **Gate:** tool.

**V4 · The snapshot runs its gates concurrently.** DRC, Board Setup check,
footprint check, route status and edge, the render and the tiles are
independent child processes; a thread pool runs them together. **Where:**
`tools/review/snapshot.py`. **Done when:** `SUMMARY.md` is identical and the
wall clock drops. **Size:** 2 h. **Gate:** tool.

**R8 · Prescribed shell lines become tool subcommands.** The allowlist covers
`tools/kpy tools/*` and the venv's `tools/*`; the skills still prescribe bare
`kicad-cli sch export pdf`, `python3 -c` chunk printers and `timeout …
PYTHONUNBUFFERED=1 …` prefixes, each a permission prompt. Add
`netgroup.py --timeout S` (unbuffered log, iterations sized to end inside it,
partial written), `sch.py pdf` for the plan preview, and a `place.py render`
for the placement crop. **Where:** those tools and the `routing`, `schematic`,
`placement` skills. **Done when:** no skill names a bare kicad-cli, `python3
-c` or `timeout` line. **Size:** half a day. **Gate:** tool.

**X3 · A fork helper for the board tools.** Port `studies/par.py` (fork, not
spawn; results in job order; `HW_JOBS=1` forces serial) to
`kicad_project/tools/par.py`. pcbnew objects are neither picklable nor
thread-safe, so the rule is: load the board once, fork, read in the children,
return plain tuples, write only in the parent. First user: `pack.py`'s six
restarts. **Done when:** serial and parallel runs give identical plans.
**Size:** 2 h. **Gate:** tool.

## Phase 1 — days each, the large returns

**S1 · `sch.py draw`: install the proven preview as the board's sheets.**
`plan` already writes every sheet as a complete KiCad file under
`sch_plan/preview/` and proves it (netlist parity IDENTICAL, ERC clean, check
clean); the Konnect replay exists only to reproduce that file. `draw` copies
each planned sheet into the board, carrying over the existing sheet's UUIDs
for symbols (by refdes and unit), sheet symbols and the root so the PCB's
links survive a redraw; sets the project name and instance paths; backs up
under `.sch_backups/`; and proves the write the way `frame` and `flags`
already do: netlist parity with the plan and with the handoff, ERC, `check`,
and a kicad-cli re-save that changes nothing. It subsumes `frame`, `flags`
and `footprints`: the preview already carries the section text boxes, the DNP
flags and the Footprint field. Konnect stays for a one-off hand fix after
drawing; `drawn` stays as the gate on such a fix. **Where:**
`kicad_project/tools/sch/sch.py`, `emit.py` (UUID carry-over), the README;
the `schematic` skill §3–§5 rewritten and `draw_sheet.md` retired;
`kicad_project/CLAUDE.md` "KiCad files go through Konnect or pcbnew" is
rewritten as a rule, not an exception list (decided 2026-11-08): **which tool
edits what**, one row per kind of edit, naming the cases where the
project's own tools are preferred to Konnect or pcbnew and the cases that
stay theirs —

| edit | tool | why |
|---|---|---|
| a sheet's drawing: parts, wires, labels, frames, title block, DNP and BOM flags, Footprint fields | `sch.py plan` then `draw` | the plan is proven before a file is written; the write is proven after |
| a one-off fix on a drawn sheet (a jog moved to clear a text) | Konnect, KiCad open; then `drawn` | one undo step, no plan churn |
| a library symbol | `symgen.py` / `symfields.py` | Konnect cannot set field positions, pin lengths or delete a saved symbol |
| a footprint file | `fp.py` | land hashed and signed |
| Board Setup, a placement plan, a routing group, labels, a schematic re-link | the `pcbnew` scripts, KiCad closed | bulk, backup first, read back after |
| a single interactive move with KiCad open | Konnect | one undo step |
| any KiCad file by text editor | never | — |

and the `schematic` skill's §3 reads from that table.
**Evidence:** `PLAN PASSES` already means the preview is the drawing; the
README's whole "What Konnect cannot do" list (sheet-pin sides, stale embedded
symbols, skipped fields, duplicate junctions, field positions, UUID churn)
is the cost of the replay. **Done when:** a board drawn by `draw` passes
`parity`, `parity --handoff`, `check`, ERC and `drawn` with zero Konnect
calls, and a redraw keeps every symbol UUID the PCB references so
`schupdate.py` finds nothing to re-link. **Size:** 2–3 days. **Gate:** decided
2026-11-08 — yes, as the rule table above; now tool.

**S3 · Small edits are plan then draw.** Follows from S1: skill §5 says
`plan --handoff`, `draw`, done; a straight Konnect edit no longer leaves
`sch_plan/` stale. **Size:** part of S1.

**H1 · Typed workmanship rules in the handoff.** A `WORKMANSHIP` dict in
`tools/handoff/board_template.py` with exactly the rows `guide.py` knows
(joints, copper to edge, via to pad gap, 45-degree via link, copper shape,
class width, thermals, plane layer, no vias, class layers, keep-out, via
field, ground beside, frozen, thermal pad), each with its source; published
as `handoff/rules.json` and rendered as rows of `10_layout.md`. `guide.py`
reads `rules.json` first, and the table in `general_routing_guide.md` becomes
a generated block between markers (as STATUS has) that a tool refreshes and
the review snapshot checks. `route.py rules` prints each row's handoff
source. **Where:** `board_template.py`, `publish.py`,
`kicad_project/tools/route/guide.py`, the scaffold's guide,
`tools/review/snapshot.py` (a gate: guide block current). **Evidence:**
commit 952b510 — a carrier board stopped on these rows as a blocking
question. **Done when:** a new board's guide table is produced with no hand
typing and a hand edit inside the markers fails the snapshot gate.
**Size:** 1–2 days. **Gate:** tool.

**H2 · Floorplan zones and the placement seed.** `FLOORPLAN_ZONES = [{name,
rect, side, allow, note}]` in the board module, published as
`handoff/floorplan.json` (the SVG stays for the eye); `place.py seed` writes
them into `placement.json` `keepouts` and sets `hole_to_edge` from
`14_fab.md`; the layer and class table of `ROUTING_PLAN.md` §1 is generated
from `14_fab.md` the same way. **Where:** `board_template.py`, `publish.py`,
`kicad_project/tools/place/place.py`, the scaffold. **Done when:** a new
board's `placement.json` passes `place.py check` without a hand edit.
**Size:** 1 day. **Gate:** tool.

**R7 · A pre-routing feasibility gate.** `route.py prelayout` runs, from the
placement and the guide, every test that the two boards learned to run late:
`fanout.py plan` and `layers` per BGA; `escape_bound.py` per BGA per layer;
the via-site count per fine-pitch row (the staircase rule of the routing
skill §0); the bundle inversion count per connector pair; `pourreach.py` for
the rail vias. One PASS/FAIL line per test, required in the placement review
request. **Where:** `kicad_project/tools/route/route.py` (or a new
`prelayout.py`), the `placement` skill §5, the pcb-reviewer checklist item
1, the `REVIEW.md` scaffold's Gates line. **Evidence:** the in-pad fanout
(seven rounds), the 48 inversions at R5, the via-site shortage at Q8 — each
found after copper was laid. **Done when:** fixtures of those three cases
produce FAIL lines before any routing. **Size:** 2–3 days. **Gate:** tool.

**V2 · A scoped, diffed snapshot.** For a PCB group review, tile only the
group's window (the applied manifest's bounding box plus a margin) and skip
the sheet tiles; for every review, compare each tile with the same tile of
the board's previous snapshot by perceptual hash and write `changed.md`; the
reviewer agents read changed and in-scope tiles first and the rest only for a
"fab release" or "all" scope. **Where:** `tools/review/snapshot.py`,
`.claude/agents/pcb-reviewer.md`, `schematic-reviewer.md`. **Done when:** a
snapshot of an unchanged board lists zero changed tiles and a one-via change
lists only its tiles. **Size:** 1 day. **Gate:** tool.

**V3 · Measurements as data for the PCB reviewer.** The snapshot writes
`measurements.json`: per `10_layout.md` row that names a distance, the
measured value; per coupled pair, gap, length and skew; per net, via count
and layers; pours and their islands; parts outside their `placement.json`
group; silk over mask openings. The pcb-reviewer reads it instead of
measuring with pcbnew heredocs. **Where:** `tools/review/snapshot.py`,
`.claude/agents/pcb-reviewer.md`. **Done when:** the reviewer prompt's "to
measure the board" section is gone and a fixture review completes with no
Bash call. **Size:** 1–2 days. **Gate:** tool.

## Phase 2 — process and algorithm

**V1 · Review by milestone, with a group review on request.** Decided
2026-11-08 (option D). Mandatory reviews: placement as a whole once every
group passes `place.py check`; routing at five milestones — fanout and
escape, buses, power, pours, close-out — plus the fixes for a CHANGES
verdict and the fab release. A board session may still request a review of
a single group it is unsure of, in the same `REVIEW.md` form; `route.py rip`
by the applied manifest is the safety net a per-group stop was guarding, and
the pre-routing gate (R7) catches the structural failures that used to
surface at R5. Resolve the contradiction found in this review: the routing
and placement skills say "the next group does not start until the verdict is
PASS", while `kicad_project/CLAUDE.md` and the protocol §10 say "independent
work may go on" — all six files take the protocol's wording. **Independent**
is defined: a group shares no corridor with the change under review, uses
none of the copper that change reserved, and moves no part that change
touched; the board writes the claim in `ROUTING_PLAN.md` §5 and the reviewer
checks it at the next milestone. **Where:** the `routing`, `placement` and
`review-board` skills, `kicad_project/CLAUDE.md`, `RELAY_PROTOCOL.md` §10,
the `ROUTING_PLAN.md` and `REVIEW.md` scaffolds. **Done when:** the files say
the same thing and a board's first routing milestone is reviewed as one
request. **Size:** half a day. **Gate:** decided — now tool.

**R2 · Conflict-only rip-up.** After iteration 1, `negotiate` re-routes only
the nets that overlap (standard PathFinder); the full re-route stays as
`--full`. **Where:** `maze.py`, `netgroup.py`. **Done when:** the fixtures
converge in the same or fewer iterations at lower wall clock, deterministic
per seed. **Size:** 1 day. **Gate:** tool.

**R3 · Corridor-bounded search.** Each net's A* is limited to a band round
its straight line (or its previous path) and widened on failure; `--limit`
becomes the fallback, not the first thing a big window hits. **Where:**
`maze.py`. **Size:** 1 day. **Gate:** tool.

**R5 · Parallel where the work is independent.** Through X3: seed sweeps
(first converged wins, deterministic by seed); the nets of one iteration
routed in parallel against the previous iteration's congestion (the Jacobi
variant, behind a flag, documented as a different convergence);
`escape_bound.py` per layer; the via fixer's candidate search per via with a
serial commit; `pack.py` restarts. **Done when:** `HW_JOBS=1` and parallel
give identical plans for every sequential mode. **Size:** 2–3 days.
**Gate:** tool.

**X1 · The board session is headless by default.** `place.py apply`,
`route.py apply` and `rip`, `labels`, `schupdate.py` and `board_setup.py
apply` already want KiCad closed; with S1 the GUI is the user's viewer only.
A standing permission to close KiCad is recorded once in
`kicad_project/project.json` and read by the skills, instead of being asked
every session. **Where:** `setup.sh init`, `project.json`, the `board-setup`,
`placement`, `routing` skills, `kicad_project/CLAUDE.md`. **Size:** half a
day. **Gate:** user.

**X2 · The relay during a build.** The switch stays the user's. `bin/board`
and `bin/concept` print one line when it is off, since with it off the user
carries every ring of every review. **Size:** 1 h. **Gate:** user.

## Phase 3 — new mechanisms

**R4 · A compiled router core.** `kicad_project/tools/route/_maze.c`: the A*
inner loop, the halo stamping and `conflicts`, compiled by `tools/setup.sh`
with the system C compiler into `tools/.native/` and loaded through ctypes
over the existing `bytearray` and `array` buffers (no Python headers, so it
works with KiCad.app's bundled Python too). `maze.py` falls back to pure
Python when the library is missing; a fixture routed both ways must be
identical. `SETUP.md`: a C compiler becomes a requirement. **Evidence:** 18 s
per iteration is the inner loop; a compiled A* over the same arrays is
typically 20–100× faster. **Size:** 1–2 weeks. **Gate:** user (the
requirement), then tool.

**S2 · A symbol generator from a pin table.** `tools/sch/symgen.py`: from a
CSV of pin number, name, type and function or bank, units by function, at
most 40 rows a side, every unit's body with its top-left corner at the
origin, pin length for the widest number, fields just above the origin, and
a pin-by-pin report against the symbol it replaces. It retires the
`create_symbol` two-pass, `symfields.py`, `--pin-length` and `--delete`
loop. **Done when:** a symbol it writes passes `check`'s `pins-side`,
`field-far` and `text-graphic` on a planned sheet with no fix-up. **Size:**
3–4 days. **Gate:** tool.

**H4 · Datasheet extraction, once per MPN, on the concept side.**
`tools/parts/extract.py`, run by `datasheet.py` after filing, in its own venv
`tools/parts/.venv-ml` made by `setup.sh install` (torch-sized; absent, the
step is skipped with one clear line and filing still works):

- Docling, text-layer mode, for the tables → `<MPN>.pins.csv` and
  `<MPN>.tables.json`, every cell with page and bounding box (datasheets
  are born-digital: the text layer is exact, no OCR);
- ColQwen2.5 as a page index → `<MPN>.pages.json` for the queries "pin
  assignment", "recommended land pattern", "absolute maximum ratings",
  "package outline", so a reader opens two pages, not two hundred;
- Qwen2.5-VL-7B over the land-pattern crop → `<MPN>.land.json`, candidates
  never authority, served locally (Ollama or vLLM); fallback parser
  PaddleOCR-VL for a scanned PDF.

`publish.py` copies and hashes the three files into `handoff/datasheets/`
beside the PDF. Consumers: S2 for symbols; `fp.py verify --against` to
compare the footprint with `land.json` and show only mismatches; the
schematic-reviewer's checklist items 3 and 4 as a diff; the page ranges a
session reads. **Validate first:** run it over the datasheets already filed
and diff `pins.csv` against the handoff's hand-made PINOUT CSVs — that corpus
is the ground truth; keep the parser that matches. Licences: MIT and Apache
2.0 only (Docling, Qwen VL, ColQwen, PaddleOCR-VL); GPL and AGPL parsers
(Surya, Marker, Ultralytics) stay out, as Konnect does. `SETUP.md`: an
optional GPU. **Size:** 1–2 weeks. **Gate:** user (the optional requirement),
then tool.

**H3 · A handoff self-test agent.** `.claude/agents/handoff-reader.md`: a
fresh-context agent with `omitClaudeMd: true` and Read, Grep and Glob only,
given one board's `handoff/` folder, asked to plan the schematic, the
placement and the routing groups from it and to list every fact it lacks as
`QUESTIONS.md`-shaped entries. Run by `publish.py --selftest` or as a step of
the `decision` and `answer-question` skills before a board session starts.
It is the one place a subagent beats a session here: it simulates a board
session's first day in minutes, without the round trip. **Done when:** on a
fixture handoff with one NETS row deleted it reports that hole. **Size:** 1
day. **Gate:** tool.

## Not doing, and why

- **Rewriting the tools in another language.** pcbnew pins the interpreter;
  the time is in kicad-cli, which is already C++, and in one Python loop
  (R4).
- **Python threads for CPU work.** The GIL; threads only for waiting on
  child processes (V4, R6).
- **numba, PyPy, a free-threaded Python.** KiCad's interpreter cannot host
  them.
- **A vision model on review tiles.** A 7B model cannot read 0.5 mm pitch
  copper; the diff (V2) and the geometric audits do the job.
- **Subagents instead of board sessions.** A subagent cannot be fenced to a
  folder, cannot ask the user, and inherits context that would defeat the
  handoff's own measurement; H3 takes the one place a subagent helps.
- **Freerouting as the router.** It loses the `.kicad_dru` rules, the plane
  layers and protected copper (its README); it stays a feasibility oracle.

## Measure before and after, per board

| metric | today's source |
|---|---|
| minutes from `PLAN PASSES` to `PARITY OK` | the schematic skill's figures |
| seconds per `route.py check` | its log |
| seconds per router iteration; runs lost | the netgroup log |
| reviews per board; minutes per review round trip | `15_reviews.md`, `history/reviews/` |
| questions per board | `QUESTIONS.md` (the handoff's own metric) |
| hand-typed rows at board start | `placement.json`, the guide, `ROUTING_PLAN.md` §1 (target: 0) |

## Order and PR shape

One pull request per phase, one commit per item, each commit message opening
with the item id. An item gated `user` opens the PR description with the
decision it needs. This repository is the template, so nothing here goes
through `giveback.py`; a project takes the items with `giveback.py merge`.
