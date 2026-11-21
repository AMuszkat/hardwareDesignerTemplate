#!/usr/bin/env python3
"""A fine-pitch BGA's fan-out by ring and site, as a plan: NXP's rule (AN10778 §4.3; board1 D115).

    tools/.venv/bin/python tools/route/bga_fanout.py plan --board board1 --config routing/U1_fanout.json \
        --out routing/U1_fanout_plan.json [--vias-out routing/U1_vias.json]
    tools/kpy tools/route/bga_fanout.py balls --board board1 --ref U1 --out balls.json   # (plan calls it itself)

The plan needs OR-Tools (tools/.venv); the balls come from the board through tools/kpy (pcbnew) -- `plan`
runs that step itself unless the config (or --balls) names a dump.  Nothing is written to a board.

THE RULE (every number from the config, none from here):
  rings 1..R (`rings_top`, NXP: 2) leave on the TOP layer: a ring-1 ball straight out of its land, a
     ring-2 ball through a LANE -- the gap between two ring-1 lands, or an empty ring-1 site -- one track a
     lane.  `lanes` below counts, per side, ring-2 tracks against the lanes they can reach (k-1/2, k+1/2,
     k if ring-1 site k is empty): an exact bipartite matching, corner ring-2 balls on either side.
  ring 1 power/ground balls go OUT of the field to a via (corners diagonally): no site.
  every other connected ball (ring >= 3, and ring-2 power/ground under `pg_ring2: "site"`) takes a via in an
     EMPTY lattice site next to it, orthogonal or diagonal (the stub's clearances are computed from the
     config and printed; a diagonal that does not clear is dropped), sites in ring < `min_site_ring` excluded
     (a via there walls the top-layer lanes).  One net a site.  A signal ball never shares; a power/ground
     ball shares with at most `share_cap`-1 others of its net, each on its own stub.  No two diagonal stubs
     in one lattice square (they would cross).
  the assignment is EXACT (CP-SAT), lexicographic: (1) every ball served; (2) the most vias -- share no
     more than the field needs; (3) the weighted vias, `core_nets` weighted highest -- they share last;
     (4) the fewest diagonal stubs.  An infeasible (1) is reported with the balls the solver could not
     serve when each is relaxed alone (`--why`).

CONFIG (JSON, the board's numbers, each with its source):
  {"ref": "U1", "land": 0.225, "via": [0.4, 0.2], "width": 0.0889, "clearance": 0.0889,
   "rings_top": 2, "pg_ring2": "site", "min_site_ring": 2, "share_cap": 4,
   "power_nets": "^(GND|VDD|NVCC|\\\\+)", "core_nets": "^(VDD_ARM|VDD_SOC)$",
   "weights": {"core": 4, "power": 2, "ground": 1}, "ground_nets": "^GND$",
   "rail_current_A": {"VDD_ARM": 3.0, ...}, "source": "10_layout.md #39 ..."}

OUTPUT (--out): {"balls": {ball: {net, ring, kind, exit, side, lane | site, shared_with}}, "vias":
  [{x, y, net, balls}], "lanes": {side: {...}}, "rails": {net: {balls, vias, max_balls_a_via, A_a_via}},
  "counts": {...}}.  --vias-out: fanout.py's model format (one entry per ball served by a site via), the
  input escape_bound.py and `fanout.py layers` take.  Exit 0 when every ball is served and every ring-2
  track has a lane; 1 otherwise (the counts say which).
"""
import argparse
import collections
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
EPS = 1e-6


def short(n):
    return (n or "").split("/")[-1]


def connected(n):
    s = short(n)
    return bool(s) and not s.startswith("unconnected")


# ----------------------------------------------------------------------------- balls
def cmd_balls(a):
    sys.path[:0] = [str(HERE), str(TOOLS / "place")]
    import fanout
    ns = argparse.Namespace(balls=None, board=a.board, ref=a.ref)
    b = fanout.load_balls(ns)
    json.dump(b, open(a.out, "w"), indent=0)
    print(f"{len(b)} balls of {a.ref} -> {a.out}")
    return 0


