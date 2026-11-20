#!/usr/bin/env python3
"""kicad_project/tools/cpsat_milp.py against scipy's HiGHS, on integer programs whose answers are known.

    python3 tests/test_cpsat_milp.py      (an interpreter with scipy; OR-Tools for the CP-SAT part)

  1. THE FALLBACK, always: HW_MILP=highs, and an interpreter where OR-Tools cannot be imported,
     both give HiGHS's answer through the same call (and say so on stderr for the second).
  2. BY HAND, with CP-SAT: a knapsack worked out below; a row of two-decimal coefficients (scaled
     by 100, its bound rounded inward); an equality with a negative bound; an infinite bound held
     by a row; an infeasible pair of rows; no objective (status 0, the row met); a big-M row; a
     continuous variable (handed to HiGHS); a row no power of ten makes integral (refused, not
     guessed); HW_JOBS=1 is one worker; hint / stall / first on a market-split program nobody
     proves fast (an early stop still meets every row; infeasible is still proven).
  3. RANDOM: 200 programs, each built round a known integer point so most are feasible (every
     seventh made infeasible), coefficients of 0-2 decimals, both bound kinds, every fifth with no
     objective -- solved by HiGHS and by CP-SAT to a zero gap: the same status, the same optimum,
     and every CP-SAT answer meets every original row.
Exit 0 and `PASS: ...` / 1 and `FAIL: ...`; `SKIP: ...` (exit 0) without scipy, or without OR-Tools
once part 1 has passed.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "kicad_project" / "tools"

try:
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint
    from scipy.optimize import milp as highs
    from scipy.sparse import csr_matrix
except ImportError as e:
    print(f"SKIP: {e} -- cpsat_milp.py takes scipy's call; run under the concept venv (.venv)")
    sys.exit(0)

sys.path.insert(0, str(TOOLS))
from cpsat_milp import milp  # noqa: E402

bad = []


def eq(what, got, want, tol=1e-9):
    ok = (got == want) if not isinstance(want, float) else (got is not None and abs(got - want) <= tol)
    if not ok:
        bad.append(f"{what}: {got!r}, expected {want!r}")


def done(skip=None):
    if bad:
        print("\n".join(bad[:30]))
        print(f"FAIL: {len(bad)} wrong; first: {bad[0]}")
        sys.exit(1)
    print(skip or f"PASS: {RESULT}")
    sys.exit(0)


KNAP = dict(c=-np.array([6, 5, 4.0]), constraints=LinearConstraint([[3, 2, 2]], -np.inf, 4), integrality=np.ones(3),
            bounds=Bounds(0, 1))           # values 6, 5, 4; weights 3, 2, 2; capacity 4 -> items 2 and 3: 9

# ---- 1. the fallback ---------------------------------------------------------------------------
os.environ["HW_MILP"] = "highs"
r = milp(**KNAP)
os.environ.pop("HW_MILP")
eq("HW_MILP=highs: optimum", r.fun, -9.0)
eq("HW_MILP=highs: HiGHS's message", "HiGHS" in (r.message or ""), True)
code = ("import sys; sys.modules['ortools'] = None; sys.path.insert(0, sys.argv[1]); import numpy as np;"
        "from scipy.optimize import LinearConstraint, Bounds; from cpsat_milp import milp;"
        "r = milp(-np.array([6, 5, 4.0]), constraints=LinearConstraint([[3, 2, 2]], -np.inf, 4), integrality=np.ones(3),"
        " bounds=Bounds(0, 1)); print(r.fun, 'HiGHS' in r.message)")
p = subprocess.run([sys.executable, "-c", code, str(TOOLS)], capture_output=True, text=True)
eq("no OR-Tools: HiGHS's answer", p.stdout.split(), ["-9.0", "True"])
eq("no OR-Tools: said on stderr", "OR-Tools is not installed" in p.stderr, True)

# ---- 1b. a model HiGHS keeps (a continuous variable): `first` drops the objective; verdict() ----
from cpsat_milp import verdict  # noqa: E402
MIX = dict(c=np.array([-1.0, -1.0]), integrality=np.array([1, 0]), bounds=Bounds([0, 0], [1, 1]))   # x binary, y real
ROW = [[1.0, 1.0]]
r = milp(**MIX, constraints=LinearConstraint(ROW, -np.inf, 1.5))
eq("mixed model: HiGHS's optimum", r.fun, -1.5)
eq("mixed model: verdict", verdict(r), "answer")
r = milp(**MIX, constraints=LinearConstraint(ROW, -np.inf, 1.5), options={"first": True})
eq("mixed model, first: a point that meets the row", r.x is not None and float(r.x[0] + r.x[1]) <= 1.5 + 1e-9, True)
eq("mixed model, first: status 1 (an answer, not the optimum) and it says how", (r.status, "first" in r.message), (1, True))
eq("mixed model, first: fun is the ORIGINAL objective at that point", r.fun, float(MIX["c"] @ r.x))
eq("mixed model, first: verdict", verdict(r), "answer")
r = milp(**MIX, constraints=LinearConstraint(ROW, 3.0, np.inf), options={"first": True})
eq("mixed model, first: infeasible is still proven", (r.status, verdict(r)), (2, "infeasible"))
eq("verdict: a time limit with no answer is undecided, never infeasible",
   verdict(type("R", (), {"x": None, "status": 1})()), "undecided")
code = ("import sys; sys.path.insert(0, sys.argv[1]); import numpy as np;"
        "from scipy.optimize import LinearConstraint, Bounds; from cpsat_milp import milp;"
        "milp(np.array([-1.0, -1.0]), constraints=LinearConstraint([[1.0, 1.0]], -np.inf, 1.5), integrality=np.array([1, 0]),"
        " bounds=Bounds([0, 0], [1, 1]), options={'stall': 5})")
p = subprocess.run([sys.executable, "-c", code, str(TOOLS)], capture_output=True, text=True)
eq("mixed model, stall: said on stderr that HiGHS has none", "stall is CP-SAT's" in p.stderr, True)

try:
    import ortools  # noqa: F401
except ImportError:
    done("SKIP: OR-Tools is not installed here (./setup.sh install); the HiGHS fallback gave the hand-worked answers")

# ---- 2. by hand, CP-SAT ------------------------------------------------------------------------
r = milp(**KNAP)
eq("knapsack optimum", r.fun, -9.0)
eq("knapsack choice", list(r.x), [0.0, 1.0, 1.0])
eq("knapsack status", (r.status, r.success), (0, True))
eq("knapsack: CP-SAT solved it", "CP-SAT OPTIMAL" in r.message, True)
# 0.35 x + 0.2 y >= 1.01 (scaled: 35 x + 20 y >= 101), x, y in 0..3, minimise x + y: no pair summing to 2 reaches it
# (0.7, 0.55, 0.4), x 3 y 0 does (1.05) -> 3
r = milp([1, 1], constraints=LinearConstraint([[0.35, 0.2]], 1.01, np.inf), integrality=[1, 1], bounds=Bounds(0, 3))
eq("two-decimal row: optimum", r.fun, 3.0)
eq("two-decimal row: the answer meets it", bool(0.35 * r.x[0] + 0.2 * r.x[1] >= 1.01), True)
# x - y == -2, x in -5..5, y in 0..1, minimise x: x = y - 2 -> -2 at y 0
r = milp([1, 0], constraints=LinearConstraint([[1, -1]], -2, -2), integrality=[1, 1], bounds=Bounds([-5, 0], [5, 1]))
eq("equality with a negative bound", (r.fun, list(r.x)), (-2.0, [-2.0, 0.0]))
# an infinite upper bound held only by a row: minimise -x with x <= 7.5 -> 7
r = milp([-1], constraints=LinearConstraint([[1]], -np.inf, 7.5), integrality=[1], bounds=Bounds(0, np.inf))
eq("infinite bound, the row's 7.5 floors to 7", r.fun, -7.0)
# x + y >= 3 and x + y <= 2: proven infeasible
r = milp([0, 0], constraints=[LinearConstraint([[1, 1]], 3, np.inf), LinearConstraint([[1, 1]], -np.inf, 2)],
         integrality=[1, 1], bounds=Bounds(0, 5))
eq("infeasible: status, x", (r.status, r.x), (2, None))
eq("infeasible: scipy's message", r.message.startswith("The problem is infeasible."), True)
# no objective: any point with x + 2y == 4, x, y in 0..3
r = milp(np.zeros(2), constraints=LinearConstraint([[1, 2]], 4, 4), integrality=[1, 1], bounds=Bounds(0, 3))
eq("no objective: status", r.status, 0)
eq("no objective: the row holds", float(r.x[0] + 2 * r.x[1]), 4.0)
# big-M, the shape of an order row: a - b - 6000 w <= 0.5 is a <= b unless w is paid for (the 0.5 floors to 0).
# b is fixed 0 and a is worth 100: with w at 30, a is taken and w paid (-70); with w at 130, a is left (0)
for price, want in ((30.0, (-70.0, [1.0, 0.0, 1.0])), (130.0, (0.0, [0.0, 0.0, 0.0]))):
    r = milp([-100, 0, price], constraints=LinearConstraint([[1, -1, -6000]], -np.inf, 0.5), integrality=[1, 1, 1],
             bounds=Bounds([0, 0, 0], [1, 0, 1]))
    eq(f"big-M order row, w at {price:g}", (r.fun, list(r.x)), want)
# a continuous variable: HiGHS solves it
r = milp([1], constraints=LinearConstraint([[1]], 0.5, np.inf), integrality=[0], bounds=Bounds(0, 2))
eq("continuous: HiGHS's answer", float(round(r.fun, 6)), 0.5)
try:
    milp([1, 1], constraints=LinearConstraint([[1 / 3, 1]], 1, np.inf), integrality=[1, 1], bounds=Bounds(0, 3))
    bad.append("1/3: no error raised")
except ValueError as e:
    eq("1/3: refused, not guessed", "no power of ten" in str(e), True)
os.environ["HW_JOBS"] = "1"
eq("HW_JOBS=1: one worker", milp(**KNAP).workers, 1)
os.environ.pop("HW_JOBS")
eq("options workers", milp(**KNAP, options={"workers": 3}).workers, 3)
# hint, stall, first: an optimisation ended early is still an answer that meets every row
eq("hint: the optimum itself", milp(**KNAP, options={"hint": [0, 1, 1]}).fun, -9.0)
eq("hint: a wrong start, the same optimum", milp(**KNAP, options={"hint": [1, 1, 1]}).fun, -9.0)
eq("hint: NaN is no hint", milp(**KNAP, options={"hint": [np.nan, 1, np.nan]}).fun, -9.0)
try:
    milp(**KNAP, options={"hint": [0, 1]})
    bad.append("hint of the wrong length: no error raised")
except ValueError as e:
    eq("hint of the wrong length: refused", "hint has shape" in str(e), True)
INF = dict(c=[1, 1], constraints=LinearConstraint([[1, 1], [1, 1]], [3, -np.inf], [np.inf, 2]), integrality=[1, 1],
           bounds=Bounds(0, 5))
eq("stall + first: infeasible is still proven", milp(**INF, options={"stall": 0.1, "first": True}).status, 2)
# a market-split program (Cornuejols & Dawande): equalities nobody proves fast, any x with its slack is an answer
rs = np.random.default_rng(7)
ms, ns = 4, 30
Am = rs.integers(0, 100, (ms, ns)).astype(float)
dm = np.floor(Am.sum(1) / 2)
A_ = np.hstack([Am, np.eye(ms), -np.eye(ms)])                    # A x + s - t = d, minimise s + t
HARD = dict(c=np.r_[np.zeros(ns), np.ones(2 * ms)], constraints=LinearConstraint(A_, dm, dm),
            integrality=np.ones(ns + 2 * ms), bounds=Bounds(0, np.r_[np.ones(ns), np.full(2 * ms, 5000)]))


def meets(r):
    return r.x is not None and np.allclose(A_ @ r.x, dm) and abs(float(HARD["c"] @ r.x) - r.fun) < 1e-9


r1 = milp(**HARD, options={"first": True, "time_limit": 60, "workers": 4})
eq("first: an answer that meets the rows", meets(r1), True)
eq("first: stopped at once", r1.status in (0, 1) and r1.wall_time < 20, True)
r2 = milp(**HARD, options={"stall": 1.0, "time_limit": 60, "workers": 4})
eq("stall: an answer that meets the rows", meets(r2), True)
eq("stall: ended before the time limit", r2.wall_time < 45 and (r2.status == 0 or "stall" in r2.message), True)
eq("stall: no worse than the first answer", r2.fun <= r1.fun + 1e-9, True)
r3 = milp(**HARD, options={"hint": r2.x, "first": True, "time_limit": 60, "workers": 4})
eq("hint + first: starts from the hinted answer", meets(r3) and r3.fun <= r2.fun + 1e-9, True)

# ---- 3. random, against HiGHS ------------------------------------------------------------------
rng = np.random.default_rng(20261115)
n_opt = n_inf = n_feas = 0
for t in range(200):
    n, m = int(rng.integers(6, 30)), int(rng.integers(3, 25))
    hi = rng.integers(1, 4, n).astype(float)
    lo = np.where(rng.random(n) < 0.2, -hi, 0.0)
    x0 = np.array([rng.integers(int(lo[j]), int(hi[j]) + 1) for j in range(n)], float)
    dec = int(rng.integers(0, 3))
    A = np.where(rng.random((m, n)) < 0.35, np.round(rng.uniform(-5, 5, (m, n)), dec), 0.0)
    v = A @ x0
    lb = np.where(rng.random(m) < 0.35, -np.inf, np.round(v - rng.uniform(0, 3, m), dec))
    ub = np.where(rng.random(m) < 0.35, np.inf, np.round(v + rng.uniform(0, 3, m), dec))
    if t % 7 == 3:
        k = int(rng.integers(m))
        lb[k], ub[k] = v[k] + 50, np.inf
    c = np.round(rng.uniform(-3, 3, n), int(rng.integers(0, 3))) * (0 if t % 5 == 0 else 1)
    con = LinearConstraint(csr_matrix(A), lb, ub)
    a = highs(c, constraints=con, integrality=np.ones(n), bounds=Bounds(lo, hi), options={"mip_rel_gap": 0})
    b = milp(c, constraints=con, integrality=np.ones(n), bounds=Bounds(lo, hi), options={"mip_rel_gap": 0, "workers": 4})
    if (a.x is None) != (b.x is None):
        bad.append(f"random {t}: HiGHS status {a.status}, CP-SAT status {b.status}: {b.message}")
        continue
    if b.x is None:
        n_inf += 1
        continue
    r_ = A @ b.x
    if not (np.all(r_ >= lb - 1e-9) and np.all(r_ <= ub + 1e-9) and np.all(b.x >= lo) and np.all(b.x <= hi)):
        bad.append(f"random {t}: CP-SAT's answer breaks a row")
    if np.any(c):
        n_opt += 1
        eq(f"random {t}: optimum", b.fun, float(a.fun), 1e-6)
    else:
        n_feas += 1
        eq(f"random {t}: no objective, status", b.status, 0)

RESULT = (f"the fallback (HW_MILP=highs, no OR-Tools) and every hand-worked case exact; 200 random integer programs agree "
          f"with HiGHS ({n_opt} optima equal, {n_feas} with no objective met, {n_inf} infeasible in both); every CP-SAT "
          "answer meets every original row")
done()
