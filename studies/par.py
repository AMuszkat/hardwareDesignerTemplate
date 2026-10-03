"""Run independent model measurements on several cores.

    import par
    results = par.pmap(one_run, jobs, note="sweep")

⚠ FORK, NOT SPAWN.  A forked worker inherits the parent's imported modules,
any JIT-compiled kernels and the module state the caller selected; a spawned
one re-imports (and re-compiles) everything, which can cost more than the run
it was asked to do.  Prime any compiled kernel ONCE in the parent, then fork.

⚠ RESULTS ARE RETURNED IN JOB ORDER, never completion order, so a parallel
sweep is byte-identical to the serial one -- which is what lets `--check`
keep comparing against the same baseline.  Give every job an explicit seed.

HW_JOBS=1 forces serial -- the first thing to try when a parallel number
disagrees with a serial one.
"""
import os


def workers(n_jobs):
    """How many processes to use for `n_jobs` independent runs."""
    env = os.environ.get("HW_JOBS")
    if env:
        return max(1, min(int(env), n_jobs))
    # leave one core for the parent and whatever else the machine is doing
    return max(1, min(n_jobs, (os.cpu_count() or 2) - 1))


def pmap(fn, jobs, note=None):
    """`[fn(j) for j in jobs]`, on as many cores as there are jobs.

    `fn` must be a module-level function (fork still pickles the CALL) and its
    result must be picklable -- plain numbers and dicts, never a figure.
    """
    jobs = list(jobs)
    n = workers(len(jobs))
    if n <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    if note:
        print(f"  [{len(jobs)} runs on {n} cores: {note}]", flush=True)
    with ctx.Pool(n) as pool:
        return pool.map(fn, jobs, chunksize=1)
