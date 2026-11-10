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

## Progress

Each landed item, with what it measured on `tests/agentic_test.py`'s
fixtures (the `pcb` stage's board is `tests/fixture_pcb.py`'s).

| item | landed | measured |
|---|---|---|
| R1 | Phase 0 PR | a run killed after 15 s leaves a partial naming iteration 1 and its contested cells; `--timeout 15` stops at 13 s with the reach table; converged plan byte-identical |
| R6 | Phase 0 PR | `check` 2.6 s → 1.8 s cold, 1.55 s warm (one kicad-cli run); `audit` 1.8 s → 0.4 s; stdout identical |
| V4 | Phase 0 PR | `snapshot --kind pcb` 6.6 s → 3.3 s; SUMMARY rows identical |
| R8 | Phase 0 PR | no skill prescribes a bare kicad-cli, `python -c` or `timeout` line |
| X3 | Phase 0 PR | `pack.py`, six restarts: 3.3 s → 0.8 s; plan identical to serial |
| V2 | Phase 1 PR | unchanged board: 0 of 14 tiles changed; one added via: one region, at the via, top and bottom only; a scoped review flags a change outside its scope |
| V3 | Phase 1 PR | a fixture review with Bash forbidden completed; a real 4-layer carrier measured in 0.7 s (3 pairs with skew, 14 layout rows) |
| R7 | Phase 1 PR | the three historical failures (in-pad fanout, via-site shortage, reversed bundle) FAIL before copper; board2's row reproduces Q8's 9 sites for 11 pins |
| H1 | Phase 1 PR | the guide's handoff rows written with no typing; a hand edit in the block fails the snapshot |
| H2 | Phase 1 PR | a new board's `placement.json` passes `place.py check` after `place.py seed`, no hand edit |
| S1, S3 | Phase 1 PR | draw 4.4 s, zero Konnect calls (the fixture plan replaces 54); every gate passes; a redraw keeps 16 symbol and 2 sheet UUIDs; `schupdate` re-links 0 of 15 |
| R2 | Phase 2 PR | 16 nets: 14 iterations in 21 s (52.5 s full re-route, same count); PCB fixture plan identical, 29 s → 21 s; `--full` keeps the old hashes |
| R3 | Phase 2 PR | opt-in (`--corridor`): identical plans, no measurable gain on the fixtures — A* already searches tightly there. Re-measured 2026-11-09 on its own case (`fixture_pcb.py --kind sparse`: four 14 mm bundles in a 100 × 70 mm window): identical plan, 5.4–5.7 s against 5.6–6.8 s — still opt-in. Found there: R1's `reach` flood took 12 of 16 s of a converging run; capped once it holds a goal and 20 000 cells, the run is 17–24 s → 4.7–7.2 s, the same plan |
| R5 | Phase 2 PR | `--seeds 4`: 82.7 s → 27.4 s, plan identical serial / forked; `--jacobi`: 16 nets 11.8 s on 8 cores, a different convergence; prelayout jobs concurrent. viafix: serial by nature (each fix changes the board the next via is judged on) and a forked candidate screen was slower (58 s → 65 s); the time was full-board scans repeated per candidate, now cached until the next edit — 200 vias `--push` 139 s → 20 s, 48 vias 8.3 s → 1.2 s, the same plan with the cache off (`vias` stage) |
| V1 | Phase 2 PR | the six files say one rule: milestones, a group on request, independence defined and claimed in `ROUTING_PLAN.md` §5 |
| X1 | Phase 2 PR | `close_kicad` in `project.json`, asked once by `setup.sh init`, printed in every board brief |
| X2 (B) | Phase 2 PR | `bin/board` and `bin/concept` print the relay-off line when a board exists; D dropped 2026-11-09 by the user: the relay stays off by default |
| S2 | Phase 3 PR | the fixture MCU's symbol from its pin table: 0 pin differences, the plan with it PASSES with no fix-up; a 124-pin table split 80 / 40 / 4 |
| H3 | Phase 3 PR | `publish.py --selftest` on the fixture handoff with a NETS row deleted reported that hole (and 12 real fixture gaps), 4 min |
| R4 | Phase 3 PR | the same routes both cores; 16 nets 52.5 s → 4.5 s (`--full`), 21 s → 1.0 s (default); PCB fixture 20 s → 2.2 s |
| H4 | Phase 3 PR | Docling's pin table matched the i.MX 8M Mini pinout, 486 of 486, now from 24 of 96 pages (`--pages auto`). Widened 2026-11-09 against KiCad's stock symbols (`extract_corpus.py`, 19 vendor datasheets): 461 of 728 pins named alike, 2 parts exact after a multi-package parser — the gate fails, so the consumers stay unwired; drawn-only pinouts, prose tables and ball-map grids are what it cannot read. the page index is the text layer (ColQwen2.5 gave the same pages at 18 s a page); the Qwen2.5-VL `land` stage was run on the iGPU and dropped — it read a 0.4 pitch as 0.6 and returned 14-digit pad counts, where Claude reading the page is faster and right |
| W1 | Phase 4 PR | the board-side wall never returns `ask`: 11 cases (own / other board / handoff × claim, no claim, one board) end allow or deny with the line to run. Evidence: two board sessions parked 20 h each on the prompt |
| M1 | Phase 4 PR | `tools/measure/sessions.py`: wall-clock by class (model, tool, waiting, permission stall), stage, tool family, review turnaround, reviewer runs, question chains. First finding: a 626.6 min reviewer run was 618.0 min of permission-prompt stall |
| M2 | Phase 4 PR | `fixture_pcb.py --kind large`: 326 parts, a 536-ball 0.5 mm BGA, 366 vias, seeded so two builds are the same board; `tests/test_pcb_large.py` holds each tool to a result hash and a time ceiling |
| P1 | Phase 4 PR | `place.py labels`: 316 s → 4.3 s on M2 (87.7 s → 5.4 s on a real 300-part board), result hash identical — bucket grids in place of all-pairs |
| P2 | Phase 4 PR | on M2, hashes identical: `place.py auto` 42 s → 5.5 s; `place.py drc` 10.5 s → 2.6 s (silk sampling 8.0 s → 0.1 s); `fanout.py layers` 35 s → 4.4 s (129 s → 3 s on a real BGA: one flow moved per layer, not re-solved). `check` 1.0 s and `plan` 0.6 s were already at the pcbnew-load floor |
| R9 | Phase 4 PR | `maze.py` prints one stderr line on the Python core or a stale build; `setup.sh check` and the harness's router stage FAIL a missing or stale core where a compiler exists |
| RA | Phase 4 PR | `netgroup.py`: `BLOCKED` (exit 4) before negotiating, with the wall named nearest first; `STUCK` (exit 3, `--stall N`) naming the nets and the cells they recycle — seconds, where board1 R2 ran 433 iterations to "no solution". A plan carries its origin (spec and board hash): `check` / `apply` refuse a stale one (exit 5), and a failed run retires the old plan at its path |
| RB | Phase 4 PR | `tools/bgwait.py` (run, wait by pid with a limit, status, kill) and a PreToolUse Bash guard on both sides that refuses a wait loop on a process name. Evidence: a `while pgrep -f` loop matched its own shell for 4 h 52 min after three 3-minute runs |
| R10 | Phase 4 PR | `race.py`: three spec variants as processes, each on its own board copy with its own check, one table, the winner by a stated order — 1.9–2× over serial, each plan's sha equal to its solo run |
| V6 | Phase 4 PR | `snapshot --kind pcb` writes `geometry.json` (queried by `geom.py`: 70 hand values exact, 639 object pairs within 1 µm of pcbnew) and `CHANGES.md`, the object diff against the last REVIEWED snapshot |
| V5 | Phase 4 PR | five check rules (`cap reach`, `layers apart`, `stitch via`, `neck length`, `distance`) in `route.py audit`; a verdict's `Check RVn.k:` line → `handoff/findings.json` → `route.py audit --findings` (6 findings on a real board in 0.4 s) → the snapshot's `audit` and `findings` gates |
| Q1 | Phase 4 PR | a new or changed custom DRC rule is proven on the PCB before it publishes (`rulecheck.py`, 1.7 s a run); a third answer on one `Topic:` is refused without an `Escalated:` line |
| V7 | Phase 4 PR | `REVIEW.md`'s form has a `Meanwhile:` line; the contract and both skills say ring, then start it. Unmeasured until M1 reads the next real board |

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
field, ground beside, frozen, thermal pad — and, since V5, the five checks),
each with its source; published
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

