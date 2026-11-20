"""cpsat_milp.py -- scipy.optimize.milp's call, solved by Google OR-Tools' CP-SAT on every core.

    sys.path.insert(0, <kicad_project>/tools)            # a board's build/ script: os.path.join(HERE, "..", "..", "tools")
    from cpsat_milp import milp                            # instead of: from scipy.optimize import milp
    from scipy.optimize import LinearConstraint, Bounds    # unchanged
    res = milp(c, constraints=LinearConstraint(A, lb, ub), integrality=np.ones(n), bounds=Bounds(lo, hi),
               options={"time_limit": 600})
    res.x, res.fun, res.status, res.message, res.success, res.mip_dual_bound, res.mip_gap

WHY: scipy's milp is HiGHS, which searches a MIP on one core.  CP-SAT searches with a portfolio
of workers at once and is strongest exactly where HiGHS is slowest -- 0/1 programs full of
big-M rows, and proving that none has an answer.  On the first project's BGA fanout (2,200-
2,400 binaries, 14,000-16,000 rows, half of them big-M order rows; eight cases), CP-SAT on 14
workers took 5-24 s for what HiGHS took 459 s to proving infeasibility, 1,193 s to an optimum,
or 3,000 s and its time limit to nothing at all; every answer equal to HiGHS's where HiGHS had one.

WHAT IT SOLVES: an integer program -- every variable integer (integrality 1).  CP-SAT has no
continuous variables: a model with one goes to scipy's HiGHS unchanged (one line on stderr).
So does every model when OR-Tools is not installed (`./setup.sh install`; or `pip install
ortools` into the interpreter that runs the script), and HW_MILP=highs forces HiGHS -- the
same call, for a comparison or a doubt.

THE SAME ANSWER AS HiGHS, NOT AN APPROXIMATION OF IT:
  * every constraint row is multiplied by the smallest power of ten (up to 10^6) that makes all
    its coefficients integers to 1e-9, and its bounds then rounded INWARD (ceil the lower, floor
    the upper, 1e-6 slack for float noise): with integer variables the row's value is an integer,
    so this is the same set of solutions.  A row no power of ten makes integral (1/3) raises:
    guessing would change the answer -- scale the model, or HW_MILP=highs;
  * the objective likewise when it scales; otherwise CP-SAT's own floating objective;
  * every answer is checked against the ORIGINAL float rows and bounds before it is returned;
  * "optimal" means what scipy's HiGHS means by it: within a relative gap of 1e-4 (HiGHS's
    default mip_rel_gap) -- two runs may stop at 20052 and 20053; mip_rel_gap 0 asks for the proof.
  * an infinite variable bound becomes +-10^9 (CP-SAT's domains are finite).

⚠ SEVERAL WORKERS, SEVERAL EQUAL ANSWERS.  With more than one worker the search is not
deterministic: of two answers of equal cost, either may come back, run to run (the fanout above:
16-22 of 91 balls' choices differed at the same cost).  HW_JOBS=1 is one worker -- deterministic,
and still 10-30 times faster than HiGHS there (14 s and 110 s).  The JSON a run writes is the
record a board draws from; re-solving is not how a result is reproduced.

options (scipy's names): time_limit (s), disp (CP-SAT's search log on stdout), mip_rel_gap;
and this file's: workers (default: $HW_JOBS, else par.grant() -- every core when this is the only
solve, an even share beside other solves and pools that hold a claim; par.py).  presolve and
node_limit are accepted and ignored.

A SOLVE THAT IS ONE OF MANY (a loop that solves, checks the answer elsewhere, adds a cut and
solves again) seldom needs the proven optimum, and a model CP-SAT cannot prove optimal runs to its
time limit EVERY round (the first project's fanout loop: 266 rounds, 9.3 h of 14.5 in solves that
all ended at their 180 s limit).  Three options of this file's for that, none changing what an
answer is -- each answer still meets every original row:
  * hint: a vector (the last round's x; NaN where there is none) CP-SAT starts from.  After one
    more cut the old answer is nearly feasible, so the first answer comes in seconds;
  * stall: seconds.  Once an answer exists and none better has come for this long, stop with the
    best (status 1, "no better answer for ... s") -- the time limit only bounds a search that is
    still improving;
  * first: True stops at the first answer (status 1) -- for a loop that wants any answer, or to
    learn fast that one exists.  With no objective every solve already does.
An infeasible model is still PROVEN infeasible (status 2) under all three; they only end an
optimisation early.  HiGHS (HW_MILP=highs, or a continuous variable) honours `first` -- it solves
the model without its objective -- and ignores hint and stall (a line on stderr).

A CHECK ASKS FOR A VERDICT, NOT AN OPTIMUM.  A model solved only to learn whether an answer fits
(a strip, a placement, a budget) has no use for its objective: `first: True`, and the solve is the
search for one point.  And only status 2 is "no": `verdict(res)` returns "answer", "infeasible"
or "undecided" -- a time limit with no answer proves nothing and must never become a cut.

status as scipy's: 0 optimal (or every row met, with no objective), 1 the time limit (x is the
best found, or None when none was), 2 infeasible -- PROVEN, 4 anything else.  The result also
carries wall_time, build_time and workers; its message names CP-SAT (or HiGHS) and the size.

Tests: tests/test_cpsat_milp.py (hand-worked cases, 200 random programs against HiGHS).
"""
import math
import os
import sys
import time

