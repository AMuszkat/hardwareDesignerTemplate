#!/usr/bin/env python3
"""
snapshot.py -- freeze a board's drawn work and run every gate on the copy, for a review.

    python3 tools/review/snapshot.py --board board1 --kind schematic --id RV3
    python3 tools/review/snapshot.py --board board1 --kind pcb       --id RV4 [--scope R3]

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

and, for the reader, everything as FILES it can read without a shell: every
page and PCB view cut into overlapping tiles small enough to be seen at full
resolution (tiles.md says which parts each shows, words.csv where every text
is), parts.csv (KiCad's DNP flag and a DNP field, apart), and pins.csv /
nets.csv -- every placed part's every pin with its name, type, no-connect flag
and net. tmpWork/ is the reviewer's only scratch folder:
temporary, emptied with every retake.

The gates are independent child processes and run concurrently (a thread
pool waiting on them; HW_JOBS=1 runs them one at a time); a gate that reads
another's output (the sheet PNGs read the PDF) waits for it, and SUMMARY.md
lists them in the same order either way. The board's DRC cache
(routing/.drc_cache) is not copied: a review's DRC is its own run.

--scope (tools/review/changes.py) names what a PCB review is about -- a
routing group (its applied manifest's copper), a placement.json group, refs:
or box: -- and the PCB views are tiled only inside it (plus 3 mm), the sheet
tiles skipped; all / fab (the default) tiles everything. Every snapshot also
diffs each page and view against the board's previous snapshot of the same
kind and writes changed.md: the changed regions and the tiles that show them.

Writes SUMMARY.md (one row per gate) beside the outputs. Exit 0 = every gate
passed, 1 = a gate failed (the review starts from it), 2 = the snapshot itself
could not be made.
"""
import argparse
import datetime
import csv
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import changes  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
KP = ROOT / "kicad_project"
TOOLS = KP / "tools"
VPY = TOOLS / ".venv" / "bin" / "python"
KPY = TOOLS / "kpy"
# Generated, backup and lock files: not the board's work, and some are large.
SKIP_RE = re.compile(r"(^~.*\.lck$|\.lck$|^fp-info-cache$|^_autosave-|-backups$|\.bak$|-bak$|\.kicad_prl$)")
# A picture bigger than about 1.15 megapixels is scaled down before a reader
# sees it, and a 1.27 mm schematic text then blurs: a reviewer that cannot read
# a pin number renders and crops its own, outside the project. Every image here
# fits; the detail comes as overlapping tiles.
TILE_PX = (1280, 896)
SHEET_DPI, PCB_DPI = 300, 600          # 1.27 mm text = 15 px; a 0.5 mm pitch = 12 px
OVERLAP_MM = 12
DRC_NOTE = re.compile(r"^drc: .*(: kicad-cli run,|from the cache)")
# Rows that are outputs for the reader, not gates.
EXPORTS = ("netlist", "bom", "sch-pdf", "sch-png", "pcb-render", "pins", "sheet-tiles", "pcb-tiles", "changes",
           "measure")
TMPWORK_README = """\
tmpWork -- the reviewer's temporary workspace.

Helper scripts, crops, text dumps and any other intermediate file of this
review go here and nowhere else (never /tmp, never a session scratchpad).
Anything here may be deleted whenever it is no longer needed, and all of it is
deleted when the snapshot is retaken. Nothing here is a record: the review's
report goes to history/reviews/.
"""


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
                                 "__pycache__", ".drc_cache") or n.endswith("-backups")
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


def jobs():
    try:
        return max(1, int(os.environ.get("HW_JOBS", "0"))) if os.environ.get("HW_JOBS") else (os.cpu_count() or 1)
    except ValueError:
        return os.cpu_count() or 1


