#!/usr/bin/env python3
"""crops.py -- pictures of copper, from JSON dumps (matplotlib; the board venv, not KiCad's Python).

    tools/.venv/bin/python tools/copper/crops.py window DUMP.json OUT.png x0 y0 x1 y1 [--layers F.Cu,B.Cu]
    tools/.venv/bin/python tools/copper/crops.py sites SITES.json COPPER.json OUT_DIR [--kind K] [--net N] [--layer L]
    tools/.venv/bin/python tools/copper/crops.py moves BEFORE.json AFTER.json MOVES.json OUT_PREFIX [--per 8]

  window  one panel per copper layer of a window of the board: pads (net-coloured, labelled), tracks, vias,
          courtyards.  DUMP.json is cudump.py's.
  sites   a sheet of crops, one per copper-shape site: the net's copper on the site's layer in red, other nets
          grey, the site marked.  SITES.json and COPPER.json are shape.py's (--out, --dump).
  moves   before | after crops of every via the fixer moved (viafix.py's OUT_moves.json), from two cudump.py dumps.

A picture is how a fix is judged after the gates: a legal fix can still be worse than what it replaced (a wedge
beside a pad, a slot along a pin, a dog-leg).  Crop every one."""
import argparse
import json
import os
import sys

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, Circle, Polygon
except ImportError:
    sys.exit("crops: matplotlib is not importable here: run it with tools/.venv/bin/python (tools/setup.sh installs it)")

_col = {}


def colour(n, ground="GND"):
    if n == ground:
        return "0.55"
    if n not in _col:
        _col[n] = plt.cm.tab20(len(_col) % 20)
    return _col[n]


def cmd_window(a):
    D = json.load(open(a.dump)); x0, y0, x1, y1 = a.x0, a.y0, a.x1, a.y1
    LAY = a.layers.split(",") if a.layers else D.get("layers", ["F.Cu", "B.Cu"])
    W = 11.0; H = W * (y1 - y0) / (x1 - x0)
    fig, axs = plt.subplots(len(LAY), 1, figsize=(W, H * len(LAY) + 0.6 * len(LAY)))
    axs = [axs] if len(LAY) == 1 else list(axs)
    inb = lambda x, y, m=1: x0 - m <= x <= x1 + m and y0 - m <= y <= y1 + m
    for ax, L in zip(axs, LAY):
        ax.set_title(L, fontsize=9); ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.set_aspect("equal"); ax.grid(True, lw=0.2); ax.tick_params(labelsize=6)
        for c in D["court"]:
            if any(inb(x, y, 3) for x, y in c):
                ax.add_patch(Polygon(c, closed=True, fill=False, ls="--", lw=0.5, ec="0.6"))
        for p in D["pads"]:
            if L not in p["layers"]:
                continue
            l, t, r, bm = p["box"]
            if not inb((l + r) / 2, (t + bm) / 2):
                continue
            if p["round"]:
                ax.add_patch(Circle(((l + r) / 2, (t + bm) / 2), (r - l) / 2, fc=colour(p["net"]), alpha=0.65, ec="k", lw=0.3))
            else:
                ax.add_patch(Rectangle((l, t), r - l, bm - t, fc=colour(p["net"]), alpha=0.65, ec="k", lw=0.3))
            if L == LAY[0]:
                ax.text((l + r) / 2, (t + bm) / 2, p["ref"].split(".")[-1] + "\n" + p["net"][:9], fontsize=4, ha="center", va="center")
        for t in D["tracks"]:
            if t["layer"] != L:
                continue
            (p, q), (r_, s) = t["pts"]
            if not (inb(p, q) or inb(r_, s)):
                continue
            ax.plot([p, r_], [q, s], color=colour(t["net"]), lw=max(t["width"], 0.1) * 72 * W / (x1 - x0), solid_capstyle="round", alpha=0.85)
        for v in D["vias"]:
            if inb(*v["at"]):
                ax.add_patch(Circle(v["at"], v["pad"] / 2, fc=colour(v["net"]), ec="k", lw=0.4))
        for r, x, y in D["refs"]:
            if inb(x, y, 0) and L == LAY[0]:
                ax.text(x, y - 0.6, r, fontsize=6, color="b", ha="center")
    plt.tight_layout(); plt.savefig(a.out, dpi=110)
    print(f"wrote {a.out}")


