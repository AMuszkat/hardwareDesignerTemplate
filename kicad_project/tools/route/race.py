#!/usr/bin/env python3
"""Race several variants of a group's spec -- one process each, each on its
own scratch copy of the board, each gated by its own `route.py check` -- and
print ONE table with the winner.  Nothing is written to the real board.

    tools/kpy tools/route/race.py --board board1 routing/R3_a.json routing/R3_b.json "routing/R3_a.json --seed 2"
    tools/kpy tools/route/race.py --board board1 --timeout 900 a.json b.json c.json -- --repair

WHY.  A group that does not converge is answered by trying something else: the
other layer first, a wider window, another seed, `--repair`.  Tried one after
another, each try costs a full run before the next can start, and the session
decides in between on one result at a time.  The tries do not depend on each
other, so they run AT ONCE, one router process per variant (the router itself
is one core per run), and the decision is taken once, on all results, by an
order that is written down.

A VARIANT is one argument: a spec file, optionally followed -- inside the same
quoted argument -- by netgroup options for that variant alone
(`"routing/R3_a.json --seed 2 --repair"`).  Options after a bare `--` go to
every variant.  The same spec twice with different options is two variants.
A spec is found like netgroup finds it: as typed, else inside the board folder.

WHAT ONE VARIANT DOES, in its own process group:
  1. the board folder is COPIED to `<race>/<variant>/<board folder>/` (real
     copies, never links; renders, datasheets, backups and lock files left
     out; `routing/.drc_cache/` kept, so a baseline the real board already has
     is not run again).  The copy lives outside kicad_project/ -- the system
     temp folder, or `--out DIR` -- because the board tools refuse a folder
     inside kicad_project/ that is not a board;
  2. `netgroup.py --board <copy> <spec> --timeout S [options]`, log in
     `<race>/<variant>/netgroup.log`.  `--timeout` (default 600 s) is
     netgroup's own: it stops between iterations and leaves a partial.  A run
     still alive at 1.5 x S + 60 s is killed, group and all;
  3. if it converged: `route.py check --board <copy> <plan>`, log in
     `<race>/<variant>/check.log`, killed after `--check-timeout` (900 s);
  4. the plan (or the partial) is copied to `<race>/<variant>/plan.json`
     (`partial.json`), and the board copy is deleted unless `--keep`.

At most `--jobs` variants run at a time: HW_JOBS when set, else one per
variant up to the core count less one (tools/par.py's rule).  Each child gets
HW_JOBS = cores / jobs, so a variant that itself forks (`--seeds`, `--jacobi`)
does not oversubscribe; neither changes its plan with HW_JOBS.  A variant's
plan is the plan the same command writes on the real board: same bytes, same
sha (tests/test_race.py holds it to that).

THE TABLE: variant, converged, iterations, route seconds, check verdict, new
DRC, rule failures, vias, track length (mm, every track of the plan), and for
a variant that did not converge how it ended.

THE WINNER, in this order -- the first difference decides:
  1. converged (a plan was written);
  2. the check ran to a verdict;
  3. fewest new DRC violations;
  4. fewest rule failures (the guide's own rows, `CHECK FAIL (N new DRC, M rule)`);
  5. fewest vias;
  6. shortest track length;
  7. the order on the command line.
It is the best of what ran, not a pass: the line says whether it is CHECK
PASS.  Under it, the commands that take it to the real board: copy the plan,
`route.py check` it THERE (the gate of record is the real board's), `apply`.

    exit 0    the winner converged and its check passed
    exit 1    no variant did (the table and the logs say why)
    exit 2    usage

Plain Python, no pcbnew: it only starts the tools that need it, through
tools/kpy.  `<race>/race.json` is the table as data.
"""
import argparse
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
KP = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import boardsel      # noqa: E402  (which folder --board means, and the other-board wall)
import par           # noqa: E402  (the core-count rule)

KPY = TOOLS / "kpy"
NETGROUP = TOOLS / "route" / "netgroup.py"
ROUTE = TOOLS / "route" / "route.py"

# left out of a scratch copy: big, or another process's state, never an input of the router or the gate
SKIP_DIRS = {"renders", "datasheets", ".history", ".git", "sch_plan", ".board_setup_backups", "handoff.old",
             "__pycache__"}
SKIP_GLOBS = ("*-backups", "*-bak", "*.bak", "_autosave-*", "~*", "*.lck", "fp-info-cache", "*.partial.json")

