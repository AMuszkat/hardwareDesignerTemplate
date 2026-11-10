#!/usr/bin/env python3
"""A custom DRC rule is proven on the board before it is published (ROADMAP Q1a).

    python3 tests/test_rule_gate.py [--keep]

On a scratch copy of the repo: a board made by tools/handoff/new_board.py, the
harness's fixture design, and tests/fixture_pcb.py's `prelayout` board (a
100-ball BGA U1, two bus headers) as its PCB. It then drives the gate the way
the concept session meets it:

  1  a rule published while the board has no PCB needs no proof;
  2  with a PCB, a NEW rule naming a net that is not on the board does not
     publish, and the one line it prints names the rulecheck command;
  3  tools/review/rulecheck.py fails that rule ("matches nothing"), and the
     publish still refuses it;
  4  a rule KiCad cannot read (it drops the whole rule file, silently) is
     named by rulecheck;
  5  good rules (a net wildcard, a net class from the page) pass rulecheck,
     publish, and `publish.py --check` is fresh; the MANIFEST carries the
     result hash and each rule's state;
  6  the same rule with one number moved is unproven again;
  7  a rule about U1's escape fails with no FAB["escape"] row, fails on a
     bound that is PROVEN SHORT, and publishes with a bound that is not --
     which 14_fab.md then states;
  8  FAB["unproven"] publishes a rule the board cannot prove yet, as a lint
     note, and rulecheck prints it WAIVED without failing;
  9  probes that fire more than 199 times together (KiCad reports no more of
     one violation type in a run, so the first to fire starve the rest -- on
     a real board that read as "no rule loaded") still give every rule its
     verdict; which probes starve depends on the order KiCad's threads visit
     the items, so the runs that settle them are also driven with a
     simulated DRC, where the order is fixed.

Exit 0 pass / 1 fail; 0 with SKIP when kicad-cli or pcbnew is absent. The last
stdout line is PASS / FAIL / SKIP.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import agentic_test as A                             # noqa: E402  (the fixture design, the copy's ignore list)

KPY = ROOT / "kicad_project" / "tools" / "kpy"

DUMP = r'''
import json, sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
f = next(f for f in b.GetFootprints() if f.GetReference() == "U1")
P = {p.GetNumber(): [p.GetPosition().x / 1e6, p.GetPosition().y / 1e6] for p in f.Pads()}
out = sys.argv[2]
json.dump([{"x": x, "y": y} for x, y in P.values()], open(out + "/vias_all.json", "w"))
cx = sum(x for x, _ in P.values()) / len(P)
cy = sum(y for _, y in P.values()) / len(P)
inner = sorted(P, key=lambda k: ((P[k][0] - cx) ** 2 + (P[k][1] - cy) ** 2, k))[:4]     # the four nearest the centre
outer = sorted(P, key=lambda k: (P[k][1], P[k][0]))[:4]                                # four on the outer row
json.dump({k: P[k] for k in inner}, open(out + "/esc_inner.json", "w"))
json.dump({k: P[k] for k in outer}, open(out + "/esc_outer.json", "w"))
'''


def rule(name, constraint, cond):
    return f'(rule "{name}"\n  (constraint {constraint})\n  (condition "{cond}"))'


def main():
    keep = "--keep" in sys.argv
    if not shutil.which("kicad-cli"):
        print("SKIP: kicad-cli is not installed")
        return 0
    if A.run([KPY, "-c", "import pcbnew"], ROOT, timeout=120)[0]:
        print("SKIP: tools/kpy cannot import pcbnew")
        return 0
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="rule-gate-"))
    fails, drc_s = [], []

    def expect(ok, what, out=""):
        print(("  ok    " if ok else "  FAIL  ") + what)
        if not ok:
            fails.append(what)
            if out:
                print("        " + "\n        ".join(out.strip().splitlines()[-6:]))

    try:
        copy = tmp / "repo"
        shutil.copytree(ROOT, copy, ignore=A.IGNORE, symlinks=True)
        n = A.free_board(copy)
        bid = f"board{n}"
        rc, out = A.run([sys.executable, "tools/handoff/new_board.py", str(n), "rulegate", "Rule gate test board",
                         "--fab", "example_standard", "--no-publish"], copy)
        if rc:
            print("FAIL: new_board: " + A.tail(out))
            return 1
        bus = copy / "studies" / f"components_{bid}.py"
        bus.write_text(bus.read_text() + "\nCOMPONENTS = " + repr(A.COMPONENTS) + "\n")
        mod = copy / "tools" / "handoff" / "boards" / f"{bid}.py"
        base = mod.read_text() + A.BOARD_DATA
        board = copy / "kicad_project" / f"{bid}_rulegate"
        cmd = f"python3 tools/review/rulecheck.py --board {bid}"

        def data(rules, classes=(), escape=(), unproven=None, note="rule gate test"):
            mod.write_text(base + f"\nRELEASE_NOTE = {note!r}\nFAB['rules'] = {list(rules)!r}\n"
                           f"FAB['netclasses'] = {list(classes)!r}\n"
                           + (f"FAB['escape'] = {list(escape)!r}\n" if escape else "")
                           + (f"FAB['unproven'] = {unproven!r}\n" if unproven else ""))

        def publish(*extra):
            return A.run([sys.executable, "tools/handoff/publish.py", *(extra or ("--board", bid))], copy)

        def rulecheck():
            t = time.time()
            r = A.run([sys.executable, "tools/review/rulecheck.py", "--board", bid], copy)
            drc_s.append(time.time() - t)
            return r

        def manifest():
            return json.loads((board / "handoff" / "MANIFEST.json").read_text())

        dead = ("dead net", rule("test: dead net", "clearance (min 0.3mm)",
                                 "A.NetName == '*NOPE_T' && B.NetName != A.NetName"), "test")
        bus_w = ("bus wildcard", rule("test: bus wildcard", "clearance (min 0.2mm)",
                                      "A.NetName == 'BUS*' && B.NetName != A.NetName"), "test")
        bus_c = ("bus class", rule("test: bus class", "track_width (min 0.15mm)", "A.NetClass == 'BUSC'"), "test")
        classes = [("BUSC", "BUS0, BUS1, BUS2", 0.2, 0.2, "0.3 / 0.6", "test")]

        # 1 -- no PCB: any rule publishes, and nothing is owed
        data([dead])
        rc, out = publish()
        expect(rc == 0 and "rulecheck" not in out, "1 no PCB yet: a rule publishes without a proof, no lint", out)
        expect(manifest().get("rulecheck", {}).get("rules", {}).get("test: dead net", {}).get("state") == "unproven",
               "1 the MANIFEST records it as unproven")
        rc, out = rulecheck()
        expect(rc == 2 and "no PCB" in out, "1 rulecheck without a PCB exits 2", out)

        # the PCB arrives (the board session's work)
        rc, out = A.run([KPY, ROOT / "tests" / "fixture_pcb.py", tmp, "--kind", "prelayout"], ROOT)
        if rc:
            print("FAIL: fixture_pcb: " + A.tail(out))
            return 1
        for ext in (".kicad_pcb", ".kicad_pro"):
            shutil.copy(tmp / "prelayout" / f"prelayout{ext}", board / f"prelayout{ext}")
        (board / "routing").mkdir(exist_ok=True)
        rc, out = A.run([KPY, "-c", DUMP, board / "prelayout.kicad_pcb", board / "routing"], ROOT)
        if rc:
            print("FAIL: reading U1's balls: " + A.tail(out))
            return 1
        rc, out = publish("--check", "--board", bid)
        expect(rc == 0 and "never proven" in out and cmd in out,
               "1 with a PCB, --check stays fresh and says the published rule was never proven", out)

        # 2 -- a NEW rule that matches no net does not publish
        rev = manifest()["rev"]
        data([dead, ("dead net 2", dead[1].replace("dead net", "dead net 2").replace("NOPE", "NADA"), "test")])
        rc, out = publish()
        line = [ln for ln in out.splitlines() if ln.startswith("publish:")]
        expect(rc == 2 and len(line) == 1 and cmd in line[0] and "test: dead net 2" in line[0],
               "2 a new rule on a board with a PCB does not publish; the line names the rulecheck command", out)
        expect(manifest()["rev"] == rev, "2 the published handoff is untouched")

        # 3 -- rulecheck fails it, and the publish still refuses
        rc, out = rulecheck()
        expect(rc == 1 and "matches nothing on the board: NetName '*NADA_T'" in out and "RULECHECK FAIL" in out,
               "3 rulecheck: the rule matches nothing on the board (exit 1)", out)
        proof = copy / "tools" / "handoff" / "rulechecks" / f"{bid}.json"
        expect(proof.exists() and json.loads(proof.read_text())["rules"]["test: dead net 2"]["ok"] is False,
               "3 the result file records the failure")
        rc, out = publish()
        expect(rc == 2 and "fails: matches nothing" in out and cmd in out, "3 the publish refuses a failed rule", out)

        # 4 -- a rule KiCad cannot read is named (it would silently void every rule)
        data([bus_w, ("typo", rule("test: typo", "clearance (min 0.2mm)", "A.NetNam == 'BUS0'"), "test")])
        rc, out = rulecheck()
        expect(rc == 1 and "FAILS   test: typo: KiCad cannot read this rule" in out
               and "PROVEN  test: bus wildcard" in out,
               "4 rulecheck names the rule that makes KiCad drop the whole file", out)

        # 5 -- good rules: rulecheck, publish, --check fresh
        data([bus_w, bus_c], classes)
        rc, out = rulecheck()
        expect(rc == 0 and "RULECHECK PASS" in out and "NetClass 'BUSC' x" in out,
               "5 a net wildcard and a page net class are proven (exit 0)", out)
        rc, out = publish()
        expect(rc == 0 and "published rev" in out, "5 the proven rules publish", out)
        m, pr = manifest(), json.loads(proof.read_text())
        expect(m["rulecheck"]["result"] == pr["hash"] and
               all(r["state"] == "proven" for r in m["rulecheck"]["rules"].values()) and
               f"tools/handoff/rulechecks/{bid}.json" in m["sources"],
               "5 the MANIFEST holds the result hash, every rule proven, the proof as a source")
        rc, out = publish("--check", "--board", bid)
        expect(rc == 0 and "fresh" in out and "proven" not in out, "5 --check: fresh, nothing owed", out)

        # 6 -- one number moved: the proof no longer covers the text
        moved = (bus_w[0], bus_w[1].replace("0.2mm", "0.25mm"), "test")
        data([moved, bus_c], classes)
        rc, out = publish()
        expect(rc == 2 and "'test: bus wildcard' is changed" in out and cmd in out,
               "6 a changed rule is unproven again", out)
        rc, _ = rulecheck()
        rc2, out = publish()
        expect(rc == 0 and rc2 == 0, "6 proven again, it publishes", out)

        # 7 -- a rule about a BGA's escape carries escape_bound.py's bound
        esc = ("U1 escape", rule("test: U1 escape", "clearance (min 0.0889mm)",
                                 "A.intersectsCourtyard('U1') && B.intersectsCourtyard('U1')"), "test")
        args = ["--vias", "routing/vias_all.json", "--width", "0.0889", "--clearance", "0.0889"]
        data([moved, bus_c, esc], classes)
        rc, out = rulecheck()
        expect(rc == 1 and 'FAB["escape"] has no row for U1' in out, "7 an escape rule with no bound fails", out)
        short = {"ref": "U1", "layer": "In1.Cu", "args": ["--sources", "routing/esc_inner.json", *args]}
        data([moved, bus_c, esc], classes, [short])
        rc, out = rulecheck()
        expect(rc == 1 and "PROVEN SHORT" in out and "FAILS   test: U1 escape: escape bound U1 In1.Cu" in out,
               "7 a bound that is PROVEN SHORT fails the rule", out)
        rc, out = publish()
        expect(rc == 2 and "PROVEN SHORT" in out, "7 and it does not publish", out)
        fits = {"ref": "U1", "layer": "In1.Cu", "args": ["--sources", "routing/esc_outer.json", *args]}
        data([moved, bus_c, esc], classes, [fits])
        rc, out = rulecheck()
        expect(rc == 0 and "ESCAPE  U1 In1.Cu: 4 of 4, not excluded" in out, "7 a bound that is not excluded passes", out)
        rc, out = publish()
        fab = (board / "handoff" / "14_fab.md").read_text()
        expect(rc == 0 and "- `U1` on `In1.Cu`: **4 of 4** — not excluded" in fab,
               "7 it publishes, and 14_fab.md states the bound", out)
        rc, out = A.run([KPY, "kicad_project/tools/board_setup/board_setup.py", "plan", "--board", board,
                         "--fab", board / "handoff" / "14_fab.md"], copy)
        expect("test: U1 escape" in (board / "handoff" / "14_fab.md").read_text() and "Traceback" not in out,
               "7 board_setup.py still reads the page", out)
        rc, out = publish("--check", "--all")
        expect(rc == 0, "7 --check --all: fresh", out)

        # 8 -- the waiver: a rule the board cannot prove yet
        data([moved, bus_c, esc, dead], classes, [fits], {"test: dead net": "the NOPE bank is not drawn yet"})
        rc, out = publish()
        expect(rc == 0 and "LINT: custom DRC rule 'test: dead net' is published unproven" in out,
               "8 FAB['unproven'] publishes it, as a lint note", out)
        expect(manifest()["rulecheck"]["rules"]["test: dead net"]["state"].startswith("waived: "),
               "8 the MANIFEST records the waiver")
        rc, out = rulecheck()
        expect(rc == 0 and "WAIVED  test: dead net: matches nothing" in out and ", 1 waived" in out,
               "8 rulecheck prints the waived rule and does not fail on it", out)

        # 9 -- more probe hits than KiCad reports in one run
        # four rules that each match every pad use the limit up within the first 50 pads visited;
        # a courtyard not reached by then is asked in a second run (KiCad's threads decide which)
        pads = [(f"every pad {k}", rule(f"test: every pad {k}", "clearance (min 0.05mm)",
                                        f"A.Type == 'Pad' && A.Type != '{t}'"), "test")
                for k, t in enumerate(("Via", "Track", "Zone", "Footprint"))]
        yards = ("header yards", rule("test: header yards", "clearance (min 0.15mm)", " || ".join(
            f"(A.intersectsCourtyard('{j}') && B.intersectsCourtyard('{j}'))" for j in ("J3", "J4", "J5"))),
            "test")
        data([*pads, esc, moved, bus_c, yards], classes, [fits])
        rc, out = rulecheck()
        m = re.search(r"(\d+) DRC run\(s\)", out)
        runs = int(m.group(1)) if m else 0
        expect(rc == 0 and runs >= 1 and "lower bound" in out and out.count("  PROVEN  ") >= 8
               and "FAILS" not in out,
               f"9 over the 199-violation limit every rule still gets its verdict ({runs} DRC run(s) this time)",
               out)

        # the same, with the order fixed: a DRC that reports the first 199 hits in probe order
        sys.path.insert(0, str(ROOT / "tools" / "review"))
        import rulecheck as R
        true = {"p0": 300, "p1": 150, "p2": 100, "p3": 0, "p4": 5, R.CANARY: 1}
        asked = []

        def fake(_out, _stem, _own, _rules, probes):
            import collections
            left, by = R.CAP, collections.Counter()
            asked.append([n for n, _, _ in probes])
            for n in sorted((n for n, _, _ in probes), key=lambda n: (n == R.CANARY, n)):   # the canary last
                by[n] = min(true[n], left)
                left -= by[n]
            return by, [], ""

        real, R.drc = R.drc, fake
        try:
            _by, _vio, err, loaded, counts, lower, nrun = R.probe(
                "", "", "", [], [(n, "", "") for n in ("p0", "p1", "p2", "p3", "p4")], (R.CANARY, "", ""))
        finally:
            R.drc = real
        expect(not err and loaded and nrun == 3 and counts == {"p0": 199, "p1": 150, "p2": 49, "p3": 0, "p4": 5}
               and lower == {"p0", "p1", "p2"} and asked[1] == [R.CANARY, "p1", "p2", "p3", "p4"]
               and asked[2] == [R.CANARY, "p3", "p4"],
               "9 simulated: three runs settle five probes, the one true zero stays zero, capped counts are marked",
               f"{err!r} {loaded} {nrun} {counts} {lower} {asked}")
    finally:
        if keep:
            print(f"  kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails)[:300])
        return 1
    print(f"PASS: rule gate: 9 cases, {len(drc_s)} rulecheck runs at {sum(drc_s) / len(drc_s):.1f} s each "
          f"(max {max(drc_s):.1f} s), {time.time() - t0:.0f} s in all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
