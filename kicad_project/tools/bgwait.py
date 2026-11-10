#!/usr/bin/env python3
"""Start a long command in the background and wait on it BY PID, with a time
limit -- for both session types (the concept side calls it by path).

    python3 tools/bgwait.py run --name R2_a [--log FILE] -- tools/kpy tools/route/netgroup.py --board board1 routing/R2_a.json
    python3 tools/bgwait.py wait R2_a R2_b [PID ...] [--timeout 100]
    python3 tools/bgwait.py status [NAME ...]
    python3 tools/bgwait.py kill NAME ...
    python3 tools/bgwait.py clean

WHY IT EXISTS.  A board session started three router runs in the background
and waited with `while pgrep -f "netgroup.py ... R2_emmc_"; do sleep 20; done`.
`pgrep -f` matches whole command lines and the loop's own shell carried that
string, so the loop matched itself: it sat 4 h 52 min at 0 CPU on three runs
that had ended within 3 minutes, never read a log and had no time limit.  A
wait by NAME cannot tell the job from whoever is asking about it; a wait by
PID can.  `tools/session/bashguard.py` (a PreToolUse hook on Bash) refuses the
name-polling loop and names this tool.

`run` starts the command DETACHED: its own session and process group, stdin
closed, stdout and stderr into the log (default `<jobs>/NAME.log`), Python
children unbuffered (`PYTHONUNBUFFERED=1`) so the log's tail is the run's
present.  A small wrapper process is the job: it starts the command, waits for
it and records the exit code, so `wait` can report it long after.  The job
file `<jobs>/NAME.json` holds {name, pid, start, started, cmd, cwd, log} and,
once it ended, {exit, signal, ended, cpu_s}.

⚠ A PID ALONE IS NOT A PROCESS.  Pids are reused, so a job is the PAIR (pid,
start time): `/proc/<pid>/stat` field 22 on Linux, `ps -o lstart=` elsewhere.
A recycled pid has another start time and reads as "ended", never as the job.

`wait` takes job NAMES and bare PIDS (a process started some other way) and
ALWAYS has a time limit: `--timeout` defaults to 100 s -- inside the Bash
tool's own default limit of 120 s, so the report is printed, not cut off --
and is capped at 3600 s; for a longer wait give the Bash call a longer timeout
too, or wait again.  It returns as soon as ALL of them ended, or at the
timeout, and in EVERY case prints per job: state, exit code, elapsed time, CPU
seconds used, CPU used during the last interval ("0.0 s CPU in the last 60 s:
idle or hung") and the last lines of its log with the log's age.  So a job
that is finished, or hung, is visible on the first wait.  A line per running
job is printed every `--every` seconds (default 30) on the way.

    exit 0    all ended, every exit code known and 0
    exit 1    all ended, one failed -- or its exit code is unknown (a bare
              PID, or a job killed with -9: only `run` records exit codes)
    exit 124  the timeout: at least one still runs (like timeout(1))
    exit 2    usage: no such job, a job of that name still running, ...

`status` is the same report without waiting (running jobs are sampled for
`--sample` seconds, default 1, to say whether they use CPU).  `kill` sends
SIGTERM to the job's process group, SIGKILL after `--grace` seconds (default
5) to whatever is left in its session.  `clean` forgets the ended jobs.

WHERE THE JOB FILES LIVE -- git-ignored, one folder per session:
    HW_BGWAIT_DIR                       when set (tests);
    kicad_project/bgwait.tmp/<boardN>/  a board session (HW_BOARD, exported by
                                        bin/board; else the board folder the
                                        current directory is in);
    out/bgwait/                         the concept session (anywhere else in
                                        the repository);
    <system temp>/bgwait-<user>/        outside the repository, or when the
                                        folder above cannot be written.
A name not found in this session's folder is looked up in the others only when
HW_BOARD is unset: a board session never waits on, or kills, another board's job.

Standard library only; Linux and macOS.
"""
import argparse
import collections
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KP = Path(__file__).resolve().parent.parent           # kicad_project/
ROOT = KP.parent
BOARD_RE = re.compile(r"(board\d+)_\w+")
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9._-]*")
TIMEOUT_DEFAULT = 100.0       # under the Bash tool's default limit of 120 s
TIMEOUT_MAX = 3600.0
WINDOW = 60.0                 # the "last interval" the CPU delta is quoted over
LINUX = os.path.isdir("/proc/self") and os.path.exists("/proc/self/stat")
TICK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def die(msg):
    print(f"bgwait: {msg}", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# where the job files live
# ---------------------------------------------------------------------------

def _temp_dir():
    try:
        import getpass
        user = getpass.getuser()
    except Exception:
        user = str(os.getuid())
    return Path(tempfile.gettempdir()) / f"bgwait-{user}"


def _inside(path, base):
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def jobs_dir():
    """This session's job folder (see the module docstring)."""
    env = os.environ.get("HW_BGWAIT_DIR")
    if env:
        return Path(env)
    cwd = Path.cwd().resolve()
    board = os.environ.get("HW_BOARD", "")
    if not re.fullmatch(r"board\d+", board):
        board = ""
        if _inside(cwd, KP):
            parts = cwd.relative_to(KP).parts
            m = BOARD_RE.fullmatch(parts[0]) if parts else None
            board = m.group(1) if m else ""
    if board:
        d = KP / "bgwait.tmp" / board
    elif _inside(cwd, KP):
        d = KP / "bgwait.tmp" / "session"
    elif _inside(cwd, ROOT) and (ROOT / "kicad_project").is_dir():
        d = ROOT / "out" / "bgwait"
    else:
        return _temp_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        if os.access(d, os.W_OK):
            return d
    except OSError:
        pass
    return _temp_dir()


def other_dirs(mine):
    """Every other job folder there is -- searched for a name only when this
    session has no HW_BOARD (a board session keeps to its own)."""
    if os.environ.get("HW_BOARD") or os.environ.get("HW_BGWAIT_DIR"):
        return []
    out = [d for d in sorted((KP / "bgwait.tmp").glob("*")) if d.is_dir()]
    out += [ROOT / "out" / "bgwait", _temp_dir()]
    return [d for d in out if d.is_dir() and d.resolve() != Path(mine).resolve()]


# ---------------------------------------------------------------------------
# processes: identity, liveness, CPU
# ---------------------------------------------------------------------------

def _stat(pid):
    """Linux: (state, ppid, pgrp, session, cpu ticks incl. reaped children, start ticks)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            raw = f.read().decode("ascii", "replace")
        f_ = raw[raw.rindex(")") + 2:].split()         # the command name may hold spaces and ')'
        return (f_[0], int(f_[1]), int(f_[2]), int(f_[3]),
                int(f_[11]) + int(f_[12]) + int(f_[13]) + int(f_[14]), f_[19])
    except (OSError, ValueError, IndexError):
        return None


def _ps(fmt, pid=None):
    try:
        r = subprocess.run(["ps", "-o", fmt] + (["-p", str(pid)] if pid else ["-A"]),
                           capture_output=True, text=True, timeout=20)
        return r.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def start_token(pid):
    """What tells this process from a later one with the same pid, or None when
    there is no such process."""
    if LINUX:
        s = _stat(pid)
        return s[5] if s else None
    return " ".join(_ps("lstart=", pid).split()) or None


def alive(pid, token):
    """Is the process (pid, start time) still running?  A zombie has ended."""
    if not pid or token is None:
        return False
    if LINUX:
        s = _stat(pid)
        return bool(s) and s[5] == token and s[0] not in "ZX"
    if " ".join(_ps("lstart=", pid).split()) != token:
        return False
    return not _ps("stat=", pid).strip().startswith("Z")


def _cputime(txt):
    """ps's [dd-]hh:mm:ss[.cc] as seconds."""
    days, _, rest = txt.rpartition("-")
    sec = 0.0
    for part in rest.split(":"):
        sec = sec * 60 + float(part)
    return sec + 86400 * int(days or 0)


def cpu_seconds(pid):
    """CPU seconds used so far by the process, everything it started and
    everything in its session -- a job is a tree, and its time is the tree's.
    On Linux a live process also carries its reaped children's time, so a
    worker pool that came and went is still counted."""
    table = {}
    if LINUX:
        for name in os.listdir("/proc"):
            if name.isdigit():
                s = _stat(int(name))
                if s:
                    table[int(name)] = (s[1], s[3], s[4] / TICK)
    else:
        for ln in _ps("pid=,ppid=,sess=,time=").splitlines():
            f_ = ln.split()
            try:
                table[int(f_[0])] = (int(f_[1]), int(f_[2]), _cputime(f_[3]))
            except (ValueError, IndexError):
                pass
    kids = collections.defaultdict(list)
    for p, (pp, _sess, _c) in table.items():
        kids[pp].append(p)
    take, todo = set(), [pid]
    while todo:
        p = todo.pop()
        if p in take or p not in table:
            continue
        take.add(p)
        todo += kids.get(p, [])
    take |= {p for p, (_pp, sess, _c) in table.items() if sess == pid}
    return sum(table[p][2] for p in take)


def session_pids(pid):
    """Linux: every live process in the session `pid` leads (kill's stragglers)."""
    out = []
    if LINUX:
        for name in os.listdir("/proc"):
            if name.isdigit():
                s = _stat(int(name))
                if s and s[3] == pid and s[0] not in "ZX":
                    out.append(int(name))
    return out


# ---------------------------------------------------------------------------
# job files
# ---------------------------------------------------------------------------

def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save(path, job):
    """Whole, renamed over the old one: a reader never sees half a job file."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(job, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


class Target:
    """One thing waited on: a job (it has a job file) or a bare pid."""

    def __init__(self, name=None, path=None, pid=None):
        self.name, self.path = name, path
        self.job = load(path) if path else None
        if self.job is not None:
            self.pid, self.token = self.job.get("pid"), self.job.get("start")
        else:
            self.pid, self.token = pid, start_token(pid)
            self.seen = self.token is not None           # was it there when we first looked
        self.samples = collections.deque()               # (monotonic time, CPU seconds)
        self.t_first = time.monotonic()

    @property
    def label(self):
        return self.name or f"pid {self.pid}"

    def refresh(self):
        if self.path:
            self.job = load(self.path) or self.job

    def running(self):
        """A job has ended once its wrapper recorded an exit code or is gone;
        the wrapper writes the job file BEFORE it exits, so a job file read
        after the wrapper is gone is final."""
        if self.job is not None:
            if "exit" in self.job:
                return False
            if alive(self.pid, self.token):
                return True
            self.refresh()
            return False
        return alive(self.pid, self.token)

    def sample(self):
        now = time.monotonic()
        self.samples.append((now, cpu_seconds(self.pid)))
        while len(self.samples) > 2 and self.samples[1][0] <= now - WINDOW:
            self.samples.popleft()

    def last_interval(self):
        """(seconds, CPU seconds used in them) over the newest WINDOW of samples."""
        if len(self.samples) < 2:
            return None
        (t0, c0), (t1, c1) = self.samples[0], self.samples[-1]
        return (t1 - t0, max(0.0, c1 - c0)) if t1 - t0 >= 0.2 else None


def find(arg, mine):
    """A NAME of this session's jobs, else a PID; never a pattern."""
    for d in [mine] + other_dirs(mine):
        p = Path(d) / f"{arg}.json"
        if p.exists() and load(p) is not None:
            if Path(d) != Path(mine):
                print(f"bgwait: {arg} found in {d}", file=sys.stderr)
            return Target(name=arg, path=p)
    if arg.isdigit():
        return Target(pid=int(arg))
    have = sorted(p.stem for p in Path(mine).glob("*.json"))
    die(f"no job named {arg!r} in {mine} (have: {', '.join(have) or 'none'}). A job is started with "
        f"`bgwait.py run --name NAME -- cmd ...`; anything else is waited on by its PID.")


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------

def dur(s):
    s = max(0, int(round(s)))
    if s < 100:
        return f"{s} s"
    if s < 6000:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"


def size(n):
    return f"{n} B" if n < 2000 else (f"{n / 1e3:.0f} kB" if n < 2e6 else f"{n / 1e6:.1f} MB")


def log_tail(path, n):
    """The last `n` lines of a log, read from its end (a log may be large)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            f.seek(max(0, end - 64 * 1024))
            data = f.read()
    except OSError:
        return None
    lines = data.decode("utf-8", "replace").replace("\r", "\n").splitlines()
    lines = [ln for ln in lines if ln.strip()]
    return [ln if len(ln) <= 220 else ln[:217] + "..." for ln in lines[-n:]] if n > 0 else []


def idle_note(dt, dcpu):
    verdict = "idle or hung" if dcpu <= max(0.02, 0.005 * dt) else "working"
    return f"{dcpu:.1f} s CPU in the last {dt:.0f} s: {verdict}"


def report(t, tail):
    """Print one target's block; return (running, exit code or None)."""
    t.refresh()
    now = time.time()
    running = t.running()
    job = t.job
    rc = None
    if job is not None:
        started = job.get("started") or job.get("created") or now
        if running:
            head = f"RUNNING for {dur(now - started)} (pid {t.pid})"
        elif "exit" in job:
            rc = job["exit"]
            sig = f", signal {job['signal']}" if job.get("signal") else ""
            head = (f"ended, exit {rc}{sig}, after {dur(job.get('ended', now) - started)}"
                    f" ({dur(now - job.get('ended', now))} ago); {job.get('cpu_s', 0):.1f} s CPU")
        elif not t.pid:
            head = "never started (the wrapper did not register: read the log)"
        else:
            head = (f"ended, EXIT CODE NOT RECORDED (pid {t.pid} is gone: killed with -9, or the machine "
                    f"restarted), started {dur(now - started)} ago")
    elif running:
        head = f"RUNNING (pid {t.pid}; not started with `bgwait.py run`: no exit code will be known)"
    elif t.seen:
        head = f"ended after at least {dur(time.monotonic() - t.t_first)} of this wait; exit code unknown " \
               "(not started with `bgwait.py run`)"
    else:
        head = "not running: there is no such process (it ended earlier, or the pid is wrong); exit code unknown"
    if running:
        if t.samples:
            head += f"; {t.samples[-1][1]:.1f} s CPU so far"
        iv = t.last_interval()
        if iv:
            head += ", " + idle_note(*iv)
    print(f"{t.label}: {head}")
    if job is not None:
        log = job.get("log")
        try:
            st = os.stat(log)
            print(f"   log {log} ({size(st.st_size)}, last write {dur(now - st.st_mtime)} ago)")
        except (OSError, TypeError):
            print(f"   log {log}: missing")
        else:
            for ln in log_tail(log, tail) or []:
                print(f"   | {ln}")
    return running, rc


def summary(results, waited=None):
    """The last line and the exit code: 124 running, 0 all zero, else 1."""
    n_run = sum(1 for r, _rc in results if r)
    ended = [rc for r, rc in results if not r]
    bits = []
    if ended:
        bits.append(f"{len(ended)} ended (exit " + ", ".join("?" if rc is None else str(rc) for rc in ended) + ")")
    if n_run:
        bits.append(f"{n_run} STILL RUNNING" + (f" after {dur(waited)}" if waited is not None else ""))
    code = 124 if n_run else (0 if ended and all(rc == 0 for rc in ended) else 1)
    return "bgwait: " + ", ".join(bits or ["nothing to report"]), code


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_run(a):
    if not a.cmd:
        die("run: no command (bgwait.py run --name NAME -- cmd ...)")
    if not NAME_RE.fullmatch(a.name):
        die(f"run: --name {a.name!r}: a letter or _ first, then letters, digits, . _ - (a number is a pid)")
    d = jobs_dir()
    d.mkdir(parents=True, exist_ok=True)
    jf = d / f"{a.name}.json"
    old = Target(name=a.name, path=jf) if jf.exists() else None
    if old and old.job is not None and old.running():
        die(f"run: a job named {a.name} is still running (pid {old.pid}): wait on it, "
            f"`bgwait.py kill {a.name}`, or choose another name")
    log = Path(a.log).resolve() if a.log else d / f"{a.name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    save(jf, {"name": a.name, "cmd": a.cmd, "cwd": os.getcwd(), "log": str(log),
              "created": time.time(), "pid": None})
    with open(log, "wb") as lf:                              # a run's log is its own: truncated
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_exec", str(jf)],
                         stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                         start_new_session=True, close_fds=True)
    job, t0 = None, time.monotonic()
    while time.monotonic() - t0 < 15:
        job = load(jf)
        if job and job.get("pid"):
            break
        time.sleep(0.02)
    if not job or not job.get("pid"):
        die(f"run: the wrapper did not register within 15 s; read {log}")
    me = os.path.relpath(Path(__file__).resolve())
    me = me if not me.startswith("..") else str(Path(__file__).resolve())
    print(f"{a.name}: started, pid {job['pid']}, log {log}")
    print(f"wait:   python3 {me} wait {a.name} --timeout {TIMEOUT_DEFAULT:g}")
    return 0


