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
     second job of a name that still runs;
  7. STEPS: a job that runs other commands in turn records, per command, the
     wall-clock it worked, its CPU and its runs, and `wait` prints the table --
     the script that solved on one core for most of a job is named.

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
    # 0 ---------------------------------------------------------------- the cores table, by hand
    sys.path.insert(0, str(BG.parent))
    import bgwait
    H = 3600.0
    jobs = [{"cmd": ["tools/kpy", "tools/route/netgroup.py", "a.json"], "started": 0, "ended": H, "cpu_s": H},
            {"cmd": ["tools/kpy", "tools/route/netgroup.py", "b.json"], "started": 0, "ended": H, "cpu_s": H},
            {"cmd": ["python3", "build/loop.py"], "started": H, "ended": 3 * H, "cpu_s": 24 * H},
            {"cmd": ["python3", "build/loop.py"], "started": 0, "ended": None, "cpu_s": None}]      # still running: left out
    rows, (busy, cpu, mean, total) = bgwait.cores_table(jobs, total=24)
    r = {x[0]: x for x in rows}
    expect([round(v, 6) for v in r["netgroup.py"][1:6]] == [2, 1.0, 2.0, 1.0, 2.0],
           "cores: two 1 h router runs side by side -- 1 h of wall, 2 core-hours, 1 core a run, 2 at once", str(rows))
    expect([round(v, 6) for v in r["loop.py"][1:6]] == [1, 2.0, 24.0, 12.0, 1.0],
           "cores: one 2 h loop at 24 core-hours -- 12 cores a run", str(rows))
    expect((round(busy, 6), round(cpu, 6), round(mean, 6), total) == (3.0, 26.0, round(26 / 3, 6), 24),
           "cores: 3 h with a job running, 26 core-hours, 8.67 of 24 busy", str((busy, cpu, mean, total)))
    expect(bgwait.cores_table(jobs, total=24, since=H)[0][0][0] == "loop.py" and len(bgwait.cores_table(jobs, since=H)[0]) == 1,
           "cores: --since keeps the jobs started on or after it")
    # 0a --------------------------------------------------------------- a step's name, from its argv
    for argv, want in ((["python3", "/x/build/strip_plan.py", "--copy", "c"], "strip_plan.py"),
                       (["timeout", "1700", "tools/kpy", "tools/route/netgroup.py", "--board", "b"], "netgroup.py"),
                       (["nice", "-n", "5", "/venv/bin/python3", "-u", "x.py"], "x.py"),
                       (["env", "A=1", "bash", "build/loop.sh", "j"], "loop.sh"),
                       (["bash", "-c", "cd x; y"], "bash -c"), (["python3", "-m", "pytest", "-q"], "pytest"),
                       (["/usr/bin/java", "-Xmx2g", "-jar", "/a/router.jar", "--gui"], "router.jar"),
                       (["kicad-cli", "pcb", "drc"], "kicad-cli"), (["sleep", "4"], "sleep")):
        expect(bgwait.step_label(argv) == want, f"steps: {' '.join(argv)} is the step {want}", bgwait.step_label(argv))
    lines = bgwait.steps_lines([["strip_plan.py", 5, 4500.0, 4600.0], ["u500_fanout.py", 3, 560.0, 7300.0], ["cp", 9, 20.0, 1.0]], 6600.0)
    expect(len(lines) == 3 and "68 %" in lines[1] and "strip_plan.py  x5, 1.0 core(s)" in lines[1] and "13.0 core(s)" in lines[2],
           "steps: the report names the command that held 68 % of a job at one core, and drops a step under 3 %", str(lines))
    tmp = Path(tempfile.mkdtemp(prefix="test_bgwait_"))
    env = dict(os.environ, HW_BGWAIT_DIR=str(tmp / "jobs"))
    env.pop("HW_MEM_FLOOR_GB", None)
    if bgwait.LINUX:
        # 0c -------------------------------------------------------------- steps: which command of a job used its wall-clock
        busy = tmp / "busy_step.py"
        tmp.mkdir(parents=True, exist_ok=True)
        busy.write_text("import sys, time\nt = time.time() + float(sys.argv[1])\nwhile time.time() < t:\n    pass\n")
        loop = tmp / "loop_step.sh"
        loop.write_text(f"for i in 1 2; do {sys.executable} {busy} 1.5; done\nsleep 1\n")
        rc, out, _ = bg(dict(env, HW_BGWAIT_STEP_EVERY="0.1"), "run", "--name", "steps", "--", "bash", str(loop))
        rc, out, _ = bg(env, "wait", "steps", "--timeout", "30", timeout=40)
        st = {r[0]: r for r in json.loads((tmp / "jobs" / "steps.json").read_text()).get("steps", [])}
        b = st.get("busy_step.py")
        expect(b is not None and b[1] == 2 and 2.4 <= b[2] <= 3.6 and 0.8 <= b[3] / b[2] <= 1.2,
               "steps: two 1.5 s runs of a script inside a loop -- 2 runs, about 3 s of wall, one core", str(st))
        expect("loop_step.sh" not in st and st.get("sleep", [0, 0, 0, 9])[3] < 0.2,
               "steps: the shell that waited on them is not a step, and a sleep used no CPU", str(st))
        expect("where the time went" in out and "busy_step.py  x2" in out and not (tmp / "jobs" / "steps.steps").exists(),
               "steps: the wait report prints the table, and the running job's side file is gone at the end", out)
        # 0b -------------------------------------------------------------- memory: the floor, the kernel's first choice, the peak
        rc, out, _ = bg(dict(env, HW_MEM_FLOOR_GB="99999"), "run", "--name", "nomem", "--", sys.executable, "-c", "print(1)")
        expect(rc == 75 and "NOT STARTED" in out and not (tmp / "jobs" / "nomem.json").exists(),
               "memory: under the floor `run` refuses (exit 75) and starts nothing", out)
        code = ("import time; a = bytearray(300 * 2 ** 20); a[::4096] = b'x' * len(a[::4096]); "
                "print(open('/proc/self/oom_score_adj').read().strip()); time.sleep(4.5)")
        rc, out, _ = bg(env, "run", "--name", "mem", "--", sys.executable, "-c", code)
        rc, out, _ = bg(env, "wait", "mem", "--timeout", "20", timeout=30)
        job = json.loads((tmp / "jobs" / "mem.json").read_text())
        expect("| 900" in out, "memory: a job runs at oom_score_adj 900 -- the kernel takes it before the session", out)
        if job.get("scope"):                          # Linux with a systemd user manager: the capped jobs slice
            # a job past a (test) cap is killed inside the slice, its exit recorded, and nothing outside it touched
            hog = dict(env, HW_JOBS_SLICE="hwjobs-test.slice", HW_JOBS_MEM_GB="0.2", HW_JOBS_SWAP_GB="0")
            rc, out, _ = bg(hog, "run", "--name", "hog", "--", sys.executable, "-c",
                            "a = bytearray(800 * 2 ** 20); a[::4096] = b'x' * len(a[::4096])")
            rc, out, _ = bg(env, "wait", "hog", "--timeout", "20", timeout=30)
            expect("hog: ended, exit 137, signal 9" in out,
                   "memory: a job past the jobs slice's cap is killed there (signal 9, recorded), and this test lives on", out)
        expect(0.25 <= job.get("peak_gb", 0) <= 0.6 and "peak 0." in out,
               f"memory: a job that held 300 MB records its peak ({job.get('peak_gb')} GB) and the report shows it", out)
        rows, _ = bgwait.cores_table([job])
        expect(rows and rows[0][7] == job["peak_gb"], "memory: `cores` carries the peak of a run", str(rows))
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
