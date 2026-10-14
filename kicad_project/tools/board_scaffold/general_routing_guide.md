# General routing guide — {{BOARD}}

Yours. The rules this board is routed to and audited against, in one place.
Every rule comes from the handoff (`handoff/14_fab.md`, `handoff/10_layout.md`)
or from the user; a rule that is not in those is not yours to invent, and a
rule in them that blocks the work is a question, not a local edit. Read this
file before any group; `route.py audit` enforces the table below on the
board, and `route.py check` on every plan.

## The numbers the tools read

One row per rule. The `rule` and `setting` columns are read by
`tools/route/guide.py` (exact row names, the forms shown); `source` names the
handoff line. Delete a row and that rule is simply not run; `route.py audit`
prints what is active. A rule you cannot express here is a `*` note in the
sections below, for the eye.

| rule | setting | source |
|---|---|---|
| joints | 0/45 | 10_layout.md #_ |
| copper to edge | 0.5 mm | 14_fab.md: copper-to-edge + outline tolerance |
| via to pad gap | 0.15 mm | 10_layout.md #_ |
| 45-degree via link | last resort | 10_layout.md #_ |
| copper shape | on | 10_layout.md #_ |
| class width | on | 14_fab.md Net classes |
| thermals | outer: all pads; inner: THT only | 14_fab.md / 10_layout.md #_ |

Other rows the tools know (add the ones the handoff gives; one per line):

```
| plane layer     | In1.Cu: GND                                   | 14_fab.md Physical Stackup |
| no vias         | Sensitive (NET_A: 1)                          | 14_fab.md Net classes      |
| class layers    | Sensitive: F.Cu                               | 14_fab.md Net classes      |
| keep-out        | zone A: Fast                                  | 10_layout.md #_            |   placement.json keep-out name: classes kept out
| via field       | U1: 0.45/0.15 lattice, widths 0.1016-0.127   | 14_fab.md Net classes      |   a BGA: via pad/drill, on the pad lattice, track widths inside
| ground beside   | Fast: GND within 0.6 mm                       | 10_layout.md #_            |   class: ground net, distance from the track's edge
| frozen          | class Reference, net ref_taps                 | 10_layout.md #_            |   copper the via fixer never pushes
| thermal pad     | U5.5: solid                                   | 10_layout.md #_            |   a pad's own connection: solid / none
```

## Joints and corners

* Every joint between two track segments is 0 or 45 degrees, never 90. A
  right-angle turn is two 45-degree joints with a diagonal at least one track
  width long. No arcs. Entries into a pad or a via are free; pours are exempt.
  At a junction of three or more, every pair of axes is 0, 45 or 135 degrees
  (a tee is a joint: a branch leaves at 45). Every layer, every class, the
  copper already laid as much as the new.

## Net-tied copper polygons

* A filled polygon on a copper layer carrying a net is copper of that net for
  DRC and connectivity (it is not a zone). Use it for what a track cannot
  draw: a bus head, a star, a taper into a small pad, a mitre where a wide
  feed turns, a neck. It is written through the plan (`polys`), never by hand.
* Its edges turn at 0 or 45 degrees only, no arcs.
* Full size: the pads it joins are taken whole; an edge leaving a pad is as
  wide as the pad; bus edges flush; inside corners get 0.3 mm 45-degree
  fillets; a branch leaves a chamfer. Each pad keeps its clearance to its
  neighbour.
* It is a link, not a pour: it never joins a pad solidly into an outer-layer
  pour of its net (that undoes the thermal spokes).
* Its narrowest section away from a pad entry meets its class's track width.
* A wide track that ends in a polygon is laid shorter so its round cap lies
  inside the polygon.
* Refill the zones after writing one (`apply` does).

## Copper shape

* Beside DRC and the angle check, the copper is judged by its shape: a track
  entering a rectangular pad across its corner, a track wider than its pad, a
  round cap or a wide bus ending in a bulb, a track end overshooting another
  track, a track stacked on another, an acute wedge or slot between same-net
  copper, a polygon with a neck, a polygon solid into a pour. `route.py
  audit` lists them (`shape.py`); a plan that adds one fails `check`.
* A site left on purpose is listed in the group report with its reason.

## Vias and their links

* A via is joined to its pad by ONE straight segment that runs out of the
  pad's centre and into the via's centre, along one of the pad's axes (0, 90,
  180, 270 degrees). A straight 45-degree link only as a last resort; any
  other angle, or a bent link, never.
* A via's copper keeps the gap in the table from every pad, its own net's
  included — within a net this is the only gap, KiCad applies none. This is
  this harness's rule (`vialink.py`), not a KiCad DRC rule: KiCad's
  "0 errors" says nothing about it, so every group report states the
  `vialink` count before → after.
* An exception only when no track or part can be moved to make room: declare
  it in `routing/vialink_exceptions.json` (`{"via": [x, y], "why": "..."}`)
  and the user reviews it with the group.
* Keep via moves short: a decoupling via far from its capacitor is legal and
  wrong.

## Pours

* Outer pours relieve every pad with thermal spokes; inner planes relieve
  through-hole pins only; vias stay solid (the `thermals` row). A pad that
  must differ is a `thermal pad` row.
* Every island of a pour is tied by a via, a pad or a pin; a pour is judged
  by its fill, not its outline (`pour.py`).

## Placement lessons that routing pays for

* Leave the via sites in the decoupling placement from the start: between two
  rows of capacitors allow the via pad + two gaps + the clearance, or the via
  rule cannot be met there without moving parts.
