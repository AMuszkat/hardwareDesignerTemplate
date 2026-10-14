# `tools/copper` — the copper rules of `general_routing_guide.md`, as tools

KiCad's DRC, schematic parity, the 0/45-degree angle check and the edge check
can all pass on copper that is legal and ugly, or legal and wrong: a track
entering a pad across its corner, a wide track ending in a round bulb, a via
joined to its pad at an odd angle or touching it. These tools judge and fix
that. Each reads the board's `general_routing_guide.md` (the table) and the
`.kicad_pro` beside the board; none writes the board — a fix is a `route.py`
plan, judged by `route.py check` and laid by `route.py apply` like any group.

    tools/kpy tools/copper/shape.py     [--board B] [--out SITES.json] [--dump COPPER.json]
    tools/kpy tools/copper/vialink.py   [--board B] [--out SITES.json]
    tools/kpy tools/copper/vianeed.py   [--board B] [--out OUT.json]
    tools/kpy tools/copper/viafix.py    [--board B] [--out PREFIX] [--push ...]
    tools/kpy tools/copper/entryfix.py  [--board B] [--sites SITES.json] [--out PREFIX]
    tools/kpy tools/copper/fastside.py  [--board B] [--pcb BEFORE --after AFTER --guide DIR]
    tools/kpy tools/copper/thermals.py  [--board B] [--apply]
    tools/kpy tools/copper/poly.py      [--board B] [--plan P.json]
    tools/kpy tools/copper/cudump.py    [--board B] [--out DUMP.json]
    tools/.venv/bin/python tools/copper/crops.py window|sites|moves ...

`--board` takes `boardN`, the folder name or a path (default: this session's
board); `--pcb FILE --guide DIR` judges a scratch copy with the board's guide.
`route.py audit` runs the audits together, and `route.py check` runs `shape`
and `vialink` on every plan's scratch copy (a new site fails the plan).

| tool | does | writes |
|---|---|---|
| `shape.py` | the copper-shape audit: pad entries, overshoots, stacked tracks, bulbs, wedges, polygon necks, polygons solid into a pour | `routing/shape_sites.json`; `--dump` the copper for pictures |
| `vialink.py` | the via-link audit: a via linked straight, centre to centre, on the pad's axis, and the gap to every pad | `routing/vialink_sites.json`; exit 1 on a live site |
| `vianeed.py` | for every flagged via: what blocks each axial place (tracks / a via / a part / the edge) — run it first | `routing/vianeed.json` + a table |
| `viafix.py` | fixes via links: moves the via onto the pad's axis, re-lays the link, re-attaches the via's tracks; `--push` shoves other nets' tracks aside | ONE plan (`rips`, `ripvias`, `polyrips`, then the new copper), a report, the moves |
| `entryfix.py` | re-lays bad pad entries axially and centred | ONE plan + a report |
| `poly.py` | lists the net-tied polygons; dry-runs a plan's `polys` / `polyrips` | — |
| `fastside.py` | the guide's `ground beside`: how much of a class's track has ground on each side, on the refilled fill; before → after on two boards | — |
| `thermals.py` | the guide's thermal policy typed into every pour | the board, with `--apply` (a plan's `"thermals": true` does it in `apply`) |
| `cudump.py` / `crops.py` | a copper dump, and from it: a window per layer, a sheet of crops per shape site, before/after crops of every via moved | PNGs |

`vfcore.py` (obstacles at the stricter clearance, via legality on every copper
layer, 0/45 path search, re-attaching a track end) and `shove.py` (pushing
other nets' tracks and vias, with undo) are the libraries behind the fixers.

## What the guide gives them

| guide row | used by |
|---|---|
| `via to pad gap`, `45-degree via link` | `vialink`, `viafix`, `vianeed`, `entryfix` (a moved via keeps the gap) |
| `copper to edge` | the fixers' edge margin |
| `copper shape` | `route.py check` and `audit` run `shape` |
| `via field` (a BGA) | its courtyard is skipped by `shape`, `vialink`, `fastside`; nothing inside it is pushed |
| `ground beside` | `fastside`, and `audit` |
| `frozen` | copper `viafix --push` never moves |
| `thermals`, `thermal pad` | `thermals.py` |

Exceptions to the via rule live in `<board>/routing/vialink_exceptions.json`
(`[{"via": [x, y], "why": "..."}]`): a declared site is listed, not counted.

## The loop for a polish group

1. `route.py audit`: the counts as they are (the report quotes them before → after).
2. `vianeed.py`: which vias a push can free, which need a part moved, which need the user.
3. `viafix.py --push` (or `entryfix.py`): a plan. A full push search over a
   hundred vias takes tens of minutes; `--only` the accepted list re-runs in
   seconds. Search wide once, then iterate narrow with `--skip`, `--bar`, `--avoid`.
4. `route.py check` on the plan: DRC, angles, edge, shape (0 new), vialink
   (no new site), the short test. Each regression points at a move.
5. Pictures: `cudump.py` before, `viafix --save routing/trial.kicad_pcb` (never beside the board: the tools want ONE .kicad_pcb there), `cudump.py`
   on it, `crops.py moves`. **Crop every fix** — a legal fix can be worse than
   what it replaced (a wedge beside a pad, a slot along a pin).
6. `route.py apply` with KiCad closed, then `route.py audit`; the report; STOP
   for the user's review.

## Traps (met on the board these were built on)

- A fix that makes a wedge or a slot is worse than a corner entry: leave the
  site, and write the reason in the report.
- Moved vias and pushed tracks reshape the pours: judge ground beside and
  islands on the refilled copper, before vs after.
- Keep via moves short (`--maxmove`): a decoupling via far from its capacitor
  is legal and wrong.
- A via shared by two pads needs a place on both pads' axes, or one pad gets
  its own via.
- An axial link into a ground pad inside the ground pour can take a thermal
  spoke (DRC `starved_thermal`): `--avoid UUID:side`.
- The effective polygon of a round pad is inscribed: the tools keep 0.01 mm
  over the gap, or KiCad measures 0.1457 where the polygon said 0.15.
- A `pcbnew` wrapper is a new object on every lookup: compare items by
  `m_Uuid.AsString()`, never with `is`.