def cmd_sites(a):
    S = json.load(open(a.sites)); Cp = json.load(open(a.copper)); os.makedirs(a.out, exist_ok=True)
    HALF, PER = a.half, a.per
    sel = [s for s in S if (not a.kind or s["kind"] == a.kind) and (not a.net or s["net"] == a.net) and (not a.layer or s["layer"] == a.layer)]
    sel.sort(key=lambda s: (s["kind"], s["layer"], s["net"], s["at"]))
    by_layer = {}
    for c in Cp["copper"]:
        by_layer.setdefault(c["layer"], []).append(c)
    inb = lambda poly, x0, y0, x1, y1: any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in poly) or (
        min(p[0] for p in poly) <= x0 and max(p[0] for p in poly) >= x1 and min(p[1] for p in poly) <= y0 and max(p[1] for p in poly) >= y1)
    made = []
    for page in range(0, len(sel), PER):
        chunk = sel[page:page + PER]; cols = 4; rows = (len(chunk) + cols - 1) // cols
        fig, axs = plt.subplots(rows, cols, figsize=(4 * cols, 4 * rows)); axs = axs.flatten() if rows * cols > 1 else [axs]
        for ax in axs:
            ax.axis("off")
        for ax, s in zip(axs, chunk):
            x, y = s["at"]; x0, y0, x1, y1 = x - HALF, y - HALF, x + HALF, y + HALF
            ax.axis("on"); ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.set_aspect("equal"); ax.tick_params(labelsize=5)
            for c in by_layer.get(s["layer"], []):
                if not inb(c["poly"], x0, y0, x1, y1):
                    continue
                own = c["net"] == s["net"]
                ax.add_patch(Polygon(c["poly"], closed=True, fc="#c83232" if own else "#b0b0b0", ec="k" if own else "none",
                                     lw=0.3, alpha=0.9 if own else 0.5, zorder=2 if own else 1))
            if s.get("poly"):
                ax.add_patch(Polygon(s["poly"], closed=True, fc="yellow", ec="k", lw=0.5, alpha=0.8, zorder=3))
            ax.add_patch(Circle((x, y), max(0.08, s["size"] / 2), fill=False, ec="blue", lw=1.2, zorder=4))
            for p in Cp["pads"]:
                if p["layer"] == s["layer"] and x0 <= p["at"][0] <= x1 and y0 <= p["at"][1] <= y1:
                    ax.text(p["at"][0], p["at"][1], p["name"], fontsize=5, ha="center", va="center", zorder=5)
            extra = {k: v for k, v in s.items() if k in ("into", "w", "cross", "off", "angle", "sides", "area", "w_other")}
            ax.set_title(f"{s['kind']} {s['layer']} {s['net']}\n{s['at']} size {s['size']} {extra}", fontsize=6)
        fig.tight_layout()
        fn = f"{a.out}/{a.kind or 'all'}_{page // PER:02d}.png"; fig.savefig(fn, dpi=110); plt.close(fig); made.append(fn)
    print(f"{len(sel)} site(s), {len(made)} sheet(s): " + ", ".join(made))


def cmd_moves(a):
    B0, B1, M = json.load(open(a.before)), json.load(open(a.after)), json.load(open(a.moves))
    HALF, PER = a.half, a.per
    M = [m for m in M if abs(m["old"][0] - m["new"][0]) + abs(m["old"][1] - m["new"][1]) > 0.002 or "PUSH" in m.get("how", "")]
    inner = [L for L in B1.get("layers", []) if L not in ("F.Cu", "B.Cu")]
    style = {"F.Cu": ("-", 0.9), "B.Cu": ("--", 0.45), **{L: (":", 0.45) for L in inner}}

    def draw(ax, D, cx, cy, net, title):
        x0, y0, x1, y1 = cx - HALF, cy - HALF, cx + HALF, cy + HALF
        ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.set_title(title, fontsize=7)
        inb = lambda x, y, m=0.8: x0 - m <= x <= x1 + m and y0 - m <= y <= y1 + m
        col = lambda n: "crimson" if n == net else "0.55"
        for p in D["pads"]:
            if "F.Cu" not in p["layers"]:
                continue
            l, t, r, bm = p["box"]
            if not inb((l + r) / 2, (t + bm) / 2):
                continue
            fc = "#f3a6a6" if p["net"] == net else "0.82"
            if p["round"]:
                ax.add_patch(Circle(((l + r) / 2, (t + bm) / 2), (r - l) / 2, fc=fc, ec="k", lw=0.3))
            else:
                ax.add_patch(Rectangle((l, t), r - l, bm - t, fc=fc, ec="k", lw=0.3))
            ax.text((l + r) / 2, (t + bm) / 2, p["ref"], fontsize=4.5, ha="center", va="center", clip_on=True)
        sc = 72 * 3.2 / (x1 - x0)
        for t in D["tracks"]:
            (p, q), (r_, s) = t["pts"]
            if not (inb(p, q) or inb(r_, s)):
                continue
            st = style.get(t["layer"])
            if not st:
                continue
            ax.plot([p, r_], [q, s], st[0], color=col(t["net"]), lw=max(t["width"], 0.08) * sc, alpha=st[1], solid_capstyle="round")
        for v in D["vias"]:
            if inb(*v["at"]):
                ax.add_patch(Circle(v["at"], v["pad"] / 2, fc=col(v["net"]), ec="k", lw=0.4, alpha=0.9, zorder=5))

    for page in range(0, len(M), PER):
        ch = M[page:page + PER]; fig, axs = plt.subplots(len(ch), 2, figsize=(6.4, 3.2 * len(ch)))
        if len(ch) == 1:
            axs = [axs]
        for (l, r), m in zip(axs, ch):
            cx, cy = (m["old"][0] + m["new"][0]) / 2, (m["old"][1] + m["new"][1]) / 2
            draw(l, B0, cx, cy, m["net"], f"BEFORE  {m['net']} at {m['pad']}")
            draw(r, B1, cx, cy, m["net"], f"AFTER  {m['how'][:60]}")
        plt.tight_layout(); fig.savefig(f"{a.out}_{page // PER + 1}.png", dpi=130); plt.close(fig)
    print(f"{len(M)} vias, {(len(M) + PER - 1) // PER} sheet(s): {a.out}_N.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("window"); w.add_argument("dump"); w.add_argument("out")
    for k in ("x0", "y0", "x1", "y1"):
        w.add_argument(k, type=float)
    w.add_argument("--layers"); w.set_defaults(fn=cmd_window)
    s = sub.add_parser("sites"); s.add_argument("sites"); s.add_argument("copper"); s.add_argument("out")
    s.add_argument("--kind"); s.add_argument("--net"); s.add_argument("--layer")
    s.add_argument("--half", type=float, default=1.2); s.add_argument("--per", type=int, default=12); s.set_defaults(fn=cmd_sites)
    m = sub.add_parser("moves"); m.add_argument("before"); m.add_argument("after"); m.add_argument("moves"); m.add_argument("out")
    m.add_argument("--half", type=float, default=1.6); m.add_argument("--per", type=int, default=8); m.set_defaults(fn=cmd_moves)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
