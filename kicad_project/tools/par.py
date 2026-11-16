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

SEVERAL JOBS ON ONE MACHINE SHARE ITS CORES (`grant`, `claim`).  A solver or a
pool that takes "every core" beside another one runs two threads a core: both
slow down.  A job that is about to use many cores asks `grant()` how many are
its to take -- the free ones, and never less than an even share among the jobs
that hold a claim -- and holds `with par.claim(n):` while it uses them.  Two
loops started together settle at half the machine each within one solve; a
job alone still gets all of it.  Claims are files a dead process leaves
behind harmlessly (the pid and its start time are checked).  HW_JOBS=N
overrides the arithmetic, as everywhere.

COMMANDS, NOT FUNCTIONS (`cmap`).  A what-if count is a list of commands, each
one core: `par.cmap(cmds)` runs them a grant at a time and returns
(exit code, seconds, output) in the order given.  Ten variants started by hand
three at a time are ten runs' wall-clock on a machine that had room for all.

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


# ---- a share of the machine --------------------------------------------------------------------
def _claims_dir():
    import tempfile
    d = os.path.join(os.environ.get("HW_CORES_DIR") or os.path.join(
        os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), f"hw-cores-{os.getuid()}"))
    os.makedirs(d, exist_ok=True)
    return d


def _start(pid):
    """The process's start time in clock ticks (Linux), so a reused pid is not mistaken for the claimant."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def _alive(pid, start):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return not start or _start(pid) == start


def claims():
    """[(pid, cores)] held by OTHER live processes; a dead claimant's file is removed."""
    import json
    out, d = [], _claims_dir()
    for name in os.listdir(d):
        path = os.path.join(d, name)
        try:
            with open(path) as f:
                c = json.load(f)
            pid, n, start = int(c["pid"]), int(c["cores"]), c.get("start", "")
        except (OSError, ValueError, KeyError):
            continue
        if pid == os.getpid():
            continue
        if _alive(pid, start):
            out.append((pid, n))
        else:
            try:
                os.unlink(path)
            except OSError:
                pass
    return out


def grant(want=None, total=None):
    """How many cores this process may take now: HW_JOBS if set; else the cores no other job has
    claimed, and never less than an even share (total // (claimants + 1)) -- so a second job is not
    starved by a first that took everything, and the first comes down to its share on its next ask."""
    env = os.environ.get("HW_JOBS")
    if env:
        try:
            n = max(1, int(env))
            return min(n, want) if want else n
        except ValueError:
            pass
    total = total or os.cpu_count() or 1
    others = claims()
    n = max(1, total // (len(others) + 1), total - sum(c for _, c in others))
    return max(1, min(n, want)) if want else n


class claim:
    """`with par.claim(n):` -- tell the other jobs on this machine that n cores are in use here."""

    def __init__(self, cores):
        self.cores, self.path = int(cores), None

    def __enter__(self):
        import json
        import threading
        self.path = os.path.join(_claims_dir(), f"{os.getpid()}-{threading.get_ident()}.json")
        with open(self.path, "w") as f:
            json.dump({"pid": os.getpid(), "cores": self.cores, "start": _start(os.getpid())}, f)
        return self

    def __exit__(self, *exc):
        try:
            os.unlink(self.path)
        except OSError:
            pass
        return False


def cmap(cmds, jobs=None, cwd=None, env=None, timeout=None, note=None, stop=None):
    """Run commands (argv lists) `jobs` at a time (default: grant()); [(exit, seconds, output)] in the
    order given.  A command that outlives `timeout` seconds is killed: exit 124.  `stop(i, result)`,
    called as each ends, may return True to cancel the ones not yet started (their result: None) --
    "the first variant that closes".  Each command should be one core (HW_JOBS=1 is set for it
    unless `env` says otherwise): the pool is the parallelism."""
    import subprocess
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    cmds = [list(map(str, c)) for c in cmds]
    n = max(1, min(len(cmds), jobs or grant(len(cmds))))
    e = dict(os.environ, HW_JOBS="1")
    e.update(env or {})
    halt, lock = threading.Event(), threading.Lock()

    def one(i):
        if halt.is_set():
            return None
        t0 = time.time()
        try:
            r = subprocess.run(cmds[i], cwd=cwd, env=e, capture_output=True, text=True, timeout=timeout)
            res = (r.returncode, time.time() - t0, r.stdout + r.stderr)
        except subprocess.TimeoutExpired as x:
            out = (x.stdout or b"") + (x.stderr or b"")
            res = (124, time.time() - t0, out.decode(errors="replace") if isinstance(out, bytes) else out)
        if stop:
            with lock:
                if stop(i, res):
                    halt.set()
        return res
    if note:
        print(f"  [{len(cmds)} commands, {n} at a time: {note}]", file=sys.stderr, flush=True)
    if n <= 1:
        return [one(i) for i in range(len(cmds))]
    with claim(n), ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(one, range(len(cmds))))