_procs = set()
_lock = threading.RLock()        # re-entrant: the signal handler runs in the main thread, which may hold it
_stop = threading.Event()        # set by a signal: no variant starts another process
_t0 = time.monotonic()
# a variant's processes are a GROUP of their own (killed whole at a limit) inside race.py's SESSION, so
# whoever ends the session -- `bgwait.py kill` -- ends them too
_GROUP = dict(process_group=0) if sys.version_info >= (3, 11) else dict(start_new_session=True)


def die(msg):
    print(f"race: {msg}", file=sys.stderr)
    sys.exit(2)


def note(msg):
    with _lock:
        print(f"[{time.monotonic() - _t0:6.1f} s] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# measuring a plan, ranking results (imported by tests/test_race.py)
# ---------------------------------------------------------------------------

def measure_plan(path):
    """(track length in mm, vias, sha256) of a plan file -- every track and
    via the plan holds, prelaid `extra_*` included: what `apply` would lay."""
    raw = Path(path).read_bytes()
    plan = json.loads(raw)
    length = 0.0
    for t in plan.get("tracks", []):
        pts = t.get("pts", [])
        length += sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    return round(length, 3), len(plan.get("vias", [])), hashlib.sha256(raw).hexdigest()


def read_check(text, rc):
    """route.py check's verdict out of its output -> (verdict, new DRC, rule).
    verdict: 'PASS', 'FAIL', or None when it never printed one (it crashed)."""
    last = None
    for ln in text.splitlines():
        if ln.startswith("CHECK "):
            last = ln
    if last is None:
        return None, None, None
    if last.startswith("CHECK PASS"):
        return "PASS", 0, 0
    m = re.search(r"\((\d+) new DRC, (\d+) rule\)", last)
    return "FAIL", int(m.group(1)) if m else None, int(m.group(2)) if m else None


def iterations(log):
    """Negotiation iterations a netgroup log holds, every leg and seed counted."""
    return len(re.findall(r"^\s+iteration \d+:", log, re.M))


def rank_key(r):
    """The winner's order (module docstring): smaller is better."""
    big = 10 ** 9
    return (0 if r.get("converged") else 1,
            0 if r.get("check") in ("PASS", "FAIL") else 1,
            r["new_drc"] if r.get("new_drc") is not None else big,
            r["rule"] if r.get("rule") is not None else big,
            r["vias"] if r.get("vias") is not None else big,
            r["length_mm"] if r.get("length_mm") is not None else float(big),
            r.get("order", 0))


def winner(results):
    """The best result by `rank_key`, or None when nothing ran."""
    return min(results, key=rank_key) if results else None


# ---------------------------------------------------------------------------
# one variant
# ---------------------------------------------------------------------------

def label_of(spec, opts, taken):
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(spec).stem) or "variant"
    tail = "".join(re.sub(r"[^A-Za-z0-9.]+", "", o) if o.startswith("-") else "_" + re.sub(r"[^A-Za-z0-9.]+", "", o)
                   for o in opts)
    name = (base + ("-" + tail if tail else ""))[:60]
    k, out = 2, name
    while out in taken:
        out, k = f"{name}-{k}", k + 1
    taken.add(out)
    return out


def copy_ignore(_dir, names):
    import fnmatch
    return [n for n in names if n in SKIP_DIRS or any(fnmatch.fnmatch(n, g) for g in SKIP_GLOBS)]


def run_logged(cmd, cwd, log, env, limit):
    """Run `cmd` in its own process group, output to `log`; kill the group at
    `limit` seconds -> (exit code or None when killed, seconds)."""
    t = time.monotonic()
    with open(log, "wb") as lf:
        lf.write((" ".join(shlex.quote(str(c)) for c in cmd) + "\n").encode())
        lf.flush()
        with _lock:
            if _stop.is_set():
                raise RuntimeError("interrupted before it started")
            p = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                 stdout=lf, stderr=subprocess.STDOUT, **_GROUP)
            _procs.add(p)
        try:
            rc = p.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            kill_group(p)
            rc = None
        finally:
            with _lock:
                _procs.discard(p)
    return rc, time.monotonic() - t