def board_dir(board):
    sys.path.insert(0, str(TOOLS))
    import boardsel
    for f in ("board_dir_arg", "resolve", "board_dir"):
        if hasattr(boardsel, f):
            try:
                return Path(getattr(boardsel, f)(board))
            except Exception:
                pass
    return Path(board)


def load_balls(a, cfg, bdir):
    src = a.balls or cfg.get("balls")
    if src:
        p = Path(src)
        if not p.is_absolute():
            p = (bdir / p) if bdir and (bdir / p).exists() else p
        return json.load(open(p)), str(p)
    out = Path(a.out).with_suffix(".balls.json")
    cmd = [str(TOOLS / "kpy"), str(HERE / "bga_fanout.py"), "balls", "--ref", cfg["ref"], "--out", str(out)]
    if a.board:
        cmd += ["--board", a.board]
    subprocess.run(cmd, check=True)
    return json.load(open(out)), str(out)


# ----------------------------------------------------------------------------- the lattice
class Field:
    def __init__(self, balls):
        xs = sorted({round(b["x"], 4) for b in balls})
        ys = sorted({round(b["y"], 4) for b in balls})
        self.p = min(q - p for p, q in zip(xs, xs[1:]) if q - p > 1e-3)
        py = min(q - p for p, q in zip(ys, ys[1:]) if q - p > 1e-3)
        if abs(py - self.p) > 1e-3:
            sys.exit(f"pitch {self.p} x {py}: a rectangular pitch is not handled")
        self.x0, self.y0 = xs[0], ys[0]
        self.nx = round((xs[-1] - xs[0]) / self.p) + 1
        self.ny = round((ys[-1] - ys[0]) / self.p) + 1
        self.at = {}
        for b in balls:
            i, j = round((b["x"] - self.x0) / self.p), round((b["y"] - self.y0) / self.p)
            if abs(self.x0 + i * self.p - b["x"]) > 1e-3 or abs(self.y0 + j * self.p - b["y"]) > 1e-3:
                sys.exit(f"ball {b['ball']} off the lattice")
            self.at[(i, j)] = b

    def xy(self, q):
        return (round(self.x0 + q[0] * self.p, 4), round(self.y0 + q[1] * self.p, 4))

    def ring(self, q):
        return min(q[0], q[1], self.nx - 1 - q[0], self.ny - 1 - q[1]) + 1

    def inside(self, q):
        return 0 <= q[0] < self.nx and 0 <= q[1] < self.ny

    def sides(self, q):
        """The sides a ball on ring r faces (both at a corner): N = y min, S = y max, W = x min, E = x max."""
        r = self.ring(q) - 1
        s = []
        if q[1] == r:
            s.append(("N", q[0]))
        if q[1] == self.ny - 1 - r:
            s.append(("S", q[0]))
        if q[0] == r:
            s.append(("W", q[1]))
        if q[0] == self.nx - 1 - r:
            s.append(("E", q[1]))
        return s

    def ring1_site(self, side, k):
        return {"N": (k, 0), "S": (k, self.ny - 1), "W": (0, k), "E": (self.nx - 1, k)}[side]


def geometry(F, cfg):
    p, land, (vp, _vd), w, c = F.p, cfg["land"], cfg["via"], cfg["width"], cfg["clearance"]
    g = {
        "via_land": p - vp / 2 - land / 2,                       # site via to an orthogonal neighbour's land
        "via_via": p - vp,                                       # two vias in neighbouring sites, pad to pad
        "diag_stub_land": p / math.sqrt(2) - land / 2 - w / 2,   # a diagonal stub to the two corner lands
        "diag_stub_via": p / math.sqrt(2) - vp / 2 - w / 2,      # a diagonal stub to a via in a corner site
        "lane_gap": (p - land) / 2 - w / 2,                      # one track between two lands, a side
        "lane_pitch_gap": p / 2 - w,                             # two lanes side by side (k, k+1/2), edge to edge
    }
    g["site_ok"] = g["via_land"] + EPS >= c and g["via_via"] + EPS >= c
    g["diag_ok"] = g["diag_stub_land"] + EPS >= c and g["diag_stub_via"] + EPS >= c
    g["lane_ok"] = g["lane_gap"] + EPS >= c
    return g