import numpy as np


def _pow10(vals, tol=1e-9, max_pow=6):
    """The smallest 10^p (p <= max_pow) that makes every value an integer within tol, or None."""
    vals = np.asarray(vals, float)
    if not len(vals):
        return 1
    for p in range(max_pow + 1):
        s = vals * 10 ** p
        if np.all(np.abs(s - np.rint(s)) <= tol * np.maximum(1.0, np.abs(s))):
            return 10 ** p
    return None


def _rows(constraints):
    """scipy's constraints argument (a LinearConstraint, an (A, lb, ub) tuple, or a list of
    either) as [(CSR matrix, lb, ub)]."""
    from scipy.optimize import LinearConstraint
    from scipy.sparse import csr_matrix
    if constraints is None:
        return []
    if isinstance(constraints, LinearConstraint) or (isinstance(constraints, tuple) and len(constraints) == 3
                                                     and not isinstance(constraints[0], LinearConstraint)):
        constraints = [constraints]
    out = []
    for c in constraints:
        A, lb, ub = (c.A, c.lb, c.ub) if isinstance(c, LinearConstraint) else c
        A = csr_matrix(A) if hasattr(A, "tocsr") else csr_matrix(np.atleast_2d(np.asarray(A, float)))
        A.sum_duplicates()
        m = A.shape[0]
        out.append((A, np.broadcast_to(np.asarray(lb, float), (m,)), np.broadcast_to(np.asarray(ub, float), (m,))))
    return out


class Result(dict):
    """scipy's OptimizeResult in shape: attribute or key access."""
    __getattr__ = dict.get


def _highs(why, c, integrality, bounds, constraints, options):
    """scipy's HiGHS, one core.  `first` is honoured here too -- the model is solved WITHOUT its
    objective, which is what "any answer" means and is where HiGHS is quick (board1's strip check:
    a point in 1.0 s; with its objective the same solve ran to its 900 s limit, five times an
    iteration, the gap never under 60 %).  `stall` and `hint` have no HiGHS equivalent through
    scipy: one line on stderr says so, and a solve that ends at its time limit WITH an answer says
    how far from proven it was -- that limit was the solve's whole duration."""
    from scipy.optimize import milp as highs
    if why:
        print(f"cpsat_milp: {why}; HiGHS solves this one", file=sys.stderr)
    options = dict(options or {})
    opts = {k: v for k, v in options.items() if k not in ("workers", "hint", "stall", "first")}
    c = np.asarray(c, float)
    t0 = time.time()
    if options.get("first") and np.any(c):
        r = highs(np.zeros(len(c)), integrality=integrality, bounds=bounds, constraints=constraints, options=opts)
        if r.x is not None:
            r.fun, r.status, r.success = float(c @ r.x), 1, False
            r.message = f"Stopped at the first answer (first): HiGHS solved the model without its objective. ({r.message})"
            r.mip_dual_bound = r.mip_gap = None
        r.wall_time = time.time() - t0
        return r
    if options.get("stall") is not None and np.any(c):
        print("cpsat_milp: stall is CP-SAT's; HiGHS runs to its optimum or to time_limit (first: True is honoured: "
              "the model without its objective)", file=sys.stderr)
    r = highs(c, integrality=integrality, bounds=bounds, constraints=constraints, options=opts)
    r.wall_time = time.time() - t0
    if r.status == 1 and r.x is not None and np.any(c):
        gap = getattr(r, "mip_gap", None)
        print(f"cpsat_milp: HiGHS ended at its limit after {r.wall_time:.0f} s with an answer"
              + (f", {100 * gap:.0f} % from proven" if gap is not None else "")
              + " -- one core, and the limit was the whole solve. For a verdict alone: options first=True; "
              "for a better point: a shorter time_limit costs little once the answer has stopped moving.",
              file=sys.stderr)
    return r


