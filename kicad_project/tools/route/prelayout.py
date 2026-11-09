#!/usr/bin/env python3
"""The pre-routing feasibility gate: every test the boards learned to run late, run before copper.

    tools/kpy tools/route/prelayout.py --board board1 [--spec routing/prelayout.json]

One line per test, `PRELAYOUT <test> <target>: PASS | FAIL | TODO | WAIVED -- why`, then
`PRELAYOUT PASS` or `PRELAYOUT FAIL (...)`; exit 1 on any FAIL or TODO. The placement review
request quotes the lines. Read-only: nothing on the board is written (fanout models go to
routing/prelayout/).

What it finds by itself, so a test cannot be skipped by leaving it out of the spec -- each
candidate is TODO (a failure) until the spec configures or waives it:
  * a BGA (36+ round pads on a grid)                            -> "fanout"
  * a fine-pitch row (8+ pads at <= 0.5 mm pitch)               -> "rows"
  * two connectors (J/P/X/CN) joined by 4+ point-to-point nets   -> "bundles" (measured with
    the defaults anyway; TODO until the spec says how many inversions the corridor can take)

THE SPEC, <board>/routing/prelayout.json (every number from the handoff; each entry may carry
"why"):

  {"fanout":  [{"ref": "U1", "plan": ["--via", "0.4,0.2", "--clearance", "0.0889", "--width", "0.0889",
                                      "--in-pad", "^VDD", "--share", "^GND$"],
                "layers": "routing/R1_layers_spec.json", "in_pad_ok": false}],
   "escape":  [{"ref": "U1", "layer": "In2.Cu", "args": ["--vias", "routing/prelayout/vias_U1.json",
                "--spec", "routing/R1_esc_spec.json", "--width", "0.1016", "--clearance", "0.1016", "--nfp"]}],
   "pourreach": [{"layer": "In4.Cu", "args": ["--box", "x0,y0,x1,y1", "--vias", "...", "--pad-nets", "VDD_SOC"]}],
   "rows":    [{"ref": "J1", "pads": "outer", "side": "S", "via": [0.6, 0.3], "gap": 0.2,
                "clearance": 0.2, "width": 0.2, "edge": 0.45, "lateral": 0.6, "skip": "^$", "strip": null}],
   "bundles": [{"a": "J1", "b": "J_MAIN1", "max": 0, "why": "one free layer in the corridor"}],
   "waive":   [{"check": "rows", "target": "J7", "why": "..."}]}

fanout   fanout.py plan (model to routing/prelayout/vias_<ref>.json), then `layers` when a
         layer file is given. FAIL: an own-via ball fell back into its pad and in_pad_ok is
         false; the field is "in-pad only" and in_pad_ok is false; `layers` leaves a net unplaced.
escape   escape_bound.py, per BGA per layer: FAIL when the bound proves the escape short.
pourreach  pourreach.py: FAIL when a rail via is cut off from its pour.
rows     the via sites a fine-pitch row has (routing skill §0, "count a fine-pitch row's via
         sites"). The strip runs from the pads' outer end (toward "side") to the first
         obstacle -- another part's courtyard or Edge.Cuts (outline, cut-out) -- or "strip" mm
         when given, less "edge" either way. k staggered via rows fit when the deeper rows' links pass
         between the vias above: same-row spacing L = max(pad + clearance,
         pad + (k-1) * width + k * clearance), one via per L / k along the row, adjacent rows
         sqrt((pad + clearance)^2 - (L/k)^2) apart. A pin's via stays within "lateral" mm of
         it. FAIL when some run of used pins needs more sites than fit beside it (Hall's
         condition on the windows -- exact for points on a line). "pads": "outer" (the row
         nearest "side"), "all", "odd", "even" or a list; "skip": nets that leave on the
         surface and need no via.
bundles  for two connectors, the point-to-point nets between them ordered across the bundle
         (perpendicular to the line between the connectors' centres) at each end; the
         inversions (pairs in opposite order) each need a via pair where both layers are
         free. FAIL above "max".
"""
import argparse
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "place"))
import place as P  # noqa: E402

