#!/usr/bin/env python3
"""The group router stops when it cannot end, and says why -- board-free, on maze.py alone.

    python3 tests/test_router_stall.py

What happened on a live board (board1's R2, one bus on three layers): one run negotiated 433
iterations and three minutes over six cells two nets both needed, and one said "no solution"
about a net shut in at its start by a reserved via.  Neither said which nets, where, or by what.
maze.Grid now does; this test holds it to that on five fixtures, each with BOTH search cores (the
compiled one when it is built, and the Python one) and the same answer from each:

  channel    two nets, one wall, one gap one track wide.  negotiate() must stop as STUCK within
             N + 5 iterations (N = the `stuck` window, 20), name both nets, and give the gap's
             cells; with the detector off the same fixture uses every iteration it is given.
  lanes      three nets, a gap two tracks wide: the overlap moves from lane to lane, so no cell is
             contested all the time.  Stopped all the same, within 3 N, all three nets named.
  detour     the channel again, with a long clean way round the wall.  The overlaps sit on the
             same cells for more than N iterations here too -- the price of the detour is reached
             late -- and the run must NOT be stopped: it converges, and the way-round test is what
             held the stop back (it must have been asked, and said "there is a way").
  blocked    a net whose start is closed in by a labelled via and two labelled pads: blocked_end()
             names the net, the end ("source"), the cells it reaches and the via, nearest first;
             the same with the goal closed in ("goal"); a net that is not shut in gives None.
  converge   the harness's own router fixture (tests/agentic_test.py, 8 nets through a funnel):
             the same result hash and iteration count with the detector on, off, and at a window
             of ONE iteration -- it reads the grid and never moves a route.

Exit 0 and a last line `PASS: ...`, or exit 1 and `FAIL: ...`.
"""

import contextlib
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kicad_project" / "tools" / "route"))
sys.path.insert(0, str(ROOT / "tests"))
import maze  # noqa: E402

N = 20                      # the detector's window, negotiate()'s default
NEG = dict(pres0=0.5, pres_mul=1.1, hist_inc=0.5, hist_decay=0.7, seed=1, via_cost=2.0, limit=400000)
SAYS = "these nets need the same channel: give one another layer / move the via / widen the window"
fails = []


def check(ok, what):
    if not ok:
        fails.append(what)
    return ok


def grid(w, h, layers=("F.Cu",)):
    maze.CLR, maze.PADCLR, maze.W, maze.VIA_R = 0.15, 0.2, 0.2, 0.3
    G = maze.Grid(0.0, 0.0, w, h, list(layers), step=0.35, strict=True)   # one track per cell row
    G.diagonal = True
    G.only45 = True
    return G


def wall(G, x0, x1, open_rows, label="the wall"):
    """A wall from top to bottom between x0 and x1, open on the given cell rows.  Its cells."""
    cells = {j * G.nx + i for j in range(G.ny) if j not in open_rows
             for i in range(G.nx) if x0 <= G.xy(j * G.nx + i)[0] <= x1}
    G.mark(label)
    G.block(G.layers + ["via"], cells)
    return cells


def negotiate(G, jobs, iters, stuck):
    """-> (paths, iterations run, [(iteration, verdict) of every way-round test])."""
    log, asked = [], []
    inner = maze.Grid._stuck_test

    def spy(self, it, *a, **k):
        r = inner(self, it, *a, **k)
        asked.append((it, r))
        return r
    maze.Grid._stuck_test = spy
    try:
        paths = G.negotiate(jobs, iters=iters, log=log.append, stuck=stuck, **NEG)
    finally:
        maze.Grid._stuck_test = inner
    return paths, sum(1 for ln in log if "pres" in ln), asked


def channel(nets, lanes, stuck=N, iters=120):
    G = grid(20.0, 10.0)
    jm = G.ny // 2
    wall(G, 9.0, 11.0, set(range(jm, jm + lanes)))
    jobs = [dict(net=f"N{k}", src=(2.0, 3.0 + 4.0 * k / (nets - 1)), src_layer="F.Cu",
                 goals={G.cell(18.0, 3.0 + 4.0 * k / (nets - 1))}, goal_layer="F.Cu", allowed={"F.Cu"})
            for k in range(nets)]
    paths, its, asked = negotiate(G, jobs, iters, stuck)
    return G, paths, its, [G.xy(j * G.nx)[1] for j in range(jm, jm + lanes)]


