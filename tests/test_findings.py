#!/usr/bin/env python3
"""A review finding becomes a check a tool runs before the next review (ROADMAP V5).

    python3 tests/test_findings.py [--keep]

What it replaces: a decoupling cap drifted off its ball in five consecutive reviews of one
board, and each time a reviewer measured it.  A finding a number decides now carries a check
(`Check RV4.5: cap reach | U1: 1.27 mm (C65)` under its verdict), the handoff publishes the
checks as `findings.json`, `route.py audit --findings` runs them on the board, and the review
snapshot runs them as a gate.  This test holds each link to it:

  rules     kicad_project/tools/route/checks.py's five rules on plain lists, no KiCad: each
            passes where it should, fails where it should and names the site; every malformed
            setting is refused, and a rule that is not a check is refused as a finding.
  board     tests/fixture_pcb.py's `vias` board through tools/kpy: `route.py audit --findings`
            with no findings.json passes in one line; six findings give four FAILs by id and
            exit 1; the two that hold alone exit 0; a setting the parser cannot read exits
            non-zero by the finding's id.  `route.py audit` runs the same rules as rows of the
            guide (handoff/rules.json), one gate line each.
  snapshot  tools/review/snapshot.py --kind pcb on that board: SUMMARY.md has the `audit` and
            `findings` rows, `findings` FAIL with the ids in its line.
  publish   on a scratch copy of the repo, a board made by new_board.py: Check lines under a
            verdict publish as handoff/findings.json (id, rule, setting, source) and the
            handoff's README names the file; a line in a fenced block is text; a malformed
            line and an unknown rule stop the publish with the file and the form;
            `publish.py --check` is fresh.

Exit 0 and a last line `PASS: ...`, exit 1 and `FAIL: ...`; `SKIP: ...` (exit 0) when pcbnew or
kicad-cli is not on this machine -- the `rules` part has run by then, and a failure there is
still a FAIL.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "kicad_project" / "tools"
KPY = str(TOOLS / "kpy")
sys.path.insert(0, str(TOOLS / "route"))
sys.path.insert(0, str(ROOT / "tests"))
import checks as CK          # noqa: E402
import guide                 # noqa: E402

fails = []


def expect(ok, what, out=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        fails.append(what)
        tail = [ln for ln in str(out).strip().splitlines() if "Debug:" not in ln and "property.h" not in ln][-8:]
        if tail:
            print("        " + "\n        ".join(tail))


def one(rule, setting, geo, classes_of=None, full_width=None):
    G = {}
    CK.parse(rule, setting, {"source": "test"}, G)
    (_n, _k, _r, sites), = CK.run(G, geo, classes_of, full_width)
    return sites


def rules():
    geo = {"pads": [("U1", "A1", "VDD", 0.0, 0.0), ("U1", "A2", "GND", 1.0, 0.0),
                    ("C1", "1", "VDD", 0.5, 1.0), ("C1", "2", "GND", 1.5, 1.0)],
           "vias": [], "tracks": []}
    expect(one("cap reach", "U1: 1.27 mm (C1)", geo) == [], "cap reach: both pads 1.118 mm from their ball hold at 1.27 mm")
    s = one("cap reach", "U1: 1.0 mm (C1)", geo)
    expect(len(s) == 2 and "C1.1 (VDD)" in s[0] and "1.118 mm" in s[0], "cap reach: fails at 1.0 mm, names pad, net, distance", s)
    geo2 = dict(geo, vias=[("VDD", 0.5, 1.5)])
    s = one("cap reach", "U1: 0.6 mm (C1)", geo2)
    expect(len(s) == 1 and "C1.2 (GND)" in s[0], "cap reach: a same-net via within reach counts; the other pad still fails", s)
    s = one("cap reach", "U1: 1.27 mm (C1, C9)", geo)
    expect(len(s) == 1 and "C9 is not on the board" in s[0], "cap reach: a named part that is gone is a site, not a pass", s)

    tr = lambda *rows: {"pads": [], "vias": [], "tracks": list(rows)}
    over = tr(("X", "In1.Cu", (0.0, 0.0), (10.0, 0.0), 0.1), ("Y", "In2.Cu", (2.0, 0.02), (6.0, 0.02), 0.1))
    s = one("layers apart", "In1.Cu / In2.Cu: 0 mm", over)
    got = float(s[0].split(": ")[1].split(" mm")[0]) if s else 0
    expect(len(s) == 1 and "X (In1.Cu) over Y (In2.Cu)" in s[0] and 4.0 <= got <= 4.4,
           "layers apart: 4 mm of Y under X is one site with its length", s)
    expect(one("layers apart", "In1.Cu / In2.Cu: 5 mm", over) == [], "layers apart: the same overlap holds at 5 mm")
    expect(one("layers apart", "In1.Cu / In2.Cu: 0 mm",
               tr(("X", "In1.Cu", (0.0, 0.0), (10.0, 0.0), 0.1), ("Y", "In2.Cu", (5.0, -3.0), (5.0, 3.0), 0.1))) == [],
           "layers apart: a square crossing is free")
    expect(one("layers apart", "In1.Cu / In2.Cu: 0 mm",
               tr(("X", "In1.Cu", (0.0, 0.0), (10.0, 0.0), 0.1), ("X", "In2.Cu", (2.0, 0.0), (6.0, 0.0), 0.1),
                  ("Y", "In2.Cu", (0.0, 0.5), (10.0, 0.5), 0.1), ("Z", "F.Cu", (0.0, 0.0), (10.0, 0.0), 0.1))) == [],
           "layers apart: the same net, a track 0.5 mm aside and another layer are not sites")

    vg = {"pads": [], "tracks": [], "vias": [("D_P", 0.0, 0.0), ("GND", 1.0, 0.0), ("OTHER", 9.0, 9.0)]}
    cls = lambda net: ["Pairs"] if net.startswith("D_") else ["Default"]
    expect(one("stitch via", "Pairs: GND within 1.27 mm", vg, cls) == [], "stitch via: a GND via 1 mm away holds at 1.27 mm")
    s = one("stitch via", "Pairs: GND within 0.5 mm", vg, cls)
    expect(len(s) == 1 and "D_P via at (0.000, 0.000)" in s[0] and "1.000 mm" in s[0], "stitch via: fails at 0.5 mm; only the class's vias are judged", s)
    s = one("stitch via", "Pairs: GND within 2 mm", dict(vg, vias=[("D_P", 0.0, 0.0)]), cls)
    expect(len(s) == 1 and "no GND via" in s[0], "stitch via: no via of the net at all is a site", s)

    ng = tr(("A", "F.Cu", (0.0, 0.0), (2.0, 0.0), 0.1), ("A", "F.Cu", (2.0, 0.0), (4.0, 1.0), 0.1),
            ("A", "F.Cu", (4.0, 1.0), (9.0, 1.0), 0.2), ("B", "F.Cu", (0.0, 5.0), (2.0, 5.0), 0.1),
            ("B", "F.Cu", (5.0, 5.0), (7.0, 5.0), 0.1))
    fw = lambda net, lay: 0.2
    s = one("neck length", "4 mm", ng, cls, fw)
    expect(len(s) == 1 and s[0].startswith("A on F.Cu") and "4.236 mm at 0.1 mm" in s[0],
           "neck length: a 4.236 mm run in two segments fails at 4 mm; two separate 2 mm necks do not add up", s)
    expect(one("neck length", "5 mm", ng, cls, fw) == [], "neck length: holds at 5 mm")
    expect(one("neck length", "Pairs: 1 mm", ng, cls, fw) == [], "neck length: a class's rule judges only its nets")
    expect(one("neck length", "1 mm", ng, cls, lambda net, lay: None) == [], "neck length: a net with no known width is not judged")

    expect(one("distance", "C1.1 to U1.A1 <= 1.27 mm", geo) == [], "distance: pad to pad, <=")
    s = one("distance", "C1 to U1 >= 2 mm", geo)
    expect(len(s) == 1 and "1.118 mm, not >= 2.0 mm" in s[0], "distance: a bare REF is its nearest pad; >= fails with the number", s)
    s = one("distance", "C7.1 to U1.A1 <= 1 mm", geo)
    expect(len(s) == 1 and "C7.1 is not on the board" in s[0], "distance: an object that is gone is a site", s)

    bad = [("cap reach", "U1: 1.27 mm"), ("cap reach", "U1: 1.27 (C1)"), ("layers apart", "In1.Cu / In1.Cu: 0 mm"),
           ("layers apart", "In1.Cu: 0 mm"), ("stitch via", "Pairs: GND 1.27 mm"), ("neck length", "four mm"),
           ("distance", "C1.1 to U1.A1 < 1 mm"), ("distance", "C1.1 U1.A1 <= 1 mm")]
    refused = 0
    for rule, val in bad:
        try:
            CK.parse(rule, val, {}, {})
        except CK.RuleError:
            refused += 1
    expect(refused == len(bad), f"every malformed setting is refused ({refused} of {len(bad)})")
    try:
        guide.validate_findings([("RV1.1", "angles", "45")])
        expect(False, "a rule that is not a check is refused as a finding's check")
    except SystemExit as e:
        expect("not a check rule" in str(e) and "RV1.1" in str(e), "a rule that is not a check is refused as a finding's check", e)
    try:
        G = guide.validate([("distance", "C1.1 to U1.A1 <= 1.27 mm", "test"), ("cap reach", "U1: 1.27 mm (C1)", "test")])
        expect(len(G.get("distance", [])) == 1 and len(G.get("cap_reach", [])) == 1, "the check rules are rows of the guide too")
    except SystemExit as e:
        expect(False, "the check rules are rows of the guide too", e)


FINDINGS = [("RV1.1", "distance", "R1.1 to R1.2 <= 2 mm"), ("RV1.2", "distance", "R1.1 to R2.1 <= 1 mm"),
            ("RV1.3", "cap reach", "R2: 3 mm (R1)"), ("RV2.1", "layers apart", "F.Cu / B.Cu: 0 mm"),
            ("RV2.2", "neck length", "1 mm"), ("RV2.3", "stitch via", "Default: N1 within 1 mm")]
OPEN = ["RV1.2", "RV1.3", "RV2.1", "RV2.3"]
# the fixture's WALL<k> and ROOF<k> tracks touch, and KiCad saves a touching pair under one of the two names --
# not the same one every run -- so a site is matched by its form, never by one net
SITE = re.compile(r"(?:ROOF|WALL)\d+ \(F\.Cu\) over N\d+ \(B\.Cu\) near \(\d+\.\d+, \d+\.\d+\): \d\.\d+ mm shared")


def write_findings(board, rows):
    (board / "handoff").mkdir(exist_ok=True)
    (board / "handoff" / "findings.json").write_text(json.dumps(
        [{"id": i, "rule": r, "setting": s, "source": "review " + i.split(".")[0]} for i, r, s in rows], indent=1))


def sh(cmd, cwd=ROOT, timeout=900):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=str(cwd), timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def board(tmp, times):
    rc, out = sh([KPY, ROOT / "tests" / "fixture_pcb.py", "--kind", "vias", tmp / "fx"])
    b = tmp / "fx" / "vias"
    if rc or not (b / "vias.kicad_pcb").exists():
        expect(False, "tests/fixture_pcb.py --kind vias builds the board", out)
        return None
    audit = [KPY, TOOLS / "route" / "route.py", "audit", "--board", b]
    rc, out = sh(audit + ["--findings"])
    expect(rc == 0 and "FINDINGS PASS: no handoff/findings.json" in out, "no findings.json: one PASS line, exit 0", out)
    write_findings(b, FINDINGS)
    t = time.time()
    rc, out = sh(audit + ["--findings"])
    times["findings"] = time.time() - t
    last = [ln for ln in out.splitlines() if ln.startswith("FINDINGS")]
    rows = {ln.split()[0]: ln.split()[1] for ln in out.splitlines() if ln.strip().startswith(("RV1.", "RV2."))}
    expect(rc == 1 and last and f"FINDINGS FAIL: 4 of 6" in last[-1] and all(i in last[-1] for i in OPEN)
           and [i for i, v in rows.items() if v == "FAIL"] == OPEN and rows.get("RV1.1") == "PASS" and rows.get("RV2.2") == "PASS",
           "six findings: the four that are open fail by id, the two that hold pass, exit 1", out)
    expect(SITE.search(out) and "R1.1 to R2.1: 6.000 mm" in out,
           "the sites are named: layers by KiCad's names, the distance with its number", out)
    write_findings(b, [f for f in FINDINGS if f[0] not in OPEN])
    rc, out = sh(audit + ["--findings"])
    expect(rc == 0 and "FINDINGS PASS: 2 finding check(s) hold" in out, "the findings that hold: FINDINGS PASS, exit 0", out)
    write_findings(b, [("RV3.1", "distance", "R1.1 near R1.2")])
    rc, out = sh(audit + ["--findings"])
    expect(rc != 0 and "RV3.1" in out and "'distance' takes" in out, "a setting the parser cannot read stops by the finding's id", out)
    write_findings(b, FINDINGS)
    (b / "handoff" / "rules.json").write_text(json.dumps(
        [{"rule": "distance", "setting": "R1.1 to R1.2 <= 2 mm", "source": "test"},
         {"rule": "layers apart", "setting": "F.Cu / B.Cu: 0 mm", "source": "test"}]))
    t = time.time()
    rc, out = sh(audit)
    times["audit"] = time.time() - t
    gl = {ln.split("  ")[1].strip(): ln for ln in out.splitlines() if ln.startswith("  ") and ("PASS" in ln or "FAIL" in ln)}
    last = [ln for ln in out.splitlines() if ln.startswith("AUDIT")]
    expect("PASS" in gl.get("distance", "") and "FAIL" in gl.get("layers apart", "") and re.search(r"\d+ site\(s\)", gl.get("layers apart", ""))
           and last and "layers apart" in last[-1] and "distance" not in last[-1],
           "route.py audit: the same rules as rows of the guide, one gate line each, the failing one in the AUDIT line", out)
    return b


def snapshot(tmp, b, times):
    shutil.copytree(ROOT / "tools" / "review", tmp / "tools" / "review", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "kicad_project").mkdir()
    os.symlink(TOOLS, tmp / "kicad_project" / "tools")
    dst = tmp / "kicad_project" / "board9_fx"
    shutil.copytree(b, dst, ignore=shutil.ignore_patterns("*.kicad_prl", ".drc_cache", "__pycache__"))
    (tmp / "tools" / "handoff" / "reviews").mkdir(parents=True)
    (tmp / "tools" / "handoff" / "reviews" / "board9.md").write_text("# Reviews -- board9\n")
    t = time.time()
    rc, out = sh([sys.executable, tmp / "tools" / "review" / "snapshot.py", "--board", "board9", "--kind", "pcb", "--id", "RV9"], cwd=tmp)
    times["snapshot"] = time.time() - t
    summ = tmp / "out" / "reviews" / "board9_fx" / "RV9-pcb" / "SUMMARY.md"
    if not summ.exists():
        expect(False, "snapshot.py --kind pcb writes SUMMARY.md", out)
        return
    rows = {ln.split("|")[1].strip(): [x.strip() for x in ln.split("|")[2:4]] for ln in summ.read_text().splitlines() if ln.startswith("| ")}
    expect(rows.get("findings", [""])[0] == "FAIL" and "4 of 6" in rows["findings"][1] and "RV1.2" in rows["findings"][1],
           "the snapshot's `findings` row: FAIL, with the count and the ids", summ.read_text())
    expect(rows.get("audit", [""])[0] == "FAIL" and "AUDIT FAIL" in rows["audit"][1] and "layers apart" in rows["audit"][1],
           "the snapshot's `audit` row: the guide's rules, with the failing gates", summ.read_text())
    g = (summ.parent / "gates" / "findings.txt")
    expect(g.exists() and SITE.search(g.read_text()), "gates/findings.txt holds the sites")


def publish(tmp, times):
    import agentic_test as A
    copy = tmp / "repo"
    shutil.copytree(ROOT, copy, ignore=A.IGNORE, symlinks=True)
    n = A.free_board(copy)
    bid = f"board{n}"
    rc, out = A.run([sys.executable, "tools/handoff/new_board.py", str(n), "findings", "Findings test board"], copy)
    if rc:
        expect(False, "new_board.py makes the board", out)
        return
    rev = copy / "tools" / "handoff" / "reviews" / f"{bid}.md"
    rev.parent.mkdir(parents=True, exist_ok=True)
    handoff = copy / "kicad_project" / f"{bid}_findings" / "handoff"
    head = ("# Reviews\n\n## RV4 — pcb: power — CHANGES (2 must-fix)\n\nReviewed: handoff rev 2.\n\n"
            "| id | severity | where | finding | evidence | fix |\n|---|---|---|---|---|---|\n"
            "| RV4.5 | must | C65 | 2.1 mm from its ball | 10_layout | move it |\n\n")

    def pub(*extra):
        t = time.time()
        r = A.run([sys.executable, "tools/handoff/publish.py", *(extra or ("--board", bid))], copy)
        times.setdefault("publish", []).append(time.time() - t)
        return r

    rc, out = pub()
    expect(rc == 0 and not (handoff / "findings.json").exists(), "no Check line: no findings.json", out)
    rev.write_text(head + "Check RV4.5: cap reach | U1: 1.27 mm (C65)\n- Check RV4.6: Distance | C12.1 to U1.A5 <= 1.27 mm\n\n"
                   "```\nCheck RV9.9: distance | this is the format, quoted\n```\n\n"
                   "## M1 — pcb: all — PASS\n\nCheck M1.2: layers apart | In4.Cu / In5.Cu: 0 mm\n")
    rc, out = pub()
    got = json.loads((handoff / "findings.json").read_text()) if (handoff / "findings.json").exists() else None
    expect(rc == 0 and got == [
        {"id": "RV4.5", "rule": "cap reach", "setting": "U1: 1.27 mm (C65)", "source": "review RV4"},
        {"id": "RV4.6", "rule": "distance", "setting": "C12.1 to U1.A5 <= 1.27 mm", "source": "review RV4"},
        {"id": "M1.2", "rule": "layers apart", "setting": "In4.Cu / In5.Cu: 0 mm", "source": "review M1"}],
        "three Check lines publish as findings.json; the one in a fenced block is text", out + "\n" + json.dumps(got))
    readme = "".join(p.read_text() for p in handoff.glob("*README*")) + "".join(p.read_text() for p in handoff.glob("00_*"))
    expect("findings.json" in readme and "audit --findings" in readme, "the handoff's first page names findings.json and the command")
    man = json.loads((handoff / "MANIFEST.json").read_text())
    expect("findings.json" in json.dumps(man), "the MANIFEST carries findings.json")
    rc, out = pub("--check", "--board", bid)
    expect(rc == 0, "publish.py --check: fresh", out)
    good = rev.read_text()
    rev.write_text(good + "Check RV4.7: cap reach | U1 1.27 (C65)\n")
    rc, out = pub()
    line = [ln for ln in out.splitlines() if ln.startswith("publish:")]
    expect(rc == 2 and line and "RV4.7" in line[0] and "'cap reach' takes" in line[0] and f"reviews/{bid}.md" in line[0],
           "a setting the board's parser cannot read stops the publish, by file, id and form", out)
    rev.write_text(good + "Check RV4.7: angles | 45\n")
    rc, out = pub()
    expect(rc == 2 and "not a check rule" in out, "a rule that is not a check stops the publish", out)
    rev.write_text(good + "Check 4.7 cap reach | U1: 1.27 mm (C65)\n")
    rc, out = pub()
    expect(rc == 2 and "a finding's check reads" in out, "a Check line without an id stops the publish", out)
    rev.write_text(good)
    rc, out = pub("--check", "--board", bid)
    expect(rc == 0, "restored: --check is fresh again", out)


def main():
    keep = "--keep" in sys.argv
    t0 = time.time()
    print("rules (no KiCad)")
    rules()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails)[:300])
        return 1
    if not shutil.which("kicad-cli") or subprocess.run([KPY, "-c", "import pcbnew"], capture_output=True).returncode:
        print(f"SKIP: the rules hold on plain lists ({time.time() - t0:.1f} s); pcbnew through tools/kpy or kicad-cli is not "
              "on this machine, so the board, snapshot and publish parts did not run")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="test-findings-"))
    times = {}
    try:
        print("board (tools/kpy)")
        b = board(tmp, times)
        if b is not None:
            print("snapshot")
            snapshot(tmp, b, times)
        print("publish (scratch copy)")
        publish(tmp, times)
    finally:
        if keep:
            print(f"kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails)[:400])
        return 1
    print(f"PASS: five rules on plain lists; on the vias board 6 findings judged in {times.get('findings', 0):.1f} s "
          f"(4 open by id), audit with 2 check rows {times.get('audit', 0):.1f} s, snapshot with the `audit` and `findings` "
          f"rows {times.get('snapshot', 0):.1f} s; Check lines published as findings.json "
          f"({len(times.get('publish', []))} publishes, {sum(times.get('publish', [])):.1f} s); {time.time() - t0:.0f} s in all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