LINES = []


def say(test, target, status, why):
    LINES.append(status)
    print(f"PRELAYOUT {test} {target}: {status} -- {why}", flush=True)


def tool(script, args, board):
    r = subprocess.run([sys.executable, str(HERE / script), *[str(x) for x in args]], cwd=str(board),
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def last(text, pat):
    return next((ln.strip() for ln in reversed(text.splitlines()) if re.search(pat, ln)), "")


# -- detection -----------------------------------------------------------------

def pad_xy(B, p):
    return B.mm(p.GetPosition().x), B.mm(p.GetPosition().y)


def is_bga(B, f):
    pads = [p for p in f.Pads() if p.GetShape() == B.pn.PAD_SHAPE_CIRCLE and not p.HasHole()]
    if len(pads) < 36:
        return False
    xs = {round(pad_xy(B, p)[0], 2) for p in pads}
    ys = {round(pad_xy(B, p)[1], 2) for p in pads}
    return len(xs) >= 4 and len(ys) >= 4 and len(pads) >= 0.4 * len(xs) * len(ys)


def min_pitch(B, f):
    pts = sorted(pad_xy(B, p) for p in f.Pads() if p.GetNumber())
    best = None
    for i, a in enumerate(pts):
        for b in pts[i + 1:i + 4]:
            d = math.dist(a, b)
            if d > 1e-3 and (best is None or d < best):
                best = d
    return best


CONN = re.compile(r"^(J|P|X|CN|CON)[A-Z_]*\d+$")


def p2p_nets(B):
    """{(refA, refB): {net: (padA, padB)}} for nets with exactly one pad on each of two connectors
    and no pad elsewhere."""
    where = defaultdict(list)
    for r, f in B.fps.items():
        for p in f.Pads():
            n = p.GetNetname()
            if n:
                where[n].append((r, p))
    out = defaultdict(dict)
    for n, lst in where.items():
        if len(lst) == 2 and lst[0][0] != lst[1][0] and all(CONN.match(r) for r, _ in lst):
            (ra, pa), (rb, pb) = sorted(lst, key=lambda t: t[0])
            out[(ra, rb)][n] = (pa, pb)
    return out


# -- bundles -------------------------------------------------------------------

def inversions(seq):
    return sum(1 for i in range(len(seq)) for j in range(i + 1, len(seq)) if seq[i] > seq[j] + 1e-6)


def bundle(B, a, b, nets):
    ca = B.fps[a].GetPosition()
    cb = B.fps[b].GetPosition()
    dx, dy = B.mm(cb.x - ca.x), B.mm(cb.y - ca.y)
    L = math.hypot(dx, dy) or 1.0
    ux, uy = -dy / L, dx / L                       # across the bundle
    rows = []
    for n, (pa, pb) in nets.items():
        xa, ya = pad_xy(B, pa)
        xb, yb = pad_xy(B, pb)
        rows.append((xa * ux + ya * uy, xb * ux + yb * uy, n))
    rows.sort()
    return inversions([r[1] for r in rows]), len(rows)


# -- rows ----------------------------------------------------------------------

DIRS = {"S": ((1, 0), (0, 1)), "N": ((1, 0), (0, -1)), "E": ((0, 1), (1, 0)), "W": ((0, 1), (-1, 0))}


def row_check(B, c):
    f = B.fps[c["ref"]]
    (ux, uy), (vx, vy) = DIRS[c["side"]]
    U = lambda x, y: x * ux + y * uy
    V = lambda x, y: x * vx + y * vy
    pads = [p for p in f.Pads() if p.GetNumber()]
    sel = c.get("pads", "outer")
    if sel == "outer":
        far = max(V(*pad_xy(B, p)) for p in pads)
        pads = [p for p in pads if V(*pad_xy(B, p)) > far - 0.05]
    elif sel in ("odd", "even"):
        pads = [p for p in pads if p.GetNumber().isdigit() and int(p.GetNumber()) % 2 == (1 if sel == "odd" else 0)]
    elif isinstance(sel, list):
        pads = [p for p in pads if p.GetNumber() in [str(s) for s in sel]]
    skip = re.compile(c.get("skip", "^$"))
    used = sorted(U(*pad_xy(B, p)) for p in pads if p.GetNetname() and not skip.search(p.GetNetname().split("/")[-1]))
    if not used:
        return "PASS", "no used pin in the row"
    pad_d, _drill = c.get("via", [0.6, 0.3])
    r = pad_d / 2
    gap, clr, w = c.get("gap", 0.2), c.get("clearance", 0.2), c.get("width", 0.2)
    edge, lat = c.get("edge", 0.5), c.get("lateral", 0.6)
    outer = max(V(*corner) for p in pads for corner in (
        (B.mm(p.GetBoundingBox().GetLeft()), B.mm(p.GetBoundingBox().GetTop())),
        (B.mm(p.GetBoundingBox().GetRight()), B.mm(p.GetBoundingBox().GetBottom())),
        (B.mm(p.GetBoundingBox().GetLeft()), B.mm(p.GetBoundingBox().GetBottom())),
        (B.mm(p.GetBoundingBox().GetRight()), B.mm(p.GetBoundingBox().GetTop()))))
    lo, hi = used[0] - lat - r, used[-1] + lat + r
    if c.get("strip") is not None:
        end, what = outer + float(c["strip"]) - edge, f"{float(c['strip']):.2f} mm strip (given)"
    else:
        end, what = None, None
        for ref, g in B.fps.items():
            if ref == c["ref"]:
                continue
            cy = B.courtyard(g)
            if not cy:
                continue
            us = sorted((U(cy[0], cy[1]), U(cy[2], cy[3])))
            vs = sorted((V(cy[0], cy[1]), V(cy[2], cy[3])))
            if us[1] >= lo and us[0] <= hi and vs[0] > outer and (end is None or vs[0] < end):
                end, what = vs[0], f"{ref}'s courtyard"
        pn = B.pn
        for d in B.b.GetDrawings():
            if d.GetLayer() != pn.Edge_Cuts:
                continue
            s = pn.SHAPE_POLY_SET()
            d.TransformShapeToPolygon(s, pn.Edge_Cuts, 0, pn.FromMM(0.01), pn.ERROR_INSIDE)
            for i in range(s.OutlineCount()):
                o = s.Outline(i)
                pts = [(B.mm(o.CPoint(k).x), B.mm(o.CPoint(k).y)) for k in range(o.PointCount())]
                for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                    # the segment clipped to the row's span: its nearest point beyond the pads
                    u1, v1, u2, v2 = U(x1, y1), V(x1, y1), U(x2, y2), V(x2, y2)
                    if max(u1, u2) < lo or min(u1, u2) > hi:
                        continue
                    cand = []
                    for t in (0.0, 1.0) + tuple((b_ - u1) / (u2 - u1) for b_ in (lo, hi) if abs(u2 - u1) > 1e-12):
                        if -1e-9 <= t <= 1 + 1e-9:
                            uu, vv = u1 + (u2 - u1) * t, v1 + (v2 - v1) * t
                            if lo - 1e-9 <= uu <= hi + 1e-9:
                                cand.append(vv)
                    for vv in cand:
                        if vv > outer + 1e-6 and (end is None or vv < end):
                            end, what = vv, "Edge.Cuts"
        if end is None:
            return "TODO", "no obstacle found beyond the row: give \"strip\""
        what = f"{end - outer:.2f} mm to {what}"
        end -= edge
    D = (end - r) - (outer + gap + r)               # room for the via rows' centres
    s = pad_d + clr
    best = None
    for k in range(1, int(c.get("rows_max", 3)) + 1):
        Lr = max(s, pad_d + (k - 1) * w + k * clr)
        eff = Lr / k
        dy = math.sqrt(max(0.0, s * s - eff * eff)) if k > 1 else 0.0
        if D < -1e-9 or (k - 1) * dy > D + 1e-9:
            continue
        if best is None or eff < best[1]:
            best = (k, eff)
    if best is None:
        return "FAIL", (f"{len(used)} used pin(s), no room for one via row ({what}, {edge:g} mm kept, "
                        f"via {pad_d:g} at {gap:g} from the pads)")
    k, eff = best
    worst = None
    for i in range(len(used)):
        for j in range(i, len(used)):
            need = j - i + 1
            fit = int(math.floor((used[j] - used[i] + 2 * lat) / eff + 1e-9)) + 1
            if fit < need and (worst is None or need - fit > worst[0]):
                worst = (need - fit, need, fit, used[j] - used[i])
    head = f"{what}: {k} via row(s), one via per {eff:.2f} mm, links within {lat:g} mm"
    if worst:
        return "FAIL", (f"{worst[1]} used pins over {worst[3]:.2f} mm need {worst[1]} sites, {worst[2]} fit ({head}); "
                        "a pin open or a via size the handoff must decide -- ask at placement")
    return "PASS", f"{len(used)} used pins fit ({head})"


# -- main ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--spec", default="routing/prelayout.json")
    a = ap.parse_args()
    board = Path(P.board_dir_arg(a.board))
    sp = Path(a.spec) if Path(a.spec).is_absolute() else board / a.spec
    S = json.loads(sp.read_text()) if sp.exists() else {}
    if not sp.exists():
        print(f"(no {sp.relative_to(board) if sp.is_relative_to(board) else sp}: only what can be found and measured "
              "without one)")
    B = P.Board(board)
    waived = {(w["check"], w["target"]): w.get("why", "NO REASON GIVEN") for w in S.get("waive", [])}
    outd = board / "routing" / "prelayout"

    def waive(check, target):
        if (check, target) in waived:
            say(check, target, "WAIVED", waived[(check, target)])
            return True
        return False

    # fanout (+ layers)
    conf = {c["ref"]: c for c in S.get("fanout", [])}
    for ref in sorted(set(conf) | {r for r, f in B.fps.items() if is_bga(B, f)}):
        if waive("fanout", ref):
            continue
        c = conf.get(ref)
        if not c:
            say("fanout", ref, "TODO", "a ball grid with no \"fanout\" entry: its via model and layer split are unproven")
            continue
        outd.mkdir(parents=True, exist_ok=True)
        vias = outd / f"vias_{ref}.json"
        rc, out = tool("fanout.py", ["plan", "--board", board, "--ref", ref, *c.get("plan", []), "--out", vias], board)
        if rc:
            say("fanout", ref, "FAIL", "fanout.py plan: " + (last(out, r"\S") or f"exit {rc}"))
            continue
        mode = last(out, r"^MODE:").replace("MODE:", "").strip()
        fb = sum(int(m) for m in re.findall(r"(\d+) (?:own|share) ball\(s\) found no free site", out))
        why = [f"mode {mode}", last(out, r"^vias:")]
        bad = False
        if not c.get("in_pad_ok") and (mode == "in-pad only" or fb):
            bad = True
            why.append(f"{fb} ball(s) fell back to a via in the pad, and in_pad_ok is not set" if fb
                       else "no via site in the field: every via in its pad")
        if c.get("layers"):
            lp = Path(c["layers"]) if Path(c["layers"]).is_absolute() else board / c["layers"]
            rc2, out2 = tool("fanout.py", ["layers", "--board", board, "--ref", ref, "--vias", vias, "--layers", lp,
                                           "--out", outd / f"layers_{ref}.json"], board)
            why.append(last(out2, r"unplaced") or f"layers exit {rc2}")
            bad |= rc2 != 0
        say("fanout", ref, "FAIL" if bad else "PASS", "; ".join(w for w in why if w))

    # escape_bound per BGA per layer and pourreach per layer are independent child processes:
    # run together (threads only wait on them), reported in the spec's order
    jobs = []
    for c in S.get("escape", []):
        tgt = f"{c['ref']}/{c['layer']}"
        if not waive("escape", tgt):
            jobs.append(("escape", tgt, "escape_bound.py",
                         ["--board", board, "--ref", c["ref"], "--layer", c["layer"], *c.get("args", [])],
                         r"bound|BOUND|excluded|short"))
    for c in S.get("pourreach", []):
        tgt = c["layer"]
        if not waive("pourreach", tgt):
            jobs.append(("pourreach", tgt, "pourreach.py", ["--board", board, "--layer", c["layer"], *c.get("args", [])],
                         r"POURREACH"))
    if jobs:
        from concurrent.futures import ThreadPoolExecutor
        import os
        n = int(os.environ.get("HW_JOBS") or 0) or (os.cpu_count() or 1)
        with ThreadPoolExecutor(max_workers=max(1, n)) as ex:
            outs = list(ex.map(lambda j: tool(j[2], j[3], board), jobs))
        for (test, tgt, _s, _a, pat), (rc, out) in zip(jobs, outs):
            say(test, tgt, "PASS" if rc == 0 else "FAIL", last(out, pat) or f"exit {rc}")

    # fine-pitch rows
    conf = defaultdict(list)
    for c in S.get("rows", []):
        conf[c["ref"]].append(c)
    found = {r for r, f in B.fps.items() if len(f.Pads()) >= 8 and not is_bga(B, f)
             and (min_pitch(B, f) or 9) <= 0.5 + 1e-6}
    for ref in sorted(set(conf) | found):
        if waive("rows", ref):
            continue
        if ref not in conf:
            say("rows", ref, "TODO", f"pads at {min_pitch(B, B.fps[ref]):.2f} mm pitch with no \"rows\" entry: "
                "count its via sites before planning beyond it")
            continue
        if ref not in B.fps:
            say("rows", ref, "FAIL", "not on the board")
            continue
        for c in conf[ref]:
            st, why = row_check(B, c)
            sel = c.get("pads", "outer")
            say("rows", f"{ref}({sel if isinstance(sel, str) else f'{len(sel)} pads'},{c['side']})", st, why)

    # bundles
    p2p = p2p_nets(B)
    conf = {tuple(sorted((c["a"], c["b"]))): c for c in S.get("bundles", [])}
    for key in sorted(set(conf) | {k for k, v in p2p.items() if len(v) >= 4}):
        tgt = f"{key[0]}-{key[1]}"
        if waive("bundles", tgt):
            continue
        nets = p2p.get(key, {})
        if len(nets) < 2:
            say("bundles", tgt, "FAIL" if key in conf else "PASS", f"{len(nets)} point-to-point net(s) between them")
            continue
        inv, n = bundle(B, key[0], key[1], nets)
        c = conf.get(key)
        if not c:
            say("bundles", tgt, "TODO", f"{inv} inversion(s) among {n} nets; no \"bundles\" entry says how many "
                "the corridor's free layers can take")
            continue
        say("bundles", tgt, "PASS" if inv <= c.get("max", 0) else "FAIL",
            f"{inv} inversion(s) among {n} nets, max {c.get('max', 0)}" + (f" ({c['why']})" if c.get("why") else ""))

    n_fail, n_todo = LINES.count("FAIL"), LINES.count("TODO")
    if n_fail or n_todo:
        print(f"PRELAYOUT FAIL ({n_fail} fail, {n_todo} todo, {LINES.count('PASS')} pass, "
              f"{LINES.count('WAIVED')} waived)")
        return 1
    print(f"PRELAYOUT PASS ({LINES.count('PASS')} pass, {LINES.count('WAIVED')} waived)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