def t_channel(tag):
    G, paths, its, rows = channel(2, 1)
    d = G.stuck
    if not check(paths is None and d is not None, f"{tag} channel: not stopped as stuck (iterations {its})"):
        return None
    b = d["boxes"].get("F.Cu", {})
    check(its <= N + 5, f"{tag} channel: stopped at iteration {its}, later than N + 5 = {N + 5}")
    check(sorted(d["nets"]) == ["N0", "N1"], f"{tag} channel: names {sorted(d['nets'])}, not N0 and N1")
    # the gap is the row y = rows[0] through a wall from x 9 to 11 (the nets' halos reach a cell or two past it)
    check(b and b["y0"] == b["y1"] == rows[0] and 8.0 <= b["x0"] <= 9.4 and 10.6 <= b["x1"] <= 12.0,
          f"{tag} channel: the box {b} is not the gap (y {rows[0]}, x 9 .. 11)")
    text = "\n".join(G.stuck_lines())
    check(SAYS in text and "N0" in text and "N1" in text and "STUCK at iteration" in text,
          f"{tag} channel: the printed block lacks the nets or the sentence:\n{text}")
    check(not d["open_if_lifted"], f"{tag} channel: claims a way with the other nets lifted: {d['open_if_lifted']}")
    G0, p0, its0, _r = channel(2, 1, stuck=0, iters=60)
    check(p0 is None and G0.stuck is None and its0 == 60,
          f"{tag} channel: with the detector off it should use all 60 iterations, used {its0}")
    return dict(d, at=its)


def t_lanes(tag):
    G, paths, its, rows = channel(3, 2)
    d = G.stuck
    if not check(paths is None and d is not None, f"{tag} lanes: not stopped as stuck (iterations {its})"):
        return None
    check(its <= 3 * N, f"{tag} lanes: stopped at iteration {its}, later than 3 N = {3 * N}")
    check(sorted(d["nets"]) == ["N0", "N1", "N2"], f"{tag} lanes: names {sorted(d['nets'])}, not all three")
    b = d["boxes"].get("F.Cu", {})
    check(b and b["y0"] <= rows[0] and rows[-1] <= b["y1"] and b["y1"] - b["y0"] < 1.5,
          f"{tag} lanes: the box {b} does not hold both lanes {rows}")
    return dict(d, at=its)


def t_detour(tag):
    """The gap near the top, the wall open along the bottom: a clean way of about 100 extra cells."""
    G = grid(12.0, 40.0)
    jm = 6
    wall(G, 5.65, 6.35, {jm} | set(range(G.ny - 3, G.ny)))
    ym = G.xy(jm * G.nx)[1]
    jobs = [dict(net=f"N{k}", src=(4.6, ym + (k - 0.5) * 1.4), src_layer="F.Cu",
                 goals={G.cell(7.4, ym + (k - 0.5) * 1.4)}, goal_layer="F.Cu", allowed={"F.Cu"}) for k in range(2)]
    paths, its, asked = negotiate(G, jobs, 120, N)
    check(paths is not None and G.stuck is None, f"{tag} detour: stopped (stuck {G.stuck}) though a way round exists")
    check(its > N, f"{tag} detour: converged in {its} iterations -- the fixture no longer outlasts the window {N}")
    check(asked and not any(r for _it, r in asked),
          f"{tag} detour: the way-round test was not what held the stop back (asked: {asked})")
    if paths:
        check(not any(G.conflicts(n, *rv) for n, rv in paths.items()), f"{tag} detour: the result overlaps")
    return dict(at=its, asked=len(asked))


VIA_LABEL = "via of GND at (5.000, 3.950), reserved by the spec's reserve_vias"


def pocket(end):
    """A net whose `end` sits in a pocket: pads above and below, a wall behind, and a reserved via
    in the mouth.  Without the via the pocket is open (the caller checks that too)."""
    G = grid(20.0, 8.0, ("F.Cu", "B.Cu"))
    G.mark("pad U1.1 (VDD) at (3.000, 2.850)")
    G.block(["F.Cu", "B.Cu", "via"], G.rect(1.0, 2.4, 5.0, 3.3))        # rows up to y 3.15
    G.mark("pad U1.2 (VDD) at (3.000, 5.000)")
    G.block(["F.Cu", "B.Cu", "via"], G.rect(1.0, 4.5, 5.0, 5.5))        # rows from y 4.55: the mouth is 3.5 .. 4.2
    G.mark("pad U1.3 (NC) at (1.000, 3.950)")
    G.block(["F.Cu", "B.Cu", "via"], G.rect(0.8, 2.4, 1.2, 5.5))
    inside, outside = (3.15, 3.85), (17.15, 3.85)
    a, b = (inside, outside) if end == "source" else (outside, inside)
    job = dict(net="D3", src=a, src_layer="F.Cu", goals={G.cell(*b)}, goal_layer="F.Cu", allowed={"F.Cu", "B.Cu"})
    return G, job


