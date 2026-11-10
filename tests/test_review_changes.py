#!/usr/bin/env python3
"""The object diff of a PCB review snapshot names exactly what was changed on the board.

    python3 tests/test_review_changes.py

tests/fixture_pcb.py's board, in a temporary tree with a copy of tools/review/,
is snapshotted four times by the real tools/review/snapshot.py --kind pcb:

  RVa   the board as generated. No snapshot has a verdict yet: changes.json has
        `base: null` and an inventory, CHANGES.md says everything is new.
  (a verdict for RVa is written into tools/handoff/reviews/board9.md; then one
   footprint is moved, one track and one via are added -- through pcbnew)
  RVb   changes.json names exactly those: one part moved (from, to), one
        track on its net and layer with its length, one via; nothing removed,
        no zone, text, pad, outline or rule change. The three texts that moved
        with the part are counted as following it, not listed.
  RVc   the board untouched since RVb, which has no verdict: the base is still
        RVa and the same three changes are listed again (work is diffed
        against the last REVIEWED state, not the last snapshot).
  (RVb gets its verdict)
  RVd   base RVc's predecessor with a verdict, RVb: nothing changed.

Each snapshot must also carry geometry.json and the `geometry` and
`object-diff` rows in SUMMARY.md, and tools/review/geom.py must read it.

Exit 0 and `PASS: ...` / 1 and `FAIL: ...`; `SKIP: ...` (exit 0) when pcbnew
(through kicad_project/tools/kpy) or kicad-cli is missing.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KPY = ROOT / "kicad_project" / "tools" / "kpy"
MOVE = ("R3", (30.0, 9.0), (32.5, 8.0))                 # ref, where fixture_pcb.py puts it, where it goes
TRACK = ("SIG3", "F.Cu", (5.0, 18.81), (12.0, 18.81))   # from J1.4 east, 7 mm
VIA = ("SIG3", (12.0, 18.81))


def kpy_side(pcb):
    """Under kpy: the three edits, made the way a board tool makes them."""
    import pcbnew as pn
    MM = pn.FromMM
    b = pn.LoadBoard(pcb)
    nets = b.GetNetsByName()
    f = b.FindFootprintByReference(MOVE[0])
    f.SetPosition(pn.VECTOR2I(MM(MOVE[2][0]), MM(MOVE[2][1])))
    t = pn.PCB_TRACK(b)
    t.SetStart(pn.VECTOR2I(MM(TRACK[2][0]), MM(TRACK[2][1])))
    t.SetEnd(pn.VECTOR2I(MM(TRACK[3][0]), MM(TRACK[3][1])))
    t.SetWidth(MM(0.2)); t.SetLayer(b.GetLayerID(TRACK[1])); t.SetNet(nets[TRACK[0]])
    b.Add(t)
    v = pn.PCB_VIA(b)
    v.SetPosition(pn.VECTOR2I(MM(VIA[1][0]), MM(VIA[1][1])))
    v.SetWidth(pn.F_Cu, MM(0.6)); v.SetDrill(MM(0.3)); v.SetLayerPair(pn.F_Cu, pn.B_Cu); v.SetNet(nets[VIA[0]])
    b.Add(v)
    pn.SaveBoard(pcb, b)
    return 0


def near(p, q, tol=1e-3):
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


def exactly_the_edits(c, bad, tag):
    """changes.json `c` holds the three edits and nothing else."""
    s = c["summary"]
    want = dict(parts_moved=1, parts_added=0, parts_removed=0, parts_changed=0, pads_renetted=0, tracks_added=1,
                tracks_removed=0, vias_added=1, vias_removed=0, zones_added=0, zones_removed=0, zones_changed=0,
                texts_moved=0, texts_changed=0, texts_added=0, texts_removed=0, outline_changed=False, rules_changed=0)
    for k, v in want.items():
        if s.get(k) != v:
            bad.append(f"{tag}: summary.{k} = {s.get(k)!r}, expected {v!r}")
    m = c["parts"]["moved"]
    if not (len(m) == 1 and m[0]["ref"] == MOVE[0] and near(m[0]["from"], MOVE[1]) and near(m[0]["to"], MOVE[2])):
        bad.append(f"{tag}: parts.moved = {m}, expected {MOVE}")
    pn = c["copper"]["per_net"]
    if not (len(pn) == 1 and pn[0]["net"] == TRACK[0] and pn[0]["layer"] == TRACK[1]
            and pn[0]["added"]["segments"] == 1 and abs(pn[0]["added"]["length"] - 7.0) < 1e-3
            and pn[0]["removed"]["segments"] == 0):
        bad.append(f"{tag}: copper.per_net = {pn}, expected one 7 mm track of {TRACK[0]} on {TRACK[1]}")
    a = c["copper"]["added"]
    if not (len(a) == 1 and {tuple(round(x, 3) for x in a[0]["a"]), tuple(round(x, 3) for x in a[0]["b"])}
            == {TRACK[2], TRACK[3]}):
        bad.append(f"{tag}: copper.added = {a}")
    v = c["vias"]["added"]
    if not (len(v) == 1 and v[0]["net"] == VIA[0] and near(v[0]["at"], VIA[1])):
        bad.append(f"{tag}: vias.added = {v}, expected {VIA}")
    if abs(s["length_added"] - 7.0) > 1e-3 or s["length_removed"] != 0:
        bad.append(f"{tag}: length +{s['length_added']} / -{s['length_removed']}, expected +7.0 / -0")
    if c["texts"]["followed"] != 3:
        bad.append(f"{tag}: texts.followed = {c['texts']['followed']}, expected 3 (R3's reference, its value and its fab-layer reference)")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--kpy":
        return kpy_side(sys.argv[2])
    if not shutil.which("kicad-cli"):
        print("SKIP: kicad-cli is not on PATH (snapshot.py needs it)")
        return 0
    if subprocess.run([str(KPY), "-c", "import pcbnew"], capture_output=True).returncode:
        print("SKIP: pcbnew is not importable through kicad_project/tools/kpy")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="test-review-changes-"))
    try:
        # a tree snapshot.py takes for the repository: its own folder, the board tools, one board
        shutil.copytree(ROOT / "tools" / "review", tmp / "tools" / "review", ignore=shutil.ignore_patterns("__pycache__"))
        (tmp / "kicad_project").mkdir()
        os.symlink(ROOT / "kicad_project" / "tools", tmp / "kicad_project" / "tools")
        board = tmp / "kicad_project" / "board9_fx"
        r = subprocess.run([str(KPY), str(ROOT / "tests" / "fixture_pcb.py"), str(tmp / "fx"), "--nets", "4"],
                           capture_output=True, text=True, cwd=ROOT)
        if r.returncode:
            print(r.stdout + r.stderr)
            print("FAIL: tests/fixture_pcb.py did not build the board")
            return 1
        board.mkdir()
        for f in (tmp / "fx" / "fixture").glob("fixture.kicad_*"):
            if f.suffix != ".kicad_prl":
                shutil.copy2(f, board / f.name)
        reviews = tmp / "tools" / "handoff" / "reviews" / "board9.md"
        reviews.parent.mkdir(parents=True)
        reviews.write_text("# Reviews -- board9\n")
        times = []

        def snapshot(rid):
            t0 = time.monotonic()
            r = subprocess.run([sys.executable, str(tmp / "tools" / "review" / "snapshot.py"), "--board", "board9",
                                "--kind", "pcb", "--id", rid], capture_output=True, text=True, cwd=tmp)
            times.append(time.monotonic() - t0)
            out = tmp / "out" / "reviews" / "board9_fx" / f"{rid}-pcb"
            if not (out / "SUMMARY.md").exists():
                raise RuntimeError(f"snapshot {rid}: no SUMMARY.md (exit {r.returncode}): " + (r.stderr or r.stdout)[-600:])
            rows = {ln.split("|")[1].strip(): [x.strip() for x in ln.split("|")[2:4]]
                    for ln in (out / "SUMMARY.md").read_text().splitlines() if ln.startswith("| ")}
            for name in ("geometry", "object-diff"):
                if name not in rows or rows[name][0] in ("FAIL", "ERROR", "not run"):
                    raise RuntimeError(f"snapshot {rid}: SUMMARY row {name}: {rows.get(name)}")
            for f in ("geometry.json", "CHANGES.md", "changes.json", "changed.md", "measurements.json"):
                if not (out / f).exists():
                    raise RuntimeError(f"snapshot {rid}: {f} is missing")
            time.sleep(1.1)                       # `taken` is to the second: the next one is strictly newer
            return out, json.loads((out / "changes.json").read_text()), rows["object-diff"][1]

        bad = []
        a_out, a, a_row = snapshot("RVa")
        if a.get("base") is not None or a["inventory"]["tracks"] != 0 or a["inventory"]["parts"] != 7:
            bad.append(f"RVa: expected base null and an inventory of 7 parts, 0 tracks: {json.dumps(a)[:300]}")
        if "all new" not in a_row or "new to review" not in (a_out / "CHANGES.md").read_text():
            bad.append(f"RVa: the row / CHANGES.md do not say everything is new: {a_row}")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "review" / "geom.py"), str(a_out), "label"],
                           capture_output=True, text=True)
        if r.returncode or "R1" not in r.stdout:
            bad.append("RVa: geom.py <snapshot> label did not print the labels: " + (r.stderr or r.stdout)[-300:])

        reviews.write_text(reviews.read_text() + "\n## RVa — pcb: the fixture as generated — PASS\n\nNothing to fix.\n")
        r = subprocess.run([str(KPY), str(Path(__file__).resolve()), "--kpy", str(board / "fixture.kicad_pcb")],
                           capture_output=True, text=True, cwd=ROOT)
        if r.returncode:
            print(r.stdout + r.stderr)
            print("FAIL: the edits through pcbnew did not apply")
            return 1
        b_out, b, b_row = snapshot("RVb")
        if (b.get("base") or {}).get("id") != "RVa" or (b.get("base") or {}).get("verdict") != "PASS":
            bad.append(f"RVb: base = {b.get('base')}, expected RVa / PASS")
        else:
            exactly_the_edits(b, bad, "RVb")
        md = (b_out / "CHANGES.md").read_text()
        for word in (MOVE[0], TRACK[0], "RVa"):
            if word not in md:
                bad.append(f"RVb: CHANGES.md does not name {word}")
        if "since RVa (PASS)" not in b_row:
            bad.append(f"RVb: SUMMARY row: {b_row}")

        c_out, c, _ = snapshot("RVc")                # RVb has no verdict: still against RVa
        if (c.get("base") or {}).get("id") != "RVa":
            bad.append(f"RVc: base = {c.get('base')}, expected RVa (RVb has no verdict)")
        else:
            exactly_the_edits(c, bad, "RVc")
        if "RVb-pcb" not in (c_out / "CHANGES.md").read_text():
            bad.append("RVc: CHANGES.md does not say the newer RVb snapshot was passed over")

        reviews.write_text(reviews.read_text() + "\n## RVb — schematic + pcb: one part, one track, one via — CHANGES (1 must-fix)\n")
        _, d, d_row = snapshot("RVd")
        if (d.get("base") or {}).get("id") != "RVb":
            bad.append(f"RVd: base = {d.get('base')}, expected RVb (CHANGES is a verdict)")
        elif any(v for k, v in d["summary"].items() if k != "nets_with_copper_changes" and v) or d["texts"]["followed"]:
            bad.append(f"RVd: nothing was changed since RVb, but: {d['summary']}")

        # the same diff from the command line, on the two snapshot folders
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "review" / "changes.py"), str(a_out), str(b_out), "--json"],
                           capture_output=True, text=True)
        try:
            exactly_the_edits(json.loads(r.stdout), bad, "changes.py RVa RVb")
        except (ValueError, KeyError) as e:
            bad.append(f"changes.py OLD NEW --json: {e!r}: {(r.stderr or r.stdout)[-300:]}")
        if bad:
            print("\n".join(bad))
            print(f"FAIL: {len(bad)} wrong; first: {bad[0]}")
            return 1
        print(f"PASS: 4 snapshots ({min(times):.1f}-{max(times):.1f} s each); no verdict -> all new (7 parts, 0 tracks); "
              f"after RVa's PASS the diff names exactly {MOVE[0]} moved {MOVE[1]} -> {MOVE[2]}, 1 track "
              f"(+{b['summary']['length_added']:.3f} mm {TRACK[0]} {TRACK[1]}), 1 via, 3 texts following; an unreviewed "
              "snapshot is passed over as the base; after RVb's CHANGES verdict: 0 changes")
        return 0
    except RuntimeError as e:
        print(f"FAIL: {e}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
