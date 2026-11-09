#!/usr/bin/env python3
"""A part's pins as KiCad's own stock symbol library has them -- a second ground truth for extracted pin tables.

    python3 tools/parts/kicad_pins.py <MPN> [--csv out.csv]     # which stock symbol, and its pins
    python3 tools/parts/kicad_pins.py --list MPN,MPN,...         # which MPNs have one

KiCad's symbols are drawn by its librarians from the vendor's datasheet and reviewed
before they ship, so for a part with one, its pin numbers and names are an
independent reading of the same page an extractor reads: validate_extract.py
--kicad diffs an extracted <MPN>.pins.csv against it (ROADMAP H4).

The match is by name: a stock symbol whose name, read with KiCad's lowercase
`x` as any one character, is a prefix of the MPN (symbols omit the package,
temperature and reel suffixes), at least 5 characters and half the MPN long --
the longest wins; else, for an MPN of 6 or more that names a family
(TLV733P), the shortest member whose name it heads. A derived symbol
(`extends`) takes its parent's pins.
KICAD_SYMBOL_DIR (or KICAD10/9/8_SYMBOL_DIR) points at the library.
"""
import argparse
import csv
import os
import re
import sys
from pathlib import Path


def symbol_dir():
    for env in ("KICAD_SYMBOL_DIR", "KICAD10_SYMBOL_DIR", "KICAD9_SYMBOL_DIR", "KICAD8_SYMBOL_DIR"):
        if os.environ.get(env) and Path(os.environ[env]).is_dir():
            return Path(os.environ[env])
    for p in (Path("/usr/share/kicad/symbols"), Path("/usr/local/share/kicad/symbols"),
              Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")):
        if p.is_dir():
            return p
    return None


_TOP = re.compile(r'^\t\(symbol "([^"]+)"', re.M)
_PIN = re.compile(r'\(pin\s+\w+\s+\w+.*?\(name\s+"((?:[^"\\]|\\.)*)".*?\(number\s+"((?:[^"\\]|\\.)*)"', re.S)


def _blocks(text):
    """(name, block text) for every top-level symbol of a .kicad_sym"""
    starts = [(m.start(), m.group(1)) for m in _TOP.finditer(text)]
    for k, (at, name) in enumerate(starts):
        yield name, text[at:starts[k + 1][0] if k + 1 < len(starts) else len(text)]


def library():
    """{symbol name: (library, parent or None, {number: name})}"""
    d = symbol_dir()
    if d is None:
        return {}
    lib = {}
    for f in sorted(d.glob("*.kicad_sym")):
        text = f.read_text(errors="replace")
        for name, blk in _blocks(text):
            m = re.search(r'\(extends\s+"([^"]+)"\)', blk)
            pins = {}
            for pn, num in _PIN.findall(blk):
                pins.setdefault(num, pn)
            lib[name] = (f.stem, m.group(1) if m else None, pins)
    return lib


def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _pattern(sym):
    """a symbol name as a prefix regex: KiCad's lowercase x is any one character"""
    out = []
    for ch in sym:
        if ch == "x":
            out.append(".")
        elif ch.isalnum():
            out.append(re.escape(ch.upper()))
    return "".join(out)


def find(mpn, lib=None):
    """(library, symbol, {number: name}) for the stock symbol of `mpn`, or None"""
    lib = library() if lib is None else lib
    m = _norm(mpn)
    best = None
    for name, (libname, parent, pins) in lib.items():
        pat = _pattern(name)
        n = len(pat.replace("\\", ""))
        if n >= 5 and n >= len(m) / 2 and re.match(pat, m):
            score = (2, n)                                  # the symbol's name heads the MPN
        elif len(m) >= 6 and re.match(re.escape(m), _norm(name)):
            score = (1, -n)                                 # the MPN is a family the symbol belongs to: shortest member
        else:
            continue
        if best is None or score > best[0] or (score == best[0] and name < best[2]):
            best = (score, libname, name)
    if not best:
        return None
    _n, libname, name = best
    pins, seen, cur = {}, set(), name
    while cur and cur in lib and cur not in seen:          # a derived symbol: its parent's pins
        seen.add(cur)
        _l, parent, p = lib[cur]
        if p:
            pins = p
            break
        cur = parent
    return libname, name, pins


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mpn", nargs="?")
    ap.add_argument("--csv", help="write the pins as number,name")
    ap.add_argument("--list", help="comma-separated MPNs: print the ones with a stock symbol")
    a = ap.parse_args()
    if symbol_dir() is None:
        sys.exit("kicad_pins: KiCad's symbol library not found (set KICAD_SYMBOL_DIR)")
    lib = library()
    if a.list:
        for mpn in a.list.split(","):
            r = find(mpn, lib)
            if r:
                print(f"{mpn}\t{r[0]}:{r[1]}\t{len(r[2])} pins")
        return
    if not a.mpn:
        ap.error("an MPN, or --list")
    r = find(a.mpn, lib)
    if not r:
        sys.exit(f"kicad_pins: no stock symbol for {a.mpn}")
    print(f"{a.mpn}: {r[0]}:{r[1]}, {len(r[2])} pins")
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["number", "name"])
            for k, v in r[2].items():
                w.writerow([k, v])


if __name__ == "__main__":
    main()
