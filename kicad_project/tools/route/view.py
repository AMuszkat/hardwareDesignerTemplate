#!/usr/bin/env python3
"""A planning picture of one window of the board, optionally with a plan on top.

    tools/kpy tools/route/view.py --board board1 123.5 112.5 131.5 116.5 routing/renders/corner
    tools/kpy tools/route/view.py --board board1 X0 Y0 X1 Y1 OUT --plan routing/R3_spi.json \
        [--scale 40] [--layers In2.Cu,B.Cu] [--tint 'SIG_*=#fc6,+3V3=#9cf']

Board mm, 1 mm grid, every copper layer in its own colour (F.Cu red, B.Cu blue,
inner layers green/olive/...), zone FILLS shaded underneath, pads tinted (GND
green, plus any --tint NETGLOB=#colour), courtyards and designators, vias as
rings.  A plan's tracks and vias are drawn over the board's; a stalled
plan may carry `marks` (points to flag) and `guides` (lines) for a picture; no tool writes them.
Writes OUT.svg and OUT.png (PyMuPDF, from kicad_project/tools/.venv -- pcbnew
pins this tool to KiCad's own interpreter, so the raster runs out of process).

`route.py render` is the picture for the REVIEW (KiCad's own plot).  This one is
for the work: it is what shows that a corridor's first track sealed a via row,
that Manhattan jogs cannot slide a ribbon, and which net clipped whose corner.
"""

import argparse
import fnmatch
import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "place"))
import place as P  # noqa: E402

