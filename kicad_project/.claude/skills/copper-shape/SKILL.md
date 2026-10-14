---
name: copper-shape
description: >
  Judge and polish the shape of the copper beyond DRC and the angle rule: bad
  pad entries (a track across a pad's corner, wider than its pad, off-centre),
  bulbs, overshoots, stacked tracks, wedges and slots, polygon necks; and draw
  net-tied copper polygons for the shapes a track cannot make. Triggers on
  "ugly pad entry", "track over a corner", "bulb", "wedge", "notch", "slot",
  "overshoot", "stacked track", "polygon", "copper polygon", "shape.py",
  "entryfix", "polish the copper", or a screenshot of copper that is legal
  but wrong.
---

# Copper shape: legal is not enough

The rules are in `<board>/general_routing_guide.md` ("Copper shape",
"Net-tied copper polygons"); the `copper shape` row of its table switches
the gate on. Then `route.py check` fails any plan that adds a shape site, and
`route.py audit` lists the board's sites. A site left on purpose is written
into the group report with its reason — "a fix that makes a wedge or a slot is
worse than a corner entry" is a reason.

## 1. Audit

```
tools/kpy tools/copper/shape.py --dump routing/shape_copper.json   # sites -> routing/shape_sites.json
tools/.venv/bin/python tools/copper/crops.py sites routing/shape_sites.json routing/shape_copper.json routing/crops
```

Read the crops before believing a site. The audit was tuned on a real board
by looking at every false-positive class; a new board can still show one
(a part whose pad shape the rules have not met). A false positive is a tool
defect to fix in `shape.py`, with the crop as the evidence — never an
excuse to switch the gate off.

## 2. Fix pad entries

```
tools/kpy tools/copper/entryfix.py                              # -> routing/entryfix.json + _report.txt
tools/kpy tools/route/route.py check routing/entryfix.json
```

`entryfix.py` re-lays each `entry-corner`, `entry-offset` and `cap-out` site:
back from the pad to an anchor (a via, another pad, a tee, a junction), then
onto the pad's centre line and straight into the pad's centre, every joint
0/45. A via whose only copper is the link may move onto the pad's axis, off it
by the guide's gap. Its plan carries the rips and the new copper; `check`
judges it, `apply` lays it. What it leaves (`HAND:` lines in the report) is
yours to draw, as a plan.

## 3. Polygons for what a track cannot draw

A track is a stadium. A bus head, a star, a taper into a small pad, a mitre
where a wide feed turns, a neck: draw a net-tied polygon in the plan —

```
"polys": [{"net": "/Power/DVDD", "layer": "F.Cu", "pts": [[x, y], ...]}]
```

- Take the net's pads whole; an edge leaving a pad is as wide as the pad;
  bus edges flush; 0.3 mm 45-degree fillets inside; a chamfer where a branch
  leaves. Edges turn at 0 or 45 degrees only.
- Re-lay the wide track shorter so its round cap lies inside the polygon (the
  plan's `rips` take the old segment, its `tracks` the shorter one).
- Never join a pad solidly into an outer-layer pour of its net (the shape
  audit's `poly-pour`), and keep the narrowest section at the class width
  (`poly-neck`).
- For a custom pad, stop 0.17 mm short of its chamfers.
- `tools/copper/poly.py --plan P.json` dry-runs the polygons before `check`.

## 4. Report

The group report lists: shape sites before → after (from `route.py audit`),
each site closed, each site left with its reason, every polygon drawn and
what it replaced. Then STOP for the user's review, like any group.
