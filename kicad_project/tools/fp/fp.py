#!/usr/bin/env python3
"""
fp.py -- get a real footprint (and its 3D model) for a real part, check it
against the vendor's own land pattern, and only then put it in the library.

WHOSE FILE THIS IS.  Yours.  It lives inside kicad_project/, the board
session owns it, edits it and runs it; the concept session does not touch
it.  Footprints derive from datasheets, and the datasheets themselves are
published in `<board>/handoff/datasheets/` -- so this is work that finishes
on this side of the wall, without a question and without anyone quoting pad
dimensions to you in prose.

THE CHAIN, in the order `get` tries it:

  1. already staged/installed and signed        -- nothing to do
  2. LCSC / EasyEDA, via easyeda2kicad          -- symbol + footprint + STEP
  3. --url  a URL you found (the manufacturer's own is worth more than any
            portal's redraw)                    -- .kicad_mod / .zip / .step
  4. --file a file you saved yourself (Chrome MCP on a login-walled portal:
            SnapEDA, Ultra Librarian, SamacSys) -- same handling
  5. NOTHING WORKED -> `get` prints THE FORK and exits 2:
        (a) author it from the datasheet with Konnect's library toolset
            (`create_footprint`, `edit_footprint_pad`, `set_footprint_models`)
        (b) draw it by hand in the KiCad footprint editor
     Either way `adopt` files it with the same provenance record, so an
     authored footprint is auditable exactly like a downloaded one.

THE GATE.  A downloaded footprint is a stranger's arithmetic, and a wrong
land pattern is the one class of error that survives every ERC/DRC pass and
is found at assembly.  So nothing reaches the library until

    verify   printed the geometry it actually has, and
    sign     recorded WHICH DATASHEET AND PAGE it was checked against,
             with that datasheet's sha256,

and `check` re-reads all of it afterwards: a footprint edited after signing
fails on its own hash, and a datasheet REVISED under a signed footprint
fails on the datasheet's -- which is the failure nobody catches by eye,
because nothing about your drawing changes when the vendor moves a pad.

    python3 tools/fp/fp.py get <MPN> --lcsc C123456
    python3 tools/fp/fp.py get <MPN> --url https://<vendor>/<cad>.zip
    python3 tools/fp/fp.py get <MPN> --file ~/Downloads/<part>.kicad_mod
    python3 tools/fp/fp.py verify <MPN> --pins 4
    python3 tools/fp/fp.py sign <MPN> \
        --datasheet <MPN> --pages 5 \
        --note "pads 0.64 x 0.70, inner gap 0.70, span 2.10 -- Fig 3"
    python3 tools/fp/fp.py install <MPN>
    python3 tools/fp/fp.py adopt <FootprintName> --mpn <MPN> \
        --source authored --datasheet <MPN> --pages 4 --note "..."
    python3 tools/fp/fp.py check          # audit everything; exit 1 on any problem
    python3 tools/fp/fp.py list

SILK AND COURTYARD are the session's, the LAND is not.  Two commands change
the first without touching the second, and prove it (the pad nodes are
hashed before and after):

    python3 tools/fp/fp.py tidy <name> [--silk-keep 0,3] [--silk-dot X Y] \\
        [--courtyard 0.25] [--dry-run]
    python3 tools/fp/fp.py derive Package_SO:SOIC-8_3.9x4.9mm_P1.27mm \\
        --used-by "U2 <MPN>, U3 <MPN>" [same options]

`tidy` drops every F.SilkS line/rect/arc that is not a mark (a filled pin-1
polygon, a dot up to 0.25 mm radius, or an index you --silk-keep), and
rebuilds F.CrtYd as pads-or-body plus --courtyard.  `derive` copies one of
KiCad's OWN footprints into the library, tidies it the same way, and records
it with source "stock": no datasheet, because the land is KiCad's and stays
byte-identical to KiCad's -- `check` re-hashes both files and fails the day
either drifts.  Chip R/C/L are refused here too.

MPN -> LCSC id is the one lookup this script cannot do (LCSC and EasyEDA
both answer a script with 403, and headless Chrome with an empty page).
Get it from Konnect's integration toolset -- `search_jlcpcb_parts(mpn)` --
or from the LCSC page in a real browser, and pass it with --lcsc.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
KROOT = os.path.dirname(os.path.dirname(HERE))          # kicad_project/
sys.path.insert(0, os.path.dirname(HERE))
import boardsel                                         # noqa: E402
# One venv for every board-side tool that needs a package the KiCad python
# lacks (easyeda2kicad here, PyMuPDF for renders): kicad_project/tools/.venv,
# made by kicad_project/tools/setup.sh from requirements.txt.
VENV_FP = os.path.join(KROOT, "tools", ".venv", "bin", "easyeda2kicad")
TODAY = datetime.date.today().isoformat()
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like "
      "Gecko) Chrome/124.0 Safari/537.36")
CAD_EXT = (".kicad_mod",)
MODEL_EXT = (".step", ".stp", ".wrl", ".wings")


# --- where things are -------------------------------------------------------
class Board:
    """Everything path-shaped, resolved once.

    ⚠ The library STEM is read off the existing `lib/*.pretty`, not built
    from the folder name: a board folder `board1_main` holds `board1.pretty`
    by convention, but a library someone renamed is the real one, and a tool
    that guessed would quietly start a second library next to it.  With no
    library yet, the stem is the folder name up to its first underscore.
    """

    def __init__(self, name=None):
        # boardN, the folder name or a path; default: the folder you are in,
        # else this session's board (HW_BOARD). Never another session's.
        self.dir = str(boardsel.resolve(name))
        self.name = os.path.basename(self.dir)
        self.lib = os.path.join(self.dir, "lib")
        pretties = sorted(f for f in os.listdir(self.lib)
                          if f.endswith(".pretty")) \
            if os.path.isdir(self.lib) else []
        if len(pretties) > 1:
            die(f"{self.lib} holds more than one .pretty: {pretties}")
        self.stem = pretties[0][:-len(".pretty")] if pretties \
            else self.name.split("_")[0]
        if not self.stem:
            die(f"{self.lib} holds a library with an empty name ('.pretty'); remove it")
        self.pretty = os.path.join(self.lib, self.stem + ".pretty")
        self.shapes = os.path.join(self.lib, self.stem + ".3dshapes")
        self.stage = os.path.join(self.lib, ".fp")
        self.prov = os.path.join(self.lib, "footprints.json")
        self.datasheets = os.path.join(self.dir, "handoff", "datasheets")

    def model_uri(self, fname):
        return "${KIPRJMOD}/lib/%s.3dshapes/%s" % (self.stem, fname)

    def load(self):
        if os.path.exists(self.prov):
            return json.load(open(self.prov))
        return {"footprints": {}}

    def save(self, rec):
        os.makedirs(self.lib, exist_ok=True)
        with open(self.prov, "w") as f:
            json.dump(rec, f, indent=2, sort_keys=True)
            f.write("\n")


def die(msg, code=1):
    print("fp: " + msg, file=sys.stderr)
    sys.exit(code)


def sha(path_or_bytes):
    b = path_or_bytes if isinstance(path_or_bytes, bytes) \
        else open(path_or_bytes, "rb").read()
    return hashlib.sha256(b).hexdigest()[:16]


def slug(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


def ds_path(board, mpn):
    """The datasheet filed for an MPN in handoff/datasheets/, or None. The
    publisher names the file after the MPN with path-unsafe characters ('/')
    replaced by '_'."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in mpn)
    for n in (mpn, safe):
        p = os.path.join(board.datasheets, n + ".pdf")
        if os.sep not in n and os.path.exists(p):
            return p
    return None


# --- s-expression reading ---------------------------------------------------
def _tokens(s):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c in "()":
            out.append(c)
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and s[j] != '"':
                if s[j] == "\\":
                    buf.append(s[j + 1])
                    j += 2
                else:
                    buf.append(s[j])
                    j += 1
            out.append("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and not s[j].isspace() and s[j] not in "()":
                j += 1
            out.append(s[i:j])
            i = j
    return out


def parse(text):
    """Nested lists; quoted strings and bare atoms both come back as str
    (nothing here needs to tell them apart)."""
    toks, pos = _tokens(text), 0

    def rd():
        nonlocal pos
        t = toks[pos]
        pos += 1
        if t == "(":
            lst = []
            while toks[pos] != ")":
                lst.append(rd())
            pos += 1
            return lst
        return t
    return rd()


def deep(tree, name):
    if isinstance(tree, list):
        if tree and tree[0] == name:
            yield tree
        for x in tree:
            yield from deep(x, name)


def kid(node, name):
    for x in node:
        if isinstance(x, list) and x and x[0] == name:
            return x
    return None


def nums(node, i=1):
    out = []
    for v in node[i:]:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            break
    return out


# --- geometry report --------------------------------------------------------
def read_fp(path):
    tree = parse(open(path).read())
    fp = {"name": tree[1], "path": path,
          "attr": " ".join(kid(tree, "attr")[1:]) if kid(tree, "attr") else "",
          "descr": kid(tree, "descr")[1] if kid(tree, "descr") else "",
          "pads": [], "models": [], "graphics": [],
          "texts": [x[1] for x in deep(tree, "property")]}
    for p in deep(tree, "pad"):
        at = nums(kid(p, "at") or ["at", 0, 0])
        sz = nums(kid(p, "size") or ["size", 0, 0])
        dr = kid(p, "drill")
        lay = kid(p, "layers")
        fp["pads"].append({
            "num": p[1], "type": p[2], "shape": p[3],
            "x": at[0] if at else 0.0, "y": at[1] if len(at) > 1 else 0.0,
            "rot": at[2] if len(at) > 2 else 0.0,
            "w": sz[0] if sz else 0.0, "h": sz[1] if len(sz) > 1 else 0.0,
            # (drill 0.8) or (drill oval 0.6 1.2): the first NUMBER
            "drill": (nums(dr, 2 if len(dr) > 1 and dr[1] == "oval" else 1)
                      or [0.0])[0] if dr else None,
            "layers": [x for x in (lay[1:] if lay else [])],
        })
    for m in deep(tree, "model"):
        fp["models"].append(m[1])
    for g in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc",
              "fp_text"):
        for node in deep(tree, g):
            lay = kid(node, "layer")
            pts = []
            for k in ("start", "end", "center", "mid", "at"):
                c = kid(node, k)
                if c:
                    v = nums(c)
                    if len(v) >= 2:
                        pts.append((v[0], v[1]))
            if g == "fp_circle" and kid(node, "center") and kid(node, "end"):
                # a circle's extent is centre +- radius, not its two points
                (cx, cy), (ex, ey) = nums(kid(node, "center"))[:2], \
                    nums(kid(node, "end"))[:2]
                r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
                pts = [(cx - r, cy - r), (cx + r, cy + r)]
            xy = kid(node, "pts")
            if xy:
                for p in deep(xy, "xy"):
                    v = nums(p)
                    if len(v) >= 2:
                        pts.append((v[0], v[1]))
            fp["graphics"].append({"kind": g,
                                   "layer": lay[1] if lay else "",
                                   "pts": pts})
    return fp


