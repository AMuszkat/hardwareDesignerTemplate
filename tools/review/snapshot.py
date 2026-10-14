#!/usr/bin/env python3
"""
snapshot.py -- freeze a board's drawn work and run every gate on the copy, for a review.

    python3 tools/review/snapshot.py --board board1 --kind schematic --id RV3
    python3 tools/review/snapshot.py --board board1 --kind pcb       --id RV4

The concept session runs it before a reviewer agent (.claude/agents/) looks at
a board; the `review-board` skill is the procedure, tools/review/README.md the
story. It never writes a board folder: the board is COPIED to

    out/reviews/<board>/<id>-<kind>/snap/<board folder>/     (+ snap/tools -> kicad_project/tools)

so the board session's own gates run on a frozen copy while that session may
keep working, and every number in the review names the files it came from
(manifest.json: the sha256 of every copied file). The gates are the board
session's own tools, run exactly as its skills run them:

  schematic   ERC (kicad-cli), sch.py check, sch.py parity (when a plan exists),
              sch.py parity --handoff; plus the netlist, a BOM with the fields,
              and every sheet as PDF + PNG for the reviewer to look at
  pcb         place.py drc (DRC WITH schematic parity), board_setup.py check
              (Board Setup == 14_fab), fp.py check (every footprint signed and
              current), route.py status and edge; the schematic's ERC and
              parity --handoff too (a PCB is only as right as its schematic);
              plus top / bottom / inner renders

Writes SUMMARY.md (one row per gate) beside the outputs. Exit 0 = every gate
passed, 1 = a gate failed (the review starts from it), 2 = the snapshot itself
could not be made.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KP = ROOT / "kicad_project"
TOOLS = KP / "tools"
VPY = TOOLS / ".venv" / "bin" / "python"
KPY = TOOLS / "kpy"
# Generated, backup and lock files: not the board's work, and some are large.
SKIP_RE = re.compile(r"(^~.*\.lck$|\.lck$|^fp-info-cache$|^_autosave-|-backups$|\.bak$|-bak$|\.kicad_prl$)")


def die(msg, code=2):
    print(f"snapshot: {msg}", file=sys.stderr)
    sys.exit(code)


def board_dir(arg):
    have = {m.group(1): d for d in sorted(KP.iterdir())
            if d.is_dir() and (m := re.fullmatch(r"(board\d+)_\w+", d.name))}
    if arg in have:
        return have[arg]
    p = KP / arg
    if p.is_dir() and re.fullmatch(r"board\d+_\w+", p.name):
        return p
    die(f"no board {arg!r} (have: {', '.join(have) or 'none'})")


def ignore(src, names):
    out = set()
    rel = Path(src)
    for n in names:
        p = rel / n
        if p.is_dir() and (n in ("renders", ".history", ".sch_backups", ".board_setup_backups",
                                 "__pycache__") or n.endswith("-backups")
                           or (n == "preview" and rel.name == "sch_plan")
                           or (n == "out" and rel.name == "build")):
            out.add(n)
        elif SKIP_RE.search(n):
            out.add(n)
    return out


def freeze(s, d):
    """Datasheets and 3D models are hard-linked, not copied (a board's handoff is
    100+ MB of PDFs): publish.py and the footprint tools replace such files, never
    rewrite them in place, so the link still holds the content of the moment. The
    KiCad files are copied -- an editor may rewrite them in place."""
    if Path(s).suffix.lower() in (".pdf", ".step", ".stp", ".wrl"):
        try:
            os.link(s, d)
            return d
        except OSError:
            pass
    return shutil.copy2(s, d)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class Run:
    def __init__(self, out):
        self.out, self.rows = out, []

    def gate(self, name, cmd, cwd=None, ok=None, note=""):
        """Run one gate; its whole output goes to gates/<name>.txt."""
        log = self.out / "gates" / f"{name}.txt"
        try:
            r = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True,
                               timeout=1800)
            text, code = (r.stdout or "") + (r.stderr or ""), r.returncode
        except (OSError, subprocess.TimeoutExpired) as e:
            text, code = f"could not run: {e}", -1
        log.write_text("$ " + " ".join(str(c) for c in cmd) + f"\n(exit {code})\n\n" + text)
        passed = ok(code, text) if ok else code == 0
        tail = next((ln.strip() for ln in reversed(text.strip().splitlines()) if ln.strip()), "")
        self.rows.append((name, "PASS" if passed else ("ERROR" if code == -1 else "FAIL"),
                          note or tail[:160], f"gates/{log.name}"))
        return passed, text

    def skip(self, name, why):
        self.rows.append((name, "not run", why, ""))


def erc(run, sch, out):
    j = out / "erc.json"
    run.gate("erc", ["kicad-cli", "sch", "erc", "--format", "json", "--severity-all", "-o", j, sch])
    if not j.exists():
        return
    d = json.loads(j.read_text())
    n = {"error": 0, "warning": 0}
    for s in d.get("sheets", []):
        for v in s.get("violations", []):
            n[v.get("severity", "error")] = n.get(v.get("severity", "error"), 0) + 1
    name, _, _, log = run.rows[-1]
    run.rows[-1] = (name, "PASS" if n["error"] == 0 and n["warning"] == 0 else "FAIL",
                    f"{n['error']} errors, {n['warning']} warnings (erc.json)", log)


def schematic(run, b, out, root_sch):
    erc(run, root_sch, out)
    sch = [VPY, TOOLS / "sch" / "sch.py"]
    run.gate("sch-check", sch + ["check", "--board", b],
             ok=lambda c, t: c == 0 and "CHECKS PASS" in t)
    if (b / "sch_plan" / "netlist.json").exists():
        run.gate("sch-parity", sch + ["parity", "--board", b], ok=lambda c, t: c == 0 and "PARITY OK" in t)
    else:
        run.skip("sch-parity", "no sch_plan/netlist.json: the board was not drawn from a plan")
    run.gate("handoff-parity", sch + ["parity", "--board", b, "--handoff"],
             ok=lambda c, t: c == 0 and "HANDOFF PARITY OK" in t)


def exports(run, out, root_sch):
    """What the reviewer reads rather than gates: netlist, BOM, the sheets as pictures."""
    run.gate("netlist", ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
                         "-o", out / "netlist.xml", root_sch], note="netlist.xml")
    run.gate("bom", ["kicad-cli", "sch", "export", "bom", "-o", out / "bom.csv",
                     "--fields", "Reference,Value,Footprint,MPN,DNP,${DNP},${EXCLUDE_FROM_BOM},${QUANTITY}",
                     "--labels", "Refs,Value,Footprint,MPN,DNP field,DNP flag,Excluded,Qty",
                     "--group-by", "Value,Footprint,MPN,DNP,${DNP}", root_sch], note="bom.csv")
    pdf = out / "schematic.pdf"
    run.gate("sch-pdf", ["kicad-cli", "sch", "export", "pdf", "-o", pdf, root_sch], note="schematic.pdf")
    if pdf.exists() and shutil.which("pdftoppm"):
        (out / "sheets").mkdir(exist_ok=True)
        run.gate("sch-png", ["pdftoppm", "-png", "-r", "110", pdf, out / "sheets" / "sheet"],
                 note="sheets/sheet-N.png, one per page")


def pcb(run, b, out):
    kpy = [KPY]
    run.gate("drc", kpy + [TOOLS / "place" / "place.py", "drc", "--board", b])
    run.gate("board-setup", kpy + [TOOLS / "board_setup" / "board_setup.py", "check", "--board", b])
    run.gate("footprints", [VPY, TOOLS / "fp" / "fp.py", "check", "--board", b])
    run.gate("route-status", kpy + [TOOLS / "route" / "route.py", "status", "--board", b],
             ok=lambda c, t: c in (0, 1), note="unrouted nets per group (information)")
    run.gate("route-edge", kpy + [TOOLS / "route" / "route.py", "edge", "--board", b])
    run.gate("pcb-render", kpy + [TOOLS / "route" / "route.py", "render", "--board", b,
                                  "--out", out / "pcb"], note="pcb/top.png, bottom.png, inner.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", required=True, help="boardN or the folder name")
    ap.add_argument("--kind", required=True, choices=["schematic", "pcb"])
    ap.add_argument("--id", help="the request id in the board's REVIEW.md (RVn); default: a timestamp")
    ap.add_argument("--out", help="default: out/reviews/<board>/<id>-<kind>/")
    a = ap.parse_args()
    if not shutil.which("kicad-cli"):
        die("kicad-cli is not on PATH")
    src = board_dir(a.board)
    rid = a.id or datetime.datetime.now().strftime("%Y%m%d-%H%M")
    out = Path(a.out) if a.out else ROOT / "out" / "reviews" / src.name / f"{rid}-{a.kind}"
    if out.exists():
        shutil.rmtree(out)
    (out / "gates").mkdir(parents=True)
    snap = out / "snap"
    snap.mkdir()
    b = snap / src.name
    shutil.copytree(src, b, ignore=ignore, symlinks=True, copy_function=freeze)
    os.symlink(TOOLS, snap / "tools")                    # ${KIPRJMOD}/../tools resolves
    for f in ("project.json", "local.json"):
        if (KP / f).exists():
            shutil.copy2(KP / f, snap / f)
    pros = sorted(b.glob("*.kicad_pro"))
    if len(pros) != 1:
        die(f"{src.name}: {len(pros)} .kicad_pro files -- nothing to review yet" if not pros
            else f"{src.name}: several .kicad_pro files")
    root_sch = pros[0].with_suffix(".kicad_sch")
    files = {str(p.relative_to(b)): sha(p) for p in sorted(b.rglob("*"))
             if p.is_file() and "handoff" not in p.relative_to(b).parts}
    man = b / "handoff" / "MANIFEST.json"
    rev = json.loads(man.read_text()).get("rev") if man.exists() else None
    (out / "manifest.json").write_text(json.dumps(
        {"board": src.name, "kind": a.kind, "id": rid, "handoff_rev": rev,
         "taken": datetime.datetime.now().isoformat(timespec="seconds"), "files": files}, indent=1))

    run = Run(out)
    if a.kind == "schematic":
        schematic(run, b, out, root_sch)
    else:
        erc(run, root_sch, out)
        run.gate("handoff-parity", [VPY, TOOLS / "sch" / "sch.py", "parity", "--board", b, "--handoff"],
                 ok=lambda c, t: c == 0 and "HANDOFF PARITY OK" in t)
        if not list(b.glob("*.kicad_pcb")):
            die(f"{src.name}: no .kicad_pcb -- nothing to review yet")
        pcb(run, b, out)
    exports(run, out, root_sch)

    gates = [r for r in run.rows if r[0] not in ("netlist", "bom", "sch-pdf", "sch-png", "pcb-render")]
    failed = [r for r in gates if r[1] in ("FAIL", "ERROR")]
    L = [f"# Review snapshot — {src.name}, {a.kind}, {rid}", "",
         f"Handoff rev {rev}; {len(files)} files frozen in `snap/` (sha256 in `manifest.json`); "
         f"taken {datetime.datetime.now():%Y-%m-%d %H:%M}.", "",
         "| gate | result | detail | log |", "|---|---|---|---|"]
    L += [f"| {n} | {s} | {d.replace('|', '/')} | {g} |" for n, s, d, g in run.rows]
    L += ["", f"**{len(failed)} of {len(gates)} gates failed.**" if failed else
          f"**All {len([r for r in gates if r[1] == 'PASS'])} gates that ran passed.**"]
    (out / "SUMMARY.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\n{out}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
