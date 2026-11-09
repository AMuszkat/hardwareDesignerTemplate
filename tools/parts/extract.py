#!/usr/bin/env python3
"""Datasheet extraction, once per MPN, beside the filed PDF -- candidates, never authority.

    python3 tools/parts/extract.py <MPN | path.pdf> [--stages tables,pages,land] [--pages 70-90] [--force]
    python3 tools/parts/extract.py --check          # which stages this machine can run

Runs in its own venv, tools/parts/.venv-ml (`./setup.sh install-ml`; torch-sized,
optional): launched from any other Python it re-runs itself there, and without
the venv it says so in one line and exits 0 -- filing a datasheet never depends
on it. `datasheet.py` runs it after filing a PDF.

Three stages, each skipped with one line when its model or server is absent:

  tables  Docling, text-layer mode (no OCR: a vendor datasheet is born
          digital, its text layer is exact) -> <MPN>.tables.json (every table:
          page, bounding box, header, rows) and <MPN>.pins.csv (number, name,
          page, from every table whose header names a pin / ball column and a
          name / signal column)
  pages   ColQwen2.5 (colpali-engine) as a page index -> <MPN>.pages.json: the
          best pages for "pin assignment", "recommended land pattern",
          "absolute maximum ratings", "package outline", so a reader opens two
          pages, not two hundred
  land    Qwen2.5-VL-7B served locally by Ollama (HW_OLLAMA, default
          http://localhost:11434; model HW_LAND_MODEL, default qwen2.5vl:7b)
          over the land-pattern page pages.json found -> <MPN>.land.json, the
          pad sizes and pitch the model reads off the drawing, marked
          "candidates": true. Never a footprint's authority: fp.py's signature
          still quotes the vendor's page.

The outputs sit beside the PDF in reference/vendor/<mfr>/, and publish.py copies
and hashes them into handoff/datasheets/ with it. NO CONSUMER READS THEM until
`validate_extract.py` has diffed the extracted pin tables against the hand-made
PINOUT CSVs already published, and they matched (ROADMAP H4). Licences: Docling
(MIT), ColQwen2.5 and Qwen2.5-VL (Apache 2.0) only.
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
QUERIES = {"pin assignment": "pin assignment table: pin number, pin name, type, description",
           "recommended land pattern": "recommended land pattern / PCB footprint with pad dimensions",
           "absolute maximum ratings": "absolute maximum ratings table",
           "package outline": "package outline drawing with dimensions"}
NUM_HDR = re.compile(r"^(pin|ball|pad|terminal)s?(\(s\))?\s*(no\.?|number|#|num|position(\(s\))?s?)?$|^(no\.?|number|#)$",
                     re.I)
NAME_HDR = re.compile(r"name|signal|symbol|mnemonic", re.I)
PIN_TOKEN = re.compile(r"\b[A-Z]{0,3}\d{1,4}\b")


def pin_columns(row):
    """(number column, name column) of a header row, or None."""
    cells = [c.strip() for c in row]
    ni = next((i for i, h in enumerate(cells) if NUM_HDR.match(h)), None)
    mi = next((i for i, h in enumerate(cells) if i != ni and NAME_HDR.search(h)), None)
    return (ni, mi) if ni is not None and mi is not None else None


def pin_rows(tables):
    """{number: (name, page)} from every pin / ball table. Its header row may sit under a row of
    group labels; a table continued on the next page carries no header and takes the previous
    one's columns; one cell may list several balls ("A1, A5, B9" -- a supply's)."""
    pins, prev = {}, None
    for t in tables:
        grid = [t["header"]] + t["rows"]
        cols, start = None, 0
        for k in range(min(3, len(grid))):
            cols = pin_columns(grid[k])
            if cols:
                start = k + 1
                break
        if not cols and prev and len(grid[0]) == prev[1] and t["page"] in (prev[2], prev[2] + 1):
            cols, start = prev[0], 0                     # a continuation: no header of its own
        if not cols:
            prev = None
            continue
        prev = (cols, len(grid[0]), t["page"])
        ni, mi = cols
        for r in grid[start:]:
            if len(r) <= max(ni, mi):
                continue
            # a footnote mark printed after a name ("SAI5_MCLK 3") is not part of it
            name = re.sub(r"\s+\d{1,2}$", "", r[mi].strip())
            nums = PIN_TOKEN.findall(r[ni])
            names = name.split()
            if len(nums) > 1 and len(names) == len(nums):
                # rows the extractor merged into one ("NAND_DATA05 NAND_DATA06 NAND_DATA07" over
                # "L26 K26 N26"): paired in order; a supply's one name over many balls stays one
                pairs = list(zip(nums, names))
            else:
                pairs = [(n_, name) for n_ in nums]
            for num, nm in pairs:
                if nm and not NUM_HDR.match(nm):
                    pins.setdefault(num, (nm, t["page"]))
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


def ollama_url():
    return os.environ.get("HW_OLLAMA", "http://localhost:11434").rstrip("/")


def ollama_up():
    import urllib.request
    try:
        with urllib.request.urlopen(ollama_url() + "/api/tags", timeout=3) as r:
            return [m.get("name") for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return None


def resolve(arg):
    p = Path(arg)
    if p.suffix.lower() == ".pdf" and p.exists():
        return p.resolve(), p.stem
    ix = json.loads(INDEX.read_text()) if INDEX.exists() else {}
    m = ix.get(arg)
    if not m:
        sys.exit(f"extract: {arg} is neither a PDF nor an MPN in {INDEX.relative_to(ROOT)}")
    return (ROOT / m["path"]).resolve(), arg


def page_range(spec):
    if not spec:
        return None
    a, _, b = spec.partition("-")
    return (int(a), int(b or a))


# -- tables (Docling) ---------------------------------------------------------

def stage_tables(pdf, out_stem, pages):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    opts = PdfPipelineOptions()
    opts.do_ocr = False                       # born-digital: the text layer is exact
    opts.do_table_structure = True
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    kw = {"page_range": pages} if pages else {}
    doc = conv.convert(str(pdf), **kw).document
    tables = []
    for t in doc.tables:
        prov = t.prov[0] if t.prov else None
        page = prov.page_no if prov else None
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
        w.writerow(["number", "name", "page"])
        for num in sorted(pins, key=lambda s: (re.sub(r"\d", "", s), int(re.sub(r"\D", "", s) or 0))):
            w.writerow([num, *pins[num]])
    return f"{len(tables)} tables, {len(pins)} pins"


# -- pages (ColQwen2.5) -----------------------------------------------------------

def stage_pages(pdf, out_stem, pages):
    import torch
    import fitz                                 # PyMuPDF renders the pages
    from colpali_engine.models import ColQwen2_5, ColQwen2_5_Processor
    name = os.environ.get("HW_PAGE_MODEL", "vidore/colqwen2.5-v0.2")
    model = ColQwen2_5.from_pretrained(name, torch_dtype=torch.float32, device_map="cpu").eval()
    proc = ColQwen2_5_Processor.from_pretrained(name)
    docf = fitz.open(str(pdf))
    lo, hi = pages or (1, docf.page_count)
    from PIL import Image
    imgs, nums = [], []
    for i in range(lo - 1, min(hi, docf.page_count)):
        pix = docf[i].get_pixmap(dpi=72)
        imgs.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        nums.append(i + 1)
    with torch.no_grad():
        emb = []
        for k in range(0, len(imgs), 4):
            emb += list(model(**proc.process_images(imgs[k:k + 4])))
        q = model(**proc.process_queries(list(QUERIES.values())))
    scores = proc.score_multi_vector(q, emb)
    out = {}
    for qi, key in enumerate(QUERIES):
        top = sorted(range(len(nums)), key=lambda j: -float(scores[qi][j]))[:3]
        out[key] = [{"page": nums[j], "score": round(float(scores[qi][j]), 3)} for j in top]
    Path(f"{out_stem}.pages.json").write_text(json.dumps(
        {"source": pdf.name, "index": name, "candidates": True, "queries": out}, indent=1) + "\n")
    return ", ".join(f"{k}: p{v[0]['page']}" for k, v in out.items())


# -- land (Qwen2.5-VL via Ollama) ---------------------------------------------------

LAND_PROMPT = ("This is a page of an electronic component datasheet. If it shows a recommended land pattern "
               "(PCB footprint), return ONLY JSON: {\"unit\": \"mm\", \"pads\": [{\"name\": ..., \"width\": ..., "
               "\"height\": ...}], \"pitch\": ..., \"span\": ..., \"notes\": ...}, every number as printed on the "
               "drawing. If the page has no land pattern, return {\"none\": true}.")


def stage_land(pdf, out_stem, pages):
    import base64
    import urllib.request
    import fitz
    pj = Path(f"{out_stem}.pages.json")
    if not pj.exists():
        return "skipped -- needs the pages stage first (which page is the land pattern)"
    page = json.loads(pj.read_text())["queries"]["recommended land pattern"][0]["page"]
    pix = fitz.open(str(pdf))[page - 1].get_pixmap(dpi=150)
    img = base64.b64encode(pix.tobytes("png")).decode()
    model = os.environ.get("HW_LAND_MODEL", "qwen2.5vl:7b")
    body = json.dumps({"model": model, "prompt": LAND_PROMPT, "images": [img], "stream": False,
                       "format": "json", "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(ollama_url() + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        reply = json.loads(r.read()).get("response", "{}")
    try:
        got = json.loads(reply)
    except ValueError:
        got = {"unparsed": reply}
    Path(f"{out_stem}.land.json").write_text(json.dumps(
        {"source": pdf.name, "page": page, "model": model, "candidates": True,
         "authority": "none: the vendor's page is the authority (fp.py signs against it)", "land": got},
        indent=1) + "\n")
    return f"page {page}: {'no land pattern seen' if got.get('none') else 'candidates written'}"


STAGES = {"tables": (stage_tables, lambda: have("docling"), "docling not installed (./setup.sh install-ml)"),
          "pages": (stage_pages, lambda: have("colpali_engine") and have("fitz"),
                    "colpali-engine not installed (./setup.sh install-ml --pages)"),
          "land": (stage_land, lambda: ollama_up() is not None and have("fitz"),
                   f"no Ollama at {ollama_url()} (install Ollama, `ollama pull qwen2.5vl:7b`; HW_OLLAMA)")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", nargs="?", help="an MPN in datasheets.json, or a PDF")
    ap.add_argument("--stages", default="tables,pages,land")
    ap.add_argument("--pages", help="A-B: only these pages (a long datasheet's pin table)")
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
    out = {"tables": ".pins.csv", "pages": ".pages.json", "land": ".land.json"}
    for k in [s.strip() for s in a.stages.split(",") if s.strip()]:
        fn, ok, why = STAGES[k]
        if Path(stem + out[k]).exists() and not a.force:
            print(f"extract {mpn} {k}: kept ({Path(stem + out[k]).name} exists; --force redoes it)")
            continue
        if not ok():
            print(f"extract {mpn} {k}: skipped -- {why}")
            continue
        try:
            print(f"extract {mpn} {k}: {fn(pdf, stem, page_range(a.pages))}", flush=True)
        except Exception as e:                  # one stage failing never stops the others, nor filing
            print(f"extract {mpn} {k}: FAILED -- {type(e).__name__}: {str(e)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
