#!/usr/bin/env python3
"""
kicad_project/tools/route/race.py against the same variants run one after
another by hand, on the PCB fixture (tests/fixture_pcb.py), in a temp folder.

    python3 tests/test_race.py

Three variants of the fixture group's spec -- the spec as generated, the same
with a wider clearance, the same with `--seed 3` -- are
  * run SERIALLY, the way a session does it without the tool: netgroup, then
    `route.py check`, one variant after the other on one board copy;
  * RACED: `race.py` with the three (and a fourth, a spec that cannot run, to
    see a failed variant become a row and not the race's end).

What must hold:
  1. every variant's plan from the race is byte-identical to the serial one
     (same sha256): a scratch copy and a neighbour process change nothing;
  2. the race names the same winner the serial results give by the stated
     order (converged, new DRC, rule, vias, length);
  3. the race takes less wall-clock than the serial runs (needs 4 cores;
     reported, not judged, on a smaller machine).  Both times are printed;
  4. the real board folder is untouched: every file's sha before = after;
  5. ONE table, a WINNER line, the commands that take the winner (its plan
     and the plan's origin record) to the real board, exit 0; the broken variant is a `NO` row with its reason.

SKIP (exit 0) without pcbnew or kicad-cli.  The last line is PASS / FAIL /
SKIP.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KP = ROOT / "kicad_project"
TOOLS = KP / "tools"
KPY = TOOLS / "kpy"
sys.path.insert(0, str(TOOLS / "route"))


def run(cmd, cwd=KP, env=None, timeout=1800):
    t = time.monotonic()
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout + r.stderr, time.monotonic() - t, r.stdout


def folder_digest(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(folder).rglob("*")) if p.is_file()}


def main():
    rc, out, _, _ = run([KPY, "-c", "import pcbnew; print(pcbnew.Version())"])
    if rc != 0:
        print("SKIP: pcbnew is not importable through kicad_project/tools/kpy")
        return 0
    if not shutil.which("kicad-cli"):
        print("SKIP: kicad-cli is not on PATH (route.py check needs it)")
        return 0
    import race                                    # plain Python: the ranking and the plan measure

    tmp = Path(tempfile.mkdtemp(prefix="test_race_"))
    fails = []
    try:
        rc, out, _, _ = run([KPY, ROOT / "tests" / "fixture_pcb.py", tmp, "--nets", "8"])
        board = tmp / "fixture"
        spec = board / "routing" / "R1_spec.json"
        if rc != 0 or not spec.exists():
            print("FAIL: tests/fixture_pcb.py did not write the fixture: " + out.strip()[-300:])
            return 1
        S = json.loads(spec.read_text())
        (board / "routing" / "R1_wide.json").write_text(json.dumps(dict(S, clearance=0.25), indent=1) + "\n")
        (board / "routing" / "R1_offgrid.json").write_text(json.dumps(dict(S, pitch=0.25), indent=1) + "\n")
        variants = [("routing/R1_spec.json", []), ("routing/R1_wide.json", []), ("routing/R1_spec.json", ["--seed", "3"])]
        plan_rel = S["plan"]

        # ---- serial, by hand: one copy, one variant after the other
        serial_board = tmp / "serial" / "fixture"
        shutil.copytree(board, serial_board)
        serial, t0 = [], time.monotonic()
        for k, (sp, opts) in enumerate(variants):
            rc, out, dt, _ = run([KPY, TOOLS / "route" / "netgroup.py", "--board", serial_board, sp,
                                  "--timeout", "600", *opts])
            plan = serial_board / plan_rel
            if rc != 0 or not plan.exists():
                print(f"FAIL: the serial run of {sp} {' '.join(opts)} did not converge: " + out.strip()[-300:])
                return 1
            length, vias, sha = race.measure_plan(plan)
            rc, out, dt2, _ = run([KPY, TOOLS / "route" / "route.py", "check", "--board", serial_board, plan_rel])
            verdict, new, rule = race.read_check(out, rc)
            serial.append(dict(order=k, converged=True, check=verdict, new_drc=new, rule=rule, vias=vias,
                               length_mm=length, plan_sha=sha, route_s=dt, check_s=dt2))
            print(f"serial  {sp} {' '.join(opts):9s} route {dt:5.1f} s, check {dt2:5.1f} s: CHECK {verdict}, "
                  f"{vias} vias, {length:.2f} mm, plan {sha[:12]}")
        serial_s = time.monotonic() - t0
        serial_win = race.winner(serial)["order"]

        # ---- raced
        before = folder_digest(board)
        args = [sp + (" " + " ".join(opts) if opts else "") for sp, opts in variants] + ["routing/R1_offgrid.json"]
        t0 = time.monotonic()
        rc, out, _, stdout = run([KPY, TOOLS / "route" / "race.py", "--board", board, *args,
                                  "--timeout", "600", "--out", tmp / "race"])
        race_s = time.monotonic() - t0
        print(stdout.rstrip())
        after = folder_digest(board)
        try:
            R = json.loads((tmp / "race" / "race.json").read_text())
        except (OSError, ValueError) as e:
            print(f"FAIL: race.py left no race.json ({e}); exit {rc}: " + out.strip()[-400:])
            return 1
        rows = R["results"]

        if rc != 0:
            fails.append(f"race.py exit {rc}, want 0 (the winner converged and passed)")
        if len(rows) != 4:
            fails.append(f"{len(rows)} result rows, want 4")
        for k in range(3):
            if not rows[k]["converged"]:
                fails.append(f"variant {rows[k]['variant']} did not converge in the race: {rows[k]['state']}")
            elif rows[k]["plan_sha"] != serial[k]["plan_sha"]:
                fails.append(f"variant {rows[k]['variant']}: plan sha {rows[k]['plan_sha'][:12]} raced, "
                             f"{serial[k]['plan_sha'][:12]} serial")
            elif race.measure_plan(rows[k]["plan"])[2] != serial[k]["plan_sha"]:
                fails.append(f"variant {rows[k]['variant']}: the plan race.py kept is not the plan it measured")
            if (rows[k]["check"], rows[k]["new_drc"], rows[k]["rule"]) != \
                    (serial[k]["check"], serial[k]["new_drc"], serial[k]["rule"]):
                fails.append(f"variant {rows[k]['variant']}: check {rows[k]['check']} ({rows[k]['new_drc']}, "
                             f"{rows[k]['rule']}) raced, {serial[k]['check']} ({serial[k]['new_drc']}, "
                             f"{serial[k]['rule']}) serial")
        if len(rows) == 4 and (rows[3]["converged"] or "grid" not in rows[3]["state"]):
            fails.append(f"the off-grid variant is not a failed row with its reason: {rows[3]['state']!r}")
        if len({r["plan_sha"] for r in rows[:3]}) != 3:
            fails.append("the three variants did not give three different plans: the comparison proves nothing")
        win = next((r["order"] for r in rows if r["variant"] == R["winner"]), None)
        if win != serial_win:
            fails.append(f"winner: variant {win} raced, variant {serial_win} from the serial results")
        if f"WINNER: {R['winner']} -- CHECK PASS" not in stdout:
            fails.append("no `WINNER: <variant> -- CHECK PASS` line")
        if stdout.count("variant ") != 1 or stdout.count("\nWINNER:") != 1:
            fails.append("not exactly one table and one WINNER line on stdout")
        for need in (f"cp {tmp / 'race' / R['winner'] / 'plan.json'}", f"cp {tmp / 'race' / R['winner'] / 'plan.origin.json'}",
                     "route.py check --board", "route.py apply --board"):
            if need not in stdout:
                fails.append(f"the commands to apply the winner lack `{need}`")
        if before != after:
            changed = sorted(set(before.items()) ^ set(after.items()))
            fails.append(f"the real board folder changed: {sorted({c[0] for c in changed})[:5]}")
        # the printed commands, on a copy: the winner's plan arrives WITH its origin record and checks as fresh
        taken = tmp / "taken" / "fixture"
        shutil.copytree(board, taken)
        wdir = tmp / "race" / R["winner"]
        if (wdir / "plan.origin.json").exists():
            shutil.copyfile(wdir / "plan.json", taken / plan_rel)
            shutil.copyfile(wdir / "plan.origin.json", (taken / plan_rel).with_suffix(".origin.json"))
            rc, out, _, _ = run([KPY, TOOLS / "route" / "route.py", "check", "--board", taken, plan_rel])
            if rc != 0 or "no origin record" in out or "STALE" in out.upper():
                fails.append(f"the winner taken to the board with its origin record does not check as a fresh plan "
                             f"(exit {rc}): " + out.strip()[-200:])
        else:
            fails.append("the winner has no plan.origin.json beside its plan.json")
        if any((tmp / "race" / r["variant"] / "fixture").exists() for r in rows):
            fails.append("a variant's board copy was left behind without --keep")
        cores = os.cpu_count() or 1
        if race_s >= serial_s and cores >= 4:
            fails.append(f"no time saved: raced {race_s:.1f} s, serial {serial_s:.1f} s on {cores} cores")
        for f in fails:
            print("  BAD " + f)
        if fails:
            print(f"FAIL: {len(fails)} check(s); first: {fails[0]}")
            return 1
        print(f"PASS: 3 variants raced in {race_s:.1f} s against {serial_s:.1f} s serially ({serial_s / race_s:.1f}x, "
              f"{R['jobs']} at a time on {cores} cores): same winner ({R['winner']}), same 3 plan sha, real board "
              f"untouched, a failed 4th variant reported as a row")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
