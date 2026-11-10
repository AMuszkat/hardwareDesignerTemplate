---
name: review-board
description: >
  Review a board's schematic or PCB after a change: freeze it, run its gates,
  spawn the schematic-reviewer or pcb-reviewer agent, verify and triage the
  findings, publish the verdict in the handoff (15_reviews.md), and ring the
  board (relay on) or tell the user (relay off). Triggers on "boardN RVn
  ready", "review the schematic", "review the PCB", "review board", "REVIEW.md",
  a board session's review ring, "is the board right".
---

# A board change, reviewed

Every schematic change and every PCB change (a placement group, a routing
group, the fixes for a review) is reviewed before the board session builds on
it — by the concept session's reviewer agents, not by waiting for the user.
The board asks in its `REVIEW.md`; the answer is a verdict in its handoff.
The user can still review anything at any time, and still owns decisions and
the fab order (`kicad_project/RELAY_PROTOCOL.md`, "Reviews").

## 1. What is asked

- **A ring** `boardN RVn ready — schematic|pcb: <title>`, or the user saying
  so: read `kicad_project/boardN_*/REVIEW.md`, entry `RVn` — kind, scope,
  the gates it ran, waivers added.
- **The user asks for a review with no request** ("review board2's PCB"):
  the id is `Mn`, the next number in `tools/handoff/reviews/boardN.md`; the
  scope is what the user said, else "all".
- `.venv/bin/python tools/handoff/publish.py --check --board boardN`: a
  stale handoff is published first — the board drew against the old one, and
  that is the first thing the reviewer must know.

## 2. Freeze and gate

    python3 tools/review/snapshot.py --board boardN --kind schematic|pcb --id RVn [--scope S]

PCB reviews come at milestones (the placement as a whole; routing's fanout
and escape, buses, power, pours, close-out), plus a single group on request.
A request about one group passes `--scope`: the routing group's id (`R3`,
its applied manifest's copper), a `placement.json` group's name, or
`refs:A,B`; the PCB views are then tiled only round it and the sheet tiles
skipped. A fab release, fixes across the board, or a request with no
group: no `--scope` (everything). Every snapshot writes `changed.md` against
the board's previous snapshot of the same kind; a PCB snapshot also writes
`CHANGES.md` / `changes.json` — the object-level diff against the last PCB
snapshot **whose id has a verdict in `tools/handoff/reviews/boardN.md`** —
and `geometry.json`, which the reviewer queries with `tools/review/geom.py`
instead of scripting. The reviewer reads `CHANGES.md` first; so record every
verdict under its request id (step 5), or the next review diffs against an
older state.

It copies the board into `out/reviews/<board>/<id>-<kind>/`, runs the board
session's own gates on the copy and writes `SUMMARY.md`. Exit 1 is not a
stop — the failures are the review's first findings. **A fix re-review is
judged from the snapshot when it can be**: the `findings` gate passes (every
finding's check holds), `CHANGES.md` shows nothing outside the findings'
objects, and no open finding lacks a check — then verify those lines
yourself and write the verdict, no agent. Anything else (a finding without
a check, a change beyond the fixes) gets the reviewer. Exit 2 (no KiCad
project, no PCB, no `kicad-cli`) is told to the board / the user as is.

## 3. Review

Spawn the agent — `schematic-reviewer` or `pcb-reviewer` — with the board,
the id, the scope, the snapshot directory, the report path
`history/reviews/<YYYY-MM-DD>-<board>-<id>-<kind>.md`, and the line "your
only scratch folder is `<snapshot>/tmpWork/`; never `/tmp` or a scratchpad"
(its environment names a scratchpad outside the project, where every read
asks the user). A request whose scope
covers both (a redraw after placement) gets both, in parallel. Wait for
it; do not review the same thing yourself meanwhile.

## 4. Verify, then triage — the reviewer's report is not the verdict

- **Every `must` finding is checked by you** against its evidence before it
  reaches the board: open the page, rerun the line (a measured one: the
`geom.py` query it quotes, on the same snapshot). A false finding sent to a
  board session costs a fix that breaks something; drop it with one line in
  the report's *Not proven* ("dropped: <why>").
- **`handoff` findings are yours**: fix the GENERATOR in the same pass (the
  `answer-question` skill, step 3), and say in the finding "fixed in rev R".
- **`decision` findings go to the user** (protocol §5): options and a
  recommendation in the terminal, a push notification; the finding stays
  open as `waits on the user`.
- **A finding the board disputed** (its reply in `REVIEW.md`) is re-judged
  on the reply. Upheld twice → the user decides.
- **Loop breaker:** the third CHANGES verdict on the same scope stops for the
  user, whatever the findings.

## 5. Publish the verdict

Append to `tools/handoff/reviews/boardN.md`:

    ## RVn — <kind>: <scope> — PASS | CHANGES (<k> must-fix)

    Reviewed: handoff rev <R>, snapshot <time>; the full report is concept's
    record. Gates: <one line from SUMMARY.md>.

    | id | severity | where | finding | evidence | fix |
    |---|---|---|---|---|---|

**A must-fix finding a number decides carries its check**, one line under
the table for each:

    Check RVn.k: cap reach | U1: 1.27 mm (C65)
    Check RVn.k: distance | C12.1 to U1.A5 <= 1.27 mm

The rules are the routing guide's check rules (`kicad_project/tools/route/
checks.py`: `cap reach`, `layers apart`, `stitch via`, `neck length`,
`distance`); the publisher validates the line with the board's own parser
and writes them all to `handoff/findings.json`. The board runs them with
`route.py audit --findings` before it asks again, and the next snapshot runs
them as its `findings` gate — closed findings included, so the one that
comes back fails in seconds. Never delete a Check line when its finding
closes. A rule that belongs to the whole design and not to one finding goes
into the board's `WORKMANSHIP` rows instead (the same forms), so every
`route.py audit` runs it.

Only `drawing` findings and the `handoff` ones you fixed (marked "fixed in
rev R — nothing to do") go in the table, each with evidence the board can
read — a handoff page, a datasheet in `handoff/datasheets/`, a gate line —
never a repo path outside `kicad_project/`. `should` findings travel with the
verdict and stay open until a later review sees them fixed. An earlier
review's open findings that this one saw fixed are marked `closed (RVn)`.

Then set `RELEASE_NOTE` (the verdict, and which drawn work it invalidates),
publish, `publish.py --check --all`, `./verify.sh`.

## 6. Tell them

- **Relay on:** `boardN handoff rev R — review RVn: PASS|CHANGES (<k>
  must-fix); invalidates: <…>` with `notify_when_idle: true`. On the idle
  notice: a request in `REVIEW.md` still `requested`, or an unanswered
  question → handle it; none → nothing.
- **Relay off:** tell the user that line; they carry it.
- To the user, always: the verdict, the must-fix findings in one line each,
  anything waiting on them — and the turn's retro rows
  (`kicad_project/SELF_IMPROVEMENT.md`).
