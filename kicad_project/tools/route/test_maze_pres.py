"""Regression: negotiate()'s present cost must stay below the search's INF (1e18).

Two nets whose ends overlap can never separate, so pres grows every iteration. Before the cap
(pres_max=1e30) it passed 1e18 at iteration 19 with pres_mul 10, every shared cell then cost
more than INF, and the router reported net A "walled in -- no route exists", which is false: it
is a congestion that never settles, not a wall. Run: python3 tools/route/test_maze_pres.py
"""
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [here, os.path.dirname(here)]
import maze

g = maze.Grid(0, 0, 4, 2, ["F.Cu"], step=0.1)
jobs = [dict(net=n, src=(0, y), src_layer="F.Cu", goals=[g.cell(4, y)], goal_layer="F.Cu",
             allowed=["F.Cu"]) for n, y in (("A", 1.0), ("B", 0.9))]
seen = []
g.negotiate(jobs, iters=30, pres_mul=10.0, pres_max=1e30, log=seen.append, stuck=0)
walled = [l for l in seen if "walled" in l]
top = max(float(l.split("pres ")[1].split(",")[0]) for l in seen if "pres " in l)
ok = g.walled is None and not walled and top <= 1e12
print(f"pres peaked at {top:.3g}; walled={g.walled}; " + ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