**X1 · The board session is headless by default.** Decided 2026-11-09
(option C). The `pcbnew` scripts on the closed board are the normal path for
every PCB step (`board_setup.py apply`, `place.py apply`, `route.py apply`
and `rip`, `labels`, `silk`, `schupdate.py`, `refresh.py`); KiCad is opened
only for a step that needs Konnect under the rule table of S1 (an
interactive move, a one-off schematic fix, a library refresh) or because the
user wants to look. A standing permission to close KiCad is recorded once in
`kicad_project/project.json` by `setup.sh init` and printed in the
session-start brief, instead of being asked per board and kept in
`ROUTING_PLAN.md` §4. The guard stays in full and no permission waives it:
close only after `get_project_info` shows this session's own project, only
after `save_project`, by SIGTERM, lock files cleared only once no KiCad
process remains; another board's project open means do nothing and tell the
user. A session that closed a project the user had open reopens it after
its closed-board step and says so. **Where:** `setup.sh init`,
`project.json`, `tools/session/relay.py brief`, the `board-setup`,
`placement`, `routing` skills, `kicad_project/CLAUDE.md` (the same paragraph
S1 rewrites), the `ROUTING_PLAN.md` scaffold §4. **Done when:** a board is
placed and routed through the scripts with KiCad opened only by the user.
**Size:** half a day. **Gate:** decided — now tool.

