#!/usr/bin/env python3
"""The extractor's measurement: a corpus of vendor datasheets whose pins KiCad's stock symbols already hold.

    python3 tools/parts/extract_corpus.py [--out out/extract_corpus] [--per-host 3] [--seed 1] [--jobs 4] [-v]

ROADMAP H4 gates every consumer of an extracted pin table on the extractor
matching pin tables people made. One board's hand-made PINOUT is one
datasheet; KiCad's librarians have drawn thousands, each from the vendor's
own PDF, and the symbol names the PDF. So:

  pick     stock symbols with 20-120 pins and a PDF datasheet link, `--per-host`
           of them from each of ten vendors' hosts, fixed by `--seed`
  fetch    each PDF into OUT/pdf/ (curl, then the local headless Chrome of
           datasheet.py) -- never into reference/vendor/, which is the
           project's; a host that refuses both is reported and skipped
  extract  extract.py --stages tables on each (its own venv), `--jobs` at once
  grade    validate_extract.grade against the symbol, the best package of a
           multi-package table: same name / same primary name / another /
           missing, per part and in total -> OUT/report.md

Re-run after any change to extract.py's parser: the corpus is kept, so only
`extract` and `grade` repeat (`--force` re-extracts). Numbers from the run of
2026-11-09 (KiCad 10.0.7, Docling 2.149, 19 of 30 PDFs fetched): 461 of 728
pins named alike, 2 parts exact -- ROADMAP's H4 row.
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import kicad_pins as KP        # noqa: E402
import validate_extract as V   # noqa: E402

HOSTS = ["st.com", "ww1.microchip.com", "ti.com", "analog.com", "nxp.com", "silabs.com",
         "datasheets.maximintegrated.com", "ftdichip.com", "espressif.com", "issi.com"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


def pick(per_host, seed):
    rows = []
    for f in sorted(KP.symbol_dir().glob("*.kicad_sym")):
        for name, blk in KP._blocks(f.read_text(errors="replace")):
            if "(extends" in blk:
                continue
            m = re.search(r'\(property "Datasheet" "([^"]*)"', blk)
            url = m.group(1) if m else ""
            if not url.startswith("http") or not url.lower().endswith(".pdf"):
                continue
            pins = {}
            for pn, num in KP._PIN.findall(blk):
                pins.setdefault(num, pn)
            if 20 <= len(pins) <= 120:
                rows.append(dict(lib=f.stem, sym=name, url=url, pins=pins,
                                 host=re.sub(r"https?://(www\.)?([^/]+).*", r"\2", url)))
    rnd, out = random.Random(seed), []
    for h in HOSTS:
        cand = [r for r in rows if r["host"] == h]
        rnd.shuffle(cand)
        seen = set()
        for r in cand:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            out.append(r)
            if sum(1 for x in out if x["host"] == h) == per_host:
                break
    return out


def fetch(r, pdf):
    if pdf.exists():
        return "kept"
    url = r["url"].replace("http://", "https://")
    subprocess.run(["curl", "-sSL", "--max-time", "60", "-A", UA, "-o", str(pdf), url], capture_output=True)
    if pdf.exists() and pdf.read_bytes()[:5] == b"%PDF-":
        return "curl"
    pdf.unlink(missing_ok=True)
    try:
        import datasheet
        blob, why = datasheet.chrome_fetch(url, timeout=90)
    except Exception as e:                      # datasheet.py needs the concept venv's modules
        return f"refused ({type(e).__name__})"
    if blob:
        pdf.write_bytes(blob)
        return "chrome"
    return f"refused ({why})"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "out" / "extract_corpus"))
    ap.add_argument("--per-host", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="re-extract every PDF")
    ap.add_argument("-v", action="store_true", help="list each part's differing and missing pins")
    a = ap.parse_args()
    if KP.symbol_dir() is None:
        sys.exit("extract_corpus: KiCad's symbol library not found (KICAD_SYMBOL_DIR)")
    out = Path(a.out)
    (out / "pdf").mkdir(parents=True, exist_ok=True)
    pj = out / "pick.json"
    corpus = json.loads(pj.read_text()) if pj.exists() else pick(a.per_host, a.seed)
    pj.write_text(json.dumps(corpus, indent=1))
    have = []
    for r in corpus:
        pdf = out / "pdf" / (re.sub(r"[^\w.+-]", "_", r["sym"]) + ".pdf")
        how = fetch(r, pdf)
        print(f"  fetch  {r['sym']:24s} {how}")
        if pdf.exists():
            have.append((r, pdf))
    todo = [str(p) for r, p in have if a.force or not p.with_suffix(".pins.csv").exists()]
    if todo:
        print(f"  extract {len(todo)} PDF(s), {a.jobs} at once ...", flush=True)
        subprocess.run(["xargs", "-P", str(a.jobs), "-I{}", sys.executable, str(HERE / "extract.py"), "{}",
                        "--stages", "tables"] + (["--force"] if a.force else []),
                       input="\n".join(todo), text=True)
    lines = ["# Extractor vs KiCad's stock symbols", "",
             "| part | symbol | package | judged | same | same primary | another | missing |", "|---|---|---|---|---|---|---|---|"]
    tot = dict(judged=0, same=0, prim=0, diff=0, miss=0)
    exact = 0
    for r, pdf in have:
        want = {k.upper(): v for k, v in r["pins"].items()}
        csvp = pdf.with_suffix(".pins.csv")
        byp = (V.table(str(csvp), ("number",), ("name",), by_package=True) if csvp.exists() else {}) or {"": {}}
        best = sorted(((len(g[0]) + len(g[1]), pk, g) for pk, g in
                       ((pk, V.grade(want, got, True)) for pk, got in byp.items())), key=lambda x: (-x[0], x[1]))[0]
        _n, pk, (same, prim, diff, miss, _extra) = best
        j = len(same) + len(prim) + len(diff) + len(miss)
        exact += bool(j and not diff and not miss)
        for k, v in (("judged", j), ("same", len(same)), ("prim", len(prim)), ("diff", len(diff)), ("miss", len(miss))):
            tot[k] += v
        lines.append(f"| {r['sym']} | {r['lib']} | {pk or '-'} | {j} | {len(same)} | {len(prim)} | {len(diff)} | {len(miss)} |")
        print(f"  grade  {r['sym']:24s} {len(same) + len(prim):3d}/{j:3d} named alike, {len(diff)} another, {len(miss)} missing"
              + (f"  [{pk}]" if pk else ""))
        if a.v:
            got = byp[pk]
            for n in diff[:8]:
                print(f"           differs {n}: KiCad {want[n]!r}, extracted {got[n]!r}")
            if miss:
                print(f"           missing {' '.join(miss[:24])}")
    alike = tot["same"] + tot["prim"]
    summary = (f"{len(have)} of {len(corpus)} PDFs fetched; {alike} of {tot['judged']} pins named alike "
               f"({tot['same']} same, {tot['prim']} same primary), {tot['diff']} another, {tot['miss']} missing; "
               f"{exact} part(s) exact")
    lines += ["", summary, ""]
    (out / "report.md").write_text("\n".join(lines))
    print(summary + f" -> {out / 'report.md'}")


if __name__ == "__main__":
    main()
