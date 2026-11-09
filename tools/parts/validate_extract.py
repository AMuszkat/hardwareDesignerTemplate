#!/usr/bin/env python3
"""The gate before anything reads an extracted pin table: diff it against a hand-made PINOUT CSV.

    python3 tools/parts/validate_extract.py <MPN.pins.csv> <PINOUT.csv> [--json out.json]

The PINOUT CSV is a board's published pinout (its "Pin Number" / "Pin Name"
columns, or "number" / "name"), made by hand from the same datasheet and
already reviewed: the ground truth. Every pin of it is looked up in the
extraction -- same name (case and spaces ignored), a different name, or
missing -- and every extracted pin it does not have is listed. Exit 0 only
when every PINOUT pin is extracted with its name; ROADMAP H4: no consumer
(symgen.py, fp.py verify --against, the reviewer checklist) is wired to an
extractor whose tables have not passed this on the datasheets already filed.
"""
import argparse
import csv
import json
import re
import sys


def table(path, num_keys, name_keys):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}
    keys = {k.strip().lower(): k for k in rows[0]}
    nk = next((keys[k] for k in num_keys if k in keys), None)
    mk = next((keys[k] for k in name_keys if k in keys), None)
    if not nk or not mk:
        sys.exit(f"{path}: no number / name columns (have {list(rows[0])})")
    return {r[nk].strip().upper(): r[mk].strip() for r in rows if r.get(nk, "").strip()}


def norm(s):
    return re.sub(r"\s+", "", s).upper()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("extracted")
    ap.add_argument("pinout")
    ap.add_argument("--json")
    a = ap.parse_args()
    got = table(a.extracted, ("number",), ("name",))
    want = table(a.pinout, ("pin number", "number", "pin", "ball"), ("pin name", "name", "ball name"))
    same = [n for n in want if n in got and norm(got[n]) == norm(want[n])]
    diff = [n for n in want if n in got and norm(got[n]) != norm(want[n])]
    miss = [n for n in want if n not in got]
    extra = [n for n in got if n not in want]
    print(f"{len(want)} pins in the PINOUT: {len(same)} extracted with the same name, {len(diff)} with another, "
          f"{len(miss)} missing; {len(extra)} extracted pins not in the PINOUT")
    for n in diff[:20]:
        print(f"  differs  {n}: PINOUT {want[n]!r}, extracted {got[n]!r}")
    if miss:
        print(f"  missing  {' '.join(miss[:40])}" + (" ..." if len(miss) > 40 else ""))
    if a.json:
        json.dump({"pinout": a.pinout, "extracted": a.extracted, "same": len(same), "differs": diff,
                   "missing": miss, "extra": extra}, open(a.json, "w"), indent=1)
    ok = not diff and not miss
    print("EXTRACTION MATCHES the hand-made pinout" if ok else "EXTRACTION DOES NOT MATCH -- no consumer may read it")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