VENV_PY = HERE.parents[1] / "tools" / ".venv" / "bin" / "python"
INNER = ["#2a2", "#aa0", "#0aa", "#a5a", "#a60", "#5a5"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("x0", type=float)
    ap.add_argument("y0", type=float)
    ap.add_argument("x1", type=float)
    ap.add_argument("y1", type=float)
    ap.add_argument("out", help="output path without extension")
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--plan", help="a route.py plan (or a netgroup .partial.json) to draw on top")
    ap.add_argument("--scale", type=int, default=40, help="pixels per mm")
    ap.add_argument("--layers", default="", help="draw only these copper layers, comma separated")
    ap.add_argument("--tint", default="", help="NETGLOB=#colour,... pad tints (GND is green by default)")
    a = ap.parse_args()
    a.board = P.board_dir_arg(a.board)
    out = Path(a.out)
    if not out.is_absolute() and not out.parent.exists():
        out = Path(a.board) / out           # "routing/renders/x" means the board's own folder
    out.parent.mkdir(parents=True, exist_ok=True)
    a.out = str(out)
    x0, y0, x1, y1, S = a.x0, a.y0, a.x1, a.y1, a.scale
    only = set(filter(None, a.layers.split(",")))
    tints = [("GND", "#9c9")] + [tuple(t.split("=", 1)) for t in a.tint.split(",") if "=" in t]

    B = P.Board(a.board)
    pn, mm = B.pn, B.mm
    canon = pn.BOARD.GetStandardLayerName                 # a board may rename its layers
    cu = [canon(l) for l in B.b.GetEnabledLayers().CuStack()]
    col = {"F.Cu": "#d33", "B.Cu": "#36c"}
    for i, n in enumerate(q for q in cu if q not in col):
        col[n] = INNER[i % len(INNER)]

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{(x1 - x0) * S:.0f}" height="{(y1 - y0) * S:.0f}" '
         f'viewBox="{x0} {y0} {x1 - x0} {y1 - y0}">',
         f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="#fff"/>']

    def trk(layer, p, q, w, op=0.75):
        if only and layer not in only:
            return
        o.append(f'<line x1="{p[0]}" y1="{p[1]}" x2="{q[0]}" y2="{q[1]}" stroke="{col.get(layer, "#888")}" '
                 f'stroke-width="{w}" stroke-linecap="round" opacity="{op}"/>')

    # zone fills first, underneath everything: a pour is what the review is about
    for z in B.b.Zones():
        if z.GetIsRuleArea():
            continue
        for lid in z.GetLayerSet().CuStack():
            lay = canon(lid)
            if only and lay not in only:
                continue
            poly = z.GetFilledPolysList(lid)
            for i in range(poly.OutlineCount()):
                ring = poly.Outline(i)
                pts = " ".join(f"{mm(ring.CPoint(k).x):.3f},{mm(ring.CPoint(k).y):.3f}" for k in range(ring.PointCount()))
                o.append(f'<polygon points="{pts}" fill="{col.get(lay, "#888")}" opacity="0.16"/>')
                for h in range(poly.HoleCount(i)):
                    hh = poly.Hole(i, h)
                    hp = " ".join(f"{mm(hh.CPoint(k).x):.3f},{mm(hh.CPoint(k).y):.3f}" for k in range(hh.PointCount()))
                    o.append(f'<polygon points="{hp}" fill="#fff"/>')

    for ref, f in B.fps.items():
        c = B.courtyard(f)
        if c and c[2] > x0 and c[0] < x1 and c[3] > y0 and c[1] < y1:
            poly = [g for g in f.GraphicalItems()
                    if g.GetLayer() == B.CRT[B.side(f)] and g.GetShape() == pn.SHAPE_T_POLY]
            if len(poly) == 1:   # a contoured courtyard (an angled connector): its shape, not its box
                pts = " ".join(f"{mm(q.x)},{mm(q.y)}" for q in poly[0].GetPolyPoints())
                o.append(f'<polygon points="{pts}" fill="none" stroke="#c6c" stroke-width="0.03"/>')
            else:
                o.append(f'<rect x="{c[0]}" y="{c[1]}" width="{c[2] - c[0]}" height="{c[3] - c[1]}" '
                         f'fill="none" stroke="#c6c" stroke-width="0.03"/>')
            o.append(f'<text x="{(c[0] + c[2]) / 2}" y="{c[1] + 0.35}" font-size="0.4" text-anchor="middle" '
                     f'fill="#909">{ref}</text>')
        for p in f.Pads():
            bb = p.GetBoundingBox()
            L, T, R, Bm = (mm(v) for v in (bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom()))
            if R < x0 or L > x1 or Bm < y0 or T > y1:
                continue
            n = p.GetNetname().split("/")[-1]
            fill = next((c_ for g, c_ in tints if fnmatch.fnmatch(n, g)), "#bbb")
            if p.GetShape() == pn.PAD_SHAPE_CIRCLE:
                o.append(f'<circle cx="{(L + R) / 2}" cy="{(T + Bm) / 2}" r="{(R - L) / 2}" fill="{fill}" opacity="0.8"/>')
            else:
                o.append(f'<rect x="{L}" y="{T}" width="{R - L}" height="{Bm - T}" fill="{fill}" opacity="0.8"/>')
            if (R - L) > 0.5:
                o.append(f'<text x="{(L + R) / 2}" y="{(T + Bm) / 2 + 0.08}" font-size="0.22" '
                         f'text-anchor="middle">{n[-9:]}</text>')

    for t in B.b.GetTracks():
        if t.Type() == pn.PCB_VIA_T:
            x, y, r = mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetBoundingBox().GetWidth()) / 2
            o.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#222" opacity="0.6"/>'
                     f'<circle cx="{x}" cy="{y}" r="{r / 2}" fill="#fff"/>')
        else:
            trk(canon(t.GetLayer()), (mm(t.GetStart().x), mm(t.GetStart().y)), (mm(t.GetEnd().x), mm(t.GetEnd().y)),
                mm(t.GetWidth()))

    if a.plan:
        pp = Path(a.plan) if Path(a.plan).exists() else Path(a.board) / a.plan
        pl = json.loads(pp.read_text())
        for t in pl.get("tracks", []):
            for i in range(len(t["pts"]) - 1):
                trk(t.get("layer", "F.Cu"), t["pts"][i], t["pts"][i + 1], t["width"], 0.95)
        for v in pl.get("vias", []):
            o.append(f'<circle cx="{v["at"][0]}" cy="{v["at"][1]}" r="{v.get("pad", 0.6) / 2}" fill="#f80" '
                     f'opacity="0.8"/><circle cx="{v["at"][0]}" cy="{v["at"][1]}" r="{v.get("drill", 0.3) / 2}" fill="#fff"/>')
        for m in pl.get("marks", []):
            o.append(f'<circle cx="{m[0]}" cy="{m[1]}" r="0.12" fill="none" stroke="#f0f" stroke-width="0.05"/>')
        for g in pl.get("guides", []):
            for i in range(len(g) - 1):
                o.append(f'<line x1="{g[i][0]}" y1="{g[i][1]}" x2="{g[i + 1][0]}" y2="{g[i + 1][1]}" '
                         f'stroke="#000" stroke-width="0.04" opacity="0.8"/>')

    for gx in range(math.ceil(x0), int(x1) + 1):          # 1 mm grid
        o.append(f'<line x1="{gx}" y1="{y0}" x2="{gx}" y2="{y1}" stroke="#000" stroke-width="0.01" opacity="0.25"/>'
                 f'<text x="{gx + 0.03}" y="{y0 + 0.3}" font-size="0.3" fill="#555">{gx}</text>')
    for gy in range(math.ceil(y0), int(y1) + 1):
        o.append(f'<line x1="{x0}" y1="{gy}" x2="{x1}" y2="{gy}" stroke="#000" stroke-width="0.01" opacity="0.25"/>'
                 f'<text x="{x0 + 0.03}" y="{gy - 0.03}" font-size="0.3" fill="#555">{gy}</text>')
    o.append("</svg>")
    out = Path(a.out)
    out.with_suffix(".svg").write_text("\n".join(o))
    raster = "import pymupdf, sys; d = pymupdf.open(sys.argv[1]); d[0].get_pixmap(dpi=96).save(sys.argv[2])"
    for exe in (str(VENV_PY), "python3"):
        if exe != "python3" and not Path(exe).exists():
            continue
        r = subprocess.run([exe, "-c", raster, str(out.with_suffix(".svg")), str(out.with_suffix(".png"))],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print("wrote", out.with_suffix(".png"))
            return 0
    print(f"wrote {out.with_suffix('.svg')} (no PyMuPDF for the PNG -- run tools/setup.sh)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
