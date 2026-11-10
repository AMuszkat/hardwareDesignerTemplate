#!/usr/bin/env python3
"""fabpkg.py -- the fabrication package of a released board, from kicad-cli, checked.

    tools/kpy tools/fab/fabpkg.py --board board2 [--out DIR] [--force-unreleased]

A board session's deliverable is "the fabrication package (Gerbers, drill files, outline,
the order notes quoted from 14_fab.md) and a DRC-clean board at those rules"; the order is
the user's.  This makes it, the same way every time, into <board>/fab/<pcb>-<date>/:

  gerbers/     every ENABLED copper layer, F/B mask, F/B silk, F/B paste, Edge.Cuts
               (Protel extensions, X2, zones refilled -- `--check-zones`), and the drills:
               Excellon, PTH and NPTH apart, mm, with a Gerber X2 drill map and a report
  <pcb>-gerbers.zip   the gerbers/ folder, what a fab's upload form takes
  <pcb>-bom.csv       grouped by Value / Footprint / MPN, every designator spelt out (no
                      "H1-H4" range: an assembler reads a list), DNP left out
  <pcb>-pos.csv       placement, both sides, mm, DNP left out
  ORDER_NOTES.md      14_fab.md's order-form table and its "Order notes" block, VERBATIM,
                      with the handoff revision and 14_fab's hash, the files, the gates

It REFUSES:
  - unless the board's last "fab release" review in handoff/15_reviews.md is PASS
    (`--force-unreleased` makes a draft whose ORDER_NOTES say so in its first line);
  - unless `place.py drc` exits 0 (violations none, unconnected 0, parity 0, silk).
After writing, it checks what it wrote: one Gerber per layer asked for, both drill files,
a BOM line with an MPN for every fitted part (a blank MPN lets the assembler substitute:
board2 RV23.4), and a placement row for every fitted footprint.  Exit 0 only when all hold.
"""
import argparse
import csv
import datetime
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "place"))
import place as P  # noqa: E402


def die(msg, code=2):
    print(f"fabpkg: {msg}", file=sys.stderr)
    sys.exit(code)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"{' '.join(cmd[:4])} ... failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-400:]}", 3)
    return r.stdout


def released(handoff):
    """(ok, line): the LAST review heading that names a fab release, and whether it is PASS."""
    f = handoff / "15_reviews.md"
    if not f.exists():
        return False, "no handoff/15_reviews.md"
    heads = [ln.strip() for ln in f.read_text().splitlines()
             if ln.startswith("## ") and "fab release" in ln.lower()]
    if not heads:
        return False, "no fab-release review in 15_reviews.md"
    return bool(re.search(r"\bPASS\s*$", heads[-1])), heads[-1]


