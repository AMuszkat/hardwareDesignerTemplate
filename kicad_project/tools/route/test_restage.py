#!/usr/bin/env python3
"""test_restage.py -- restage.py without a router: a stub runner replays a list of (log, exit code).

    python3 tools/route/test_restage.py            every test below, exit 0 when all pass
    python3 tools/route/test_restage.py --replay SPEC LOG EXIT [pull|push]
                                                   a real log of your board through the parser and one move
                                                   (prints what restage.py --dry prints, plus the invariants)

The logs here are written in the router's own words (maze.Grid.blocked_lines, netgroup.py's closing
lines); a board keeps a replay of its REAL logs beside its job script, because the first thing that
breaks this tool is a report text that changed.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import restage  # noqa: E402

STUB = r'''
import shutil, sys
d, spec, tmo = sys.argv[1:4]
try: n = int(open(d + "/n").read())
except OSError: n = 0
n += 1; open(d + "/n", "w").write(str(n))
shutil.copyfile(spec, d + "/%d.spec.seen" % n); open(d + "/%d.timeout" % n, "w").write(tmo)
sys.stdout.write(open(d + "/%d.log" % n).read())
sys.exit(int(open(d + "/%d.exit" % n).read()))
'''


def spec_of(stages):
    """stages: [[net or (net, leg index)], ...] -> a spec in the router's form (legs with a stage)."""
    nets = {}
    for s, names in enumerate(stages):
        for x in names:
            n = x[0] if isinstance(x, tuple) else x
            nets.setdefault(n, {"src": [0, 0], "legs": []})["legs"].append({"to_xy": [1, s], "stage": s})
    return {"group": "G", "plan": "routing/G.plan.json", "nets": nets}


def head(stage, nets, iters=3):
    out = [f"\n--- leg {stage + 1}: {' '.join(nets)}"]
    for i in range(1, iters + 1):
        out.append(f"   iteration {i}: pres {0.5 * i:.2f}, 2 nets overlap on 9 cells")
    return out


def blk(net, end, wallers, rows=(), at=(1.0, 2.0), pre=False, more=0):
    out = [f"BLOCKED {end.upper()}: {net} is shut in at its {end}, near ({at[0]:.3f}, {at[1]:.3f}) mm"]
    out.append("   from its goal it reaches 12 cell(s) (12 on In1.Cu) and never its source" if end == "goal"
               else "   from its source it reaches 12 cell(s) (12 on In1.Cu) and none of its 1 goal cell(s)")
    out.append("   what closes it, nearest first (distance from the goal; cells of the wall):")
    for d, layer, what, cells in rows:
        out.append("      %6.3f mm  %-7s %s  -- %d cell(s), nearest at (%.3f, %.3f)" % (d, layer, what, cells, at[0], at[1]))
    if more:
        out.append(f"      ... and {more} more")
    if wallers:
        out.append("   walled by %s's fixed copper (prelaid, or an earlier leg of this run): if it is a leg, give %s's "
                   "leg an earlier \"stage\" (or theirs a later one) so the two are negotiated together, or reorder "
                   "its legs" % (", ".join(sorted(wallers)), net))
    out.append("   no layer change inside it (40 cell(s) without a legal via), because of:")
    out.append("       0.450 mm  via     the spec's \"block\" rectangle [1, 2, 3, 4]  -- 40 cell(s)")
    out.append(f"   a blocked {'end' if end == 'goal' else 'start'} point, not congestion: more iterations do not open it")
    out.append(f"BLOCKED at this leg: {net}" + (" -- nothing was negotiated" if pre else ""))
    out.append("wrote /x/G.plan.partial.json (the legs that closed, and the diagnosis)\nno plan at /x/G.plan.json")
    return out


def stall(kind, nets):
    out = [f"   {n}: 7 contested cell(s): In1.Cu(1.000, 2.000)" for n in nets]
    out += [f"   {n} reaches 100+ cells; 1+ of 1 goal cells" for n in nets]
    if kind == "plateau":
        out.append("PLATEAU at this leg: best 7 contested cell(s) at iteration 2; the last 40 iterations' best 7 is "
                   "not 2 % below it (--plateau 40; --plateau 0 runs to --iters / --timeout)")
    elif kind == "stuck":
        out = ["STUCK at iteration 3: the same 7 cell(s) have been contested for 20 iterations",
               "   nets: " + ", ".join(f"{n} (7 cell(s))" for n in nets),
               "   In1.Cu: 7 cell(s) in x 1.000 .. 2.000, y 1.000 .. 2.000 mm",
               "STUCK at this leg (--stall 20; --stall 0 runs to --iters)"]
    else:
        out.append("STOPPED at the timeout")
    out.append("wrote /x/G.plan.partial.json (the last iteration, overlaps and all)\nno plan at /x/G.plan.json")
    return out


