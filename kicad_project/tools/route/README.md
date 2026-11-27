# `tools/route` — routing, one gated group at a time

**Yours**, like `tools/place` and `tools/fp`. A board is routed in the groups
`<board>/ROUTING_PLAN.md` lists, each gated by these tools (`check`, `audit`,
`audit --findings`). When the PCB is reviewed is the board's **review mode**
(`../session/review.py`): **release** (the default) — whole, at `pcb: fab
release`, then its fix rounds; **milestones** — at fanout and escape, buses,
power, pours, close-out, and a group alone on request (the board's
`REVIEW.md`; the verdict in `handoff/15_reviews.md`). These tools make a group cheap to propose, judge and undo.
The workflow is the `routing` skill.

    tools/kpy tools/route/route.py status --board board1 [--nets]
    tools/kpy tools/route/route.py check  --board board1 routing/R1_power.json
    tools/kpy tools/route/route.py apply  --board board1 routing/R1_power.json   # KiCad closed
    tools/kpy tools/route/route.py rip    --board board1 R1                      # KiCad closed
    tools/kpy tools/route/route.py render --board board1 [--out DIR]
    tools/kpy tools/route/route.py angles --board board1 [--nets]
    tools/kpy tools/route/route.py edge   --board board1
    tools/kpy tools/route/route.py audit  --board board1                      # every rule of the guide, on the board
    tools/kpy tools/route/route.py rules  --board board1                      # what the guide switches on
    tools/kpy tools/route/netgroup.py --board board1 routing/R3_spec.json
    tools/kpy tools/route/decap.py    --board board1 --group R2 --refs C9,C10 --plan routing/R2_decap.json
    tools/kpy tools/route/fanout.py plan   --board board1 --ref U1 --via 0.4,0.2 --clearance C --width W ...
    tools/kpy tools/route/fanout.py layers --board board1 --ref U1 --vias placement/vias_U1.json --layers L.json
    tools/kpy tools/route/pour.py     --board board1 [--try NET:LAYER=zone.json] [--over 'GLOB:LAYER'] [--section MM --section-net NET --from .. --to ..]
    tools/kpy tools/route/stitch.py   --board board1 --plan routing/R6_pours.json --net GND --pitch 5 [--pads] --out routing/R6_gnd.json
    tools/kpy tools/route/prelayout.py --board board1 [--spec routing/prelayout.json]   # before the first group
    tools/kpy tools/route/view.py     --board board1 X0 Y0 X1 Y1 OUT [--plan plan.json] [--vias model.json] [--contested netgroup.log]

`tools/kpy` only (they import `pcbnew`). `apply` and `rip` need KiCad
**closed**; everything else reads the saved file. `--board` takes `boardN`, the
folder name or a path; left out, it is this session's board (`HW_BOARD`), and
another session's board is refused. A plan, spec or zone file is taken as
typed, else inside the board folder (`routing/R1.json`).

- **`rowfan.py` -- a via for every signal pin of a fine-pitch row, found together** (a QFN's rows, a connector's):
  at 0.4 mm pitch a 0.4 mm via fits only `gap + r` (0.30 mm) beyond its own tip, and two neighbours' vias are then
  0.4 apart -- the row fans out in two staggered rows, and which pin gets which depends on what stands beyond the tips.
  It lists each pin's legal axis (and, `--diag`, 45-degree) sites against the board and a `--plan` (planned power
  copper), then searches one per pin with every pair legal; `ROWFAN FAIL` names the pin without which the rest close.
  Run it on each row BEFORE drawing the power copper round a QFN, and again with that copper as `--plan`: board1's
  first PMIC stage drew the switch nodes sideways under seven signal pins and `route.py check` passed it (pins with no
  copper yet have nothing for DRC to see). Its candidates start EXACTLY at `gap + r` beyond the tip and step by
  `--step`: a 0.025 grid missed every site of a stagger that fits by 0.002 mm (use 0.005). A 45-degree link out of a
  0.2 mm pin leaves a wedge at the tip that the shape gate calls a notch -- a via in the pin (0.02 mm off its centre
  line where a neighbour needs it) is the way out.
- **`padvias.py` also maps** (`--map x0,y0,x1,y1 --net N`: where a via of the net may stand, in its pads or on free
  board), places vias in boxes beside a pad (`--boxes NET@box:N`), counts net-tied polygons as copper, and
  `--ignore-refs` asks what a part move would free. A via on the bottom of a 0.4 mm board-to-board connector's span
  is only legal between its two pad rows: map before choosing a pin's via.
