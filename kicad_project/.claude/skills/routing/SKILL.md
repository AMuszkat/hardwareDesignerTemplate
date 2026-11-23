---
name: routing
description: >
  Route a PCB one group at a time, each gated by the board's own tools, the PCB reviewed when the
  board's review mode says (release, the default: whole at the fab release; milestones: as it is
  built): plan a group in a file, gate it with KiCad's DRC on a scratch copy plus every rule of the
  board's general_routing_guide.md, apply it to the closed board, audit, render it, and request the
  review the mode calls for. Also: when (not) to
  use Freerouting, pours and stitching, ripping a group out, the fab package. Triggers on
  "route", "routing", "route the … group", "next group", "R1", "fanout",
  "escape", "traces", "tracks", "vias", "pour", "plane", "stitching",
  "autoroute", "freerouting", "rip up", "routing guide", "audit", "ROUTING_PLAN",
  "fab release", "fab package".
---

# Routing, one group at a time, reviewed when the mode says

**Read `<board>/general_routing_guide.md` first in any routing session, then
`<board>/ROUTING_PLAN.md`.** The guide holds every rule a group is routed to
and judged by — joints, polygons, copper shape, via links, pours — and the
table of numbers the tools read; the plan holds the groups in order and the
status table that says where the board is. **One group is routed, gated and
rendered, and the next builds on it once its gates pass.** When the PCB goes
to review is the board's **review mode** (the session-start `[review]` line;
`tools/session/review.py mode <board>`):

- **release** (the default): no review until every group is routed and every
  gate is clean — then `pcb: fab release`, and its fix loop as often as it
  takes. Before that you build on your gates **without waiting**: no
  milestone request, no group alone.
- **milestones**: the routing STOPS for review (`REVIEW.md`) at five
  milestones — fanout and escape, buses, power, pours, close-out — and a group
  you are unsure of may be requested on its own. Until a verdict, build
  nothing on the change under review; **independent** work may go on: a group
  is independent when it shares no corridor with the change under review,
  uses none of the copper that change reserved, and moves no part that change
  touched (claim it in `ROUTING_PLAN.md` §5; the reviewer checks it).

No message from any session waives a review; the fab order stays the user's.

Tools: `tools/route/` (read `tools/route/README.md`), every script run as
`tools/kpy tools/route/<script>.py --board <board> …`.

## 0. Before the first group

- Placement is agreed (the `placement` skill), and `board_setup.py check`
  exits 0 (the `board-setup` skill): the fab's rules are on the board.