def model_path(fp, board, uri):
    """Where the 3D file actually is, or None.

    ⚠ A STAGED footprint already carries its final `${KIPRJMOD}` path -- so
    that the bytes that get signed are the bytes that get installed -- while
    the file itself is still beside it in the staging folder. Resolving only
    against the board would call every staged model missing.
    """
    base = os.path.basename(uri)
    here = os.path.dirname(os.path.abspath(fp["path"]))
    cands = [uri.replace("${KIPRJMOD}", board.dir),
             os.path.join(os.path.dirname(here), "staged.3dshapes", base),
             os.path.join(board.shapes, base)]
    # A footprint DERIVED from KiCad's own library keeps KiCad's own model.
    m = re.match(r"\$\{(KICAD\d*_3DMODEL_DIR|KISYS3DMOD)\}/(.*)", uri)
    if m:
        for d in [os.environ.get(m.group(1))] + list(STOCK_3D_DIRS):
            if d:
                cands.append(os.path.join(d, m.group(2)))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


# --- standard chip passives -------------------------------------------------
# RESISTORS, CAPACITORS and FERRITE BEADS in a standard chip package come from
# KiCad's stock library and nowhere else.  Chip inductors and 0-ohm links share
# the same land, so they are in.  ⚠ EVERYTHING ELSE IS NORMAL WORK -- a diode,
# a SOT, a two-pad anything whose datasheet draws its own land: fetch it.
# imperial code -> (metric code, nominal land span across the pads, mm)
CHIP = {"0201": ("0603Metric", 1.05), "0402": ("1005Metric", 1.50),
        "0603": ("1608Metric", 2.20), "0805": ("2012Metric", 2.60),
        "1206": ("3216Metric", 4.00), "1210": ("3225Metric", 4.00),
        "1812": ("4532Metric", 5.60), "2010": ("5025Metric", 6.20),
        "2512": ("6332Metric", 7.60)}


# KiCad's own libraries: $KICAD<N>_FOOTPRINT_DIR / _3DMODEL_DIR when set (KiCad
# sets them for its own processes; a shell usually has none), then the usual
# install locations on Linux and macOS.
_MAC = "/Applications/KiCad/KiCad.app/Contents/SharedSupport"
STOCK_DIRS = tuple(d for d in (
    [os.environ.get(k) for k in sorted(os.environ) if re.fullmatch(r"KICAD\d*_FOOTPRINT_DIR", k)]
    + ["/usr/share/kicad/footprints", "/usr/local/share/kicad/footprints",
       os.path.expanduser("~/.local/share/kicad/footprints"), _MAC + "/footprints"]) if d)
STOCK_3D_DIRS = tuple(d for d in (
    [os.environ.get(k) for k in sorted(os.environ) if re.fullmatch(r"KICAD\d*_3DMODEL_DIR", k)]
    + ["/usr/share/kicad/3dmodels", "/usr/local/share/kicad/3dmodels",
       os.path.expanduser("~/.local/share/kicad/3dmodels"), _MAC + "/3dmodels"]) if d)
_STOCK = None


def stock_chips():
    """(code, span, pad w, pad h) for every chip land KiCad itself ships.

    Measured from the stock library rather than typed in here, so "standard"
    means whatever the installed KiCad calls standard -- including the
    HandSolder variants -- and nothing drifts when it updates. Falls back to
    the CHIP table above if the library is not where it usually is.
    """
    global _STOCK
    if _STOCK is not None:
        return _STOCK
    out = []
    for d in STOCK_DIRS:
        for lib in ("Resistor_SMD.pretty", "Capacitor_SMD.pretty",
                    "Inductor_SMD.pretty"):
            path = os.path.join(d, lib)
            if not os.path.isdir(path):
                continue
            for f in os.listdir(path):
                m = re.match(r"^[RCL]_(\d{4})_\d+Metric.*\.kicad_mod$", f)
                if not m or m.group(1) not in CHIP:
                    continue
                try:
                    fp = read_fp(os.path.join(path, f))
                except Exception:                           # noqa: BLE001
                    continue
                b = geometry(fp)["pad_bbox"]
                if b and len(fp["pads"]) == 2:
                    out.append((m.group(1), b[2] - b[0],
                                fp["pads"][0]["w"], fp["pads"][0]["h"]))
    if not out:
        out = [(k, v[1], 0.0, 0.0) for k, v in CHIP.items()]
    _STOCK = out
    return out


def standard_chip(fp):
    """The imperial code if this is a two-terminal standard chip package.

    Resistors, capacitors and ferrite beads in one of these do NOT get
    fetched and do NOT get verified against a datasheet: the land is IPC's,
    KiCad's stock library already draws it to a density level DRC
    understands, and a vendor's redraw of an 0603 is a private copy of a
    public standard.  Concept does not publish their datasheets either.

    ⚠ It matches the PADS as well as the span, because plenty of two-pad
    parts are NOT chip passives -- SOD-323 sits within 0.1 mm of an 0805's
    span -- and refusing to fetch a diode's real land would be the same
    mistake in the other direction.  Diodes, SOTs and the rest go through
    the normal workflow.
    """
    pads = fp["pads"]
    if len(pads) != 2 or any(p["type"] != "smd" or p["drill"] for p in pads):
        return None
    b = geometry(fp)["pad_bbox"]
    if not b:
        return None
    span = b[2] - b[0]
    w, h = pads[0]["w"], pads[0]["h"]
    for code, ss, sw, sh in stock_chips():
        if abs(span - ss) <= 0.40 and (not sw or (abs(w - sw) <= 0.30
                                                  and abs(h - sh) <= 0.30)):
            return code
    return None


def _passive_refusal(code, mpn):
    m, _ = CHIP[code]
    return f"""
fp: {mpn} looks like a standard {code} chip package (two SMD pads; both the
    land span and the pads match KiCad's {code}/{m}).

    CHIP RESISTORS, CAPACITORS AND FERRITE BEADS DO NOT COME FROM A VENDOR AND
    ARE NOT VERIFIED AGAINST A DATASHEET. Use KiCad's own:

        Resistor_SMD:R_{code}_{m}        Capacitor_SMD:C_{code}_{m}
        Inductor_SMD:L_{code}_{m}        (…_HandSolder for the wider land)

    Nothing to fetch, nothing to sign, no datasheet to ask concept for.
    If this is NOT one of those three -- a diode, a SOT, any 2-pad part whose
    datasheet draws its own land -- re-run with --anyway and say why in the
    sign note. The rule is about R, C and ferrite beads only.
"""


def _bbox(points):
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def geometry(fp):
    """The numbers a datasheet land-pattern drawing can be compared to."""
    pads = fp["pads"]
    corners = []
    for p in pads:
        w, h = (p["h"], p["w"]) if round(p["rot"]) % 180 == 90 \
            else (p["w"], p["h"])
        corners += [(p["x"] - w / 2, p["y"] - h / 2),
                    (p["x"] + w / 2, p["y"] + h / 2)]
    pitch = None
    if len(pads) > 1:
        d = []
        for i, a in enumerate(pads):
            for b in pads[i + 1:]:
                d.append(round(((a["x"] - b["x"]) ** 2 +
                                (a["y"] - b["y"]) ** 2) ** 0.5, 4))
        pitch = min(x for x in d if x > 0) if any(x > 0 for x in d) else None
    crt = _bbox([pt for g in fp["graphics"] if "CrtYd" in g["layer"]
                 for pt in g["pts"]])
    slk = _bbox([pt for g in fp["graphics"]
                 if "Silk" in g["layer"] and g["kind"] != "fp_text"
                 for pt in g["pts"]])
    return {"pad_bbox": _bbox(corners), "pitch": pitch,
            "courtyard": crt, "silk": slk}


def _fmt_box(b):
    if not b:
        return "none"
    return (f"x {b[0]:+.3f}..{b[2]:+.3f} ({b[2]-b[0]:.3f})  "
            f"y {b[1]:+.3f}..{b[3]:+.3f} ({b[3]-b[1]:.3f})")