def invariants(before, after, moved=()):
    """mkq-style: stages contiguous, every net keeps exactly its legs, leg order kept, net order kept,
    and only the legs named in `moved` changed their place relative to the others."""
    mb, ma = restage.stage_map(before), restage.stage_map(after)
    assert not restage.check_map(ma), restage.check_map(ma)
    assert list(before["nets"]) == list(after["nets"]), "net order changed"
    assert set(mb) == set(ma), "a leg was lost or added"
    for n in before["nets"]:
        for lb, la in zip(before["nets"][n]["legs"], after["nets"][n]["legs"]):
            assert {k: v for k, v in lb.items() if k != "stage"} == {k: v for k, v in la.items() if k != "stage"}, n
    keep = [k for k in mb if k not in moved]
    for x in keep:
        for y in keep:
            assert (mb[x] < mb[y]) == (ma[x] < ma[y]) and (mb[x] == mb[y]) == (ma[x] == ma[y]), (x, y)
    assert {k: v for k, v in before.items() if k != "nets"} == {k: v for k, v in after.items() if k != "nets"}


def loop(spec, seq, policy="pull", extra=(), tmp=None, cont=False):
    """Run restage.py's loop on a stub that replays seq = [(log lines, exit code), ...]."""
    tmp = tmp or tempfile.mkdtemp(prefix="restage_test_")
    sd, wd = os.path.join(tmp, "seq"), os.path.join(tmp, "work")
    os.makedirs(sd, exist_ok=True)
    open(os.path.join(tmp, "stub.py"), "w").write(STUB)
    json.dump(spec, open(os.path.join(tmp, "spec.json"), "w"))
    base = len([f for f in os.listdir(sd) if f.endswith(".exit")])
    for i, (lines, code) in enumerate(seq, base + 1):
        open(os.path.join(sd, f"{i}.log"), "w").write("\n".join(lines) + "\n")
        open(os.path.join(sd, f"{i}.exit"), "w").write(str(code))
    argv = [sys.executable, os.path.join(HERE, "restage.py"), "--spec", os.path.join(tmp, "spec.json"), "--workdir", wd,
            "--deadline", str(time.time() + 600), "--policy", policy, "--iter-cap", "77", "--min-run", "1",
            *(["--continue"] if cont else []), *extra,
            "--", sys.executable, os.path.join(tmp, "stub.py"), sd, "{spec}", "{timeout}"]
    r = subprocess.run(argv, capture_output=True, text=True)
    st = json.load(open(os.path.join(wd, "restage.json"))) if os.path.exists(os.path.join(wd, "restage.json")) else {}
    return r.returncode, st, wd, tmp, r.stdout + r.stderr


def smap(path):
    m = restage.stage_map(json.load(open(path)))
    return {(n if k == 0 else f"{n}#{k + 1}"): s for (n, k), s in m.items()}


# --------------------------------------------------------------------------------------------- the tests
def t_parse_and_moves():
    S = spec_of([["A", "B"], ["X", "C", "D"], ["E"]])
    log = head(0, ["A", "B"]) + head(1, ["X", "C", "D"]) + blk(
        "X", "goal", ["A", "B", "C"], rows=[(0.1, "In1.Cu", "A's own copper", 4), (0.2, "In1.Cu", "via of GND at (1.000, 2.000), on the board", 2),
                                           (0.3, "In2.Cu", "track of Z on In2.Cu, (1.000, 2.000) to (3.000, 4.000), reserved by the spec's reserve_tracks", 5)], more=2)
    rep = restage.parse_log("\n".join(log), 4)
    assert (rep["stage"], rep["state"]) == (1, "blocked"), rep
    b = rep["blocked"][0]
    assert (b["net"], b["end"], b["at"], b["more"]) == ("X", "goal", [1.0, 2.0], 2), b
    assert b["wallers"] == ["A", "B", "C"] and len(b["walls"]) == 4, b
    key, walls, target = restage.classify(S, restage.stage_map(S), b, 1)
    kinds = {w: k for w, k, s in walls}
    assert key == ("X", 0) and target == 0 and kinds["A"] == "leg" and kinds["B"] == "leg" and kinds["C"] == "reserve", walls
    assert sorted(k for w, k, s in walls if w not in "ABC") == ["board", "spec", "spec"], walls
    new, cells, recs = restage.decide(S, rep, "pull", [], [], None)
    invariants(S, new, moved=[("X", 0)])
    assert restage.stage_map(new)[("X", 0)] == 0 and restage.stage_map(new)[("C", 0)] == 1 and recs[0]["policy"] == "pull"
    new, cells, recs = restage.decide(S, rep, "push", [], [], None)
    invariants(S, new, moved=[("X", 0)])
    m = restage.stage_map(new)
    assert (m[("X", 0)], m[("A", 0)], m[("C", 0)], m[("E", 0)]) == (0, 1, 2, 3), m


