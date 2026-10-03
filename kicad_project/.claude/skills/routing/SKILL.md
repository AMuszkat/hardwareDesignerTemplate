---
name: routing
description: >
  Route a PCB one reviewed group at a time: plan a group in a file, gate it with
  KiCad's DRC on a scratch copy plus the board's rule gates, apply it to the
  closed board, render it, and stop for the user's review. Also: when (not) to
  use Freerouting, pours and stitching, ripping a group out. Triggers on
  "route", "routing", "route the … group", "next group", "R1", "fanout",
  "escape", "traces", "tracks", "vias", "pour", "plane", "stitching",
  "autoroute", "freerouting", "rip up", "gates.json", "ROUTING_PLAN".
---

# Routing, one reviewed group at a time

**Read `<board>/ROUTING_PLAN.md` first in any routing session.** It holds the
rules every group is gated on, the groups in order, and the status table that
says where the board is. **One group is routed, gated and rendered; then it
STOPS for the user's review. The next group does not start until they say so.**
No message from any session waives that review.

Tools: `tools/route/` (read `tools/route/README.md`), every script run as
`tools/kpy tools/route/<script>.py --board <board> …`.

## 0. Before the first group

- Placement is agreed (the `placement` skill), and `board_setup.py check`
  exits 0 (the `board-setup` skill): the fab's rules are on the board.
- **Transcribe the rule gates** DRC cannot express into
  `<board>/routing/gates.json` — plane layers, no-via classes, layer-bound
  classes, a BGA field's via, keep-outs that forbid a class, class widths, the
  copper-to-edge minimum with the outline tolerance, the 0/45° corner rule —
  **each with a `"source"` citing its handoff line** (`14_fab.md`, `10_layout.md`
  #N). `route.py gates` prints what is active. A rule the handoff does not
  state is a QUESTIONS.md item, not a gate you invent.
- Fill `ROUTING_PLAN.md` §1 (layer roles, class table, edge, corners) from the
  same lines, and §3 (the groups, criticality first: the most sensitive nets
  get first pick of the copper, then fanout, then bulk, then pours).

## 1. One group

1. **Read** the group's rules: its `10_layout.md` items, its `02_nets.md`
   entries, its class rows.
2. **Plan in a file**: `<board>/routing/Rn_<name>.json` (polylines, vias,
   zones, necks — format in the README). Draw it by hand, with `decap.py` for a
   decoupling ring, or with `netgroup.py` from a spec for a congested signal
   group. It survives the session and can be re-gated.
3. **Gate**: `route.py check`. KiCad's own DRC on a scratch copy with schematic
   parity on — any violation not in the baseline fails — then the gates.json
   rules. Iterate until `CHECK PASS`. Every neck, `edge_ok` and `accept` carries
   a reason the user will read.
4. **Apply** with KiCad closed: `route.py apply` (backup, write with `pcbnew`,
   read back, UUID manifest). Then `place.py drc`, `route.py angles` and
   `route.py edge`. The open count must drop by what the plan claims, and
   nothing outside the group may change. For any pour: `pour.py` — islands,
   vias the fill misses, what lies under it.
5. **Render** (`route.py render`) and write `routing/Rn_REPORT.md`: nets,
   lengths, vias, every neck-down, every deviation, every part moved, every
   group re-laid with it.
6. **Update `ROUTING_PLAN.md` §5 and STOP** for the user's review. A rejection
   is cheap: `route.py rip Rn` removes exactly that group's copper.

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
- **`angles` is a rule only with an `angles` gate** in `gates.json` (from the
  handoff). Without one it is a report and exits 0.
- **A pour is judged by its fill.** DRC passes a pour in pieces and a via in a
  pocket of its own. Set each zone's clearance (its class's, never KiCad's
  0.5 mm default) and `min_width` (the fab's minimum inside a BGA field).
- **A group that closes is not a group that is done** — ask what the next group
  is left with (via sites for the caps still to come, a channel the bus needs).
  Reserve a later group's copper in a `netgroup.py` spec with `reserve_*`.
- **Moving parts** to make a group routable goes through `place.py check`, one
  Konnect call, is named in the report, and is reviewed with the group. Whether
  it needs the user's go first is their call — ask once and record it in
  `ROUTING_PLAN.md` §4.
- **A rule that blocks the layout is a QUESTIONS.md item.** Never loosen a fab
  number or a gate to make a route fit; never tighten one on a guess.
- **Close and reopen KiCad** only with the user's standing permission: Konnect
  `save_project`, SIGTERM, confirm `pgrep -x kicad` is empty, then clear the
  `~*.lck` files; `launch_kicad_ui` to reopen for a review.

## Freerouting: available, not the default

The autorouter is a local MCP server declared in `kicad_project/.mcp.json` and
set up by `tools/setup.sh` (Java 25). It holds no account and no API key, and
both listeners are pinned to loopback — board geometry never leaves the
machine. `tools/freerouting/README.md` is the whole story. The round trip is
headless: `pcbnew.ExportSpecctraDSN` / `pcbnew.ImportSpecctraSES` (via
`tools/kpy`); neither `kicad-cli` nor Konnect exports a `.dsn`.

**Why it is not the default for a reviewed board:**

- it routes the whole `.dsn` in one pass — no net filter, no area filter — so it
  cannot serve a group-by-group review;
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