class Run:
    """The gates, run concurrently.  Each gate reserves its row when it is STARTED, so the
    summary's order is the order of the calls, whatever order the gates finish in."""

    def __init__(self, out, n_jobs=None):
        self.out, self.rows, self.pending, self.times = out, [], [], {}
        self.n_jobs = n_jobs or jobs()
        self.pool = ThreadPoolExecutor(max_workers=self.n_jobs)

    def gate(self, name, cmd, cwd=None, ok=None, note="", post=None, after=None):
        """Start one gate; its whole output goes to gates/<name>.txt.  `after`: a gate (the
        future this returns) whose output this one reads -- waited for first.  `post(i)`
        rewrites row i once the gate is done.  Returns a future of (passed, text)."""
        i = len(self.rows)
        self.rows.append((name, "not run", "still running", ""))

        def work():
            if after is not None:
                after.result()
            log = self.out / "gates" / f"{name}.txt"
            t0 = time.monotonic()
            try:
                r = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True,
                                   timeout=1800)
                text, code = (r.stdout or "") + (r.stderr or ""), r.returncode
            except (OSError, subprocess.TimeoutExpired) as e:
                text, code = f"could not run: {e}", -1
            self.times[name] = time.monotonic() - t0
            log.write_text("$ " + " ".join(str(c) for c in cmd) + f"\n(exit {code})\n\n" + text)
            passed = ok(code, text) if ok else code == 0
            # the DRC cache's stderr note ("drc: ... kicad-cli run") is not the gate's result line
            tail = next((ln.strip() for ln in reversed(text.strip().splitlines())
                         if ln.strip() and not DRC_NOTE.match(ln)), "")
            self.rows[i] = (name, "PASS" if passed else ("ERROR" if code == -1 else "FAIL"),
                            note or tail[:160], f"gates/{log.name}")
            if post:
                post(i)
            return passed, text

        f = self.pool.submit(work)
        self.pending.append(f)
        return f

    def wait(self):
        for f in self.pending:
            f.result()
        self.pending = []

    def skip(self, name, why):
        self.rows.append((name, "not run", why, ""))


def erc(run, sch, out):
    j = out / "erc.json"

    def count(i):
        if not j.exists():
            return
        d = json.loads(j.read_text())
        n = {"error": 0, "warning": 0}
        for s in d.get("sheets", []):
            for v in s.get("violations", []):
                n[v.get("severity", "error")] = n.get(v.get("severity", "error"), 0) + 1
        name, _, _, log = run.rows[i]
        run.rows[i] = (name, "PASS" if n["error"] == 0 and n["warning"] == 0 else "FAIL",
                       f"{n['error']} errors, {n['warning']} warnings (erc.json)", log)
    run.gate("erc", ["kicad-cli", "sch", "erc", "--format", "json", "--severity-all", "-o", j, sch], post=count)


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