def kill_group(p):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
        except OSError:
            pass
        try:
            p.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def kill_all(signum=None, _frame=None):
    """SIGTERM / SIGINT / SIGHUP to race.py: end every variant's process group,
    start nothing more, and leave by the interrupted path.  Without it the
    variants would outlive the race and keep their cores."""
    _stop.set()
    with _lock:
        live = list(_procs)
    for sig, pause in ((signal.SIGTERM, 1.0), (signal.SIGKILL, 0.0)):
        for p in live:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, sig)
                except OSError:
                    pass
        t = time.monotonic()
        while pause and any(p.poll() is None for p in live) and time.monotonic() - t < pause:
            time.sleep(0.05)
    if signum is not None:
        raise KeyboardInterrupt


def run_variant(v, board, race_dir, a, env):
    """One variant, start to finish -> its result row (plain data)."""
    vdir = race_dir / v["label"]
    scratch = vdir / board.name
    r = dict(variant=v["label"], order=v["order"], spec=v["spec_arg"], options=v["opts"], converged=False,
             state="not run", iterations=None, route_s=None, check=None, check_s=None, new_drc=None, rule=None,
             vias=None, length_mm=None, plan_sha=None, plan=None, dir=str(vdir))
    try:
        vdir.mkdir(parents=True)
        t = time.monotonic()
        shutil.copytree(board, scratch, ignore=copy_ignore, symlinks=False)
        r["copy_s"] = round(time.monotonic() - t, 2)
        # the spec, at the place it has in the real board (or routing/ for one kept elsewhere)
        try:
            rel = v["spec"].resolve().relative_to(board.resolve())
        except ValueError:
            rel = Path("routing") / v["spec"].name
        spec = scratch / rel
        spec.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(v["spec"], spec)
        shutil.copyfile(v["spec"], vdir / "spec.json")
        plan = scratch / v["plan_rel"]
        plan.parent.mkdir(parents=True, exist_ok=True)
        partial = plan.with_suffix(".partial.json")
        for old in (plan, partial):                  # the real board's last result is not this run's
            if old.exists():
                old.unlink()
        timeout = v["timeout"]
        cmd = [KPY, NETGROUP, "--board", scratch, spec] + v["opts"]
        if "--timeout" not in v["opts"]:
            cmd += ["--timeout", f"{timeout:g}"]
        note(f"{v['label']}: routing ({r['copy_s']} s to copy the board)")
        rc, secs = run_logged(cmd, vdir, vdir / "netgroup.log", env, 1.5 * timeout + 60)
        log = (vdir / "netgroup.log").read_text(errors="replace")
        r["route_s"] = round(secs, 1)
        r["iterations"] = iterations(log)
        r["core"] = next((ln.split(":", 1)[1].strip() for ln in log.splitlines() if ln.startswith("router core:")), "")
        if rc == 0 and plan.exists():
            r["converged"], r["state"] = True, "converged"
            shutil.copyfile(plan, vdir / "plan.json")
            r["plan"] = str(vdir / "plan.json")
            r["length_mm"], r["vias"], r["plan_sha"] = measure_plan(plan)
        else:
            if rc is None:
                r["state"] = "killed at the hard limit"
            elif rc < 0:
                r["state"] = f"killed by signal {-rc}"
            elif "STOPPED at the timeout" in log:
                r["state"] = "stopped at the timeout"
            elif "NO SOLUTION" in log:
                r["state"] = "no solution"
            else:
                last = [ln for ln in log.splitlines() if ln.strip()][-1:] or ["no output"]
                r["state"] = f"exit {rc}: {last[0][:90]}"
            if partial.exists():
                shutil.copyfile(partial, vdir / "partial.json")
                try:
                    st = json.loads(partial.read_text()).get("state")
                    r["state"] += f" (partial: {st})" if st else ""
                except ValueError:
                    pass
            note(f"{v['label']}: {r['state']} after {r['route_s']} s, {r['iterations']} iterations")
            return r
        note(f"{v['label']}: converged in {r['route_s']} s, {r['iterations']} iterations; checking")
        rc, secs = run_logged([KPY, ROUTE, "check", "--board", scratch, plan], vdir, vdir / "check.log", env,
                              a.check_timeout)
        out = (vdir / "check.log").read_text(errors="replace")
        r["check_s"] = round(secs, 1)
        r["check"], r["new_drc"], r["rule"] = read_check(out, rc)
        if r["check"] is None:
            r["check"] = "killed" if rc is None else f"no verdict (exit {rc})"
        note(f"{v['label']}: CHECK {r['check']} in {r['check_s']} s")
    except Exception as e:                           # one variant's accident is a row, not the race's end
        r["state"] = f"race.py: {type(e).__name__}: {e}"
        note(f"{v['label']}: {r['state']}")
    finally:
        if not a.keep:
            shutil.rmtree(scratch, ignore_errors=True)
    return r


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------