def section(text, title):
    """A '## title' section of a markdown page, up to the next '## '."""
    m = re.search(r"^## " + re.escape(title) + r".*?$(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    return m.group(0).rstrip() if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board")
    ap.add_argument("--out", help="default <board>/fab/<pcb>-<YYYYMMDD>")
    ap.add_argument("--force-unreleased", action="store_true")
    a = ap.parse_args()
    bdir = Path(P.board_dir_arg(a.board))
    B = P.Board(bdir)
    pcb = Path(B.pcb)
    sch = pcb.with_suffix(".kicad_sch")
    handoff = bdir / "handoff"
    stem = pcb.stem

    ok, line = released(handoff)
    if not ok and not a.force_unreleased:
        die(f"not released: {line} -- request 'pcb: fab release' in REVIEW.md first", 2)
    r = subprocess.run([str(ROOT / "tools" / "kpy"), str(ROOT / "tools" / "place" / "place.py"), "drc",
                        "--board", str(bdir)], capture_output=True, text=True)
    drc_lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if r.returncode != 0:
        die("place.py drc is not clean:\n  " + "\n  ".join(drc_lines), 2)

    out = Path(a.out) if a.out else bdir / "fab" / f"{stem}-{datetime.date.today():%Y%m%d}"
    if out.exists() and any(out.iterdir()):
        die(f"{out} exists and is not empty: give --out, or remove it", 2)
    g = out / "gerbers"
    g.mkdir(parents=True, exist_ok=True)

    pn, b = B.pn, B.b
    cu = [b.GetLayerName(l) for l in b.GetEnabledLayers().CuStack()]
    layers = cu + ["F.Mask", "B.Mask", "F.Silkscreen", "B.Silkscreen", "F.Paste", "B.Paste", "Edge.Cuts"]
    run(["kicad-cli", "pcb", "export", "gerbers", "--layers", ",".join(layers), "--check-zones",
         "--subtract-soldermask", "-o", str(g) + "/", str(pcb)])
    run(["kicad-cli", "pcb", "export", "drill", "--format", "excellon", "--excellon-units", "mm",
         "--excellon-separate-th", "--generate-map", "--map-format", "gerberx2", "--generate-report",
         "--report-path", str(g / f"{stem}-drill-report.txt"), "-o", str(g) + "/", str(pcb)])
    bom = out / f"{stem}-bom.csv"
    run(["kicad-cli", "sch", "export", "bom", "--fields", "Reference,Value,Footprint,MPN,${QUANTITY}",
         "--labels", "Reference,Value,Footprint,MPN,Qty", "--group-by", "Value,Footprint,MPN",
         "--ref-range-delimiter", "", "--exclude-dnp", "-o", str(bom), str(sch)])
    pos = out / f"{stem}-pos.csv"
    run(["kicad-cli", "pcb", "export", "pos", "--side", "both", "--format", "csv", "--units", "mm",
         "--exclude-dnp", "-o", str(pos), str(pcb)])
    z = out / f"{stem}-gerbers.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(g.iterdir()):
            zf.write(f, f.name)

    # --- what was written, checked
    bad = []
    gbr = sorted(p.name for p in g.iterdir() if p.suffix.lower() not in (".drl", ".txt", ".gbrjob"))
    want = len(layers)
    layer_files = [n for n in gbr if "drl_map" not in n.lower() and "-drl" not in n.lower()
                   and "_map" not in n.lower()]
    if len(layer_files) != want:
        bad.append(f"{len(layer_files)} layer Gerber(s), {want} asked for: {layer_files}")
    drl = sorted(p.name for p in g.iterdir() if p.suffix.lower() == ".drl")
    if not any("NPTH" in n for n in drl) or not any("PTH" in n and "NPTH" not in n for n in drl):
        bad.append(f"drill files: {drl} (PTH and NPTH both expected)")
    fitted = sorted(f.GetReference() for f in b.Footprints()
                    if not f.IsDNP() and not f.IsExcludedFromBOM() and not f.GetReference().startswith("#"))
    rows = list(csv.DictReader(bom.open()))
    in_bom = {r_.strip() for row in rows for r_ in row["Reference"].split(",")}
    no_mpn = [row["Reference"] for row in rows if not row.get("MPN", "").strip()]
    if no_mpn:
        bad.append(f"BOM lines with no MPN (the assembler would choose the part): {no_mpn}")
    fit_bom = {f.GetReference() for f in b.Footprints() if not f.IsDNP() and not f.IsExcludedFromBOM()}
    missing_bom = sorted(fit_bom - in_bom)
    if missing_bom:
        bad.append(f"fitted footprints not in the BOM: {missing_bom}")
    prow = list(csv.DictReader(pos.open()))
    in_pos = {row.get("Ref", row.get("Designator", "")) for row in prow}
    fit_pos = {f.GetReference() for f in b.Footprints() if not f.IsDNP() and not f.IsExcludedFromPosFiles()}
    missing_pos = sorted(fit_pos - in_pos)
    if missing_pos:
        bad.append(f"fitted footprints not in the placement file: {missing_pos}")

    # --- the order notes, quoted from 14_fab.md
    fab = (handoff / "14_fab.md").read_text()
    man = json.loads((handoff / "MANIFEST.json").read_text()) if (handoff / "MANIFEST.json").exists() else {}
    rev = man.get("revision", man.get("rev", "?"))
    h14 = hashlib.sha256((handoff / "14_fab.md").read_bytes()).hexdigest()[:16]
    head = fab.split("\n## ", 1)[0].strip()
    form = section(fab, "What to choose on the order form") or "(14_fab.md has no order-form section)"
    notes = [
        ("**DRAFT -- NOT RELEASED** (" + line + ")\n\n") if not ok else "",
        f"# {stem} -- fabrication package, {datetime.date.today():%Y-%m-%d}\n\n",
        "**The order is the user's.** Nothing here was sent anywhere.\n\n",
        f"- Released by: `{line}` (handoff/15_reviews.md)\n",
        f"- Handoff revision {rev}; 14_fab.md sha256 {h14}\n",
        "- Gates at packaging (`place.py drc`):\n" + "".join(f"  - {ln.strip()}\n" for ln in drc_lines) + "\n",
        "## Files\n\n",
        f"- `{z.name}`: upload this ({len(layer_files)} layer Gerbers, {len(drl)} drill files, drill map, report)\n",
        f"- `{bom.name}`: the BOM, {len(rows)} lines, {len(in_bom)} parts, DNP left out\n",
        f"- `{pos.name}`: placement, {len(prow)} rows, both sides, mm, DNP left out\n",
        "- `gerbers/`: the same files unzipped\n\n",
        "## From 14_fab.md, verbatim\n\n",
        head + "\n\n",
        form + "\n",
    ]
    (out / "ORDER_NOTES.md").write_text("".join(notes))

    print(f"wrote {out}")
    print(f"  {z.name}: {len(layer_files)} layers + {len(drl)} drill files")
    print(f"  {bom.name}: {len(rows)} lines / {len(in_bom)} parts;  {pos.name}: {len(prow)} rows")
    print(f"  ORDER_NOTES.md: 14_fab's order form and notes, rev {rev}")
    for x in bad:
        print(f"  FAIL {x}")
    print("FABPKG " + ("PASS" if not bad else f"FAIL ({len(bad)})") + ("" if ok else " -- DRAFT, not released"))
    return 0 if not bad and ok else 1


if __name__ == "__main__":
    sys.exit(main())