def checks(fp, board, pins=None):
    """(severity, message). FAIL blocks `sign` unless --override."""
    out = []
    pads = fp["pads"]
    if not pads:
        out.append(("FAIL", "no pads at all"))
    numbered = [p["num"] for p in pads if p["num"] not in ("", '""')]
    dup = sorted({n for n in numbered if numbered.count(n) > 1})
    # ⚠ Duplicates are legal (a split thermal pad, a shielded connector) but
    # they are also what a bad conversion produces, so they are never silent.
    if dup:
        out.append(("FAIL", f"pad numbers used more than once: "
                            f"{', '.join(dup)} -- legal only if the part "
                            f"really has one net on several lands"))
    for p in pads:
        if p["w"] <= 0 or p["h"] <= 0:
            out.append(("FAIL", f"pad {p['num']} has zero size"))
        if p["type"] == "smd" and not any("Cu" in l for l in p["layers"]):
            # An UNNUMBERED smd "pad" on paste/mask only is an aperture --
            # KiCad's own way of splitting an exposed pad's paste -- not a
            # land. A numbered one with no copper is a real defect.
            if p["num"] in ("", '""'):
                out.append(("WARN", f"unnumbered paste/mask aperture on "
                                    f"{','.join(p['layers'])} (fine for an "
                                    f"exposed-pad paste split)"))
            else:
                out.append(("FAIL", f"pad {p['num']} is smd but on no "
                                    f"copper layer"))
        if p["type"] == "thru_hole" and not p["drill"]:
            out.append(("FAIL", f"pad {p['num']} is thru_hole with no drill"))
        if p["type"] == "smd" and any(l.startswith("B.") for l in p["layers"]):
            out.append(("WARN", f"pad {p['num']} is smd on a back layer"))
    # Pins = distinct pad NUMBERS: paste apertures have none, and a split
    # thermal pad repeats one.
    if pins is not None and len(set(numbered)) != pins:
        out.append(("FAIL", f"{len(set(numbered))} numbered pads, datasheet says {pins}"))
    code = standard_chip(fp)
    if code:
        out.append(("FAIL", f"this measures as a standard {code} chip land "
                            f"-- if it is a resistor, capacitor or ferrite "
                            f"bead, use KiCad's stock "
                            f"R/C/L_{code}_{CHIP[code][0]} instead of a "
                            f"vendor copy. If it is a diode or anything else "
                            f"with its own land, sign it with --override "
                            f"saying so."))
    g = geometry(fp)
    if not g["courtyard"]:
        out.append(("WARN", "no courtyard -- DRC cannot check clearance "
                            "and the placement tool has nothing to bump"))
    elif g["pad_bbox"]:
        c, b = g["courtyard"], g["pad_bbox"]
        if b[0] < c[0] or b[1] < c[1] or b[2] > c[2] or b[3] > c[3]:
            out.append(("WARN", "pads stick out of the courtyard"))
    # Silk carries a pin-1 mark, not the body (see `tidy`), so the thing to
    # miss is the mark -- on a part where orientation can be got wrong.
    if not g["silk"] and len(pads) >= 3:
        out.append(("WARN", "nothing on silkscreen, so no pin-1 mark"))
    if not fp["models"]:
        out.append(("WARN", "no 3D model referenced"))
    for m in fp["models"]:
        if not m.startswith("${KIPRJMOD}") and not m.startswith("${KICAD") \
                and not m.startswith("${KISYS3DMOD}"):
            out.append(("WARN", f"3D model path is not project-relative: "
                                f"{m}"))
        if not model_path(fp, board, m):
            if m.startswith("${KICAD") or m.startswith("${KISYS3DMOD}"):
                # KiCad's footprint names a model KiCad's package does not
                # ship: the stock part on the board had no model either.
                out.append(("WARN", f"KiCad's own 3D model is not installed: "
                                    f"{m}"))
            else:
                out.append(("FAIL", f"3D model file is missing: {m}"))
    if not any(g_["kind"] == "fp_text" for g_ in fp["graphics"]) \
            and not fp["texts"]:
        out.append(("WARN", "no reference/value text"))
    return out


def report(fp, board, pins=None):
    g = geometry(fp)
    print(f"\n=== {fp['name']}   ({fp['path']})")
    print(f"    attr: {fp['attr'] or '(none)'}   pads: {len(fp['pads'])}"
          f"   models: {len(fp['models']) or 'none'}")
    if fp["descr"]:
        print(f"    descr: {fp['descr'][:100]}")
    print("\n    pad     type       shape        x        y     w x h"
          "        rot  drill  layers")
    for p in sorted(fp["pads"], key=lambda p: (len(p["num"]), p["num"])):
        print(f"    {p['num']:<6} {p['type']:<10} {p['shape']:<10} "
              f"{p['x']:+8.3f} {p['y']:+8.3f}  "
              f"{p['w']:.3f} x {p['h']:.3f}  {p['rot']:5.0f}  "
              f"{('%.3f' % p['drill']) if p['drill'] else '   - '}  "
              f"{','.join(p['layers'][:3])}")
    print(f"\n    pad extent   {_fmt_box(g['pad_bbox'])}")
    print(f"    courtyard    {_fmt_box(g['courtyard'])}")
    print(f"    silkscreen   {_fmt_box(g['silk'])}")
    print(f"    min pad centre spacing  "
          f"{('%.4f mm' % g['pitch']) if g['pitch'] else 'n/a'}")
    for m in fp["models"]:
        print(f"    3D model     {m}")
    cs = checks(fp, board, pins)
    print("")
    for sev, msg in cs:
        print(f"    {sev}: {msg}")
    if not cs:
        print("    no automatic complaints.")
    return cs


# --- fetching ---------------------------------------------------------------
_GRAB = """<html><body><pre id="o">START</pre><script>
(async () => {
  try {
    const r = await fetch(%s, {credentials: "omit"});
    const b = new Uint8Array(await r.arrayBuffer());
    let s = ""; const C = 0x8000;
    for (let i = 0; i < b.length; i += C)
      s += String.fromCharCode.apply(null, b.subarray(i, i + C));
    document.getElementById("o").textContent =
      "FP_OK " + r.status + " " + b.length + " " + btoa(s);
  } catch (e) { document.getElementById("o").textContent = "FP_ERR " + e; }
})();
</script></body></html>"""
CHROME_BINS = tuple(b for b in (
    os.environ.get("CHROME_BIN"), "google-chrome-stable", "google-chrome",
    "chromium", "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium") if b)


def chrome_fetch(url, timeout=90):
    """(bytes, None) or (None, why), through the LOCAL headless Chrome.

    ⚠ A host can refuse a script and answer a browser -- silently, at any
    timeout, with any headers.  So a 403 or a hang from urllib is never
    proof the file is unreachable; this is the second opinion.  The page
    below fetches the bytes itself and hands them back base64 in the DOM,
    because --dump-dom is the only channel headless Chrome offers without a
    DevTools client.  --disable-web-security is confined to a throwaway
    profile, never your real one.
    """
    exe = next((b for b in CHROME_BINS if shutil.which(b) or os.access(b, os.X_OK)), None)
    if not exe:
        return None, "no local Chrome/Chromium"
    d = tempfile.mkdtemp(prefix="fp-")
    try:
        page = os.path.join(d, "grab.html")
        open(page, "w").write(_GRAB % json.dumps(url))
        try:
            p = subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--disable-web-security",
                 "--user-data-dir=" + os.path.join(d, "prof"),
                 "--virtual-time-budget=%d" % (timeout * 1000 // 3),
                 "--dump-dom", "file://" + page],
                capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, f"headless Chrome gave up after {timeout}s"
        m = re.search(r"FP_OK (\d+) (\d+) ([A-Za-z0-9+/=]+)",
                      p.stdout.decode("utf8", "replace"))
        if not m:
            e = re.search(r"FP_ERR ([^<]{0,120})",
                          p.stdout.decode("utf8", "replace"))
            return None, ("chrome: " + e.group(1).strip()) if e \
                else "chrome returned no payload"
        blob = base64.b64decode(m.group(3))
        if len(blob) != int(m.group(2)):
            return None, "chrome: truncated"
        return blob, None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read(), None
    except Exception as e:                                  # noqa: BLE001
        blob, why = chrome_fetch(url)
        return (blob, None) if blob else (None, f"{e}; then {why}")


# --- staging ----------------------------------------------------------------
def stage_dir(board, mpn):
    return os.path.join(board.stage, slug(mpn))


def _fresh(d):
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    os.makedirs(os.path.join(d, "staged.pretty"), exist_ok=True)
    os.makedirs(os.path.join(d, "staged.3dshapes"), exist_ok=True)
    return d


def _harvest(src_dir, stage, board, pick=None, name=None):
    """Pull the one .kicad_mod and any 3D models out of whatever arrived."""
    mods, models = [], []
    for root, _, fs in os.walk(src_dir):
        for f in fs:
            p = os.path.join(root, f)
            if f.lower().endswith(CAD_EXT):
                mods.append(p)
            elif f.lower().endswith(MODEL_EXT):
                models.append(p)
    if not mods:
        return None, ("no .kicad_mod anywhere in what was downloaded"
                      + (f" (3D models found: "
                         f"{[os.path.basename(m) for m in models]})"
                         if models else ""))
    if len(mods) > 1:
        if pick:
            mods = [m for m in mods if m.endswith(pick)]
        if len(mods) != 1:
            return None, ("more than one footprint in the download; re-run "
                          "with --pick <suffix of the one you want>:\n    "
                          + "\n    ".join(os.path.relpath(m, src_dir)
                                          for m in sorted(mods)))
    text = open(mods[0], encoding="utf8", errors="replace").read()
    try:
        tree = raw_parse(text)
    except IndexError:
        tree = None
    if not isinstance(tree, list) or not tree or tree[0] not in ("footprint", "module") \
            or len(tree) < 2 or isinstance(tree[1], list):
        return None, (f"{os.path.basename(mods[0])} is not a KiCad footprint "
                      "(a block page or a login page saved under that name?)")
    # A KiCad library id is "lib:footprint", so a COLON inside the footprint
    # NAME makes an id KiCad cannot resolve -- easyeda2kicad emits names like
    # "easyeda2kicad:HDR-TH_8P-...", KiCad silently sanitises the colon and
    # then reports the footprint missing from the library. Slug it here,
    # where the name is first chosen, so it is right in every later step.
    fpname = slug(name or rq(tree[1]))
    # Keep BOTH 3D formats: the footprint points at the .wrl (colours), and
    # KiCad's STEP exporter picks the same-named .step up on its own.
    kept = []
    for m in models:
        dst = os.path.join(stage, "staged.3dshapes", os.path.basename(m))
        shutil.copy2(m, dst)
        kept.append(os.path.basename(dst))
    wrl = [k for k in kept if k.lower().endswith(".wrl")]
    ref = (wrl or kept or [None])[0]
    # Edit the PARSED tree, never the text: a model path with a space or a
    # parenthesis in it (a vendor's "C:/Users/Jo Smith/PART (2).step") cannot
    # be found by a regular expression.
    tree[1] = '"%s"' % fpname
    if ref:
        # The final in-project path NOW, so the bytes that get signed are the
        # bytes that get installed.
        models = [n for n in tree if isinstance(n, list) and n and n[0] == "model"]
        if models:
            models[0][1] = '"%s"' % board.model_uri(ref)
        else:
            tree.append(["model", '"%s"' % board.model_uri(ref),
                         ["offset", ["xyz", "0", "0", "0"]],
                         ["scale", ["xyz", "1", "1", "1"]],
                         ["rotate", ["xyz", "0", "0", "0"]]])
    out = os.path.join(stage, "staged.pretty", fpname + ".kicad_mod")
    # kicad-cli rewrites older dialects (what converters emit) into the
    # installed version's format -- and is the proof KiCad can load the file.
    open(out, "w").write(_normalise(raw_dump(tree), fpname))
    return out, None


