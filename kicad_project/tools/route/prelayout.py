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
   "waive":   [{"check": "rows", "target": "J7", "why": "..."},
               {"check": "rows", "target": "U400:S", "why": "one side's rows entry; the part's others still run"}]}

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
groups   <board>/routing/groups.json: every routing group, DECLARED before the first of them is
         routed, each with the count that shows it fits -- so the groups that do not fit come
         back from the placement as one batch of questions, not one a day from the routing:

           {"groups": [{"id": "R_emmc", "nets": ["^EMMC_", "^SD1_"], "matched": false,
                        "count": ["tools/route/escape_bound.py", "--board", "{board}", "..."],
                        "why": "eight data lines out of U1's west side on In2"},
                       {"id": "R_fan", "nets": ["^DRAM_"], "matched": true,
                        "covered": "fanout U1 and escape U1 In2.Cu above"}],
            "pours": ["^GND$", "^VDD_", "^NVCC_"]}

         "nets": patterns on the net's short name. COVERAGE: every net of the board with two
         or more pads is in exactly one group or matches "pours" (FAIL names the rest).
         "count": a command (run from kicad_project/, a .py with this interpreter, "{board}"
         the board folder) that exits 0 when the group fits and non-zero when it does not; it
         is the group's ONE feasibility count (budget.py): ended at 30 minutes, and no answer
         by then is FAIL "does not fit". "covered": the test above, or the earlier group, that
         already proves it, in words (a reviewer reads them). Neither: TODO. A result is kept
         in routing/prelayout/count_<id>.json and reused until one of the group's own parts
         moves or the handoff revision changes; --no-count runs nothing and reports the kept
         results only. The line also names the group's budget tier (budget.py), which the
         router, the race and the check then count against.
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "place"))
import place as P  # noqa: E402
import budget  # noqa: E402  (tools/route: the tiers, and each group's one feasibility count)

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