def cmd_exec(jobfile):
    """The wrapper, detached by `run`: it IS the job.  Its pid leads the job's
    session and process group; it starts the command, waits, and records the
    exit code -- the only way the code outlives the process."""
    job = load(jobfile)
    if job is None:
        print(f"bgwait: no job file {jobfile}", flush=True)
        return 2
    pid = os.getpid()
    job.update(pid=pid, start=start_token(pid), started=time.time())
    save(jobfile, job)
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    try:
        child = subprocess.Popen(job["cmd"], cwd=job.get("cwd") or None, env=env)
    except OSError as e:
        print(f"bgwait: cannot start {job['cmd'][0]!r}: {e}", flush=True)
        rc = 127
    else:
        def forward(signum, _frame):
            # `kill` signals the whole group, so the command has it already; a
            # plain `kill <pid>` reaches only this wrapper, which passes it on
            # and stays to record how the command ended
            try:
                child.send_signal(signum)
            except OSError:
                pass
        for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(s, forward)
        rc = child.wait()
    tm = os.times()
    job.update(exit=rc if rc >= 0 else 128 - rc, ended=time.time(),
               cpu_s=round(tm.children_user + tm.children_system, 2))
    if rc < 0:
        job["signal"] = -rc
    save(jobfile, job)
    return job["exit"]