def t_source_before_negotiation():
    S = spec_of([["A"], ["B"], ["X", "C"]])
    log = head(0, ["A"]) + head(1, ["B"]) + ["\n--- leg 3: X C"] + blk("X", "source", ["B"], pre=True)
    rep = restage.parse_log("\n".join(log), 4)
    assert (rep["stage"], rep["state"], rep["blocked"][0]["end"]) == (2, "blocked", "source"), rep
    new, cells, recs = restage.decide(S, rep, "pull", [], [], None)
    assert restage.stage_map(new)[("X", 0)] == 1 and "source" in cells[0][0]
    invariants(S, new, moved=[("X", 0)])


def t_pull_empties_a_stage():
    S = spec_of([["A"], ["X"], ["C"]])
    rep = restage.parse_log("\n".join(head(1, ["X"]) + blk("X", "goal", ["A"])), 4)
    new, _, _ = restage.decide(S, rep, "pull", [], [], None)
    assert restage.stage_map(new) == {("A", 0): 0, ("X", 0): 0, ("C", 0): 1}
    invariants(S, new, moved=[("X", 0)])


def t_plateau_exit6():
    S = spec_of([["A", "B"], ["C"]])
    for kind in ("plateau", "stuck", "stopped"):
        code, st, wd, tmp, out = loop(S, [(head(0, ["A", "B"]) + stall(kind, ["A", "B"]), 3 if kind == "stuck" else 1)])
        assert code == 6 and "stage 0" in st["last"] and "A B" in st["last"], (kind, code, out)
        assert open(os.path.join(wd, "RESTAGE.md")).read().rstrip().splitlines()[-1].startswith("RESULT exit 6"), kind


def t_closes_on_iteration_3():
    S = spec_of([["A", "B"], ["X", "Y"], ["C"]])
    seq = [(head(0, ["A", "B"]) + head(1, ["X", "Y"]) + blk("X", "goal", ["A"]), 4),
           (head(0, ["A", "B", "X"]) + head(1, ["Y"]) + blk("Y", "source", ["B", "X"], pre=True), 4),
           (head(0, ["A", "B", "X", "Y"]) + head(1, ["C"]) + ["wrote /x/G.plan.json"], 0)]
    push = [seq[0], (head(0, ["X"]) + head(1, ["A", "B"]) + ["\n--- leg 3: Y"] + blk("Y", "source", ["B", "X"], pre=True), 4), seq[2]]
    for policy, final in (("pull", {"A": 0, "B": 0, "X": 0, "Y": 0, "C": 1}), ("push", {"Y": 0, "X": 1, "A": 2, "B": 2, "C": 3})):
        seq = push if policy == "push" else seq
        code, st, wd, tmp, out = loop(S, seq, policy, extra=["--live", os.path.join(tempfile.mkdtemp(), "live.json")])
        assert code == 0 and st["iter"] == 3 and len(st["moves"]) == 2, (policy, code, out)
        assert smap(os.path.join(wd, "final.spec.json")) == final, (policy, smap(os.path.join(wd, "final.spec.json")))
        specs = [json.load(open(os.path.join(wd, "iter%02d.spec.json" % i))) for i in (1, 2, 3)]
        invariants(specs[0], specs[1], moved=[("X", 0)])
        invariants(specs[1], specs[2], moved=[("Y", 0)])
        seen = json.load(open(os.path.join(tmp, "seq", "3.spec.seen")))            # the stub got --live's file
        assert seen == specs[2] and open(os.path.join(tmp, "seq", "1.timeout")).read() == "77"
        md = open(os.path.join(wd, "RESTAGE.md")).read()
        assert md.count("\n| ") == 4 and "RESULT exit 0" in md, md