def straddle_check(usedp, strad, v_lo, v_hi, lat, r, clr, step=0.01):
    """Each used pin's via site against the pads of parts that straddle the row's outer edge.

    usedp: (u, net, pin) per used pin; strad: (u0, u1, v0, v1, net, 'REF.pad') in the row's frame. A via
    centre may stand at v in [v_lo, v_hi] (the strip) and u within `lat` of its pin. A pad of another net
    blocks the centres nearer than r + clr to its box; a pad of the pin's own net may take the via (C104's
    under J1 do). Returns '' or the FAIL text naming every pin left with no site and the pads that shut it.
    Neighbouring vias are not modelled here: that is the run count's job."""
    if not strad:
        return ""
    nv = max(1, int(round((v_hi - v_lo) / step)))
    nu = max(1, int(round(2 * lat / step)))
    ok, obsof = [], []
    for u, net, pin in usedp:
        obs = [o for o in strad if o[4] != net and o[0] - r - clr < u + lat and o[1] + r + clr > u - lat]
        obsof.append(obs)
        ok.append(not obs or any(
            all(math.hypot(max(o[0] - x, 0, x - o[1]), max(o[2] - y, 0, y - o[3])) >= r + clr - 1e-9 for o in obs)
            for x in (u - lat + 2 * lat * i / nu for i in range(nu + 1))
            for y in (v_lo + (v_hi - v_lo) * j / nv for j in range(nv + 1))))
    # a run of neighbouring used pins of ONE net (a power entry: J1's six +5V_IN pins) is joined on the part's
    # own layer and drops where any of them has a site: J1 pin 79 alone is shut by C104.2, 77 and 81 are not
    bad = []
    for i, (u, net, pin) in enumerate(usedp):
        if ok[i]:
            continue
        a_, b_ = i, i
        while a_ > 0 and usedp[a_ - 1][1] == net:
            a_ -= 1
        while b_ + 1 < len(usedp) and usedp[b_ + 1][1] == net:
            b_ += 1
        if not any(ok[a_:b_ + 1]):
            bad.append((pin, net.split("/")[-1], sorted({o[5] for o in obsof[i]})))
    if not bad:
        return ""
    who = sorted({x for _p, _n, o in bad for x in o})
    return (f"{len(bad)} used pin(s) with no via site past the pads of {', '.join(who)} (a part whose courtyard "
            f"straddles the row's outer edge): " + ", ".join(f"pin {p} {n}" for p, n, _o in bad))


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
    usedp = sorted((U(*pad_xy(B, p)), p.GetNetname(), p.GetNumber()) for p in pads
                   if p.GetNetname() and not skip.search(p.GetNetname().split("/")[-1]))
    used = [u for u, _n, _p in usedp]
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
    strad = []          # pads of parts whose courtyard straddles the row's outer edge: (u0, u1, v0, v1, net, ref.pad)
    if c.get("strip") is not None:
        end, what = outer + float(c["strip"]) - edge, f"{float(c['strip']):.2f} mm strip (given)"
    else:
        end, what = None, None
        # Every courtyard that overlaps the via strip (u in [lo, hi], v beyond the pads' outer edge) is tested.
        # One wholly beyond that edge ends the strip at its near edge, for the whole row (unchanged). One that
        # STRADDLES the edge (starts level with or behind it) was skipped by a `near edge > outer` test: R37 at
        # F (23.70, 39.10), courtyard from y 38.63, its pads 0.09 mm into B-side J2's south strip, read PASS.
        # Such a part (on the other side, under the row: Y100 and C104 under J1, J400 beside J2) cannot end
        # the strip for the whole row -- J1 has 22 vias laid between and inside those pads -- so its PADS are
        # tested, each pin on its own (straddle_check).
        for ref, g in B.fps.items():
            if ref == c["ref"]:
                continue
            cy = B.courtyard(g)
            if not cy:
                continue
            us = sorted((U(cy[0], cy[1]), U(cy[2], cy[3])))
            vs = sorted((V(cy[0], cy[1]), V(cy[2], cy[3])))
            if not (us[1] >= lo and us[0] <= hi and vs[1] > outer + 1e-6):
                continue
            if vs[0] > outer:
                if end is None or vs[0] < end:
                    end, what = vs[0], f"{ref}'s courtyard"
                continue
            for q in g.Pads():
                bx = B.box(q.GetBoundingBox())
                qu = sorted((U(bx[0], bx[1]), U(bx[2], bx[3])))
                qv = sorted((V(bx[0], bx[1]), V(bx[2], bx[3])))
                if qu[1] >= lo and qu[0] <= hi and qv[1] > outer + 1e-6:
                    strad.append((qu[0], qu[1], qv[0], qv[1], q.GetNetname(), f"{ref}.{q.GetNumber()}"))
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
    # the straddlers are judged on the strip as found, and on its first via row at least when a nearer
    # obstacle shuts it -- so each part that must move is named in one run (moves2: R405 and R37)
    sfail = straddle_check(usedp, strad, outer + gap + r, max(end - r, outer + gap + r), lat, r, clr)
    if best is None:
        return "FAIL", (f"{len(used)} used pin(s), no room for one via row ({what}, {edge:g} mm kept, "
                        f"via {pad_d:g} at {gap:g} from the pads)" + (f"; and {sfail}" if sfail else ""))
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
                        "a pin open or a via size the handoff must decide -- ask at placement"
                        + (f"; and {sfail}" if sfail else ""))
    if sfail:
        return "FAIL", f"{sfail} ({head})"
    return "PASS", f"{len(used)} used pins fit ({head})"


# -- main ----------------------------------------------------------------------

# -- groups ---------------------------------------------------------------------