def cmd_wait(a):
    timeout = a.timeout
    if timeout <= 0:
        die("wait: --timeout must be positive (a wait always has a limit)")
    if timeout > TIMEOUT_MAX:
        print(f"bgwait: --timeout {timeout:g} capped at {TIMEOUT_MAX:g} s (wait again for longer)", file=sys.stderr)
        timeout = TIMEOUT_MAX
    mine = jobs_dir()
    targets = [find(x, mine) for x in a.jobs]
    t0 = time.monotonic()
    beat, pause, timed_out = t0, 0.05, False
    while True:
        now = time.monotonic()
        live = [t for t in targets if t.running()]
        for t in live:
            t.sample()
        if not live:
            break
        if now - t0 >= timeout:
            timed_out = True
            break
        if a.every > 0 and now - beat >= a.every:
            beat = now
            for t in live:
                iv = t.last_interval()
                print(f"[{dur(now - t0):>6}] {t.label} running, {t.samples[-1][1]:.1f} s CPU so far"
                      + (", " + idle_note(*iv) if iv else ""), flush=True)
        time.sleep(max(0.01, min(pause, timeout - (now - t0))))
        pause = min(1.0 if LINUX else 5.0, pause * 1.5)
    results = [report(t, a.tail) for t in targets]
    line, code = summary(results, time.monotonic() - t0)
    if timed_out:
        me = os.path.relpath(Path(__file__).resolve())
        line += (f" -- the {timeout:g} s limit. Read the log lines above before waiting again; a job at "
                 f"0 CPU will not end by itself (python3 {me} kill NAME).")
    print(line)
    return code