def t_cycle_exit7():
    S = spec_of([["B"], ["A"]])
    seq = [(head(0, ["B"]) + head(1, ["A"]) + blk("A", "goal", ["B"]), 4),
           (head(0, ["A"]) + head(1, ["B"]) + blk("B", "goal", ["A"]), 4)]
    code, st, wd, tmp, out = loop(S, seq, "push")
    assert code == 7 and st["iter"] == 2 and "cycle" in st["last"] and "iteration 1" in st["last"], (code, out)


def t_reserves_only_exit5():
    S = spec_of([["A"], ["X", "C"], ["D"]])
    seq = [(head(0, ["A"]) + head(1, ["X", "C"]) + blk("X", "goal", ["C", "D"], rows=[(0.1, "In1.Cu", "via of GND at (1.000, 2.000), on the board (in field U9)", 3)]), 4)]
    code, st, wd, tmp, out = loop(S, seq)
    assert code == 5 and "reserves C D" in st["last"] and "[board]" in st["last"] and "(1.000, 2.000)" in st["last"], (code, out)
    S0 = spec_of([["X", "C"]])                                                        # stage 0 has no earlier stage
    code, st, wd, tmp, out = loop(S0, [(head(0, ["X", "C"]) + blk("X", "source", [], pre=True), 4)])
    assert code == 5 and "no stage opens it" in st["last"], (code, out)


def t_leg_order():
    S = spec_of([["M", "A"], ["B"], [("M", 1), "C"]])
    rep = restage.parse_log("\n".join(head(2, ["M", "C"]) + blk("M", "goal", ["B"])), 4)
    new, _, recs = restage.decide(S, rep, "pull", [], [], None)
    assert restage.stage_map(new)[("M", 1)] == 1 and recs[0]["leg"] == 1
    invariants(S, new, moved=[("M", 1)])
    rep = restage.parse_log("\n".join(head(2, ["M", "C"]) + blk("M", "goal", ["A"])), 4)
    for policy in ("pull", "push"):
        try:
            restage.decide(S, rep, policy, [], [], None)
            raise AssertionError("a leg passed its predecessor")
        except restage.Stop as e:
            assert e.code == 5 and "keep their order" in e.text, e.text


def t_stall_after_pull_becomes_push():
    S = spec_of([["A", "B"], ["X"], ["C"]])
    seq = [(head(0, ["A", "B"]) + head(1, ["X"]) + blk("X", "goal", ["A"]), 4),
           (head(0, ["A", "B", "X"]) + stall("plateau", ["A", "X"]), 1),
           (head(0, ["X"]) + head(1, ["A", "B"]) + stall("plateau", ["A", "B"]), 1)]
    code, st, wd, tmp, out = loop(S, seq)
    assert code == 6 and st["iter"] == 3 and st["pushed"] == ["X"], (code, out)
    assert smap(os.path.join(wd, "iter03.spec.json")) == {"X": 0, "A": 1, "B": 1, "C": 2}
    assert "undo iteration 1's pull" in st["records"][1]["move"], st["records"][1]
    code, st, wd, tmp, out = loop(S, seq[:2], "push")                               # under push a stall is the end
    assert code == 6 and st["iter"] == 2, (code, out)


def t_refused_exit8():
    S = spec_of([["A"], ["B"]])
    code, st, wd, tmp, out = loop(S, [(["BUDGET SPENT -- G: 1 of 1 round(s); this run refused."], 76)])
    assert code == 8 and "refused" in st["last"], (code, out)
    code, st, wd, tmp, out = loop(S, [(["Traceback (most recent call last):", "KeyError: 'x'"], 1)])
    assert code == 8, (code, out)


def t_max_iter_and_continue():
    S = spec_of([["A"], ["X"]])
    seq = [(head(0, ["A"]) + head(1, ["X"]) + blk("X", "goal", ["A"]), 4)]
    code, st, wd, tmp, out = loop(S, seq, extra=["--max-iter", "1"])
    assert code == 9 and smap(os.path.join(wd, "next.spec.json")) == {"A": 0, "X": 0}, (code, out)
    code, st, wd, tmp, out = loop(S, [(head(0, ["A", "X"]) + ["wrote /x/G.plan.json"], 0)], tmp=tmp, cont=True)
    assert code == 0 and st["iter"] == 2 and len(st["moves"]) == 1, (code, out)
    code, st, wd, tmp, out = loop(S, [], extra=["--deadline", str(time.time() + 5), "--min-run", "60"])
    assert code == 9 and st["iter"] == 0 and "deadline" in st["last"], (code, out)