def natural(s):
    """Sort key: J2 before J10, pin 2 before pin 10."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", s or "") if t]


def pdf_pages(pdf):
    """[(width, height)] in points, one per page."""
    t = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
    n = int(re.search(r"^Pages:\s+(\d+)", t, re.M).group(1))
    t = subprocess.run(["pdfinfo", "-f", "1", "-l", str(n), pdf], capture_output=True, text=True).stdout
    return [(float(w), float(h)) for w, h in re.findall(r"^Page\s+\d+ size:\s+([\d.]+) x ([\d.]+)", t, re.M)]


def ink_box(pdf, page, dpi=24):
    """The inked part of one page, in points, 3 mm around it: a PCB plot sits on a whole sheet."""
    r = subprocess.run(["pdftoppm", "-gray", "-r", str(dpi), "-f", str(page), "-l", str(page),
                        "-singlefile", str(pdf)], capture_output=True)
    m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", r.stdout)
    if not m:
        return None
    w, h, px = int(m[1]), int(m[2]), r.stdout[m.end():]
    xs, ys = [], []
    for y in range(h):
        row = px[y * w:(y + 1) * w]
        ink = [x for x in (next((i for i, v in enumerate(row) if v < 240), None),
                           next((w - 1 - i for i, v in enumerate(reversed(row)) if v < 240), None))
               if x is not None]
        if ink:
            xs += ink
            ys.append(y)
    if not ys:
        return None
    k, pad = 72 / dpi, 3 / 25.4 * 72
    return (max(0, min(xs) * k - pad), max(0, ys[0] * k - pad), (max(xs) + 1) * k + pad, (ys[-1] + 1) * k + pad)


def tile(pdf, page, box, dpi, outdir, name):
    """Cut box (points) of one page into TILE_PX tiles overlapping by OVERLAP_MM; [(name, box in points)]."""
    s, ov = dpi / 72, round(OVERLAP_MM / 25.4 * dpi)
    X0, Y0, X1, Y1 = (round(v * s) for v in box)

    def starts(lo, hi, size):
        span = hi - lo
        if span <= size:
            return [(lo, span)]
        n = math.ceil((span - ov) / (size - ov))
        return [(lo + round(i * (span - size) / (n - 1)), size) for i in range(n)]

    made = []
    for r, (y, hh) in enumerate(starts(Y0, Y1, TILE_PX[1]), 1):
        for c, (x, ww) in enumerate(starts(X0, X1, TILE_PX[0]), 1):
            n = f"{name}-r{r}c{c}"
            subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-f", str(page), "-l", str(page),
                            "-x", str(x), "-y", str(y), "-W", str(ww), "-H", str(hh), "-singlefile",
                            str(pdf), str(outdir / n)], capture_output=True)
            if (outdir / f"{n}.png").exists():
                made.append((n, (x / s, y / s, (x + ww) / s, (y + hh) / s)))
    return made


WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')


def pdf_words(pdf):
    """{page: [(x, y, text)]}, each word at its centre, in points."""
    t = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True).stdout
    return {i: [((float(a) + float(c)) / 2, (float(b) + float(d)) / 2, html.unescape(w))
                for a, b, c, d, w in WORD_RE.findall(p)]
            for i, p in enumerate(t.split("<page ")[1:], 1)}


def pictures(out_dir, what, sources, dpi, parts, crop, scope=None):
    """sources: [(name, pdf, page, title)]. Every page as tiles in out_dir/tiles/, with
    out_dir/tiles.md (which parts each tile shows) and out_dir/words.csv (every text, where
    it is, which tiles show it). `scope`: a box in points the tiles stay inside.
    Returns (number of tiles, {source name: [(tile name, box in points)]})."""
    tdir = out_dir / "tiles"
    tdir.mkdir(parents=True, exist_ok=True)
    mm = 25.4 / 72
    tw, th = (round(v / dpi * 25.4) for v in TILE_PX)
    L = [f"# Tiles — {what}", "",
         f"Each tile is at most {TILE_PX[0]} × {TILE_PX[1]} px at {dpi} dpi (about {tw} × {th} mm), "
         f"so it is seen at full resolution; neighbours overlap by {OVERLAP_MM} mm or more. Positions are mm "
         f"from the page's top-left corner; *texts* counts the words on a tile (0: nothing but lines, or "
         f"blank). Any text on a page: `grep -w <text> words.csv`.", ""]
    rows, words, total, by = [], {}, 0, {}
    for name, pdf, page, title in sources:
        size = pdf_pages(pdf)[page - 1]
        box = (ink_box(pdf, page) if crop else None) or (0, 0, *size)
        box = changes.clip(box, scope)
        made = tile(pdf, page, box, dpi, tdir, name) if box else []
        by[name] = made
        total += len(made)
        ws = words.setdefault(pdf, pdf_words(pdf)).get(page, [])
        L += [f"## {name} — {title}", "",
              f"{size[0] * mm:.0f} × {size[1] * mm:.0f} mm page, {len(made)} tiles.", "",
              "| tile | x mm | y mm | texts | parts on it |", "|---|---|---|---|---|"]
        for n, (a, b, c, d) in made:
            inside = [w for x, y, w in ws if a <= x <= c and b <= y <= d]
            on = sorted({w for w in inside if w in parts}, key=natural)
            L.append(f"| tiles/{n}.png | {a * mm:.0f}–{c * mm:.0f} | {b * mm:.0f}–{d * mm:.0f} | "
                     f"{len(inside)} | {', '.join(on)} |")
        L.append("")
        for x, y, w in ws:
            rows.append((name, f"{x * mm:.1f}", f"{y * mm:.1f}",
                         " ".join(n for n, (a, b, c, d) in made if a <= x <= c and b <= y <= d), w))
    (out_dir / "tiles.md").write_text("\n".join(L))
    with open(out_dir / "words.csv", "w", newline="") as f:
        csv.writer(f).writerows([("page", "x_mm", "y_mm", "tiles", "text")] + rows)
    return total, by


def columns(f):
    """A CSV's header, [] when there is none."""
    try:
        with open(f, newline="") as h:
            return next(csv.reader(h), [])
    except OSError:
        return []


