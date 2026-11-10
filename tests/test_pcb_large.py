#!/usr/bin/env python3
"""
The placement and fanout tools on a board the size of a real one: each one's
time, and a hash of its result (ROADMAP M2).

    python3 tests/test_pcb_large.py            # judge against tests/fixtures/pcb_large.json
    python3 tests/test_pcb_large.py --bless    # record this machine's hashes there (somebody looked)

The 50 x 30 mm fixture every other stage uses hides where these tools spend
their time: a search that is quadratic in the parts costs nothing on eight of
them.  `tests/fixture_pcb.py --kind large` writes 326 footprints round a
536-ball BGA, and this runs, in a temp folder:

    place.py labels --dry-run --json     the silk-label search
    place.py auto                        sixteen parked capacitors to their pins
    place.py check                       a plan, one part of it illegal on purpose
    place.py drc                         kicad-cli's DRC and the silk tests it lacks
    fanout.py plan                       the BGA's via model
    fanout.py layers                     the nets over three layers (a matroid partition)

What must hold:
  1. each tool's RESULT is the recorded one (sha256 of the file it writes, or
     of its report without clock and path): a faster tool that answers
     differently is a different tool.  The record carries the KiCad version
     it was made with; under another version the hashes are printed and not
     judged, since pcbnew's geometry belongs to KiCad;
  2. each tool stays under its CEILING, a third of what it took before its
     search was indexed (labels 316 s, auto 42 s, drc 10.5 s, layers 35 s; the
     two that were never slow get 5 s).  The margin is wide on purpose: the
     ceiling catches the old algorithm coming back, not a busy machine.

Every time is printed.  SKIP (exit 0) without pcbnew or kicad-cli.  The last
line is PASS / FAIL / SKIP.
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KP = ROOT / "kicad_project"
KPY = str(KP / "tools" / "kpy")
RECORD = ROOT / "tests" / "fixtures" / "pcb_large.json"
# seconds on the machine that recorded them, before the speed-ups (2026-11-10, ROADMAP M2)
BEFORE = {"labels": 316.0, "auto": 42.0, "check": 1.0, "drc": 10.5, "plan": 0.63, "layers": 35.0}
CEILING = {k: max(5.0, v / 3) for k, v in BEFORE.items()}


def run(cmd, timeout=1800):
    t0 = time.time()
    r = subprocess.run([str(c) for c in cmd], cwd=str(KP), capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout or "", r.stderr or "", time.time() - t0


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def report(text, board):
    """A tool's printed report as the hash sees it: no wx chatter, no clock, no temp path."""
    keep = [ln.replace(str(board), "<board>") for ln in text.splitlines()
            if not re.match(r"\d\d:\d\d:\d\d: Debug: ", ln) and not ln.startswith("drc:") and "property.h" not in ln]
    return "\n".join(keep)


def main():
    bless = "--bless" in sys.argv[1:]
    rc, out, _, _ = run([KPY, "-c", "import pcbnew; print(pcbnew.Version())"])
    if rc:
        print("SKIP: pcbnew is not importable through kicad_project/tools/kpy")
        return 0
    kicad = out.strip().splitlines()[-1]
    if not shutil.which("kicad-cli"):
        print("SKIP: kicad-cli is not on PATH (place.py drc needs it)")
        return 0
    place, fanout = KP / "tools" / "place" / "place.py", KP / "tools" / "route" / "fanout.py"
    tmp = Path(tempfile.mkdtemp(prefix="pcb-large-"))
    got, secs, bad = {}, {}, []
    try:
        rc, out, err, _ = run([KPY, ROOT / "tests" / "fixture_pcb.py", tmp, "--kind", "large"])
        B = tmp / "large"
        if rc or not (B / "large.kicad_pcb").exists():
            print("FAIL: fixture_pcb.py --kind large: " + (out + err).strip()[-300:])
            return 1
        vias, layers = B / "placement" / "vias_U1.json", B / "routing" / "layers.json"
        steps = [  # name, command, the exit it must give, what is hashed
            ("labels", [KPY, place, "labels", "--board", B, "--dry-run", "--json"], 0, None),
            ("auto", [KPY, place, "auto", "--board", B, B / "placement" / "auto_spec.json"], 0,
             B / "placement" / "auto_spec.plan.json"),
            ("check", [KPY, place, "check", "--board", B, B / "placement" / "check_plan.json"], 1, "report"),
            ("drc", [KPY, place, "drc", "--board", B], 1, "report"),
            ("plan", [KPY, fanout, "plan", "--board", B, "--ref", "U1", "--via", "0.4,0.2", "--clearance", "0.0889",
                      "--width", "0.0889", "--in-pad", "^VDD", "--share", "^GND$", "--skip-rings", "1",
                      "--out", vias], 0, vias),
            ("layers", [KPY, fanout, "layers", "--board", B, "--ref", "U1", "--vias", vias,
                        "--layers", B / "routing" / "layers_spec.json", "--out", layers], 0, layers),
        ]
        for name, cmd, want_rc, what in steps:
            for f in (what,) if isinstance(what, Path) else ():
                f.unlink(missing_ok=True)                # the fixture ships its own copy of some
            rc, out, err, dt = run(cmd)
            secs[name] = round(dt, 2)
            if rc != want_rc:
                bad.append(f"{name}: exit {rc}, not {want_rc}: {report(out + err, B).strip()[-200:]}")
                continue
            if what is None:
                got[name] = sha(out)                     # --json: stdout is the result
            elif what == "report":
                got[name] = sha(report(out, B))
            else:
                got[name] = sha(what.read_text()) if what.exists() else "no file"
            print(f"  {name:7s} {dt:7.2f} s (before {BEFORE[name]:g} s, ceiling {CEILING[name]:.1f} s)  {got[name]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    times = ", ".join(f"{k} {secs[k]:g} s" for k in secs)
    if bless and not bad:
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        RECORD.write_text(json.dumps({"kicad": kicad, "sha256_16": got, "seconds_before": BEFORE,
                                      "seconds_when_recorded": secs}, indent=1) + "\n")
        print(f"PASS: recorded for KiCad {kicad} in {RECORD.relative_to(ROOT)}: {times}")
        return 0
    rec = json.loads(RECORD.read_text()) if RECORD.exists() else None
    judged = bool(rec) and rec["kicad"] == kicad
    if judged:
        bad += [f"{k}: result {got.get(k)} is not the recorded {v}" for k, v in rec["sha256_16"].items()
                if k in got and got[k] != v]
    bad += [f"{k}: {secs[k]:g} s is over its ceiling of {CEILING[k]:.1f} s (it took {BEFORE[k]:g} s before the "
            f"speed-up)" for k in secs if secs[k] > CEILING[k]]
    for b in bad:
        print("  BAD " + b)
    if bad:
        print(f"FAIL: {len(bad)} problem(s); first: {bad[0][:300]}")
        return 1
    how = ("every result the recorded one" if judged else
           f"results NOT judged (the record is for KiCad {rec['kicad']}, this is {kicad})" if rec else
           "results NOT judged (no record: --bless)")
    print(f"PASS: 326 footprints round a 536-ball BGA -- {times}; {how}; before: "
          + ", ".join(f"{k} {v:g}" for k, v in BEFORE.items()) + " s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