def t_blocked(tag):
    out = {}
    for end in ("source", "goal"):
        G, job = pocket(end)
        check(G.blocked_end(job) is None and G.blocked_end(job, cap=4000) is None,
              f"{tag} blocked {end}: an open pocket is called shut in")
        G.mark(VIA_LABEL)
        G.static_via(5.0, 3.95, 0.3)
        for cap in (0, 4000):
            d = G.blocked_end(job, cap=cap)
            if not check(d is not None, f"{tag} blocked {end} (cap {cap}): a shut pocket is not found"):
                continue
            check(d["net"] == "D3" and d["end"] == end, f"{tag} blocked {end}: says {d['net']} / {d['end']}")
            check(0 < d["cells"] < 400, f"{tag} blocked {end}: {d['cells']} cells reached, not a small pocket")
            labels = [r["label"] for r in d["wall"]]
            check(VIA_LABEL in labels and any("pad U1.1" in s for s in labels) and any("pad U1.2" in s for s in labels),
                  f"{tag} blocked {end}: the wall {labels} lacks the via or a pad")
            near = d["wall"][0]
            check(near["dist"] <= 1.6 and all(a["dist"] <= b["dist"] for a, b in zip(d["wall"], d["wall"][1:])),
                  f"{tag} blocked {end}: the wall is not listed nearest first")
            text = "\n".join(maze.Grid.blocked_lines(d))
            check(f"BLOCKED {end.upper()}: D3" in text and VIA_LABEL in text and "not congestion" in text,
                  f"{tag} blocked {end}: the printed block lacks the net, the end or the via:\n{text}")
            out[end] = d["cells"]
        # and negotiate() agrees: the net has no path at all
        log = []
        check(G.negotiate([job], iters=3, log=log.append, **NEG) is None and G.walled == "D3",
              f"{tag} blocked {end}: negotiate() routed a net blocked_end() calls shut in")
    return out


def t_converge(tag):
    """The harness's fixture: same hash and iterations whatever the detector's window."""
    import agentic_test as T
    inner = maze.Grid.negotiate
    seen, rows = [], []
    for window in (0, N, 1):
        def neg(self, jobs, _w=window, **kw):
            kw["stuck"] = _w
            r = inner(self, jobs, **kw)
            seen.append(self.stuck)
            return r
        maze.Grid.negotiate = neg
        T.STAGES.clear()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                T._route_fixture(True, "conflict", tag)
        finally:
            maze.Grid.negotiate = inner
        s = T.STAGES[-1]
        rows.append((s["status"], s["numbers"].get("iterations"), s["numbers"].get("result_sha")))
    check(all(r[0] == "PASS" for r in rows), f"{tag} converge: the fixture did not route: {rows}")
    check(len(set(rows)) == 1, f"{tag} converge: the detector moved the result (windows 0, {N}, 1): {rows}")
    check(not any(seen), f"{tag} converge: stopped as stuck on a run that converges")
    return rows[0]


def one_core(tag):
    return dict(channel=t_channel(tag), lanes=t_lanes(tag), detour=t_detour(tag), blocked=t_blocked(tag),
                converge=t_converge(tag))


def main():
    t0 = time.time()
    compiled = maze._CORE
    res = {}
    if compiled is not None:
        res["compiled"] = one_core("compiled")
    maze._CORE = None
    try:
        res["python"] = one_core("python")
    finally:
        maze._CORE = compiled
    if len(res) == 2:
        for k in res["python"]:
            check(res["python"][k] == res["compiled"][k],
                  f"the two cores disagree on {k}: python {res['python'][k]}, compiled {res['compiled'][k]}")
    if fails:
        for f in fails:
            print("  " + f)
        print(f"FAIL: {len(fails)} check(s), the first: {fails[0].splitlines()[0]}")
        return 1
    r = res["python"]
    print(f"PASS: channel stuck at iteration {r['channel']['at']} (2 nets, {r['channel']['ncells']} cells; all "
          f"60 with it off), lanes at {r['lanes']['at']} (3 nets), detour converged in {r['detour']['at']} "
          f"after {r['detour']['asked']} way-round tests, blocked source {r['blocked']['source']} / goal "
          f"{r['blocked']['goal']} cells with the via named, harness fixture {r['converge'][1]} iterations "
          f"sha {r['converge'][2]} at windows 0/{N}/1; cores: {' = '.join(res)}; {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
