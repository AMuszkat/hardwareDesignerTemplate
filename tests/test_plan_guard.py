#!/usr/bin/env python3
"""A stale plan cannot be applied by mistake -- on tests/fixture_pcb.py's board, with pcbnew.

    python3 tests/test_plan_guard.py

What happened on a live board (board1's R2): a routing run ended in "no solution", and at its
output path lay a plan from an EARLIER run, routed before the spec's track widths were corrected.
Nothing told the two apart.  Now netgroup.py records where a plan came from (origin.py: the
spec's hash, the board file's hash, beside the plan), retires an older plan when it starts, and
`route.py check` / `apply` test the record before any gate.  This test holds them to it, on a
temp copy of the fixture board (netgroup, check, apply and rip are the real tools, run through
tools/kpy):

  fresh      a plan just routed checks: plan, spec and board all named, all unchanged; the plan
             file has the keys it always had (the record is a sidecar, not in the plan).
  spec       one width changed in the spec -> apply exits 5, names the spec AND the key
             ("width 0.2 -> 0.25") and the board; the board file is untouched; no manifest.
             `--accept-stale spec` passes the gate.
  failed     a run that ends without a plan (the changed spec, one iteration) leaves NO plan at
             the output path: the older one is <plan>.stale.json, byte for byte, which route.py
             refuses by name; renamed back (the old plan at the path, the incident itself) it is
             refused for its spec.  A run that then succeeds deletes the retired one.
  same       the spec restored but reformatted and its "why" reworded, the board file touched
             (a new time, the same content): not stale.  The plan is byte for byte the first one.
  board      one byte of the board changed -> apply exits 5, names the board as the reason and
             the spec as unchanged.
  edits      origin.judge alone: a hand-added "accept" key is not a change; a moved point is.
  siblings   a second plan routed on the same board and applied first: the first plan is NOT
             stale -- the board moved only by a recorded step, which the gate names -- and still
             not after that group is ripped; then it applies, and its own apply is recorded.
  no record  a plan with no origin record gets one line and passes the gate.

Exit 0 and a last line `PASS: ...`, exit 1 and `FAIL: ...`, or exit 0 and `SKIP: ...` when
pcbnew or kicad-cli is not on this machine.
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
TOOLS = ROOT / "kicad_project" / "tools"
KPY = str(TOOLS / "kpy")
sys.path.insert(0, str(TOOLS / "route"))
import origin  # noqa: E402

calls = []


def run(*cmd):
    """(exit code, stdout + stderr) of one tool, HW_MAZE_CORE as the caller set it."""
    t = time.time()
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    calls.append((Path(str(cmd[1])).name, round(time.time() - t, 1)))
    return r.returncode, r.stdout + r.stderr


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Failed(Exception):
    pass


def need(cond, what, out=""):
    if not cond:
        tail = "\n".join(ln for ln in out.splitlines() if "Debug:" not in ln and "property.h" not in ln)[-1500:]
        raise Failed(what + (("\n--- output ---\n" + tail) if tail else ""))


def main():
    rc, out = run(KPY, "-c", "import pcbnew; print(pcbnew.Version())")
    if rc:
        print("SKIP: pcbnew is not importable by kicad_project/tools/kpy (KiCad is not installed here)")
        return 0
    if not shutil.which("kicad-cli"):
        print("SKIP: kicad-cli is not on PATH (route.py check runs KiCad's DRC)")
        return 0
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="plan-guard-"))
    try:
        return body(tmp, t0)
    except Failed as e:
        print(f"FAIL: {e}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def body(tmp, t0):
    rc, out = run(KPY, ROOT / "tests" / "fixture_pcb.py", tmp, "--nets", "4")
    need(rc == 0, "fixture_pcb.py failed", out)
    B = tmp / "fixture"
    pcb = B / "fixture.kicad_pcb"
    spec, plan = B / "routing" / "R1_spec.json", B / "routing" / "R1_plan.json"
    rec, stale = origin.sidecar(plan), origin.stale_path(plan)
    man, ledger = B / "routing" / "R1.applied.json", B / "routing" / origin.LEDGER
    spec0 = spec.read_text()
    netgroup = lambda *a: run(KPY, TOOLS / "route" / "netgroup.py", "--board", B, *a)
    route = lambda *a: run(KPY, TOOLS / "route" / "route.py", a[0], "--board", B, *a[1:])

    # fresh
    rc, out = netgroup("routing/R1_spec.json")
    need(rc == 0 and plan.exists() and rec.exists(), "netgroup wrote no plan or no origin record", out)
    plan0, board0 = plan.read_bytes(), pcb.read_bytes()
    sha0 = hashlib.sha256(plan0).hexdigest()
    need(sorted(json.loads(plan0)) == ["group", "necks", "tracks", "vias", "why", "zones"],
         f"the plan file's keys changed: {sorted(json.loads(plan0))}")
    rc, out = route("check", "routing/R1_plan.json")
    need(rc == 0 and "CHECK PASS" in out, "a fresh plan does not check", out)
    need("spec   routing/R1_spec.json: unchanged" in out and "board  fixture.kicad_pcb: unchanged" in out,
         "check does not name the spec and the board as unchanged", out)

    # spec: one width
    S = json.loads(spec0)
    S["width"] = 0.25
    spec.write_text(json.dumps(S, indent=1) + "\n")
    rc, out = route("apply", "routing/R1_plan.json")
    need(rc == 5, f"apply of a plan whose spec changed: exit {rc}, not 5", out)
    need("STALE PLAN R1_plan.json" in out and "R1_spec.json CHANGED" in out and "width 0.2 -> 0.25" in out,
         "the refusal does not name the spec and what changed in it", out)
    need("board  fixture.kicad_pcb: unchanged" in out, "the refusal does not name the board", out)
    need("--accept-stale spec" in out, "the refusal does not name the override", out)
    need(pcb.read_bytes() == board0 and not man.exists() and not ledger.exists(),
         "a refused apply touched the board, wrote a manifest or recorded a step")
    rc, out = route("check", "routing/R1_plan.json", "--accept-stale", "spec")
    need(rc == 0 and "on your word" in out and "CHECK PASS" in out, "--accept-stale spec does not pass the gate", out)

    # failed: a run without a plan leaves none at the path
    rc, out = netgroup("routing/R1_spec.json", "--iters", "1")
    need(rc != 0, "the one-iteration run was meant to fail and did not", out)
    need(not plan.exists(), f"a run that ended without a plan (exit {rc}) left {plan.name} at its output path", out)
    need(not rec.exists(), "the retired plan's origin record is still at the plan's path")
    need(stale.exists() and stale.read_bytes() == plan0, "the older plan was not moved aside byte for byte", out)
    need("retired R1_plan.json -> R1_plan.stale.json" in out and f"no plan at {plan}" in out,
         "the run does not say it retired the older plan and left none", out)
    fail_rc = rc
    rc, out = route("apply", "routing/R1_plan.json")
    need(rc not in (0, 5) and "no such plan" in out, f"apply of the missing plan: exit {rc}", out)
    rc, out = route("apply", "routing/R1_plan.stale.json")
    need(rc == 5 and "retired" in out and "R1_spec.json CHANGED" in out,
         f"apply of the retired plan: exit {rc}, or it does not say retired and spec", out)
    os.replace(stale, plan)                      # the incident itself: the old plan at the path
    os.replace(origin.sidecar(stale), rec)
    rc, out = route("apply", "routing/R1_plan.json")
    need(rc == 5 and "width 0.2 -> 0.25" in out, f"the old plan put back at the path: exit {rc}, not 5", out)
    need(pcb.read_bytes() == board0, "a refused apply touched the board")
    rc, out = netgroup("routing/R1_spec.json", "--iters", "1")
    need(rc != 0 and stale.exists() and not plan.exists(), "a second failed run did not retire the plan again", out)
    rc, out = netgroup("routing/R1_spec.json")
    need(rc == 0 and plan.exists() and not stale.exists() and not origin.sidecar(stale).exists(),
         "a run that wrote its plan left the retired one behind", out)
    need("removed R1_plan.stale.json" in out, "the run does not say it deleted the retired plan", out)

    # same: reformatted spec, reworded why, touched board
    S = json.loads(spec0)
    S["why"] = "reworded: " + S["why"]
    spec.write_text(spec0)
    rc, out = netgroup("routing/R1_spec.json")
    need(rc == 0 and plan.read_bytes() == plan0, "the same spec on the same board did not give the same plan bytes", out)
    spec.write_text(json.dumps(dict(sorted(S.items(), reverse=True)), indent=4))
    os.utime(pcb, (time.time() + 5, time.time() + 5))
    rc, out = route("check", "routing/R1_plan.json")
    need(rc == 0 and "STALE" not in out and "spec   routing/R1_spec.json: unchanged" in out
         and "board  fixture.kicad_pcb: unchanged" in out,
         "a reformatted spec or a touched board made the plan stale", out)

    # board: one byte of content
    pcb.write_bytes(board0 + b"\n")
    rc, out = route("apply", "routing/R1_plan.json")
    need(rc == 5, f"apply on a changed board: exit {rc}, not 5", out)
    need("fixture.kicad_pcb CHANGED" in out and "spec   routing/R1_spec.json: unchanged" in out
         and "--accept-stale board" in out, "the refusal does not name the board as changed and the spec as not", out)
    need(pcb.read_bytes() == board0 + b"\n" and not man.exists(), "a refused apply touched the board")
    pcb.write_bytes(board0)

    # edits: judged without a board tool
    v = origin.judge(plan, B, pcb)
    need(not v["stale"], f"the restored board is stale: {v['stale']}")
    P = json.loads(plan0)
    plan.write_text(json.dumps(dict(P, accept={}), indent=1) + "\n")
    need(not origin.judge(plan, B, pcb)["stale"], "a hand-added \"accept\" key made the plan stale")
    P["tracks"][0]["pts"][0][0] += 0.2
    plan.write_text(json.dumps(P, indent=1) + "\n")
    v = origin.judge(plan, B, pcb)
    need(list(v["stale"]) == ["plan"], f"a moved point: stale {list(v['stale'])}, not ['plan']")
    plan.write_bytes(plan0)

    # siblings: another plan from the same board, applied first
    S2 = json.loads(spec0)
    keep = sorted(S2["nets"])[:1]
    S2.update(group="R1s", plan="routing/R1s_plan.json", nets={n: S2["nets"][n] for n in keep})
    (B / "routing" / "R1s_spec.json").write_text(json.dumps(S2, indent=1) + "\n")
    rc, out = netgroup("routing/R1s_spec.json")
    need(rc == 0, "the sibling spec did not route", out)
    rc, out = route("apply", "routing/R1s_plan.json")
    need(rc == 0 and "applied R1s" in out, "the sibling plan did not apply", out)
    need(len(origin.steps(B)) == 1 and origin.steps(B)[0]["before"] == hashlib.sha256(board0).hexdigest()
         and origin.steps(B)[0]["after"] == sha(pcb), "apply did not record the board's step")
    rc, out = route("check", "routing/R1_plan.json")
    need(rc != 5 and "STALE" not in out and "only by recorded steps -- apply R1s" in out,
         f"a plan routed before a sibling was applied is called stale (exit {rc})", out)
    sib_rc = rc
    rc, out = route("rip", "R1s")
    need(rc == 0, "rip of the sibling failed", out)
    rc, out = route("apply", "routing/R1_plan.json")
    need(rc == 0 and "applied R1:" in out and "STALE" not in out, f"the plan did not apply after the sibling's rip (exit {rc})", out)
    need(man.exists() and origin.steps(B)[-1]["op"] == "apply" and origin.steps(B)[-1]["after"] == sha(pcb),
         "apply left no manifest or no step")
    nsteps = len(origin.steps(B))
    v = origin.judge(plan, B, pcb)
    need(not v["stale"] and any("only by recorded steps" in ln for ln in v["lines"]),
         f"after its own apply the plan is stale: {v['stale']}")

    # no record
    hand = B / "routing" / "H1_plan.json"
    hand.write_text(json.dumps(dict(json.loads(plan0), group="H1"), indent=1) + "\n")
    rc, out = route("check", "routing/H1_plan.json")
    need(rc != 5 and "has no origin record" in out, f"a plan with no origin record: exit {rc}, or no line saying so", out)

    tools_s = sum(s for _n, s in calls)
    print(f"PASS: fresh plan checks (sha {sha0[:12]}, the same after a reformatted spec and a touched board); "
          f"spec width changed -> apply exit 5; board changed -> apply exit 5; --accept-stale passes; "
          f"failed run (exit {fail_rc}) left no plan and a .stale.json route.py refuses; sibling applied first "
          f"-> not stale (check exit {sib_rc}), applied after its rip, {nsteps} recorded steps; no record -> one "
          f"line; {len(calls)} tool runs, {round(time.time() - t0)} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
