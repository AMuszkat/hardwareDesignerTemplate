# `tools/route` — routing, one reviewed group at a time

**Yours**, like `tools/place` and `tools/fp`. A board is routed in the groups
`<board>/ROUTING_PLAN.md` lists, and **every group is reviewed before the
next one starts** (the board's `REVIEW.md`; the verdict in
`handoff/15_reviews.md`). These tools make a group cheap to propose, judge and undo.
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
    tools/kpy tools/route/pour.py     --board board1 [--try NET:LAYER=zone.json] [--over 'GLOB:LAYER']
    tools/kpy tools/route/view.py     --board board1 X0 Y0 X1 Y1 OUT [--plan plan.json]

`tools/kpy` only (they import `pcbnew`). `apply` and `rip` need KiCad
**closed**; everything else reads the saved file. `--board` takes `boardN`, the
folder name or a path; left out, it is this session's board (`HW_BOARD`), and
another session's board is refused. A plan, spec or zone file is taken as
typed, else inside the board folder (`routing/R1.json`).

## The gate is KiCad's DRC, not arithmetic of ours

`check` writes the plan into a **scratch copy** of the board and runs KiCad's
own DRC on it, schematic parity on. Any violation that is not already in the
baseline fails the plan. The real board is never touched.

⚠ **One thing KiCad's DRC cannot see, and the gate checks itself: a short that
KiCad renames away.** When a board is saved, `pcbnew` gives a track or via
that touches a pad of *another* net — with no pad of its own net in the same
piece of copper — that pad's net. A GND stub landed on a +3V3 pad is saved as
+3V3: one net, no `shorting_items`, the open count unmoved. So `check`,
`apply` and `rip` read the saved file back and compare every track's and via's
net with what was written; a difference is reported as `RULE short:` and fails
the plan (`apply` restores the board).

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
| `keep-out` | `name: Class` | a track of that class touching the `placement.json` keep-out of that name |
| `via field` | `U1: 0.45/0.15 lattice, widths 0.1016-0.127, margin 0.4` | inside the part's pad field: a via not exactly that size, or off the pad lattice; a track whose width is outside the range. Outside it: a via of exactly that size. The copper tools skip the field |
| `ground beside` | `Fast: GND within 0.6 mm` | reported by `audit` and `check`: mm of the class's track with ground on 2 / 1 / 0 sides, before → after |
| `frozen` | `class X, net Y` | copper `viafix.py --push` never moves |
| `thermal pad` | `U5.5: solid` | one pad's own connection to the pour |

A plan declares its exceptions, each with a reason printed every run and
reviewed with the group:

- `"necks": [{"net", "width", "why"}]` — a width the class does not give;
- `"edge_ok": [{"net", "layer"?, "min", "why"}]` — a closer edge distance;
- `"accept": [{"type", "why"}]` — a DRC violation type the group produces on
  purpose (a fanout group ends every escape in free board, which DRC reports
  as unconnected ends). Never a way to wave away a real error; a declared type
  that does not occur is reported so it gets deleted.
- a via that cannot be linked by the rule: `routing/vialink_exceptions.json`
  (`{"via": [x, y], "why"}`), listed by every audit, never counted.

Some rows are *also* expressed as `.kicad_dru` rules by `board_setup.py` (a
no-via class, an area clearance). Then DRC and the guide agree independently,
and if they ever disagree, something moved.

`route.py audit` runs every active rule on the board as it is — DRC through
`place.py drc`, joints, edge, shape, via links, ground beside, pour islands —
and ends `AUDIT PASS` or `AUDIT FAIL (...)`. It is the line a group report
quotes, before and after.

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
(`rips`, `ripvias`, `polyrips`) is not brought back by `rip` — it is in the
backup `apply` made, and `rip` says so.

`route.py angles` audits the copper already on the board with the same code
`check` runs on the scratch copy (the short-diagonal test no longer depends on
the order segments are stored in, so the two agree). It is a **report** unless
the guide has a `joints` row: without one it prints what it finds and
exits 0. `apply --force` replaces a group: the previous copy's copper is
removed first, by its manifest.

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
  To choose a split over several layers, anneal over assignments with the
  bound as the per-layer test (keep pairs together); board1 needed four layers
  where any three stayed 3 short (a pocket of 2-3 balls with one exit each
  makes per-layer capacities not add). The bound is necessary, not sufficient:
  a split it passes can still stall the grid router at one gap (board1: three
  layers each contested at the same depopulated site) -- move one net per hot
  gap to a layer with room and re-route, and sweep `--seed` / `--hist`.
