# `tools/copper` — the copper rules of `general_routing_guide.md`, as tools

KiCad's DRC, schematic parity, the 0/45-degree angle check and the edge check
can all pass on copper that is legal and ugly, or legal and wrong: a track
entering a pad across its corner, a wide track ending in a round bulb, a via
joined to its pad at an odd angle or touching it. These tools judge and fix
that. Each reads the board's `general_routing_guide.md` (the table) and the
`.kicad_pro` beside the board; none writes the board — a fix is a `route.py`
plan, judged by `route.py check` and laid by `route.py apply` like any group.

    tools/kpy tools/copper/shape.py     [--board B] [--out SITES.json] [--dump COPPER.json] [--trackless]
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

⚠ **The scan cache.** A via's candidate search judges hundreds of places on
one board state, and scanning the whole board for each (a track list, the
obstacles round a point) was nearly all of its time. `vfcore.freeze()` keeps
those scans until the next edit: every edit made while frozen calls
`vfcore.changed()` — `shove.Undo` does it for every push and undo, `viafix`
for its apply and revert. A new edit path that skips it reads a stale board.
`HW_VF_CACHE=0` turns the cache off; the timing test (`vias` stage) runs
`viafix` both ways on `tests/fixture_pcb.py --kind vias` and requires the
same plan. The search stays serial on purpose: each via's fix changes the
board the next via is judged on, and forking a batch of candidates cost more
than judging them once the scans were cached (measured: 58 s → 65 s forked).

## What the guide gives them

| guide row | used by |
|---|---|
| `via to pad gap`, `45-degree via link` | `vialink`, `viafix`, `vianeed`, `entryfix` (a moved via keeps the gap) |
| `copper to edge` | the fixers' edge margin |
| `copper shape` | `route.py check` and `audit` run `shape` |
| `via field`, `bga field` (a BGA) | its courtyard is skipped by `shape`, `fastside`; nothing inside it is pushed. `vialink` lifts there only the BGA's OWN balls: their links, and the gap of a via linked to one (or in one); every other part's link to a via inside it is judged as anywhere. A ball's own link (its escape) is lifted wherever its via lands; that via's gap outside the courtyard is judged as anywhere |
| `ground beside` | `fastside`, and `audit` |
| `frozen` | copper `viafix --push` never moves |
| `thermals`, `thermal pad` | `thermals.py` |

Exceptions to the via rule live in `<board>/routing/vialink_exceptions.json`
(`[{"via": [x, y], "why": "..."}]`): a declared site is listed, not counted.

## The loop for a polish group

1. `route.py audit`: the counts as they are (the report quotes them before → after).
2. `vianeed.py`: which vias a push can free, which need a part moved, which need the user.
3. `viafix.py --push` (or `entryfix.py`): a plan. A full push search over a
   hundred vias took tens of minutes before the scan cache (the fixture's 200:
   139 s → 20 s); `--only` the accepted list re-runs in seconds. Search wide once, then iterate narrow with `--skip`, `--bar`, `--avoid`.
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
- **`thermals.py` replaces only KiCad's default inner relief** (0.5 mm gap, 0.5 mm spoke, which
  merges into a slot round a 2.54 mm pin row) with 0.2 / 0.3. A zone whose plan set
  `thermal_gap` / `spoke_width` keeps them. It used to overwrite every inner zone, and `apply`'s
  read-back does not compare zone settings, so board2's GND planes held 0.2 / 0.3 while their plan
  and report said 0.3 / 0.5 (RV22.3). To prove it: the zone's `thermal_gap` in the saved board
  equals the plan's.
- The effective polygon of a round pad is inscribed: the tools keep 0.01 mm
  over the gap, or KiCad measures 0.1457 where the polygon said 0.15.
- A `pcbnew` wrapper is a new object on every lookup: compare items by
  `m_Uuid.AsString()`, never with `is`.
- **A straight link split in two is still one link.** A track broken at a rule area's edge (a
  handoff that wants every track broken there) can put the break INSIDE the via's own copper; the
  audit then found the pad-side piece as a link ending off the via's centre (`via-offset`), and a
  split anywhere else read as a bent link (`via-angle`). `vialink.links_of` now walks a link in
  straight pieces: consecutive collinear segments of one net, layer and width, joined end to end
  with no third item at the joint (another segment's end or middle, a via centred there, a pad of
  the net; in a via's copper only when the next segment also ends in that via), are ONE segment.
  The chain it returns is still every real segment (what `viafix` rips); `.pieces` is what is
  judged. Prove it: a plan whose link is pad centre -> break -> via centre passes `route.py check`
  with no exception, the same link bent or stopped short inside the via still fails `via-offset`,
  and the audit of the board's backups is unchanged before -> after (board1 RV59.8: 143 boards,
  2675 sites, identical; `board1_module/tmpWork/RV59_vialink/`).
