#!/usr/bin/env python3
"""Datasheet extraction, once per MPN, beside the filed PDF -- candidates, never authority.

    python3 tools/parts/extract.py <MPN | path.pdf> [--stages tables,pages] [--pages 70-90] [--force]
    python3 tools/parts/extract.py --check          # which stages this machine can run

Runs in its own venv, tools/parts/.venv-ml (`./setup.sh install-ml`; torch-sized,
optional): launched from any other Python it re-runs itself there, and without
the venv it says so in one line and exits 0 -- filing a datasheet never depends
on it. `datasheet.py` runs it after filing a PDF.

Two stages, each skipped with one line when its tool is absent:

  tables  Docling, text-layer mode (no OCR: a vendor datasheet is born
          digital, its text layer is exact; a scan is skipped with one line)
          -> <MPN>.tables.json (every table: page, bounding box, header, rows)
          and <MPN>.pins.csv (number, name, page, package: from every table
          whose stacked header names a pin / ball / package column and a name
          / signal column; one table for several packages gives one set of
          pins each). Past 40 pages only the pin-table pages the text layer
          shows (--pages auto)
  pages   the text layer as a page index -> <MPN>.pages.json: the best pages
          for "pin assignment", "recommended land pattern", "absolute maximum
          ratings", "package outline", so a reader opens two pages, not two
          hundred. No model.

Tried and dropped (2026-11-10): ColQwen2.5 as the page index (606 s for 33
pages on CPU, the same answers as the text layer in a second) and
Qwen2.5-VL-7B through Ollama reading the land-pattern page (on the iGPU 1 to 2
minutes a page; it listed dimension callouts as pads, took 4.3 for the pitch
of 0.4, and on four other pages returned pad counts of 14 digits). Claude reads
the page itself with the Read tool's `pages`, seconds and right -- the land
stays the vendor's page, which fp.py signs against.

The outputs sit beside the PDF in reference/vendor/<mfr>/, and publish.py copies
and hashes them into handoff/datasheets/ with it. NO CONSUMER READS THEM until
`validate_extract.py` has diffed the extracted pin tables against the hand-made
PINOUT CSVs already published and KiCad's stock symbols (extract_corpus.py),
and they matched (ROADMAP H4; 2026-11-09 they do not yet). Licences: Docling
(MIT) only.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VENV = HERE / ".venv-ml" / "bin" / "python"
INDEX = ROOT / "reference" / "vendor" / "datasheets.json"
NUM_HDR = re.compile(r"^(pin|ball|pad|terminal)s?(\(s\))?\s*(no\.?|number|#|num|position(\(s\))?s?)?$|^(no\.?|number|#)$",
                     re.I)
NAME_HDR = re.compile(r"name|signal|symbol|mnemonic|port\s*pin", re.I)
PIN_TOKEN = re.compile(r"\b[A-Z]{0,3}\d{1,4}\b")


PKG_HDR = re.compile(r"\b(\d+\s*-?\s*(pin|ld|lead|ball|bump)s?\b|\d+\s+[A-Z]*(BGA|QFP|QFN)\b"
                     r"|\b[A-Z]*(QFN|QFP|LQFP|TQFP|SOIC|SOP|SSOP|TSSOP|MSOP|PDIP|SPDIP|DIP|BGA|LGA|WLCSP|CSP|DFN|SON|PLCC)"
                     r"[-\dA-Z]*)")
RANGE = re.compile(r"^([A-Za-z_]+?)\s*(\d+)\s*[-–—]\s*(?:\1)?\s*(\d+)$")
NUMRANGE = re.compile(r"^(\d{1,3})\s*[-\u2013\u2014]\s*(\d{1,3})$")       # "2-7": pins 2 to 7


def pin_columns(row):
    """(number column, name column) of a header row, or None."""
    cells = [c.strip() for c in row]
    ni = next((i for i, h in enumerate(cells) if NUM_HDR.match(h)), None)
    mi = next((i for i, h in enumerate(cells) if i != ni and NAME_HDR.search(h)), None)
    return (ni, mi) if ni is not None and mi is not None else None


def pin_layout(grid):
    """(number columns {column: package}, name column, first data row) of a pin table whose header may take up to
    three stacked rows ("Pin Number" over "QFN" | "TQFP"), or None.  A number column is one whose stacked header
    names a pin / ball number or a package; several are one table for several packages, each its own package."""
    first = None
    for depth in (1, 2, 3):
        if len(grid) <= depth:
            break
        stack = [" ".join(grid[r][c].strip() for r in range(depth) if c < len(grid[r]) and grid[r][c].strip())
                 for c in range(len(grid[0]))]
        nums = {}
        for c, h in enumerate(stack):
            words = h.lower()
            if NUM_HDR.match(h) or re.search(r"\b(pin|ball|pad|terminal)s?\s*(no\.?|number|#|num)", words) or PKG_HDR.search(h):
                nums[c] = h
        name = next((c for c, h in enumerate(stack) if c not in nums and NAME_HDR.search(h)), None)
        if nums and name is not None:
            # the package of each number column: its header less the words every number column shares
            common = set.intersection(*[set(h.split()) for h in nums.values()]) if len(nums) > 1 else set(nums[next(iter(nums))].split())
            pk = {c: " ".join(w for w in h.split() if w not in common) for c, h in nums.items()}
            if len(pk) == 1 or len(set(pk.values())) == len(pk):
                return pk, name, depth
            first = first or (pk, name, depth)         # the packages' own labels sit a row lower: look deeper
    return first


def _pairs(nums, name):
    """[(number, name)] of one row"""
    names = name.split()
    m = RANGE.match(name.replace(" ", " "))
    if len(nums) > 1 and m:
        lo, hi = int(m.group(2)), int(m.group(3))
        seq = list(range(lo, hi + 1)) if lo <= hi else list(range(lo, hi - 1, -1))
        if len(seq) == len(nums):                        # DIG 0-DIG 7 over eight pins
            return [(n_, f"{m.group(1)}{k}") for n_, k in zip(nums, seq)]
    if len(names) > 1 and "/" in names[0] and all(w in names[0].split("/") for w in names[1:]):
        name = names[0]                                  # "MCLR/VPP/RE3 MCLR VPP": the composite, then its parts
        names = [name]
    if len(nums) > 1 and len(names) == len(nums):
        # rows the extractor merged into one ("NAND_DATA05 NAND_DATA06 NAND_DATA07" over
        # "L26 K26 N26"): paired in order; a supply's one name over many balls stays one
        return list(zip(nums, names))
    return [(n_, name) for n_ in nums]


def pin_rows(tables):
    """{(package, number): (name, page)} from every pin / ball table. Its header may take stacked rows (a row of
    group labels, "Pin Number" over one column per package); a table for several packages gives one set of pins
    per package ("" when it has one); a table continued on the next page carries no header and takes the previous
    one's columns; one cell may list several balls ("A1, A5, B9" -- a supply's); a "-" is a pin that package has
    not."""
    pins, prev = {}, None
    for t in tables:
        grid = [t["header"]] + t["rows"]
        lay = pin_layout(grid)
        if not lay and prev and len(grid[0]) == prev[1] and t["page"] in (prev[2], prev[2] + 1):
            lay = (prev[0][0], prev[0][1], 0)            # a continuation: no header of its own
        if not lay:
            prev = None
            continue
        prev = ((lay[0], lay[1]), len(grid[0]), t["page"])
        numcols, mi, start = lay
        one = len(numcols) == 1
        for r in grid[start:]:
            if len(r) <= max([mi, *numcols]):
                continue
            # a footnote mark printed after a name ("SAI5_MCLK 3") is not part of it -- unless the name is a range
            raw = r[mi].strip()
            name = raw if RANGE.match(raw) else re.sub(r"\s+\d{1,2}$", "", raw)
            if not name or NUM_HDR.match(name) or NAME_HDR.fullmatch(name):
                continue
            for ni, pkg in numcols.items():
                nr = NUMRANGE.match(r[ni].strip())
                nums = ([str(k) for k in range(int(nr.group(1)), int(nr.group(2)) + 1)]
                        if nr and int(nr.group(1)) < int(nr.group(2)) <= int(nr.group(1)) + 64 else PIN_TOKEN.findall(r[ni]))
                for num, nm in _pairs(nums, name):
                    if nm and not NUM_HDR.match(nm):
                        pins.setdefault(("" if one else pkg, num), (nm, t["page"]))
    return pins


def in_venv():
    return Path(sys.prefix).resolve() == (HERE / ".venv-ml").resolve()


def reexec():
    if in_venv():
        return
    if not VENV.exists():
        print("extract: skipped -- no tools/parts/.venv-ml (optional; ./setup.sh install-ml makes it)")
        sys.exit(0)
    os.execv(str(VENV), [str(VENV), "-I", str(Path(__file__).resolve()), *sys.argv[1:]])


def have(mod):
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def resolve(arg):
    p = Path(arg)
    if p.suffix.lower() == ".pdf" and p.exists():
        return p.resolve(), p.stem
    ix = json.loads(INDEX.read_text()) if INDEX.exists() else {}
    m = ix.get(arg)
    if not m:
        sys.exit(f"extract: {arg} is neither a PDF nor an MPN in {INDEX.relative_to(ROOT)}")
    return (ROOT / m["path"]).resolve(), arg


def npages(pdf):
    try:
        import pymupdf as fitz
        return len(fitz.open(str(pdf)))
    except Exception:
        return 0


def page_range(spec):
    if not spec:
        return None
    a, _, b = spec.partition("-")
    return (int(a), int(b or a))


# -- tables (Docling) ---------------------------------------------------------

AUTO_OVER = 40          # a datasheet longer than this gets --pages auto by default
_PINKEY = re.compile(r"pin\s*(configuration|description|assignment|function|definition|out\b|list|name|no\b|no\.|number|#)"
                     r"|ball\s*(map|name|assignment|out)|terminal\s*(functions|configuration)|signal\s*description"
                     r"|pinout|pin-out", re.I)
_PINNAME = re.compile(r"\b(V(DD|CC|SS|EE|BAT|IN|REF)[A-Z0-9_]*|A?GND[A-Z0-9_]*|P[A-K]\d{1,2}|R[A-K]\d|GPIO\d+|IO\d+"
                      r"|N\.?C\.?|RESET|RST|XTAL\d?|OSC\d?|SDA|SCL|MISO|MOSI|SCK|TXD?\d?|RXD?\d?|EN|EP|PAD)\b")
_PINLIKE = re.compile(r"\b(?:[A-Z][A-Z0-9]*_[A-Z0-9_]+|V(?:DD|SS|CC|EE)[A-Z0-9_]*|A?GND[A-Z0-9_]*|[A-Z]{1,3}\d{1,3}(?:[./]\w+)*|NC)\b")


def auto_pages(pdf, cap=30):
    """(pages, why): the 1-based pages of the pin tables, found in the text layer -- a page that names one (a
    heading such as 'Pin Descriptions', 'Terminal Functions', 'Ball Map') with 4 or more common pin names (VDD,
    GND, PA0, RESET, SDA) or 15 or more pin-like tokens (SAI5_MCLK, VDD_SOC, A12, NC), every page that follows
    a chosen one with 8 or 40 (a long table runs on), and up to two before it as dense (a table that starts above
    the heading of the next: i.MX 8M Mini's supply rails, p75); the strongest first, at most `cap`.  ([], why) when nothing qualifies; None when the PDF has no
    text layer at all (a scan: Docling here runs without OCR)."""
    import pymupdf as fitz
    doc = fitz.open(str(pdf))
    sc, blank = [], 0
    for pg in doc:
        t = pg.get_text()
        blank += len(t.strip()) < 40
        sc.append((len(_PINKEY.findall(t)), len(_PINNAME.findall(t)), len(_PINLIKE.findall(t))))
    if blank == len(sc):
        return None, "no text layer (a scanned PDF)"
    head = lambda x: sc[x][0] and (sc[x][1] >= 4 or sc[x][2] >= 15)
    more = lambda x: sc[x][1] >= 8 or sc[x][2] >= 40
    runs, i = [], 0
    while i < len(sc):
        if head(i):
            run = [i]
            b = i                                       # a table may start a page or two before its heading's page
            while b - 1 >= 0 and i - (b - 1) <= 2 and more(b - 1) and not any(b - 1 in r_ for r_ in runs):
                b -= 1
                run.insert(0, b)
            while i + 1 < len(sc) and more(i + 1):
                i += 1
                run.append(i)
            runs.append(run)
        i += 1
    pick = []
    for run in sorted(runs, key=lambda r: (-sum(sc[x][0] * (sc[x][1] + sc[x][2]) for x in r) / len(r), r[0])):
        for x in run:
            if len(pick) < cap:
                pick.append(x)
    if not pick:
        return [], "no page of its text layer names a pin table"
    return sorted(x + 1 for x in pick), f"{len(pick)} of {len(sc)} pages"


def _subset(pdf, pages, tmpdir):
    """a PDF of just these 1-based pages, and the map from its page numbers back to the original's"""
    import pymupdf as fitz
    src, dst = fitz.open(str(pdf)), fitz.open()
    for p in pages:
        dst.insert_pdf(src, from_page=p - 1, to_page=p - 1)
    out = Path(tmpdir) / (pdf.stem + ".pages.pdf")
    dst.save(str(out))
    return out, {k + 1: p for k, p in enumerate(pages)}


def stage_tables(pdf, out_stem, pages):
    import tempfile
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    note, back, src = "", None, pdf
    tmp = tempfile.TemporaryDirectory(prefix="extract-")
    if pages in ("auto", None) and have("fitz"):
        sel, why = auto_pages(pdf)
        if sel is None:                       # a scan: nothing for a text-layer reader, auto or whole
            tmp.cleanup()
            return f"skipped -- {why}: Docling runs without OCR here"
        if pages == "auto":
            if not sel:
                tmp.cleanup()
                return f"0 tables, 0 pins -- {why} (--pages A-B names them)"
            src, back = _subset(pdf, sel, tmp.name)
            note = f" (pages {_ranges(sel)}: {why})"
    if pages == "auto":
        pages = None
    opts = PdfPipelineOptions()
    opts.do_ocr = False                       # born-digital: the text layer is exact
    opts.do_table_structure = True
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    kw = {"page_range": pages} if pages else {}
    doc = conv.convert(str(src), **kw).document
    tmp.cleanup()
    tables = []
    for t in doc.tables:
        prov = t.prov[0] if t.prov else None
        page = prov.page_no if prov else None
        page = back.get(page, page) if back else page
        bbox = list(prov.bbox.as_tuple()) if prov else None
        grid = [[(c.text or "").strip() for c in row] for row in t.data.grid]
        if not grid:
            continue
        header, rows = grid[0], grid[1:]
        tables.append({"page": page, "bbox": bbox, "header": header, "rows": rows})
    pins = pin_rows(tables)
    Path(f"{out_stem}.tables.json").write_text(json.dumps(
        {"source": pdf.name, "extractor": "docling (text layer, no OCR)", "candidates": True, "tables": tables},
        indent=1) + "\n")
    with open(f"{out_stem}.pins.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["number", "name", "page", "package"])
        for pk, num in sorted(pins, key=lambda k: (k[0], re.sub(r"\d", "", k[1]), int(re.sub(r"\D", "", k[1]) or 0))):
            w.writerow([num, *pins[(pk, num)], pk])
    pk = sorted({k[0] for k in pins})
    return f"{len(tables)} tables, {len(pins)} pins" + (f" in {len(pk)} packages ({'; '.join(pk)})" if pk != [""] and pk else "") + note


def _ranges(pages):
    out, a = [], None
    for k, p in enumerate(pages):
        if a is None:
            a = p
        if k + 1 == len(pages) or pages[k + 1] != p + 1:
            out.append(f"{a}-{p}" if a != p else f"{a}")
            a = None
    return ",".join(out)


# -- pages (the text layer) ------------------------------------------------------
# A page index needs no model: a vendor datasheet's pages are born digital, and the words a reader looks for
# are the headings. ColQwen2.5 (2026-11-09) took 606 s on CPU for 33 pages to find what this finds in a second.

PAGE_WORDS = {"pin assignment": ("pin assignment", "pin functions", "pin configuration", "pin description",
                                 "terminal functions", "pin out", "pinout", "signal description"),
              "recommended land pattern": ("land pattern", "recommended footprint", "recommended land",
                                           "board layout", "pcb footprint", "pad layout", "solder pad layout"),
              "absolute maximum ratings": ("absolute maximum ratings",),
              "package outline": ("package outline", "package dimensions", "outline dimensions",
                                  "mechanical data", "package drawing")}


def stage_pages(pdf, out_stem, pages):
    import fitz
    docf = fitz.open(str(pdf))
    lo, hi = pages or (1, docf.page_count)
    texts = {i + 1: docf[i].get_text().lower() for i in range(lo - 1, min(hi, docf.page_count))}
    out = {}
    for key, words in PAGE_WORDS.items():
        sc = {}
        for n, t in texts.items():
            hits = sum(t.count(w) for w in words)
            if hits:                    # density: a heading on a sparse drawing page beats a mention in prose
                sc[n] = hits * 1000 / max(len(t.split()), 30)
        top = sorted(sc, key=lambda n: -sc[n])[:3]
        out[key] = [{"page": n, "score": round(sc[n], 2)} for n in top]
    Path(f"{out_stem}.pages.json").write_text(json.dumps(
        {"source": pdf.name, "index": "text layer", "candidates": True, "queries": out}, indent=1) + "\n")
    return ", ".join(f"{k}: " + (f"p{v[0]['page']}" if v else "none") for k, v in out.items())


STAGES = {"tables": (stage_tables, lambda: have("docling"), "docling not installed (./setup.sh install-ml)"),
          "pages": (stage_pages, lambda: have("fitz"), "PyMuPDF not installed (./setup.sh install-ml)")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", nargs="?", help="an MPN in datasheets.json, or a PDF")
    ap.add_argument("--stages", default="tables,pages")
    ap.add_argument("--pages", help="A-B: only these pages; auto (the tables stage's default past "
                    f"{AUTO_OVER} pages): only the pages whose text layer names a pin table, and their continuations")
    ap.add_argument("--force", action="store_true", help="redo a stage whose output exists")
    ap.add_argument("--check", action="store_true", help="say which stages this machine can run")
    a = ap.parse_args()
    reexec()
    if a.check or not a.what:
        for k, (_f, ok, why) in STAGES.items():
            print(f"  {k:7s} {'ready' if ok() else 'skipped -- ' + why}")
        return 0
    pdf, mpn = resolve(a.what)
    stem = str(pdf.with_suffix(""))
    out = {"tables": ".pins.csv", "pages": ".pages.json"}
    for k in [s.strip() for s in a.stages.split(",") if s.strip()]:
        fn, ok, why = STAGES[k]
        if Path(stem + out[k]).exists() and not a.force:
            print(f"extract {mpn} {k}: kept ({Path(stem + out[k]).name} exists; --force redoes it)")
            continue
        if not ok():
            print(f"extract {mpn} {k}: skipped -- {why}")
            continue
        try:
            if a.pages == "auto":
                pg = "auto" if k == "tables" else None
            elif a.pages:
                pg = page_range(a.pages)
            else:
                pg = "auto" if k == "tables" and npages(pdf) > AUTO_OVER else None
            print(f"extract {mpn} {k}: {fn(pdf, stem, pg)}", flush=True)
        except Exception as e:                  # one stage failing never stops the others, nor filing
            print(f"extract {mpn} {k}: FAILED -- {type(e).__name__}: {str(e)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