def verdict(res):
    """What a solve PROVED, for a loop that turns a failed check into a cut:
        "answer"      res.x is a point that meets every row (optimal or not);
        "infeasible"  PROVEN to have none (status 2) -- the only result a cut may be built on;
        "undecided"   anything else: a time limit with no answer, an error.  Nothing was proven:
                      solve longer, or stop -- never a cut (the first project read a 180 s miss as
                      "infeasible" in one loop and a 900 s miss as "does not fit" in another).
    """
    if getattr(res, "x", None) is not None:
        return "answer"
    return "infeasible" if getattr(res, "status", None) == 2 else "undecided"


def milp(c, *, integrality=None, bounds=None, constraints=None, options=None):
    options = dict(options or {})
    c = np.asarray(c, float)
    n = len(c)
    integ = np.ones(n) if integrality is None else np.broadcast_to(np.asarray(integrality), (n,))
    if os.environ.get("HW_MILP", "").lower() == "highs":
        return _highs("", c, integrality, bounds, constraints, options)
    if np.any(integ == 0):
        return _highs("a continuous variable -- CP-SAT solves integer programs only", c, integrality, bounds,
                      constraints, options)
    try:
        from ortools.sat.python import cp_model as cp
    except ImportError:
        return _highs("OR-Tools is not installed in this interpreter (./setup.sh install, or pip install ortools)",
                      c, integrality, bounds, constraints, options)
    if bounds is None:
        lo, hi = np.zeros(n), np.full(n, np.inf)
    else:
        lo = np.broadcast_to(np.asarray(bounds.lb, float), (n,))
        hi = np.broadcast_to(np.asarray(bounds.ub, float), (n,))
    BIGB = 10 ** 9
    t0 = time.time()
    model = cp.CpModel()
    xs = []
    for j in range(n):
        l = -BIGB if lo[j] == -np.inf else math.ceil(lo[j] - 1e-6)
        h = BIGB if hi[j] == np.inf else math.floor(hi[j] + 1e-6)
        if l > h:
            return Result(x=None, fun=None, success=False, status=2,
                          message=f"The problem is infeasible. (cpsat_milp: variable {j} has bounds [{lo[j]}, {hi[j]}])")
        xs.append(model.new_int_var(l, h, f"x{j}"))
    rows = _rows(constraints)
    nrows = 0
    for A, lb, ub in rows:
        for i in range(A.shape[0]):
            s, e = A.indptr[i], A.indptr[i + 1]
            idx, val = A.indices[s:e], A.data[s:e]
            keep = val != 0
            idx, val = idx[keep], val[keep]
            m = _pow10(val)
            if m is None:
                raise ValueError(f"cpsat_milp: row {nrows} has a coefficient no power of ten up to 10^6 makes an "
                                 f"integer ({val[:6]} ...): scale the model, or solve it with HW_MILP=highs")
            l = -np.inf if lb[i] == -np.inf else math.ceil(lb[i] * m - 1e-6)
            h = np.inf if ub[i] == np.inf else math.floor(ub[i] * m + 1e-6)
            nrows += 1
            if not len(idx):
                if l > 0 or h < 0:
                    return Result(x=None, fun=None, success=False, status=2,
                                  message=f"The problem is infeasible. (cpsat_milp: row {nrows - 1} is empty and excludes 0)")
                continue
            expr = cp.LinearExpr.weighted_sum([xs[k] for k in idx], [int(round(v * m)) for v in val])
            model.add_linear_constraint(expr, cp.INT_MIN if l == -np.inf else int(l), cp.INT_MAX if h == np.inf else int(h))
    nz = np.nonzero(c)[0]
    om = _pow10(c[nz]) if len(nz) else None
    if len(nz):
        coefs = [int(round(v * om)) for v in c[nz]] if om else [float(v) for v in c[nz]]
        model.minimize(cp.LinearExpr.weighted_sum([xs[k] for k in nz], coefs))
    build = time.time() - t0

    solver = cp.CpSolver()
    P = solver.parameters
    try:                                                   # a share of the machine, beside other solves and pools
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import par
    except ImportError:
        par = None
    workers = options.get("workers") or (par.grant() if par else
                                         int(os.environ.get("HW_JOBS") or 0) or os.cpu_count() or 1)
    P.num_workers = max(1, int(workers))
    if options.get("time_limit") is not None:
        P.max_time_in_seconds = float(options["time_limit"])
    gap = options.get("mip_rel_gap")
    P.relative_gap_limit = 1e-4 if gap is None else float(gap)
    P.log_search_progress = bool(options.get("disp"))
    hint = options.get("hint")
    if hint is not None:
        hint = np.asarray(hint, float)
        if hint.shape != (n,):
            raise ValueError(f"cpsat_milp: hint has shape {hint.shape}, the model has {n} variables")
        for j in np.nonzero(np.isfinite(hint))[0]:
            model.add_hint(xs[j], int(round(hint[j])))
    stall, first, why = options.get("stall"), bool(options.get("first")), ""
    held = par.claim(P.num_workers) if par else None
    if held:
        held.__enter__()
    if first and len(nz):
        P.stop_after_first_solution = True
        why = "the first answer (first)"
    if stall is not None and len(nz):
        import threading

        class _Watch(cp.CpSolverSolutionCallback):        # the time of the last better answer
            def __init__(self):
                super().__init__()
                self.last = None

            def on_solution_callback(self):
                self.last = time.time()
        watch, done, hit = _Watch(), threading.Event(), []

        def _guard():
            while not done.wait(min(0.2, float(stall) / 4 or 0.05)):
                if watch.last is not None and time.time() - watch.last >= float(stall):
                    hit.append(1)
                    solver.stop_search()
                    return
        th = threading.Thread(target=_guard, daemon=True)
        th.start()
        try:
            st = solver.solve(model, watch)
        finally:
            done.set()
            th.join()
        if hit:
            why = f"no better answer for {float(stall):g} s (stall)"
    else:
        try:
            st = solver.solve(model)
        finally:
            if held:
                held.__exit__(None, None, None)
                held = None
    if held:
        held.__exit__(None, None, None)
    name = solver.status_name(st)
    tail = (f"(CP-SAT {name}: {P.num_workers} worker(s), {solver.wall_time:.1f} s solving, {build:.1f} s building "
            f"{n} variables x {nrows} rows)")
    found = st in (cp.OPTIMAL, cp.FEASIBLE)
    x = np.array([solver.value(v) for v in xs], float) if found else None
    if found:                                              # the answer against the ORIGINAL rows: a scaling slip cannot pass
        worst = max([0.0] + [float(np.max(np.maximum(lb - A @ x, A @ x - ub), initial=0.0)) for A, lb, ub in rows]
                    + [float(np.max(np.maximum(lo - x, x - hi), initial=0.0))])
        if worst > 1e-6:
            return Result(x=None, fun=None, success=False, status=4,
                          message=f"cpsat_milp: CP-SAT's answer breaks a row of the model by {worst:g} -- a bug here; "
                                  "solve with HW_MILP=highs and report it")
    fun = float(c @ x) if found else None
    bound = (solver.best_objective_bound / (om or 1)) if (found and len(nz)) else fun
    if st == cp.OPTIMAL:
        status, msg = 0, f"Optimization terminated successfully. {tail}"
    elif st == cp.FEASIBLE:
        limit = options.get("time_limit")
        early = why and (limit is None or solver.wall_time < float(limit) - 0.5)
        status, msg = 1, (f"Stopped at {why}. {tail}" if early else f"Time limit reached. {tail}")
    elif st == cp.INFEASIBLE:
        status, msg = 2, f"The problem is infeasible. {tail}"
    elif st == cp.UNKNOWN:
        status, msg = 1, f"Time limit reached: no assignment found. {tail}"
    else:
        status, msg = 4, f"{name}: {solver.solution_info()} {tail}"
    mgap = (abs(fun - bound) / max(1e-9, abs(fun))) if (found and bound is not None and fun) else (0.0 if found else None)
    return Result(x=x, fun=fun, success=status == 0, status=status, message=msg, mip_dual_bound=bound, mip_gap=mgap,
                  mip_node_count=None, wall_time=solver.wall_time, build_time=build, workers=P.num_workers)