def run_count(g, board, sig, keep):
    """One group's feasibility count -> (status, why).  Logged as the group's count (budget.py)."""
    cmd = [str(x).replace("{board}", str(board)) for x in g["count"]]
    if cmd and cmd[0].endswith(".py"):
        cmd = [sys.executable] + cmd
    hb = budget.home(board)
    tok = None
    if hb is not None:
        try:
            tok = budget.gate(board, g["id"], g["_nets"], kind="count", matched=bool(g.get("matched")),
                              label="prelayout: " + " ".join(cmd[1:])[:140])
        except SystemExit:                                # its one count on this placement is used: say what it said
            c = next((e for e in reversed(budget.read(hb)["groups"].get(g["id"], {}).get("events", []))
                      if e["kind"] == "count"), None)
            if c and c.get("s") is not None:
                return ("PASS" if c.get("ok") else "FAIL",
                        f"counted already on this placement ({c.get('label', '')}): {c.get('result', '')}")
            return "TODO", "its one feasibility count on this placement is running or died without an answer"
    t0 = time.time()
    env = dict(os.environ, **{budget.ENV_IN: "count"})
    try:
        r = subprocess.run(cmd, cwd=str(budget.KP), capture_output=True, text=True, timeout=budget.COUNT_S, env=env)
        rc, out = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        rc, out = None, ""
    except OSError as e:
        rc, out = 127, str(e)
    secs = time.time() - t0
    if rc is None:
        status, why = "FAIL", f"no answer in {budget.dur(budget.COUNT_S)}: it does not fit"
    else:
        tail_ = last(out, r"\S")[:200]
        status = "PASS" if rc == 0 else "FAIL"
        why = (f"fits ({budget.dur(secs)})" if rc == 0 else f"does not fit (exit {rc}, {budget.dur(secs)})") \
            + (f": {tail_}" if tail_ else "")
    if tok:
        tok.end(why, ok=status == "PASS")
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text(json.dumps(dict(sig=sig, status=status, why=why, s=round(secs, 1), cmd=cmd), indent=1) + "\n")
    return status, why