def parts_table(root, out):
    """parts.csv: every placed part with KiCad's own DNP / exclude flags and a DNP *field*, apart
    -- the BOM export shows a field named DNP and the flag the same way."""
    rows = []
    for c in root.iter("comp"):
        props = {p.get("name"): p.get("value") for p in c.iter("property")}
        fields = {f.get("name"): (f.text or "") for f in c.iter("field")}
        rows.append((c.get("ref"), c.findtext("value") or "", c.findtext("footprint") or "",
                     fields.get("MPN", ""), "yes" if "dnp" in props else "",
                     fields.get("DNP", props.get("DNP", "")) or "",
                     "yes" if "exclude_from_bom" in props else "",
                     "yes" if "exclude_from_board" in props else "", props.get("Sheetfile", "")))
    rows.sort(key=lambda r: natural(r[0]))
    with open(out / "parts.csv", "w", newline="") as f:
        csv.writer(f).writerows([("ref", "value", "footprint", "mpn", "dnp_flag", "dnp_field",
                                  "exclude_from_bom", "exclude_from_board", "sheet")] + rows)
    return sum(1 for r in rows if r[4])


def pin_table(out):
    """pins.csv (every placed part's every pin: name, type, no-connect flag, net) and nets.csv
    (every net and its pins), from netlist.xml -- what a reviewer otherwise parses by hand."""
    root = ET.parse(out / "netlist.xml").getroot()
    n_dnp = parts_table(root, out)
    lib = {(p.get("lib"), p.get("part")): [(q.get("num"), q.get("name"), q.get("type")) for q in p.iter("pin")]
           for p in root.iter("libpart")}
    at, nets = {}, []
    for n in root.iter("net"):
        nodes = n.findall("node")
        nets.append((n.get("name"), n.get("class") or "",
                     " ".join(f"{d.get('ref')}.{d.get('pin')}" for d in nodes)))
        for d in nodes:
            at[(d.get("ref"), d.get("pin"))] = (n.get("name"), d.get("pintype") or "", d.get("pinfunction") or "")
    rows, refs = [], set()
    for c in root.iter("comp"):
        ref, src = c.get("ref"), c.find("libsource")
        refs.add(ref)
        pins = {num: (nm, ty) for num, nm, ty in lib.get((src.get("lib"), src.get("part")) if src is not None
                                                         else None, [])}
        for (r, num), (_, ty, nm) in at.items():
            if r == ref and num not in pins:
                pins[num] = (nm, ty.replace("+no_connect", ""))
        sheet = next((p.get("value") for p in c.iter("property") if p.get("name") == "Sheetfile"), "")
        for num in sorted(pins, key=natural):
            nm, ty = pins[num]
            net, nty, _ = at.get((ref, num), ("", "", ""))
            rows.append((ref, num, nm or "", ty or "", "yes" if "no_connect" in nty.split("+")[1:] else "",
                         net, c.findtext("value") or "", sheet))
    rows.sort(key=lambda r: (natural(r[0]), natural(r[1])))
    with open(out / "pins.csv", "w", newline="") as f:
        csv.writer(f).writerows([("ref", "pin", "name", "type", "nc_flag", "net", "value", "sheet")] + rows)
    with open(out / "nets.csv", "w", newline="") as f:
        csv.writer(f).writerows([("net", "class", "pins", "nodes")] +
                                [(n, k, len(p.split()), p) for n, k, p in sorted(nets, key=lambda t: natural(t[0]))])
    return refs, len(rows), len(nets), n_dnp


