#!/usr/bin/env python3
"""knots.py -- where a router race that did not close is stuck.

    python3 tools/review/knots.py <partial.json> [<partial.json> ...] [--gap 0.3] [--via-sep 0.5]

READ ONLY. A race that ends on a plateau leaves `partial.json` beside each
variant (kicad_project/tools/route): `contested` maps a net to the cells
[layer, x, y] it still shares or cannot keep. This prints them as KNOTS --
cells of one layer nearer than --gap, with the nets in them -- largest first:
a handful of places, not a cell total.

Two more readings a cell total hides, printed under the knots:

  PAIR TWICE   the same two nets knotted an even number of times on one layer
               cross and cross back: no crossing is needed, the two run in
               each other's lane between the knots. Exchange the two paths
               there by hand; a negotiating router rarely does (each net
               already holds its own cheapest lane). An odd count is one
               real crossing: one of the two changes layer once.
  VIA PAIR     contested via cells are single cells and join no knot: each is
               printed with the via of another net nearer than --via-sep
               (centre to centre), from the file's own `vias`.

Read it before a term is offered for the wall: a knot of two tracks on one
layer is a crossing with no second layer there, and the term to look for is
what shuts the other layers at that place. A static count (crossing pairs,
lanes) ranks a rung only after it has predicted a race.
"""
import argparse, json, math
from collections import Counter


def knots(contested, gap):
    cells = {}
    for net, pts in contested.items():
        for layer, x, y in pts:
            cells.setdefault((layer, round(x, 3), round(y, 3)), set()).add(net)
    out = []
    for (layer, x, y), nets in sorted(cells.items()):
        hit = [k for k in out if k["layer"] == layer
               and any(abs(x - a) <= gap and abs(y - b) <= gap for a, b in k["pts"])]
        if hit:
            k = hit[0]
            for other in hit[1:]:
                k["pts"] += other["pts"]; k["nets"] |= other["nets"]; out.remove(other)
        else:
            k = {"layer": layer, "pts": [], "nets": set()}; out.append(k)
        k["pts"].append((x, y)); k["nets"] |= nets
    return sorted(out, key=lambda k: -len(k["pts"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("partial", nargs="+")
    ap.add_argument("--gap", type=float, default=0.3, help="cells nearer than this (mm) are one knot")
    ap.add_argument("--via-sep", type=float, default=0.5, help="two vias nearer than this (mm, centres) are a pair")
    a = ap.parse_args()
    for f in a.partial:
        p = json.load(open(f))
        ks = knots(p.get("contested", {}), a.gap)
        n = sum(len(k["pts"]) for k in ks)
        print(f"{f}: {p.get('state', '?')} at iteration {p.get('iteration', '?')}, {n} cells in {len(ks)} knot(s)")
        for k in ks:
            xs = [x for x, _ in k["pts"]]; ys = [y for _, y in k["pts"]]
            print(f"  {len(k['pts']):4d}  {k['layer']:7s} x {min(xs):.2f}-{max(xs):.2f}  y {min(ys):.2f}-{max(ys):.2f}"
                  f"  {' x '.join(sorted(k['nets']))}")
        twice = Counter((k["layer"], tuple(sorted(k["nets"]))) for k in ks if len(k["nets"]) == 2 and k["layer"] != "via")
        for (layer, nets), c in sorted(twice.items()):
            if c > 1:
                print(f"  PAIR {'TWICE' if c % 2 == 0 else f'x{c}'}  {layer}  {' x '.join(nets)}: {c} knots -- "
                      + ("no crossing is needed, exchange the two paths between them" if c % 2 == 0
                         else "one real crossing, the others cancel"))
        leaf = lambda n: n.split("/")[-1]
        vias = [(leaf(v["net"]), v["at"][0], v["at"][1]) for v in p.get("vias", [])]
        seen = set()
        for net, pts in sorted(p.get("contested", {}).items()):
            for layer, x, y in pts:
                if layer != "via":
                    continue
                for n2, x2, y2 in vias:
                    d = math.hypot(x - x2, y - y2)
                    key = tuple(sorted([(leaf(net), x, y), (n2, x2, y2)]))
                    if n2 != leaf(net) and d < a.via_sep and key not in seen:
                        seen.add(key)
                        print(f"  VIA PAIR  {leaf(net)} ({x:.3f}, {y:.3f}) and {n2} ({x2:.3f}, {y2:.3f}): {d:.3f} apart, {a.via_sep} needed")


if __name__ == "__main__":
    main()
