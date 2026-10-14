# Routing plan — {{BOARD}}

Yours. Group by group. **One group is routed, gated and rendered; then it
STOPS for review (`REVIEW.md`). The next group does not start until the
verdict in `handoff/15_reviews.md` is PASS.**
This file is the state: the table at the bottom says where the board is,
across sessions. Read it first in any routing session.

The method is the `routing` skill; the tools are `tools/route/` (README there).

---

## 1. The rules every group is held to

**The rules are in `general_routing_guide.md`, beside this file**: joints,
polygons, copper shape, via links and their gap, pours, the edge margin, each
from a handoff line. Its table is what `route.py check` and `route.py audit`
enforce; `route.py rules` prints what is active. This section only adds what
is specific to this board's layers and classes.

One row per copper layer of the stackup in `14_fab.md` (two for a 2-layer
board, plus `In1.Cu`, `In2.Cu`, … for more):

| layer | role | what may go on it |
|---|---|---|
| F.Cu | | |
| B.Cu | | |

| class | track | clearance | via | rule that bites (source) |
|---|---|---|---|---|
| Default | | | | |

- KiCad enforces the **stricter** of two nets' clearances; the DRC gate makes
  that KiCad's arithmetic, not ours.

---

## 2. How each group is done

1. **Read** the group's rules: its `10_layout.md` items, its `02_nets.md`
   entries, its class row above.
2. **Plan in a file** — `routing/Rn_<name>.json`: polylines, vias, zones. In
   the repo, so it survives a session and can be re-gated.
3. **Gate on a scratch copy**: `route.py check`. KiCad's own DRC (parity on)
   judges the plan; any violation not in the baseline fails it; then every rule
   of the guide: a new copper-shape site or a new via-link site fails it too.
4. **Apply** (KiCad closed, backup first, read back): `route.py apply`, then
   `route.py audit`. The open count must drop by what the plan claims, nothing
   outside the group may change, and the audit's counts go into the report.
5. **Render** (`route.py render`) and write a short report
   (`routing/Rn_REPORT.md`): nets, lengths, vias, every neck-down, every
   deviation, every part moved.
6. **Stop for review**: request it in `REVIEW.md` and ring concept. A CHANGES
   verdict is cheap: fix and request again, or `route.py rip Rn` removes
   exactly that group's copper.

**Moving parts:** if a group cannot be routed as placed, say so and propose the
smallest move that serves a rule; whether that needs the user's go first is
the user's call — record it under §4.

---

## 3. The groups, in order

Order = criticality first (the most sensitive nets get first pick of the
copper), then dependency (everything that escapes a fine-pitch part waits on
its fanout), then bulk, then pours and stitching, then close-out.

### R0 — Tooling and studies *(no copper)*

### R1 — <the most sensitive group>

### Rn — Pours and stitching

### Rn+1 — Close-out (labels, full DRC with parity, fab package — the order is the user's)

---

## 4. Decisions

| # | decision | state |
|---|---|---|
| 1 | | |

---

## 5. Status

| group | state | open before → after | reviewed |
|---|---|---|---|
| R0 | not started | | |
