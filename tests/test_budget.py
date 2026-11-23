#!/usr/bin/env python3
"""
kicad_project/tools/route/budget.py -- the effort budget -- on a board folder made of text
files in a temp kicad_project/, with bgwait.py's --group / --count on real processes.
Nothing in the repository is written, and KiCad is not needed.

    python3 tests/test_budget.py

What must hold:
  1. THE TIER is read from the nets: 5 plain nets A; a pair, 13 nets or a BGA ball B; 13
     balls, a matched bus or 41 nets C;
  2. WHOSE BOARD: the board folder and a scratch copy inside it are that board; a folder
     outside every board (a fixture, a review snapshot -- which carries a COPY of the
     ledger) has no budget;
  3. ROUNDS: tier A lets two router runs start and refuses the third with exit 76 and
     `BUDGET SPENT`; the round in hand keeps three checks, the fourth is refused;
  4. THE INCIDENT: the same nets under a new name are the same group -- a fresh name buys
     no fresh budget;
  5. a group drawn by hand: its first check opens the round (six checks, the seventh
     refused, tier A);
  6. runs of one group in flight together are one round;
  7. TIME: a group whose time is used is refused whatever rounds it has; a run that died
     without a word costs 10 minutes, not its limit;
  8. THE WALL: `wall` writes the next `## Qn` into QUESTIONS.md from the ledger with the
     changes in the order of their cost whatever order they were typed in, and prints the
     ring; needs --blocks and a --change; the group is then refused; it reopens when a
     later handoff revision answers that Qn, and when one of its parts moves; its second
     wall is addressed to the user;
  9. THE COUNT: one per placement and handoff revision, the second refused; `bgwait.py run
     --group G --count` is ended at the limit (3 s here) and recorded "it does not fit";
 10. `bgwait.py run --group`: the job is one round with its wall-clock time; a router run
     inside it is not charged twice; a job for a spent group is refused before it starts
     (exit 76); a job of no group is on the ledger's "outside every group" line;
 11. a finished group (every net on copper) starts a new budget when work on it resumes;
 12. HW_BUDGET_IN (set by race.py and prelayout.py for their own children): nothing is
     counted and nothing refused.

Standard library only.  The last line is PASS / FAIL.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "kicad_project" / "tools"
fails = []


def expect(ok, what, out=""):
    print(("  ok   " if ok else "  BAD  ") + what)
    if not ok:
        fails.append(what)
        if out:
            print("\n".join("       | " + ln for ln in str(out).strip().splitlines()[-25:]))


def pcb_text(x=10.0):
    fp = lambda ref, at: (f'\t(footprint "lib:X"\n\t\t(layer "F.Cu")\n\t\t(at {at} 5)\n'      # noqa: E731
                          f'\t\t(property "Reference" "{ref}"\n\t\t)\n\t)\n')
    return "(kicad_pcb\n\t(version 20260206)\n" + fp("U1", "20 20") + fp("J1", f"{x:g} 5") + fp("R1", "30 30") + ")\n"


def make(tmp):
    """A temp kicad_project/ with the two tools and one board of text files."""
    kp = tmp / "kicad_project"
    (kp / "tools" / "route").mkdir(parents=True)
    shutil.copy(TOOLS / "bgwait.py", kp / "tools" / "bgwait.py")
    src = (TOOLS / "route" / "budget.py").read_text()
    for a, b in (("COUNT_S = 30 * 60", "COUNT_S = 3"), ("UNGROUPED_S = 60", "UNGROUPED_S = 0.2")):
        assert a in src, a
        src = src.replace(a, b)
    (kp / "tools" / "route" / "budget.py").write_text(src)
    b = kp / "board9_test"
    (b / "handoff").mkdir(parents=True)
    (b / "routing").mkdir()
    nets = {f"P{i}": [["J1", str(i)], ["R1", "1"]] for i in range(60)}                # plain
    nets.update({f"B{i}": [["U1", f"A{i + 1}"], ["J1", str(100 + i)]] for i in range(40)})   # on a ball of U1
    nets.update({"USB_DP": [["J1", "200"], ["R1", "2"]], "USB_DN": [["J1", "201"], ["R1", "2"]]})
    (b / "handoff" / "netlist.json").write_text(json.dumps({"nets": nets}))
    (b / "handoff" / "MANIFEST.json").write_text(json.dumps({"rev": 7}))
    (b / "handoff" / "07_answers.md").write_text("# Answers\n\n## Q3 — something old\n")
    (b / "QUESTIONS.md").write_text("# Questions\n\n## Q3 — an old one\n**Status:** answered\n")
    (b / "board9.kicad_pcb").write_text(pcb_text())
    return kp, b


def refused(fn):
    """-> the exit code fn() left with, or None."""
    try:
        fn()
    except SystemExit as e:
        return e.code
    return None


def main():
    tmp = Path(tempfile.mkdtemp(prefix="budget-"))
    try:
        kp, b = make(tmp)
        sys.path.insert(0, str(kp / "tools" / "route"))
        import budget as B
        led = lambda: json.loads((b / "routing" / "ledger.json").read_text())          # noqa: E731
        P = lambda *i: [f"P{k}" for k in i]                                            # noqa: E731

        def run(name, nets, **kw):
            tok = B.gate(b, name, nets, kind="run", limit=60, **kw)
            tok.end("did not converge", ok=False)
            return tok

        def check(name, nets, board=b):
            tok = B.gate(board, name, nets, kind="check")
            tok.end("CHECK FAIL (1 new DRC, 0 rule)", ok=False)

        print("1. the tier")
        tiers = {k: B.classify(b, n, m)[0] for k, n, m in (
            ("5 plain", P(0, 1, 2, 3, 4), False), ("a pair", ["USB_DP", "USB_DN", "P0"], False),
            ("13 nets", P(*range(13)), False), ("3 balls", ["B0", "B1", "B2"], False),
            ("13 balls", [f"B{i}" for i in range(13)], False), ("matched", P(0, 1), True),
            ("41 nets", P(*range(41)), False))}
        expect(tiers == {"5 plain": "A", "a pair": "B", "13 nets": "B", "3 balls": "B", "13 balls": "C",
                         "matched": "C", "41 nets": "C"}, f"tiers from the nets: {tiers}")

        print("2. whose board")
        copy = b / "tmpWork" / "try1"
        (copy / "routing").mkdir(parents=True)
        shutil.copy(b / "board9.kicad_pcb", copy / "board9.kicad_pcb")
        snap = tmp / "out" / "reviews" / "RV1-pcb"
        (snap / "routing").mkdir(parents=True)
        shutil.copy(b / "board9.kicad_pcb", snap / "board9.kicad_pcb")
        (snap / "routing" / "ledger.json").write_text(json.dumps({"home": str(b), "groups": {}}))
        expect(B.home(b) == b.resolve() and B.home(copy) == b.resolve() and B.home(snap) is None,
               "the board and a copy inside it are the board; a snapshot outside (with a copied ledger) is nobody's")
        cache = tmp / "cache" / "board9_tmp" / "try2"                  # a copy kept outside kicad_project/
        cache.mkdir(parents=True)
        shutil.copy(b / "board9.kicad_pcb", cache / "board9_test.kicad_pcb")
        other = tmp / "cache" / "fixture"                              # another board's files: not this session's
        other.mkdir()
        shutil.copy(b / "board9.kicad_pcb", other / "board3_x.kicad_pcb")
        none = B.home(cache)
        os.environ["HW_BOARD"] = "board9"
        try:
            mine, theirs, snapped = B.home(cache), B.home(other), B.home(snap)
        finally:
            del os.environ["HW_BOARD"]
        expect(none is None and mine == b.resolve() and theirs is None and snapped is None,
               "a copy outside kicad_project is the board's when this session is that board's and the copy "
               f"carries its file name; else nobody's ({none}, {mine}, {theirs}, {snapped})")

        print("3. rounds and checks (tier A: 2 rounds, 3 checks each)")
        nA = P(0, 1, 2, 3, 4)
        run("gA", nA)
        check("gA", nA)
        run("gA", nA)
        rc = refused(lambda: run("gA", nA))
        expect(rc == 76, f"the third router run of a tier A group is refused with exit 76 (got {rc})")
        for _ in range(3):
            check("gA", nA, board=copy)                                 # on the scratch copy: charged to the board
        rc = refused(lambda: check("gA", nA))
        t = B.tally(led()["groups"]["gA"])
        expect(rc == 76 and t["rounds"] == 2 and t["state"] == "spent",
               f"round 2 keeps three checks (run on the copy), the fourth is refused (exit {rc}, {t['rounds']} rounds, {t['state']})")

        print("3b. rounds from before the ledger")
        nP = P(57, 58, 59)                                              # no later case touches these: a shared net is the same group
        spec = b / "routing" / "past_spec.json"
        spec.write_text(json.dumps({"group": "past", "window": [0, 0, 9, 9], "nets": {n: {} for n in nP}}))
        rc0 = B.cmd_budget(b, str(spec), before="1: a six-variant count, 0 of 3")
        rc1 = B.cmd_budget(b, str(spec), before="none")
        run("past", nP)
        rc = refused(lambda: run("past", nP))
        t = B.tally(led()["groups"]["past"])
        expect(rc0 == 0 and rc1 == 2 and rc == 76 and t["rounds"] == 2,
               f"one round entered as spent before the ledger leaves a tier A group one router run "
               f"({rc0}, {rc1}, exit {rc}, {t['rounds']} rounds)")

        print("4. the same nets under a new name")
        rc = refused(lambda: run("gA_retry_seed7", nA[:4]))
        expect(rc == 76 and "gA_retry_seed7" not in led()["groups"],
               f"a new name for four of gA's five nets is gA: refused (exit {rc}), no new group")

        print("5. a group drawn by hand")
        nH = P(10, 11, 12)
        for _ in range(6):
            check("hand", nH)
        rc = refused(lambda: check("hand", nH))
        expect(rc == 76 and B.tally(led()["groups"]["hand"])["rounds"] == 2,
               f"six checks are its two rounds, the seventh is refused (exit {rc})")

        print("6. runs in flight together")
        nF = P(20, 21, 22)
        child = ("import sys, time; sys.path.insert(0, sys.argv[1]); import budget; "
                 "budget.gate(sys.argv[2], 'fly', ['P20', 'P21', 'P22'], kind='run', limit=60); "
                 "print('in', flush=True); time.sleep(float(sys.argv[3])); budget.note('plan written', True)")
        ps = [subprocess.Popen([sys.executable, "-c", child, str(kp / "tools" / "route"), str(b), "3"],
                               stdout=subprocess.PIPE, text=True) for _ in range(3)]
        for p in ps:
            p.stdout.readline()
        mid = B.tally(led()["groups"]["fly"])["rounds"]
        for p in ps:
            p.wait()
        g = led()["groups"]["fly"]
        expect(mid == 1 and B.tally(g)["rounds"] == 1 and all(e.get("s") is not None for e in g["events"]),
               f"three runs in flight are one round ({mid} while running), each closed with its time at exit")
        run("fly", nF)
        expect(B.tally(led()["groups"]["fly"])["rounds"] == 2, "a run after they ended is round 2")

        print("7. time")
        run("slow", P(30, 31))
        L = led()
        L["groups"]["slow"]["events"][0]["s"] = 46 * 60
        (b / "routing" / "ledger.json").write_text(json.dumps(L))
        rc = refused(lambda: run("slow", P(30, 31)))
        expect(rc == 76, f"46 min used of tier A's 45: refused with a round still left (exit {rc})")
        dead = dict(t=time.time() - 3000, kind="run", s=None, limit=2700, pid=999999999, round=True)
        live = dict(dead, pid=os.getpid())
        expect(B.cost(dead, time.time()) == B.DEAD_S and B.cost(live, time.time()) == 2700,
               "a run that died without a word costs 10 min; one still running, its age up to its limit")

        print("8. the wall")
        rc1 = B.cmd_wall(b, "gA", "", ["placement: move J1"])
        rc2 = B.cmd_wall(b, "gA", "J1's row has four via sites for nine pins", [])
        rc3 = B.cmd_wall(b, "gA", "J1's row has four via sites for nine pins",
                         ["layer: a sixth layer", "placement: J1 0.8 mm south: nine sites", "rule: via gap 0.15 in the strip"])
        q = (b / "QUESTIONS.md").read_text()
        body = q[q.find("## Q4"):]
        order = [body.find("**placement**"), body.find("**rule**"), body.find("**layer**")]
        expect(rc1 == 2 and rc2 == 2 and rc3 == 0 and "## Q4 — WALL gA" in q and 0 < order[0] < order[1] < order[2]
               and "| when | what | time | how it ended |" in body and "did not converge" in body,
               "wall needs --blocks and a --change; writes Q4 from the ledger, the changes in the order of their cost", q)
        rc = refused(lambda: check("gA", nA))
        expect(rc == 76 and B.tally(led()["groups"]["gA"])["state"] == "wall", f"a walled group is refused (exit {rc})")
        (b / "handoff" / "07_answers.md").write_text("# Answers\n\n## Q4 — WALL gA\nTopic: J1 escape\n")
        rc = refused(lambda: check("gA", nA))
        expect(rc == 76, "an answer with no new revision does not reopen it")
        (b / "handoff" / "MANIFEST.json").write_text(json.dumps({"rev": 8}))
        run("gA", nA)
        g = led()["groups"]["gA"]
        expect(g["epoch"] == 2 and B.tally(g)["rounds"] == 1, "rev 8 answering Q4 reopens it: a new budget, round 1")
        rc = B.cmd_wall(b, "gA", "still four sites", ["swap: J1 pins 3 and 7"])
        q = (b / "QUESTIONS.md").read_text()
        expect(rc == 0 and "## Q5 — WALL gA, the SECOND -- for the user" in q, "its second wall is addressed to the user", q[-900:])
        (b / "board9.kicad_pcb").write_text(pcb_text(x=10.8))            # J1 moves: gA's part
        run("gA", nA)
        expect(led()["groups"]["gA"]["epoch"] == 3, "one of its parts moved: it reopens")
        rc = refused(lambda: run("fly", nF))
        expect(rc == 76 and led()["groups"]["fly"]["epoch"] == 1,
               "a spent group that asked no question is not reopened by the move: the wall comes first")

        print("9. the feasibility count")
        nC = P(40, 41)
        B.gate(b, "cnt", nC, kind="count").end("fits", ok=True)
        rc = refused(lambda: B.gate(b, "cnt", nC, kind="count"))
        (b / "board9.kicad_pcb").write_text(pcb_text(x=11.6))
        tok = B.gate(b, "cnt", nC, kind="count")
        tok.end("fits", ok=True)
        expect(rc == 76 and bool(tok), f"one count per placement and revision (second: exit {rc}); a moved part allows another")
        env = dict(os.environ, HW_BGWAIT_DIR=str(tmp / "jobs"), HW_BOARD="board9")
        for k in ("HW_MEM_FLOOR_GB", "HW_BUDGET_IN", "HW_BUDGET_JOB", "HW_BUDGET_BOARD"):
            env.pop(k, None)
        bg = lambda *a, timeout=60: subprocess.run([sys.executable, str(kp / "tools" / "bgwait.py"), *a],   # noqa: E731
                                                   capture_output=True, text=True, timeout=timeout, env=env, cwd=str(b))
        spec = b / "routing" / "long_spec.json"
        spec.write_text(json.dumps({"window": [0, 0, 1, 1], "nets": {"P50": [], "P51": []}}))
        t0 = time.time()
        r = bg("run", "--name", "cnt_long", "--group", str(spec), "--count", "--", sys.executable, "-c", "import time; time.sleep(60)")
        r2 = bg("wait", "cnt_long", "--timeout", "30")
        took = time.time() - t0
        e = next((x for x in led()["groups"].get("long_spec", {}).get("events", []) if x["kind"] == "count"), {})
        expect(r.returncode == 0 and took < 25 and "does not fit" in str(e.get("result")) and e.get("ok") is False,
               f"bgwait --count is ended at the limit ({took:.1f} s for a 60 s command, limit 3 s) and recorded 'it does not fit'",
               r.stdout + r.stderr + r2.stdout + r2.stderr + json.dumps(e))

        print("10. bgwait --group")
        jspec = b / "routing" / "job_spec.json"
        jspec.write_text(json.dumps({"window": [0, 0, 1, 1], "nets": {"P52": [], "P53": []}}))
        inner = ("import sys; sys.path.insert(0, sys.argv[1]); import budget; "
                 "budget.gate(sys.argv[2], 'job_spec', ['P52', 'P53'], kind='run', limit=60); budget.note('plan written', True)")
        r = bg("run", "--name", "j1", "--group", str(jspec), "--", sys.executable, "-c", inner, str(kp / "tools" / "route"), str(b))
        bg("wait", "j1", "--timeout", "30")
        g = led()["groups"].get("job_spec", {"events": []})
        kinds = sorted(x["kind"] for x in g["events"])
        job = next((x for x in g["events"] if x["kind"] == "job"), {})
        t = B.tally(g) if g["events"] else {}
        expect(r.returncode == 0 and kinds == ["job", "run"] and t.get("rounds") == 1 and job.get("s") is not None
               and str(job.get("result", "")).startswith("exit 0") and abs(t["seconds"] - job["s"]) < 0.01,
               f"a job with a router run inside is ONE round and its own time ({kinds}, {t.get('rounds')} round)",
               r.stdout + r.stderr + json.dumps(g, indent=1))
        r = bg("run", "--name", "j2", "--group", "hand", "--", sys.executable, "-c", "print(1)")
        expect(r.returncode == 76 and "BUDGET SPENT" in r.stderr and not (tmp / "jobs" / "j2.log").exists(),
               f"a job for a spent group is refused before it starts (exit {r.returncode})", r.stdout + r.stderr)
        r = bg("run", "--name", "j3", "--count", "--", sys.executable, "-c", "print(1)")
        expect(r.returncode == 2, f"--count without --group is a usage error (exit {r.returncode})")
        bg("run", "--name", "loose", "--", sys.executable, "-c", "import time; time.sleep(0.6)")
        bg("wait", "loose", "--timeout", "30")
        ug = led().get("ungrouped", [])
        expect(len(ug) == 1 and ug[0]["name"] == "loose", f"a job of no group is on the 'outside every group' line ({len(ug)})")

        print("11. a finished group")
        nD = P(55, 56)
        run("done", nD)
        plan = b / "routing" / "done_plan.json"
        plan.write_text(json.dumps({"group": "done", "tracks": [{"net": "/x/P55"}, {"net": "P56"}], "vias": []}))
        B.applied(copy, plan)
        n0 = B.tally(led()["groups"]["done"])["nets_applied"]
        B.applied(b, plan)
        t = B.tally(led()["groups"]["done"])
        ok1 = n0 == 0 and t["nets_applied"] == 2 and t["state"] == "applied" and t["complete"]
        run("done_rvfix", nD)
        g = led()["groups"]["done"]
        expect(ok1 and g["epoch"] == 2 and B.tally(g)["rounds"] == 1 and "done_rvfix" not in led()["groups"],
               "an apply on a copy is not copper; on the board the group is finished, and later work is a new budget")

        print("12. HW_BUDGET_IN")
        os.environ["HW_BUDGET_IN"] = "race"
        before = json.dumps(led(), sort_keys=True)
        tok = B.gate(b, "hand", nH, kind="check")
        os.environ.pop("HW_BUDGET_IN")
        expect(not tok and json.dumps(led(), sort_keys=True) == before, "a tool's own children are not counted and not refused")

        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            B.cmd_budget(b)
        print(buf.getvalue())
        expect("SPENT, and no question asked yet" in buf.getvalue() and "outside every group: 1" in buf.getvalue(),
               "the table names the spent groups and the time outside every group")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print(f"FAIL: {len(fails)} check(s); first: {fails[0]}")
        return 1
    print("PASS: tiers from the nets; a new name buys no budget; rounds, checks, time and the count are refused at "
          "their limits (exit 76); the wall writes the question and reopens on an answer or a move; bgwait --group "
          "charges a job once")
    return 0


if __name__ == "__main__":
    sys.exit(main())
