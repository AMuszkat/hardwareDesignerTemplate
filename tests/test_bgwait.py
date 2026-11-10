#!/usr/bin/env python3
"""
kicad_project/tools/bgwait.py end to end, on real processes, in a temp job
folder (HW_BGWAIT_DIR): nothing in the repository is written.

    python3 tests/test_bgwait.py

What must hold:
  1. a job that ENDS: `wait` returns at once with its exit code and the tail
     of its log (exit 3 -> wait exits 1; exit 0 -> wait exits 0);
  2. a job that SLEEPS: `wait --timeout 3` comes back at the limit with exit
     124 and the line "0.0 s CPU in the last N s: idle or hung";
  3. THE INCIDENT: a wait whose own command line carries the job's name does
     not hang.  The job is named like the pattern the board session polled,
     the waiting shell's command line contains that string, and the job ends
     after a second: the wait must return within seconds, by pid;
  4. `kill` ends a job and its children (the whole process group) and the job
     file says how it ended;
  5. a bare PID (a process not started by `run`) is waited on too, with its
     exit code reported as unknown; a pid that is not there is "not running";
  6. usage errors exit 2: an unknown name, a --timeout that is not positive, a
     second job of a name that still runs.

Standard library only, nothing to skip.  The last line is PASS / FAIL with
the measured times.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BG = ROOT / "kicad_project" / "tools" / "bgwait.py"
PATTERN = "netgroup.py --board board1 routing/R2_emmc_"     # what the incident's loop polled

fails = []


def bg(env, *args, timeout=60, shell_line=None):
    """Run bgwait (or a whole shell line) -> (exit code, stdout+stderr, seconds)."""
    t = time.perf_counter()
    cmd = ["bash", "-c", shell_line] if shell_line else [sys.executable, str(BG), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=str(ROOT))
        rc, out = r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        rc, out = -999, f"HUNG: no return within {timeout} s\n{e.stdout or ''}"
    return rc, out, time.perf_counter() - t


def expect(ok, what, out=""):
    print(("  ok   " if ok else "  BAD  ") + what)
    if not ok:
        fails.append(what)
        if out:
            print("\n".join("       | " + ln for ln in out.strip().splitlines()[-25:]))


def gone(pid):
    """No such process, or only a zombie of it."""
    try:
        txt = Path(f"/proc/{pid}/stat").read_text()
        return txt.rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return not os.path.isdir("/proc")              # alive (macOS path); on Linux /proc said gone


def main():
    if not BG.is_file():
        print(f"FAIL: {BG} is missing")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="test_bgwait_"))
    env = dict(os.environ, HW_BGWAIT_DIR=str(tmp / "jobs"))
    env.pop("HW_BOARD", None)
    numbers = {}
    loose = []
    try:
        # 1 ---------------------------------------------------------------- a job that ends
        print("1. a job that ends")
        rc, out, _ = bg(env, "run", "--name", "quick", "--", "sh", "-c",
                        "echo first line; echo the last line of quick; exit 3")
        expect(rc == 0 and "quick: started, pid " in out, "run prints the pid", out)
        rc, out, dt = bg(env, "wait", "quick", "--timeout", "30")
        numbers["ended"] = dt
        expect(rc == 1, f"wait exits 1 for a job that exited 3 (got {rc})", out)
        expect("quick: ended, exit 3" in out, "the report names the exit code", out)
        expect("| the last line of quick" in out, "the report carries the log's tail", out)
        expect(dt < 5, f"it returned promptly ({dt:.2f} s of a 30 s limit)")
        rc, out, _ = bg(env, "run", "--name", "fine", "--", "sh", "-c", "echo all good")
        rc, out, dt = bg(env, "wait", "fine", "quick", "--timeout", "30")
        expect(rc == 1 and "fine: ended, exit 0" in out and "quick: ended, exit 3" in out,
               "two jobs in one wait: both reported, exit 1 because one failed", out)
        rc, out, dt = bg(env, "wait", "fine", "--timeout", "30")
        expect(rc == 0 and "| all good" in out, "wait exits 0 when every job exited 0", out)
        job = json.loads((tmp / "jobs" / "quick.json").read_text())
        expect(all(k in job for k in ("name", "pid", "start", "cmd", "log", "exit")),
               "the job file holds name, pid, start time, cmd, log and the exit code")

        # 2 ---------------------------------------------------------------- a job that sleeps
        print("2. a sleeping job")
        rc, out, _ = bg(env, "run", "--name", "sleeper", "--", "sh", "-c",
                        "echo going to sleep; sleep 300 & sleep 300; wait")
        expect(rc == 0, "run", out)
        rc, out, dt = bg(env, "wait", "sleeper", "--timeout", "3")
        numbers["timeout"] = dt
        expect(rc == 124, f"wait exits 124 at the timeout (got {rc})", out)
        expect(2.8 <= dt < 8, f"it came back at the limit ({dt:.2f} s for --timeout 3)")
        expect("sleeper: RUNNING" in out and "idle or hung" in out and "0.0 s CPU in the last" in out,
               "the report says RUNNING and '0.0 s CPU in the last N s: idle or hung'", out)
        expect("| going to sleep" in out, "and still shows the log's tail", out)
        rc, out, _ = bg(env, "run", "--name", "sleeper", "--", "true")
        expect(rc == 2 and "still running" in out, "a second job of a running name is refused (exit 2)", out)
        rc, out, _ = bg(env, "status")
        expect(rc == 0 and "sleeper: RUNNING" in out and "quick: ended, exit 3" in out,
               "status lists the running and the ended jobs", out)

        # 3 ---------------------------------------------------------------- the incident
        print("3. the waiter's own command line carries the job's name")
        name = "R2_emmc_a"
        rc, out, _ = bg(env, "run", "--name", name, "--", "sh", "-c",
                        f"sleep 1; echo routed; : {PATTERN}a.json")
        expect(rc == 0, "run", out)
        line = (f": 'waiting for {PATTERN}' ; {sys.executable} {BG} wait {name} --timeout 30 "
                f"# {PATTERN} {name}")
        rc, out, dt = bg(env, shell_line=line, timeout=45)
        numbers["incident"] = dt
        expect(rc == 0, f"the wait returns 0 (got {rc})", out)
        expect(dt < 10, f"it ended with the job, not at the limit ({dt:.2f} s; the job runs 1 s, the limit is 30 s)")
        expect(f"{name}: ended, exit 0" in out and "| routed" in out, "with the exit code and the log", out)
        if shutil.which("pgrep"):
            # what the board session did, for the record: the shell asking is itself a match
            r = subprocess.run(["bash", "-c", f"pgrep -f '{PATTERN}zzz_no_such_run' >/dev/null; echo $?"],
                               capture_output=True, text=True)
            print(f"       (for the record: `pgrep -f` for a run that does not exist returns "
                  f"{r.stdout.strip()} from a shell whose command line names it; 0 = 'still running')")

        # 4 ---------------------------------------------------------------- kill
        print("4. kill")
        job = json.loads((tmp / "jobs" / "sleeper.json").read_text())
        pid = job["pid"]
        kids = []
        if os.path.isdir("/proc"):
            for p in Path("/proc").iterdir():
                if p.name.isdigit():
                    try:
                        f = (p / "stat").read_text().rsplit(")", 1)[1].split()
                    except (OSError, IndexError):
                        continue
                    if int(f[3]) == pid:                      # field 6: the session id
                        kids.append(int(p.name))
            expect(len(kids) >= 3, f"the job is a session of its own with its children in it ({len(kids)} processes)")
        t = time.perf_counter()
        rc, out, dt = bg(env, "kill", "sleeper")
        numbers["kill"] = dt
        expect(rc == 0 and "sleeper: ended, exit" in out, f"kill reports the job ended (exit {rc})", out)
        time.sleep(0.3)
        left = [p for p in kids + [pid] if not gone(p)]
        expect(not left, f"no process of the job is left (left: {left})")
        rc, out, dt = bg(env, "wait", "sleeper", "--timeout", "10")
        expect(rc == 1 and dt < 5 and "signal 15" in out, "a wait on the killed job returns at once, exit 1, signal named", out)
        rc, out, _ = bg(env, "kill", "sleeper")
        expect(rc == 0 and "already ended" in out, "killing it again is harmless", out)

        # 5 ---------------------------------------------------------------- bare pids
        print("5. bare pids")
        p1 = subprocess.Popen(["sleep", "1"])
        p2 = subprocess.Popen(["sleep", "300"])
        loose += [p1, p2]
        rc, out, dt = bg(env, "wait", str(p1.pid), "--timeout", "20")
        expect(rc == 1 and dt < 8 and "exit code unknown" in out,
               f"a bare pid that ends: reported in {dt:.2f} s, exit code unknown, wait exits 1", out)
        rc, out, dt = bg(env, "wait", str(p2.pid), "fine", "--timeout", "2")
        expect(rc == 124 and f"pid {p2.pid}: RUNNING" in out and "fine: ended, exit 0" in out,
               "a bare pid that runs on, next to an ended job: exit 124, both reported", out)
        p2.kill()
        p2.wait()
        p1.wait()
        rc, out, dt = bg(env, "wait", str(p2.pid), "--timeout", "5")
        expect(rc == 1 and dt < 3 and "not running" in out, "a pid that is not there is 'not running', at once", out)

        # 6 ---------------------------------------------------------------- usage
        print("6. usage errors")
        rc, out, dt = bg(env, "wait", "R2_emmc_", "--timeout", "5")
        expect(rc == 2 and "no job named" in out and dt < 3,
               "a name PATTERN is not a job: exit 2 at once (never matched against command lines)", out)
        rc, out, _ = bg(env, "wait", "fine", "--timeout", "0")
        expect(rc == 2, "--timeout 0 is refused: a wait always has a limit", out)
        rc, out, dt = bg(env, "wait", "fine", "--timeout", "99999")
        expect(rc == 0 and "capped at 3600" in out, "--timeout above the cap is capped at 3600 s, and said so", out)
        rc, out, _ = bg(env, "clean")
        expect(rc == 0 and not list((tmp / "jobs").glob("*.json")), "clean forgets the ended jobs", out)
    finally:
        for p in loose:
            if p.poll() is None:
                p.kill()
        for jf in (tmp / "jobs").glob("*.json"):
            try:
                os.killpg(json.loads(jf.read_text())["pid"], signal.SIGKILL)
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print(f"FAIL: {len(fails)} check(s); first: {fails[0]}")
        return 1
    print(f"PASS: ended job reported in {numbers['ended']:.2f} s with exit code and log tail; sleeping job -> exit 124 "
          f"after {numbers['timeout']:.2f} s (--timeout 3) with the idle line; wait naming its own job returned in "
          f"{numbers['incident']:.2f} s (job 1 s, limit 30 s); kill ended the process group in {numbers['kill']:.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