- **Which runs are links: topology, then 3.0 mm in a straight line (10_layout #48 (3), board1
  Q87).** A via link is the copper from a pad to a via of its net with no other pad, via or tee
  between them, whose pad and via centres are at most `vialink.LINK_MAX` = 3.0 mm apart IN A
  STRAIGHT LINE -- never measured along the copper. `links_of` walks the copper by topology alone
  (a pad ends the walk; a via, a tee or a dead end stops it), however many pieces and however long,
  then classes the run by that straight distance: a longer run is a route, judged by the joints and
  shape rules, not here (`skipped: run over 3.0 mm straight: a route`; `links_of(..., routes=[])`
  lists them). The trap in the old bound (at most 4 pieces and 3 mm of COPPER): a tuned link --
  1.15 mm straight, 2 mm of meander -- read as no link at all and passed (board1 b1d DSI_CLK_P/N),
  while a bent run 3.4 mm straight under 3 mm of copper at its last joint was judged as one. Prove
  it: `board1_module/tmpWork/vialink_scope/handcase.py` (a tuned 2.5 mm link fails `via-angle`, a
  3.4 mm bent and a 3.5 mm straight run are routes; HEAD's bound fails it), board1 at b949b628
  keeps 0 live sites with its 11 ETH_MDI / AUD_FS runs routes at 4.04..8.66 mm, and b1d's two
  CLK runs read `via-angle` at 1.00 / 1.15 mm.
- **A BGA's courtyard lifts its own balls, never another part's link.** `vialink` used to skip
  every via inside a `via field` / `bga field` courtyard, so the capacitors and resistors placed
  under a BGA (bottom side, linked to vias among the balls) were never judged: 14 off-axis links
  and a bend under U300 passed every audit (board1 RV61.1 / RV61.2). Now `guide.field_parts`
  gives each field's ref with its box, and for a via inside it `vialink` skips only that part's
  pads -- its balls' links, and the via-to-pad gap of a via linked to a ball or sitting in one (the
  balls' escape and site vias, which the field's own rule governs). Any other pad's link is judged
  as anywhere, and so is the gap of a via no ball uses (a capacitor's own via among the balls).
  A site there carries `"field": REF`. A via in a pad of its own net still needs no link to THAT
  pad, but another pad's link to it is judged (RV61.2's C301.1 runs to a via in C302.1's pad).
  Prove it: the board as RV61 saw it names exactly the review's 14 links (13 `via-angle`, and
  C305.2's bend as `via-offset`: its axis piece lies inside the pad); a plan that tilts a clean
  capacitor link under U1 fails `route.py check` with `RULE vialink`; across the board's 150
  backups the audit only ADDS sites, each inside a field or to a via in another pad
  (`board1_module/tmpWork/vialink_field/`).
- **A ball's escape is lifted wherever its via lands; the courtyard lifts only the gap.** After
  the courtyard fix above, a ball's link was lifted only when its via stood INSIDE the courtyard,
  so a ring-2 ball's mandatory bent escape (leave between two ring-1 lands, turn 45 to its lane,
  change layer at a pair via outside the ball field) to a via just past the courtyard edge read
  as `via-angle` and failed `route.py check` (board1 R3ab_c: CSI_D0_P from U1.B14 to a via 0.25
  mm below the courtyard). Now a link to a `bga field` / `via field` part's own pad is never
  judged, wherever its via lies (skipped as "escape link of a field's ball, via outside its
  courtyard"); the via's gap to every pad, the BGA's balls included, is still judged outside the
  courtyard, and every other part's link stays judged as before. Prove it: the escape plan passes
  `route.py check`; the board's own audit is unchanged (21 sites); a capacitor linked off-axis to
  a via outside every courtyard still fails; across 150 backups the audit only REMOVES ball-link
  sites whose via is outside that ball's courtyard (`board1_module/tmpWork/vialink_escape/`).
- `shape.py` exempts a BGA field's courtyard by where a piece's CENTRE falls.
  A short escape that only just leaves the field (a via on a site outside the
  ball array, 0.3 mm from the edge, its track 0.3 mm past it) is one thin
  piece from via edge to free end; its centre lands outside, and the whole
  escape reads as a "nub". A routed escape, longer, centres inside and passes.
  Board1 (R1, RV5.6) ended such laid exits 0.03 mm past the courtyard edge;
  the false positive disappears anyway once the route continues. Read the
  piece's extent in the report before "fixing" a nub at a field's edge.

- **`guardblock.py`: a guarded block holds no other net's copper.** A rule like "no other net's copper inside the
  crystal block on F.Cu or the inner layers", where the block is pads plus tracks grown by a clearance, is checked by
  neither KiCad's DRC nor `route.py audit`. TRAP (board1 #15, 2026-11-23): R_J1N's PDM_DATA3 In2 run went through the
  PMIC crystal block. Every gate passed, and a worker found it only while reading a router's contested cells.
  Run it after every group near a guarded block and before the fab release:
  `tools/kpy tools/copper/guardblock.py BOARD --parts Y100,C122,C123 --nets PMIC_XTAL_IN,PMIC_XTAL_OUT --layers
  F.Cu,In1.Cu,...,In6.Cu --own GND --clr 0.1016 [--zones]`. It exits 1 and prints a line per intrusion. Zones are
  tested only with `--zones`, because a whole plane under the block may be what the rule asks for.