def reader(run, out, kind, scope=None):
    """Everything the reviewer reads as a file, so it never needs a shell or a folder outside this one.
    scope: (label, box in board mm or None, note) from changes.scope_box."""
    label, sbox, note = scope or ("all", None, "every tile")
    refs = set()
    if (out / "netlist.xml").exists():
        refs, n_pins, n_nets, n_dnp = pin_table(out)
        run.rows.append(("pins", "done", f"{len(refs)} parts ({n_dnp} with KiCad's DNP flag), "
                         f"{n_pins} pins, {n_nets} nets", "parts.csv, pins.csv, nets.csv"))
    if not (shutil.which("pdftoppm") and shutil.which("pdftotext") and shutil.which("pdfinfo")):
        run.skip("sheet-tiles", "poppler-utils (pdftoppm, pdftotext, pdfinfo) not installed")
        return
    pdf = out / "schematic.pdf"
    srcs, tiles = [], {}
    if pdf.exists():
        root = ET.parse(out / "netlist.xml").getroot() if (out / "netlist.xml").exists() else None
        title = {int(s.get("number")): f"{s.get('name')} ({s.findtext('title_block/source')})"
                 for s in (root.iter("sheet") if root is not None else [])}
        pages = [(f"p{i}", pdf, i, title.get(i, "")) for i in range(1, len(pdf_pages(pdf)) + 1)]
        srcs += [(n_, f_, p_) for n_, f_, p_, _t in pages]
        if kind == "pcb" and sbox:
            run.skip("sheet-tiles", f"a PCB review scoped to {label}: the sheets are whole in sheets/sheet-N.png")
        else:
            n, by = pictures(out / "sheets", "the schematic", pages, SHEET_DPI, refs, False)
            tiles.update(by)
            run.rows.append(("sheet-tiles", "done", f"{n} tiles at {SHEET_DPI} dpi", "sheets/tiles.md"))
    views = [(v, out / "pcb" / f"{v}.pdf", 1, v) for v in ("top", "bottom", "inner")
             if (out / "pcb" / f"{v}.pdf").exists()]
    if kind == "pcb" and views:
        n, by = pictures(out / "pcb", "the PCB (top, bottom, inner copper)" + (f", scope {label}" if sbox else ""),
                         views, PCB_DPI, refs, True, changes.to_points(sbox))
        tiles.update(by)
        srcs += [(n_, f_, p_) for n_, f_, p_, _t in views]
        run.rows.append(("pcb-tiles", "done", f"{n} tiles at {PCB_DPI} dpi" + (f" inside {note}" if sbox else ""),
                         "pcb/tiles.md"))
    if srcs:
        run.rows.append(("changes", "done", changes.report(out, kind, srcs, tiles, f"{label} — {note}" if sbox else note),
                         "changed.md"))