- **`sitesolve.py` -- how many nets of a set can drop a via in a region AT ONCE: an exact count, not a capped
  search.** TRAP (board1 NORTHGAP r10, 2026-11-24): a depth-first site search capped at 400k nodes stopped with
  "no set", and that was read as a wall; it found 3 of 8 nets where the exact count found 4 of 8 (and proved 8 of 8
  infeasible). A capped search cannot prove "no set". `tools/kpy tools/route/sitesolve.py gen --board COPY
  SPEC.json CANDS.json.gz [--pull NAME:D] [--drop NAME] [--line NAME]` lists every site on the spec's grid (each
  net's ends -- pad or stubs -- links straight or straight + one 45, the board's class clearances and hole rules or a
  flat model clearance, the exit lanes each item shuts by exitlane.py's own judge, GND stitch sites in the pour,
  every pairwise conflict); `tools/.venv/bin/python tools/route/sitesolve.py solve CANDS OUT.json [--free B,..]
  [--freeany] [--gnd new|old|off] [--need N]` is CP-SAT under the board's exit gate: OPTIMAL, INFEASIBLE, or
  FEASIBLE-at-limit (said so). `table --board COPY SPEC ROWS.json --out DIR` runs a what-if table in parallel
  (`table.tsv`: terms | sited/N | failing nets | cost). The spec format is the tool's docstring. PROOF:
  `tools/kpy tools/route/test_sitesolve.py` (a synthetic board counted by hand: 1 of 6, 2 of 6 with a polygon
  pulled, INFEASIBLE when N > slots; `--board-test` adds board1's identity with the r10c count). Its pair index
  found 1557 conflicts the r10c script's index had missed (cells inflated by the via radius only): the counts held,
  but a candidate pair is queried at 2 x via radius + clearance.
- **The shape gate judges a net on a layer only once it has a track there** (`shape.py`: 'a net with no track on this
  layer has only pads'). A plan that gives a net its FIRST track on a layer can be failed for a notch that was there
  before (board1: a ring capacitor's unlinked power via 0.10 mm from its pad, shown when C63's link went in). Fix the
  old site in the same plan (link it), do not route round the gate with a polygon.
- **`orphans.py` -- the copper a placement plan orphans, named before the plan is applied** (`tools/kpy
  tools/route/orphans.py --board boardN PLACE.json [--out RIPS.json] [--merge PLAN.json OUT.json]`, read-only, about a second):
  `place.py check` judges the parts and `route.py check` the plan's own copper, so neither sees the link and the via a
  moved capacitor leaves behind, joined to no pad. board1's D116 moved 48 parts with their new copper in one plan, both
  checks passed, and only a rehearsal apply showed four capacitors' old copper still there (4 GND segments on B.Cu and
  3 vias; track_dangling 6 -> 8) -- the handoff's rule there: 'copper a move orphans is ripped by its group and laid again'. The tool
  moves the plan's moving parts in memory (x, y on the nanometre, rotation, side; a part listed at its own place does
  not move), reads KiCad's connectivity before and after, and lists each track and via of a cluster that lost a pad
  and touches none now (`whole cluster`), or that hangs as a dead end between the lost pad and the first junction or
  the first item still on a pad (`stub`); one line an item with its net, layer, ends, width, the group whose
  `*.applied.json` holds its UUID and the pad it hung on, a via in a zone of its net marked `in_zone`. `--merge` writes
  the group's route plan with them as `rips` / `ripvias` (each with its `why`; an item the plan already rips is not
  added twice). What it prints as `shared -- decide by hand` is yours: the last track to a pin that stays (it will
  dangle), a blob of short fat links joined along their bodies. It never looks through a zone. Exit 0 none, 1 listed.
  Prove it: a plan holding only parts at their own place gives 0 items and exit 0; after `place.py apply` on a scratch
  copy, `route.py check` of the merged plan says `removes N segments, M vias` and `CHECK PASS` (a rip that matches
  nothing fails the plan), and track_dangling does not rise.
  **The trap: a via that never touched its pad has no pad to lose.** A capacitor's power via set beside the pad at the
  via-to-pad gap, with no link, waiting for a pour (board1: 43 vias with no pad and no track before D116) touches no pad and
  no track, so 'touched before, not after' never names it: D116 left two behind (VDDA_1V8 0.102 mm of copper from C48.1,
  VDD_GPU_VPU_DRAM 0.420 mm from C11.1; review RV59.5). `route.py audit` could not show them either: via_dangling stood
  at KiCad's 199 limit before and after, all 199 listed on GND. So a BARE cluster (no pad before the move, none after)
  is judged by what it stands BESIDE -- the pads of its net whose centre is within `--reach MM` of one of its vias'
  (default 1.27, the handoff's distance for a capacitor's own via; give the board's own; 0 = off): beside a pad before
  the move and beside none after, of any part, it is named `unlinked, beside C48.1`. A pad that stays in reach, or a
  moved pad that lands in reach, keeps it; one standing in a zone of its net is a plane via and is only printed
  (`beside -- decide by hand`, never a rip). It names what THIS plan strands, on the board as it is BEFORE the apply:
  run after the apply it finds nothing, and a via stranded by an earlier move is a rip you write. Prove it: a plan
  that moves only the capacitor far away names its bare via; one that shifts it 0.3 mm does not; with two parts in
  reach (board1: C100.1 and L100.2 at (32.42, 13.1)) moving one names nothing and moving both names it; `--reach 0`
  prints the list as it was. To see what is already stranded: every via with no pad and no track in KiCad's
  connectivity and no pad of its net within the reach (board1 after D116: 14 of 36, the two and 12 GND vias older
  than the move).
  **The trap: a moved pad that sat ON another pad's via.** A capacitor under a BGA stands on the GND balls' own
  dog-bone vias; the peel took a via held by one track as a dead end, so moving the capacitor named the ball's via
  (`stub of C10.2`) and left the ball's fan-out track to dangle (board1 RV59: 4 U1 GND ball vias and C37.2's via among
  26 listed; the worker kept them by hand). The rule now: the peel takes a TRACK held at one end at most, a VIA only
  when nothing holds it -- a via that copper still reaching a pad holds, or that stands on a pad's land, is that pad's
  fan-out via and prints `kept: ... still reaches U1.AA21 through its track` (no rip, no decision). And a via the
  board names as STITCHING -- the guide's `stitch via` row (a via of its net within the distance of a class via this
  plan does not rip), or the group that laid it (its own `why` in the plan its `*.applied.json` names says stitch, or
  the plan is `stitch.py`'s) -- prints `stitch -- decide by hand`, never a rip (board1: R3head's (19.95, 31.65), `#42 /
  D74: the pairs' GND stitching`, had been ripped as `unlinked, beside C54.2`). Prove it: the pre-RV59fix copy with
  `placement/RV59caps_place.json` lists exactly `routing/RV59caps.json`'s 20 ripvias, 5 kept, 1 stitching; the D116
  controls (`tmpWork/RV59_orphans/s3_*`) are unchanged line for line; with that plan's `why` made neutral the stitching
  via is ripped again (`tmpWork/orphans_ballvia/proofs.sh`).
  **The trap: the route plan's OWN rips orphan copper too.** A via the plan rips (to move it) may be another pad's as
  well: board1 RV60.5 ripped U300 A6's GND via (14.95, 16.1), which C305.2's B.Cu link also ran to, and the link
  showed only as `track_dangling` in `route.py check`. So `--plan ROUTE.json` (default: `--merge`'s PLAN) takes the
  plan's rips, ripvias and polyrips and the rips listed off, lays its tracks, vias and polygons (in memory, no
  refill), and prints every OTHER track whose end was held before and by nothing now, and every via held before and
  by nothing now outside a zone of its net, as `dangles -- relink or rip: ... on C305.2: its end (14.95, 16.1) was
  held by via GND (14.95, 16.1)`, then a `DANGLES:` total; never a rip (it may be a pad's link to re-lay), exit 1.
  Run it on every plan that rips, before `check`. Prove it: the pre-RV60fix copy with `RV60_fix/place.json` and
  that plan WITHOUT C305.2's relink names the link; with `RV60_fix/route.json` 0; only the via ripped names the A6
  stub's segment and the link, the via laid again in place 0, the two tracks ripped names the via and the stub's
  next segment; with no plan every RV59 / D116 control is unchanged (`tmpWork/orphans_shared/proofs.sh`).
- **`padvias.py` -- vias IN a pad** (a regulator's output or input capacitor, an exposed pad): `--pads REF.PAD[:N],...`
  keeps N centres (or as many as fit) inside each pad, legal on every copper layer (the other nets' copper by the larger
  class clearance, holes by 14_fab's hole gaps, the via-to-pad gap from every OTHER pad), `--pitch` apart, spread from a
  corner; `--count` is the survey (legal centres and how many fit per pad) to run BEFORE a bottom-side part is placed
  under a power pad: board1 found a buck's output capacitor with 0 sites under two straps placed on the other side.
  `stitch.py --only-pads` remains the tool for ONE via beside a small pad on a link.  SWIG hands out a new wrapper per
  call: compare pads by reference, number and position, never with `is` (the first run found 0 sites everywhere --
  each pad forbade itself).

## The gate is KiCad's DRC, not arithmetic of ours

`check` writes the plan into a **scratch copy** of the board and runs KiCad's
own DRC on it, schematic parity on. Any violation that is not already in the
baseline fails the plan. The real board is never touched.

**One kicad-cli run per check.** The baseline (the board as it is) is
`tools/place/drccache.py`'s: a report cached under `routing/.drc_cache/`,
keyed by the sha256 of every file the DRC reads (the board, project, rules,
every schematic sheet, the library tables, `lib/`'s symbols and footprints,
kicad-cli itself). The same board state is never run twice — the second
`check` of a board, `apply`'s "before" count and `audit` all read it — and a
miss runs concurrently with the scratch copy's DRC. stderr says which
(`drc: ... from the cache`); stdout is what it was. `HW_DRC_CACHE=0` turns it
off; the folder is safe to delete. `audit` runs `place.py drc`'s summary and
`pour.py`'s report in-process, on the board it already loaded.

**`check` counts every pour's UNTIED islands** on the scratch copy against the board: a piece
beside the largest that holds no via or through-hole pin of its net (`pour.py` prints them).
A plan that adds one fails (`RULE pour: ... untied island(s)`). A GND pour that tracks split is
legal when every piece is stitched, so raw island counts failed a sound R6. DRC
passes a pour in pieces, and before this only `audit` saw it -- after `apply`
(board2 R5: a CHECK PASS plan cut the +5V strip; ripped and re-routed).

**A net's zones on one layer are ONE copper set** to `pour.py`, `check` and `audit` (one
report line per NET on LAYER, `(N zones, one copper set)`): KiCad knocks a lower-priority
zone out of a higher-priority one of the same net and its connectivity joins them along the
shared edge. Judged zone by zone, the lower one's sliver against that edge read as an untied
island and the higher one's vias as "not reached" (board1 R6a_real: a 0.21 mm2 VDD_SOC prio-2
piece against the prio-5 corridor failed `pours`; `audit`'s count went 5 -> 4 pours, PASS).

**`pournet`: an island that holds a pad or a via must join its net**, by KiCad's own
connectivity on the refilled board (`pour.py`'s `POURNET PASS|FAIL` line, `audit`'s `pournet`
row; `check` fails a plan that adds one, `RULE pournet: ...`). The net's copper is the cluster
of its largest pour island that holds an item; any other island whose items are in another
cluster is named with its area, what stands on it and what its cluster reaches. The untied
test passes such an island (it holds a via), and DRC cannot be read for it: its unconnected
list is capped at 499 on board1. board1 R6a_real: C22.1 and its in-pad via (26.0, 17.5) on a
0.30 mm2 NVCC_DRAM island of In4 passed DRC, prelayout, audit and irfill; `pournet` names it.
Proof: board2's GND In2 in 14 stitched pieces, 21 islands judged, PASS.

⚠ **The open count is KiCad's connectivity, not the DRC report.** `kicad-cli
pcb drc` lists unconnected items only up to a cap (499 on board 1 with 1264
open), so a count read from it stops moving on a big board: 22 nets routed end
to end once read "0 closed". `check` and `apply` count with pcbnew
(`BuildConnectivity`, `GetUnconnectedCount`). A gate plan that lays a fanout
model's vias unconnected (board 1's `esc_gate.py`) raises the count by those
vias: compare a plain copy when the number matters.

⚠ **One thing KiCad's DRC cannot see, and the gate checks itself: a short that
KiCad renames away.** When a board is saved, `pcbnew` gives a track or via
that touches a pad of *another* net — with no pad of its own net in the same
piece of copper — that pad's net. A GND stub landed on a +3V3 pad is saved as
+3V3: one net, no `shorting_items`, the open count unmoved. So `check`,
`apply` and `rip` read the saved file back and compare every track's and via's
net with what was written; a difference is reported as `RULE short:` and fails
the plan (`apply` restores the board).

⚠ **A rule that relaxes clearance over an area relaxes the whole SEGMENT that touches it**
(`areaclear.py`; `check`'s `RULE area clear:`, `audit`'s `area clear` row). KiCad judges
`A.intersectsArea('X') && B.intersectsArea('X')` per item: a track that starts inside the area
and runs on past its edge is held to the area's number over its whole length, and the handoff's
"past it every class clearance holds" goes untested -- board1 R3ab: two 45-degree DSI lines 0.1035
apart ran past U1_FIRST_MM's edge, where their class needs 0.2032, and DRC passed them. Breaking
the track at the edge does not help either: the outer segment's round cap still reaches into the
area. `areaclear.py` reads every such rule from the `.kicad_dru`, clips each track at the area's
outline and measures every piece outside it against every other net's copper on the layer
(tracks, flashed vias and pads, pour fills, whole) at the stricter class clearance (Board Setup's
floor under it; a coupled pair or a pair a rule names by `NetName` at the pair rules' number).
So a line must stand at its class spacing where it crosses the edge: the opening is done inside
the area. A breach whose other copper lies inside the area is printed `-- to its copper at (x, y)
inside the area` (board1 R3abB2: CSI_D1_N's jog cornered 0.12 mm inside the edge, 0.1716 from
CSI_D0_P at the edge). A point inside another relaxed region (another such area, a courtyard a
clearance rule names) is that region's. Proof: `python3 tools/route/test_areaclear.py` (a 45-degree
breach, a pair broken and opened at the edge, a pair broken and not opened, pairs, a via and a pad).

- **A plan's clearances in seconds, before the round: `planclr.py`** -- a design aid, never instead
  of `check`, which still runs every round and is still counted. TRAP (board1 R_U1W r172c,
  2026-11-23): 9 PCIE_85 items (0.106-0.180 against 0.2032) surfaced only after `check`'s ~2 min
  DRC, in a counted round. `tools/kpy tools/route/planclr.py --board B PLAN.json [--pcb
  SCRATCH.kicad_pcb] [--all]` lays the plan in memory as `apply` does -- its `rips`, `ripvias`
  and `polyrips` removed FIRST by `rip_plan`'s own matching (a judge that skips them reads ripped
  copper as obstacles), and measures each plan item, per copper layer, against other nets'
  tracks, vias, pads and copper graphics and against holes, at the board's own `.kicad_dru`
  (last match wins, both orientations, `(layer outer|inner)`), else the stricter class clearance;
  then `exitlane.py`'s judge. A condition it cannot read is never guessed: the pair prints
  `UNJUDGED` with both candidate values. Zone fills, edge and hole-to-hole are `check`'s. `--pcb`
  stages the file as `<board>.kicad_pcb` beside copies of the project: pcbnew finds net classes by
  the board file's own stem, so a `.scratch` file loaded as-is reads every net Default.
  Proof: `--selftest` (a hand-counted board: 0.275 < 0.3 class by pattern, 0.1 < 0.12 by the
  last rule over a looser and an inner one, a hole 0.36 < 0.4, a plan via, one UNJUDGED, a ripped
  track and via never reported; moved clear, PASS) and r172c: all 9 DRC items at the DRC's gaps
  (+-0.0003), 15 pairs in all (DRC names the first per track), exitlane U1 A26, 8 s.
  It also lists every NEW dangling track end of the plan's nets (KiCad's `track_dangling`): a
  plan whose rips take a stub it does not re-lay. TRAP (board1 R_U1W nwp2, 2026-11-23): three
  GPIO15 rips took the ball stub and the new track began where it had ended; planclr passed,
  `check` failed 5 min in on a 0.245 mm end. Proof: the selftest's ripped chain (2 ends,
  hand-counted; re-laid, none) and nwp2 before/after the fix (1 end at (21.705, 22.58); 0).
- **A capped DRC type reads as "new" when a plan moves items.** KiCad lists at most 199
  violations of a test (`via_dangling` on a fanout-heavy board), so ripping and re-adding vias
  changes which 199 are shown, and `check` calls the newly shown ones new. TRAP (board1 nwp2,
  2026-11-23): 5 pre-existing escape vias of other nets appeared. Prove the claim before
  accepting it: a masked before/after DRC with only the plan's nets and the named ones kept
  (far under the cap), `new` = the plan's own vias only.
  Or uncapped, in one step: `tools/kpy tools/route/viadangle.py list BOARD OUT.json` on the copy
  before and after the plan (pcbnew's own TestTrackEndpointDangling after a refill), then
  `python3 tools/route/viadangle.py diff BEFORE.json AFTER.json` -> `DANGLE PASS` (no via newly
  dangling, by UUID). Board1 R1d134c p3, 2026-11-24: 247 -> 246, the "new" SPI_CE0 via dangled
  before; the accept's `why` cites the diff.

That indirection is the whole point. A hand-written clearance test that uses
*the track's own* net class misses that KiCad enforces the **stricter of the two
nets'** clearances — a Default track beside a Power pad needs the Power
clearance. Ask KiCad.

## The rules: `<board>/general_routing_guide.md`

DRC cannot express some rules a board lives by — a plane layer that must stay
unbroken, a class that takes no via, a via linked straight and centred, a
track that must not cross a pad's corner. Those live in ONE file per board,
`general_routing_guide.md` (every new board gets the scaffold): prose for the
eye, and a table — `rule | setting | source` — that `tools/route/guide.py`
reads. **Every row names the handoff line it came from**, because a rule
nobody can trace is a rule nobody can retire. **A row left out is simply not
run.** `route.py rules` prints what is active; a misspelt row name is an error
listing the names the tools know.

| row | setting | what `check` fails / `audit` reports |
|---|---|---|
| `joints` | `0/45` | a joint between two track segments that is not 0 or 45°; a right-angle turn whose diagonal is shorter than one track width; an arc. Entries into a pad or a via are free; a tee is a joint, so a branch leaves at 45° |
| `copper to edge` | `0.5 mm` | new copper (tracks, vias, zone outlines) closer than that to Edge.Cuts. A number STRICTER than Board Setup's copper-to-edge — the fab's minimum plus its outline tolerance — since DRC enforces Board Setup's |
| `via to pad gap` | `0.15 mm` | a via nearer any pad than the gap (its own net's included — DRC never tests that), or linked to its pad other than straight, centre to centre, on the pad's axis (`tools/copper/vialink.py`) |
| `45-degree via link` | `last resort` / `allowed` / `never` | a straight 45° link is listed as a choice, free, or a site |
| `copper shape` | `on` | a plan that adds a shape site: corner / wide / offset pad entry, overshoot, bulb, stacked track, wedge, notch, polygon neck, polygon solid into a pour (`tools/copper/shape.py`) |
| `class width` | `on` | a track whose width is not its class's (outside a via field with `widths`) |
| `thermals` | `outer: all pads; inner: THT only` | the thermal policy a plan's `"thermals": true` types into every pour |
| `plane layer` | `In1.Cu: GND` | any track on that layer; a zone there not of the plane's net |
| `no vias` | `Class (NET: 1)` | a net of the class with more vias on the whole board than its cap (0 for the class, the named exceptions' own) |
| `class layers` | `Class: F.Cu (except NET)` | a track of the class on any other layer |
| `via clear of class` | `PAIRS: 0.625 mm (except GND)` | a via of another net (the plan's in `check`, every one on the board in `audit`) whose centre is nearer a class track's centreline than that. The case: tracks may run beneath a pair on a layer below its reference plane, but a via's antipad in the plane opens it under the pair. DRC cannot see it, because the antipad is on another layer and the nets never touch. Set it to via pad / 2 + plane clearance + the class track's half width. board2 Q10: 0.3 + 0.2 + 0.125; R5's routes had been checked by hand until RV21.5 asked for it as a gate |
| `keep-out` | `name: Class` | a track of that class touching the `placement.json` keep-out of that name |
| `via field` | `U1: 0.45/0.15 lattice, widths 0.1016-0.127, margin 0.4` | inside the part's pad field: a via not exactly that size, or off the pad lattice; a track whose width is outside the range. Outside it: a via of exactly that size. The copper tools skip the field |
| `ground beside` | `Fast: GND within 0.6 mm` | reported by `audit` and `check`: mm of the class's track with ground on 2 / 1 / 0 sides, before → after |
| `frozen` | `class X, net Y` | copper `viafix.py --push` never moves |
| `thermal pad` | `U5.5: solid` | one pad's own connection to the pour |
| `cap reach` | `U1: 1.27 mm (C12, C13)` | `audit`: a pad of a listed part farther than that (centre to centre) from every same-net pad of U1 and every same-net via — the decoupling cap that drifted off its pin. The parts are named, so one that leaves a courtyard does not leave the rule. **`U1: 12 mm to pads (C20, C21)`** measures to U1's own pads only: a part that stands on its own vias always has a same-net via within reach, so a distance to the PART (a bulk capacitor allowed some millimetres from its balls) needs this form |
| `layers apart` | `In4.Cu / In5.Cu: 0 mm` | `audit`: two nets, one on each layer, whose tracks lie over each other for more than that per net pair. A square crossing is free |
| `stitch via` | `Pairs: GND within 1.27 mm` | `audit`: a via of a net in the class with no via of the named net within the distance |
| `neck length` | `4 mm` or `Fast: 4 mm` | `audit`: a connected run of track narrower than the net's width (its class's, or the narrowest its custom rules give on that layer) longer than that |
| `distance` | `C12.1 to U1.A5 <= 1.27 mm` | `audit`: two named objects, `REF.PAD` or `REF` (its nearest pad), centre to centre; `<=` or `>=` |

A plan declares its exceptions, each with a reason printed every run and
reviewed with the copper (at the group's milestone, or the fab release):

- `"necks": [{"net", "width", "why"}]` — a width the class does not give;
  **not needed inside a `bga field` courtyard**: a track piece lying wholly in
  one (its points within the courtyard grown by its own width, so a neck ending
  on the edge counts) and narrower than its class is the fab's escape neck, which
  the guide's `bga field` row makes the rule there. Wider than the class, or
  narrow outside the courtyard, still fails, and DRC holds the minimum width.
  board1 R1a, 2026-11-23: 12 DDR_DIFF escapes at 0.1016 inside U500
  (14_fab BGA_ESCAPE_U500) failed "class wants 0.142" with 0 new DRC. With
  this fix, a piece run 2.3 mm out of the courtyard, or narrowed outside it,
  still fails.
- `"edge_ok": [{"net", "layer"?, "min", "why"}]` — a closer edge distance;
- `"accept": [{"type", "why"}]` — a DRC violation type the group produces on
  purpose (a fanout group ends every escape in free board, which DRC reports
  as unconnected ends). Never a way to wave away a real error; a declared type
  that does not occur is reported so it gets deleted -- **unless the group is
  one piece of several**: "declared but none occurred" is judged on the board
  the check ran on, so a piece rehearsed after the piece that meets its ends
  (board1 R3ab: C laid before B) reports its track_dangling unused, and
  deleting it fails the real order (A, D, B, C: ten track_dangling, CHECK
  FAIL). Judge the entry on the board in the order the pieces will be applied.
- a via that cannot be linked by the rule: `routing/vialink_exceptions.json`
  (`{"via": [x, y], "why"}`), listed by every audit, never counted.

Some rows are *also* expressed as `.kicad_dru` rules by `board_setup.py` (a
no-via class, an area clearance). Then DRC and the guide agree independently,
and if they ever disagree, something moved.

`route.py audit` runs every active rule on the board as it is — DRC through
`place.py drc`, joints, edge, shape, via links, ground beside, pour islands —
and ends `AUDIT PASS` or `AUDIT FAIL (...)`. It is the line a group report
quotes, before and after.

**The last five rows are checks (`checks.py`): what a review measured once,
measured by a tool from then on.** They are plain geometry on the board's
pads, vias and tracks, and they have a second home. A review finding a
number decides carries one — `Check RV4.5: cap reach | U1: 1.27 mm (C65)`
under its verdict — and the handoff publishes them all as
`handoff/findings.json`:

    tools/kpy tools/route/route.py audit --findings --board board1

runs every one on the board as it is, one `PASS|FAIL` line per finding id
with the sites, and ends `FINDINGS PASS` or `FINDINGS FAIL (...)` (exit 1).
Closed findings are run too, which is the point: the one that comes back
fails here in a second, not in the next review. `route.py check` runs them too, before
and with the plan: a finding that holds before and fails with the plan fails
the check (`finding: RVn ...`). TRAP (board1 NORTHGAP T2, 2026-11-25): a
plan's vias narrowed two power pours below their IPC width (RV64.1, RV64.2);
DRC and every other gate passed on the copy, and the audit found it only
after apply. Both lines — `AUDIT` and
`FINDINGS` — go in every pcb review request, and the review snapshot runs
both itself. A check does not replace DRC (copper under the minimum) or
`fanout.py layers`' `pad_gap` (padded vias of an inner-layer pair): neither
is repeated here.

## The plan file

```json
{"group": "R1", "why": "one line for the reviewer",
 "tracks": [{"net": "+3V3", "layer": "F.Cu", "width": 0.4,
             "pts": [[151.0, 128.0], [151.0, 134.0], [153.5, 136.5]]}],
 "vias":   [{"net": "+3V3", "at": [151.0, 130.0], "pad": 0.6, "drill": 0.3}],
 "zones":  [{"net": "GND", "layer": "In1.Cu", "poly": [[100, 100], [174, 100], [174, 140], [100, 140]],
             "clearance": 0.2, "min_width": 0.15, "priority": 1, "pad_connection": "solid"}],
 "necks":  [{"net": "Net-(U2-FB)", "width": 0.15,
             "why": "0.5 mm between U2 pad 4 (Power) and R9's GND pad"}]}
```

Copper a plan takes away or draws beside tracks (the copper fixers write
these; a hand plan may too):

```json
 "rips":     [{"net": "/BUS0", "layer": "B.Cu", "pts": [[11.5, 7.6], [18.6, 7.6]]}],
 "ripvias":  [{"net": "/BUS0", "at": [11.5, 7.6]}],
 "polyrips": [{"net": "/BUS1", "layer": "F.Cu", "at": [20.5, 20.5]}],
 "polys":    [{"net": "/BUS1", "layer": "F.Cu", "pts": [[20.5, 19.5], [21.3, 19.5], [21.3, 20.9], [20.9, 21.3], [20.5, 20.9]]}],
 "thermals": true
```

`rips` names a segment by its two ends (either order), `ripvias` a via by its
centre, `polyrips` a net-tied polygon by its first vertex; a rip that matches
nothing fails the plan. `polys` are filled, net-tied copper polygons (the
guide's rule; `tools/copper/poly.py --plan` dry-runs them); `thermals` re-types
every pour's connection to the guide's policy. The order on apply: rips, then
polygons, tracks, vias, zones, thermals, refill, save.

A `tracks` entry is a polyline: consecutive points become segments. Net names
are exactly as KiCad holds them, hierarchical ones included —
`route.py status --nets` prints every net with its class. A zone's
`clearance` defaults to its net's class clearance (never KiCad's 0.5 mm zone
default, which strands caps on islands of their own); `pad_connection` is
`solid`, `thermal`, `tht_thermal` or `none`; `holes` cuts polygons out of it.

## `apply` and `rip`

`apply` re-runs `check`, backs the board up, writes, saves, then **reloads the
saved file and confirms every item is there** before recording the group in
`<board>/routing/<group>.applied.json`. That manifest holds each item's UUID,
which is what makes `rip <group>` exact: it deletes that group's copper and
nothing else, then renames the manifest to `.ripped`. What the group REMOVED
(`rips`, `ripvias`, `polyrips`) is recorded whole in the manifest's
`removed_items` (uuid, net, layer(s), xy, width / size and drill, the via's
flags, and KiCad's own s-expression of the item), and `rip` lays each one
again from it — same UUID, same padstack flags — then reads them back
(`restored N of N removed items`). An older manifest with no
`removed_items` says so and restores nothing (the copper is in the backup).

- **A rip did not use to give back what the plan removed, and a dead apply
  left a lock.** TRAP (board1, 2026-11-22): `rip` took back only what a
  group ADDED, so rip + re-apply stopped at `plan rips a ... via that is not
  on the board` (exit 2); and that exit -- like every `die()` between
  `check`'s background DRC start and its result -- orphaned a kicad-cli whose
  stdout had no reader: it took `~<board>.kicad_pro.lck`, was killed writing
  to the closed pipe, and the next apply refused "KiCad has the board open".
  WAY AROUND: `removed_items` above, restored by `rip`; a rip entry already
  gone that this group's own earlier record names (the live manifest under
  `--force`, or the last `.ripped` one not restored -- for an older one, its
  plan's own entries) is taken as done with a note, its record carried on;
  any other missing item still exits 2 and names it (a via: the nearest of
  its net). `tools/place/drccache.py` ends every run nobody waited for at
  exit (atexit, and `route.py`'s `finally`) and removes the lock files that
  appeared since it started -- never one that was there before, nor while a
  KiCad GUI or another kicad-cli on that folder is alive. PROOF:
  `tools/kpy tools/route/test_ripapply.py` (a 4-layer synthetic board outside
  `kicad_project/`: apply, rip, apply, rip equal to the board before item by
  item; an old-format rip then re-apply; a missing ripvia exits 2 with no
  lock left and the next apply passes).
- **A board put back from its backup by `cp`, not by `rip`, leaves the
  manifests of what it undid.** TRAP (board1, 2026-11-23): a real-board
  script applied a group, failed on a later step, and its `fail()` copied
  the pre-run backup back. `routing/<group>.applied.json` stayed and now
  described copper that was gone. The re-run's apply of that group stopped
  at `... is already applied; rip it first, or --force` (exit 3). `--force`
  would rip by UUIDs that no longer exist. WAY AROUND: a script that
  restores a backup also sets aside every `<board>/routing/*.applied.json`
  the run wrote: `touch $MARK` before its first step, then `find ... -newer
  $MARK` (renamed `.restored-HHMM`). Not `-newer $BAK`: `cp -a` keeps the
  board's own mtime, so that also takes manifests of groups applied before
  the run (board1 R_W300, 2026-11-23).
  `route.py status` then lists only what is on the board. PROOF: the re-run
  applied both groups ("applied R_U1W_nwp2 ... read back and matched").

`route.py angles` audits the copper already on the board with the same code
`check` runs on the scratch copy (the short-diagonal test no longer depends on
the order segments are stored in, so the two agree). It is a **report** unless
the guide has a `joints` row: without one it prints what it finds and
exits 0. `apply --force` replaces a group: the previous copy's copper is
removed first, by its manifest.

## A plan's origin: a stale plan is refused (`origin.py`)

A plan file does not say which spec it was routed from or which board it was routed
on, so a plan left at its path by an earlier run reads exactly like the result of the
run that just failed (board1's R2: "no solution" after three minutes, and at the
output path a plan from before the spec's track widths were corrected). Three
mechanisms, all in `origin.py` (board-free, standard library):

**The record.** `netgroup.py` writes `<plan>.origin.json` beside its plan: the
spec's path and hash, the board file's hash, the time, the command. A sidecar, not
keys in the plan — the plan's bytes are what they always were.

**The test.** `route.py check` and `apply` run it before any gate, print one line
each for plan, spec and board, and exit **5** on anything not accepted:

    STALE PLAN R2_emmc_In2.json
       plan   R2_emmc_In2.json: as written 2026-11-10 19:02:11
       spec   routing/R2_emmc_In2_spec.json CHANGED since the plan was made (05487e99c9df then, 460bc6ae62ce now): width 0.2 -> 0.25
       board  board1.kicad_pcb: unchanged (8cadc3fc7fc8)
       planned 2026-11-10 19:02:11 by: netgroup.py routing/R2_emmc_In2_spec.json
       nothing applied. Run the planner again on the spec (it replaces the plan), or pass --accept-stale spec to have this file applied as it is

| what | stale when | not stale when |
|---|---|---|
| `spec` | any key that decides the route differs (the refusal names the keys, with both values for a number), or the spec file is gone | it was reformatted, its keys reordered, `1` written `1.0`, its `"why"` reworded, its `"plan"` path or a `"_..."` comment changed |
| `board` | the `.kicad_pcb`'s content differs and route.py did not record how | the file was copied, touched, checked out or restored (same content); or it moved only by recorded `apply`/`rip` steps |
| `plan` | a key the planner wrote differs (a moved point, another file copied over the path) | a key was ADDED by hand (`accept`, `edge_ok`, `marks`) |
| `retired` | the file is a `<plan>.stale.json` | — |

`--accept-stale WHAT` (`spec`, `board`, `plan`, `retired`, comma separated, or
`all`) takes the file all the same; the refusal prints the exact flag. A plan with
no record (hand-written, from `fanout.py` / `pour.py` / `stitch.py` / `decap.py`,
or older than this) gets one `note:` line and is checked as before.

**TRAP: two groups planned on the same board, applied one after the other**
(board1 R_J1N and R_PMIC, 2026-11-23). Both passed `check` on one board. R_J1N went
in first with `--accept-stale board`, and its tracks share the strip between U100
and U1 on In2 / In7 with R_PMIC. R_PMIC's own `--accept-stale` apply then failed
with 50 new DRC (tracks crossing, shorts). By then its tier-B budget was spent, so
the re-route became a wall (Q94).
**WAY AROUND:** `--accept-stale board` is safe only if no group applied since the
plan put copper on the plan's layers inside its area. When two groups share a
corridor, plan the second on a copy of the board after the first is applied. Do
not run them side by side. Apply the group whose budget has rounds left last: it
is the one that re-routes. **PROOF:** board1 tmpWork/R_PMIC/apply.log (CHECK FAIL,
50 new DRC on board f73d9bf1); R_J1N's rehearsal was clean on the same board.

*Why a content hash for the board, not time and size:* a copy to a scratch folder, a
`touch`, a git checkout and a restore from a backup all change the time and keep the
content — false refusals, which teach the flag — and a save that moves one coordinate
can keep the size. Hashing a board file costs milliseconds.

*Why recorded steps are not a refusal:* several plans routed on one board and
applied one after the other is how a bus on three layers is done. Every `apply` and
`rip` appends the board's hash before and after to
`<board>/routing/board_steps.jsonl`; a plan whose board hash leads to the present one
through those steps is taken, the gate names the steps, and `check` judges it against
that copper on the scratch copy — the test that matters. Plans gated one at a time
are still not gated against each other until the first is on the board.

**Retiring.** `netgroup.py` moves a plan already at its output path aside as
`<plan>.stale.json` (its record as `<plan>.stale.origin.json`) when it **starts**, so
a run that fails, hits `--timeout` or is killed leaves **no plan at its path** — its
last line says so — and a run that writes its plan deletes the retired one. Moved,
not deleted: it may be the only copy of a route that took an hour, and nothing has
replaced it yet. Unchanged: rename both files back and it is the plan it was, judged
by its record like any other. (Not retired: a plan the spec's own `plane.zone`
reads.) Two runs on ONE output path: each retires only when it starts, so a run that
fails later never removes the plan another wrote meanwhile.

A plan routed on a **copy** of the board (a what-if in the scratchpad, a race of
variants) keeps a record that fits the real board — the same content is the same
hash — provided its `.origin.json` is copied back WITH it and the spec is at the same
path inside the board folder. Copied back alone it is a plan with no record.

- **A planner's own `_...` working values are not the spec** (board1 R_J2, 2026-11-23). TRAP: netgroup writes
  `fields[]._box / _m / _clr / _shrink` into the spec it holds in memory before the origin is written; `normal()`
  stripped `_` keys only at the top, so every plan with a `fields` block was refused at once as `spec CHANGED`
  (`fields [...] -> [...]`, no visible difference) against its untouched spec file. WAY AROUND: `normal()` drops
  `_` keys at every depth, and a record written before that is judged by its stored `normal`, re-normalised.
  PROOF: R_J2.json, refused with exit 5, judges `spec unchanged (449acc49c30d)` with no `--accept-stale`.

Another planner gets the same guard with two calls: `origin.retire(plan_path)` when
it starts, `origin.write(plan_path, plan, spec_path, spec, pcb, board_sha,
board_dir)` when it writes. `tests/test_plan_guard.py` holds all of it on the fixture
board.

## `netgroup.py` + `maze.py` — the negotiated router, driven by a spec

A group of signal nets from a JSON spec: legs to pads, points or prelaid copper
(`to_path`), escapes and links laid beforehand as `extra_tracks`/`extra_vias`, a
later group's copper kept free as `reserve_*`, an optional `field` (a BGA's pad
field, shut or opened on inner layers), a `plane` with aprons. It writes a plan;
`route.py check` judges it like any other. Every option is in the module
docstring; the layers default to every copper layer that is not a guide
`plane layer`, and the edge band to the guide's `copper to edge` (else Board Setup).

`maze.py` is a negotiated-congestion router (PathFinder) with 45° moves: A* over
(cell, layer, heading); obstacles are *static* (foreign copper, pads, zoning),
*fixed* (a net's own board copper: hard for the others, free for it) or
*routed* (priced, not forbidden, while negotiating). What made it converge is in
its docstring. What the spec has to be told:

- **A differential pair's merge at a 0.5 mm BGA edge is drawn, not negotiated.**
  The grid carries ONE track width, puts the group's clearance between a pair's
  own two lines (no pair gap), and lays no new via inside a `field` -- where the
  courtyard's thinner rule is what lets a merge via stand. Board 1's 14 pairs
  straddling U1's rows A / B (P down its in-pad via, N on top, one via to merge
  each): every pessimistic bound stalled at the courtyard edge, all 28 nets and
  each class alone, even at 0.15 to everything. Drawn by a script at the real
  widths (`board1_module/build/pair_merge.py`: lanes as mitred offsets of one
  spine, `vialink_exceptions` for the merge vias) and gated by `check` with the
  fanout model laid beside (`esc_gate.py`), it closed in a dozen 6-second checks.
  Netgroup still settles the order: count the inversions between the two ends
  first (the routing skill), and fix it with `to_xy` lane legs when a crossing
  needs a layer swap -- negotiation does not find one.
- **Another net's filled pour on a routing layer is a static obstacle, never a `plane`.** TRAP
  (board1 J1S_pre, 2026-11-24): with In5 in the spec's layers, the In5 VDD_GPU_VPU_DRAM pour shut
  five nets outright (BLOCKED SOURCE). And a `block` whose layer is not in the spec's `layers`
  raises KeyError 'F.Cu' in maze.block, where it should be skipped. WORKED: dump the pour's zone
  (outline and layer) first. Read the handoff's term for that layer before offering it: a power
  layer carries a signal only as far as the handoff says (board1 #31: In5 only from a field fan via
  to the first site past the courtyard), and a run that crosses the pour cuts the rail.
- **A series part on a pair's lane (an AC cap) is measured by its pad span ACROSS the lane.**
  An 0402 turned to sit on a vertical lane presents its pads' LENGTH sideways: 0.46 mm across,
  not the 0.25-0.30 of a straight line, so its centre needs half that plus the clearance plus
  the neighbour's half width from a foreign line (board 1: 0.523 at 0.2032 to a 0.18 line) and
  0.47 from its own partner. Space the verticals from the caps' span first, then draw the lines;
  board 1's PCIe channel was spaced for the lines, the caps failed DRC, and U400 moved 1.5 mm to
  make room (Q32). A cap's courtyard thinner rule applies only when BOTH items are inside it.
- **A pair's P / N tune: survey the room before drawing a bump.** At 45 degrees only, a bump of
  height h gains 2h(sqrt2 - 1) = 0.83h over 2h + top of line, and its top is the diagonal of a
  right-angle turn, so it is at least one track width long (`check`'s angle rule). Each via transit
  counts as the board's thickness, so a P line with two vias more than its N needs about 2.4 mm of
  bumps, and a corridor at the class spacing rarely has it. Board 1 measured each line's outer-side
  room per 0.05 mm (`board1_module/build/pair_tune_room.py --profile NET`). A rise that starts at a
  joint and extends the diagonal before it costs no flat. Two lines' bumps often want the SAME gap
  (DSI_CLK_N's and DSI_D2_N's): one of them bumps the other way, toward a partner still on the
  other layer. Then it asked for a skew budget for the rest (D76) rather than re-laying reviewed copper.
- **A bundle that jogs sideways at 45 degrees jogs staggered.** Lines d apart that all start a 45-degree
  jog at the same x come 0.71 d apart on the diagonal, which breaks the pair gap and the class clearance.
  Start each line's jog 0.414 d (d(sqrt2 - 1)) beyond its neighbour's, on the side the jog runs toward, so
  the diagonals stay d apart. Board 1's PCIe lanes moved 0.1 mm north this way (RV12.2), and DRC failed
  the unstaggered try 8 times.
- **Draw the three-way meetings, route the rest.** A net that must reach an IC,
  a connector pin AND a pull-up does not converge in negotiation when dozens of
  others press on the same corner; lay the pin-to-pull link and route one leg to
  it.
- **Frozen escapes carry the placement they were cut for.** Cut them where the
  parts have not changed, and gate the cut pieces alone before routing on them.
- **Use a slot grid**: pitch = track + clearance. On a finer grid a one-cell
  sidestep is a diagonal shorter than the track width the angle rule asks for.
  The grid is then *strict* (a cell exactly at the clearance distance is
  free) — inferred from `pitch == width + clearance`, or set `"strict"` in
  the spec.
- **A 0.5 mm BGA field with a via in every pad** (board1's U1, in-pad vias at
  0.0889 mm): its only lanes are the diagonals between diagonal vias, 0.354 mm
  from each centre against a 0.333 halo -- and the corner-cell test of a
  45-degree step shuts every one of them, on any grid pitch. Set
  `"static_exact": true` (and `"fix_exact": true` for the routed nets' own
  vias): a round obstacle's halo grows by what a step's chord can cut into it
  and the step is judged by its end cells. The symptom without it: every net
  `reaches` tens of thousands of cells and its goal, and iteration 1 still
  says "walled in" (`reach()` floods without the turn rule).
- **`fix_exact` grew a fixed halo by the sagitta of its clearance part alone**
  (CLR + W/2) -- at a 0.0889 mm field rule three times too much (0.034 mm), which
  shut the 0.354 mm lanes between other nets' in-pad vias ("walled in" at
  iteration 1 only when the spec's clearance was made small). Fixed: the growth
  is `sqrt(R^2 + step^2/2) - R` on each obstacle's whole halo R (`Grid._grow`).
- **Inside a field the nets routed here keep its rule between each other too**:
  netgroup sets `Grid.clr_at` from the fields, so two escapes in a BGA courtyard
  are held 0.0889 apart (BGA_ESCAPE_U1), not the group's 0.2032. Before it, an
  escape group was judged at the class clearance inside the field and could
  never converge.
- **Measure a BGA's escape on its own first**: a copy of the spec whose legs end
  `to_path` on a ring just outside the courtyard (each net anywhere on it). It
  runs in minutes and says whether the field lets N nets out on a layer before
  an hour of routing to the far part says nothing clearer.
- **Can the escape leave in a GIVEN order? Ordered intervals, not fixed exits.**
  Give each net a `to_path` segment of its own on that ring, the segments in the
  order asked (net k gets the k-th slice of the edge): the router picks the
  point, and the order holds because the slices do. Fixed `to_xy` exits at the
  escape's own pitch at the courtyard line prove nothing -- board1's U1 east
  block did not route its DRAWN order that way at 1/24 or 1/48 mm (a point on
  the line rounds outside the field, where the clearance doubles, and the drawn
  escape had been compacted onto a 1/98 grid afterwards); the ordered ring
  routed it in 8 s. A swap A/B of the drawn order then costs one run each, all
  in parallel (`xargs -P`): 10 of 61 drew there. A failing run sits to its
  `--timeout`, a passing one ends in seconds -- set the timeout from that.
- **Re-lay an escape from its via, never from a kept stub at the courtyard.**
  A drawn escape ends inside the courtyard at the FIELD's pitch (board1's U1
  In2: 0.204 mm at x 36.14); beyond the courtyard line the class pitch holds
  (0.305), and 0.16 mm of run cannot widen a cluster of four to it at 45
  degrees. Every relay that kept those stubs jammed at the line; ripping the
  whole escape back to the vias routed the same set in 3 s. A 1-D check of the
  strip beyond (vias in columns) must test that line too, at class pitch,
  with the parts' fixed copper there (the pairs left drawn, the dog-bones at the
  edge at the field's clearance) -- and test a line where it SLANTS past a
  column via, not only where it crosses the column.
- **netgroup's ROUTED is not a proof; `route.py check` is.** A script that chains router runs
  (relays, legs) must check every plan it gets back before it reports "routed". board1's relay
  script reported all six relays routed while one shorted a drawn pair tail. Also: netgroup can
  miss another net's via by 2-4 um on its grid. Reserve other nets' vias 0.01-0.02 mm larger in the
  spec, and the DRC misses go.
- **The fab's widths moved (a new stack-up): re-width drawn copper as a plan.**
  `tools/kpy tools/route/rewidth.py --board B --class SE_50 --from 0.178 --to 0.128` writes a
  route plan that rips each matching segment and lays it again on the same points at the new
  width, so `route.py check` / `apply` gate it like any group. Same points is right for a
  single-ended line, and wrong for a coupled pair: its gap is the rule, so narrowing it in place
  opens the gap by (from - to). The tool refuses a pair class. Move each coupled run toward its
  partner by (old - new) / 2, keeping each segment's direction, and re-join the uncoupled tails.
  Leave runs inside courtyards and entries at pad / via pitch as they are (board1, D111: 8 -> 10
  layers; its `tmpWork/D111_pairs/recouple*.py`).
- **A model and a geometric check in a loop: cut on a deletion filter.**
  When the check (a strip placement, a relay) refuses the model's answer, drop
  its objects one at a time and keep each drop that still fails: dropping an
  object only relaxes the check, so what remains fails whatever the rest is.
  The nogood is every literal of the remaining objects' state in the model
  (layer, via or not, entry variables), at most len - 1 to hold. Cutting on
  "undo a layer move" or on vias alone is NOT valid -- the moved net goes back
  to a layer that may be fuller -- and board1's loop declared "no answer" on
  such cuts before this was found.
  **"No answer" from the model is a proof only when the solver says
  infeasible.** `cpsat_milp` reports a time limit with no assignment as
  "Time limit reached: no assignment found" -- the loop must re-solve with more
  time, never read it as "no answer left" (board1's loop r ended "infeasible"
  after 6 cuts on a 180 s miss; the same model with time found an answer).
  Print which one ended a loop. The same holds for the check: a check that crashed
  printed no verdict, so the loop stops on it and never reads it as "does not fit"
  (board1's loop e1 "proved" the pairs alone could not fit, on an empty object list).
- **An escape that will not converge: bound it before re-splitting.**
  `escape_bound.py --ref U1 --layer In2.Cu --spec <escape spec> --vias <fanout
  model> --width W --clearance C [--nfp]` -- a planar cut relaxation over the
  vias' Delaunay triangulation (max-flow): `BOUND k of N`. Below N is a PROOF that no router
  fits those nets on that layer with that via model, and it prints the min cut
  (the balls the wall runs through, how many tracks each gap passes). board1's
  U1 (in-pad vias at 0.5 mm): In2 41 nets, bound 29; the 20-net B.Cu escape the
  router solved bounded at exactly 20 (calibrate against a solved one). Moving
  9 nets to another layer did not move the router's overlap (22 -> 21 nets):
  the CA block sat inside an 11-track cut on EVERY layer, which only the bound
  showed. `--nfp` when the fab removes non-functional pads (other nets' vias
  are holes + copper-to-hole on the layer); `--pad-nets A,B` for a power layer
  that carries a few signals -- its own pours' vias keep their pads there.
  To choose a split over several layers, run `fanout.py layers`: the exact
  most nets the layers take together (a matroid partition over this bound),
  not an anneal -- a pocket of 2-3 balls with one exit each makes per-layer
  capacities not add, which is where a greedy or annealed split stalls. The bound is necessary, not sufficient:
  a split it passes can still stall the grid router at one gap (board1: three
  layers each contested at the same depopulated site) -- move one net per hot
  gap to a layer with room and re-route, and sweep `--seed` / `--hist`.
  A layer confined to one region (board1's In4 / In5 carry signal only toward
  U500) gets `--exit east`: with exits on every side the bound passed In5's 7
  nets; east only it is 6 (two gaps of 3), which is what the router hit.
- **The pitch decides what fits through a gap of 3.** Two parallel 45-degree
  tracks k cells apart are k x pitch / sqrt(2) apart: at 0.0625 four cells
  give 0.1768 against 0.0889 + 0.0889 = 0.1778, so diagonals need five
  (0.221) and a gap the bound passes 3 through takes 2 on the grid. Pick a
  pitch where the straight AND the diagonal steps both land just over the
  track pitch, with the ball pitch a whole number of cells: 0.5 / 11 =
  0.04545 (4 cells straight 0.182, 6 diagonal 0.193) closed board1's In5 at
  6 nets where 0.0625 and 0.5 / 9 stalled at the same two gaps every seed.
  Do not round the pitch (sources must sit on grid points to 1e-4).
- **An escape-only spec keeps the class rules outside the field.** Only the
  BGA's courtyard has the area clearance; a leg to a ring just outside it runs
  at the layer's class width and clearance, or KiCad's DRC fails the ring
  parts (board1: two DQS tracks 0.144 from bottom caps against DDR_DIFF's
  0.2032 that the grid had passed at 0.0889). Gate every layer's escape with
  `route.py check` -- `track_dangling` at the ring is expected, any other DRC
  finding is not.
- **`straighten()` cannot see obstacles; the writer now checks it.** Pulling a
  one-cell jog straight past a via cut its halo by 4 um (board1: 0.1737 from a
  hole against 0.1778, DRC's find, not the grid's). `netgroup` records each
  round obstacle's exact radius as it builds the halo and writes a run's own
  cell polyline where the straightened one comes nearer ("N run(s) left
  unstraightened"). It also widens a leg shorter than the track width between
  two same-sense 45-degree turns -- a one-cell flat or chamfer the angle rule
  reads as a right angle -- by sliding its ends back along the two diagonals,
  kept only where no halo is cut ("N short leg(s) widened").
- **Fixed copper inside a field keeps the field's rule.** A prelaid escape stub (a
  ring ball's top-layer stub, `extra_tracks`) beside another net's took the group's
  clearance (board1 F.Cu: #30's 0.356) even inside U1's courtyard, so two stubs
  0.5 mm apart shut the lane between them; maze now builds every track's halo with
  the field's clearance where it lies (fixed vias were already pre-shrunk).
- **`"cross_square"`: two layers one core apart that may cross only at 90 degrees**
  (`[{"layer", "pts", "width"}]`, the other layer's finished tracks relabelled
  to this one): each cell within the two half-widths of a segment admits only
  the two headings perpendicular to it (a bend admits none), checked on entry,
  exit and a diagonal step's corner cells. Pricing the other layer's copper
  instead does not work -- a square crossing costs more cells than a 38 mm loop
  round the field. Two NEIGHBOURING balls on the two layers wall each other in
  whichever routes first (board1 In4 / In5): keep neighbour pairs on one layer.
- **A board via with its inner pads removed is a hole there, not a pad.** Once the fanout is
  ON the board (not a spec's `reserve_vias` with a hole-sized pad), `netgroup` used to read every
  via at its F.Cu pad on every layer, so an inner-layer escape inside a BGA field was judged
  against 0.4 mm pads the fab removes: board1's U1 had two In2 escapes fit a 1.0 mm via gap in
  KiCad's DRC (hole + 0.1778) and STUCK in the grid. Now a via whose padstack removes unconnected
  layers and that pcbnew does not flash on a field's open inner layer is its drill / 2 + the
  board's copper-to-hole there. A power layer's own pours are not poured yet, so pcbnew does not
  flash their vias either: name those nets in `"keep_pads": {"In5.Cu": [...]}`.
- **A drilled pad's HOLE is an obstacle on every layer, whatever copper its pad has** (netgroup,
  2026-11-23). TRAP: netgroup skipped every pad that lists no copper layer (it was meant for paste
  apertures), so an NPTH drawn on the mask layers only was not there at all. A test hole of 0.3 mm
  on R_J2's In7 route came back -0.20 mm into its wall. A PTH was covered only where its pad halo
  happened to reach copper-to-hole. WAY AROUND: every pad with a drill now stamps its hole (round,
  or a slot's capsule) on every routed layer at drill / 2 + Board Setup's copper-to-hole (14_fab:
  0.1778). New vias keep the hole-to-hole (0.28) from it, their own net's hole included; a routed
  net's own hole is not an obstacle to that net. A copperless pad with NO drill is still not an
  obstacle. Read `GetDrillSize`, not the layer set, before you call a pad a hole: R_J2's "J2 NPTH
  holes" were J2's B.Paste apertures (J2 has no drill at all), and the spec's `block` rects
  over them cost room for nothing. PROOF (`board1_module/tmpWork/T_netgroup_npth/`): with the test
  hole the route keeps 0.224 mm, against -0.20 before. R_J2's spec with and without its blocks, the
  same spec widened over H2's 2.7 mm PTH, and three R2 specs give byte-identical plans or partials,
  old code against new.
- **A net-tied copper polygon (a PCB_SHAPE with a net) is copper, not a zone** (netgroup,
  2026-11-23). TRAP: netgroup stamped pads, tracks, vias and zones but never the board's drawn
  polygons, so R_ETHLED's ETH_LED_SPEED_N via landed at (35.3, 6.6) inside the F.Cu NVCC_DRAM
  polygon of L105 / C105 and route.py check failed `shorting_items`. A zone's fill is only an
  antipad to a via; a polygon is a short on every layer the via passes. WAY AROUND: every
  copper PCB_SHAPE in the window (a fill or a stroke) is stamped on its layer from its outline
  (`TransformShapeToPolygon` + `SquaredDistance`): no via within padclr + via radius of it on ANY
  layer, no track within the clearance that applies (a field's own inside it) + half a track on
  its layer, each stamp `G.mark`ed so a shut net names the polygon. A routed net's own polygon is
  its fixed copper. straighten() is tested against the polygons too. A spec's `extra_vias` /
  `reserve_vias` inside or touching another net's polygon, or an `extra_track` / `reserve_track`
  into one on its layer, is REFUSED with its name (exit 1); a via nearer than padclr is a warning.
  PROOF (`board1_module/tmpWork/T_netgroup_poly/`, `run.sh`): R_ETHLED's spec is refused, naming
  the polygon; the same group with the router choosing SPEED's via (`ethled_rv2`) puts it 0.270 mm
  off the polygon, against 0.170 mm before (both pass `polyclr.py`); R_ETHLED2, R_EMMC_t2 c_v3 (a
  polygon on its routed F.Cu), R_J2 and `ethled_rv` give identical plans, old code against new.
- **A pad of a routed net is its real outline, and every clearance is the stricter of two classes**
  (netgroup, 2026-11-23). TRAP (board1 P_PHY2 v15c): a pad of a net routed IN the group was its
  fixed copper only as the stadium inscribed in its box, so the group's other nets never saw a
  rectangle's corners -- PHY_CLK125 shorted into Y200.3, LED1 0.067 mm from R214.1, INT_N 0.076
  from R215.1; and every static item got one `padclr`, so an SE_50 pad (0.2032) was kept at the
  spec's 0.1016 and LED1 came 0.149 from U200.19. 8 DRC errors, which P_PHY3 patched with spec
  blocks. WAY AROUND: the stadium stays the net's track end (heading, trunk); the pad's own
  outline (`TransformShapeToPolygon` per layer: rect, roundrect, chamfered, custom, rotated) is
  added to its fixed copper as zero-width rows + outline, and every pad near the window goes to
  the writer's test. Each board item carries `GetOwnClearance(layer)` (class and local override),
  a spec item its net's; each static is stamped at max(padclr, its own, the routed nets'
  strictest), and the routed nets' `clearance` is raised to their strictest class (printed).
  `padclr` / `clearance` are floors now. A net walled in by another routed net's fixed copper
  (a leg's order) is named in the BLOCKED report with "give the leg an earlier stage".
  PROOF (`board1_module/tmpWork/T_netgroup_pad/`, `run.sh`): spec_v15c with no blocks gives
  `CHECK FAIL (0 new DRC, 2 rule)` against 8 new DRC before (the 2 rules: LED1's via-angle at
  U202.1, there before, and a new INT_N notch); R_ETHLED2 and R_J2 identical plans old vs new;
  R_EMMC_t2 c_v3 re-routes D2/D3/D4, its check on T_ngpoly 30 new DRC against 40 old, no new kind.
- **A .kicad_dru area rule stricter than the class is not in netgroup's stamps** (board1 R_PMIC, 2026-11-23).
  TRAP: inside U1_FIRST_MM ('fab: U1 first mm', 0.1016) a PMIC_STBY_REQ B.Cu mitre was laid 0.0546 from
  R102.1, because netgroup stamps a pad at its class clearance, not the area's. WAY AROUND: a `block` on
  the pad grown to the area's number, or a hand fix of the one segment (edit the plan JSON, keep 0/45).
  Keep the spec path in the plan's origin ABSOLUTE when the plan was routed on a scratch copy: `origin.py
  write` stores it relative to the copy, and a check on another copy then reads "spec is gone".
  PROOF: tmpWork/R_PMIC/fix_gp2.py -- the one DRC gone, CHECK PASS, 9 joins unchanged.
- **Parallel `--only` runs need a spec copy each** (netgroup, 2026-11-23). TRAP: board1's R_EMMC_t1 ran six
  `--only` subsets of ONE spec side by side; they share its plan and `plan.partial.json.tmp`, and crashed each
  other (`FileNotFoundError` on the partial). WAY AROUND: copy the spec per run (its plan and partial follow it).
  Also: a `to_xy` goal right beside a field land of the routed net's own ball is blocked by that land -- end the
  leg on the pad (`"to": [ref, pad]`). PROOF: `board1_module/tmpWork/R_EMMC_t1/STATE.md` (s5* specs, one each).
- **A capacity split is only as true as its exits.** `fanout.py layers` proved board1's U1 at
  139 of 139 and no router closed it: the model ended 0.5 mm past the ball array, at the
  field's clearance. Each exit it counted and the board did not have cost a routing round --
  gaps between a pair run's tracks (they lead only to that connector), a 0.78 mm slot between
  two parts that the CLASS clearance outside the courtyard shuts, a column of parts cut in half
  by the band. Give every layer its `"obstacles"` out to about 3 mm past the array (laid
  tracks sampled, every pad, grown by class-minus-field clearance outside the courtyard) and
  shut a side that leads nowhere with a row of discs: 133 of 139, which is what the router had
  been saying. Then vary ONE rule per run (a `pad_gap`, an `only`) to find which one binds.
- **Route a split with its own flow as guides.** `fanout.py layers --guides FILE` writes each
  placed net's path through the gaps (a min-cost flow on the same triangulation); a netgroup
  net's `"guide"` prices every cell farther than `guide_reach` from it. The negotiation then
  settles geometry inside a topology known to fit: board1's In5 (13 nets) closed in 28
  iterations where it had stalled on two nets at one gap for every seed. A layer with slack
  may do better without (In4: closed unguided, stalled guided at one pinch).
- **Gate an escape against the WHOLE fanout.** `route.py check` judges what the
  plan holds; an escape plan holds its own nets' vias, so DRC never measures it
  against the field's other vias (board1 RV4.4: 331 of 358 missing, three
  real clearances at one GND ball). Lay every fanout via in a copy of the plan,
  with `"nfp": true` on a via whose inner pads the fab removes (KiCad's
  "remove unconnected layers", start and end kept), `accept` track_dangling and
  via_dangling with reasons, and gate that copy.
- **`pourreach.py`: a rail via must still reach its pour.** A signal track
  through a power layer's via field can close the last gap between a rail via
  and its pour, and DRC cannot see it (there is no pour yet). `pourreach.py
  --layer --box <field> --vias <models> --pad-nets <the layer's pours> --nfp
  --plans <escapes> --min-width 0.1` floods each pour net's free space from
  outside the field: `POURREACH PASS|FAIL`, one line per via cut off. Run it
  with and without `--plans` -- a via already walled in by other nets' holes is
  the fanout's, not the escape's (board1 In5: 3 of 33 without tracks, 8 with).
  Two traps: `--vias <model>` on a board that already HOLDS that fanout counts
  every via twice (run the board alone); and `--box` is where "outside" begins,
  so copper laid past the field is judged only with a box that contains it --
  board1's via-site routes outside U1's courtyard shut a rail via in while the
  courtyard box still read PASS. Reserve a cut via's own corridor
  (`--corridors`) in the re-route's spec (`reserve_tracks`, width 0.01 so the
  keep-clear does not swallow a start point 0.2 mm away).
- **`reachcmp.py`: the pour gate as a before / after comparison, at several widths.** `pourreach` at the
  fab's minimum says whether a rail via is cut off; it does not show a via that reached through 0.3 mm
  before a group and reaches through 0.12 mm after it. `reachcmp.py --board B --before <backup .kicad_pcb
  or folder> --gate routing/reach_gate.json` runs both boards over the gate file's layers, boxes and widths
  and prints only what changed (`REACH SAME` / `REACH CHANGED`). Board 1's bus crossing: on one power
  layer the group cut two ring capacitors' vias off their rails from the supply's side (a box with
  `"sides": "N"`, where the regulator is -- with all four sides everything "reaches"); on the other layer
  nothing was cut off, but six I/O-rail balls went from >= 0.3 mm to 0.125-0.15 mm, which the report then
  states with their currents. **Choose the layer of a group that may take either power layer by running
  this on both** (swap the plan's two layer names, `pourreach --plans`): the guess was wrong here.
  **The gate file also says how copper is read**: `"clr_track": 0.1016` (pourreach's default 0.0889 is an
  OUTER escape's rule; a power layer is judged at the fab's inner clearance) and `"board_pads": true`
  (`pourreach --board-pads`: each via ON THE BOARD is the pad or the bare hole the board has on that layer,
  its own size -- without it every other net's via inside a box is taken as a bare hole, which is wrong for
  a via site outside a BGA courtyard, where the pad stays). Each line prints the rail vias in the box
  before -> after: a change that adds a part's vias shows there.
- **Before the pour gate, every part in or beside the change gets its own vias on the gate's scratch copy,
  and its area is a box of the gate.** "It has no supply via yet" read as a pass, and a capacitor's last
  via sites had been taken by the group under review (board 1's RV24: a decoupling capacitor 2 mm from its
  pin with 88 in-pad sites before the group and 0 after). List the bottom parts under a new fan of tracks,
  find each one's nearest site that is legal on BOTH power layers (outside a BGA courtyard a via keeps its
  pad on every layer), lay them on a copy, then gate -- a via that does not reach its rail there means the
  part moves, and that belongs in the same request.
- **Escapes that end on a GND layer take the via sites nearest the BGA, and two of them never end on
  NEIGHBOURING sites.** Two plane-layer tracks that leave side by side and end on vias one pitch apart close
  the plane between them: the two antipads merge and the strip is an island with no ground via -- DRC has
  no test for it and no pour exists yet to show it. Leave one site empty between them (about 0.5 mm of
  plane then joins the strip to the rest), keep such tracks from under a pad that needs the plane, and
  measure before the request: raster the layer as a pour would fill it and list every piece that does not
  reach the window's border (board 1's `build/gnd_islands.py`; 0.8 mm2 under a bias resistor's pad).
- **`lanecount.py`: how many more tracks fit across a line, and WHERE.** `tools/kpy tools/route/lanecount.py BOARD.kicad_pcb --layer L
  --x x0,x1 --y y1,y2,...` lists, per line, the runs where a track's centre may lie and what is there now. Read the runs, not
  the total: "8 more fit" was a corridor behind three other legs plus two dead-end slots beside a via, and the strait took
  one net fewer than wanted. Count a strait lane by lane before a plan sends one more net through it.
  **Inner layers only: it sees tracks and vias, not pads, zones or pours**, so a count on F.Cu or B.Cu under a part (or
  across a poured inner layer) reads lanes that are a pad or a plane. TRAP (board1 NORTHGAP r13, 2026-11-24): the race's
  knot under U100 read "F and B open" and lanecount agreed; the F copper there is U100's exposed pad and no via can stand.
  WORKED: list every item near the window by layer from pcbnew (pads, zones and footprints included) before calling a
  layer open.
  **A cut counts free lanes at one line, not whether they join the next cut.** A diagonal track between two cuts splits
  a lane into a pocket: free at both lines, through at neither. TRAP (board1 NORTHGAP r15, 2026-11-24): the via-row cut
  under U100 read In2 1 free, but PMIC_RST_B's diagonal In2 leg closed it to the north -- 0 joined. WORKED: count at
  cuts closer together than the shortest track between them (0.2 mm), and call a lane through only when it is free at
  every cut from end to end.
- **`lanecount.py --cut-x`: the same count across a VERTICAL line, with pads, pours, a width and a clearance -- and a
  cut that carries the lines says nothing about their vias.** `tools/kpy tools/route/lanecount.py BOARD.kicad_pcb --cut-x
  39.0,39.5,43.25 --y 13.2,29.8 --layer F.Cu,In2.Cu,B.Cu --width 0.128 --clearance 0.2032 [--own NET,NET] [--through 2]
  [--span X0,X1] [--area-rule]` cuts KiCad's own outlines (tracks, via pads or bare holes, pads, shapes, filled pours),
  grown by clearance + half the width, and lists every free run with what bounds it; `--own` nets are no obstacle unless
  they span the cut, `--through 2` says how many slots stay 2 pitches deep (the rest are dead ends), `--area-rule` counts
  at a rule area's clearance. The old call is unchanged (horizontal lines, tracks and vias only, fixed pitch). It is
  an upper bound, importable (`copper`, `cut`, `through`). PROVE IT on a cut countable by eye before a table is believed:
  two parallel tracks with nothing between (gap - widths - two clearances -> floor(run / pitch) + 1; one pair that
  must REFUSE a second line), the copper already drawn must fit, a poured plane must read 0. TRAP (board1 R1,
  2026-11-27): the channel between two BGAs read 20-49 free lanes a layer at every cut for 25 lines, and a six-variant
  round had closed none: a through via needs ALL its layers free at one point, 6 % of the channel did, and the half
  with 13 lines held 9-11 such sites with a GND via in reach. WORKED: count via sites on every layer at once (greedy
  packing = lower bound, a cover by discs of the via spacing = upper bound), per half, against the lines ending there --
  and per VARIANT from each net's own `"layers"`, never the spec's top-level list (the superset, equal in all).
- **Parallel 45-degree runs between two obstacles are counted in x - y** (or x + y): n runs need (n - 1) x 0.2874 mm of it at
  4 mil spacing, 0.318 on a 0.0455 grid (7 cells, 0.225 mm apart), plus each obstacle's own keep-out x 1.414. Six runs
  wanted 1.59 between two vias 1.50 apart: one via site moved 0.14 mm, by hand, before any router ran.
- **`gndislands.py`: a plane island before there is a pour** (`dump` with KiCad's python, `find` with numpy / scipy): rasters a GND
  layer as a pour would fill it and lists every piece that does not reach the window's border, with the ground vias in it,
  and any escape under a named pad. A standing gate wherever escapes end on a ground layer.
- **The first track of a net on a layer puts that whole net and layer before the shape rule.** `shape.py` judges nub and
  notch only where a net has a track on the layer, so a slot between two of its vias, or between a pad and its unlinked via,
  is listed by no audit until then. `route.py check` compared a baseline that skipped the net with a scratch copy that did
  not, and called every such slot "new" however far away the plan's track lay (two short bottom links: 15 pad-and-via
  notches of GND / +1V8 from all over the board; two cap links on B.Cu: notches 9 and 16 mm from them). `check` now judges
  both copies on the same nets and layers: such a site is a note, "on the board already, not this plan's", and no failure
  -- but `audit` lists it once the plan is applied. So link the pad or join the vias in the same group, or keep rail and
  ground links for the power group, where the net's shape is settled once (a signal group brings vias in pads, which the
  rule passes). To see them before any plan: `shape.py --trackless` (sites marked `trackless`). To prove the comparison: a
  plan holding only one far track of such a net reports `0 new` and the note.
- **A site ends at its via.** A link from a site via to a far pad through several bends is judged by the via-link rule (one
  straight segment); draw such links with the onward routes, not with the escape group.
- **KiCad's DRC report stops at 199 violations a test and 499 unconnected items.** On an unrouted board
  "via_dangling 199, unconnected 499, as before" is the limit both times and says nothing about the group
  just routed: `place.py drc` / `route.py audit` mark such a count "AT KiCad's report limit -- at least that
  many". Judge a routed group by `route.py status`' open links and by walking each net's own copper from
  its first pad to its last via (track ends that stop inside a via's pad count as joined).
- **`pad.GetConnectedPads()` is not a net's connectivity**: it returns the pads joined to that pad
  directly, so a script that counts clusters with it reads every routed two-pad net as 1 open (board1
  R_next_inventory, 2026-11-23: eight connected MDI nets counted open). Use `route.py status`, or
  `board.GetConnectivity().GetConnectedItems(pad, False)` and look for the other pad in the result.
- **A via added in memory over another net's stale fill takes that zone's net.** TRAP (board1 R6a_in4,
  2026-11-23): 50 trial channel vias added with `PCB_VIA` over the board's saved fills came back, after the
  first `BuildConnectivity()` and `SaveBoard`, all on NVCC_DRAM -- the zone under them; refilling first did not
  help. WAY AROUND: `zone.UnFill()` on every zone, then `BuildConnectivity()`, then fill, and read each via's
  net back before judging anything (board1 `tmpWork/R6a_in4/addvias.py`). PROOF: with UnFill first the 50
  read back on their own nets.
- **A rip at the 199 limit shows old violations as new.** `route.py check` compares against a baseline
  KiCad cut at 199 a test, so a plan that REMOVES copper uncovers violations that were there before
  and hidden (board1 D115: 74 via_dangling). Prove they predate the plan (the same items on the
  before copy, past the cap: DRC on the copy with the plan's area masked) and accept them in the
  plan with that reason; never accept a new kind. A rip plan, like any plan, carries a "group" key.
- **A DRC diff of your own (kicad-cli on a before and an after copy) runs `--refill-zones` on both.**
  Without it the after copy's zones keep their old fill, and every moved via reads as a via-to-zone
  clearance hit that a refill would clear (board1 NORTHGAP r7: 33 false clearance violations from six
  re-sited J1 fan vias). PROOF: the same pair of copies with `--refill-zones` read 0 new clearance or
  hole violations (tmpWork/NORTHGAP/r7/drc7f.txt).
  Both copies also sit beside the board's own `.kicad_pro`, `.kicad_dru` and `fp-lib-table`: kicad-cli
  on a `.kicad_pcb` alone runs KiCad's default rules and no project library, so the two halves of the
  diff are not the same test (board1 NORTHGAP r8: 27 `lib_footprint_issues` on the base copy only, read
  as 27 GONE items, tmpWork/NORTHGAP/r8/drc8.txt). PROOF: a diff's by-type line shows no type present
  on one side only that the plan cannot make.
- **Via sites for a fan of track ends: least total length, then uncross.**
  Giving each end of one layer the nearest free site by squared length handed
  board1's In3 three knots of crossing nets, and no amount of banning sites
  undid a crossing; the sum of plain lengths, with every pair of same-layer
  straight lines that cross swapped, routed first time. Giving sites out one
  net at a time, shortest first, walls the later ends in (a 38 mm path). A
  site is a through via: keep it a lane (0.9 mm) off every OTHER end, on any
  layer, or it hems that end to no heading. And netgroup's first corner at an
  existing track's end is two cells -- shorter than a track width, which the
  angle rule fails: re-draw it to a full width before `check`.
- **An escape's layer split is proven ball to ball, not to the ring.** board1's U1 escape put seven
  eMMC lines on the one inner signal layer because they fit there AT U1; routed on to the eMMC, they
  cross three times with the corridor empty -- their order leaving U1 is not the order the other
  BGA's via map can take them in (each ball has one or two open sides; a via column is a wall). Before
  an escape is applied, route every bus of it end to end on a scratch plan (source via to far via, each
  line on its split layer), or at least write both ends' orders down and count the inversions. And a
  group of low-speed via sites goes in AFTER the buses that cross its band: 14 of board1's 53 sites
  stood in that same corridor.
- **`netgroup.py --limit` in a big fine window.** The default (900000) ran out
  in a 24 mm window at 0.045 mm and read as "walled in" for a net with a clear
  way; `--limit 8000000` routed it. "not shut in -- ... or --limit" in the
  diagnosis is this.
  A small window at a fine pitch does it too (board1 R3ab: 11.5 x 9.5 mm at
  0.025 mm, every 10-net run "not shut in -- 45-degree rule" at 900000, routed
  at 5000000): raise `--limit` before reading that line as the 45-degree rule.
  **Since 2026-11-23 the router does this itself**: a net with no path inside `--limit` is searched ONCE more
  over the whole grid with no limit before it is called walled (`maze.py`, `_search_full`; the log line
  `had no path inside --limit N ... searched once without it`), and a net that needed it is searched
  without the limit for the rest of the run. So "not shut in" now always means the 45-degree rule at an
  end, and `--limit` is a speed setting: set it high enough that the first search succeeds (each failed
  first search costs its expansions every iteration until the net is on the unlimited list). MISSING
  (board1 NORTHGAP, Q97): a round closed 0 of 16 with most nets "stopped at the state limit", and the
  wall asked the user for one more round at 20 M.
  So does adding a layer: the search is per layer, so each one added multiplies the cells it reaches. Board1's
  P_EMMClay (2026-11-23) went from F + In2 to F + In2 + In7 (+ B.Cu) and stopped in 25 s at 900000 with "EMMC_D7
  not shut in"; at `--limit 5000000` it routed. PROOF: `board1_module/tmpWork/P_EMMClay/STATE.md`.
- **A `goal_layer` the goal pad does not have is not refused** (netgroup,
  2026-11): a leg with `goal_layer` F.Cu "closed" on J2's B.Cu-only pad by ending
  an F.Cu track over it. Give a SMD pad's own layer; `route.py check` sees the
  open end, netgroup's ROUTED does not.
- **`route.py rip` keeps the group's plan as `<plan>.ripped.json`.** netgroup
  writes over the plan's path on the next run; a rip taken back (the board
  restored from `.<group>-rip-bak`, the manifest renamed back) needs it.
  `--corridors --json` gives each via's reach path in the no-plan geometry, to
  reserve (as the rail's `reserve_tracks`) for the vias a route cut off --
  reserving all of them walls the field, every corridor crossing every escape.
- **An escape on a plane layer is gated by `pourreach` IN the routing loop, not after it.**
  board1 planned 56 signal escapes on In3 / In4 / In5, each plan DRC-clean, and only then ran
  pourreach: 60 of 124, 54 of 70 and 20 of 222 rail vias cut off. With the gate in the loop
  (route; pourreach; reserve the cut vias' corridors; drop the nets that no longer fit; repeat --
  `board1_module/build/plane_esc.py`) the same layers hold 7, 4 and 19. Count a power layer's
  signal capacity that way BEFORE any split gives it nets: `fanout.py layers` does not know a pour
  has to get in. Reserving every corridor up front is worse than reserving what a route cut
  (In4: 5 against 7). And give `--json` an ABSOLUTE path: a relative one is not written where
  you ran the command.
- **`bga field: U1, U300` in the guide** makes the copper rules (shape, via
  links) skip the inside of those courtyards, where the neck-down and the
  via-in-pad are the fab rules' business, without `via field`'s refusal of the
  same via outside. And `vialink` never judges a via whose centre is in a pad
  of its own net (via-in-pad: no link).
- **A field is its part's courtyard, and its tracks are written at its width.**
  KiCad's area rule holds only where both items intersect the courtyard, so
  `netgroup` takes the field from the courtyard (the pads + 0.3 only for a
  part with none) and writes each run split at it: the field's `width`
  inside, the group's outside, joined on the first cell outside. Written at
  the group's width throughout, board1's U1 escapes came 0.080-0.085 apart
  against 0.0889; with a field 0.2 past the courtyard the grid gave 0.0889
  where DRC wants the class's 0.2032. A spec's `padclr` is the LARGER of the
  two classes' clearances (SE_50's 0.2032 against a Default pad), not the
  pad's own.
- **Two BGAs in one group** (a SoC and its DRAM): `"fields": [...]`, one per
  part, each at its own area clearance. With one field, the other part's
  dog-bones keep the group's class clearance between them (SE_50's 0.2032
  where U500's courtyard allows 0.1016) and its goals sit in pockets no net
  reaches ("0 of 4 goal cells").
- **A net that starts on a field part's land names it: `"src_pad": [REF, PAD]`** (or ends with
  `"to": [REF, PAD]`). A field's lands are obstacles to every net, its own included, unless the spec says
  which pad is the net's; a bare `"src"` on the land reads "reaches 1 cell -- walled in" (board 1's R2:
  eMMC D0 / D2 out of U300's row A).
- **A field whose courtyard is far larger than its ball array takes `"box"`** (the array + about 0.35).
  Inside the field box the escape is modelled thin and written thin. Without it the whole run inside the
  courtyard came out at the neck width: 9.3 mm against #39's 4 mm cap on board 1's eMMC, whose
  11.5 x 13 mm body surrounds a 6.5 x 6.5 mm array.
- **A bus between two BGAs: count the lanes with the HOLE's clearance, and prove the order at both ends
  before the first escape.** On an inner layer a via with its pad removed is still a hole, and copper keeps
  the board's copper-to-hole from it: two vias 0.5 mm apart pass a track only if `0.5 - 2 x (drill / 2 +
  copper-to-hole)` holds one (board 1: 0.2 mm drill, 0.1778 -> -0.056 mm, NO orthogonal lane at all; the
  only ways through a via column are an empty site -- 0.444 mm, two 4 mil tracks -- and the diagonal lane
  between two diagonal vias, 0.05 mm of slack hole to hole, 0.026 hole to pad, 0.002 pad to pad). So a via
  in the pad of an OUTER-column ball walls in the ball behind it on every inner layer. Board 1's eMMC: with
  vias under the three outer balls the clock left by a 4.5 mm loop through the field, the bus's order at
  the SoC could not be laid at the memory, and the group read as "one inner layer is not enough". Fix,
  without a layer: an outer-row ball leaves on the top with NO via in its pad and takes its via outside
  the field -- its top leg crosses over the inner lines, so each such net can be put anywhere in the
  inner layer's order. Routed as one spec per layer with the via sites FIXED (`extra_vias`, a row across
  the bundle in the inner layer's order) and a `"guide"` per net at `"guide_cost": 20`: left to itself the
  negotiation (two layers, free vias) did not converge in 300 iterations x 4 seeds, and with fixed sites
  but no guides it took the shorter way round for one net and stalled on two crossings; guided, 43
  iterations. A lane narrower than the grid (0.026 mm) is drawn (`extra_tracks`) and the leg ends on
  its outer end.
- **Length for a match is laid as loops the router is walled into, not as guides alone.** A `"guide"` is a
  price: a cell between a loop's two legs is near the guide too, so every loop was cut short (board 1's eMMC:
  11 lines to +/-1 mm of the clock in time, 5 to 10 mm to add on each). What worked, one negotiated run per
  layer in 4 s: each line's loop drawn in its guide, on the side of the bundle that line has free (nest them:
  the outer line the deeper loop), `"guide_reach": 0.05-0.1`, and the INSIDE of the loop shut to that net alone
  -- `"avoid": [[x0, y0, x1, y1, layer, 1e8]]`. The wall, not the guide, sets a bump's height (its top comes
  one cell beyond the wall's end); make it at least 0.25 mm thick -- the plan writer's straightening pulled a
  run through a 0.1 mm one, avoid cells being no obstacle to it -- and close its base on real copper (a via,
  another net's track), or the net goes round the wrong end. A net that has NO legal path pays the 1e8 and
  goes through the wall: the plan then shows a track inside a wall, which is the sign that a lane you counted
  on is shut (measure the pads, the grid rounds a 0.128 mm need to 0.15). A corner the grid leaves under one
  track width, or a row that must sit between grid lines (two tracks in 0.555 mm), is redrawn in the script
  that merges the layers, and `check` judges the result. A table of every line's delay (per-layer ps/mm from
  the handoff, each via by the depth it is used over) after every run is what makes it converge: seven runs.
- **A via a plan lays inside a BGA courtyard carries `"nfp": true`, and the board is read back for it.** Where the fab
  removes non-functional pads (14_fab), a via without it keeps a pad on every layer: DRC is silent, the planes lose
  0.02 mm a side round each one and the order form says something the board does not (board 1's RV22.4: three site
  vias a hand-merged plan laid; the fanout tools set it, a merged plan does not). It is a gate now, not a thing to
  remember -- the rule came back at 259 vias (RV59.1: a 280-via plan built by a script that never wrote the key).
  Name the parts in `<board>/routing/prelayout.json`, `"nfp_vias": {"refs": ["U1", "U300"], "why": "<the handoff
  line>"}`, and: `route.py check` refuses a plan via whose centre is inside one of those courtyards without the
  key (`RULE nfp`; a via already on the board is counted in the note, not charged to the plan); `apply` reads the
  SAVED board back and restores the backup when a via of the plan lost the flag; `audit` has an `nfp` line that
  fails on any such via on the board, with the count per part and the first positions (`nfp ---- not run` without
  the key -- `route.py rules` says which). To repair vias already laid, do not rip and re-lay them: the plan key
  `"nfpvias": [{"net", "at"}]` sets the flag IN PLACE, so each via keeps its UUID and the group that laid it still
  rips whole (`ripped 594 of 594`); laid again they belong to the fixing group and the first group's rip leaves
  them standing (`ripped 335 of 594`, exit 0). Prove the gate both ways on a copy: `audit` fails before and passes
  after; a one-via plan inside the courtyard without `"nfp"` is refused, the same via with it and a via outside
  without it are not.
- **`netgroup.py` types `"nfp"` itself, before it writes the plan and its origin record.** Every plan via whose
  centre is inside a courtyard of `"nfp_vias"` gets `"nfp": true` (the courtyards are route.py's own
  `nfp_rule`, so what it types is what `check` refuses), a via already typed is left as it is, and the run
  prints `nfp: N of M plan vias typed (refs U1, U300)` (`nfp: not run` without the key). TRAP (board1 R_W300,
  2026-11-23): the flag was typed by a board-local script AFTER the run, which edited the plan `origin.py` had
  hashed -- `check` and `apply` stopped on a stale plan (exit 5) and the group went through with
  `--accept-stale plan`. Never edit a netgroup plan after it is written; a flag it lacks is a netgroup defect.
  Proof: `tools/kpy tools/route/netgroup.py --board B --selftest [--at X,Y --off X,Y]` (no routing, no budget,
  the board read-only): no rule touches nothing, a via inside each courtyard (and `--at`) is typed, one outside
  (and `--off`) is not, an already-typed via comes back unchanged; the expected answers are a ray cast over
  the courtyard corners, not KiCad's `Contains`, and a point on the wrong side of its case stops it (exit 2).
  board1: `--at 16.9,7.65 --off 20.5,10.0` (U300's courtyard is x 7.455..19.945, y 5.405..19.395 -- so
  (10, 10) is inside it, not an outside case).
- **A BGA's unrouted exit lane is invisible to DRC and to the escape bound: `exitlane.py` measures it.** A ring-1
  ball leaves straight out on the part's own outer layer, a ring-2 ball between two ring-1 lands; until those
  tracks are drawn, another net's via, pad or stub standing in a lane breaks no DRC rule (there is no track to be
  near), and `fanout.py layers` / `escape_bound.py` count the lane free. board1 RV60.1 / RV60.5: five capacitor
  vias on U1's ring-1 lines and A6's GND via and stub on U300's A5 line, with the escape at 162 of 162.
  `tools/kpy tools/route/exitlane.py --board B --ref U1 [--plan <bga_fanout.py plan>] [--side S] [--gate
  assign|plan|strict]` draws every ring-1/2 signal ball's lanes from the board (pitch, lattice, rings, courtyard;
  the exit layer is the part's side), takes the width and clearance from the part's area class (`fab: <CLASS>
  class inside <REF>`) or the nets' classes, and names each blocking item: net, xy, layer, edge distance against
  width/2 + clearance, and the ball, ring, side and lane it shuts. `assign` (default) asks a free straight lane
  for every ring-1 ball and a lane of its OWN for every ring-2 ball (an exact matching); `plan` also the ring-2
  lanes the fan-out plan assigned; `strict` every candidate lane. A ball whose own track already leaves it is
  dropped (DRC judges it now). Exit 0 / 1 / 2. Name the parts in `<board>/routing/prelayout.json`,
  `"exit_lanes": [{"ref", "plan"?, "side"?, "gate"?, "why"}]`, and `route.py check` judges every entry whose
  courtyard (+1 mm) the plan's copper or rips come near, before and with the plan, and refuses a ball the plan
  newly leaves without a lane (`RULE exitlane:`); `audit` has an `exitlane` line per part (`---- not run`
  without the key). Run it after a placement or decoupling plan moves copper near a BGA, not only after routing.
  Prove it both ways on copies: the board before the fix fails naming the known items (board1: U1 5 vias, 9
  balls; U300 the via and its two stub segments), the board after passes; a one-via plan back on a lane is
  refused by `check`, a rip-only plan is not.
  For a search: `exitlane.evaluate(lanes, blocked, routed)` is the gate on a set of blocked (ball, lane key)
  pairs, `blocked_by(result, item)` the pairs one via / pad / track would newly block, `shuts(result, extra)`
  the balls that then fail -- `tools/place/sites.py` drops via sites with them (board1 RV61_c28).
  A search that runs WITHOUT pcbnew (a numpy / CP-SAT via model) asks the same: `tools/kpy
  tools/route/exitlane_dump.py BOARD_DIR OUT.json --ref U1` once on the scratch copy the plan will meet, then
  `exitlane_dump.load(OUT)["U1"]` and `shut_by(r, items)` in plain python -- same lanes, blocked set, width and
  clearance. TRAP (board1 R1-0, 2026-11-23): a channel via model with no lane term reported 'closes' that
  pretest failed on 10 U1 balls; with the dump's term it finds those same 10.
  A ROUTER does not read `"exit_lanes"`: `netgroup.py` / `race.py` model a BGA's courtyard strip as free copper
  unless told. TRAP (board1 NORTHGAP r10, 2026-11-24): nine rounds converged on site vias north of U1 that
  `route.py check` then refused, 14 exitlane items, on the lanes of balls OUTSIDE the group. WAY AROUND:
  `tools/kpy tools/route/exitlane.py --board COPY --spec-blocks SPEC.json|nets.txt|A,B,C --out BLOCKS.json`
  writes, per prelayout entry, the still-free lanes of the signal balls whose nets are not the list's, as spec
  `"block"` rects on the exit layer (ring-2 by the gate: assign the matched lane, plan the plan's, strict every
  candidate; a lane copper already shuts is printed SHUT, not emitted) -- two per lane: tracks at width/2 +
  clearance + track/2, vias at width/2 + clearance (netgroup adds its via radius), so netgroup's centre-cell
  block is the gate's edge distance; paste `block` into the spec. Or opt in in the spec, `"exit_lanes":
  "prelayout"` (or `["U1"]`): netgroup blocks the same rects itself (without the key, nothing changes). PROOF:
  `tools/kpy tools/route/test_exitlane_blocks.py` -- U1's nine north ring-1 lanes are emitted; a real via / track
  on each block's edge fails exactly the balls whose blocks hold it, 0.004 beside passes; 30,000 via centres
  outside every block in the courtyard band shut no ball outside the group; without the flag the output is
  byte for byte the old tool's.
- **A pair's own gap is a custom DRC rule, and an in-memory pretest does not run it.** TRAP (board1 R3b_fix43,
  2026-11-23): one line of a pair moved 0.09 to clear an aggressor passed pretest (angles, exitlane, areaclear,
  shape, vialink) and failed `route.py check` 25 minutes later on 'fab: a pair's own gap' (0.104 < 0.15). WAY
  AROUND: before the rehearsal, measure every moved pair's P-N minimum against that rule's number read from the
  .kicad_dru, exempt only where BOTH items touch a listed courtyard (KiCad's per-item semantics); or move the
  pair as a pair. PROOF: that measure reproduced DRC's three numbers (0.1038 / 0.1162 / 0.1284) exactly.
- **An area rule whose area is wider than the fab's is a hole in DRC: measure what it relaxes.** KiCad's
  `intersectsCourtyard` relaxes a gap wherever BOTH items touch the courtyard -- a track laid along the courtyard
  line is relaxed for its whole length -- and a memory's courtyard can be 3 mm wider each way than its ball array,
  on both sides of the board. Board 1 passed DRC with 0.091 mm gaps 3 mm from the nearest ball (RV22.2). A script
  that samples each routed line and reports the smallest outer-layer gap OUTSIDE the true area (and, for a bus
  with a spacing cap, each line's length under each gap) is the gate: `board1_module/build/emmc_spacing.py`,
  60 lines of pcbnew, pads as boxes, BGA lands and vias as circles, a via on an inner layer only where it keeps
  a pad.
- **A script that builds a plan's `rips` from the board lists by group, never by net.** Board 1's builder took
  "every segment of the bus's nets" and ripped the neighbouring group's pull-up link, applied a minute before
  (2 segments, said plainly in `apply`'s last line: "removed 2 segments"). Read that line every time.
- **"converged" is not "routed as meant": read every length after a run.** A net's `avoid` at 1e8 is a
  price, and negotiation outbids any price: a net whose way is shut ends "converged" THROUGH its own wall
  (board 1: one of six strip nets 23 mm through the BGA's interior, a different one each run, the other five
  on their lanes). The net far longer than its neighbours is the one that was shut in. Route it again ALONE:
  the rest of the plan as `extra_tracks`, the escape hatch under a spec `"block"` -- `BLOCKED SOURCE` then
  lists the wall, nearest first, and the last entries of that list name the real obstacle. On board 1 it
  named three in turn, none of them congestion:
  - **a guide between grid lines.** A guide snaps either way; two neighbours that snap towards each other
    leave 4 cells where a lane needs 5. Put every guide point ON the grid (`G(x, y)`), lanes a whole number
    of cells apart (5 cells of 0.5 / 11 for 0.1016 / 0.1016), and `"guide_reach"` under half a cell (0.02):
    with one cell of play the same thing happens further along.
  - **parallel 45-degree runs that turn at one height.** Lanes p apart are p / sqrt 2 apart on the diagonal
    (0.227 -> 0.16 mm, under the clearance). Stagger the turns: each lane further out turns later by 3 cells
    (0.257 mm between diagonals). 2 cells give 0.225 mm -- legal by 0.022 mm and still shut on the raster.
  - **a lane a few micrometres clear of a fixed pad.** The router jogs one cell off it and the jog shuts the
    next lane. A 0.5 mm power via beside a strait takes a lane the 0.4 mm arithmetic gave: count a strait's
    lanes in CELLS from the real pads (0.968 mm is five 0.2032 lanes and four 5-cell ones).
- **Out of a BGA ball straight, then turn** -- when the next ball's via keeps its pad on that layer (its own
  track leaves there). A 45-degree run out of a ball passes it at 0.354 mm centre to centre against 0.352
  asked: 0.002 mm, which the raster closes for one net of a row or another.
- **Nets that enter a corridor from one side take its lanes in the order they enter; give the far sites out
  in that same order**, or one wraps round another's site and the length cap goes. Two families that cross
  in plan view (one set entering south and leaving north, the other the reverse) need a layer each -- a
  member of one family fits on the other's layer only where it can pass beyond that family's END points.
- **A leg's `"src"` is a grid point** -- a via site a leg STARTS from is snapped to the grid before it is
  given to the group that ends there (`source ... is not a grid point` otherwise). A hop that starts at an
  existing track's end takes a guide whose first segment goes ON in the direction the track arrives: left
  free, it turns back after one cell and `check` fails `RULE angle` on a 0.02 mm stub.
- **A via site kept open for a later group is a wall for this one.** A reserved site beside a column of
  sites closed the only way north out of a pocket; the track it shut in went 32 mm round. Before fixing
  sites, draw every track that must pass BETWEEN them, lane by lane.
- **A foreign track that crosses a field's edge is cut at the edge** (netgroup, 2026-11): the part inside
  keeps the field's clearance, the part outside the class's. Judged whole by its two ends, another net's
  escape that ends on the ring took the class's 0.2032 all the way in and shut the one-site gap it shares
  with a second escape (board 1: BT_PCM_CLK's 0.343 halo across a 0.319 mm lane; `BLOCKED SOURCE` named
  the track with its nearest cell 0.325 away -- a halo wider than the field's rule is the sign).
- **Deleting copper with pcbnew in a script: `board.RemoveNative(item)`, never `board.Remove(item)`** in a
  loop over `GetTracks()` -- the SWIG proxy frees the item twice and the interpreter dies with a
  segmentation fault AFTER the loop, before `SaveBoard` (exit 139, the board untouched). `route.py rip`
  uses RemoveNative; a what-if script must too.
- **A goal via off the grid**: end the leg with `"to_path": [[x, y], [x, y]]`
  (the cells within 0.151 of the via), not `"to_xy"` -- that rounds to one
  cell, which may lie in a neighbour's halo.
- **A source off the grid kills a race slot in seconds**: netgroup refuses a leg whose source is off
  the window's grid (0.025), and a dry gate does not catch it. A variant that adds a net to the group
  (a neighbour's track ripped and re-routed inside the plan) snaps that net's source and goal to the
  grid in the spec before the race. TRAP (board1 NORTHGAP r190, 2026-11-25): PMIC_RST_B's source at
  (27.645, 5.008) killed the ceiling slot at 5 s; it was re-raced in a later slot at (27.65, 5.0).
- **A source on a shut field's courtyard line is walled by the field's halo**: a field with
  `"open": []` is grown by about W/2 + clearance, so a lane-end source ON the courtyard line sits
  inside it and netgroup blocks the leg at set-up -- in seconds, but as a router run, so it costs
  the round. End each escape stub past the halo (courtyard line plus W/2 + clr, on the grid) before
  the race, and check every source against the grown field in the spec builder. TRAP (board1 R1
  tracks, 2026-11-25): R1T_F's 50 F legs were BLOCKED in 8 s, every source on U1's courtyard line.
- **Plans checked one at a time on one copy are not proven together.** `check` judges a plan
  against the board as it stands, so plans made side by side each pass and still collide when
  applied: a through via one plan lays (an in-pad via for a net changing layer) crosses another
  layer's plan, and two plans on one layer meet at their seam -- a via whose inner ring appears
  only once the other plan's track joins it (board1 R1, 2026-11-20: 8 relays each CHECK PASS,
  then 4 failed applied in turn). Lay what every plan must see first (the vias), then route and
  `apply` the plans one after another on the copy, each against the ones before it; the copy
  that took every plan is the proof, filed as the rehearsed scratch board.
- **A scratch copy lives OUTSIDE `kicad_project/`, never in `<board>/tmpWork/`.** `boardsel`
  resolves any path inside a board's folder to the board's folder itself, so `route.py apply
  --board <board>/tmpWork/x/cp/<board>` checks against and WRITES the real board (board1 R1,
  2026-11-20: seven relay plans failed "stacked" against the real board's copper and the eighth
  was applied to it; ripped by its manifest, the board back to its backup byte for byte).
  `place.board_dir_arg` now refuses such a path. Build copies in the session's scratchpad and file
  only their results (plans, logs, the rehearsed `r_final.kicad_pcb.scratch`) in `tmpWork/`.
  That suffix keeps any tool from taking it for a board -- and keeps `pcbnew.LoadBoard` from
  reading it: it returns `None`, and a comparison script dies on `'NoneType' object has no attribute
  'GetFootprints'` (board1 RV59c63). To compare the real board with it after an apply, copy it into
  a folder of its own as `<board>.kicad_pcb` first; a script that loads a board checks for `None`.
- **A waypoint is a `"to_xy"` leg, never a `"to_path"` one**: the leg after a `to_path` leg starts
  at the path's FIRST point (`leg_xy`), not where the route met the path, so its copper and the
  waypoint's need not join. A net whose order the router keeps getting wrong (two nets crossed and
  stuck on the same cells, iteration after iteration) takes a first leg `{"to_xy": [x, y],
  "goal_layer": L}` at the order a model already proved (a strip's y at a column), then its goal:
  board1's DMI1_B, via four columns out, crossed DATA8_B when free and routed CHECK PASS with the
  waypoint (R1, 2026-11-19). Proof: `check` passes, and the plan's two legs meet at the waypoint.
- **A waypoint is a joint: the angle rule holds across it.** The leg after a `to_xy` starts with
  the heading the leg before arrived with (netgroup does this), so the turn there is 45 degrees at
  most -- before, it started with none and left 90- and 135-degree joints at the waypoint. Give the
  waypoint leg `"goal_dirs"` toward the next goal (a goal to the east: `[8, 1, 2]`), or the router
  may reach it from behind and turn back. Two traps remain, both for the plan's post-pass, since
  netgroup writes each leg as its own track: a one-cell 45-degree step on each side of the waypoint
  is a right angle whose diagonal is under one track width (`check`: "the diagonal of a right-angle
  turn"; widen it by sliding its ends back along the runs either side, B - u*s and C + z*s,
  s = (w - d) / sqrt 2), and a waypoint the router can reach only from behind comes back as a
  loop with copper stacked on copper -- the order the waypoint states does not route on that layer,
  a model's cut, not a joint to repair (board1 R1 g1-g3, 2026-11-20).
- **Every waypoint is a fixed joint the negotiation cannot move: place one only where a line must
  keep a side.** Legs are negotiated one stage at a time, each finished leg frozen, so an exact
  point at every column of a strip turns one problem into a chain of small ones whose joints no
  later stage can shift. board1's U1 relays with a `to_xy` at all six strip columns stuck (`STUCK`,
  the same 2-4 cells for 20 iterations) at column-1 points where no via stood, with `--seeds 8`,
  `--full` and `--repair` alike; with waypoints only at the columns holding a via (where the line
  must pass on its planned side) and at the end, the same nets routed (R1, 2026-11-20). Proof:
  route the failing net alone (`--only NET`): if it routes, the obstacle is the other nets' frozen
  legs, not the board.
- **`STUCK` names the nets whose cells are contested, not the net that shut the channel.** The
  loser is often listed alone ("nets: X (9 cells)", "would pass if the nets that are NOT in
  conflict lay elsewhere"): a neighbour that holds the way it needs stays uncontested. A diagnosis
  or a model cut built from that list blames one net for a combination; take every net whose copper
  in the `.partial.json` runs within a clearance or two of the contested cells (board1 R1: twelve
  one-net relay cuts made the fanout model infeasible; with the neighbours added, none was one-net).
- **A through via (or a THT pin) is a goal on every layer: say so.** A leg
  with one `"goal_layer"` that arrives on another lays its own via a cell short
  of the prelaid one to change layer -- DRC `hole_to_hole` at 0.0-0.15 mm and a
  via-link site per leg (board2 R5: five of twenty). `"goal_layer": ["In2.Cu",
  "B.Cu", "F.Cu"]` arrives on any; the source side takes a list the same way
  (`"layer"`). Proof: no new `hole_to_hole` and no new vialink site in `check`.
- **The edge band runs round every Edge.Cuts hole too**, not only the outer
  outline (board2's R5: before it, the LED and GPIO nets went straight across
  a 48 x 25 mm cut-out, legal to the grid). Prove it with `route.py check`:
  no `RULE edge` line on a route that passes a cut-out.
- **A plane is blocked on its layer** except an apron round each net's own
  escape end there; priced instead, a net rides through its neighbours' aprons.
- **A narrow pour is cut by vias, not only by tracks: `"vias": false`.** The
  plane block stops tracks on its layer; a via of another net there is only an
  antipad, harmless in a solid inner plane -- but a row of four across a 4 mm
  top-side rail strip cut it in two, J1's 5 V pins on a 6 mm2 island of their
  own, and DRC passed it (board2 R5). `"plane": {..., "vias": false}` keeps
  every new via out of the pour, its `open` strips included (a hop crosses on
  the plane's layer and changes layer outside), the aprons and prelaid vias
  excepted. `route.py check` now fails a plan that adds an island to any pour;
  `pour.py --section` measures what is left across it.
- **`reach()` separates contested from walled in.** When negotiation stalls it
  floods each net's reachable cells; a net that reaches a few dozen against tens
  of thousands for the others is a placement or fanout defect to report, not a
  route to keep negotiating for. The flood stops once it holds a goal cell and
  20 000 cells (printed "20000+", `capped` in the partial): an open net needs
  no more, and flooding a 100 × 70 mm window per net was most of a converging
  run's time (12 of 16 s on `tests/fixture_pcb.py --kind sparse`).
- **"walled in" with a big `reach` is the turn rule, not a wall.** `reach()`
  ignores the 0/45 joint rule; A* does not. A via that reaches 80 000 cells and
  still has "no path even through the others" is boxed in by fixed copper
  through which the only exits need a 90-degree turn (board2's R5: a
  pre-routed lane hard against the via row). Draw the fixed copper with
  `view.py` and free the lane; more `--limit` does not help.
- **"walled in" late in a long negotiation, with pres near 1e17, was the cost overflowing.**
  The search's INF is 1e18 (`maze.py`, `_maze.c`); `negotiate()` let the present cost grow to
  `pres_max=1e30`, so once pres times a shared cell passed INF every contested cell read
  unreachable and a congested net was reported "no route exists as the ends stand". The present
  cost is now capped at 1e12 whatever `pres_max` says; past it, history does the work. Proof:
  `python3 tools/route/test_maze_pres.py` (two nets whose ends overlap, pres_mul 10: the old
  code walled A at iteration 19, now PASS). A run walled after many iterations at a high
  `--pres-mul` before 2026-11-23 is not proof of a wall: count it again.
- **A zone outline inset by exactly the copper-to-edge number fails at its corners.** KiCad
  turns a rounded corner into chords, so the copper there sits about 2 um nearer the edge than
  the arc. board1's R6 GND planes, inset 0.4 with corner radius R2.6 against an outline of R3.0,
  read 0.398 at the corners. Inset the corners a few um more (R2.595 read 0.405). Prove it with
  `route.py check`, which reads the edge after the fill.
- **The search runs compiled when it can.** `route/_maze.c` is the same A*
  (built by `tools/setup.sh` with the system C compiler into
  `tools/.native/`, loaded through ctypes over the grid's own buffers): the
  SAME routes, bit for bit — the agentic test routes its fixture both ways
  and compares — about ten to forty times faster (16 nets: 52.5 s → 4.5 s
  with `--full`, 21 s → 1.0 s by default; the PCB fixture 20 s → 2.2 s).
  Every run's first line says which core is active (`router core: compiled
  ...` or `python (...)`), and so does `route.py rules`; `HW_MAZE_CORE=python`
  forces the Python one. A route with cross-square headings (`xdir`) uses the
  Python search. No C compiler: the same routes, slower. Halo stamping and
  the overlap count stay in Python: the profile puts the time in the search.
- **Only the overlapping nets are ripped up** after the first iteration
  (conflict-only PathFinder): the others keep their paths, and the rising
  cost of the contested cells moves the overlapping nets round them. When
  the overlap has not fallen for 4 iterations, one iteration re-routes every
  net. `--full` re-routes every net every iteration — the router's result
  before this, for a run that must reproduce an old plan. On the agentic
  fixtures: 16 nets 52.5 s → 21 s in the same 14 iterations; the PCB
  fixture's plan identical, 29 s → 21 s. Deterministic per seed either way.
- **`--seeds N` tries N seeds at once** (forked, `tools/par.py`): every
  seed negotiates the leg against the same frozen copper and the LOWEST seed
  that converges is taken, whichever finished first — the same plan with
  `HW_JOBS=1`, and seed 1's plan when seed 1 converges. When none does, seed
  `--seed` runs again with its partial and stall report. On the PCB fixture
  four seeds cost one run's wall clock (82.7 s serial, 27.4 s forked).
- **`--jacobi` routes an iteration's nets in parallel**, each against the
  congestion the iteration started with. A different convergence, not a
  faster same one: two nets can take the same free lane and meet again next
  iteration. On the 16-net router fixture it took 30 iterations (14
  default) yet finished in 11.8 s on 8 cores (21 s default); on the PCB
  fixture 41 iterations (3 default), 61 s against 21 s. Try it on a big
  group that converges slowly; the same plan with `HW_JOBS=1`. NOT on a small
  crowded leg: on board1's 10-line CSI pocket it ran at 1.6-2.3 cores and
  stayed at 3-4 x the serial overlap at the same iteration (2400 cells
  against 592) -- there every net fights for the same few lanes, and a
  Jacobi step moves them all onto the same one. Run serial variants side by
  side as separate jobs instead (one core each).
- **A `src` off the window's grid stops at once** ("not a grid point"): an
  escape end taken from copper (board1: y 28.795 at pitch 0.025) must be
  snapped, or the window origin chosen so it lands on the grid.
- **`--corridor MM` bounds each net's search** to the box round its ends and
  its last path, grown by MM (or 30 % of its span), doubled until it routes,
  up to the whole window — so a corridor never walls in a net the window
  would not. It is OFF by default: on the agentic fixtures (16 nets across a
  funnel; the same bundle in a 90 x 70 mm window) A* already searched
  tightly, the plans were identical and the time within noise. Use it when a
  big window's nets hit `--limit` and read as walled in; a corridor can also
  trade a better detour outside it for a worse path inside, so compare.
- **Wait on a background run by its pid, never with `pgrep -f <its command line>`.** A wait loop written
  as `while pgrep -f "netgroup.py ... spec"; do sleep 20; done` carries that string in its own shell's
  command line, matches itself and never ends: board1's three R2 runs were over in 3 minutes and the loop
  sat for 4 h 50. Start it with `tools/bgwait.py run --name N -- ...` and wait with
  `tools/bgwait.py wait N --timeout 100` (below): by pid, bounded, and it prints the CPU and the log's tail.
- **Plans gated one at a time against the fanout are not gated against each other.** `route.py check`
  lays only what its plan holds on the scratch copy, so two plans that each pass can still collide: board1's
  In2 escape proof ran two tracks through a through via of the pairs' plan, and only `apply` (which checks
  on the board as it stands) saw the short. Before a milestone or the fab release, lay every passed plan on one scratch copy
  -- a merged plan through `check` -- or put the earlier plans' vias and tracks in the later spec's `reserve_*`.
- **Bound a run with `--timeout S`, not a `timeout` prefix.** It stops
  between iterations when the next one (sized by the slowest so far) would not
  end inside S seconds, prints the contested cells and `reach` per net, and
  leaves `<plan>.partial.json` with `"state": "stopped"`. The log is
  line-buffered, so even a killed run's log ends at its last line.
- **Every iteration writes `<plan>.partial.json`** (whole, renamed over the
  old one): the closed legs, the open leg's latest iteration, `contested`
  cells per net, `reach` per net from iteration 3, and `state` — `running`
  (still going, or killed: board2's R5 lost four 90-minute runs before this),
  `stopped`, `stalled`, `walled`. A run that writes its plan removes it. For
  `view.py --plan`, never for a gate.
- **What-if on a scratch board: copy it OUTSIDE kicad_project/** (the session
  scratchpad). `--board` takes a path, but `boardsel` refuses any folder
  inside kicad_project/ that is not a board's (tmpWork/ included). Copy the
  `.kicad_pcb`, `.kicad_pro`, `.kicad_dru`, `fp-lib-table`, `lib/` and
  `routing/`, change the copy with pcbnew (delete a via, move a part), run
  the same spec with `--board <copy>`: the cheapest proof that one obstacle,
  not the router, is what fails a group.
- **A copy of the board keeps the project's file name.** `pcbnew.LoadBoard` takes the net
  classes from the `.kicad_pro` of the SAME base name, and says nothing when there is none: a
  baseline kept beside the project as `pre_apply.kicad_pcb` loads with every net in class
  `Default` (`GetNetClassName()`; board1: 615 of 615 nets, where the same file named
  `board1_module.kicad_pcb` gives 139 of them their classes). A script that took clearances
  from such a load put a via 0.169 mm from a PCIE_85 track that asks 0.2032, and only the
  route check's DRC saw it. Keep a baseline in a folder of its own under the project's name,
  or resolve the classes from the `.kicad_pro`'s `netclass_patterns` yourself. Prove it by
  counting the classes the load reports: all `Default` on a board that has classes is the trap.
  **The same trap fills zones wrong**: a probe that loads a lone `.kicad_pcb` (no `.kicad_pro` /
  `.kicad_dru` beside it) and calls `ZONE_FILLER` fills at default clearances (board1 R6a_c22:
  14 In4 islands instead of 7, VDD_GPU 9.67 mOhm instead of 7.26). Copy the project files in
  with the board before any in-memory fill or IR probe.
- **A scratch copy is a folder of its own, with ONE `.kicad_pcb`, and a new folder every run.**
  `--board <folder>` stops on a second board file ("expected one .kicad_pcb, found 2"), so a
  baseline is copied IN as the board (`cp base/pre_apply.kicad_pcb "$S"/<project>.kicad_pcb`),
  never kept beside it. A script that runs a plan through its gates makes
  `S=<scratch>/<name>_$(date +%H%M%S)` (rsync without `*.lck`, `~*`, `*.place-bak`) and writes
  that path to a file beside the plan for the later steps: emptying last run's folder instead
  needs an `rm` on a shell variable, which the session's safety check refuses, and what is left
  in a reused folder (a lock, a `.place-bak`, a second board) is the next run's false result.
- **The tools' own scratch copies go to `$TMPDIR`, and a full `/tmp` stops a two-step apply half way.**
  `route.py check` (and so `apply`, which runs it first) copies the board folder, `lib/` with its
  3D models included (board1: 48 MB), into `tempfile`'s folder. On a
  machine whose `/tmp` is a tmpfs with a user quota, a session's old scratch copies fill it, and the
  copy dies with `shutil.Error ... [Errno 122] Disk quota exceeded` -- board1's D116: `place.py
  apply` had written 71 placements, `route.py apply` died before its check, the board stood placed
  and unrouted, and the dead run's `~<project>.kicad_pro.lck` in the board folder refused the next
  one (`a STALE lock`: with no `kicad` / `kicad-cli` process, remove that one file by its full name).
  Before an apply that follows a placement: `df -h /tmp` and `du -sh` the scratch folder; when it is
  near full, `export TMPDIR=<a folder on disk>` for the run. An apply script keeps the baseline under
  its own name first and can be resumed from the route step. Scratch copies of the board are 45 MB
  each: a gate script that makes a fresh folder every run says so in its last line, and the session
  lists the old ones for the user to clear.
- **A fixed piece's free end goes ON the grid** (`window` origin + k x `pitch`;
  board2: x = 0.05 + 0.2k, y = 0.2k). The leg ending there reaches the nearest
  cell, and an end 0.15 mm off it joins with a jog the shape gate rejects.
- **Where a gap is narrower than the grid can see, draw that piece and route
  the rest.** A 45-degree slot between a board cut-out's corner arc and a
  pair (board2: 1.27 mm between the arc and USB_DN's diagonal, 0.975 needed)
  fits one track exactly, but not on a 0.2 grid with the pair's blocks: the
  router went 120 mm round the board instead. Measure the slot from the arc's
  centre (distance to the line minus the radius), put the track at a
  distance between the two limits, and give the leg its grid-aligned end.
- **No via inside a goal pad** — an 0402's land with a drill in it is a solder
  thief at assembly.

## An escape toward one neighbour: what a negotiation settles and what it does not (board1's DRAM, R1)

- **End the router at the BGA's courtyard edge, not at a line in the channel.** Inside the field the tracks run at the
  field's pitch, outside at the class pitch: between the two EVERY track has to shift, and a negotiation does not settle
  N tracks that all move -- 37 nets to a line 1.7 mm out never converged, at any split, guided or not; to the edge the
  same nets closed. The fan-out and the channel are then drawn by construction (sorted exits, lanes at the class pitch).
- **Choose the grid for the pitch.** At 4 / 4 mil, 0.2032 mm is 5 cells of 1/24 mm = 0.2083: a gap the bound counts
  three tracks in passes two. 0.5 / 49 mm puts it on 20 cells (0.2041). Cut the window to the field, and give the
  bigger grid `--limit 30000000 --jacobi --corridor 1.5 --pres-mul 1.2` (6 s an iteration; without `--limit` the run
  ends in seconds with "not shut in ... or --limit").
- **After a stall, freeze what settled and re-negotiate the rest.** Take the partial's uncontested nets as a plan,
  apply it, and run `--only` the contested nets -- with whoever fences them in (a BLOCKED SOURCE names the frozen track:
  free that net too). Three nets closed in 4 iterations and five in 11 after 100 iterations of the whole half had not.
  `--only` takes the spec's KEYS (`DRAM_CA2_A`), not short names: a name that matches nothing dies in `max()`.
- **A via keeps its pad on its own layer, and that decides the exits.** Two pads of one inner layer on a 0.707 mm
  diagonal leave 0.002 mm: the bound counts one track there, a router none. Grow every radius by 0.01 mm in the
  capacity model, and choose WHICH nets take the other layer by the gaps their holes open.
- **`fanout.py` flow guides are one flow.** A min-cost flow's paths are not planar between two destinations and it will
  send a net of one channel out through the other's side: one flow per group with a fence of posts between them, and
  each net `avoid`s the other group's half in the run (two runs that do not see each other shorted at apply).
- **Draw a differential pair's escape; route the singles round it.** A router takes each line the short way: it split
  a pair round a via, 1.75 mm apart. A build script draws both necks converging before the edge and the coupled lines
  from it -- at the COUPLED pitch already where the two widen side by side (necks at the neck pitch short when they widen).
- **"own copper" in a BLOCKED SOURCE can be another group's.** Two of the nets had escapes left by a different group
  (laid when a neighbour was re-routed); list every track of the nets on the board before routing them.
- **Count the spacing rule before the split, and the order before the escape.** A "5 x from other signals" rule for the
  pairs' layer decides how many single-ended nets that layer can carry across the channel at all (pairs 0.512 + 0.905
  each side, a single 0.181 at 0.362): a split made by escape capacity alone needed 16.3 mm of a 15 mm channel. And an
  escape that closes is not yet an order the far BGA can take: read the order off the plans and give it to the far
  side's fanout model BEFORE polishing the escape -- here the layer's order was infeasible, found after the escape closed.

- **Read what a model's best answer does with every exemption it was given.** A fanout model let a line that changes layer in
  the channel out of the order rule ("it is on another layer"): its optimum sent one net from the BGA's north edge to the far
  part's middle gap across every track of the layer it arrived on, and every count taken from it was too low (12 against
  an infeasible half). A line that changes layer crosses the channel on ONE layer -- the one it arrives on if the via is at the
  near end, the one it left on if at the far end -- and is in that layer's order like any other. Draw the three worst nets of
  an answer before quoting its number.
- **A through via for one more net in a pocket other groups cross is checked on EVERY layer first.** A site that is free on
  the layers the net uses stood on four plane-layer lanes of an earlier group; `route.py check` reports them as shorts.
  `west_pocket.py near`-style site finders must count all copper, and the cheaper way out is often no via at all (the net
  stayed on its inner layer to a via under its pad).

## Three measurements no gate made (board1's RV27: three must-fix findings, each legal to DRC)

- **`viawebs.py --board PCB --box ...`: antipads that merge.** A row of signal vias under a part, flashed on every inner layer,
  0.67 mm apart: each plane keeps 0.070 mm webs between their clearances -- one slot, which `gndislands.py` passes (a slot that
  reaches the box's edge is no island) and DRC does not test. `WEBS PASS|FAIL`; the cure is the via's own "remove unconnected
  layers" (a plan via's `"nfp": true`), where the fab page allows it.
- **`necklen.py --board PCB --match NET_`: a net's neck over BOTH outer layers.** A limit on the neck-down counts the top stub
  out of the land AND the bottom-layer escape; a report that summed one layer passed 4.18 mm against 4. `NECKS PASS|FAIL`.
- **Where a spacing rule starts is a coordinate: end the lines on the right side of it.** Lines drawn to "1 mm past the
  courtyard" at x 37.30 stood 0.025 mm PAST the mark (the courtyard edge was 36.275, not 36.295), at fan-out spacing where the
  wider rule already held; six router-made lines ran 0.59 mm past it. Read the courtyard edge off the board, end at or before
  the mark, and list the nets with copper beyond it.

## When `netgroup.py` cannot end: BLOCKED, STUCK, and the exit codes

A run that cannot produce a plan stops early and prints which nets, where, and by
what. Read the block; do not re-run with more `--iters` or another `--seed` first.

| exit | last line | what it means | what to do |
|---|---|---|---|
| 0 | `wrote <plan>` | a plan | `route.py check` |
| 4 | `BLOCKED at this leg: NET` | one END of a net is shut in by what the run cannot move | move or drop the nearest blocker in the list, start the net elsewhere, open a layer to it, widen the window |
| 3 | `STUCK at this leg` | the nets named need the same channel and none has a way round the others | give one another layer, move the via, widen the window |
| 1 | `NO SOLUTION at this leg` / `STOPPED at the timeout` | out of `--iters`, the `--timeout`, or a net with no path that is not shut in (the 45° rule at an end; never `--limit`, which the router lifts once itself) | the contested cells and the `reach` table, as before |

**BLOCKED** is found before anything is negotiated, in milliseconds per net
(`maze.Grid.blocked_end`): a flood from the net's source and one back from its goal,
over static obstacles and the other nets' fixed copper only. A flood that ends in a
small closed region without meeting the other end has found a blocked start (or end)
point. The block lists the wall, nearest first, and says what each piece IS, because
`netgroup.py` labels every obstacle as it stamps it (`maze.Grid.mark`):

    BLOCKED SOURCE: EMMC_D3 is shut in at its source, near (16.450, 15.150) mm
       from its source it reaches 419 cell(s) (419 on B.Cu) and none of its 29 goal cell(s)
       what closes it, nearest first (distance from the source; cells of the wall):
           0.200 mm  B.Cu    EMMC_D4's own copper  -- 25 cell(s), nearest at (16.250, 15.150)
           0.200 mm  B.Cu    via of EMMC_VDDI at (16.450, 14.650), reserved by the spec's reserve_vias (in field U300)  -- 11 cell(s), ...
           0.354 mm  B.Cu    pad C300.1 (EMMC_VDDI) at (16.430, 14.500)  -- 8 cell(s), ...

(board1's R2 on B.Cu: the reserved via beside the start point, which the session had
found by hand.) The origin of a track or via is one of `on the board`, `the spec's
extra_*`, `reserved by the spec's reserve_*`; the other labels are pads, a field's
shut pad area, the edge band, a `block` rectangle, a pour, the `plane`, and the via
spacing round a via an earlier leg laid. The pre-check floods at most 4000 cells per
end; a net that `negotiate` then finds walled in gets the same test without the cap.

**STUCK** (`--stall N`, default 20; `--stall 0` turns it off) stops a negotiation
that no price can settle. Three conditions, all needed (`maze.Grid.negotiate`,
`stuck`):

1. for N iterations every contested cell had already been contested in the N
   iterations before it — the overlaps recycle the same cells (the same ones every
   time when two nets want one lane; lane after lane when three want two);
2. the overlap count made no new minimum in those N iterations;
3. no net that overlaps now can be laid clear of all the others as they lie — a
   flood with no turn rule and no expansion limit, so "no way" is certain.

1 and 2 alone would stop a net whose clean detour is merely long (the rising price
reaches it at iteration 25–40); a flat overlap COUNT is no test at all (it sits flat
in runs that converge); 3 alone fires in the middle of a reshuffle. The block:

    STUCK at iteration 30: the same 20 cell(s) have been contested for 20 iterations with no new cell and no fewer overlaps, and no net on them has a way round the others
       nets: EMMC_RST_N (9 cell(s)), EMMC_STROBE (11 cell(s))
       In2.Cu: 20 cell(s) in x 22.850 .. 23.100, y 17.350 .. 17.600 mm
       these nets need the same channel: give one another layer / move the via / widen the window

(board1's R2 on In2.Cu: 9 s and 30 iterations; before, 433 iterations and three
minutes to a bare "no solution".) The last line of the block says whether the nets
would pass with the nets that are NOT in conflict lifted — then another `--seed` or
`--full` may still open it — or not even then: the board has too few lanes there.
`--repair` is skipped after a STUCK stop (the stop is the proof it cannot help). The
detector reads the grid and changes nothing: a run that converges gives the same plan
with it on or off.

**PLATEAU** (`--plateau N`, default 0: off; `--plateau-pct X`, default 2) stops a
negotiation whose overlaps keep MOVING without shrinking — which STUCK lets run, since
it needs the same cells: when the best overlap count of the last N iterations is not
X % below the best before them (`maze.plateau_reached`), the leg ends with `PLATEAU at
this leg: best B contested cell(s) at iteration I ...`, exit 1, `"state": "plateau"`
in the partial; `--repair` still runs. board1's In2 legs (2026-11-15) are what it is
for: four runs to a 1500 s `--timeout`, NOT ROUTED, each within 0–11 % of its
iteration-40 best after 121–300 iterations (rIn2s: 1297 cells at 20, 1292 at 170) —
about 155 minutes of one 8-hour stretch. It is OFF by default because a flat count is
no proof (the harness's fixture sits flat from iteration 2 and converges at 14:
`--plateau 10` cuts it, 20 does not; `tests/test_router_plateau.py`). On a long leg
whose iterations take seconds, ask for `--plateau 40` (or 60) instead of waiting for
the `--timeout`; it reads the counts and changes nothing in a run that converges.

`<plan>.partial.json` carries the same diagnosis as data: `"state": "blocked"` with
a `"blocked"` list, or `"state": "stuck"` with `"stuck"` (nets, boxes per layer in
mm, every cell). `tests/test_router_stall.py` holds all of this on board-free
fixtures with both search cores.

## The search core says when it is the slow one

`maze.py` runs its search in C (`_maze.c`, built by `kicad_project/tools/setup.sh`
into `kicad_project/tools/.native/`) and falls back to the same algorithm in Python
when the library is missing, was built from an older ABI, or cannot be loaded. The
results are identical; the time is not. So the fallback is never silent: every tool
that imports `maze` prints ONE line on stderr,

    router core: python (about 10x slower) - run kicad_project/tools/setup.sh

and, when the library loads but `_maze.c` is newer than it (a pull that changed the
core, no rebuild),

    router core: compiled, but route/_maze.c is newer than .native/libmaze.so (a stale build) - run kicad_project/tools/setup.sh

`HW_MAZE_CORE=python` (asking for the Python core, as the tests that compare the two
do) prints nothing. `maze.CORE_NOTE` holds the line, or None. `./setup.sh check`
reports the compiled core as REQUIRED on a machine with a C compiler.

## `prelayout.py` — can it be routed, before any copper

The tests the boards learned to run late, run at placement:
`fanout.py` plan and layers per BGA, `escape_bound.py` per BGA and layer,
`pourreach.py` for the rail vias, each fine-pitch row's via sites, and each
connector pair's order inversions. One `PRELAYOUT <test> <target>: PASS |
FAIL | TODO | WAIVED` line each; exit 1 on a FAIL or a TODO. It FINDS the
candidates itself (36+ round pads on a grid; 8+ pads at <= 0.5 mm pitch;
connectors joined by 4+ point-to-point nets), so leaving one out of
`routing/prelayout.json` makes it TODO, not skipped. The spec is in the
docstring.

**And the routing groups, declared and counted before the first is routed** (`groups`): every net with
two or more pads belongs to one group of `routing/groups.json` or matches its `"pours"`, and every group
carries the `"count"` command that shows it fits (ended at 30 minutes: no answer is FAIL) or `"covered"`
naming the test that already proves it. The groups that do not fit then come back from the placement as
one batch of questions, not one a day from the routing. A kept result is reused until one of the group's
parts moves; `--no-count` reports the kept results only. The same file gives each run its group and its
budget (`budget.py`, below).

- **The via-site model** (a fine-pitch row): the links to a deeper via row
  pass between the vias above, so k rows space a row's vias
  `max(pad + clearance, pad + (k-1) * width + k * clearance)` apart, one via
  per that / k along the row, adjacent rows
  `sqrt((pad + clearance)^2 - pitch^2)` apart; every pin's via within
  `lateral` of it. On board2's J1 (0.6 / 0.3 via, 0.2 gap and clearance, a
  0.2 track, a 2.1 mm strip less 0.45 to the cut-out) that is two rows, one
  via per 0.60 mm, and 9 sites for the 11 used pins over 4.0 mm — what Q8
  found at R5.
- **Inversions** are counted across the bundle (perpendicular to the line
  between the connectors' centres) over the nets with exactly one pad on
  each connector and none elsewhere: a net through a series part is not in
  the count.
- **A two-row connector has two `rows` entries, one a row** (`"pads": "odd"` / `"even"` with each row's own
  `side`): `"outer"` counts only the row nearest the edge. board1 configured J1's outer row alone; its inner row
  ran under the PMIC (U100's pins and exposed pad on the top over every inner-row pad) and was found at RV31 with
  17 of 25 pins and no via site -- after the stage was drawn over it. Count every row of a connector that a part on
  the other side covers, with that part's copper (`rowfan.py --inward`, `padvias.py --map` per net) before
  placing or routing the part above it.
- **"With that part's copper" means its TOP tracks too, laid first.** A row's vias are through vias: the other
  side's tracks over the row (a crystal's legs, LDO links, a sense line) take the same sites as its pads do.
  board1's J1 counted 34 of 34 under the PMIC without them and 4-9 of 18 inner pins with them. Lay the other side's
  copper as a plan, then search the row cluster by cluster (pins more than ~0.8 mm apart do not interact), each
  `rowfan.py --plan` seeing the vias already chosen; the pins left over become runs to `padvias.py --map` sites.
- **A part whose courtyard straddles the row's outer edge is tested by its pads, pin by pin.** TRAP (board1
  R_U1W moves2, 2026-11-23): `row_check` ended the strip at the nearest courtyard whose near edge lay BEYOND the
  pads' outer edge, so R37 at F (23.70, 39.10) -- courtyard from y 38.585, 0.10 mm behind B-side J2's outer edge
  y 38.685, its pads over the via strip -- was never tested and the row read PASS. Testing its courtyard
  instead shut every row with a part under it (Y100 and C104 under J1, J400 beside J2: both rows FAIL at
  0.00 mm, though J1 has 22 vias laid there). WORKED: a courtyard wholly beyond the edge still ends the strip
  for the whole row (R405's 0.09 mm, unchanged); a straddler's pads go to `straddle_check`: each used pin needs
  one via centre within `lateral` of it, in the strip, `r + clearance` clear of every straddling pad of
  another net (a pad of its own net may take the via), and a run of neighbouring pins of one net drops where
  any of them has a site (J1's six +5V_IN pins: 79 alone is shut by C104.2, 77 and 81 are not). On the copy
  with R37 moved, J2 FAILs naming R37.1 / R37.2; with moves2 whole it names R405 and R37 in one line; the real
  board reads as before (26 pass, 8 waived). It does not take the sites a straddler removes from the run
  count -- each pin is judged on its own. `prelayout.py --selftest` holds the cases.
- **`unconnected-(...)` pads count as used pins in a row** (they match no `skip`): J2's outer row is 52 pads,
  32 unconnected, 18 GND -- "34 used" is 32 unconnected + DSI_D3_P/N. Put `unconnected-` in the row's `skip`
  (`"^(GND$|unconnected-)"`) when those pins take no via; R405's and R37's J2 FAILs are both on such pins.
- A waiver's `target` is a part (every check on it) or, for `rows`, `REF:SIDE`
  (`"U400:S"`): one side waived, the part's other `rows` entries still run.

## `fanout.py` — where a BGA's vias go, and how many nets leave over several layers

**Plan the fanout before the escape, and plan it for the escape.** board1's U1
(0.5 mm) spent seven rounds of per-net allowances (crossings, overlaps, rail
feeds) on a fanout that put every via in its ball's pad; moving the signal and
GND vias into the depopulated ball sites, as NXP's EVK does, let every DRAM
net out on three layers. `fanout.py plan` proposes that model;
`fanout.py layers` says how many nets it lets out, all layers at once.

- **`bga_fanout.py` -- the ring/site rule as an exact plan, counts included** (board1 D115, NXP AN10778
  §4.3). `tools/.venv/bin/python tools/route/bga_fanout.py plan --board B --config <board>/routing/U1_fanout.json
  --out P.json --vias-out V.json` (OR-Tools; it fetches the balls through `tools/kpy` itself). Rings 1..R leave on
  the top layer and are matched exactly to lanes per side (a gap between two ring-1 lands, or an empty ring-1
  site; corner ring-2 balls either side); ring-1 power/GND goes out of the field; every other connected ball gets
  an EMPTY adjacent site by CP-SAT: one net a site, a signal alone, power/GND up to `share_cap` a via, no two
  diagonal stubs in a square, no site in a ring under `min_site_ring` (it walls the lanes), then lexicographically
  the most vias, the core nets sharing last, the fewest diagonals. It prints the clearance arithmetic (site via to
  land, diagonal stub to lands and vias, lane gap) and drops a diagonal that does not clear; per rail: balls, vias,
  balls-a-via histogram and A a via (`rail_current_A` -- say whose number it is). `--vias-out` is `fanout.py`'s
  model format, for `escape_bound.py` / `fanout.py layers` -- the escape is NOT proved by this tool. Trap: the
  board's pad size is taken from `land` in the config, so a footprint not yet changed to its new land still
  plans at the new one -- say so. Classify by NET (`power_nets`), not by the pinout's group: board1's B25 is a
  'dedicated' ball on VDD_SNVS_0V8, and s18 counted it a signal (256 / 211 vs the tool's 257 / 224 with the 14
  DNP-strap BOOT_CFG balls).
  A DEPOPULATED package (an eMMC's 153 balls on a 14 x 14 lattice): rings are the lattice's bounding box, which
  is the populated outline only when the outer frame is solid -- print the ball map first and say so. A ring-2
  power/GND ball with NO empty site beside it (a solid 3-deep frame) goes `out` on the top layer through a lane,
  like a ring-2 signal (#39 (4)), and is listed (`ring2_pg_out_no_site`); a ring-3+ ball with no site stays
  INFEASIBLE. Trap: the solve is not reproducible run to run (CP-SAT, several workers, tied optima) -- two runs of
  one config differ in which balls share; prove a tool change by its solver inputs (classes, lanes, candidate
  sites) and the counts, not by diffing the plan (board1 U300, D115).
  The plan must also pass #45 and #44, so three optional keys (absent: the plan is unchanged -- board1 D115
  re-ran its old config: the unchanged tool's vias exactly): `via_current` {A_a_via, rail_A} caps a net's balls a via at
  floor(A_a_via x its balls / rail_A) (`share_cap_net` sets one by hand, the smaller wins) -- the D83 neck rule
  a global `share_cap` cannot say; `obstacles` (the OTHER side's pads under the field, discs or {x,y,hw,hh,net,tag}
  boxes) bar every site whose through via would come nearer than `obstacle_clearance`, own net included;
  `require_vias` (a decoupling cap's pads) make each pad get a via of its net within `require_reach`, its straight
  link (`require_link` axis | diag | any -- `any` inside a BGA courtyard, where the guide's `bga field` row lifts the
  via-link rule) clear of other pads and of every other net's via it passes. An infeasible plan is solved again
  with every ball and pad soft and prints the balls and pads it cannot serve: the count to ask with. Trap: caps
  placed under a field before its fan-out was planned bar sites wholesale (board1 U1: 86 of 243) -- plan the
  fan-out with `obstacles` BEFORE the back side is populated, or count each cap moved (board1 tmpWork/D115_plan2/movecount.py).
  A ball's ONLY capacitor (#44's ball-only caps) is planned WITH the fan-out, not after it: `cap_room`
  {caps: [{tag, ball}], fp, bounds, reach, clearance, own_gap, link_width[, free_vias]} makes the solve keep, at
  whatever site the ball's via lands on, a legal spot for the cap -- power pad on that via or one axis link <=
  reach, GND pad the same to a GND via (`free_vias`: or a cap-only via on a site no ball takes), the pads' and
  links' clearance to every other net's via, courtyards never overlapping. board1 D116: the plan solved without
  it (s1) left C64 / C65 (J15 / J14) no spot at all -- the lattice round them full of other nets -- and a cap
  placer run afterwards over the fixed vias can only report it. Prove it with the cap placer's own model on the
  new plan (board1 tmpWork/D116_u1_room/oracle.py); a plan without `cap_room` is unchanged (same vias).
  Cap-only GND vias are reported in counts.solver.cap_room (`gnd_via_new`), NOT in `vias`: the cap placer adds them.
  When a cap still has no spot, `soft_pads_first: true` re-solves with the caps first and names the BALL a cap
  costs (board1 C65: G14, the x=29 site chain J14-G14-K/L, not the neighbour cap C64 it looked like) -- the term
  to count is that ball's, not the caps'. `angles` adds rotated cap spots (angle 0 = the axis enumerator); a
  rotated spot passes the solve on its bounding box only, so prove it by DRC on a scratch copy.

- **The pitch decides which rules apply, and `plan` prints the arithmetic.**
  A via fits between four balls when `hypot(px, py) / 2 >= land/2 + clearance
  + via/2` (and the pitch >= via + clearance). Then the field is **dog-bone**:
  every ball that needs a via gets one at an interstitial point on a short
  diagonal stub, as on any 0.8 mm (often 0.65 mm) BGA. Nothing about
  depopulated sites or via-in-pad applies, no via goes in a pad, and the tool
  is just a corner picker -- use it there as well, then `layers` for the split.
- **Fine pitch** (no via between four balls -- 0.5 mm with a 0.4 mm via): a
  via goes in a depopulated site next to its ball, on an orthogonal stub or a
  diagonal one where the stub clears the two balls it passes, else in the
  pad (filled and capped, a fab option). A maximum matching (`--priority`
  nets first) gives as many balls as possible a site. **In-pad only**
  (0.4 mm): not even a site fits; every via in the pad, or HDI.
- **Which balls take a via is by net**: `--skip` (leave on the footprint's
  side; `--skip-rings K` for the outer rings' signals), `--in-pad` (rails
  whose vias feed a pour), `--share` (GND: its own site where free -- lower
  inductance -- or `--share-mode cluster`, joined on top to a neighbour that
  has one), the rest their own via. Moving the signal vias alone can be worse
  than in-pad everywhere: the GND vias left in their pads wall the lanes.
- **The plan is capacity, not routing.** The greedy matching can box in a
  ball one ring in from the edge (board1: a DQS ball lost its lane; `layers`
  reports it unplaced). Pin a site with `--fixed FILE` (the output's format)
  and re-plan. The stub's inductance (about 0.3 nH per GND ball at 0.5 mm) is
  a PDN cost the tool does not judge.
- **Two vias that keep a pad on the same inner layer are a pair rule, not a
  site rule.** At 0.5 mm with a 0.4 mm via, orthogonal neighbours leave 0.1 mm
  of copper: under an inner clearance above that (board1 rev 37: 0.1016 inside
  U1) two nets escaping on one inner layer can never take adjacent sites (or
  pads) -- a non-functional pad is dropped only where the via has no track on
  that layer. Neither `plan` nor `layers` knows a net's layer while it places
  vias, and the router does not see it either (each source keeps its own pad):
  KiCad's DRC on the routed escape found 14 such pairs. What worked on board1,
  in this order:
  1. the sites as ONE integer program (`tools/cpsat_milp.py`, scipy's call on every core): options = each ball's
     adjacent empty sites + its pad; one via a site; one diagonal stub a
     square; each same-inner-layer orthogonal pair a soft constraint at a large
     cost (pairs left are printed -- a layer move);
  2. **anchored** to the model the router already escaped (leaving a ball's
     site costs 20 against 1-1.3 for a site, 50 for the pad), so only the vias
     the rule forces move. Unanchored, the program met the rule by scattering
     the field (In2 went from 0 to 10-17 nets contested); a greedy re-solve
     that bars one site at a time put 45 vias back in their pads;
  3. a net's layer as the last lever, inside the handoff's permissions (a CA
     net to B.Cu, an outer-ring byte out on the top with no via): try the
     candidates in parallel model folders, each gated against its own model --
     the obvious move (the most contested nets) gave B.Cu necks over its 4 mm
     cap, because a net deep in a via block that is free on an inner layer is
     walled on an outer one, where every via keeps its pad.
- **An outer-ring ball that leaves on the top layer needs the site in front of
  it.** A via another ball's matching put one step outside the array, straight
  out of a via-free outer ball, walls that ball's top-layer exit (board1: F.Cu
  "no path even through the others"). Bar those sites before the matching.
- **`layers`: the exact maximum over several layers.** Each layer is
  `escape_bound.py`'s relaxation; the source sets one layer can escape form a
  matroid, so a matroid partition places the most nets possible over all the
  layers together. The footprint's own side (`"side": "component"`) takes
  nets from their balls past every land; other layers from their vias. Inner
  layers are partitioned with every non-pour via a hole (optimistic), then
  each layer is re-checked with its sources' own pads and a trapped net is
  moved off it. It writes `{net: layer}`; `"together"` groups (pairs) are
  reported when split, not enforced. Exit 1 when a net is left over: the
  model or the rules must change, no router will find it. A pass is still a
  bound -- the router has to find it.
  A layer may carry `"only": [regex, ...]`: the nets it may take -- a pour layer
  opened to a few low-speed signals (board 1's D78: 22 nets In2 + B could not take
  went to In5 / In4). "This layer first" is two runs: without the second layer,
  then with it for what is left.
  **It is quick, and it stops when told.** The partition asks thousands of
  sets that differ by one or two sources; each layer keeps ONE flow and moves
  it to the set asked for (a source dropped gives its unit back, a source
  added is one search) instead of building every flow from nothing: the same
  answer by construction (`tests/test_fanout_flow.py` holds the two against
  each other), 35 s -> 4 s on a 296-net fixture and 129 s -> 3 s on a real
  six-layer BGA. `--timeout S` stops at the next question and prints
  `STOPPED at --timeout`: the sets so far are printed and written as
  `<out>.partial.json` -- never under `--out`'s own name, which a later step
  reads as the answer -- and the exit is 1. Use it, never a `timeout` prefix.

## `decap.py` — the decoupling ring as a plan

A via beside every pad, a stub to it (turning only by 45°), and the pour. The
via sites come from `tools/place/sites.py` (board's own via and clearance);
`--pin REF.PAD=x,y` overrides one, which a COLUMN of caps needs — their vias
have to stand in a row. The zone file is hand-drawn: the shape of a power pour
is a judgement about what it must not run under, and no search makes that call.

**A pour in a fine-pitch BGA field needs `min_width` at the fab's minimum**,
not KiCad's default 0.25 mm: the antipads leave narrower bridges and the pour
otherwise fragments.

**A via it lays inside a courtyard that prelayout.json `nfp_vias` names carries
`"nfp": true`** (the fab's non-functional pad removal); before, the plan's via
had none and `route.py check` failed it on `RULE nfp` (board1, a cap's GND via
in U300's courtyard). Prove it: a `decap.py` plan for a cap under the BGA
passes `nfp` 0 new; the same plan with the key stripped fails it.

- **`decap.py` judges each cap's own pad copper where it stands** (2026-11-23, board1). TRAP: it
  found via sites through `World.nearest_via` (or took `--pin`s) and never asked whether the cap's
  pads themselves sat on foreign copper, so C17 placed at (22.50, 12.00) r180 B -- the site `sites.py`
  once offered, both pads on a VDD_SOC via at gap 0 (R_U1W D126-6) -- got two vias and two stubs in
  the plan. WAY AROUND: before its vias are searched each cap is tested with `sites.py`'s `PadCopper`
  (imported, not copied: the real pad shape per flashed layer, the stricter class clearance); a
  refused cap is left out of the plan with sites.py's reason line (`C17 (22.50, 12.00) rot 180
  PADCOPPER: pad 1/VDD_GPU_VPU_DRAM over VDD_SOC via (22.500,12.020) on B.Cu, gap 0.000 < 0.1016;
  ... -- refused: no via, no stub`), a `pad copper:` summary and `PADCOPPER FAIL` follow, the plan
  is still written for the others and the exit is 1; `--no-padcopper` gives the old behaviour. PROOF
  (`board1_module/tmpWork/decapfix/`): on a scratch copy with C16/C17/C18 moved, `--refs C16,C17,C18`
  with the same pins wrote 6 vias before and 4 after (C17 refused), C16's and C18's vias and stubs
  identical and in the same order; `--no-padcopper` writes a plan byte-identical to the old tool's;
  the caps as placed on the real board are not refused.

## `pour.py` — what a pour actually does

A zone's outline is a wish; its **fill** is the copper, and DRC does not read
the difference. It passes a pour that fell into islands, a pour whose one via
sits in a pocket of its own, and a power pour laid over a bus that must see
ground. For every net on every layer (its zones there taken as one copper set)
`pour.py` refills in memory and prints, from the
fill: islands and their areas; same-net vias and through-hole pins per island
and the vias the fill does **not** reach; the same-net SMD pads standing over
it; with `--over NETGLOB:LAYER`, the length of those tracks lying **under** the
fill (split by `--field`'s courtyard when named). `--try NET:LAYER=zone.json`
swaps an outline in memory and measures; nothing is written. Last, `POURNET`: every
island holding a pad or via joined to the rest of its net by KiCad's connectivity, or each
one cut off named (`report()` returns the same as data for `route.py`).
`--section MM [--section-net NET] --from REF[.PIN],... --to REF[.PIN],...` cuts the fill across its
long axis every 0.05 mm between the sources and the loads and sums the copper on
each cut (the fill and the net's lands): PASS when the narrowest is >= MM, and
where it is. It is the "every cross-section of the pour on the load path keeps
N mm" a handoff gives once signal hops cross a rail pour (board2 D67(b): 1.0 mm;
1.71 before R5, 1.20 after). A shrink-the-fill test (erode by MM / 2, ask if
the pins still share a piece) asks a different question -- the narrowest
channel, not the copper across -- and failed a pour the handoff's rule passes.
The window runs from the FARTHEST load pin to the nearest source: every load
draws through the cuts between it and the sources (board2 RV22.4: a window
starting at J1's first +5V pin never cut the pour where pins 79-87 draw, where
a signal hop crossed it).

**Measure the fill, do not assume it.** `GetFilledPolysList(...).OutlineCount()`
is the island count and `.Contains(pt)` says whether a via is really in the
copper.

- **A round through-hole pad takes its spokes at 45 degrees** (KiCad's default
  for a circle; a square pad's are at 0 / 90). At a pour's corner, or where two
  pours meet beside it, two of its four diagonal quadrants must be this pour's
  fill, or DRC reports `starved_thermal` (min 2 spokes). Give the pour those
  quadrants (a higher `priority` over the neighbour where they overlap), not a
  track into the pad (board2 R4: J3 pin 2 between the +5V and VBUS pours).

## `pourneck.py` — the narrowest copper a pour's current crosses (min cut)

    tools/kpy tools/route/pourneck.py --board board1 --net VDD_ARM --layer In4.Cu \
        --from '34.73,8.35;34.73,9.50' --to 22.5,15.0,35.5,28.0 --amps 2.2 --rise 10 --copper-um 35 [--raster 0.025] [--json]
    tools/.venv/bin/python tools/route/pourneck.py --selftest

`pour.py --section` cuts straight across a pour's long axis; a pour bent round vias, antipads
and another rail's fill has its narrowest cut on a curve no straight line finds. `pourneck.py`
refills in memory (the board loaded WITH its project files), rasterises ONE net's copper on ONE
layer (fill, tracks, via pads, pads), ties the `--from` items (`x,y` a via or pad there, or
`REF[.PIN]`) to a source and the copper in `--to` (a box, or `REF`'s courtyard) to a sink, and
runs a max-flow / min-cut (scipy, in `tools/.venv`; the board side in KiCad's interpreter, which
has no numpy -- it starts the flow itself; `--geom` re-runs only the flow). It prints the least
total width, each cut piece with its box, the width the current needs (IPC-2221 internal,
`--rise` and `--copper-um` are the board's numbers, never defaults) and `POURNECK PASS|FAIL`
(exit 0 / 1, 2 bad input). A via is its pad on the layer: a feed via's pad is source, so its
barrel is never a neck, but the ring of copper round it is judged. Other layers' same-net copper
is not modelled.
- **The raw raster cut reads low, and the tool corrects it.** 8-neighbour edges weighted
  (sqrt2 - 1) and (1 - 1/sqrt2) x raster make a straight cut cost its length at 0 and 45
  degrees, but where a piece meets the copper's edge the graph lacks one diagonal: 0.29 x raster
  per END of a piece (none for a closed ring round a via). board1 RV64's first figures were the
  raw raster (VDD_ARM 1.11, VDD_GPU_VPU_DRAM 2.1); corrected (rev 150) 1.21 / 2.38; this tool
  1.22 / 2.38 at 0.05 mm (raw 1.17 / 2.26), 1.23 / 2.33 at 0.025. `--selftest` holds a 3 mm
  strip, a 1.1 mm bridge and a 45-degree strip within 0.03 mm at both rasters. The printout shows
  raw and corrected per piece.
- **As a review finding's check**: `pour neck | NET LAYER: from (x, y), (x, y) to (x0, y0)-(x1, y1),
  2.2 A, 10 K, 35 um` (feeds may be `REF` / `REF.PIN`, the load `REF`; `, raster 0.025 mm`
  optional), in `checks.py`, so `route.py audit --findings` and a guide row run it (zones refilled
  once a run; about 10 s a rail on board1). board1 r b949b628: ARM and GPU FAIL, SOC and DRAM PASS.
- **A lone feed via's ring is a neck no rule means: `--source-disc R` measures outside it.** TRAP
  (board1 Q102, rev 166): a bulk cap's own pour "at least 1.5 mm wide" read 1.336 (C63) and 1.366
  (C48) at every site -- the least cut was the ring of pour round the cap's one 0.4 mm via, which is
  2 pi x 0.2 = 1.26 mm plus the raster and can never read 1.5. WORKED (D128): `--from C63 --source-disc
  0.75 --min-width 1.5` grows the source to the 0.75 mm disc round the feed via (derived from the
  cap's pad: a via in it or one straight link from it) and judges the pour's join inside the disc
  (solid, no spokes, over half its ring). On today's board C63 at (30.70, 13.00) reads 1.746 PASS,
  JOINED 360 deg; without the option the same run still reads 1.336, as before. `--selftest` holds
  the ring (1.31 / 1.34 without, 3.0 with), a 1.1 mm bridge outside the disc (still 1.1), a pinch
  wholly inside it (not counted), and a join solid / 3/8 open / 3/4 cut / on four spokes.
- **A findings `pour neck` row takes the same two terms: `, source disc 0.75 mm, min width 1.5 mm`**
  after `A, K, um` (and `, raster` if given), in that order. MISSING (board1 D128, 2026-11-23):
  checks.py's row called pourneck.measure() with neither, so a published neck finding written to
  Q102's terms still read the via ring. WORKED: on board1's PN_r169 copy, `VDD_SOC In4.Cu: from C63
  to U1, 1.0 A, 10 K, 35 um, min width 1.5 mm` fails at 1.336 mm (x 30.51-30.91, y 13.30-13.70);
  the same row with `, source disc 0.75 mm` before the width passes. A feed via not joined solid is
  named in the failure line.
- **Comparing variants, read the raw raster cut, not the reported one: the reported least cut is
  not monotonic in removing a via.** TRAP (board1 NORTHGAP r192, 2026-11-25): VDD_ARM In4 read 2.197
  with one via out and 2.160 with three out, while the raw raster figure rose 1.992 -> 1.999. A
  variant table built on the reported figure ranks a better variant as worse. WORKED: rank on the
  raw figure, and judge PASS/FAIL on the reported one as the finding does.
- **A ball field's load is `REF.balls`, never the field's box or the courtyard.** TRAP (board1
  RV64.2, D129, 2026-11-23): `to (22.5, 15.0)-(35.5, 28.0)`, U1's field, read 3.01 PASS for
  VDD_GPU_VPU_DRAM -- every copper cell of the net inside the box is load, and three lobes of the
  pour at the field's corner feed no ball; the rail's 19 balls sat behind one door of 0.574 mm
  (2.77 needed). WORKED: `--to U1.balls` (a row: `to U1.balls`) takes as load only the pads on the
  layer of the part's ball vias (a via in a pad of the net, or one straight track from it) and the
  part's own pads there: on that board the rail reads 0.574 FAIL (11 ball vias, 19 balls), and
  VDD_ARM, 2.562 PASS to the field box, reads 0.791 through two webs inside the field (`geom.py
  neck` to a box round the cluster: 0.792). `--selftest`: a strip into a walled box with a 0.5 mm
  door reads 3.0 to the box and 0.5 to the ball vias. With one or two ball vias the least cut is
  their own rings (2 pi x the pad's radius each): a box round them then.

## `pourdrop.py` — a rail's DC drop through its pours, feed vias to every ball via

    tools/kpy tools/route/pourdrop.py --board board1 --net VDD_ARM --layers In4.Cu --from '34.73,8.35;34.73,9.50' --to U1.balls --amps 2.2 --copper-um 35 --hot-c 105 --max-mohm 5.7
    tools/kpy tools/route/pourdrop.py --board board1 --net VDD_GPU_VPU_DRAM --layers In4.Cu,In5.Cu --from L102.2 --to U1.balls --amps 2.5 --copper-um 35 --hot-c 105 [--save-geom F | --geom F] [--json]
    tools/kpy tools/route/pourdrop.py --selftest

A vendor's IR budget ("VDD_ARM < 7.7 mohm from the PMIC to the balls") is a resistance, and no
width measure gives it: `pourneck.py` reads the least cut, which says where a plane is narrow and
nothing about what the narrow place costs. `pourdrop.py` solves the sheet: the net's filled copper
on every layer named, on one raster (0.025 mm), each via and each ball pad one node tied across its
layers, the feed vias held at 0 V, each ball via sinking `amps` x (the balls it serves / the
part's balls of the net). It prints the worst and the mean resistance to a ball via and where the
worst is; `--max-mohm` judges it. FAIL also when a ball via is not reached from the feeds or a
ball has no via (exit 1); exit 2 on a board it cannot read. About 70 s a rail with the refill,
10 s from a saved geometry (`--geom`).

- **Copper is hot: `--hot-c` is the temperature the budget is held at**, never 20 C. 35 um reads
  0.493 mohm/sq at 20 C and 0.657 at 105 C (+33 %). TRAP (board1 Q111, 2026-11-23): the board's
  own count of a two-layer rail read 3.9 mohm with cold copper; the same copper reads 5.05 hot
  (this tool at 20 C: 3.79).
- **What it counts is the pours only.** Not in the figure, and owed by the limit: the feed vias'
  barrels from the regulator's layer to the pour, the ball vias' barrels from the pour to the
  balls' layer, and the stub from via to ball. So the `--max-mohm` of a plane is the vendor's
  path figure LESS that series part (a study's number: board1 `s24_field_web`, 7.7 -> 5.7 and
  7.8 -> 6.4), never the vendor's figure itself. A via between two layers of the rail is an ideal
  tie (its barrel is not charged); balls of one rail are taken to draw equal current.
- **Why it, and not a width, inside a ball field.** Between a BGA's antipads a plane's webs are
  under any width-for-current figure by construction (0.5 mm pitch: under 0.4 mm a web), many in
  parallel. A web's heat is bounded by the voltage across the rail alone (`studies/common.py
  joule_rise_bound`, V^2 / 8 k rho: 20 mV in hot copper is under 6 K), so a rail inside its drop
  budget cannot overheat any web, and the drop is what the webs cost. Outside the field the
  IPC width still applies (`pour neck ... to <the field's box>`).
- **As a review finding's check**: `pour drop | NET LAYER[+LAYER]: from (x, y), (x, y) to REF.balls,
  2.5 A, <= 6.4 mohm, 35 um, 105 C` (feeds may be `REF` / `REF.PIN`; `, raster 0.025 mm` optional),
  in `checks.py`, so `route.py audit --findings` and a guide row run it. board1 (D130): VDD_ARM
  In4 5.36 of 5.7 PASS; VDD_GPU_VPU_DRAM 9.49 of 6.4 FAIL on In4 alone, 5.05 PASS with its second
  pour on In5.
- `--selftest` holds seven cases with a hand figure: a strip (9.6 squares), the same on two tied
  layers (4.8), a second layer fed through one via, a disc (ln(R/r) / 2 pi squares), an L with a
  3 : 1 current share, a gap (FAIL: not reached) and a ball with no via (FAIL: not served). A
  round pad's edge on a raster costs about 1.5 % at 0.05 mm; the tolerances scale with the raster.

## `stitch.py` — stitching vias and GND pad drops, as a plan

    tools/kpy tools/route/stitch.py --board board2 --plan routing/R6_pours.json --net GND --pitch 5 --merge --out routing/R6_gnd.json
    tools/kpy tools/route/stitch.py --board board2 --net GND --only-pads [--diag] [--pad-list REF.PAD,..] --out routing/R6_pads.json
    tools/kpy tools/route/stitch.py --board board1 --net GND --near '^DRAM_' --within 1.27 --except NET,NET@X:Y --out routing/R1_stitch.json

A layout's "stitching vias every 5 mm along the pours' edges and round the cut-out" is a few hundred
placements that each have to clear every other net's copper on every layer. `stitch.py` lays the
plan's zones, tracks and vias in memory, fills the zones, and places vias where one is legal:
- inside the net's fill on every layer it pours;
- clear of other nets' copper on every layer by the clearance;
- the via-to-pad gap from every pad;
- `--sep` from every via;
- copper-to-edge;
- `--own-gap` from the net's own copper where it does not pour.

It places them in three passes:
- **tie**: every island holding no via or THT pin of the net;
- **ring**: every Edge.Cuts contour, at `--pitch`;
- **grid**: optional, with `--grid`.

`--pads` drops every SMD pad of the net that no track touches to a via on one of its axes, linked
straight out of its centre; `--diag` falls back to a straight 45° link. A spot or pad with no site is
listed (`MISSED`), never forced. Its output is an ordinary plan for `route.py check` / `apply`.

`--near REGEX` (no tie / ring / grid) is "a ground stitching via within 50 mil of every signal via":
each via of a matching net with no `--net` via within `--within` gets the nearest legal site in that
disc, the most constrained first, one stitch serving every wanted via it reaches; `--except` leaves
out a net or one via (`NET@X:Y`). Each wanted via prints its distance before -> after, and one with
no site prints what blocks the least-blocked site in its disc (the pad, via, track or fill by name).

- **`--near` judges every site at one `--clearance`.** A signal via inside a BGA's courtyard, where
  the fab's area rule allows less, finds no site at the class clearance: run a second pass on what
  is left at the area rule's clearance (`--plan` the first pass's output, `--merge`) and let
  `route.py check`'s DRC judge it (board1 R1: one DRAM via had none at either; the least-blocked
  site's single blocker, a track, names what would have to move).

- **Drop the GND pads before the signal groups that wall them in.** board2 routed R5 (In2.Cu / B.Cu)
  first, and eight GND pads had no via site left. Two of them had none even against the power group,
  because an F.Cu rail and a pair boxed them. `stitch.py --only-pads --plan <the signal spec's fixed
  copper>` on the earlier board found every site; those vias went in as their own group, and the
  signal router routed round them. Check every hand-placed via against the later group's fixed copper
  too: `check` judges only what the board and the plan hold.
- **A via near its own net's copper on a layer the net does not pour leaves a notch** (the shape
  rule's slot under 2 x CLOSE): `--own-gap 0.6`.
- **A fill-based clearance is not tight enough alone.** A via inside the fill shrunk by its radius
  stood 0.166 mm from a track, because a 45° fill edge is approximate. Every layer is also tested
  against the copper itself.
- **`--fill-gaps` aims at the gaps.** The ring pass tries evenly spaced spots and skips one
  near a via already there, so a gap between two vias 7.5 mm apart was never aimed at
  (board2 RV23.3: two bottom-edge gaps had legal sites). After the ring, it puts a via at the
  nearest legal site to each gap's farthest point, and repeats until none fits (board2: 27
  vias, the farthest ring point 3.87 -> 2.95 mm from GND).
- **Every gap is a number.** After placing, `stitch.py` prints a `GAP` line for each stretch of
  the ring path farther than pitch / 2 from a via or a through-hole pin of the net, with its length
  and the farthest point. A report quotes those lines; "no legal site within 1.5 mm" is not a
  measurement (board2 RV22.1: four 10-12 mm stretches; a 3 mm `--reach` halved them).
- **Starved spokes** (DRC `starved_thermal`): a THT pin left one spoke into the pour, or spokes into a
  pocket with nothing else, between tracks. There is no via site, so a zone `holes` entry round the pin
  and its pocket removes the pour there; the pin reaches the net on the other layers. Each hole changes
  the fill beside it, so gate again until DRC is quiet (board2: J5 S1, J_MAIN1 21 / 23 / 27 / 29).

## `budget.py` — what a group may cost before the DESIGN has to change

A group that will not close after a few honest tries is not short of tries: a part, a rule, a pin or a
layer is in its way. So every group has an effort budget set by its size, **the tools count against it by
themselves**, and when it is spent they refuse the group until a question has been asked and the design
has moved. `budget.py`'s docstring is the manual; in short:

| tier | what puts a group in it (read from its nets, never chosen) | rounds | time |
|---|---|---|---|
| A | up to 12 nets, no BGA ball, no pair, nothing matched | 2 | 45 min |
| B | up to 40 nets, or a pair, or up to 12 balls of a BGA | 3 | 2 h |
| C | over 12 BGA balls (an escape), a matched bus (`"matched": true`), or over 40 nets | 4 | 6 h |
| any | ONE feasibility count per placement and handoff revision | — | 30 min: no answer is "it does not fit" |

- **A round** is one router run with its checks (the first, and two after a hand fix), or one `race.py`
  call of up to six variants; router runs of one group in flight together are one round, up to six. A
  group drawn by hand: its first check opens the round. **Time** is the wall-clock of the group's router
  runs, races, checks and `bgwait.py run --group` jobs.
- **What counts, with no flag to set:** `netgroup.py` (its `--timeout` is cut to what the group has left),
  `race.py`, `route.py check`, `bgwait.py run --group G` (one round and its time, however many scripts
  it runs; a router run inside it is not charged twice; six to a round), `bgwait.py run --group G
  --count` and `prelayout.py`'s `count` (the feasibility count, ended at 30 min). `bgwait.py run --group
  G --gate` is a job that only CHECKS the board (a DRC diff, a preflight, a dry run stopped before the
  race): its time is the group's, its round is not, while it runs no router or race and ends within 5
  min (`GATE_S`); past that it is a round. A search of your own (a count script, a site solver) is never
  a gate. (board1 NORTHGAP r8, 2026-11-24: two DRC jobs of 12 s and 19 s and a 7 s dry run were charged
  three of its four rounds; `tools/route/test_budget_gate.py` is the proof.) On a scratch copy
  inside the board folder too: it is charged to the board. **Never counted or refused:** `apply`, `rip`,
  `audit`, `status`, `render`.
- **Which group a run belongs to is decided by its nets, not its name**: `routing/groups.json`'s entry
  that holds at least half of them, else the ledger group that does. A new spec name, a new seed or
  `--only` half of the nets is the same group and the same budget.
- **`route.py budget`** prints every group (tier, rounds, time, nets on copper, state) and the hours of
  background jobs charged to no group; `route.py budget <group>` its record event by event;
  `route.py budget routing/X_spec.json` the tier a spec would get; `route.py budget <group> --worker
  "<task>"` logs a worker for the group and prints the two lines its brief must carry.
  `route.py budget routing/X_spec.json --before "N: what"` enters N rounds the group was given before the
  ledger existed (a board that takes the budget up mid-routing); it only ever adds.
- **`BUDGET SPENT` (exit 76)** is not an error to work round. What is left is the terms count, its
  table, and the wall:

      python3 tools/bgwait.py run --group <group> --terms --name <variant> -- <one variant on a scratch copy>
      python3 tools/route/budget.py terms <group>          # routing/terms/<group>.json: TERMS OK, or what it lacks
      tools/kpy tools/route/route.py wall <group> --blocks "<the parts, vias, nets in the way, with numbers>"

  The wall writes the next `## Qn — WALL <group>` into `QUESTIONS.md` from the ledger (every round and
  how it ended) and from the table, and prints the ring line. Then the next group. **Declare the wall
  before the budget is spent when a round has shown the cause**: the budget is a ceiling, not a target.
- **The wall carries its count** (budget.py's section of that name has the table's format). The changes a
  wall offers are rows that CLOSED on a scratch copy, and the count starts at the loose end: the
  **ceiling** -- every term that could move, moved at once -- and, when that closes, one variant per term
  (the term alone, or every term but it), all side by side in the group's terms window (20 / 45 / 90 min
  by tier, on the ledger, no round). `route.py wall` refuses without the table for a tier B or C group,
  and for any group's second wall; with it, it takes no `--change`. A ceiling that does not close is a
  wall titled `DOES NOT FIT`: no routing term is left, the question is the placement or the stack-up, and
  it is the user's at once.
- **A variant that moves another group's reserve is scored with that group's gate too**: the exit lanes
  (`exitlane.py`), a corridor kept for a group not yet routed. A converged plan that fails it does not
  close for free. Run those gates on the race's raw plan BEFORE a polish: a polish to CHECK PASS cannot
  fix copper that sits in another group's room. TRAP (board1 NORTHGAP r190, 2026-11-25): R2own moved
  the exit lanes and the eMMC reserve, converged, and was polished to CHECK PASS in 16 min. Only then
  did exitlane U1 fail (8 north balls: ENET_MDC/MDIO, BOOT_CFG13/15, RGMII_TD1) and R_EMMC's corridor
  fail (19 items), which needed a terms count with the reserves kept.
  TRAP (board1, 2026-11-23 and 24): 28 walls on five groups in 32 hours. Each wall named changes nobody
  had counted; each answer granted one; the next rounds showed it was not enough. One group of 33 nets
  reached its tenth wall with 13.5 h of tool time against a 6 h budget and no net on copper; four of its
  answers were "no new term" -- the wall had been the only way to get another budget -- and the term
  counts that were run sat outside every budget (82 ungrouped jobs, 5.7 h).
  WORKED: nothing yet -- this is the rule that replaces it; the proof is `tools/route/test_budget_terms.py`.
- **A closing row says how it is known**: `"proof": "check"` (the variant's plan passed `route.py check` on
  the copy), `"router"` (every net routed, the plan not through the gate) or `"count"` (a counting model,
  no plan). The wall prints the level in its table and calls anything but `check` A LEAD: the reopened
  group owes its gated plan before copper. **A count runner scores the router and the gate apart** --
  `converged`, `nets_left` and `check` are three fields of a variant's result, and the table is built
  from them, never from one `LEFT n` line.
  TRAP (board1, 2026-11-24): a runner wrote `LEFT 33` for a ceiling whose every net had routed -- 33 was
  the raw plan's check findings, read as nets, the makings of a `DOES NOT FIT` wall for a group that
  routes; another table marked channel counts on a fixed state `closes: true` and the wall called them
  the copper once granted. WORKED: `tools/route/test_budget_terms.py` (the two proof cases); board1's
  Q126 was reprinted with both closing rows as leads, and its answer asked for the gated plan.
- **What the first pass of a count needs, beside the ceiling** (board1 NORTHGAP, 2026-11-24: nine terms,
  27 variants in one 90-minute window; what the first pass missed was found in its last half hour):
  - **Every term ALONE, beside every term left out.** The negotiated router is not monotonic inside a
    window: one placement term alone converged in 541 s while three supersets of it were cut at 900 s
    two to six nets short, and the all-but-it rows could not show it. The smallest closing set is the
    row a wall offers first -- or the row that needs no wall at all.
  - **A BLOCKED variant is read before it is scored.** `BLOCKED SOURCE ... walled by <nets>' fixed copper
    (prelaid, or an earlier leg of this run)` on a net's SECOND leg is the spec's leg order, not the
    term: two variants read "necessary" for that reason. One leg of a net per stage (two at one stage
    are refused), so the re-stage is: the multi-leg nets' first legs alone at stage 0, every other leg
    together at stage 1 -- and the variant is counted again before the table calls the term necessary.
  - **A term's finer form is read from the plans that converged** (which nets use the opened layer, and
    for how long), not from the nets left without it: the nets left are where the search stopped, not
    what the term carried. Print the layer use of every converged plan before building a second pass.
  - **Set-up gates before the queue, two variants a job.** A dry check started while four variants hold
    the memory runs threefold slower; past the gate's 5 minutes it is charged as a round.
  - **A term the runner cannot move is still named**: the wall's text lists it as uncounted.
  - **A `count` row is built as a plan and put through `planclr` before it is offered** (seconds, no
    round): a counting model tests what its author thought of. TRAP (board1 R1, Q126 / D136,
    2026-11-24): a via-site model tested each signal via against the other vias and never against
    the pads beside it; its closing row stood three vias on resistors' pads, the wall offered it,
    concept granted it, and the first worker to lay it found the pads in four seconds. WAY AROUND:
    the row's state -> a plan (vias and rips only) -> `planclr` on a scratch copy; a row with a
    finding is `closes: null` with the finding in its note, and the model gets the missing test
    before the next count. PROOF: planclr's item list is empty for the row's plan.
- **A closing row whose only term is a part's place is not a wall yet.** A lifted part is an upper bound,
  and where the handoff lets a part move inside its own rule the move is the group's: list what the
  converged plan lays over the part's old place, look for a site inside the part's rule that the plan
  leaves free (then no router run at all), else race a few gated placements in the remaining round.
  The wall is for the part that finds no place, and its table is already counted.
- **A wall closes its group to every job** until a later handoff revision answers it (`budget.py`
  refuses a gate too): a lead polished to CHECK PASS is polished before the wall is written or after
  its answer, never beside it.
- **DOES NOT FIT is read from the ceiling's knots, not from its `LEFT n`.** Before a wall whose
  ceiling does not close, read that variant's `knots.txt`: a knot whose line reads `OPEN here` on any
  layer, or `via here: no term shuts it`, is a crossing with a free layer, hand-laid on the partial
  (one net changes layer once at the knot) and gated (planclr, the copper gates, `route.py check`:
  gates, not a round). Those two lines judge the knot against TERMS only, never copper: before the
  hand-lay is owed (and before a wall names the knot), test the via sites within about 1.6 mm of the
  crossing against every copper item on every layer, for each net's hop (board1
  `tmpWork/NORTHGAP/d138/knotsites.py`); a knot with no copper-clear site on either side is shut by
  the copper that blocks it, and that copper's owner (a net's own route, another group's polygon) is
  the term. DOES NOT FIT is shown only when every knot of the last partial has each other
  layer and its via shut by a NAMED term, with the plan's own terms (each net's layers, exit lanes,
  reserves) moved too. TRAP (board1 NORTHGAP, Q128 / D138, 2026-11-25): the ceiling plateaued with 4
  nets in two knots, each with a free layer and an unshut via; the wall said DOES NOT FIT and the
  answer was to hand-lay two layer changes -- one round trip lost.
- **The table says what stops its ceiling, and a part that stops it is a term.** A table with no
  closing row whose ceiling is `"closes": false` carries on that row `"blocked_by": [{"what": "pad
  C12.1 over the lane end (3.2, 4.1)", "part": "C12"}, {"what": "the via site reserved at (1.0, 2.0)",
  "part": null}]`, as measured; `budget.py terms` and `wall` refuse it when a part named there is in no
  term's text and the table has no `"fixed": {"C12": "<the handoff rule that keeps it there>"}` for it --
  a part the handoff lets move is a placement term, counted in the ceiling. A count its window cut is
  `"closes": null`, never false: its wall is titled `NO ANSWER IN ITS WINDOW`, shows no misfit, and is
  answered with the count that is missing. TRAP (board1 R1, Q132, 2026-11-25): an eighth wall titled
  DOES NOT FIT, pad C100.1 named as what shut a lane and C100 in none of the seven terms (the board's own
  notes read "what would free it: C100 re-sited (not counted)"), the ceiling `false` with "the F race was
  stopped by the window" in its note -- one more round trip, no term.
- **A clear site is one the hop can REACH, not one inside a radius.** Before a hand-lay or a lead
  that moves the group's own via is briefed, count the sites with every knot net's partial freed and
  trace each hop from its net's copper to the site on the hop layer; a site the holder needs is not
  free to another net. TRAP (board1 NORTHGAP r192, 2026-11-25): a radius test offered three leads
  (a pocket 'held only by the group's own via'); the hand-lay worker found 7 sites for 8 hops, the
  pocket fenced off on its layer, and no lead closed -- one worker spent (`leads/cu.py sites`).
- **A terms pool stops the rest only on the ceiling's knots, never on a cut search.** A pool that runs
  the variants in memory slots and ends the count when the ceiling 'does not close' must read WHY: a
  ceiling cut at its limit, or one whose knots have a layer OPEN, is not DOES NOT FIT -- its knots are
  hand-laid as gates and the other variants still run. TRAP (board1 NORTHGAP r192, 2026-11-25): CEIL
  was cut at 1200 s with one knot (three layers OPEN); the pool killed 10 variants as DOES NOT FIT and
  they were re-run in what was left of the window.
- **A closing row the handoff already allows is laid, not asked: `budget.py lay <group>`.** A term the
  handoff already lets the group move carries `"handoff_allows"` (the rule that lets it) in the terms
  table; when a spent group's table has a row that closes (`proof` check or router) moving only such
  terms, `lay` puts it on the ledger and the epoch gets 3 more checks (LAY_CHECKS) to gate it -- its
  reserve gates and a hand fix of what they find -- and no router run, race or count. Once an epoch.
  TRAP (board1 NORTHGAP r192, 2026-11-25): T2, the group's own net re-laid, closed by hand after the
  round's three checks were spent, and its reserve gates (planclr, the eMMC box) were refused at the door.
- **A reopen is not a refill.** A reopened group gets half of its tier's budget after its first wall and
  a quarter after every later one (never less than one round and 15 min): the wall's table holds a plan
  that closed, so the new epoch re-proves and lays it. The share is on the ledger's reopen event; an
  epoch opened before the rule keeps what it had.
- **It reopens only when the input changes**: a handoff revision after the wall answers its `Qn`, or one
  of the group's own parts moves on the board. A second wall of one group is a design problem: concept
  answers it alone only when no offered change adds a term to the design, else the user decides. A group with
  every net on copper is finished; later work on it (a review's fix) is a new budget by itself.
- The ledger is `<board>/routing/ledger.json`, written by the tools under a lock and quoted by the wall:
  never edited, never copied back from a scratch copy. A run on a scratch copy is charged to the real
  board wherever the copy is kept: inside the board folder by its path, outside `kicad_project/` (a cache
  folder) by the board's own file name in it and the session's `HW_BOARD`. `HW_BUDGET_IN` belongs to `race.py` and
  `prelayout.py`; set by hand it is a skipped gate.
- **Before asking for a part to move, compare the via map with it moved -- no router needed.** TRAP (board1
  R_PMIC Q101/D127, 2026-11-23): the wall named R60 as what shut a via site; concept granted the move (rung 7),
  and the map was the same before and after -- R60's pad ended 0.35 mm short of the row; a pour, a via and
  another group's diagonal set it. WAY AROUND: a no-cost probe (netgroup set-up only, or sites.py) on a copy with
  the part moved, its site map diffed against the unmoved one; name a part in a wall only when the diff opens a cell.
  PROOF: `diff probe_before/map.txt probe_after/map.txt` lists the cells the move opens.
- **Before laying a BGA's channel vias, test their topology, not only DRC.** TRAP (board1 R1a, 2026-11-23): 45
  channel vias passed every gate and went on the board. The next sub-group found CK_A's pair vias inside CK_B's
  span on the same layer, 0.256 mm apart where a line needs 0.345 mm. That shuts one pair inside the other: +6.6 mm
  of skew against a 1 ps match. DRC, pretest and audit cannot see it. WAY AROUND: in the via-site solver, for every
  two pairs routed on one layer, either one's vias stay outside the other's span, or a lane (width + 2 x clearance)
  fits between them. Run the same test on any applied via set before the tracks are planned. PROOF: the test
  flags exactly the shut pair on the applied set.
- **Before a spec is written for the run between two BGAs, count both ends without a router: the fan-in's open
  ends with the area-clear rule, the far BGA's via sites with a grid.** TRAP (board1 R1, 2026-11-26): a gated
  fan-in left 25 lines open on its edge behind a 45-degree hook. Continued east, 18 of them were 0.117 from the
  neighbour's hook where the area rule wants 0.2032, and four balls at the far BGA had no fan-out via site
  because the group's own inner lines held the lanes. Both were found by two geometric counts in minutes, with
  the group's one round unspent; a router would have spent it at iteration 1. So: (1) a fan-in plan ends every
  open line STRAIGHT at its edge, for at least the length and at the pitch the next piece needs, and its gate
  includes that count (every end: continues / needs a via site / no site); (2) every bare ball at the far end
  gets its site counted on every layer the via crosses before any spec names the net; (3) the list of open nets
  skips pour nets and sees an end that is a joint of two segments. Prove it: the count's file reads N / 0 / 0 on
  the copy the spec is written for.
- **Any `netgroup.py` run on a copy of a budgeted group books a round -- a 20 s smoke test of one net too.** A worker
  tried a new spec form on six nets alone as an ungrouped `bgwait` job; `netgroup.py` booked `run [round] 18 s` and the
  group's only granted round was gone before any negotiation (board1 R1, 2026-11-27). A spec-form smoke test goes INSIDE
  the pre-check's `--group G --terms` job (the pre-check script takes a variant and net names), never beside it; say so
  in the brief of every worker that builds specs. Proof: `budget.py --board B G` before and after reads the same rounds.
- **An alone pre-check that exits 0 for a line with a PINNED via is not reach, and a pinned site is assigned by reach, not
  by position.** A site list from a capacity count (legal on every layer, a ground via near) says nothing about whether
  the line's far end gets there: of 23 counted sites six were reached by no line from the far BGA on any inner layer, and
  ten pinned lines "routed alone" by paying for 1-4 extra vias (the far leg's F.Cu avoid is a price, not a wall). Before
  pinning: a flood per line from each end on each of its layers with the OTHER sites standing, a matching over that
  table, and after the pre-check a count of each alone plan's vias (one per pinned line). And a line whose far via opens
  on ONE layer has one way out: take it from a free variant's alone plan and keep every pinned site off it -- the
  failure reads as that free line's own BLOCKED SOURCE (board1 R1, 2026-11-27). Pinning did not help the round: the two
  pinned variants ended with twice the overlap of the free ones.
- **After a round that closes nothing, read WHERE before anything else: the partial plan's `contested` cells by layer and
  x band.** Six variants, 29-40 iterations each, every net overlapping, flat from iteration 10 -- and 65-77 % of the
  cells on F.Cu in all six: the lines crossed one another on the one layer they share on the way to scattered via
  places. That is ten lines of python on `*.plan.partial.json` and it names the term to count next (here: where a via
  may stand), which six more variants on the same terms do not (board1 R1, 2026-11-27).
- **A greedy sweep of shortest runs is not a count of lines that keep their order.** Asked how many of 25 lines reach a
  via place on one layer without crossing a neighbour, 36 sweeps gave 3-9 on the same terms and 10-20 at the ceiling:
  each shortest run shut the next line's end, and the only upper bound built (one via per block, a matching) read 25 in
  every row -- 45 minutes and a table that decided nothing (board1 R1, 2026-11-27). Before believing such a count, print
  the spread between sweep orders; a count of order-keeping runs needs runs that HUG (river routing from both walls) or
  a DP in the lines' order, and a bound that carries the order.
- **Build the bound that carries the order BEFORE any sweep.** For "how many of N order-keeping lines reach a via place",
  vertex-disjoint paths over blocks no wider than the run pitch (a flow from the lines' ends to the legal sites; 40 lines
  of python, 2 s a row) decided three of five rows that 70 sweeps and a matching bound could not: 18 of 25 on the
  handoff's terms is DOES NOT FIT whatever a sweep finds (board1 R1, 2026-11-27). It errs upward (any line may take any
  site), so a row is closed only by a set laid whole and verified, and a row whose lower and upper differ is UNDECIDED,
  written `"closes": null`. For the lower bound run both hugs -- a wall follower (best in an open channel) and a
  wall-biased Dijkstra (best where sites are scarce) -- and verify pairs with the SAME pitch rule the laying used, or the
  verify fails good runs at the column where the rule changes. Print the min cut's place: it names the wall to ask about.
- **`endpairs.py`: two ends of one negotiation closer than a track and its clearance -- before the pre-check, before
  any round.** `tools/kpy tools/route/endpairs.py --board <board dir> routing/SPEC.json ...` (exit 1, one `INHERENT`
  line a pair). TRAP (board1 R1, 2026-11-27): 25 fan-in lines ended on the east edge of a relaxed-clearance area at
  pitch 0.321-0.323 mm, legal inside it (0.1016) and 0.009 short of 0.128 + 0.2032 outside it; every net ALONE
  passed the pre-check in four variants (100 of 100), and the round -- six variants, 55 min, 6 cores -- ended with 25
  of 25 nets overlapping in each, flat from iteration 20: 14 pairs could never both continue, whatever the
  layers. An alone-check, a per-end area-clear count and a via-site count are all blind to it; only the pair is
  not. So: (1) a fan-in that ends at an area's edge arrives there at the pitch of the terms OUTSIDE it, rounded up
  to the router's grid -- spread inside the area, where it is cheap; (2) `endpairs.py` passes before the pre-check
  is started; (3) a negotiation whose net count never falls and whose cells are flat for 15 iterations is stopped
  and read (`contested` of the partial: a net whose every contested cell lies within 0.5 mm of its own end is
  inherent, not congested), not waited out; (4) the spec's width is the class's on EACH layer (a 50 ohm line is
  0.128 outside and 0.1016 inside on this stack-up; one width for all layers taxed the inner ones 8 %). Prove
  it: `ENDPAIRS PASS` on every variant's spec. It also refuses a `via_sep` below via pad + clearance (the same
  specs: 0.5 for 0.4 pads at 0.2032 -- DRC wants centres 0.6032, and no two vias of different nets on the board
  stood closer than 0.64): a round that had closed would have failed its check on the vias. Two sources that
  are both standing vias are not judged (the board's DRC did). AFTER the ends are re-laid, three more things the
  same round taught: `route.py check` passes an open end whose 0.128 continuation outside the area would foul a
  neighbour's in-area 45 -- count every end's continuation (an edge count) on the applied copy, not only the
  check; a via-site count is blind to a site standing in a NEIGHBOUR's exit (13 sites by count, 10 clear of the
  other lines' first 0.6 mm) -- subtract those before the count is held against the lines; and when the last
  pairs will not spread, read the class's rule before asking: a rule typed `(min 0.12) (opt 0.128)` already lets
  the few lines of one tight run leave at its minimum (a spec field with a box and its own width).
- **The pre-check runs every net alone in EVERY variant that differs in layers, and a via site is not a way
  out.** TRAP (board1 R1, 2026-11-27): seven fan-out vias had been sited at the far BGA by a site count; the
  pre-check then found two of those nets shut inside the BGA's field on every layer (25 000 cells reached, none
  of the channel), a third open only on the layers of some variants, and four spec defects -- in 67 s and 6
  min, as `--terms` jobs, with the round unspent. So: (1) a fan-out via is accepted when a flood from it, on
  the layers the net may take and at the field's terms, reaches the channel -- not when the via fits; (2) "the
  tightest variant covers the rest" holds only when a net that fails it is given its exception and tested
  there too: loop the pre-check over the variants (a variant that only adds via room is covered); (3) every
  layer any net of a spec may take is in the spec's `layers` and the variant is the per-net `layers` -- a
  net-level layer the grid lacks is a KeyError in maze, not a message; (4) a source is a grid point: where a
  net's via stands off its run's end, the run's end is the source. Prove it: `precheck/PASS` written by the
  job itself, every net x every variant `exit 0`.
- **A replay script that rips and re-lays a group carries no number from the stage before it, and a re-laid
  piece is ripped by its ends of TODAY.** TRAPS (board1 R1, 2026-11-26, each cost one rehearsal or one count):
  (1) a read-back grep typed as "41 of 41" failed the next stage's rehearsal when the group had 43 -- read the
  total from the plan, compare closed against it; (2) `route.py rip` overwrites `routing/<group>.ripped.json` and
  renames the applied manifest, so a script that rips backs both up and restores both on any failure; (3) a rip
  names a whole segment by both ends: once a stage re-lays a line with one end moved, a later plan written
  against the old ends matches nothing -- list the group's segments from the board, not from an earlier plan;
  (4) a via-site search on a 0.02 mm grid missed the only site, which a 0.01 mm grid found: search at the
  board's own snap before a pocket is called shut; (5) two parallel 45-degree pieces stepped 0.2000 in y stand
  0.1414 centre to centre, not 0.2000 -- a clearance along a diagonal is counted square to it (0.2032 square is
  0.2874 in y); (6) two group names one character apart (`R1_u500fan`, `R1u500fan`) are two manifests and one
  typo: check a new name against `routing/*.applied.json` first. Prove it: the script's rehearsal on a fresh
  copy of today's board ends with its own OK line and no net read open that was closed.
- **A chain of plans is replayed WHOLE on a fresh copy of today's board before it is called gated -- base plan
  and the script's last steps included.** TRAP (board1 R1 fan-in, 2026-11-26): three plans passed `route.py check`
  on a scratch copy whose base was an earlier scratch board (old pieces already ripped by a base plan laid with a
  private script). Replayed on a copy of the real board the chain failed twice: without the base plan a kept via
  lost its GND stitch (a rule finding), and with it the base brought seven clearance hits at the far BGA -- its
  dog-bone vias stood on copper the real board had gained since. The fix was a base cut down to what the chain
  itself needs (11 via rips, one via), which also left every connected net connected. So: (1) the replay script
  starts by refusing any board whose sha is not the one it was written for, and applies EVERY plan through
  `route.py apply`, the base first; (2) a base plan keeps only the rips and re-sites the later plans need -- drop
  an item, rehearse, keep it only if a later check names it; (3) the script's closing steps (drc diff, dangle
  diff, read-back) are run on that rehearsal, not first on the real board: a plain DRC diff does not read a
  plan's `accept` list and fails on the open ends the plan's own check accepted -- pass those one by one (net,
  layer, position) and nothing else; (4) "which net opened" is read from the boards' connectivity, never from two
  DRC reports: `place.py drc --out` caps the unconnected list at 499 and a half-routed board is past it. Prove
  it: the rehearsal's log ends with the script's own OK line on a copy whose starting sha is the real board's.
- **Gate a plan on a copy of TODAY's board, not the copy it was routed on.** TRAP (board1 R1 round 8,
  2026-11-23): the row passed every gate on its round's base copy, made hours before; the real board had since
  taken 304 tracks of other groups and moved 5 parts, none of which that gate had seen. WAY AROUND: diff the base
  copy against the real board (footprint positions, tracks by net, zones) before the apply; when they differ, cp -r
  the real board, redo the copy's own terms on it (a redrawn rule area), and run the gate again there. PROOF: the
  re-gate's DRC counts equal the real board's pre-apply baseline.
- **A placement made on a scratch copy never earns a new `--count`.** The count's signature is the handoff
  revision plus the REAL board's part positions (budget.py), so moving parts on a copy keeps the old one used; a new
  revision does earn one. Plan the counts a revision allows before spending one on a what-if.
- **A round that measured nothing is not a round** (budget.py, `FREE` = 3 an epoch; the time still counts).
  A `netgroup.py` run that ends before its first negotiation -- `BLOCKED at this leg` at leg 1, a stop in
  the set-up -- closes its ledger event with `free`, and `route.py budget <group>` shows it as `[never
  began]`; a run that started beside others is freed only if it ran alone. A `route.py check` refused as
  stale (exit 5) inside a `bgwait.py --group` job is recorded the same way, so the job is not charged the
  round a job with neither run nor check costs. MISSING (board1, 2026-11-23): R_PMIC's one granted round
  ended in 41 s on two walled-in sources and NORTHGAP's on a spec defect; both were charged, and both
  groups went back to the user as a second wall with nothing measured.
- **The wall's `Layers:` line.** Every router run records the copper layers its spec opened and the board's
  others; `route.py wall` prints both. A layer no round opened needs its reason -- the handoff row that
  closes it to the group -- or it is a `layer:` change, and the cheapest one to count before the wall goes
  out (board1 R_PMIC: two walls on three layers, with four more that published terms allowed).
- **Traps met taking the budget up mid-routing** (board1, 2026-11-23):
  `route.py wall` takes its `## Qn` title from `--blocks` cut at ~120 characters, so a long `--blocks`
  (the numbers belong there) leaves a cut title, and the MANIFEST hashes are not written. Retitle the
  entry in one line and add the hashes to its `Working from:` before you ring.
  `route.py budget` reads `routing/groups.json` from the real board only, so a draft can't be proven
  with it on a copy; prove the draft with `prelayout.py --no-count` on the copy, then install it.
  A `route.py check` refused as stale (exit 5, nothing checked) was charged a round inside a job; it is
  now recorded as `free` (above). Refresh the scratch copy and the plan's origin first all the same.

## Background runs: `tools/bgwait.py` — started and waited on by pid, with a time limit

A run that takes minutes is started in the background and waited on with `tools/bgwait.py` (board-free,
plain `python3`; its module docstring is the manual). It is not a routing tool -- any long command of
either session goes through it -- but the router is where the waiting happens:

    python3 tools/bgwait.py run --name R2_a -- tools/kpy tools/route/netgroup.py --board board1 routing/R2_a.json --timeout 900
    python3 tools/bgwait.py wait R2_a R2_b --timeout 100     # 0 all ended with exit 0 | 1 one failed | 124 still running
    python3 tools/bgwait.py status                           # every job of this session, without waiting
    python3 tools/bgwait.py kill R2_b                        # its whole process group
    python3 tools/bgwait.py run --name emmc_c1 --group routing/R_emmc_spec.json -- tools/kpy build/emmc_try.py   # charged to the group
    python3 tools/bgwait.py run --name emmc_fit --group R_emmc --count -- tools/kpy build/emmc_sites.py         # its ONE feasibility count: ended at 30 min
    python3 tools/bgwait.py run --name emmc_drc --group R_emmc --gate -- kicad-cli pcb drc --refill-zones ...    # a check: its time, no round (up to 5 min)

- **A job that works for a routing group says so: `--group <id | its spec | its plan>`.** It is then one
  round of that group's effort budget and its wall-clock time (`budget.py`, above), refused with exit 76
  when the group is spent; `--count` makes it the group's one feasibility count, ended at 30 minutes;
  `--gate` a job that only checks (DRC, preflight, dry run): its time, no round, up to 5 minutes.
  A job of no group still runs, and is listed by `route.py budget` as time outside every group: a
  what-if for one group started without `--group` is the day that produced the budget.
- **`run` makes the job a (pid, start time) pair with a recorded exit code.** The command runs detached in
  its own session, stdout and stderr in `<jobs>/NAME.log` (or `--log`), Python unbuffered; a wrapper
  records the exit code in `<jobs>/NAME.json`, so `wait` reports it long after the process is gone. The
  job folder is git-ignored and per session: `kicad_project/bgwait.tmp/<boardN>/` for a board session,
  `out/bgwait/` for the concept session, the system temp folder when neither can be written.
- **Unbuffered Python is not enough through a pipe.** `tools/kpy x.py | grep -v NOISE` in a job's script
  buffers grep's output, so the job's log stays 0 B until the job ends and `wait` shows nothing (board1
  NORTHGAP r6, 2026-11-24: a 23-minute search showed an empty log). Put `grep --line-buffered` (or
  `stdbuf -oL`) in every pipe that feeds the log. Proof: `run --name p -- bash -c 'for i in 1 2 3; do echo
  x$i; sleep 1; done | grep x'` reads 0 B after 3 s; the same with `--line-buffered` reads its lines.
- **`wait` always has a limit and always comes back with evidence.** `--timeout` defaults to 100 s (inside
  the Bash tool's default 120 s; the cap is 3600 s -- give the Bash call a longer timeout too). It returns
  the moment ALL jobs ended, or at the limit with exit 124, and each time prints per job: state, exit code,
  elapsed time, CPU seconds, CPU in the last interval (`0.0 s CPU in the last 60 s: idle or hung`) and the
  log's last lines with the log's age. Read them before waiting again: a job at 0 CPU will not end by
  itself. A bare PID (a process started some other way) is waited on too; only its exit code is unknown.
- **A wait by NAME is refused, by a hook.** `while pgrep -f "netgroup.py ... R2_emmc_"; do sleep 20; done`
  matched its own shell and sat 4 h 52 min at 0 CPU on three runs that had ended within 3 minutes. The
  PreToolUse hook `tools/session/bashguard.py` blocks a `while` / `until` loop that polls `pgrep` with a
  pattern, `ps | grep` or `pidof` and sleeps -- the bracket form `pgrep -f '[n]etgroup.py'` included: it
  still ends on a name any other process can hold, and still has no limit. A one-shot `pgrep`, and a loop
  on a pid (`kill -0 $pid`, `tail --pid=$pid`) or on a file, pass. The hook is declared in both
  sessions' `.claude/settings.json`, beside the wall.
- **Several specs of one group at once are a race, not three background runs:** `race.py`, below.
- **`--timeout` does not lengthen a round: `--iters` (default 300) ends it first.** TRAP (board1 R_EMMC/D126,
  2026-11-23): a round given `--timeout 2400` stopped at iteration 300 after 2093 s, and the next was briefed with a
  longer timeout to "give it real CPU". WAY AROUND: read the last `iteration N:` line against `--iters`. A run that
  ends at the cap with pressure still climbing is not converging: change the spec (layers, sites, a rung), not
  the clock. Raise `--iters` only where the overlap count was still falling. PROOF: `grep "iteration 300" netgroup.log`.
- **Memory runs out before cores do, and it kills.** On board1's 30 GB machine six router runs forking 4-12
  workers each beside a 24-worker solve took the board session, the concept session and the editor with
  them (2026-11-16, the kernel's OOM killer). Now: `run` refuses a job while less than the floor is
  available (exit 75; 15 % of the machine, 3 GB at least, `HW_MEM_FLOOR_GB`); every job runs at
  `oom_score_adj` 900, so the kernel takes a job before a session; `wait` and `status` show each job's
  resident memory; an ended job records `peak_gb`, which `cores` prints per script with how many such runs
  the machine holds. `par.workers()` (every `pmap`) and `par.cmap` cap their workers to what fits in memory,
  `HW_JOBS` or not. Size `--seeds` and `HW_JOBS` from `cores`' peak column, not from the core count.
- **`cores` says how much of the machine the jobs used**: `python3 tools/bgwait.py cores [--since
  YYYY-MM-DD]` prints, per script, the runs, the wall hours, the core-hours, the cores a run used and
  how many ran at once, then the machine's average -- and names the scripts that left most of it idle.
  Read it at the end of a routing day: board1's said 12.8 of 24 cores busy over 15 h, a router at one
  core a run and 2.8 runs at once.
- **A running job reads its bash script as it goes: never edit it.** bash reads a script in pieces, so an
  edit lands mid-run and the job runs garbage. Change a copy (`run2.sh`) and start that. (board1 R1 r197.)

## An integer program: `tools/cpsat_milp.py` -- scipy's `milp` call, on every core

A fanout, an assignment of nets to sites or lanes, an order: a board's `build/` script that writes ONE integer
program (all variables integer) imports the solver from here instead of scipy:

    sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
    from cpsat_milp import milp                    # was: from scipy.optimize import milp
    from scipy.optimize import LinearConstraint, Bounds

The call, the result and scipy's status codes are unchanged; the search is Google OR-Tools' CP-SAT, a
portfolio of workers on every core (`HW_JOBS=N` to share the machine). scipy's HiGHS searches one core,
and is slowest where these models live -- 0/1 variables and big-M order rows -- above all when the answer
is that there is none. Board1's U500 fanout (2,200-2,400 binaries, 14,000-16,000 rows), eight cases:
HiGHS took 459 s to prove one infeasible, 1,193 s to an optimum, and 3,000 s to its time limit with
nothing found for three; CP-SAT took 5-24 s for each, every answer equal to HiGHS's where HiGHS had one.

- **The same answer, not an approximation**: each row is scaled by a power of ten to integers and its
  bounds rounded inward, every answer is re-checked against the original rows, and "optimal" is HiGHS's
  1e-4 relative gap (`mip_rel_gap: 0` for the proof). A coefficient no power of ten carries (1/3) is
  refused, not guessed.
- **A continuous variable goes to HiGHS** (CP-SAT has none), as does everything when OR-Tools is not
  installed in the interpreter (`./setup.sh install` at the root), and `HW_MILP=highs` forces it -- one
  line on stderr says which.
- ⚠ **Several workers, several equal answers**: two runs may return two different fanouts of the same
  cost (16-22 of 91 balls' choices differed). `HW_JOBS=1` is deterministic and was still 10-30 times
  faster than HiGHS. The `--json` a run writes is the record to draw from -- never re-solve to reproduce.
- A model that HiGHS needed `--half` or a short `--time` to finish is worth solving whole again: the limit
  was HiGHS's.
- **Beside another solve it takes a share, not every core**: the default worker count is `par.grant()`
  (below), and the solve holds a claim while it runs.

## A loop that solves, checks and cuts: where its hours go, and three tools for them

A model whose answer a slower tool must confirm (a fanout, then the strips routed) becomes a loop: solve,
check the answer, turn each failure into a cut, solve again. Board1's ran 266 rounds in 14.5 h on a
24-core machine that was half idle (12.8 cores busy). Measured, round by round:

- **64 % was the solve, and every solve ended at its time limit.** CP-SAT found an answer in seconds and
  kept improving it slowly until the 180 s were up; the optimum was never proven, so the limit WAS the
  solve time, every round. `cpsat_milp`'s options for a solve that is one of many:
  `hint` (the last round's `res.x`: after one more cut it is nearly feasible), `stall` (seconds without
  a better answer, then stop with the best) and `first` (any answer). On board1's model (18,760 variables,
  51,754 rows) `hint` + `stall: 15` ended a round's solve in 85 s where the limit took 180, for 27
  channel vias against 28 -- one sample each; CP-SAT's answers differ run to run. A stall too short costs
  quality (`stall: 15` with no hint: 28 vias against 23 at the limit), and a worse answer fails more
  checks: set it from the solve's own log (`disp`), where the improvements thin out. Infeasible is still
  PROVEN under all three -- the loop's `INFEASIBLE` keeps meaning what it says.
- **36 % was the checks, on one core.** Three strips checked one after another, then a deletion filter
  for each failing strip: one router run per net, up to 18 minutes a round. Two tools:
  `tools/minset.py` -- `minimal(items, fails)` returns the SAME smallest failing set as the deletion
  filter, asking a wave of tests at once (985 waves where the serial filter ran 4,662 tests, on 300
  random cases); and `par.cmap(cmds)` -- independent commands (the three strips) a grant at a time,
  results in the order given. The test a filter calls must be safe to run beside itself: its own
  scratch file each call, never one shared `objs.json`.
- **Two loops at once each took every core.** `par.grant()` is the cores no other job has claimed, and
  never less than an even share; `with par.claim(n):` holds them. `cpsat_milp`, `minset` and `cmap`
  ask and claim by themselves; a script's own pool should too. `HW_JOBS=N` still overrides.
- **Log each phase's seconds in the loop** (solve, build, each check): the split above had to be
  rebuilt from file times, and it decides which of these to reach for.
- **A check asks for a verdict: solve it without its objective.** Board1's later strip check (a mixed
  model -- real positions, binary sides -- so HiGHS on one core) had a point that fits after 1.0 s and
  then polished a "least movement" objective to its 900 s limit: the gap never under 60 %, the value
  unchanged since second 31, five times an outer iteration -- 75 of its 110 minutes, on one core of 24.
  `options={"first": True}` is honoured for HiGHS's models too (`cpsat_milp` drops the objective). Where
  the point itself goes on (positions a router takes over), solve for the verdict first and give the
  objective a short `time_limit` of its own -- and say in the log which of the two ended the solve.
- **A time limit is not a verdict.** `cpsat_milp.verdict(res)` is `"answer"`, `"infeasible"` (status 2:
  proven) or `"undecided"`; only `"infeasible"` becomes a cut. An undecided check stops the loop or is
  solved longer -- a cut built on "no answer in 900 s" removes answers that fit.
- **The same inputs are solved once.** The loop's last round, the routing script's own re-check and its
  prep each solved one strip for one answer: a result is a file passed along, or a cache keyed by the
  hash of the inputs AND of the script.
- **`bgwait.py wait` names the command that held the job**: per command the job ran, its share of the
  wall-clock, its runs and the cores it used. Read it at a new loop's first wait (`--every 0` for no
  progress lines; never `> /dev/null`): a step at one core holding most of the job is fixed before more
  rounds are run.

## `race.py` — spec variants raced as processes, one table, one winner

When a group does not converge, or converges badly, the next tries -- the other layer first, a wider
window, another seed, `--repair` -- do not depend on each other. `race.py` runs them at once:

    tools/kpy tools/route/race.py --board board1 routing/R3_a.json routing/R3_b.json "routing/R3_a.json --seed 2"
    tools/kpy tools/route/race.py --board board1 --timeout 900 a.json b.json c.json -- --repair

A variant is ONE argument: a spec, optionally followed inside the same quotes by netgroup options for that
variant alone; options after a bare `--` go to every variant. Each variant gets its own process group, its
own copy of the board folder under `<race>/<variant>/` (the system temp folder, or `--out DIR` outside
`kicad_project/`), `netgroup.py --timeout S` (default 600 s; killed at 1.5 x S + 60 s) and, when it
converged, its own `route.py check`. It prints one table -- variant, converged, iterations, route seconds,
check, new DRC, rule failures, vias, track length -- and `<race>/race.json` holds it as data.

- **Nothing is written to the real board.** Plans, partials and logs stay in `<race>/<variant>/`
  (`plan.json` with its `plan.origin.json`, `partial.json`, `netgroup.log`, `check.log`, `spec.json`);
  the board copies are deleted unless `--keep`. Under the table are the commands that take the winner
  over: `cp` its plan AND its origin record into the board (without the record `route.py` cannot tell
  that the plan is this spec's and this board's, and says so), `route.py check` it THERE (the gate of
  record is the real board's), `apply`. A variant that did not converge says why in its row: `BLOCKED`
  (netgroup exit 4, an end shut in), `STUCK` (exit 3, the same cells recycled), the timeout, or its last line.
- **The winner's order is fixed, and printed:** converged; the check ran to a verdict; fewest new DRC;
  fewest rule failures; fewest vias; shortest track length; command-line order. It is the best of what
  ran, not a pass -- the line says `CHECK PASS` or `NOT a pass`, and the exit code is 0 only for a
  converged winner that passed (1: none did; 2: usage).
- **A raced plan is the plan the same command writes on the real board**, byte for byte: the copy and the
  neighbours change nothing (`tests/test_race.py` holds three variants to the same sha as three runs one
  after another). So the last printed line -- route it there again -- is an equal way to take a winner.
- **How many at a time:** `--jobs`, else `HW_JOBS`, else one per variant up to the cores less one. Each
  child gets `HW_JOBS` = cores / jobs, so a variant with `--seeds` or `--jacobi` does not oversubscribe.
  The checks are what load the machine (two `kicad-cli` each, many threads): on the fixture three variants
  took 6.4 s raced against 12.1 s one after another (a 24-core machine busy with other work), and eight
  at a time took 18 s for 123 s of route + check summed. A copy keeps `routing/.drc_cache/`, so a board that has been checked once does not
  pay its baseline again in every variant.
- **Race variants that differ in the SPEC before variants that differ in the seed.** When every variant
  stops at the same contested cells, the group lacks capacity there and no seed will find it (`prelayout.py`,
  the partial's `contested` and `reach`); when they stop in different places, the order is the problem
  and a seed or `--repair` is a fair try.
- **A long race goes through `bgwait.py`** (`run --name R3_race -- tools/kpy tools/route/race.py ...`,
  then `wait R3_race --timeout ...`). `bgwait.py kill` -- or any SIGTERM to `race.py` -- ends every
  variant's processes with it.
- A spec whose `"plan"` is an absolute path, or leaves the board folder, is refused: a variant writes only
  into its own copy.

## A bus between two BGAs: test cheaply, use the cores, prove the chain whole (board1's U1-U500 DRAM)

One bus took three questions, four answers and a day with no copper; a measured week of it showed
where the hours went. What would have saved them:

- **A packing model of pair vias tests each pair against ITSELF.** Its candidates must keep each line
  off its own pair's other via (the pair's own gap + via radius) and off its pair line, or every
  "close" is a capacity no copper can take: board1's CSI pocket counted 5 of 5 OPTIMAL with a via
  shape (0, 0.6) -- N's via straight behind P's along the approach -- and all 15 pairs then ran N
  through P's via (-0.23 to -0.39 mm) and crossed P; every 4-of-5 row failed the same test. Before a
  build worker is sent on a closing row, run the own-pair check on it (lines through own vias, P / N
  crossing); keep only via shapes with the two vias side by side ACROSS the approach.
- **Ask the cheap question before the joint run.** Before a joint model of every net (a 3-hour solve,
  a 21-order sweep), run the variant that drops the hard constraint -- no order, one candidate net
  admitted alone, one half of the bus -- which answers in seconds and names the bind: a 5-second
  `--no-order` run showed where a 3-hour joint solve was stuck. In a router run, the same question is
  one net routed alone into the jam (with every settled net frozen) before the whole half is
  re-negotiated: if it does not close alone, no negotiation of the half will.
- **A negotiation that has stopped improving is over.** Best overlap at iteration 40 was within 0-11 % of
  the best after 121-300 iterations in ten logs, while each run waited out its 25-minute `--timeout`.
  Read the overlap curve, not the clock: `netgroup.py --plateau 40` (above) ends such a run.
- **The cores are idle while the router runs.** A negotiation is one process on one core; a chain of
  runs on one board copy is serial. Independent chains (the bus's two halves, two layers) go on
  separate copies, at once -- `race.py`, or `bgwait.py run` on two scratch copies. Never two writers on
  one copy: they clobber each other's plan and partial.
- **A solve is tied to the board it was solved on.** A model computed from the board (exits, slots,
  orders, a fanout) names the board file's hash in its input, and the script that reads it back
  refuses a model whose hash is not the board's -- one fanout was applied and reviewed, then found
  solved from an older snapshot's exits. A frozen data dump the model reads (a region's vias, tracks,
  free heights) carries the hash of the board it was dumped from, and the input refuses it on any
  other board -- one count read a region dumped before two rounds of fixes. And after a board change,
  dump again and `cmp` the model's input files before re-solving: identical inputs are the same model,
  and the old solutions stand (board1 R1: six 10-minute solves not run, every row held).
- **Prove the chain whole before part of it goes to copper.** The fanout at one end, the escape at the
  other and the channel between are one problem: closing one end's fanout on copper (and through a
  review) before the channel was shown to close at the counts it needs cost a re-solve of the whole.
  On the scratch copy, take every stage to a count -- fanout, escape, the channel's via strips at their
  real clearances -- then lay it.
- **A model's caps are checked against the board before they are believed.** Five times a count from
  the model (tracks a gap, vias a strip) was corrected by a physical check afterwards: place the vias
  and lines the model assumed (a 1-D placement of the strip is minutes) before a count goes into a
  question.
- **A new group with no ledger entry is named by its plan's path** (`bgwait.py run --group <board>/routing/G.json`):
  `--group NAME` is refused for a group the ledger does not know, and a group named by a plan the classifier
  files under the group whose nets it shares -- check which with `route.py budget` before a gate longer than
  GATE_S, or it spends that group's round (board1 NG_LINEAGE, 2026-11-26: a 6-min gate charged to R_J1N).
- **Lines that share a field are ONE negotiation, and every spec is pre-checked net by net first.** A
  sub-group laid alone walls in the ends of the rest: board1 R1 laid its 20 CA lines and walled in 6 of
  its 42 DQ ends; then pairs laid before their single-ended neighbours walled in more. Rip what walls
  them and route both together. netgroup's reach table runs only inside a charged round, so one shut-in
  end spends the round at iteration 1. Pre-check each net alone (shut ends 0) before the round; a spent
  group does that as a `--terms` job, at no round (board1 R1 epoch 16: 2 rounds, 61 s, nothing closed).
- **One round is ONE `bgwait.py run --group G` job.** Runs in flight together count as one round only inside
  one job (xargs -P, up to RACE). Separate `run` calls are separate rounds: the second is admitted past the budget
  with a warning, and its tally then gets the FIRST job's router refused (board1 R1 r201, 2026-11-26: a user-granted
  round spent with 0 s routed). An end-to-end open-net list walks `GetConnectedItems(pad)` filtered to pads;
  `GetConnectedPads` gives direct neighbours only, and the ledger's "on copper" means some copper, not closed.
- **`route.py check` does not judge pour nets, the via-to-pad gap or nfp vias: run `route.py audit` on the
  laid copy BEFORE the rehearsal** (board1 NORTHGAP r201, 2026-11-26: gate 1 passed four defects only audit
  caught). `fill_zones` fills before BuildConnectivity, so a new nfp via is filled at hole clearance only.
  `viadangle.py`'s diff keys on UUID: a re-created via reads as newly dangling -- diff by net and position.
- **A cluster model inside U1's courtyard uses the courtyard's own F rule (DRU `BGA_ESCAPE_U1`), not the
  outside gap**: at the outside gap no fan exists and every row reads as void (board1 NORTHGAP r200, 2026-11-26).
  A pcbnew via removed with `Remove()` segfaults at Python GC: keep a reference and leave with `os._exit`.
- **A via site search counts copper polygons (net-tied PCB_SHAPE) as obstacles**: a via that clears
  tracks, vias and pads can still short a polygon (board1 NORTHGAP r200, 2026-11-26: C25's site passed
  through the PMIC_LX3 polygon). A DRC diff of your own compares parity items to the expected set, not to zero.
- **A rehearsal of an apply relies on `route.py apply`'s own check; a stand-alone `route.py check` on the
  copy is charged to the plan's group by budget.py even outside the board folder and even as a plain job.**
  (board1 R1_N, 2026-11-26: three rehearsal checks spent R1's round.) `place.py drc`'s report caps a type at
  199 items: a DRC diff of your own skips capped types and counts dangling vias with viadangle.py.
- **A rung that widens a rule area is drawn by MERGING it into the area's outline, never as a second
  rectangle.** A segment spanning two outlines lies wholly in neither: `route.py check` flags it
  (area-clear) while the router's floor fields passed it. (board1 R1 r199: 26 findings at x 36.275.)
- **Parallel `netgroup.py --only` runs of one spec race on its partial's .tmp** (named after the spec's
  plan): FileNotFoundError. Give each run its own spec copy.
- **`route.py check`'s DRC diff keys on the PAIR of items, so a pre-existing violation KiCad pairs with
  other segments of the same tracks reads as NEW.** Before fixing a 'new' error on copper the plan never
  touched, look for it in the base's DRC (`.drc_cache/`) by geometry: same nets, same place. (board1 R1
  r198: In7 CA1_B x NCS0_B, already on the base, re-paired.)
- **A field box's narrow floor is the published area's outline, read from the board, never a box typed
  over it.** A rule area with a step in its edge (y 13.225 west of a corner, y 12.725 east of it) typed as
  one rectangle grants width and gap the handoff never gave; every count on it is off published terms and
  `route.py check` fails the plan only at the end. Inside the area a line keeps the area's track gap to
  another net's via too -- stamp vias with that, not the outside gap. (board1 R1: walls 10-11 counted on a
  field 0.5 mm north of the area west of x 36.275, Q135.)
- **A via moved or re-netted in a what-if keeps its net only if it is free.** pcbnew re-nets a via no
  track touches from the copper under it (a zone, a touching track) on save and on load: a DMI1_B via came
  back GND or DATA9_B. Remove that copper first, `via.SetIsFree(True)` before saving, and read the net
  back after a reload. (board1 R1 r197: two site runs void on it.)
- **An inner stage with no vias: open both layers to every net.** netgroup with each net pinned to one
  inner layer gave NO SOLUTION where both layers open converged in 4 s (board1 R1 r197, two re-sited DQ
  lines). Pin a layer only where the handoff assigns one.

## `corners.py` — the angle rule for a polyline

`fix45(pts, width, cut)` rewrites the joints of a polyline whose segments run at
multiples of 45°: a 90° joint becomes two 45° joints with a `cut`-long segment
between them (never under one track width), a 135° one three. A segment at any
other angle cannot be repaired locally — it raises, and the polyline has to be
re-drawn. `straighten(pts, width)` turns a grid router's one-cell stairs back
into diagonals, keeping the first and last headings.

- **A widening that slides a short diagonal along the next leg can leave that leg's remnant short
  before a turn of the same sense** -- the angle rule's failure moved one piece on, not fixed
  (board1 R1 l1, DRAM_NCS1_A: S, SE 0.029, E 0.0625, NE; the SE slid to one width and left E
  0.010 before the NE). Judge any such pass with `short_turns(pts, w)` (route.py check's rule on
  one polyline, collinear pieces taken as one run): a candidate that does not lower that count is
  refused. `square_turns(pts, w, fixed=...)` re-draws what is left: the short run dropped, the two
  legs met where their lines cross, that joint chamfered like `fix45` (90 -> one piece, 135 -> two,
  each one width long), growing the run a piece at a time while a leg's remnant would be short. It
  returns the input untouched when there is nothing to do (a pass placed after the others changes
  no plan that already passes); the copper moves into the corner, so `route.py check` judges it.

**Generate at 45°, do not chamfer afterwards.** A pass that cuts the corners of
a router's output also "cuts" the router's own 45° joints, into 22.5° ones.

## `view.py` and `render`

`route.py render` is the picture for the **review**: KiCad's own plot of the
top, bottom and inner layers, rasterised with PyMuPDF from `tools/.venv`
(written to `<board>/routing/renders/` unless `--out`). `view.py` is the picture
for the **work**: one window of the board, one colour per copper layer, zone
fills shaded, an optional plan (or a stalled router's `.partial.json`) on top.

- **A stalled escape is read off one picture, not off coordinates.** `--vias
  <fanout model>` draws vias that are not on the board yet (bga_decap / `fanout.py
  plan` output) with their top stubs and net names, and `--contested <netgroup log>`
  marks every contested cell the log lists. Board1's In2 escape (D64) stalled at
  three gaps each at exactly its counted capacity (3 tracks through a 1.118 mm
  diagonal, 2 through a 1 mm row) -- the bound passed them, the 0.5/12 grid did not.
  A gentler negotiation (`--pres-mul 1.03 --iters 800`) and a finer pitch (0.5/14,
  0.5/16: worse or out of the hour) did not help; changing the fanout model did
  (the bullets under `fanout.py`).

**TRAP: an escape end inside a BGA's land halo blocks every source** (board1 NORTHGAP, 2026-11-23).
Outside a field's `box`, netgroup stamps the field's lands at the spec's `padclr`, not at the area rule.
F-row escapes that ended at y 14.7-14.8, north of U1's AG row, sat inside that halo
(r + 0.2032 + w/2). Every source came back BLOCKED, naming "field U1: its pad area", which reads like an
error in the box. Two `bgwait run --group` jobs started together (one variant each) were also charged one
round each: the "in flight together" rule covers router runs, not jobs.
**WAY AROUND:** put each escape end at least land radius + padclr + w/2 from the lands. Run all variants
inside ONE job (a script that starts them side by side), not as one job each.
**PROOF:** board1 tmpWork/NORTHGAP/v_*/netgroup.log (all sources BLOCKED at y 14.7 / 14.8);
mkspec.py Y0 = 14.6.

**TRAP: a granted single round spent on a BLOCKED SOURCE** (board1 R_PMIC, Q94, 2026-11-23). The router
stopped in 41 s on the first source it could not leave. Two site vias were walled in by copper laid since the
plan last passed. Nothing was negotiated, the other nets were never tried, and the one round was charged.
**WAY AROUND:** before the router run of a round that cannot be repeated, look at every source and goal cell
against the copy's copper on the spec's layers. A cell whose flood reaches no goal is the wall: ask with that,
and do not spend the run.
**PROOF:** board1 tmpWork/R_PMIC/netgroup_r161.log (BLOCKED SOURCE, RTC_RESET_B (24.0, 25.0), a 1527-cell
pocket).

**TRAP: netgroup's padclr floors FOREIGN TRACKS too** (board1 NORTHGAP / D125, 2026-11-23). The spec's padclr
(0.2032, set for BGA lands) also held every foreign Default-class track off by 0.2032, where DRC needs 0.1016. A
lane at J1 that is legal at DRC (x 24.855-24.871 beside PDM_DATA2) read as shut, and a west corridor counted 3
lanes where DRC gives 4. **WAY AROUND:** before reading a SHUT end or a lane count as a wall, re-count the
corridor at the class clearance of the copper that bounds it. A track-only bound is the class number, not padclr.
**PROOF:** board1 tmpWork/D125_relay/probe_rmA.log, map_rmA_west.txt.

**TRAP: race.py wants each spec's plan path relative to the board folder** (board1 R_PMIC / D126, 2026-11-23). A
spec written for a plain netgroup run with an absolute `plan` path (`/home/.../tmpWork/.../R_X.json`) is refused by
race.py in a second ("plan leaves the board folder"), and the refusal is charged as a round. **WAY AROUND:** write
`plan` as `routing/...` or `tmpWork/...`, relative to the board folder, in every variant spec before the race.
**PROOF:** board1 tmpWork/R_PMIC/d126/ (the first race refused, the second ran).
