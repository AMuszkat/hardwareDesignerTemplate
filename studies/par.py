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
import sys


# ---- memory: the limit that kills -----------------------------------------------------------------
def mem_gb():
    """(available, total) GB from /proc/meminfo; (None, None) where there is none."""
    try:
        with open("/proc/meminfo") as f:
            kv = {ln.split(":")[0]: float(ln.split()[1]) for ln in f if ":" in ln}
        return kv["MemAvailable"] / 1048576, kv["MemTotal"] / 1048576
    except (OSError, KeyError, ValueError, IndexError):
        return None, None


def _own_gb():
    """This process's resident memory, GB: what one forked worker can come to cost (copy-on-write
    copies a page the moment a child touches it, and Python touches an object to read it)."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2 ** 30
    except (OSError, ValueError, IndexError):
        return 0.0


def mem_floor_gb(total):
    try:
        return float(os.environ["HW_MEM_FLOOR_GB"])
    except (KeyError, ValueError):
        return max(3.0, 0.15 * total)


def mem_jobs(per_job_gb=None, avail=None, total=None):
    """How many jobs of `per_job_gb` each (default: this process's own size -- a fork pool's
    worker) fit in the memory available NOW with the floor kept free; at least 1, and None where
    the machine does not say (no /proc/meminfo: no cap)."""
    if avail is None or total is None:
        avail, total = mem_gb()
    if avail is None:
        return None
    per = max(0.1, _own_gb() if per_job_gb is None else float(per_job_gb))
    return max(1, int((avail - mem_floor_gb(total)) // per))


def _cores(n_jobs):
    """How many processes the CORES allow for `n_jobs` independent runs."""
    env = os.environ.get("HW_JOBS")
    if env:
        return max(1, min(int(env), n_jobs))
    # leave one core for the parent and whatever else the machine is doing
    return max(1, min(n_jobs, (os.cpu_count() or 2) - 1))


def workers(n_jobs):
    """How many processes to use for `n_jobs` independent runs: what the cores allow, and no more
    than fit in memory -- each forked worker can grow to this process's own size.  HW_JOBS does
    not lift the memory cap: too many workers is not a slow run, it is the kernel killing them."""
    n = _cores(n_jobs)
    cap = mem_jobs()
    if cap is not None and cap < n:
        print(f"  [par: {cap} worker(s), not {n}: {_own_gb():.1f} GB each against {mem_gb()[0]:.1f} GB available "
              f"(floor {mem_floor_gb(mem_gb()[1]):.1f}); HW_MEM_FLOOR_GB sets the floor]", file=sys.stderr, flush=True)
        n = cap
    return n


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
