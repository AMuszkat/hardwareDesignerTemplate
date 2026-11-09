#!/usr/bin/env python3
"""The gate before anything reads an extracted pin table: diff it against a pin table made by people.

    python3 tools/parts/validate_extract.py <MPN.pins.csv> <PINOUT.csv> [--json out.json]
    python3 tools/parts/validate_extract.py <MPN.pins.csv> --kicad <MPN> [--json out.json]

Two ground truths, each made by hand from the same datasheet and reviewed:

  * a board's published PINOUT CSV (its "Pin Number" / "Pin Name" columns, or
    "number" / "name") -- exact: every pin must be extracted with its name;
  * KiCad's own stock symbol for the part (tools/parts/kicad_pins.py), for the
    many parts no board has pinned out by hand. A librarian writes names in
    KiCad's notation (~{RESET} for an overbar, PB0/ADC0 for alternates), so a
    name also counts when its primary name agrees ("same primary"), and a
    symbol pin with no name is not judged.

Every ground-truth pin is looked up in the extraction -- same name (case and
spaces ignored), a different name, or missing -- and every extracted pin it
does not have is listed. Exit 0 only when every pin is extracted with its
name; ROADMAP H4: no consumer (symgen.py, fp.py verify --against, the
reviewer checklist) is wired to an extractor whose tables have not passed
this on a corpus of datasheets.
"""
import argparse
import csv
import json
import re
import sys


def table(path, num_keys, name_keys, by_package=False):
    """{number: name}; with by_package, {package: {number: name}} (an extraction's "package" column: one table
    for several packages gives one set of pins each; "" when it has one)"""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}
    keys = {k.strip().lower(): k for k in rows[0]}
    nk = next((keys[k] for k in num_keys if k in keys), None)
    mk = next((keys[k] for k in name_keys if k in keys), None)
    if not nk or not mk:
        sys.exit(f"{path}: no number / name columns (have {list(rows[0])})")
    if not by_package:
        return {r[nk].strip().upper(): r[mk].strip() for r in rows if r.get(nk, "").strip()}
    pk = keys.get("package")
    out = {}
    for r in rows:
        if r.get(nk, "").strip():
            out.setdefault((r.get(pk) or "").strip() if pk else "", {})[r[nk].strip().upper()] = r[mk].strip()
    return out


def norm(s):
    return re.sub(r"\s+", "", s).upper()


def knorm(s):
    """a pin name with its notation taken out: KiCad's ~{..} overbar, a /, # or n-prefix-free overbar mark,
    combining overlines, separators and case"""
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = s.replace("~{", "").replace("}", "").replace("~", "")
    s = re.sub(r"^[/!]", "", s.strip())
    s = re.sub(r"[#*]$", "", s)
    return re.sub(r"[\s_\-]", "", s).upper()


def primary(s):
    """the first name of a pin that has several (PB0/ADC0, GPIO4 (ADC1_CH3), ...)"""
    return knorm(re.split(r"[/,(\s]", s.replace("~{", "").replace("}", "").strip().lstrip("/"), maxsplit=1)[0])


def grade(want, got, kicad):
    """(same, primary, differs, missing, extra) of the ground truth `want` against `got`"""
    same, prim, diff, miss = [], [], [], []
    for n, w in want.items():
        if kicad and not w.strip():
            continue                                   # an unnamed symbol pin is not a ground truth
        if n not in got:
            miss.append(n)
        elif (knorm if kicad else norm)(got[n]) == (knorm if kicad else norm)(w):
            same.append(n)
        elif kicad and (primary(got[n]) == primary(w) or knorm(got[n]) in knorm(w).split("/")):
            prim.append(n)
        else:
            diff.append(n)
    extra = [n for n in got if n not in want]
    return same, prim, diff, miss, extra


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("extracted")
    ap.add_argument("pinout", nargs="?")
    ap.add_argument("--kicad", metavar="MPN", help="the ground truth is KiCad's stock symbol for MPN")
    ap.add_argument("--json")
    a = ap.parse_args()
    if bool(a.pinout) == bool(a.kicad):
        ap.error("a PINOUT CSV or --kicad MPN, one of the two")
    by_pkg = table(a.extracted, ("number",), ("name",), by_package=True)
    if a.kicad:
        import kicad_pins
        r = kicad_pins.find(a.kicad)
        if not r:
            sys.exit(f"validate_extract: no KiCad stock symbol for {a.kicad}")
        want = {k.strip().upper(): v for k, v in r[2].items()}
        truth = f"KiCad {r[0]}:{r[1]}"
    else:
        want = table(a.pinout, ("pin number", "number", "pin", "ball"), ("pin name", "name", "ball name"))
        truth = "the PINOUT"
    # a datasheet for several packages: the one whose pins agree best is the one this ground truth draws
    scored = sorted(((len(g[0]) + len(g[1]), pk, g) for pk, g in
                     ((pk, grade(want, got_, bool(a.kicad))) for pk, got_ in by_pkg.items())), key=lambda x: (-x[0], x[1]))
    if not scored:
        scored = [(0, "", grade(want, {}, bool(a.kicad)))]
    _n, pkg, (same, prim, diff, miss, extra) = scored[0]
    got = by_pkg.get(pkg, {})
    if len(by_pkg) > 1:
        print(f"package: {pkg or '(unlabelled)'} -- the best of {len(by_pkg)} ({'; '.join(sorted(k or '(unlabelled)' for k in by_pkg))})")
    judged = len(same) + len(prim) + len(diff) + len(miss)
    print(f"{judged} pins in {truth}: {len(same)} extracted with the same name"
          + (f", {len(prim)} with the same primary name" if a.kicad else "")
          + f", {len(diff)} with another, {len(miss)} missing; {len(extra)} extracted pins not in it")
    for n in diff[:20]:
        print(f"  differs  {n}: {truth} {want[n]!r}, extracted {got[n]!r}")
    if miss:
        print(f"  missing  {' '.join(miss[:40])}" + (" ..." if len(miss) > 40 else ""))
    if a.json:
        json.dump({"truth": truth, "extracted": a.extracted, "package": pkg, "judged": judged, "same": len(same), "primary": len(prim),
                   "differs": {n: [want[n], got[n]] for n in diff}, "missing": miss, "extra": extra},
                  open(a.json, "w"), indent=1)
    ok = judged > 0 and not diff and not miss
    print(f"EXTRACTION MATCHES {truth}" if ok else "EXTRACTION DOES NOT MATCH -- no consumer may read it")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
