"""Run independent pieces of a board tool's work on several cores.

    sys.path.insert(0, str(<kicad_project>/tools))
    import par
    results = par.pmap(one_run, jobs, note="restarts")

The board tools' port of studies/par.py, with the rule pcbnew imposes:

⚠ LOAD THE BOARD ONCE, FORK, READ IN THE CHILDREN, RETURN PLAIN TUPLES, WRITE
ONLY IN THE PARENT. pcbnew objects are neither picklable nor thread-safe:
a child reads the board it inherited, never saves it, and returns numbers,
strings, lists and dicts. Threads do not help CPU work (the GIL); fork does.

⚠ FORK, NOT SPAWN. A forked child inherits the loaded board and every module;
a spawned one would reload pcbnew and the board. `fn` may be a closure (it
reaches the children by inheritance, not by pickling); its RESULT is pickled.

⚠ RESULTS COME BACK IN JOB ORDER, never completion order, so a parallel run
is identical to the serial one as long as every job carries its own seed.
A caller that stopped early in its serial loop (the first restart to reach
zero) walks the results in order and stops at the same place.

HW_JOBS=1 forces serial -- the first thing to try when a parallel result
differs from a serial one. On macOS the default is serial (fork after a
Cocoa-linked library is loaded can abort the child); HW_JOBS=N opts in.
"""
import os
import sys

_FN = None


def workers(n_jobs):
    """How many processes to use for `n_jobs` independent runs."""
    env = os.environ.get("HW_JOBS")
    if env:
        try:
            return max(1, min(int(env), n_jobs))
        except ValueError:
            pass
    if sys.platform == "darwin":
        return 1
    return max(1, min(n_jobs, (os.cpu_count() or 2) - 1))


def _call(job):
    return _FN(job)


def pmap(fn, jobs, note=None):
    """`[fn(j) for j in jobs]`, forked across cores, in job order."""
    global _FN
    jobs = list(jobs)
    n = workers(len(jobs))
    if n <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    import multiprocessing as mp
    _FN = fn
    try:
        if note:
            print(f"  [{len(jobs)} runs on {n} cores: {note}]", file=sys.stderr, flush=True)
        with mp.get_context("fork").Pool(n) as pool:
            return pool.map(_call, jobs, chunksize=1)
    finally:
        _FN = None