def groups_test(B, board, waive, no_count):
    gf = board / "routing" / "groups.json"
    if not gf.exists():
        if not waive("groups", "board"):
            say("groups", "board", "TODO", "no routing/groups.json: the routing groups are not declared, so none has "
                "a feasibility count and none a budget tier (this file's docstring, \"groups\")")
        return
    G = json.loads(gf.read_text())
    pads = defaultdict(int)
    for f in B.fps.values():
        for p in f.Pads():
            if p.GetNetname():
                pads[budget.short(p.GetNetname())] += 1
    nets = sorted(n for n, k in pads.items() if k >= 2)
    pours = [re.compile(x) for x in G.get("pours", [])]
    owner = defaultdict(list)
    groups = [g for g in G.get("groups", []) if g.get("id")]
    for g in groups:
        pats = [re.compile(x) for x in g.get("nets", [])]
        g["_nets"] = [n for n in nets if any(x.search(n) for x in pats)]
        for n in g["_nets"]:
            owner[n].append(g["id"])
    free = [n for n in nets if n not in owner and not any(x.search(n) for x in pours)]
    twice = sorted(n for n, o in owner.items() if len(o) > 1)
    if not waive("groups", "coverage"):
        if free or twice:
            say("groups", "coverage", "FAIL",
                "; ".join(x for x in (
                    f"{len(free)} net(s) in no group and not a pour: {', '.join(free[:12])}{' ...' if len(free) > 12 else ''}"
                    if free else "",
                    f"{len(twice)} net(s) in two groups: " + ", ".join(f"{n} ({'/'.join(owner[n])})" for n in twice[:8])
                    if twice else "") if x))
        else:
            say("groups", "coverage", "PASS", f"{len(owner)} net(s) in {len(groups)} group(s), "
                f"{len(nets) - len(owner)} pour net(s)")
    hb = budget.home(board) or board
    todo = []
    for g in groups:
        tier, basis = budget.classify(hb, g["_nets"], bool(g.get("matched")))
        g["_tier"] = f"tier {tier} ({basis})"
        if waive("groups", g["id"]):
            continue
        if not g["_nets"]:
            say("groups", g["id"], "FAIL", "its patterns match no net of the board")
        elif g.get("covered") and not g.get("count"):
            say("groups", g["id"], "PASS", f"{g['_tier']}; covered by: {g['covered']}")
        elif not g.get("count"):
            say("groups", g["id"], "TODO", f"{g['_tier']}; neither a \"count\" that shows it fits nor \"covered\"")
        else:
            sig = hashlib.sha256(json.dumps([g["count"], budget.handoff_rev(hb),
                                             budget.parts_sha(board, g["_nets"])]).encode()).hexdigest()[:16]
            keep = board / "routing" / "prelayout" / f"count_{re.sub(r'[^A-Za-z0-9_.-]', '_', g['id'])}.json"
            try:
                K = json.loads(keep.read_text())
            except (OSError, ValueError):
                K = {}
            if K.get("sig") == sig:
                say("groups", g["id"], K["status"], f"{g['_tier']}; {K['why']} (kept: the group's parts have not moved)")
            elif no_count:
                say("groups", g["id"], "TODO", f"{g['_tier']}; not counted on this placement (--no-count)")
            else:
                todo.append((g, sig, keep))
    if todo:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, min(4, (os.cpu_count() or 2) // 4))) as pool:
            for (g, _s, _k), (status, why) in zip(todo, pool.map(lambda t: run_count(t[0], board, t[1], t[2]), todo)):
                say("groups", g["id"], status, f"{g['_tier']}; {why}")


def selftest():
    """straddle_check on hand-made rows (frame: u along the row, v away from it; outer edge v = 0)."""
    r, clr, lat = 0.2, 0.1016, 0.6
    v0, v1 = 0.1016 + r, 0.87                                   # J2-like: via 0.4 at 0.1016, 1.27 mm strip less 0.4
    row = [(u, f"N{u:.1f}", str(i)) for i, u in enumerate((0.0, 0.4, 0.8, 1.2, 1.6))]
    r37 = [(0.30, 0.86, 0.09, 0.71, "X", "R37.1"), (1.14, 1.70, 0.09, 0.71, "Y", "R37.2")]
    cases = [
        ("nothing in the strip", row, [], ""),
        ("R37-like 0402 over the middle pins", row, r37, "pin 2"),
        ("a pad deep in the strip leaves room nearer the pins", row, [(0.3, 1.7, 0.95, 1.4, "X", "C1.1")], ""),
        ("a pad of the pin's own net takes its via", [(0.8, "X", "2")], [(0.3, 1.3, 0.0, 0.9, "X", "C1.1")], ""),
        ("the same pad, another net", [(0.8, "Z", "2")], [(0.3, 1.3, 0.0, 0.9, "X", "C1.1")], "pin 2"),
        ("a same-net run drops where one pin has a site", [(0.4, "P", "77"), (0.8, "P", "79"), (1.2, "P", "81")],
         [(0.45, 1.15, 0.0, 0.9, "G", "C104.2")], ""),
        ("a lone pin under that pad", [(0.4, "P", "77"), (0.8, "Q", "79"), (1.2, "P", "81")],
         [(0.45, 1.15, 0.0, 0.9, "G", "C104.2")], "pin 79"),
        ("a pin with a gap beside the pad", [(0.0, "Q", "1")], [(0.3, 1.3, 0.0, 0.9, "G", "C1.1")], ""),
        ("first row only (the strip shut nearer)", row, r37, "pin 2"),
    ]
    bad = 0
    for i, (what, used, strad, want) in enumerate(cases):
        got = straddle_check(used, strad, v0, v0 if i == len(cases) - 1 else v1, lat, r, clr)
        good = (want in got) if want else not got
        bad += not good
        print(f"{'ok  ' if good else 'FAIL'} {what}: {got or 'no pin shut'}")
    print("SELFTEST PASS" if not bad else f"SELFTEST FAIL ({bad})")
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv[1:]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--spec", default="routing/prelayout.json")
    ap.add_argument("--no-count", action="store_true",
                    help="groups: run no feasibility count; report the kept results only (TODO where there is none)")
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
            # one side of a part waived on its own ("target": "U400:S"): the other sides still run
            if waive("rows", f"{ref}:{c['side']}"):
                continue
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

    groups_test(B, board, waive, a.no_count)

    n_fail, n_todo = LINES.count("FAIL"), LINES.count("TODO")
    if n_fail or n_todo:
        print(f"PRELAYOUT FAIL ({n_fail} fail, {n_todo} todo, {LINES.count('PASS')} pass, "
              f"{LINES.count('WAIVED')} waived)")
        return 1
    print(f"PRELAYOUT PASS ({LINES.count('PASS')} pass, {LINES.count('WAIVED')} waived)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