def t_seed_log_costs_no_run():
    S = spec_of([["A"], ["X"]])
    tmp = tempfile.mkdtemp(prefix="restage_test_")
    open(tmp + "/seed.log", "w").write("\n".join(head(0, ["A"]) + head(1, ["X"]) + blk("X", "goal", ["A"])))
    code, st, wd, tmp, out = loop(S, [(head(0, ["A", "X"]) + ["wrote /x/G.plan.json"], 0)], tmp=tmp,
                                  extra=["--seed-log", tmp + "/seed.log", "--seed-exit", "4", "--max-iter", "1"])
    assert code == 0 and st["iter"] == 2 and st["records"][0]["replayed"] and not st["records"][1]["replayed"], (code, out)
    assert open(os.path.join(tmp, "seq", "n")).read() == "1" and smap(os.path.join(wd, "final.spec.json")) == {"A": 0, "X": 0}


def t_two_blocked_in_one_report():
    S = spec_of([["A"], ["B"], ["X", "Y"]])
    log = head(2, ["X", "Y"], 0) + blk("X", "goal", ["A"], pre=True)[:-2] + blk("Y", "goal", ["B"], pre=True)[:-2] \
        + ["BLOCKED at this leg: X, Y -- nothing was negotiated"]
    rep = restage.parse_log("\n".join(log), 4)
    assert [b["net"] for b in rep["blocked"]] == ["X", "Y"], rep
    new, _, _ = restage.decide(S, rep, "pull", [], [], None)
    assert restage.stage_map(new) == {("A", 0): 0, ("B", 0): 1, ("X", 0): 0, ("Y", 0): 1}
    new, _, _ = restage.decide(S, rep, "push", [], [], None)
    assert restage.stage_map(new) == {("X", 0): 0, ("A", 0): 1, ("Y", 0): 2, ("B", 0): 3}
    invariants(S, new, moved=[("X", 0), ("Y", 0)])


def t_dry_and_usage():
    S = spec_of([["A"], ["X"]])
    tmp = tempfile.mkdtemp(prefix="restage_test_")
    json.dump(S, open(tmp + "/s.json", "w"))
    open(tmp + "/l.log", "w").write("\n".join(head(1, ["X"]) + blk("X", "goal", ["A"])))
    r = subprocess.run([sys.executable, os.path.join(HERE, "restage.py"), "--dry", "--spec", tmp + "/s.json", "--log", tmp + "/l.log",
                        "--exit", "4", "--policy", "push"], capture_output=True, text=True)
    assert r.returncode == 0 and "push X: stage 1 -> 0 (new)" in r.stdout, r.stdout
    assert sorted(os.listdir(tmp)) == ["l.log", "s.json"]
    r = subprocess.run([sys.executable, os.path.join(HERE, "restage.py"), "--spec", tmp + "/s.json", "--workdir", tmp + "/w",
                        "--deadline", "1", "--", "true"], capture_output=True, text=True)
    assert r.returncode == 2 and "{spec}" in r.stderr, r.stderr
    bad = spec_of([["A"], ["X"]])
    bad["nets"]["X"]["legs"][0]["stage"] = 3
    json.dump(bad, open(tmp + "/b.json", "w"))
    r = subprocess.run([sys.executable, os.path.join(HERE, "restage.py"), "--spec", tmp + "/b.json", "--workdir", tmp + "/w",
                        "--deadline", str(time.time() + 99), "--", "true", "{spec}"], capture_output=True, text=True)
    assert r.returncode == 2 and "not 0..1" in r.stderr, r.stderr


def replay(spec, log, code, policy="pull"):
    S = json.load(open(spec))
    rep = restage.parse_log(open(log, errors="replace").read(), int(code))
    print(f"stage {rep['stage']}, state {rep['state']}, iteration {rep['iteration']}, contested {len(rep['contested'])}")
    try:
        new, cells, recs = restage.decide(S, rep, policy, [], [], None)
    except restage.Stop as e:
        print(f"exit {e.code}: {e.text}")
        return e.code
    invariants(S, new, moved=[(r["net"], r["leg"]) for r in recs])
    for c in cells:
        print(f"blocked {c[0]}\n   walls: {c[1]}\n   move: {c[2]}")
    print("invariants hold: stages 0..n-1, every leg kept, leg and net order kept, nothing else moved")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--replay"]:
        sys.exit(replay(*sys.argv[2:6]))
    tests = [v for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok    {t.__name__[2:]}")
        except Exception as e:                                                        # noqa: BLE001
            failed += 1
            print(f"FAIL  {t.__name__[2:]}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed} of {len(tests)} passed")
    sys.exit(1 if failed else 0)
