#!/usr/bin/env python3
"""The group router's plateau stop -- board-free, on maze.py alone.

    python3 tests/test_router_plateau.py

What happened on a live board (board1's In2 legs, 2026-11-15): four negotiations ran to their
1500 s --timeout, NOT ROUTED, with their best overlap count at iteration 40 within 0-11 % of the
best after 121-300 iterations (rIn2s: 1297 cells at iteration 20, 1292 at 170).  The overlaps moved
from cell to cell, so `stuck` (which needs the SAME cells) never fired.  maze.plateau_reached()
and negotiate(plateau=N, plateau_pct=X) stop such a run; this test holds them to it:

  rule       plateau_reached() on synthetic overlap counts: a flat tail stops, a tail that
             improves by the percentage does not, a slow 1 % creep stops at 2 % and not at 0.5 %,
             too few iterations or n = 0 never stops.
  channel    test_router_stall's two nets through a one-track gap, `stuck` OFF: with plateau=10
             negotiate() stops early, returns None, says "plateau" and fills self.plateau with
             the best count and its iteration; with plateau=0 the same run uses every iteration.
  converge   the harness's router fixture converges with plateau=20 exactly as with it off --
             same result hash and iteration count: the stop reads the counts and moves nothing.
             At plateau=10 it is reported, not asserted: its count sits flat from iteration 2 and
             it converges at 14, so a short window cuts it (why the default is off).

Exit 0 and a last line `PASS: ...`, or exit 1 and `FAIL: ...`.
"""
import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kicad_project" / "tools" / "route"))
sys.path.insert(0, str(ROOT / "tests"))
import maze  # noqa: E402
import test_router_stall as S  # noqa: E402  (its grid / wall fixtures)

fails = []


def check(ok, what):
    if not ok:
        fails.append(what)
    return ok


def t_rule():
    pr = maze.plateau_reached
    flat = [1300, 1297] + [1295 + (k % 3) for k in range(60)]          # moves, never improves
    check(pr(flat, 40, 2.0), "rule: a flat tail of 40 did not stop")
    check(not pr(flat[:40], 40, 2.0), "rule: stopped with no iteration before the window")
    check(not pr(flat, 0, 2.0), "rule: n = 0 stopped")
    better = [1000] * 10 + [970] * 10                                  # 3 % better in the window
    check(not pr(better, 10, 2.0), "rule: a 3 % improvement in the window was called a plateau")
    creep = [1000 * 0.99 ** (k // 10) for k in range(60)]              # 1 % a window of 10
    check(pr(creep, 10, 2.0), "rule: a 1 % creep did not stop at 2 %")
    check(not pr(creep, 10, 0.5), "rule: a 1 % creep stopped at 0.5 %")
    check(pr([5, 5, 5, 5], 2, 0.0), "rule: no new minimum at 0 % did not stop")
    check(not pr([5, 5, 5, 4], 2, 0.0), "rule: a new minimum at 0 % stopped")


def t_channel():
    G = S.grid(20.0, 10.0)
    jm = G.ny // 2
    S.wall(G, 9.0, 11.0, {jm})
    jobs = [dict(net=f"N{k}", src=(2.0, 3.0 + 4.0 * k), src_layer="F.Cu",
                 goals={G.cell(18.0, 3.0 + 4.0 * k)}, goal_layer="F.Cu", allowed={"F.Cu"}) for k in range(2)]
    out = {}
    for n in (10, 0):
        log = []
        p = G.negotiate(jobs, iters=60, log=log.append, stuck=0, plateau=n, plateau_pct=2.0, **S.NEG)
        its = sum(1 for ln in log if "nets overlap on" in ln)
        out[n] = (p, its, G.plateau, [ln for ln in log if "plateau --" in ln])
        for jb in jobs:
            G.unroute(jb["net"])
    p, its, pl, said = out[10]
    if check(p is None and pl is not None, f"channel: plateau=10 did not stop (iterations {its})"):
        check(its < 60 and pl["it"] == its, f"channel: stopped at {its} (report says {pl['it']}), not early")
        check(pl["best"] <= pl["window_best"] and 1 <= pl["best_it"] <= its - 10,
              f"channel: the report is wrong: {pl}")
        check(len(said) == 1 and f"iteration {pl['best_it']}" in said[0],
              f"channel: the log line is missing or does not name the best iteration: {said}")
    p0, its0, pl0, _s = out[0]
    check(p0 is None and pl0 is None and its0 == 60,
          f"channel: with plateau=0 it should use all 60 iterations, used {its0}")
    return its, (pl or {}).get("best")


def t_converge():
    import agentic_test as T
    inner = maze.Grid.negotiate
    rows = []
    for n in (0, 20, 10):
        def neg(self, jobs, _n=n, **kw):
            kw["plateau"] = _n
            return inner(self, jobs, **kw)
        maze.Grid.negotiate = neg
        T.STAGES.clear()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                T._route_fixture(True, "conflict", "plateau")
        finally:
            maze.Grid.negotiate = inner
        s = T.STAGES[-1]
        rows.append((s["status"], s["numbers"].get("iterations"), s["numbers"].get("result_sha")))
    check(rows[0][0] == rows[1][0] == "PASS", f"converge: the fixture did not route: {rows}")
    check(rows[0] == rows[1], f"converge: plateau=20 moved the result: {rows}")
    return rows


def main():
    t_rule()
    ch = t_channel()
    cv = t_converge()
    if fails:
        for f in fails:
            print("  " + f)
        print(f"FAIL: {len(fails)} check(s), the first: {fails[0].splitlines()[0]}")
        return 1
    print(f"PASS: rule cases hold; channel stopped on a plateau at iteration {ch[0]} (best {ch[1]} cells; "
          f"all 60 with it off); harness fixture {cv[0][1]} iterations sha {cv[0][2]} with plateau 0 and 20 "
          f"(plateau 10: {cv[2][0]} at iteration {cv[2][1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