def exports(run, out, root_sch):
    """What the reviewer reads rather than gates: netlist, BOM, the sheets as pictures."""
    run.gate("netlist", ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
                         "-o", out / "netlist.xml", root_sch], note="netlist.xml")
    # KiCad merges a field named DNP into its own ${DNP} column: the BOM cannot tell the flag
    # from a text field, so parts.csv (from the netlist) carries both, separately.
    labels = "Refs,Value,Footprint,MPN,DNP flag or field,Excluded,Qty"
    run.gate("bom", ["kicad-cli", "sch", "export", "bom", "-o", out / "bom.csv",
                     "--fields", "Reference,Value,Footprint,MPN,${DNP},${EXCLUDE_FROM_BOM},${QUANTITY}",
                     "--labels", labels, "--group-by", "Value,Footprint,MPN,${DNP}", root_sch],
             ok=lambda c, t: c == 0 and columns(out / "bom.csv") == labels.split(","),
             note="bom.csv (a column missing fails)")
    pdf = out / "schematic.pdf"
    made = run.gate("sch-pdf", ["kicad-cli", "sch", "export", "pdf", "-o", pdf, root_sch], note="schematic.pdf")
    if shutil.which("pdftoppm"):
        (out / "sheets").mkdir(exist_ok=True)
        # pdftoppm on a missing PDF fails, which is the row the serial version left out: started
        # only when the PDF exists, waited for after it
        made.result()
        if pdf.exists():
            run.gate("sch-png", ["pdftoppm", "-png", "-scale-to", str(TILE_PX[0]), pdf, out / "sheets" / "sheet"],
                     note="sheets/sheet-N.png, each page whole (detail: sheets/tiles/)")


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
    run.gate("measure", kpy + [Path(__file__).resolve().parent / "measure.py", b, out / "measurements.json"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", required=True, help="boardN or the folder name")
    ap.add_argument("--kind", required=True, choices=["schematic", "pcb"])
    ap.add_argument("--id", help="the request id in the board's REVIEW.md (RVn); default: a timestamp")
    ap.add_argument("--out", help="default: out/reviews/<board>/<id>-<kind>/")
    ap.add_argument("--scope", help="pcb: a routing group (R3), a placement.json group, refs:A,B or "
                    "box:X0,Y0,X1,Y1 -- tiles only there; all / fab (default) tiles everything")
    a = ap.parse_args()
    if not shutil.which("kicad-cli"):
        die("kicad-cli is not on PATH")
    src = board_dir(a.board)
    rid = a.id or datetime.datetime.now().strftime("%Y%m%d-%H%M")
    out = Path(a.out) if a.out else ROOT / "out" / "reviews" / src.name / f"{rid}-{a.kind}"
    if out.exists():
        shutil.rmtree(out)
    (out / "gates").mkdir(parents=True)
    (out / "tmpWork").mkdir()
    (out / "tmpWork" / "README.txt").write_text(TMPWORK_README)
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
        {"board": src.name, "kind": a.kind, "id": rid, "handoff_rev": rev, "scope": a.scope or "all",
         "taken": datetime.datetime.now().isoformat(timespec="seconds"), "files": files}, indent=1))

    if a.kind == "pcb" and not list(b.glob("*.kicad_pcb")):
        die(f"{src.name}: no .kicad_pcb -- nothing to review yet")
    t_gates = time.monotonic()
    run = Run(out)
    if a.kind == "schematic":
        schematic(run, b, out, root_sch)
    else:
        erc(run, root_sch, out)
        run.gate("handoff-parity", [VPY, TOOLS / "sch" / "sch.py", "parity", "--board", b, "--handoff"],
                 ok=lambda c, t: c == 0 and "HANDOFF PARITY OK" in t)
        pcb(run, b, out)
    exports(run, out, root_sch)
    run.wait()                                  # the reader's inputs: netlist, PDFs, renders
    wall = time.monotonic() - t_gates
    serial = sum(run.times.values())
    scope = changes.scope_box(b, a.scope) if a.kind == "pcb" else (a.scope or "all", None, "every tile")
    reader(run, out, a.kind, scope)
    print(f"gates: {len(run.times)} in {wall:.1f} s on {run.n_jobs} worker(s) "
          f"({serial:.1f} s if run one at a time)", file=sys.stderr)

    gates = [r for r in run.rows if r[0] not in EXPORTS]
    failed = [r for r in gates if r[1] in ("FAIL", "ERROR")]
    L = [f"# Review snapshot — {src.name}, {a.kind}, {rid}", "",
         f"Handoff rev {rev}; {len(files)} files frozen in `snap/` (sha256 in `manifest.json`); "
         f"taken {datetime.datetime.now():%Y-%m-%d %H:%M}.", "",
         "| gate | result | detail | log |", "|---|---|---|---|"]
    L += [f"| {n} | {s} | {d.replace('|', '/')} | {g} |" for n, s, d, g in run.rows]
    L += ["", f"**{len(failed)} of {len(gates)} gates failed.**" if failed else
          f"**All {len([r for r in gates if r[1] == 'PASS'])} gates that ran passed.**"]
    L += ["", "## What to read", "",
          "- `changed.md` FIRST — what changed since the board's previous snapshot of this kind: the "
          "changed regions and the tiles that show them; a changed region outside the scope is "
          "listed as such.",
          "- `sheets/sheet-N.png` — each page whole; `sheets/tiles.md` — which tile "
          "(`sheets/tiles/pN-rRcC.png`) shows which part; `sheets/words.csv` — every text on a "
          "page, where it is and which tiles show it.",
          "- `parts.csv` — every placed part: value, footprint, MPN, KiCad's DNP flag and a DNP "
          "field apart, the exclude flags; `pins.csv` — every pin: name, type, no-connect flag, net; "
          "`nets.csv` — every net and its pins; `bom.csv`, `netlist.xml`, `erc.json`.",
          "- `snap/<board>/` — the frozen board and its `handoff/` (with `datasheets/`); "
          "`gates/*.txt` — each gate's whole output."]
    if a.kind == "pcb":
        L += ["- `measurements.json` — the board measured (measure.py): parts, groups, per-net lengths and "
              "vias, pairs (skew, gap), pours and islands, silk over mask openings, and each "
              "`10_layout.md` row's parts, points and distances. Read it instead of measuring."]
        L += ["- `pcb/top.png`, `bottom.png`, `inner.png` — each view whole; `pcb/tiles.md` and "
              "`pcb/tiles/` — the same views at full resolution."]
    L += ["- `tmpWork/` — the reviewer's ONLY scratch folder: temporary, anything in it may be "
          "deleted, all of it is when the snapshot is retaken."]
    (out / "SUMMARY.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\n{out}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