def table(results):
    def cell(x, fmt="{}"):
        return "-" if x is None else fmt.format(x)
    rows = [("variant", "converged", "iterations", "route s", "check", "new DRC", "rule", "vias", "length mm", "")]
    for r in results:
        rows.append((r["variant"], "yes" if r["converged"] else "NO", cell(r["iterations"]),
                     cell(r["route_s"], "{:.1f}"), cell(r["check"]), cell(r["new_drc"]), cell(r["rule"]),
                     cell(r["vias"]), cell(r["length_mm"], "{:.2f}"), "" if r["converged"] else r["state"]))
    w = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]) - 1)]
    out = []
    for k, row in enumerate(rows):
        line = "  ".join(str(c).ljust(w[i]) if i < 2 else str(c).rjust(w[i]) for i, c in enumerate(row[:-1]))
        out.append((line + ("  " + row[-1] if row[-1] else "")).rstrip())
        if k == 0:
            out.append("  ".join("-" * x for x in w))
    return "\n".join(out)


def tree_sha(folder):
    """One digest of the files a race must leave alone (the board file and the
    routing folder's plans and specs): the race reports if it moved."""
    h = hashlib.sha256()
    for p in sorted(Path(folder).glob("*.kicad_pcb")) + sorted(Path(folder).glob("routing/*.json")):
        try:
            h.update(p.name.encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
        except OSError:
            pass
    return h.hexdigest()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    common = []
    if "--" in argv:
        k = argv.index("--")
        argv, common = argv[:k], argv[k + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="The module docstring is the manual: head -70 tools/route/race.py")
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("variants", nargs="+", metavar="SPEC|'SPEC --opt ...'",
                    help="a spec file, or one quoted argument: the spec and this variant's netgroup options")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="seconds, each variant's netgroup --timeout (default 600); hard kill at 1.5 x + 60 s")
    ap.add_argument("--check-timeout", type=float, default=900.0, help="seconds, each variant's route.py check")
    ap.add_argument("--jobs", type=int, default=0, help="variants at a time (default: HW_JOBS, else cores - 1)")
    ap.add_argument("--out", help="the race folder (default: a new one in the system temp folder); "
                                  "outside kicad_project/")
    ap.add_argument("--keep", action="store_true", help="keep every variant's board copy (default: plans and logs only)")
    a = ap.parse_args(argv)
    if a.timeout <= 0:
        die("--timeout must be positive: every variant has a limit")
    if any(o == "--board" or o.startswith("--board=") for o in common):
        die("--board belongs to race.py: every variant runs on its own copy of that board")

    board = Path(boardsel.resolve(a.board)).resolve()
    boardsel.one_pcb(board)
    variants, taken = [], set()
    for k, arg in enumerate(a.variants):
        toks = shlex.split(arg)
        if not toks:
            die(f"variant {k + 1} is empty")
        spec_arg, opts = toks[0], toks[1:] + common
        if any(o == "--board" or o.startswith("--board=") for o in opts):
            die(f"{arg!r}: --board belongs to race.py: every variant runs on its own copy of that board")
        spec = Path(boardsel.in_board(board, spec_arg))
        if not spec.is_file():
            die(f"{spec_arg}: no such spec (as typed, or inside {board.name}/)")
        try:
            S = json.loads(spec.read_text())
        except ValueError as e:
            die(f"{spec_arg}: not JSON ({e})")
        if "plan" not in S:
            die(f"{spec_arg}: the spec lacks \"plan\" (tools/route/README.md)")
        plan_rel = Path(S["plan"])
        if plan_rel.is_absolute() or ".." in plan_rel.parts:
            die(f"{spec_arg}: \"plan\": {S['plan']} leaves the board folder -- a raced variant writes only "
                "into its own copy; give a path inside the board (routing/R3_plan.json)")
        timeout = a.timeout
        if "--timeout" in opts:
            try:
                timeout = float(opts[opts.index("--timeout") + 1])
            except (IndexError, ValueError):
                die(f"{arg!r}: --timeout needs seconds")
        variants.append(dict(order=k, spec_arg=spec_arg, spec=spec, opts=opts, plan_rel=plan_rel, timeout=timeout,
                             label=label_of(spec_arg, toks[1:], taken)))

    if a.out:
        race_dir = Path(a.out).resolve()
        if race_dir == KP or KP in race_dir.parents:
            die(f"--out {a.out} is inside kicad_project/: the board tools refuse a board copy there. "
                "Use the system temp folder (the default) or the session scratchpad")
        race_dir.mkdir(parents=True, exist_ok=True)
        if any(race_dir.iterdir()):
            die(f"--out {race_dir} is not empty: a race does not write over another race's results")
    else:
        race_dir = Path(tempfile.mkdtemp(prefix=f"race-{board.name}-"))

    cores = os.cpu_count() or 2
    jobs = max(1, min(a.jobs, len(variants))) if a.jobs > 0 else par.workers(len(variants))
    if a.jobs <= 0 and sys.platform == "darwin" and not os.environ.get("HW_JOBS"):
        jobs = max(1, min(len(variants), cores - 1))     # par's macOS rule is about fork(); these are processes
    env = dict(os.environ, HW_JOBS=str(max(1, (cores - 1) // jobs)), PYTHONUNBUFFERED="1")
    before = tree_sha(board)
    note(f"{len(variants)} variant(s) of {board.name}, {jobs} at a time, in {race_dir}")
    t0 = time.monotonic()
    for s_ in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s_, kill_all)
    try:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(lambda v: run_variant(v, board, race_dir, a, env), variants))
    except KeyboardInterrupt:
        kill_all()
        print(f"race: interrupted, every variant's processes ended; what ran is in {race_dir}", file=sys.stderr)
        return 130
    wall = time.monotonic() - t0
    moved = tree_sha(board) != before

    win = winner(results)
    passed = bool(win and win["converged"] and win["check"] == "PASS")
    (race_dir / "race.json").write_text(json.dumps(dict(
        board=str(board), wall_s=round(wall, 1), jobs=jobs, winner=win["variant"] if win else None,
        winner_passed=passed, board_changed_during_race=moved, results=results), indent=1) + "\n")

    print(table(results))
    print(f"\n{len(results)} variant(s), {jobs} at a time: {wall:.1f} s wall-clock, "
          f"{sum((r['route_s'] or 0) + (r['check_s'] or 0) for r in results):.1f} s of route + check in all. "
          f"Plans and logs: {race_dir}")
    slow = [r["variant"] for r in results if str(r.get("core", "")).startswith("python")]
    if slow:
        print(f"⚠ router core: python in {', '.join(slow)} (the compiled core is missing or stale: tools/setup.sh)")
    if moved:
        print(f"⚠ {board.name}'s board file or routing/*.json CHANGED during the race (not by race.py): the "
              "results are for the board as it was; race again before applying")
    if not win or not win["converged"]:
        print("NO WINNER: no variant converged. The logs and partials above say where each stopped "
              "(`contested`, `reach`): change the spec, not the seed, when all stop at the same place.")
        return 1
    how = "CHECK PASS" if passed else f"NOT a pass: check {win['check']}" + (
        f" ({win['new_drc']} new DRC, {win['rule']} rule)" if win["new_drc"] is not None else "")
    print(f"WINNER: {win['variant']} -- {how}; {win['vias']} vias, {win['length_mm']:.2f} mm, "
          f"{win['iterations']} iterations in {win['route_s']:.1f} s")
    print("   order: converged, check ran, fewest new DRC, fewest rule failures, fewest vias, shortest, "
          "command-line order")
    b = a.board or board.name
    b = b if not os.path.isabs(str(b)) else str(board)
    plan_rel = variants[win["order"]]["plan_rel"]
    opts = " ".join(shlex.quote(o) for o in win["options"])
    print("To take it to the real board (nothing has been written there):")
    print(f"   cp {shlex.quote(win['plan'])} {shlex.quote(str(board / plan_rel))}")
    print(f"   tools/kpy tools/route/route.py check --board {b} {plan_rel}")
    print(f"   tools/kpy tools/route/route.py apply --board {b} {plan_rel}      # KiCad closed")
    print(f"   (or route it there again, the same plan: tools/kpy tools/route/netgroup.py --board {b} "
          f"{win['spec']}{' ' + opts if opts else ''})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