- **Two BGAs in one group** (a SoC and its DRAM): `"fields": [...]`, one per
  part, each at its own area clearance. With one field, the other part's
  dog-bones keep the group's class clearance between them (SE_50's 0.2032
  where U500's courtyard allows 0.1016) and its goals sit in pockets no net
  reaches ("0 of 4 goal cells").
- **A goal via off the grid**: end the leg with `"to_path": [[x, y], [x, y]]`
  (the cells within 0.151 of the via), not `"to_xy"` -- that rounds to one
  cell, which may lie in a neighbour's halo.
- **A plane is blocked on its layer** except an apron round each net's own
  escape end there; priced instead, a net rides through its neighbours' aprons.
- **`reach()` separates contested from walled in.** When negotiation stalls it
  floods each net's reachable cells; a net that reaches a few dozen against tens
  of thousands for the others is a placement or fanout defect to report, not a
  route to keep negotiating for.
- **No via inside a goal pad** — an 0402's land with a drill in it is a solder
  thief at assembly.

## `decap.py` — the decoupling ring as a plan

A via beside every pad, a stub to it (turning only by 45°), and the pour. The
via sites come from `tools/place/sites.py` (board's own via and clearance);
`--pin REF.PAD=x,y` overrides one, which a COLUMN of caps needs — their vias
have to stand in a row. The zone file is hand-drawn: the shape of a power pour
is a judgement about what it must not run under, and no search makes that call.

**A pour in a fine-pitch BGA field needs `min_width` at the fab's minimum**,
not KiCad's default 0.25 mm: the antipads leave narrower bridges and the pour
otherwise fragments.

## `pour.py` — what a pour actually does

A zone's outline is a wish; its **fill** is the copper, and DRC does not read
the difference. It passes a pour that fell into islands, a pour whose one via
sits in a pocket of its own, and a power pour laid over a bus that must see
ground. For every filled zone `pour.py` refills in memory and prints, from the
fill: islands and their areas; same-net vias and through-hole pins per island
and the vias the fill does **not** reach; the same-net SMD pads standing over
it; with `--over NETGLOB:LAYER`, the length of those tracks lying **under** the
fill (split by `--field`'s courtyard when named). `--try NET:LAYER=zone.json`
swaps an outline in memory and measures; nothing is written.

**Measure the fill, do not assume it.** `GetFilledPolysList(...).OutlineCount()`
is the island count and `.Contains(pt)` says whether a via is really in the
copper.

- **A round through-hole pad takes its spokes at 45 degrees** (KiCad's default
  for a circle; a square pad's are at 0 / 90). At a pour's corner, or where two
  pours meet beside it, two of its four diagonal quadrants must be this pour's
  fill, or DRC reports `starved_thermal` (min 2 spokes). Give the pour those
  quadrants (a higher `priority` over the neighbour where they overlap), not a
  track into the pad (board2 R4: J3 pin 2 between the +5V and VBUS pours).

## `corners.py` — the angle rule for a polyline

`fix45(pts, width, cut)` rewrites the joints of a polyline whose segments run at
multiples of 45°: a 90° joint becomes two 45° joints with a `cut`-long segment
between them (never under one track width), a 135° one three. A segment at any
other angle cannot be repaired locally — it raises, and the polyline has to be
re-drawn. `straighten(pts, width)` turns a grid router's one-cell stairs back
into diagonals, keeping the first and last headings.

**Generate at 45°, do not chamfer afterwards.** A pass that cuts the corners of
a router's output also "cuts" the router's own 45° joints, into 22.5° ones.

## `view.py` and `render`

`route.py render` is the picture for the **review**: KiCad's own plot of the
top, bottom and inner layers, rasterised with PyMuPDF from `tools/.venv`
(written to `<board>/routing/renders/` unless `--out`). `view.py` is the picture
for the **work**: one window of the board, one colour per copper layer, zone
fills shaded, an optional plan (or a stalled router's `.partial.json`) on top.