**X2 · The relay during a build.** Decided 2026-11-09: B now, D after one
board has run under it. **D dropped 2026-11-09** (the user): `new-board`
never turns the relay on; off stays the default (`RELAY_PROTOCOL.md` §0) and
the switch the user's alone. The text of D is kept below as the record. The switch stays the user's; a message still carries
no fact and waives no gate. **B, now:** `bin/board` and `bin/concept` print
one line when the relay is off and a board folder exists — what the relay
does and the command that turns it on — since with it off the user carries
every ring of every review and answer. **D, later:** `setup.sh new-board`
turns the relay on when the first board is created, says so, and
`bin/relay off` still works; taken only after the arming handshake has been
seen to work on the user's machine, because the protocol's caution about a
ring lost on a permission prompt is earned. **Where:** `bin/board`,
`bin/concept`, `tools/session/relay.py brief`; later `setup.sh new-board`,
`RELAY_PROTOCOL.md` §0 (the default). **Size:** 1 h now; 1 h later.
**Gate:** B decided — tool, done; D dropped.

## Phase 3 — new mechanisms

**R4 · A compiled router core.** `kicad_project/tools/route/_maze.c`: the A*
inner loop, the halo stamping and `conflicts`, compiled by `tools/setup.sh`
with the system C compiler into `tools/.native/` and loaded through ctypes
over the existing `bytearray` and `array` buffers (no Python headers, so it
works with KiCad.app's bundled Python too). `maze.py` falls back to pure
Python when the library is missing; a fixture routed both ways must be
identical. Decided 2026-11-09 (option B): the C compiler is an OPTIONAL
requirement in `SETUP.md`, in Blender's tier — `setup.sh check` prints the
install command (`build-essential`, `gcc`, the Xcode command line tools) and
a note that the router runs in pure Python without it; install completes
either way; and every router run prints which core is active (`route.py
rules` and the netgroup log's first line), so a slow run is never a mystery.
No prebuilt binaries in the repo. **Evidence:** 18 s per iteration is the
inner loop; a compiled A* over the same arrays is typically 20–100× faster.
**Size:** 1–2 weeks. **Gate:** decided — now tool.

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

*2026-11-10: the two model bullets above were tried and dropped. The page
index is the text layer (the same pages as ColQwen2.5, in a second, no
model); the land pattern is read by Claude from the page the index names,
because the local Qwen2.5-VL got a pitch wrong and returned 14-digit pad
counts. Only Docling stays.*

`publish.py` copies and hashes the three files into `handoff/datasheets/`
beside the PDF. Consumers: S2 for symbols; `fp.py verify --against` to
compare the footprint with `land.json` and show only mismatches; the
schematic-reviewer's checklist items 3 and 4 as a diff; the page ranges a
session reads. **Validate first:** run it over the datasheets already filed
and diff `pins.csv` against the handoff's hand-made PINOUT CSVs — that corpus
is the ground truth; keep the parser that matches. Licences: MIT and Apache
2.0 only (Docling, Qwen VL, ColQwen, PaddleOCR-VL); GPL and AGPL parsers
(Surya, Marker, Ultralytics) stay out, as Konnect does. `SETUP.md`: an
optional GPU. Decided 2026-11-09, all four parts: (a) the optional ML venv
on the concept side, install completing without it; (b) all three models,
installed in the order Docling, ColQwen2.5, Qwen2.5-VL-7B, served locally
through Ollama (vLLM for batches), GPU recommended not required; (c) MIT
and Apache 2.0 licences only; (d) outputs beside the PDF in the vendor
store, indexed by `datasheet.py`, published and hashed with the PDF — and
no consumer reads them until the extractor's pin tables have been diffed
against the hand-made PINOUT CSVs already published; `land.json` is
candidates, never authority. **Done when:** that diff matches on the
datasheets already filed, and each consumer (S2, `fp.py verify --against`,
the reviewer checklist, the page ranges) is wired in only after its input
has passed. **Size:** 1–2 weeks. **Gate:** decided — now tool.

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

## Phase 4 — from the sessions' own time

Phases 0–3 were planned from the tools' logs. Phase 4 was planned from the
sessions' transcripts on a real project (two boards, one with a 486-ball BGA),
read with what became M1: where the wall-clock went was not where the logs
said. The largest items were not compute at all — a permission prompt nobody
saw (W1), a wait loop that could not end (RB), a router that ran 433
iterations to say "no solution" (RA), a finding found twice (V5). Each row of
the Progress table above is one item; this section holds what a row cannot.

**The measured question: would Rust or threads make the tools faster?** Each
tool was profiled on M2's fixture before anything was rewritten. Every hot
spot was an algorithm, not an interpreter: all-pairs loops over labels, pads
and silk strokes; a max-flow solved again from nothing for each layer tried.
Replacing them (a bucket grid, an incremental flow, a reach filter) gave
4–80× in plain Python with byte-identical results, and left each tool at or
near its floor — loading the board in pcbnew (0.4–0.6 s) and kicad-cli's own
DRC. What is left for native code is what already has it: the router's core
(`_maze.c`, R4). Parallelism pays at the process level, where the work is
independent: `race.py` (R10), `--seeds` (R5), the snapshot's gates (V4).

**Trials — one measurement each, built only if the number says so.** None is
built; M1 is the instrument.

- **V8 · A PCB review split by concern**: three reviewers on one snapshot,
  merged here. Keep if wall-clock halves and no verified finding is lost;
  tokens rise 50–100 %. Run after V6 has been measured on five reviews — it
  may make this unnecessary. **Gate:** user (token cost).
- **A1 · An apply lock, then forked planners for independent groups**: only
  the main conversation writes the board (a hook on `agent_id`); forks return
  plans checked on scratch copies. Try only if V7's `Meanwhile:` lines show
  independent groups exist in practice. **Gate:** user.
- **P3 · Placement seeds ranked by a cheap routing result**: `place.py auto
  --seeds N`, each scored by `prelayout.py` and `escape_bound.py`. Possible
  now that `auto` is seconds (P2). **Gate:** tool.
- **R11 · Replay the applied plans after a re-placement**: `route.py replay`
  re-checks and re-applies each applied plan in manifest order and stops at
  the first that fails; RA's origin record is its first half. **Gate:** tool.

## Not doing, and why

- **Rewriting the tools in another language.** pcbnew pins the interpreter;
  the time is in kicad-cli, which is already C++, and in one Python loop
  (R4). Measured again in Phase 4 on a 326-part board: the remaining hot
  spots were algorithms, and fixing them in Python left nothing for a
  rewrite to take.
- **Python threads for CPU work.** The GIL; threads only for waiting on
  child processes (V4, R6).
- **Subagents per schematic sheet, per footprint or per placement group.**
  The tool is seconds (`sch.py plan` 3.7 s, `draw` 3.4 s for a whole board);
  a fresh agent's start-up costs more than the part, and placement groups are
  coupled through courtyards and the fanout.
- **Two writers on one board, in any form.** One file, pcbnew on a closed
  board: plan in parallel, apply serially (A1), is the only shape.
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

`tests/agentic_test.py` is the harness: it runs the handoff publish, the
schematic plan with its proof, the negotiated router on a fixed synthetic
problem, the BGA fanout planner and the review snapshot on a scratch copy of
the repository, times each stage and writes `out/agentic/report.md`. Run it
before and after an item lands; the router stage's result hash must stay the
same for every change that claims identical output (R2, R4, R5). Its
baseline on the day the plan was written, without KiCad on the machine:

| stage | result |
|---|---|
| handoff: new board, fixture design, publish, check | 0.6 s; netlist.json complete, 15 parts |
| schematic: plan, preview, structural proof and check | 0.1 s; 53 Konnect calls replaced |
| router: 16 nets, 115 × 58 cells × 2 layers, converged | 14 iterations, 94 s, 6.7 s per iteration |
| fanout: 84-ball 0.5 mm BGA, plan and layers | 0.2 s; every inner-ring signal has a via |
| pcb (added with Phase 0, KiCad 10.0.7): fixture board, netgroup, check ×2, audit, snapshot | netgroup 3 iterations 31 s; check 2.1 s then 1.6 s; audit 0.3 s; snapshot 4.6 s serial, 2.6 s parallel |

The per-board metrics the harness cannot take, from a real board's records:

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
with the item id (Phase 4: one pull request, a commit per change). An item gated `user` opens the PR description with the
decision it needs. This repository is the template, so nothing here goes
through `giveback.py`; a project takes the items with `giveback.py merge`.