def cmd_status(a):
    mine = jobs_dir()
    if a.jobs:
        targets = [find(x, mine) for x in a.jobs]
    else:
        targets = [Target(name=p.stem, path=p) for p in sorted(Path(mine).glob("*.json")) if load(p) is not None]
    if not targets:
        print(f"bgwait: no jobs in {mine}")
        return 0
    live = [t for t in targets if t.running()]
    if live and a.sample > 0:
        for t in live:
            t.sample()
        time.sleep(a.sample)
        for t in live:
            if t.running():
                t.sample()
    results = [report(t, a.tail) for t in targets]
    print(summary(results)[0])
    return 0


def cmd_kill(a):
    mine = jobs_dir()
    code = 0
    for x in a.jobs:
        t = find(x, mine)
        if t.job is None:
            die(f"kill: {x} is not a job of this session (bare pids are yours to kill)")
        if not t.running():
            print(f"{t.label}: already ended")
            continue
        try:
            os.killpg(t.pid, signal.SIGTERM)
        except OSError as e:
            print(f"{t.label}: SIGTERM to group {t.pid}: {e}")
        t0 = time.monotonic()
        while t.running() and time.monotonic() - t0 < a.grace:
            time.sleep(0.05)
        if t.running():
            for p in set(session_pids(t.pid)) | {t.pid}:
                try:
                    os.kill(p, signal.SIGKILL)
                except OSError:
                    pass
            try:
                os.killpg(t.pid, signal.SIGKILL)
            except OSError:
                pass
            t0 = time.monotonic()
            while alive(t.pid, t.token) and time.monotonic() - t0 < 5:
                time.sleep(0.05)
            job = load(t.path) or t.job
            if "exit" not in job and not alive(t.pid, t.token):
                # the wrapper died with the group, so nobody recorded the end: say what happened
                job.update(exit=137, signal=9, ended=time.time(), killed=True)
                save(t.path, job)
        running, _rc = report(t, a.tail)
        code = code or (1 if running else 0)
    return code


