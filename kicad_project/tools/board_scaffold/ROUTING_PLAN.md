# Routing plan — {{BOARD}}

Yours. Group by group. **One group is routed, gated and rendered; then it
STOPS for the user's review. The next group does not start until they say so.**
This file is the state: the table at the bottom says where the board is,
across sessions. Read it first in any routing session.

The method is the `routing` skill; the tools are `tools/route/` (README there).

---

## 1. The rules every group is held to

Transcribed from `handoff/14_fab.md` (stackup, classes, edge) and
`handoff/10_layout.md` (numbered items) — cite the line for each. The ones DRC
cannot express are gates in `routing/gates.json`, not good intentions;
`tools/kpy tools/route/route.py gates` prints what is active.

One row per copper layer of the stackup in `14_fab.md` (two for a 2-layer
board, plus `In1.Cu`, `In2.Cu`, … for more):

| layer | role | what may go on it |
|---|---|---|
| F.Cu | | |
| B.Cu | | |

| class | track | clearance | via | rule that bites (source) |
|---|---|---|---|---|
| Default | | | | |

- **Copper to the board edge:** ___ mm (`14_fab.md`: the fab's minimum plus its
  outline tolerance). `route.py edge` audits the board.
- **Corners:** ___ (e.g. every joint 0 or 45°, `10_layout.md` #__). `route.py
  angles` audits what is already laid.
- **A pour is judged by its fill, not its outline.** `tools/route/pour.py`
  before and after any group that adds or crosses a pour.
- KiCad enforces the **stricter** of two nets' clearances; the DRC gate makes
  that KiCad's arithmetic, not ours.

---

## 2. How each group is done

1. **Read** the group's rules: its `10_layout.md` items, its `02_nets.md`
   entries, its class row above.
2. **Plan in a file** — `routing/Rn_<name>.json`: polylines, vias, zones. In
   the repo, so it survives a session and can be re-gated.
3. **Gate on a scratch copy**: `route.py check`. KiCad's own DRC (parity on)
   judges the plan; any violation not in the baseline fails it; then the rule
   gates in `gates.json`.
4. **Apply** (KiCad closed, backup first, read back): `route.py apply`, then
   `place.py drc`. The open count must drop by what the plan claims, and
   nothing outside the group may change.
5. **Render** (`route.py render`) and write a short report
   (`routing/Rn_REPORT.md`): nets, lengths, vias, every neck-down, every
   deviation, every part moved.
6. **Stop.** The user reviews in KiCad. A rejection is cheap: `route.py rip Rn`
   removes exactly that group's copper.

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