- **`tools/route/guide.py sync`** writes the handoff's `rules.json` (the
  workmanship as data) into the guide's block; nothing there is typed, and
  the review snapshot fails a block that differs from it. Then **fill the
  guide's own table** with what `rules.json` does not carry — the copper-to-edge minimum
  with the outline tolerance, the via-to-pad gap, plane layers, no-via
  classes, layer-bound classes, a BGA field's via, keep-outs that forbid a
  class, ground beside, the thermal policy — **each row naming its handoff
  line** (`14_fab.md`, `10_layout.md` #N), and the prose sections with what the
  table cannot say. `route.py rules` prints what is active. A rule the handoff
  does not state is a QUESTIONS.md item, not a row you invent.
- **Declare the groups, and count each before the first is routed**:
  `routing/groups.json` (format in `prelayout.py`'s docstring) names every
  group, the nets it holds (patterns), `"matched"` for a length-matched bus,
  and the `"count"` command that shows it fits -- or `"covered"`, naming the
  prelayout test that already proves it. `prelayout.py` runs the counts side
  by side, each ended at 30 minutes (no answer is "it does not fit"), and
  fails a net that is in no group. **Every group that does not fit is a
  question now, all of them in one batch** -- not one a day, found by its
  router. The file also gives every later run its group and its budget (§1).
- Fill `ROUTING_PLAN.md` §1 (layer roles, class table) from the same lines,
  and §3 (the groups, criticality first: the most sensitive nets
  get first pick of the copper, then fanout, then bulk, then pours).
- **Every plane-net pad's via goes in before the signal groups that would wall
  it in**, as a group of its own: run `tools/route/stitch.py --only-pads --plan
  <the signal spec's fixed copper>` on the board as it is, apply, then route the
  signals round it. The pours and the ring stitching (`stitch.py --pitch`) come
  after. board2 routed its 20 signals first, and eight GND pads had no via site
  left. Two had none even against the power group's rail and a pair, so part of
  that rail had to move to B.Cu. Then the signals were re-routed.
- **Count the order before you count the lanes.** For each pair of
  connectors a bundle joins, list its nets by the x (or y) where they leave
  the source and where they must arrive, and count the inversions. In a
  corridor that has only ONE free layer (the other a pour or a plane) the order
  cannot change: every inversion is a via pair somewhere with both layers
  free, and a fine-pitch fan-out eats that room first. Two connectors whose
  pin 1s sit at opposite ends reverse every fixed-function block. Do it before
  the first pour is laid over the corridor -- board2's +5V pour (R4) took the
  only two-layer room between a 0.4 mm receptacle and a 2.54 mm header whose
  maps run in opposite directions; 48 inversions surfaced at R5 (board2 Q7).
- **A group is "ready to route" only once its pads have via sites, not once its bbox is free.**
  An inventory (open nets, clustered by where their pads sit) shows where a group lies and that no other
  group's box overlaps it. It does not show whether each pad that must change layer has a via site on one of
  its axes or 45s, or whether the lanes between a part's pin row and its strap row hold the tracks they must.
  Before a group goes out as ready, survey each such pad's sites and each corridor's lane count on the board as
  it is. A pad with no site, or a corridor with fewer lanes than nets, is a placement count to settle first.
  board1's PHY group was dispatched as ready from a free bbox. It had 0 of 25 joins: the strap pads under
  U200's pin row had no via site, and two 0.5-0.6 mm corridors each had one lane for three to five needs.
- **Count a fine-pitch row's via sites before planning anything beyond it.**
  A row of 0.4 mm pads that drops to vias (a board-to-board receptacle) has a
  strip between its pads' outer ends and the next obstacle:
  - Start: pad end + the via-to-pad gap + the via radius.
  - End: an edge band or a cut-out.

  At 0.3 / 0.6 vias (0.8 mm centre to centre), two rows staggered about 0.55 mm
  apart hold one via per 0.6 mm. That only works where each link moves less than
  about 0.6 mm sideways: a link shifted further passes under its neighbour's
  via, which must then take the lower row, and the run falls back to 0.8 mm.

  So a run of used pins (signals AND ground) longer than the 0.6 mm staircase
  can absorb has no fanout.
  - board2: 11 used of 12 pins in 4.4 mm, 2.1 mm of strip before the cut-out.
    One GND contact was left open (Q8, D59), and a 0.2 / 0.5 via did not free
    it either.
  - Measure it with a two-ended search: pack the west half west and the east
    half east, then test the two halves together. A single left-to-right search
    stalls on its own early choices and reads as infeasible when it is not.
  - Ask at placement, not at R5.

## 1. One group

**Every group has an effort budget, and the tools count it.** Its tier is
read from its nets -- A (up to 12 nets, no BGA ball, no pair): 2 rounds,
45 min; B (up to 40 nets, a pair, up to 12 balls): 3 rounds, 2 h; C (an
escape of over 12 balls, a matched bus, over 40 nets): 4 rounds, 6 h -- and
one feasibility count of 30 minutes for any group. A round is one router run
with its checks (three), or one race of up to six variants; runs of one group
in flight together are one round. `netgroup.py`, `race.py`, `route.py check`
and `bgwait.py run --group` count by themselves, on scratch copies too, and
the group is its NETS: a new spec name or a new seed is the same budget.
`route.py budget` shows where every group stands; read it at the start of a
turn and before a worker is briefed.

- **Before the first run, say what you expect to block it** and what you
  would change if it does. A run that confirms it is the last run: declare
  the wall then, with rounds in hand.
- **`BUDGET SPENT` (exit 76) means the design has to change, not the spec.**
  The one command left is `route.py wall <group> --blocks "…" --change
  "placement: …" [--change "rule: …"] [--change "swap: …"] [--change
  "layer: …"]`: it writes the question from the ledger -- every round, how
  it ended, the changes cheapest first -- and prints the ring. Ring it (or
  tell the user, relay off) and **take the next group**; the tools refuse
  this one until a handoff revision answers that question or its parts move.
  A second wall of the same group is a design problem and says so: concept
  answers it alone only when no change you offer adds a term to the design.
  **A run that ended before its first negotiation costs no round** (BLOCKED
  at leg 1, a spec the set-up refused; three an epoch, the time counts), and
  the router searches once without `--limit` before it calls a net walled --
  so never offer "one more round" or "a higher limit" as a change: the wall
  is about the design. Its `Layers:` line lists the layers no round opened:
  for each, name the handoff row that closes it, or count it first.
- **A job for a group carries `--group`** (`bgwait.py run --name N --group
  routing/X_spec.json -- …`), a what-if count `--group X --count` (one per
  placement, ended at 30 minutes). A count that does not answer in that
  time has answered.
- **A worker gets the group's budget, not its own**: `route.py budget
  <group> --worker "<its task>"` prints the lines for its brief -- what is
  left, and that it stops at the first `BUDGET SPENT` and hands back what
  blocks. Never brief a second worker on a group whose first came back
  without copper: that is a wall.
- **Copper before counts.** A group that is ready (its pads have via sites,
  §0) is routed and applied before another group's what-if is started; the
  day's measure is nets applied (`route.py budget`, "on copper"), not runs.

1. **Read** the group's rules: the guide's sections that touch it, its
   `10_layout.md` items, its `02_nets.md` entries, its class rows. A bus head,
   a star, a taper is a net-tied polygon (`polys`), not a stack of tracks; a
   via is linked straight and centred, off the pad by the gap. **A power net's
   `02_nets` constraint ("a pour, sized for N A") and the layout's pours item
   decide pour or track, and which side, before any geometry** (board2 R4 laid
   a 35 mm 1.0 mm B.Cu trunk for a net the handoff made a top-side pour, and
   re-laid it). **Draw the window first** (`view.py`, outline and cut-outs
   included) and route every group's nets in your head to their far ends: a
   group that takes a corridor another group needs is re-laid later.
2. **Plan in a file**: `<board>/routing/Rn_<name>.json` (polylines, vias,
   zones, necks — format in the README). Draw it by hand, with `decap.py` for a
   decoupling ring, or with `netgroup.py` from a spec for a congested signal
   group (bound a long run with its own `--timeout S`, never a `timeout`
   prefix: it stops between iterations and every iteration leaves
   `<plan>.partial.json`). It survives the session and can be re-gated.
   **A run of minutes is started and waited on with `tools/bgwait.py`**
   (`run --name N -- <command>`, then `wait N --timeout 100`): by pid, with a
   time limit, and every return shows the exit code, the CPU and the log's
   tail. Never a `while pgrep …; do sleep` loop — it matches its own shell and
   never ends (a hook refuses it). Variants of one spec that do not depend on
   each other (a layer, a window, a seed) are one `race.py` call, not three
   runs.

   **Use the machine, and check that you did.** What-if counts that do not
   depend on each other start TOGETHER (`race.py`, or `par.cmap` in your own
   script), not two or three at a time. A loop that solves, checks and cuts
   gives its solve `hint` and `stall` (`cpsat_milp`), runs its checks at once
   and finds its smallest failing set with `tools/minset.py` — the README's
   "A loop that solves, checks and cuts". `bgwait.py cores --since <today>`
   at the end of a routing day says how many cores were busy and which
   script left the rest idle; a script it names is a retro row.

   **Waiting on a long job.** Start every job with `bgwait.py run --name N
   -- <command>`, then wait on ALL of them with ONE call, `bgwait.py wait A B
   C --timeout 3600`, run as a **background** Bash command
   (`run_in_background`): it returns when they have ended, and the harness
   wakes the session then. Never `sleep N; check` — a sleep is sized before
   the job's end is known, so it runs on after a job that ended early, and
   every check that answers "still running" costs a whole turn at the
   session's full context (a hook refuses a foreground sleep of 60 s or more).
   Meanwhile work on what does not depend on the job; with nothing else to
   do, end the turn and let the wait's return start the next one.

   **A run that fails says why, and the exit code is the next move:**
   `4 BLOCKED` — a net is shut in at one end: move the nearest blocker it
   lists; `3 STUCK` — the named nets need the same channel at the cells
   printed: another layer, a moved via, a wider window. Neither is answered
   with more `--iters` or another seed. A failed run leaves no plan at its
   path (the older one is `<plan>.stale.json`).
3. **Gate**: `route.py check`. KiCad's own DRC on a scratch copy with schematic
   parity on — any violation not in the baseline fails — then every rule of
   the guide: joints, edge, class width, planes, via caps, fields, keep-outs,
   and the copper rules on the scratch copy (a new shape site or a new via-link
   site fails; ground beside is reported before → after). Iterate until
   `CHECK PASS`. Every neck, `edge_ok` and `accept` carries a reason the user
   will read. `check` and `apply` exit 5 on a **stale plan** — the spec or the
   board changed since it was routed: run `netgroup.py` again;
   `--accept-stale` only on purpose, with the reason in the report.
   **`check` does not run the findings.** Before the real board, apply the
   plan to the scratch copy and run `route.py audit --findings --board
   <scratch>` there: a closed finding the group re-opens fails on the copy,
   not after the real apply (board1 R1: a re-laid U500 field passed `check`
   and put three SE_50 vias 1.30-1.60 mm from GND, re-opening RV22.1's
   1.27 mm stitch on the real board).
4. **Apply** with KiCad closed: `route.py apply` (backup, write with `pcbnew`,
   read back, UUID manifest). **When the real apply is more than one step**
   (strips, placement moves, a second group, part vias, then the plan), write
   it as ONE script that runs the same steps in the same order as the scratch
   job that proved them, never as a prose list in `STATE.md`. The script
   checks the board's sha first, backs it up, and on any failed step restores
   the backup, sets aside the `routing/*.applied.json` files the run wrote
   (the `tools/route` README, `apply` and `rip`), and exits with that step's
   code. Each step's own exit is read, not a pipe's (`${PIPESTATUS[0]}`).
   Scratch-only helpers (`assert '/kicad_project/' not in path`) get a
   read-only check in their place for the real board. TRAP (board1 R_EMMC,
   2026-11-23): the prose order in a worker's STATE.md applied nwp2 before
   the strips its check had run on, so a track crossed a cap's old via. The
   script then failed twice, both times restored cleanly, and the third run
   applied all of it.
   Then **`route.py audit`**: DRC, joints, edge,
   shape, via links, ground beside, pour islands, in one `AUDIT PASS|FAIL`
   line. The open count must drop by what the plan claims, and nothing outside
   the group may change. For any pour: `pour.py` — islands, vias the fill
   misses, what lies under it. What the audit still lists is polished with
   the `copper-shape` and `via-links` skills, as a group of its own. Then
   **`route.py audit --findings`**: every check a review finding carries
   (`handoff/findings.json`), closed ones too. `FINDINGS FAIL` names the
   findings that are open on the board again — fixed now, never sent to a
   reviewer to be found a second time.
5. **Render** (`route.py render`) and write `routing/Rn_REPORT.md`: nets,
   lengths, vias, the audit line before → after, every neck-down, every
   deviation, every shape or via-link site left and why, every part moved,
   every group re-laid with it.
6. **Update `ROUTING_PLAN.md` §5, then the review the mode calls for.**
   **release**: none for a group — the next group starts in the same turn.
   **milestones**: when the group completes a milestone (fanout and escape,
   buses, power, pours, close-out) — or it is one you want reviewed alone —
   **STOP for review**: write the request in `REVIEW.md` and ring concept
   (`boardN RVn ready — pcb: <title>`; relay off: tell the user that line)
   — Scope: the milestone's groups (`--scope` takes a group id), their
   reports `routing/Rn_REPORT.md`; Gates: the `AUDIT` and `FINDINGS` lines.
   **A request AS A PLAN** (rehearsed on a scratch copy, applied only after
   the verdict) files that scratch board, every step applied, in the board
   folder -- `tmpWork/<group>/r_final.kicad_pcb.scratch` -- and names the
   path in the request: the reviewer measures the plan on it (`snapshot.py
   --plan`). A scratch board left in a temporary folder cannot be reviewed,
   and the request waits on it (board1's RV56).
   **Ring, then start the request's `Meanwhile:` group in the same turn** — a
   review is not a wait. A group started while one is under review
   names in §5 why it is independent. Where nothing is independent, plan the
   next group instead: its spec, its window, its plan routed and checked on a
   scratch copy — nothing applied until the verdict. A CHANGES verdict is
   cheap: fix and request again, or `route.py rip Rn` removes exactly that
   group's copper.
7. **The fab release, in both modes.** After the last group, with every
   group's `AUDIT` and `FINDINGS` lines clean, `place.py drc` clean and
   `ROUTING_PLAN.md` §5 complete, request `pcb: fab release` (Scope: all;
   the reports of every group; the gates' lines). It passes only with no open
   finding. A CHANGES verdict is fixed — or disputed under **Replies** — and
   requested again as `pcb: fab release, fixes for RVn` (that exact form:
   the fab package and the snapshot diff read it), as many rounds as it
   takes; a round that closes no must-fix finding waits on the user. Every
   finding a number decides comes with its check, so `route.py audit
   --findings` proves the fix before you ring. On the PASS,
   `tools/kpy tools/fab/fabpkg.py` writes the package into `<board>/fab/`:
   Gerbers, drills, BOM and placement, and ORDER_NOTES quoted from 14_fab. It
   refuses an unreleased board and checks what it wrote. Tell the user where
   it is; the order is theirs.

## The rules that are easy to get wrong

- **The gate is KiCad's DRC, never arithmetic of yours.** KiCad enforces the
  STRICTER of two nets' clearances; a hand check using the track's own class
  passes copper DRC fails.
- **`place.py drc`, never bare `kicad-cli pcb drc`.** The bare command skips the
  schematic-parity test unless told, and an empty parity list reads exactly
  like a pass — pads with no net look "fine" and are simply never routed.
- **Tracks are written with `pcbnew` on the CLOSED board**, backup first, read
  back afterwards (`route.py apply`), the mechanism `place.py apply` uses for
  footprints. Konnect's `route_*` tools (one IPC call per segment, KiCad open)
  are for a one-off fix, not for a group.
- **A short can hide as a rename.** Saving a board gives copper that touches
  another net's pad that pad's net. `check` reads the scratch copy back and
  fails the plan with `RULE short:`; never work round it with `accept`.
- **A rule is run only if its row is in the guide's table.** `joints` left out
  makes `route.py angles` a report that exits 0; `copper shape` and `via to
  pad gap` left out mean `check` and `audit` never look. A row the handoff
  gives is never deleted to make a plan pass.
- **A pour is judged by its fill.** DRC passes a pour in pieces and a via in a
  pocket of its own. Set each zone's clearance (its class's, never KiCad's
  0.5 mm default) and `min_width` (the fab's minimum inside a BGA field).
- **A group that closes is not a group that is done** — ask what the next group
  is left with (via sites for the caps still to come, a channel the bus needs).
  Reserve a later group's copper in a `netgroup.py` spec with `reserve_*`.
- **Moving parts** to make a group routable goes through `place.py check`, one
  Konnect call, is named in the report, and is reviewed with the group (in
  release mode, at the fab release). Whether
  it needs the user's go first is their call — ask once and record it in
  `ROUTING_PLAN.md` §4.
- **A rule that blocks the layout is a QUESTIONS.md item.** Never loosen a fab
  number or a gate to make a route fit; never tighten one on a guess.
- **A bus between two parts is proved whole on a scratch copy before any of it
  goes to copper** — one end's fanout, the other end's escape, the channel's
  vias at their real clearances, each to a count. A cheap variant first (no
  order, one net alone, one half) names the bind in seconds; independent
  chains run on separate copies at once; a solve's input names the board's
  hash. `tools/route/README.md`, "A bus between two BGAs".
- **A counting model proves itself before it spends a round.** A capacity
  model of your own (a cut count, a lane count, a conflict rule) runs a
  selftest first and stops the job when it reads otherwise: one case counted
  by hand that must FAIL with the hand's number, one that must PASS (copper
  already drawn and audited). Cuts are counted between anchored pairs along
  a path, never as vertical lines at fixed x with no continuity between them
  -- that model passed a row which was short 10, and three rounds in a row
  were lost to the model, not the board (board1 Q105, Q106, Q110).
  A cut is one x, and a part whose two pads stand either side of it, or a
  via just off it, reads as room: count each cut at x and 0.4 mm either
  side and take the least (board1 Q113: F at x 37.05 read 0 short, 1 short
  at 36.65).
- **Routing is headless**: `route.py apply`, `rip`, `audit` and the copper
  tools run on the closed board. Close KiCad yourself only with the standing
  permission (`kicad_project/project.json` `close_kicad`, printed in the
  session-start brief), your own project only: Konnect `save_project`,
  SIGTERM, confirm `pgrep -x kicad` is empty, then clear the `~*.lck` files.
  A project the user had open is reopened afterwards (`launch_kicad_ui`), and
  you say so.

## Freerouting: available, not the default

The autorouter is a local MCP server declared in `kicad_project/.mcp.json` and
set up by `tools/setup.sh` (Java 25). It holds no account and no API key, and
both listeners are pinned to loopback — board geometry never leaves the
machine. `tools/freerouting/README.md` is the whole story. The round trip is
headless: `pcbnew.ExportSpecctraDSN` / `pcbnew.ImportSpecctraSES` (via
`tools/kpy`); neither `kicad-cli` nor Konnect exports a `.dsn`.

**Why it is not the default:**

- it routes the whole `.dsn` in one pass — no net filter, no area filter — so it
  cannot serve group-by-group routing, its gates and its rip;
- KiCad's Specctra export loses custom `.kicad_dru` rules (a no-via class),
  area-scoped net-class vias (a BGA field's small via: the `.dsn` carries one
  via for everything), and plane layers (exported as signal layers, so the
  router slots a ground plane — retyping the layer `(type power)` was honoured);
- `(type protect)` does not preserve existing copper: accepted groups come
  back re-routed;
- router settings through the MCP may not apply — read `router_settings` back
  from `get_job_details`; board-edge clearance is not in the `.dsn` at all;
- `cancel_job` DISCARDS the output: download the `.ses` first.

Use it on a scratch copy for a feasibility answer ("can this placement route at
all?"), never as copper to accept. Do not disable its MCP HTTP endpoint to
"harden" it: the stdio transport is a bridge to that endpoint, and the
handshake then returns nothing at all.