# ----------------------------------------------------------------------------- classify
def classify(F, cfg):
    pw = re.compile(cfg["power_nets"])
    gn = re.compile(cfg.get("ground_nets", "^GND$"))
    core = re.compile(cfg.get("core_nets", "^$"))
    excl = set(cfg.get("exclude_balls", []))
    R = cfg.get("rings_top", 2)
    out = {}
    for q, b in F.at.items():
        n = short(b["net"])
        r = F.ring(q)
        if not connected(b["net"]) or b["ball"] in excl:
            out[q] = dict(kind="none", ring=r)
            continue
        if pw.search(n):
            k = "ground" if gn.search(n) else "core" if core.search(n) else "power"
            pg = True
        else:
            k, pg = "signal", False
        if r == 1:
            ex = "out" if pg else "top"
        elif r <= R:
            ex = ("site" if cfg.get("pg_ring2", "site") == "site" else "out") if pg else "top"
        else:
            ex = "site"
        out[q] = dict(kind=k, ring=r, exit=ex, pg=pg)
    return out


# ----------------------------------------------------------------------------- lanes (rings 1..R, top layer)
def lanes(F, cls):
    """Ring-2 top-layer tracks against lanes, exact (max bipartite matching)."""
    lane_of = collections.defaultdict(list)          # ring-2 ball -> [(side, pos2)] (pos in half-pitches)
    side_lanes = collections.defaultdict(set)
    r1 = collections.Counter()
    for q, c in cls.items():
        if c["kind"] == "none" or c.get("exit") != "top":
            continue
        if c["ring"] == 1:
            for s, k in F.sides(q)[:1]:
                r1[s] += 1
        elif c["ring"] == 2:
            for s, k in F.sides(q):
                for d in (-1, 1):
                    lane_of[q].append((s, 2 * k + d))
                if F.ring1_site(s, k) not in F.at:
                    lane_of[q].append((s, 2 * k))
    n = F.nx  # square field
    for s in "NSWE":
        for k2 in range(1, 2 * (n - 1)):
            if k2 % 2 or F.ring1_site(s, k2 // 2) not in F.at:
                side_lanes[s].add(k2)
    owner = {}

    def aug(q, seen):
        for L in lane_of[q]:
            if L in seen:
                continue
            seen.add(L)
            if L not in owner or aug(owner[L], seen):
                owner[L] = q
                return True
        return False
    unmatched = [q for q in sorted(lane_of) if not aug(q, set())]
    got = {q: L for L, q in owner.items()}
    rep = {}
    for s in "NSWE":
        r2 = [q for q in lane_of if got.get(q, (None,))[0] == s or (q not in got and F.sides(q)[0][0] == s)]
        rep[s] = dict(ring1_tracks=r1[s], ring2_tracks=len(r2), lanes=len(side_lanes[s]),
                      ring2_laned=sum(1 for q in r2 if q in got),
                      no_lane=[F.at[q]["ball"] for q in r2 if q not in got])
    return got, unmatched, rep


# ----------------------------------------------------------------------------- sites (CP-SAT)
def solve_sites(F, g, cls, cfg, workers):
    from ortools.sat.python import cp_model
    cap = cfg.get("share_cap", 4)
    msr = cfg.get("min_site_ring", 2)
    wts = cfg.get("weights", {"core": 4, "power": 2, "ground": 1})
    steps = [(1, 0), (-1, 0), (0, 1), (0, -1)] + ([(1, 1), (1, -1), (-1, 1), (-1, -1)] if g["diag_ok"] else [])
    need = [q for q, c in cls.items() if c.get("exit") == "site"]
    cand = {}
    for q in need:
        cand[q] = [(q[0] + di, q[1] + dj) for di, dj in steps
                   if F.inside((q[0] + di, q[1] + dj)) and (q[0] + di, q[1] + dj) not in F.at
                   and F.ring((q[0] + di, q[1] + dj)) >= msr]
    m = cp_model.CpModel()
    x = {(q, s): m.NewBoolVar("") for q in need for s in cand[q]}
    key = {q: (short(F.at[q]["net"]) if cls[q]["pg"] else "SIG:" + F.at[q]["ball"]) for q in need}
    by_site = collections.defaultdict(list)
    for (q, s) in x:
        by_site[s].append(q)
    y = {}
    for s, qs in by_site.items():
        for k in {key[q] for q in qs}:
            y[(s, k)] = m.NewBoolVar("")
        m.Add(sum(y[(s, k)] for k in {key[q] for q in qs}) <= 1)
        for k in {key[q] for q in qs}:
            mine = [x[(q, s)] for q in qs if key[q] == k]
            for v in mine:
                m.AddImplication(v, y[(s, k)])
            m.Add(sum(mine) >= y[(s, k)])
            m.Add(sum(mine) <= (cap if not k.startswith("SIG:") else 1))
    for q in need:
        m.Add(sum(x[(q, s)] for s in cand[q]) == 1)
    diag = collections.defaultdict(list)             # lattice square (lower-left corner) -> its diagonal stubs
    for (q, s), v in x.items():
        if q[0] != s[0] and q[1] != s[1]:
            diag[(min(q[0], s[0]), min(q[1], s[1]))].append(v)
    for vs in diag.values():
        if len(vs) > 1:
            m.Add(sum(vs) <= 1)
    nvias = sum(y.values())
    kind_of = {}
    for q in need:
        kind_of.setdefault(key[q], cls[q]["kind"])
    wv = sum((1 if k.startswith("SIG:") else wts[kind_of[k]]) * v for (s, k), v in y.items())
    nd = sum(v for (q, s), v in x.items() if q[0] != s[0] and q[1] != s[1])
    sv = cp_model.CpSolver()
    sv.parameters.num_workers = workers
    sv.parameters.max_time_in_seconds = 120
    stages = []
    for name, obj, sense in (("vias", nvias, 1), ("weighted", wv, 1), ("diagonals", nd, -1)):
        m.Maximize(obj) if sense > 0 else m.Minimize(obj)
        st = sv.Solve(m)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None, dict(status=sv.StatusName(st), need=len(need)), cand
        val = round(sv.ObjectiveValue())
        stages.append((name, val, sv.StatusName(st)))
        m.ClearHints()
        for v in x.values():
            m.AddHint(v, sv.Value(v))
        m.Add(obj == val) if st == cp_model.OPTIMAL else (m.Add(obj >= val) if sense > 0 else m.Add(obj <= val))
    pick = {q: s for (q, s), v in x.items() if sv.Value(v)}
    return pick, dict(status="ok", stages=stages, need=len(need)), cand


# ----------------------------------------------------------------------------- plan
def cmd_plan(a):
    bdir = board_dir(a.board) if a.board else None
    cp = Path(a.config)
    if not cp.is_absolute() and bdir and (bdir / cp).exists():
        cp = bdir / cp
    cfg = json.load(open(cp))
    if bdir and not Path(a.out).is_absolute():
        a.out = str(bdir / a.out)
    balls, bsrc = load_balls(a, cfg, bdir)
    for b in balls:
        if cfg.get("land"):
            b["d"] = cfg["land"]
    F = Field(balls)
    g = geometry(F, cfg)
    c = cfg["clearance"]
    print(f"{cfg['ref']}: {F.nx} x {F.ny} lattice at {F.p} mm, {len(F.at)} balls, "
          f"{F.nx * F.ny - len(F.at)} empty sites; land {cfg['land']}, via {cfg['via'][0]}/{cfg['via'][1]}, "
          f"track {cfg['width']} / clearance {c}")
    print(f"  site via to a neighbour land {g['via_land']:.4f}, to a neighbour via {g['via_via']:.4f} -> "
          f"{'fits' if g['site_ok'] else 'DOES NOT FIT'}")
    print(f"  diagonal stub to corner lands {g['diag_stub_land']:.4f}, to a corner-site via {g['diag_stub_via']:.4f}"
          f" -> {'allowed' if g['diag_ok'] else 'dropped'}")
    print(f"  one track between two lands {g['lane_gap']:.4f} a side; two lanes 1/2 pitch apart "
          f"{g['lane_pitch_gap']:.4f} edge to edge -> {'fits' if g['lane_ok'] else 'DOES NOT FIT'}")
    if not g["site_ok"]:
        return 2
    cls = classify(F, cfg)
    got, unmatched, lrep = lanes(F, cls)
    pick, info, cand = solve_sites(F, g, cls, cfg, a.workers)

    cnt = collections.Counter()
    for q, cc in cls.items():
        if cc["kind"] == "none":
            cnt["none"] += 1
            continue
        t = "signal" if cc["kind"] == "signal" else "pg"
        cnt[(t, "ring<=%d" % cfg.get("rings_top", 2) if cc["ring"] <= cfg.get("rings_top", 2) else "ring3+")] += 1
        cnt[(t, cc["exit"])] += 1
    vias = {}
    if pick:
        for q, s in pick.items():
            vias.setdefault(s, []).append(q)
    out_b = {}
    for q, cc in sorted(cls.items(), key=lambda t: F.at[t[0]]["ball"]):
        b = F.at[q]
        e = dict(net=short(b["net"]), ring=cc["ring"], kind=cc["kind"], x=b["x"], y=b["y"])
        if cc["kind"] != "none":
            e["exit"] = cc["exit"]
            if cc["exit"] == "top":
                sd = F.sides(q)
                e["side"] = sd[0][0] if cc["ring"] == 1 else (got[q][0] if q in got else sd[0][0])
                if cc["ring"] >= 2:
                    e["lane"] = got[q][1] / 2 if q in got else None
            elif cc["exit"] == "out":
                sd = F.sides(q)
                e["side"] = "".join(s for s, _ in sd)
                e["diagonal"] = len(sd) > 1
            elif pick and q in pick:
                s = pick[q]
                e["site"] = list(F.xy(s))
                e["stub"] = "diagonal" if (s[0] != q[0] and s[1] != q[1]) else "orthogonal"
                e["shared_with"] = [F.at[o]["ball"] for o in vias[s] if o != q]
        out_b[b["ball"]] = e
    vlist = [dict(x=F.xy(s)[0], y=F.xy(s)[1], net=short(F.at[qs[0]]["net"]),
                  balls=sorted(F.at[q]["ball"] for q in qs)) for s, qs in sorted(vias.items())]
    rails = {}
    cur = cfg.get("rail_current_A", {})
    pgnet = {short(F.at[q]["net"]) for q, cc in cls.items() if cc.get("pg")}
    for v in vlist:
        if v["net"] not in pgnet:
            continue
        r = rails.setdefault(v["net"], dict(balls=0, vias=0, hist=collections.Counter()))
        r["balls"] += len(v["balls"])
        r["vias"] += 1
        r["hist"][len(v["balls"])] += 1
    for q, cc in cls.items():                       # pg balls that leave the field (ring 1, or ring 2 'out')
        if cc.get("exit") == "out":
            r = rails.setdefault(short(F.at[q]["net"]), dict(balls=0, vias=0, hist=collections.Counter()))
            r["out_balls"] = r.get("out_balls", 0) + 1
    for n, r in rails.items():
        r["max_balls_a_via"] = max(r["hist"]) if r["hist"] else 0
        r["balls_a_via"] = {str(k): v for k, v in sorted(r.pop("hist").items())}
        if n in cur and r["vias"]:
            r["I_A"] = cur[n]
            r["A_a_via"] = round(cur[n] / (r["vias"] + r.get("out_balls", 0)), 4)
            r["A_a_ball"] = round(cur[n] / (r["balls"] + r.get("out_balls", 0)), 4)
    sig_site = sum(1 for v in vlist if not cls[next(q for q in vias[(round((v["x"] - F.x0) / F.p),
                                                                    round((v["y"] - F.y0) / F.p))])]["pg"])
    pg_vias = len(vlist) - sig_site
    pg_site_balls = sum(len(v["balls"]) for v in vlist) - sig_site
    counts = dict(
        balls=len(F.at), unconnected_or_excluded=cnt["none"],
        signal_top=cnt[("signal", "top")], signal_site=cnt[("signal", "site")],
        signal_total=cnt[("signal", "top")] + cnt[("signal", "site")],
        pg_out=cnt[("pg", "out")], pg_site=cnt[("pg", "site")],
        site_balls_needed=info["need"], served=len(pick or {}),
        vias=len(vlist), signal_vias=sig_site, pg_vias=pg_vias,
        pg_balls_without_own_via=pg_site_balls - pg_vias,
        diagonal_stubs=sum(1 for e in out_b.values() if e.get("stub") == "diagonal"),
        ring2_no_lane=[F.at[q]["ball"] for q in unmatched], solver=info)
    plan = dict(ref=cfg["ref"], config=str(cp), balls_from=bsrc, geometry=g, counts=counts, lanes=lrep,
                rails=rails, vias=vlist, balls=out_b, source=cfg.get("source", ""))
    json.dump(plan, open(a.out, "w"), indent=1)

    print(f"signal balls: {counts['signal_total']} -- {counts['signal_top']} leave on top (rings 1-"
          f"{cfg.get('rings_top', 2)}), {counts['signal_site']} on a site via")
    print(f"power/ground balls: {cnt[('pg', 'top')] + counts['pg_out'] + counts['pg_site']} -- "
          f"{counts['pg_out']} out of the field, {counts['pg_site']} on site vias")
    print(f"site vias: {info['need']} balls need one; " + (
        f"served {counts['served']} on {counts['vias']} vias ({counts['signal_vias']} signal, "
        f"{counts['pg_vias']} power/ground); {counts['pg_balls_without_own_via']} balls without a via of their "
        f"own; {counts['diagonal_stubs']} diagonal stubs; solver {info['stages']}"
        if pick else f"NOT SERVED -- {info['status']}"))
    print("rails (site vias; out = balls leaving the field):")
    for n, r in sorted(rails.items(), key=lambda t: -t[1]["balls"]):
        print(f"  {n:20s} balls {r['balls']:3d}  vias {r['vias']:3d}  out {r.get('out_balls', 0)}  "
              f"balls a via {r['balls_a_via']}" + (f"  {r['I_A']} A -> {r['A_a_via']} A a via" if 'I_A' in r else ""))
    print("top-layer exits, rings 1-2 (lanes = gaps between ring-1 lands + empty ring-1 sites):")
    for s, r in lrep.items():
        print(f"  {s}: ring-1 {r['ring1_tracks']:3d} straight; ring-2 {r['ring2_tracks']:3d} tracks, {r['lanes']:3d}"
              f" lanes, {r['ring2_laned']:3d} laned" + (f"  NO LANE: {' '.join(r['no_lane'])}" if r['no_lane'] else ""))
    if a.vias_out:
        vo = Path(a.vias_out)
        if bdir and not vo.is_absolute():
            vo = bdir / vo
        flat = [dict(ball=bb, net=F.at[q]["net"], x=F.xy(s)[0], y=F.xy(s)[1], bx=F.at[q]["x"], by=F.at[q]["y"],
                     in_pad=False, kind="share" if cls[q]["pg"] else "own")
                for s, qs in vias.items() for q in qs for bb in [F.at[q]["ball"]]]
        json.dump(flat, open(vo, "w"), indent=0)
        print(f"wrote {vo} ({len(flat)} ball->via entries, fanout.py's model format)")
    print(f"wrote {a.out}")
    ok = bool(pick) and not unmatched
    print("BGA_FANOUT " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("balls")
    b.add_argument("--board")
    b.add_argument("--ref", required=True)
    b.add_argument("--out", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--board")
    p.add_argument("--config", required=True)
    p.add_argument("--balls", help="a ball dump [{ball, x, y, net, d}] instead of the board")
    p.add_argument("--out", required=True)
    p.add_argument("--vias-out")
    p.add_argument("--workers", type=int, default=int(os.environ.get("HW_JOBS", "8")))
    a = ap.parse_args()
    return cmd_balls(a) if a.cmd == "balls" else cmd_plan(a)


if __name__ == "__main__":
    sys.exit(main())
