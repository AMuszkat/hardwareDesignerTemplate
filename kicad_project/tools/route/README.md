# `tools/route` — routing, one reviewed group at a time

**Yours**, like `tools/place` and `tools/fp`. A board is routed in the groups
`<board>/ROUTING_PLAN.md` lists, and **the user reviews every group before the
next one starts**. These tools make a group cheap to propose, judge and undo.
The workflow is the `routing` skill.

    tools/kpy tools/route/route.py status --board board1 [--nets]
    tools/kpy tools/route/route.py check  --board board1 routing/R1_power.json
    tools/kpy tools/route/route.py apply  --board board1 routing/R1_power.json   # KiCad closed
    tools/kpy tools/route/route.py rip    --board board1 R1                      # KiCad closed
    tools/kpy tools/route/route.py render --board board1 [--out DIR]
    tools/kpy tools/route/route.py angles --board board1 [--nets]
    tools/kpy tools/route/route.py edge   --board board1
    tools/kpy tools/route/route.py gates  --board board1
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

## The rule gates: `<board>/routing/gates.json`

DRC cannot express some rules a board lives by — a plane layer that must stay
unbroken, a class that takes no via, a via size allowed only inside one
footprint's ball field. Those are DATA, transcribed from the handoff into
`routing/gates.json` (the skeleton comes with every new board). **Every entry
must carry a `"source"` citing the handoff line it came from** (`14_fab.md`
Net classes; `10_layout.md` #7) — the loader refuses one without it, because a
gate nobody can trace is a rule nobody can retire. **A gate left out (empty
list or null) is simply not run.** Keys starting with `_` are comments.
`route.py gates` prints what is active.

| key | entry | what `check` fails |
|---|---|---|
| `plane_layers` | `{"layer": "In1.Cu", "net": "GND", "source"}` | any track on that layer; a zone there that is not the plane's net |
| `class_vias` | `{"class", "max_vias": 0, "except": {"NET": 1}, "source"}` | a net of the class with more vias **on the whole board** (the board's plus the plan's) than its cap: `max_vias` for the class (0 = none), `except` per named net |
| `class_layers` | `{"class", "layers": ["F.Cu"], "except": ["NET"], "source"}` | a track of the class on any other layer |
| `field_vias` | `{"ref": "U1", "pad", "drill", "on_lattice": true, "widths": [lo, hi], "margin": 0.4, "outside_refused": true, "source"}` | inside the footprint's pad field (pad centres; tracks get `margin` more): a via that is not exactly `pad`/`drill`, or (with `on_lattice`) not on a diagonal centre of the pad grid; a track whose width is outside `[lo, hi]`. Outside the field: a via of exactly that size, unless `outside_refused` is false |
| `keepouts` | `{"name", "rect": [x0, y0, x1, y1]` or `"from_placement": "<placement.json keep-out name>", "forbid_classes": ["Fast"], "layers": [...]?, "source"}` | a track of a forbidden class touching the rectangle (on `layers`, or any). `rect` is board-relative like `placement.json` |
| `class_width` | `{"source"}` | a track whose width is not its class's width (outside a field with `widths`) |
| `edge` | `{"min": 0.5, "source"}` | new copper (tracks, vias, zone outlines) closer than `min` to Edge.Cuts. Use it for a number STRICTER than Board Setup's copper-to-edge — the fab's minimum plus its outline tolerance — since DRC already enforces Board Setup's |
| `angles` | `{"source"}` | a joint between two track segments that is not 0 or 45°; a right-angle turn whose diagonal is shorter than one track width; an arc. Entries into a pad or a via are free; a tee is a joint, so a branch leaves at 45° |

A plan declares its exceptions, each with a reason printed every run and
reviewed with the group:

- `"necks": [{"net", "width", "why"}]` — a width the class does not give;
- `"edge_ok": [{"net", "layer"?, "min", "why"}]` — a closer edge distance;
- `"accept": [{"type", "why"}]` — a DRC violation type the group produces on
  purpose (a fanout group ends every escape in free board, which DRC reports
  as unconnected ends). Never a way to wave away a real error; a declared type
  that does not occur is reported so it gets deleted.

Two gates are often *also* expressed as `.kicad_dru` rules by `board_setup.py`
(a no-via class, an area clearance). Then DRC and the gate agree
independently, and if they ever disagree, something moved.

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
nothing else, then renames the manifest to `.ripped`.

`route.py angles` audits the copper already on the board with the same code
`check` runs on the scratch copy (the short-diagonal test no longer depends on
the order segments are stored in, so the two agree). It is a **report** unless
`gates.json` has an `angles` gate: without one it prints what it finds and
exits 0. `apply --force` replaces a group: the previous copy's copper is
removed first, by its manifest.

## `netgroup.py` + `maze.py` — the negotiated router, driven by a spec

A group of signal nets from a JSON spec: legs to pads, points or prelaid copper
(`to_path`), escapes and links laid beforehand as `extra_tracks`/`extra_vias`, a
later group's copper kept free as `reserve_*`, an optional `field` (a BGA's pad
field, shut or opened on inner layers), a `plane` with aprons. It writes a plan;
`route.py check` judges it like any other. Every option is in the module
docstring; the layers default to every copper layer that is not a gates.json
plane, and the edge band to gates.json `edge` (else Board Setup).

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