def cmd_get(a, board):
    stage = stage_dir(board, a.mpn)
    prov = board.load()["footprints"]
    already = [k for k, v in prov.items() if v.get("mpn") == a.mpn]
    if already and not a.force:
        for k in already:
            f = os.path.join(board.pretty, k + ".kicad_mod")
            state = "in the library" if os.path.exists(f) else "recorded"
            print(f"{a.mpn} is already {state} as {k} "
                  f"(source {prov[k].get('source')}, signed "
                  f"{prov[k].get('verified', {}).get('date', 'NO')}).")
        print("Nothing to do. --force to fetch it again.")
        return 0
    if not (a.lcsc or a.url or a.file):
        return fork(a.mpn, "no source given: --lcsc, --url or --file")
    # A stage that is already there (perhaps signed, waiting for `install`)
    # survives until the new fetch has produced a footprint.
    old = stage + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(stage):
        os.rename(stage, old)
    rc = 1
    try:
        rc = _get(a, board, stage)
        return rc
    finally:
        if rc == 0:
            shutil.rmtree(old, ignore_errors=True)
        else:
            shutil.rmtree(stage, ignore_errors=True)
            if os.path.isdir(old):
                os.rename(old, stage)
                print("fp: the previously staged copy was kept.", file=sys.stderr)