def cmd_clean(_a):
    mine = Path(jobs_dir())
    n = 0
    for p in sorted(mine.glob("*.json")):
        t = Target(name=p.stem, path=p)
        if t.job is None or t.running():
            continue
        log = Path(t.job.get("log") or "")
        if log.parent == mine and log.exists():        # a log written elsewhere (--log) is the caller's
            log.unlink()
        p.unlink()
        n += 1
    print(f"bgwait: forgot {n} ended job(s) in {mine}")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["_exec"] and len(argv) == 2:
        return cmd_exec(argv[1])
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="The module docstring is the manual: head -70 tools/bgwait.py")
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("run", help="start a command detached; record its pid, start time and exit code")
    p.add_argument("--name", required=True, help="the job's name (letters, digits, . _ -)")
    p.add_argument("--log", help="stdout + stderr go here (default: <jobs>/NAME.log)")
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="-- the command and its arguments")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("wait", help="wait by pid until all ended, or the timeout (exit 124)")
    p.add_argument("jobs", nargs="+", metavar="NAME|PID")
    p.add_argument("--timeout", type=float, default=TIMEOUT_DEFAULT,
                   help=f"seconds (default {TIMEOUT_DEFAULT:g}, at most {TIMEOUT_MAX:g})")
    p.add_argument("--every", type=float, default=30.0, help="a progress line per running job this often (0: none)")
    p.add_argument("--tail", type=int, default=6, help="log lines shown per job")
    p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("status", help="the same report, without waiting")
    p.add_argument("jobs", nargs="*", metavar="NAME|PID")
    p.add_argument("--sample", type=float, default=1.0, help="seconds a running job's CPU is watched")
    p.add_argument("--tail", type=int, default=6)
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("kill", help="SIGTERM the job's process group, SIGKILL what is left")
    p.add_argument("jobs", nargs="+", metavar="NAME")
    p.add_argument("--grace", type=float, default=5.0)
    p.add_argument("--tail", type=int, default=3)
    p.set_defaults(fn=cmd_kill)
    p = sub.add_parser("clean", help="forget the ended jobs (their job files and default logs)")
    p.set_defaults(fn=cmd_clean)
    a = ap.parse_args(argv)
    if a.verb == "run" and a.cmd[:1] == ["--"]:
        a.cmd = a.cmd[1:]
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