def _get(a, board, stage):
    _fresh(stage)
    meta = {"mpn": a.mpn, "fetched": TODAY}
    src = os.path.join(stage, "src")

    if a.lcsc:
        if not os.path.exists(VENV_FP):
            die("easyeda2kicad is not installed -- run "
                f"{os.path.join(KROOT, 'tools', 'setup.sh')}")
        base = os.path.join(src, "easyeda")
        r = subprocess.run([VENV_FP, "--full", "--lcsc_id=" + a.lcsc,
                            "--output=" + base, "--overwrite"],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode != 0:
            return fork(a.mpn, f"easyeda2kicad failed for {a.lcsc}")
        meta.update(source="easyeda", lcsc=a.lcsc,
                    url=f"https://www.lcsc.com/product-detail/{a.lcsc}.html")
        sym = base + ".kicad_sym"
        if os.path.exists(sym):
            meta["symbol"] = os.path.relpath(sym, board.dir)
    elif a.url:
        blob, why = http_get(a.url)
        if not blob:
            print(f"fp: {a.url}\n    {why}")
            return fork(a.mpn, "the URL could not be fetched by a script or "
                               "by the local headless Chrome -- if it needs "
                               "a login or a click, open it with the Chrome "
                               "MCP, save the file, and re-run with --file")
        name = os.path.basename(a.url.split("?")[0]) or "download.bin"
        if blob[:2] == b"PK" and not name.lower().endswith(".zip"):
            name += ".zip"                    # .../download?id=... is still a zip
        open(os.path.join(src, name), "wb").write(blob)
        meta.update(source="url", url=a.url)
    else:
        p = os.path.expanduser(a.file)
        if not os.path.isfile(p):
            die(f"no such file: {p}")
        shutil.copy2(p, os.path.join(src, os.path.basename(p)))
        meta.update(source="file", url=a.origin or "", file=os.path.basename(p))

    for root, _, fs in os.walk(src):
        for f in fs:
            if f.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(os.path.join(root, f)) as z:
                        z.extractall(os.path.join(root, f[:-4] + "_unzipped"))
                except zipfile.BadZipFile:
                    print(f"fp: {f} is not a zip archive (a login or block "
                          "page saved under that name?)")
                    return fork(a.mpn, "the download is not what its name says")

    mod, why = _harvest(src, stage, board, a.pick, a.name)
    if not mod:
        print(f"fp: {why}")
        return fork(a.mpn, "nothing usable in what was downloaded")
    code = standard_chip(read_fp(mod))
    if code and not a.anyway:
        print(_passive_refusal(code, a.mpn))
        return 3
    if code:
        meta["anyway"] = True
    meta["footprint"] = os.path.basename(mod)[:-len(".kicad_mod")]
    json.dump(meta, open(os.path.join(stage, "meta.json"), "w"), indent=2)
    print(f"\nstaged {meta['footprint']} -> {os.path.relpath(mod, board.dir)}")
    if a.lcsc and meta.get("symbol"):
        print(f"a symbol also came down: {meta['symbol']} -- import it "
              f"through the symbol editor if you want it; fp.py only "
              f"installs footprints and 3D models.")
    print(f"\nNEXT: python3 tools/fp/fp.py verify {a.mpn}")
    return 0


def fork(mpn, why):
    """Step 5: nothing downloadable exists, so it gets AUTHORED -- and the
    choice of who authors it is the user's, not this script's."""
    print(f"""
fp: could not get a footprint for {mpn}.
    {why}

NOTHING ELSE IS AUTOMATIC. Ask the user which of these they want, and do
not pick for them:

  (a) AUTHOR IT FROM THE DATASHEET, here, with Konnect's library toolset:
        load_toolset('library')
        create_footprint(...) / edit_footprint_pad(...) /
        set_footprint_graphics(...) / set_footprint_models(...)
      The land pattern comes from handoff/datasheets/{mpn}.pdf -- the
      recommended-land table, never a scaled body drawing. Then:
        python3 tools/fp/fp.py adopt <name> --mpn {mpn} --source authored \\
            --datasheet {mpn} --pages <n> --note "<what you matched>"

  (b) THE USER DRAWS IT in the KiCad footprint editor, or fetches it from a
      portal that needs their login (SnapEDA, Ultra Librarian, SamacSys) and
      hands you the file. Then:
        python3 tools/fp/fp.py get {mpn} --file <path> --origin <where from>
      or, for a hand-drawn one already in the library:
        python3 tools/fp/fp.py adopt <name> --mpn {mpn} --source manual ...

  Either way it ends with a signed provenance record, so `check` treats an
  authored footprint exactly like a downloaded one.
""")
    return 2


# --- verify / sign / install ------------------------------------------------
def _locate(board, mpn):
    """The staged footprint if it is the newer copy, else the installed one.

    Staging is not cleared by `install`, so once a part is installed its
    staged copy is stale. Preferring it unconditionally made `sign` re-sign
    the OLD download after the installed footprint had been corrected -- the
    record then described a file that is not in the library. Whichever was
    written last is the one being worked on: a fresh `get` is newer than the
    installed copy, an edit after install is newer than the staged one."""
    staged = None
    d = os.path.join(stage_dir(board, mpn), "staged.pretty")
    if os.path.isdir(d):
        f = [x for x in os.listdir(d) if x.endswith(".kicad_mod")]
        if f:
            staged = os.path.join(d, f[0])
    installed = None
    for name, rec in board.load()["footprints"].items():
        if rec.get("mpn") == mpn:
            p = os.path.join(board.pretty, name + ".kicad_mod")
            if os.path.exists(p):
                installed = p
                break
    if staged and (not installed or
                   os.path.getmtime(staged) > os.path.getmtime(installed)):
        return staged, True
    if installed:
        return installed, False
    return None, False


def cmd_verify(a, board):
    path, staged = _locate(board, a.mpn)
    if not path:
        die(f"nothing staged or installed for {a.mpn} -- run `get` first")
    fp = read_fp(path)
    cs = report(fp, board, a.pins)
    if shutil.which("kicad-cli"):
        out = os.path.join(os.path.dirname(os.path.dirname(path)), "svg") \
            if staged else os.path.join(stage_dir(board, a.mpn), "svg")
        os.makedirs(out, exist_ok=True)
        r = subprocess.run(["kicad-cli", "fp", "export", "svg", "-o", out,
                            "--fp", fp["name"], os.path.dirname(path)],
                           capture_output=True, text=True)
        svg = os.path.join(out, fp["name"] + ".svg")
        if r.returncode == 0 and os.path.exists(svg):
            print(f"\n    drawing: {os.path.relpath(svg, board.dir)}"
                  f"   (open it -- the numbers above are not a picture)")
        else:
            cs.append(("FAIL", "KiCad could not draw this footprint: "
                       + (r.stderr.strip()[-200:] or "no SVG written")))
            print(f"\n    FAIL: {cs[-1][1]}")
    mp = os.path.join(stage_dir(board, a.mpn), "meta.json")
    if a.pins is not None and os.path.exists(mp):
        meta = json.load(open(mp))
        meta["pins"] = a.pins            # `sign` holds the footprint to it
        json.dump(meta, open(mp, "w"), indent=2)
    ds = ds_path(board, a.mpn) or ""
    print(f"""
NOW DO THE PART THAT IS NOT AUTOMATIC.  Open
  {os.path.relpath(ds, board.dir) if ds
   else 'handoff/datasheets/ -- no PDF filed under this MPN; see 13_datasheets.md'}
and find the RECOMMENDED LAND PATTERN (not the body/package drawing: they
differ, and the body drawing is the one that fits and does not solder).
Compare, number by number: pad count and numbering, pad size, pad pitch,
row/column gap, overall span, and anything the note says about mask (SMD vs
NSMD) or paste.

Then record what you compared:
  python3 tools/fp/fp.py sign {a.mpn} --datasheet <MPN> --pages <n> \\
      --note "<the dimensions you matched, in the datasheet's own words>"
""")
    return 1 if any(s == "FAIL" for s, _ in cs) else 0


def cmd_sign(a, board):
    path, staged = _locate(board, a.mpn)
    if not path:
        die(f"nothing staged or installed for {a.mpn}")
    fp = read_fp(path)
    meta = {}
    mp = os.path.join(stage_dir(board, a.mpn), "meta.json")
    if os.path.exists(mp):
        meta = json.load(open(mp))
    pins = a.pins if a.pins is not None else meta.get("pins")   # what `verify` was told
    cs = checks(fp, board, pins)
    fails = [m for s, m in cs if s == "FAIL"]
    if fails and not a.override:
        die("verify reports FAIL:\n  - " + "\n  - ".join(fails) +
            "\nFix it, or sign with --override \"why this is right anyway\".")
    dsname, dshash = _datasheet(board, a.datasheet)
    rec = board.load()
    entry = rec["footprints"].get(fp["name"], {})
    entry.update({
        "mpn": a.mpn,
        "source": meta.get("source", entry.get("source", "unknown")),
        "lcsc": meta.get("lcsc", entry.get("lcsc")),
        "url": meta.get("url", entry.get("url")),
        "fetched": meta.get("fetched", entry.get("fetched", TODAY)),
        "sha256": sha(path),
        "models": [os.path.basename(m) for m in fp["models"]],
        "verified": {"datasheet": dsname, "datasheet_sha256": dshash,
                     "pages": a.pages, "date": TODAY, "note": a.note,
                     "pads": len(fp["pads"]),
                     "override": a.override or None},
    })
    entry["verified"] = {k: v for k, v in entry["verified"].items()
                         if v is not None}
    entry = {k: v for k, v in entry.items() if v is not None}
    rec["footprints"][fp["name"]] = entry
    board.save(rec)
    print(f"signed {fp['name']} against {dsname}.pdf p.{a.pages} "
          f"({dshash}).")
    if staged:
        print(f"NEXT: python3 tools/fp/fp.py install {a.mpn}")
    return 0


def _datasheet(board, which):
    """(name, sha) of a datasheet IN handoff/datasheets/ -- by MPN, by file
    name, or by a path that points into that folder. Nothing else: a PDF from
    anywhere else has no hash the concept session can move, so `check` could
    never say it was revised (kicad_project/CLAUDE.md: that is a question)."""
    p = ds_path(board, which[:-4] if which.lower().endswith(".pdf") else which)
    if not p:
        p2 = os.path.abspath(os.path.expanduser(which))
        if os.path.isfile(p2) and os.path.dirname(p2) == os.path.abspath(board.datasheets):
            p = p2
    if not p:
        avail = sorted(f[:-4] for f in os.listdir(board.datasheets)
                       if f.lower().endswith(".pdf")) \
            if os.path.isdir(board.datasheets) else []
        die(f"no datasheet '{which}' in handoff/datasheets/. A datasheet that "
            "is not there is a QUESTIONS.md entry, not a download. Filed there:\n  "
            + ("\n  ".join(avail) or "none -- ask for it in QUESTIONS.md"))
    return os.path.basename(p)[:-4], sha(p)


def cmd_install(a, board):
    stage = stage_dir(board, a.mpn)
    src, staged = _locate(board, a.mpn)
    if not src or not staged:
        die(f"nothing staged for {a.mpn}")
    fp = read_fp(src)
    rec = board.load()
    entry = rec["footprints"].get(fp["name"])
    if not entry or "verified" not in entry:
        die(f"{fp['name']} is not signed -- verify it and sign it first. "
            f"The gate is the point of this script.")
    if entry["sha256"] != sha(src):
        die("the staged footprint changed after it was signed. Re-verify "
            "and re-sign.")
    os.makedirs(board.pretty, exist_ok=True)
    os.makedirs(board.shapes, exist_ok=True)
    # Anything staged BEFORE the slug fix in _harvest can still carry a colon
    # in its name, which yields an unresolvable id. Rename it on the way in,
    # and re-key the record below so `check` still matches file to record.
    newname = slug(fp["name"])
    dst = os.path.join(board.pretty, newname + ".kicad_mod")
    if os.path.exists(dst) and not a.force:
        die(f"{os.path.relpath(dst, board.dir)} already exists -- --force to "
            f"replace it.")
    # Provenance also goes INSIDE the file: footprints.json can be lost, a
    # footprint that travels into another project cannot say where it came
    # from, and the descr is the one field every KiCad tool carries along.
    v = entry["verified"]
    tag = (f"{a.mpn}; {entry.get('source')}"
           f"{' ' + entry['lcsc'] if entry.get('lcsc') else ''}; verified "
           f"against {v['datasheet']} p.{v['pages']} on {v['date']}")
    text = open(src).read()
    tree = raw_parse(text)
    _tag_descr(tree, tag)
    tree[1] = '"%s"' % newname
    new = _normalise(raw_dump(tree), newname)      # dies if KiCad cannot load it
    if land_hash(new) != land_hash(_normalise(text, newname)):
        die("the land changed while tagging the description -- nothing installed")
    open(dst, "w").write(new)
    copied = sorted(os.listdir(os.path.join(stage, "staged.3dshapes")))
    for f in copied:
        shutil.copy2(os.path.join(stage, "staged.3dshapes", f),
                     os.path.join(board.shapes, f))
    entry["sha256"] = sha(dst)
    entry["installed"] = TODAY
    if newname != fp["name"]:
        rec["footprints"].pop(fp["name"], None)
        rec["footprints"][newname] = entry
    board.save(rec)
    # The stage is spent: left behind, a later `verify`/`sign` could pick the
    # pre-install copy up again by its timestamp.
    shutil.rmtree(stage, ignore_errors=True)
    print(f"installed {os.path.relpath(dst, board.dir)}")
    for f in copied:
        ref = " (referenced)" if f in (entry.get("models") or []) else \
            " (kept beside it -- KiCad's STEP exporter finds it by name)"
        print(f"          lib/{board.stem}.3dshapes/{f}{ref}")
    print(f"\nAdd it to the part in the schematic as "
          f"`{board.stem}:{newname}`, and make sure "
          f"`{board.stem}` is in fp-lib-table (fp.py check tells you).")
    return 0


def cmd_adopt(a, board):
    """File a footprint that was AUTHORED (Konnect or by hand) so that the
    audit treats it like any other -- the fork in `get` ends here."""
    path = os.path.join(board.pretty, a.name + ".kicad_mod")
    if not os.path.exists(path):
        die(f"no {os.path.relpath(path, board.dir)} -- adopt takes the name "
            f"of a footprint that is already in the library")
    fp = read_fp(path)
    if fp["name"] != a.name:
        die(f"{a.name}.kicad_mod calls itself '{fp['name']}' inside. KiCad goes by "
            "the file name, the records by the inner one: make them the same "
            "(rename it in the footprint editor or through Konnect), then adopt.")
    cs = checks(fp, board, a.pins)
    fails = [m for s, m in cs if s == "FAIL"]
    if fails and not a.override:
        report(fp, board, a.pins)
        die("FAIL above. Fix it, or adopt with --override \"reason\".")
    dsname, dshash = _datasheet(board, a.datasheet)
    rec = board.load()
    rec["footprints"][fp["name"]] = {
        "mpn": a.mpn, "source": a.source, "url": a.origin or None,
        "fetched": TODAY, "installed": TODAY, "sha256": sha(path),
        "models": [os.path.basename(m) for m in fp["models"]],
        "verified": {"datasheet": dsname, "datasheet_sha256": dshash,
                     "pages": a.pages, "date": TODAY, "note": a.note,
                     "pads": len(fp["pads"]),
                     "override": a.override or None},
    }
    e = rec["footprints"][fp["name"]]
    e["verified"] = {k: v for k, v in e["verified"].items() if v is not None}
    rec["footprints"][fp["name"]] = {k: v for k, v in e.items()
                                     if v is not None}
    board.save(rec)
    print(f"adopted {fp['name']} ({a.source}) against {dsname}.pdf "
          f"p.{a.pages}.")
    return 0


def cmd_check(a, board):
    """The audit. Everything it can compare, it compares."""
    rec = board.load()["footprints"]
    prob = []
    have = sorted(f[:-len(".kicad_mod")] for f in os.listdir(board.pretty)
                  if f.endswith(".kicad_mod")) \
        if os.path.isdir(board.pretty) else []
    for name in have:
        p = os.path.join(board.pretty, name + ".kicad_mod")
        r = rec.get(name)
        if not r:
            prob.append(f"{name}: in the library with NO provenance record. "
                        f"Where did it come from? `adopt` it.")
            continue
        if "verified" not in r:
            prob.append(f"{name}: never signed against a datasheet.")
            continue
        if r.get("sha256") != sha(p):
            prob.append(f"{name}: EDITED since it was signed "
                        f"({r['verified']['date']}). Re-verify and re-sign"
                        f" (or, if only silk/courtyard moved, `tidy` does "
                        f"both).")
        v = r["verified"]
        if r.get("source") == "stock":
            # Derived from KiCad's own library: the datasheet is KiCad's
            # file, and the promise is that the LAND never left it.
            st = r.get("stock") or {}
            sp = os.path.expanduser(st.get("path", ""))
            if not os.path.exists(sp) and ":" in (st.get("lib_id") or ""):
                # another machine keeps KiCad's library elsewhere
                lib, nm = st["lib_id"].split(":", 1)
                sp = next((q for q in (os.path.join(d, lib + ".pretty", nm + ".kicad_mod")
                                       for d in STOCK_DIRS) if os.path.exists(q)), sp)
            if not os.path.exists(sp):
                prob.append(f"{name}: derived from KiCad's "
                            f"{st.get('lib_id')}, which is no longer at "
                            f"{sp}.")
            elif sha(sp) != st.get("sha256"):
                if land_hash(_normalise(open(sp).read(), name)) == \
                        st.get("land_sha256"):
                    print(f"note: {name}: KiCad's {st.get('lib_id')} file "
                          f"changed since it was derived, but its land did "
                          f"not (silk, text or model only).")
                else:
                    prob.append(
                        f"{name}: KiCad's own {st.get('lib_id')} HAS CHANGED "
                        f"ITS LAND since this copy was derived "
                        f"({v.get('date')}) -- a KiCad update. Nothing about "
                        f"your copy changed; compare the two lands and "
                        f"`derive --force` again if KiCad's is the one to "
                        f"follow.")
            if land_hash(open(p).read()) != st.get("land_sha256"):
                prob.append(f"{name}: its LAND no longer matches KiCad's "
                            f"{st.get('lib_id')}. A derived footprint may "
                            f"change silk and courtyard only; a land of its "
                            f"own needs a datasheet and `adopt`.")
        if v.get("datasheet"):                 # any source, stock included
            ds = os.path.join(board.datasheets, v["datasheet"] + ".pdf")
            if os.path.exists(ds):
                if sha(ds) != v["datasheet_sha256"]:
                    prob.append(
                        f"{name}: {v['datasheet']} HAS BEEN REVISED since "
                        f"this footprint was verified against "
                        f"p.{v['pages']}. Nothing about your drawing "
                        f"changed -- that is why this check exists. Re-read "
                        f"the land pattern and re-sign.")
            elif os.path.isdir(board.datasheets):
                prob.append(f"{name}: was verified against "
                            f"{v['datasheet']}, which is no longer in "
                            f"handoff/datasheets/.")
        fp = read_fp(p)
        code = standard_chip(fp)
        if code and (v.get("override") or r.get("anyway")):
            print(f"note: {name} measures as a standard {code} chip land; it was "
                  f"signed as something else: {v.get('override') or '--anyway'}")
        elif code:
            prob.append(f"{name}: measures as a standard {code} chip land. "
                        f"If it is a resistor, capacitor or ferrite bead, "
                        f"delete it and use KiCad's stock "
                        f"R/C/L_{code}_{CHIP[code][0]} -- a private copy of "
                        f"a public land pattern is one more thing to keep "
                        f"current, for nothing. If it is a diode or "
                        f"similar, re-sign it with --override saying so.")
        for m in fp["models"]:
            if not model_path(fp, board, m):
                if m.startswith("${KICAD") or m.startswith("${KISYS3DMOD}"):
                    print(f"note: {name}: KiCad's package ships no model for "
                          f"its own footprint ({os.path.basename(m)}); the "
                          f"vendor's STEP would go through set_footprint_"
                          f"models + tidy.")
                else:
                    prob.append(f"{name}: 3D model missing -- {m}")
        if not fp["models"]:
            print(f"note: {name} has no 3D model.")
    for name in sorted(set(rec) - set(have)):
        prob.append(f"{name}: recorded in footprints.json but not in "
                    f"{os.path.basename(board.pretty)}.")
    tbl = os.path.join(board.dir, "fp-lib-table")
    names = set(re.findall(r'\(lib\s*\(name\s+"?([^")\s]+)"?\)',
                           open(tbl).read())) if os.path.exists(tbl) else set()
    if have and board.stem not in names:
        prob.append(f"fp-lib-table has no entry for the project library"
                    f"{'' if os.path.exists(tbl) else ' (the file does not exist yet)'}. "
                    f"Add:\n      (lib (name \"{board.stem}\") (type "
                    f"\"KiCad\") (uri \"${{KIPRJMOD}}/lib/{board.stem}"
                    f".pretty\") (options \"\") (descr \"\"))")
    print(f"{len(have)} footprint(s) in lib/{board.stem}.pretty, "
          f"{len(rec)} record(s).")
    for p in prob:
        print("  PROBLEM: " + p)
    if not prob:
        print("  all signed, all current.")
    return 1 if prob else 0


def cmd_list(a, board):
    rec = board.load()["footprints"]
    if not rec:
        print("nothing recorded yet.")
        return 0
    print(f"{'footprint':<36} {'MPN':<24} {'source':<9} {'3D':<3} verified")
    for name, r in sorted(rec.items()):
        v = r.get("verified") or {}
        against = v.get("datasheet") or \
            (("KiCad " + v["stock"]) if v.get("stock") else "NOT SIGNED")
        print(f"{name:<36} {r.get('mpn', '?')[:24]:<24} "
              f"{r.get('source', '?'):<9} "
              f"{'yes' if r.get('models') else 'no ':<3} "
              f"{against} "
              f"{('p.' + str(v['pages'])) if v.get('pages') else ''} "
              f"{v.get('date', '')}")
    return 0


# --- silk / courtyard edits that leave the land alone ----------------------
# The pads are the datasheet's; the silkscreen and the courtyard are layout
# decisions; the default convention here is NO body outlines on silk (a pin-1
# mark is enough; the body is on F.Fab) and IPC-nominal courtyards.  Everything below
# edits those two layers and hashes the pad nodes before and after, so a
# change to the land -- the one thing a signature vouches for -- is refused.
GRAPHICS = ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly")
MARK_RADIUS = 0.25       # a silk circle this small is a pin-1 dot, not a body


def raw_parse(text):
    """parse(), but atoms keep their source spelling (quotes included), so
    the tree can be written back without guessing what was a string."""
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "()":
            toks.append(c)
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            toks.append(text[i:j + 1])
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in "()":
                j += 1
            toks.append(text[i:j])
            i = j
    pos = 0

    def rd():
        nonlocal pos
        t = toks[pos]
        pos += 1
        if t == "(":
            lst = []
            while toks[pos] != ")":
                lst.append(rd())
            pos += 1
            return lst
        return t
    return rd()


def raw_dump(node, depth=0):
    """Valid but plain; `kicad-cli fp upgrade --force` reformats afterwards
    so the library never shows two styles."""
    if not isinstance(node, list):
        return node
    out, first = "(", True
    for x in node:
        if isinstance(x, list):
            out += "\n" + "\t" * (depth + 1) + raw_dump(x, depth + 1)
        else:
            out += ("" if first else " ") + x
        first = False
    return out + ("\n" + "\t" * depth + ")" if any(isinstance(x, list)
                                                    for x in node) else ")")


def rq(atom):
    return atom[1:-1] if len(atom) >= 2 and atom[0] == '"' else atom


def rkid(node, name):
    for x in node:
        if isinstance(x, list) and x and x[0] == name:
            return x
    return None


def rlayer(node):
    k = rkid(node, "layer")
    return rq(k[1]) if k else ""


def rnums(node):
    out = []
    for v in node[1:]:
        if isinstance(v, list):
            break
        try:
            out.append(float(v))
        except ValueError:
            break
    return out


def _norm(node):
    """The same node with uuids dropped and numbers canonical, so two
    serialisations of one land hash alike."""
    if not isinstance(node, list):
        try:
            return repr(round(float(node), 6))
        except ValueError:
            return node
    return [_norm(x) for x in node
            if not (isinstance(x, list) and x and x[0] == "uuid")]


def land_hash(text):
    """sha of every (pad ...) node and every graphic on a copper, paste or mask
    layer (a net tie's polygon is land too), order-free, uuid-free,
    number-normal."""
    def land(n):
        return isinstance(n, list) and n and (
            n[0] == "pad" or (n[0] in GRAPHICS and re.search(
                r"\.(Cu|Paste|Mask)$", rlayer(n))))
    pads = sorted(json.dumps(_norm(n)) for n in raw_parse(text) if land(n))
    return sha("\n".join(pads).encode())


def _pts(node):
    if node[0] == "fp_circle":
        c, e = rkid(node, "center"), rkid(node, "end")
        if c and e:
            (cx, cy), (ex, ey) = rnums(c)[:2], rnums(e)[:2]
            r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
            return [(cx - r, cy - r), (cx + r, cy + r)]
    out = []
    for k in ("start", "end", "center", "mid"):
        c = rkid(node, k)
        if c:
            v = rnums(c)
            if len(v) >= 2:
                out.append((v[0], v[1]))
    xy = rkid(node, "pts")
    if xy:
        for p in xy[1:]:
            if isinstance(p, list) and p[0] == "xy":
                v = rnums(p)
                out.append((v[0], v[1]))
    return out


def _mark(node):
    """A pin-1 mark rather than a body line: any filled/unfilled polygon
    (KiCad's triangles and corner squares), or a small circle."""
    if node[0] == "fp_poly":
        return True
    if node[0] == "fp_circle":
        p = _pts(node)
        return p and (p[1][0] - p[0][0]) / 2 <= MARK_RADIUS + 1e-6
    return False


def _describe(node):
    p = _pts(node)
    return f"{node[0][3:]} " + " ".join(f"({x:g},{y:g})" for x, y in p[:4])


def _uuid():
    import uuid
    return '"%s"' % uuid.uuid4()


def _rect_outline(rects):
    """The outline of a union of axis-aligned rectangles, as ONE closed loop of
    corners (collinear points merged), or None if the union is not a single
    piece without holes.  Coordinate compression: every distinct x and y makes
    a grid, a cell is inside if any rectangle covers its centre, and the
    boundary is the cells' edges that are not shared."""
    xs = sorted({r[0] for r in rects} | {r[2] for r in rects})
    ys = sorted({r[1] for r in rects} | {r[3] for r in rects})
    nx, ny = len(xs) - 1, len(ys) - 1

    def inside(i, j):
        if not (0 <= i < nx and 0 <= j < ny):
            return False
        cx, cy = (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2
        return any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rects)

    nxt = {}
    for i in range(nx):
        for j in range(ny):
            if not inside(i, j):
                continue
            for a, b, ok in (((i, j), (i + 1, j), inside(i, j - 1)),
                             ((i + 1, j), (i + 1, j + 1), inside(i + 1, j)),
                             ((i + 1, j + 1), (i, j + 1), inside(i, j + 1)),
                             ((i, j + 1), (i, j), inside(i - 1, j))):
                if not ok:
                    if a in nxt:
                        return None              # a pinch point: two loops touch
                    nxt[a] = b
    if not nxt:
        return None
    start = min(nxt)
    loop, k = [start], nxt[start]
    while k != start:
        loop.append(k)
        k = nxt[k]
    if len(loop) != len(nxt):
        return None                              # more than one loop (a hole, or two pieces)
    pts = [(xs[i], ys[j]) for i, j in loop]
    out = []
    for n, q in enumerate(pts):
        a, b = pts[n - 1], pts[(n + 1) % len(pts)]
        if (a[0] == q[0] == b[0]) or (a[1] == q[1] == b[1]):
            continue                             # collinear: not a corner
        out.append(q)
    return out


def tidy_tree(tree, silk_keep=(), silk_dot=None, courtyard=None,
              silk_drop=(), fab_from_silk=False, drop_props=(),
              mask_bridges=False, contour=False):
    """Drop body silk, keep marks, rebuild the courtyard. Returns the log."""
    log = {"silk_dropped": [], "silk_kept": []}
    if mask_bridges:
        # KiCad's per-footprint "allow bridged solder mask apertures": the
        # fab's minimum web cannot fit between 0.5 mm-pitch pads, so the
        # opening is ganged (14_fab.md) rather than drawn as a sliver.
        attr = rkid(tree, "attr")
        if attr is None:
            die("footprint has no (attr) node")
        if "allow_soldermask_bridges" not in attr:
            attr.append("allow_soldermask_bridges")
            log["mask_bridges"] = True
    if drop_props:
        # A converter's extra field (easyeda2kicad's "LCSC Part") is copied
        # onto every placed instance and blocks Konnect's library refresh;
        # the value is already in the descr and in footprints.json.
        gone = [n for n in tree if isinstance(n, list) and n[0] == "property"
                and rq(n[1]) in drop_props]
        tree = [n for n in tree if not any(n is g for g in gone)]
        log["props_dropped"] = [f"{rq(n[1])}={rq(n[2])}" for n in gone]
    silk = [n for n in tree if isinstance(n, list) and n[0] in GRAPHICS
            and rlayer(n) == "F.SilkS"]
    drop, gone = [], []
    for i, n in enumerate(silk):
        if i not in silk_drop and (i in silk_keep or _mark(n)):
            log["silk_kept"].append(f"{i}: {_describe(n)}")
        else:
            log["silk_dropped"].append(f"{i}: {_describe(n)}")
            drop.append(id(n))
            if i not in silk_drop:          # junk stays junk, on any layer
                gone.append(n)
    if courtyard is not None:
        drop += [id(n) for n in tree if isinstance(n, list)
                 and n[0] in GRAPHICS and rlayer(n) == "F.CrtYd"]
    new = [n for n in tree if id(n) not in drop]
    added = []
    has_fab = any(isinstance(n, list) and n[0] in GRAPHICS
                  and rlayer(n) == "F.Fab" for n in tree)
    if fab_from_silk and gone and not has_fab:
        # No assembly drawing at all (a converted footprint): the body the
        # silk showed is still worth having, on the layer meant for it.
        import copy
        for n in gone:
            m = copy.deepcopy(n)
            rkid(m, "layer")[1] = '"F.Fab"'
            st = rkid(m, "stroke")
            if st and rkid(st, "width"):
                rkid(st, "width")[1] = "0.1"
            u = rkid(m, "uuid")
            if u:
                u[1] = _uuid()
            added.append(m)
        log["fab_from_silk"] = len(gone)
    if silk_dot:
        x, y = silk_dot
        added.append(["fp_circle", ["center", f"{x:g}", f"{y:g}"],
                      ["end", f"{x + 0.15:g}", f"{y:g}"],
                      ["stroke", ["width", "0.1"], ["type", "solid"]],
                      ["fill", "yes"], ["layer", '"F.SilkS"'],
                      ["uuid", _uuid()]])
        log["silk_dot"] = [x, y]
    if courtyard is not None:
        pts = []
        for n in new + added:                 # the footprint AS EDITED above
            if not isinstance(n, list):
                continue
            if n[0] == "pad":
                at, sz = rnums(rkid(n, "at")), rnums(rkid(n, "size"))
                w, h = (sz[1], sz[0]) if len(at) > 2 and round(at[2]) % 180 \
                    == 90 else (sz[0], sz[1])
                pts += [(at[0] - w / 2, at[1] - h / 2),
                        (at[0] + w / 2, at[1] + h / 2)]
                # A custom pad's copper is its primitives, drawn in the pad's
                # own frame (anchor at the origin, then the pad's rotation).
                prim = rkid(n, "primitives")
                if prim:
                    import math
                    th = math.radians(at[2]) if len(at) > 2 else 0.0
                    c, s = math.cos(th), math.sin(th)
                    for g in prim[1:]:
                        if not isinstance(g, list):
                            continue
                        loc = _pts(["fp_" + g[0][3:]] + g[1:])
                        if g[0] == "gr_circle" and len(loc) == 2:
                            pass                      # already a bbox
                        for x, y in loc:
                            pts.append((at[0] + x * c + y * s,
                                        at[1] - x * s + y * c))
            elif n[0] in GRAPHICS and rlayer(n) == "F.Fab":
                pts += _pts(n)
        if not pts:
            die("no pads and no F.Fab body to build a courtyard around")
        b = _bbox(pts)
        import math
        out_ = lambda r: (math.floor((r[0] - courtyard) * 100) / 100,
                          math.floor((r[1] - courtyard) * 100) / 100,
                          math.ceil((r[2] + courtyard) * 100) / 100,
                          math.ceil((r[3] + courtyard) * 100) / 100)
        if contour:
            # The courtyard follows the part instead of boxing it: the pad field
            # plus the margin, each closed F.Fab body (rect or polygon; lines are
            # markers, not body) plus the margin, and where the two do not touch
            # the band between them over their common extent -- an angled
            # header's bent legs run there.  One rectangle round all of it (the
            # default) claims the body's full length over the pin field, which is
            # bare legs on an angled box header.
            padpts = []
            for n in tree:
                if isinstance(n, list) and n[0] == "pad":
                    at, sz = rnums(rkid(n, "at")), rnums(rkid(n, "size"))
                    w, h = (sz[1], sz[0]) if len(at) > 2 and round(at[2]) % 180 \
                        == 90 else (sz[0], sz[1])
                    padpts += [(at[0] - w / 2, at[1] - h / 2),
                               (at[0] + w / 2, at[1] + h / 2)]
            if not padpts:
                die("--courtyard-contour needs pads")
            A = out_(_bbox(padpts))
            bodies = [out_(_bbox(_pts(n))) for n in new + added
                      if isinstance(n, list) and n[0] in ("fp_rect", "fp_poly")
                      and rlayer(n) == "F.Fab"]
            if not bodies:
                die("--courtyard-contour needs a closed F.Fab body drawn as ONE "
                    "rect or poly; a body drawn as separate lines (most stock "
                    "connectors) is not recognised -- use the rectangle")
            rects = [A] + bodies
            for Bd in bodies:
                oy = (max(A[1], Bd[1]), min(A[3], Bd[3]))
                ox = (max(A[0], Bd[0]), min(A[2], Bd[2]))
                if oy[0] < oy[1] and (A[2] < Bd[0] or Bd[2] < A[0]):
                    rects.append((min(A[2], Bd[2]), oy[0], max(A[0], Bd[0]), oy[1]))
                elif ox[0] < ox[1] and (A[3] < Bd[1] or Bd[3] < A[1]):
                    rects.append((ox[0], min(A[3], Bd[3]), ox[1], max(A[1], Bd[1])))
            poly = _rect_outline(rects)
            if not poly:
                die("the pads and the body do not make one outline -- use the "
                    "rectangle (omit --courtyard-contour)")
            added.append(["fp_poly", ["pts"] + [["xy", f"{x:g}", f"{y:g}"]
                                                for x, y in poly],
                          ["stroke", ["width", "0.05"], ["type", "solid"]],
                          ["fill", "no"], ["layer", '"F.CrtYd"'],
                          ["uuid", _uuid()]])
            bb = _bbox(poly)
            log["courtyard"] = list(bb)
            log["courtyard_poly"] = [list(q) for q in poly]
        x0 = math.floor((b[0] - courtyard) * 100) / 100
        y0 = math.floor((b[1] - courtyard) * 100) / 100
        x1 = math.ceil((b[2] + courtyard) * 100) / 100
        y1 = math.ceil((b[3] + courtyard) * 100) / 100
        if not contour:
            added.append(["fp_rect", ["start", f"{x0:g}", f"{y0:g}"],
                          ["end", f"{x1:g}", f"{y1:g}"],
                          ["stroke", ["width", "0.05"], ["type", "solid"]],
                          ["fill", "no"], ["layer", '"F.CrtYd"'],
                          ["uuid", _uuid()]])
            log["courtyard"] = [x0, y0, x1, y1]
    if added:
        # graphics go where KiCad keeps them: before the first pad
        k = next((i for i, n in enumerate(new)
                  if isinstance(n, list) and n[0] in ("pad", "model")),
                 len(new))
        new[k:k] = added
    return new, log


def _tag_descr(tree, tag):
    tag = tag.replace("\\", "/").replace('"', "'")
    d = rkid(tree, "descr")
    if d:
        old = rq(d[1])
        d[1] = '"%s%s"' % ((old + " -- ") if old else "", tag)
    else:
        tree.insert(2, ["descr", '"%s"' % tag])


def _normalise(text, name):
    """The text as kicad-cli writes it in the installed format.

    ⚠ Both sides of a land comparison go through this: an older-format file
    gains pad children (uuid, thermal settings) on upgrade, so hashing the
    original against the rewritten copy would call every tidy a land change.
    Nothing is written to the library here; callers compare, then copy."""
    d = tempfile.mkdtemp(prefix="fp-tidy-", suffix=".pretty")
    try:
        p = os.path.join(d, name + ".kicad_mod")
        open(p, "w").write(text if text.endswith("\n") else text + "\n")
        if shutil.which("kicad-cli"):
            r = subprocess.run(["kicad-cli", "fp", "upgrade", "--force", d],
                               capture_output=True, text=True)
            if r.returncode != 0:
                die(f"kicad-cli refused the footprint:\n{r.stderr}")
        out = open(p).read()
        raw_parse(out)                        # must still be well-formed
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _tidy_opts(a):
    def idx(v, flag):
        try:
            return tuple(int(x) for x in v.split(",") if x.strip()) if v else ()
        except ValueError:
            die(f"{flag} takes comma-separated item numbers (see --dry-run), got '{v}'")
    keep = idx(a.silk_keep, "--silk-keep")
    dot = tuple(a.silk_dot) if a.silk_dot else None
    drop = idx(a.silk_drop, "--silk-drop")
    if a.courtyard_contour and a.courtyard is None:
        die("--courtyard-contour needs --courtyard (the margin, IPC nominal 0.25)")
    return keep, dot, a.courtyard, drop, a.fab_from_silk, \
        tuple(a.drop_property or ()), a.allow_mask_bridges, a.courtyard_contour


def _print_log(log):
    for k in log["silk_dropped"]:
        print(f"    silk dropped  {k}")
    for k in log["silk_kept"]:
        print(f"    silk kept     {k}")
    if log.get("silk_dot"):
        print(f"    silk added    pin-1 dot at {tuple(log['silk_dot'])}")
    if log.get("fab_from_silk"):
        print(f"    F.Fab         {log['fab_from_silk']} dropped silk items "
              f"re-drawn there (the footprint had no assembly body)")
    for p in log.get("props_dropped") or []:
        print(f"    property      dropped {p}")
    if log.get("mask_bridges"):
        print("    attr          allow_soldermask_bridges (ganged mask opening)")
    if log.get("courtyard"):
        c = log["courtyard"]
        print(f"    courtyard     x {c[0]:+.2f}..{c[2]:+.2f}  "
              f"y {c[1]:+.2f}..{c[3]:+.2f}"
              + (f"  CONTOUR, {len(log['courtyard_poly'])} corners: "
                 + " ".join(f"({x:g},{y:g})" for x, y in log["courtyard_poly"])
                 if log.get("courtyard_poly") else "  rectangle"))


def _kicad_version():
    if shutil.which("kicad-cli"):
        r = subprocess.run(["kicad-cli", "version"], capture_output=True,
                           text=True)
        return r.stdout.strip() or "?"
    return "?"


def cmd_tidy(a, board):
    path = os.path.join(board.pretty, a.name + ".kicad_mod")
    if not os.path.exists(path):
        die(f"no {os.path.relpath(path, board.dir)}")
    rec = board.load()
    entry = rec["footprints"].get(a.name)
    if not entry or "verified" not in entry:
        die(f"{a.name} has no signed record -- `adopt` or `sign` it first; "
            f"tidy only carries a signature over, it does not create one")
    text = open(path).read()
    tree, log = tidy_tree(raw_parse(text), *_tidy_opts(a))
    print(f"tidy {a.name}:")
    _print_log(log)
    if a.dry_run:
        print("    (dry run, nothing written)")
        return 0
    before = land_hash(_normalise(text, a.name))
    new = _normalise(raw_dump(tree), a.name)
    if land_hash(new) != before:
        die("the land would change -- nothing written. tidy edits F.SilkS "
            "and F.CrtYd only; if you meant to move a pad, that is a "
            "datasheet question and a re-sign.")
    open(path, "w").write(new)
    entry["sha256"] = sha(path)
    entry.setdefault("tidy", []).append(
        {"date": TODAY, "dropped": len(log["silk_dropped"]),
         "kept": log["silk_kept"], "dot": log.get("silk_dot"),
         "fab_from_silk": log.get("fab_from_silk"),
         "props_dropped": log.get("props_dropped"),
         "mask_bridges": log.get("mask_bridges"),
         "courtyard": log.get("courtyard"),
         "courtyard_poly": log.get("courtyard_poly"), "note": a.note})
    board.save(rec)
    print(f"    written, land unchanged ({before}); record updated.")
    return 0


def cmd_derive(a, board):
    """A KiCad stock footprint, copied into the library with the silk and
    courtyard this board wants and NOTHING else changed."""
    if ":" not in a.stock:
        die("give the stock footprint as Library:Footprint")
    lib, name = a.stock.split(":", 1)
    src = next((os.path.join(d, lib + ".pretty", name + ".kicad_mod")
                for d in STOCK_DIRS
                if os.path.exists(os.path.join(d, lib + ".pretty",
                                               name + ".kicad_mod"))), None)
    if not src:
        die(f"{a.stock} is not in any of {STOCK_DIRS}")
    newname = slug(a.as_name or name)
    dst = os.path.join(board.pretty, newname + ".kicad_mod")
    if os.path.exists(dst) and not a.force and not a.dry_run:
        die(f"{os.path.relpath(dst, board.dir)} exists -- --force to redo it")
    text = open(src).read()
    code = standard_chip(read_fp(src))
    if code and not a.anyway:
        print(_passive_refusal(code, a.stock))
        return 3
    tree = raw_parse(text)
    tree, log = tidy_tree(tree, *_tidy_opts(a))
    tree[1] = '"%s"' % newname
    tag = (f"derived from KiCad {_kicad_version()} {a.stock} on {TODAY}: land "
           f"unchanged; silk body outline removed"
           + (f"; courtyard {a.courtyard:g} mm" if a.courtyard is not None
              else ""))
    _tag_descr(tree, tag)
    print(f"derive {a.stock} -> {board.stem}:{newname}")
    _print_log(log)
    if a.dry_run:
        print("    (dry run, nothing written)")
        return 0
    lh = land_hash(_normalise(text, newname))
    new = _normalise(raw_dump(tree), newname)
    if land_hash(new) != lh:
        die("the land changed on the way through -- nothing installed")
    # Judge it BEFORE it replaces anything: a failed re-derive must not cost
    # the footprint that is already in the library.
    tmp = tempfile.mkdtemp(prefix="fp-derive-")
    try:
        probe = os.path.join(tmp, newname + ".kicad_mod")
        open(probe, "w").write(new)
        cs = [(sev, m) for sev, m in checks(read_fp(probe), board)
              if not (a.anyway and "standard" in m and "chip land" in m)]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    fails = [m for s, m in cs if s == "FAIL"]
    if fails and not a.override:
        die("FAIL (nothing written):\n  - " + "\n  - ".join(fails) +
            "\nFix it, or derive with --override \"why\".")
    os.makedirs(board.pretty, exist_ok=True)
    open(dst, "w").write(new)
    fp = read_fp(dst)
    rec = board.load()
    rec["footprints"][newname] = {
        "mpn": a.mpn or f"stock:{a.stock}", "source": "stock",
        "url": f"KiCad {_kicad_version()} {a.stock}",
        "used_by": a.used_by or None,
        "anyway": True if (code and a.anyway) else None,
        # "~" for the home folder: footprints.json is committed, and a path
        # with your user name in it is personal and useless on another machine
        "stock": {"lib_id": a.stock, "path": src.replace(os.path.expanduser("~"), "~", 1),
                  "sha256": sha(src),
                  "land_sha256": lh},
        "fetched": TODAY, "installed": TODAY, "sha256": sha(dst),
        "models": [os.path.basename(m) for m in fp["models"]],
        "verified": {"stock": a.stock, "date": TODAY, "pads": len(fp["pads"]),
                     "note": a.note or tag, "override": a.override or None},
        "tidy": [{"date": TODAY, "dropped": len(log["silk_dropped"]),
                  "kept": log["silk_kept"], "dot": log.get("silk_dot"),
                  "courtyard": log.get("courtyard")}],
    }
    e = rec["footprints"][newname]
    e["verified"] = {k: v for k, v in e["verified"].items() if v is not None}
    rec["footprints"][newname] = {k: v for k, v in e.items() if v is not None}
    board.save(rec)
    for sev, msg in cs:
        print(f"    {sev}: {msg}")
    print(f"    installed lib/{board.stem}.pretty/{newname}.kicad_mod, land "
          f"{lh} == KiCad's.\n    Point the schematic at "
          f"`{board.stem}:{newname}` and Update PCB from Schematic with "
          f"'replace footprints' on.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="vendor footprints, verified, into this board's library")
    bhelp = ("boardN, the folder name or a path (default: the board folder "
             "you are in, else this session's board)")
    ap.add_argument("--board", help=bhelp)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--board", default=argparse.SUPPRESS, help=bhelp)

    class Sub:                                  # every subcommand takes --board too
        def __init__(self, sp):
            self.sp = sp

        def add_parser(self, *x, **kw):
            return self.sp.add_parser(*x, parents=[common], **kw)
    sub = Sub(ap.add_subparsers(dest="cmd", required=True))

    g = sub.add_parser("get", help="fetch and stage a footprint")
    g.add_argument("mpn")
    g.add_argument("--lcsc", help="LCSC id (C123456) -- ask Konnect's "
                                  "search_jlcpcb_parts for it")
    g.add_argument("--url", help="direct URL to a .kicad_mod, or a .zip holding one")
    g.add_argument("--file", help="a file you already saved")
    g.add_argument("--origin", help="where a --file came from (recorded)")
    g.add_argument("--pick", help="which .kicad_mod, when a zip holds several")
    g.add_argument("--name", help="rename the footprint on the way in")
    g.add_argument("--force", action="store_true")
    g.add_argument("--anyway", action="store_true",
                   help="fetch even though the land measures as a standard "
                        "chip package -- for a diode or anything else that "
                        "is not an R, C or ferrite bead")
    g.set_defaults(fn=cmd_get)

    v = sub.add_parser("verify", help="geometry report + SVG, then read the "
                                      "datasheet yourself")
    v.add_argument("mpn")
    v.add_argument("--pins", type=int, help="pad count the datasheet says")
    v.set_defaults(fn=cmd_verify)

    s = sub.add_parser("sign", help="record what you checked it against")
    s.add_argument("mpn")
    s.add_argument("--datasheet", required=True,
                   help="MPN of a PDF in handoff/datasheets/")
    s.add_argument("--pages", required=True, help="page(s) of the land table")
    s.add_argument("--note", required=True,
                   help="the dimensions you matched -- in the datasheet's "
                        "own numbers, not 'looks right'")
    s.add_argument("--pins", type=int)
    s.add_argument("--override", help="sign despite a FAIL, and say why")
    s.set_defaults(fn=cmd_sign)

    i = sub.add_parser("install", help="staged+signed -> the library")
    i.add_argument("mpn")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_install)

    d = sub.add_parser("adopt", help="file a footprint you authored by hand "
                                     "or with Konnect")
    d.add_argument("name", help="footprint name already in the library")
    d.add_argument("--mpn", required=True)
    d.add_argument("--source", default="authored",
                   choices=["authored", "manual", "vendor"])
    d.add_argument("--origin", help="URL or tool it came from")
    d.add_argument("--datasheet", required=True)
    d.add_argument("--pages", required=True)
    d.add_argument("--note", required=True)
    d.add_argument("--pins", type=int)
    d.add_argument("--override")
    d.set_defaults(fn=cmd_adopt)

    def tidy_args(p):
        p.add_argument("--silk-keep", help="comma-separated indices of "
                       "F.SilkS items to keep besides the marks (see "
                       "--dry-run for the numbering)")
        p.add_argument("--silk-drop", help="indices to drop even though "
                       "they look like marks (a converter's junk dot)")
        p.add_argument("--silk-dot", nargs=2, type=float, metavar=("X", "Y"),
                       help="a pin-1 dot to add, footprint mm (e.g. "
                            "--silk-dot -3.2 -2.9)")
        p.add_argument("--fab-from-silk", action="store_true",
                       help="if the footprint has no F.Fab body, re-draw the "
                            "dropped silk there")
        p.add_argument("--allow-mask-bridges", action="store_true",
                       help="gang the pads' mask opening (a pitch the fab's "
                            "minimum web cannot fit between)")
        p.add_argument("--drop-property", action="append", metavar="NAME",
                       help="remove a footprint field (e.g. \"LCSC Part\"); "
                            "repeatable")
        p.add_argument("--courtyard", type=float,
                       help="rebuild F.CrtYd this far beyond pads/body, mm "
                            "(IPC nominal is 0.25; omit to keep it)")
        p.add_argument("--courtyard-contour", action="store_true",
                       help="with --courtyard: follow the part (pad field + "
                            "each closed F.Fab body + the band between) "
                            "instead of one rectangle round both -- for an "
                            "angled header whose pin field is bare legs")
        p.add_argument("--note")
        p.add_argument("--dry-run", action="store_true")

    t = sub.add_parser("tidy", help="drop body silk / rebuild the courtyard "
                                    "of a library footprint, land untouched")
    t.add_argument("name", help="footprint name in the library")
    tidy_args(t)
    t.set_defaults(fn=cmd_tidy)

    dv = sub.add_parser("derive", help="copy a KiCad stock footprint into "
                                       "the library, tidied, land unchanged")
    dv.add_argument("stock", help="Library:Footprint, e.g. Diode_SMD:D_SOD-323")
    dv.add_argument("--as", dest="as_name", help="name in the library "
                                                "(default: the stock name)")
    dv.add_argument("--mpn", help="recorded MPN (default stock:<lib id>)")
    dv.add_argument("--used-by", help="refs and parts that use it, recorded")
    dv.add_argument("--force", action="store_true")
    dv.add_argument("--anyway", action="store_true",
                    help="derive even though the land measures as a standard "
                         "chip package (a diode, an LED, a fuse)")
    dv.add_argument("--override")
    tidy_args(dv)
    dv.set_defaults(fn=cmd_derive)

    c = sub.add_parser("check", help="audit the library against the records")
    c.set_defaults(fn=cmd_check)
    ls = sub.add_parser("list", help="what is recorded")
    ls.set_defaults(fn=cmd_list)

    a = ap.parse_args()
    return a.fn(a, Board(a.board))


if __name__ == "__main__":
    sys.exit(main())
