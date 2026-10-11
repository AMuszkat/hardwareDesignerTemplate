"""
A board's schematic, read: the sheet tree, every placed symbol with its pins
where KiCad puts them, wires, labels, flags and sheet symbols, plus KiCad's
own netlist. Read-only -- the files are opened, never written.

Coordinates are schematic millimetres, y down, as in the .kicad_sch file and
in kicad-cli's SVG export.
"""
import math
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import sexpr
from sexpr import every, first, value, atom, flag

PAPER = {"A5": (210, 148), "A4": (297, 210), "A3": (420, 297), "A2": (594, 420),
         "A1": (841, 594), "A0": (1189, 841), "USLetter": (279.4, 215.9),
         "USLegal": (355.6, 215.9), "USLedger": (431.8, 279.4), "A": (279.4, 215.9),
         "B": (431.8, 279.4), "C": (558.8, 431.8), "D": (863.6, 558.8), "E": (1117.6, 863.6)}
ISO = ["A4", "A3", "A2", "A1", "A0"]
FRAME = 12.0                 # KiCad's default drawing sheet: inner frame inset, mm
TITLE_BLOCK = (108.0, 32.0)  # its title block, bottom-right inside the frame
CHAR_W = 0.95                # stroke-font advance per character, x text height (kicad-cli SVG, measured)
GROUND_RE = re.compile(r"^(GND\w*|\w*_GND|VSS\w*|AGND|DGND|PGND|0V)$", re.I)


def fnum(s):
    return float(s)


def paper_size(name, portrait=False):
    w, h = PAPER.get(name, PAPER["A4"])
    return (h, w) if portrait else (w, h)


# -- transforms ------------------------------------------------------------

def xform(px, py, rot, mirror=None):
    """A library point (y up) -> its offset from the symbol origin on the sheet
    (y down), for a symbol at `rot` degrees with optional (mirror x|y)."""
    x, y = px, -py
    t = math.radians(rot)
    c, s = round(math.cos(t)), round(math.sin(t))
    x, y = x * c + y * s, -x * s + y * c
    if mirror == "x":
        y = -y
    elif mirror == "y":
        x = -x
    return x, y


def xdir(angle, rot, mirror=None):
    """A library direction in degrees -> a unit vector on the sheet."""
    t = math.radians(angle)
    return xform(round(math.cos(t)), round(math.sin(t)), rot, mirror)


# -- library symbols -------------------------------------------------------

class Pin:
    __slots__ = ("number", "name", "type", "x", "y", "angle", "length", "unit", "hidden")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class LibSym:
    """One library symbol: pins and body extent per unit, field defaults."""

    def __init__(self, node):
        self.lib_id = str(node[1])
        self.power = first(node, "power") is not None
        pn = first(node, "pin_names")
        self.pin_names_hidden = bool(pn is not None and flag(pn, "hide"))
        self.pin_name_offset = fnum(atom(pn, "offset", "0.508")) if pn is not None else 0.508
        pnum = first(node, "pin_numbers")
        self.pin_numbers_hidden = bool(pnum is not None and flag(pnum, "hide"))
        self.fields = {}
        for p in every(node, "property"):
            at = value(p, "at") or ["0", "0", "0"]
            eff = first(p, "effects")
            just = value(eff, "justify", []) if eff is not None else []
            hide = flag(p, "hide") or (eff is not None and flag(eff, "hide"))
            self.fields[str(p[1])] = dict(value=str(p[2]), x=fnum(at[0]), y=fnum(at[1]),
                                          angle=fnum(at[2]) if len(at) > 2 else 0.0,
                                          justify=list(just), hide=hide)
        self.pins, self.boxes = {}, {}
        base = self.lib_id.split(":")[-1]
        for sub in every(node, "symbol"):
            m = re.fullmatch(re.escape(base) + r"_(\d+)_(\d+)", str(sub[1]))
            if not m:
                continue
            unit, style = int(m.group(1)), int(m.group(2))
            if style > 1:
                continue                                   # De Morgan alternates
            for p in every(sub, "pin"):
                at = value(p, "at")
                pin = Pin(number=str(atom(p, "number", "")), name=str(atom(p, "name", "")),
                          type=str(p[1]), x=fnum(at[0]), y=fnum(at[1]),
                          angle=fnum(at[2]) if len(at) > 2 else 0.0,
                          length=fnum(atom(p, "length", "2.54")), unit=unit,
                          hidden=flag(p, "hide"))
                self.pins.setdefault(unit, []).append(pin)
            for g in sub[2:]:
                if not isinstance(g, list):
                    continue
                pts = []
                if g[0] == "rectangle":
                    pts = [value(g, "start"), value(g, "end")]
                elif g[0] in ("polyline", "bezier"):
                    pts = [c[1:3] for c in first(g, "pts")[1:] if sexpr.is_node(c, "xy")]
                elif g[0] == "arc":
                    pts = [value(g, "start"), value(g, "mid"), value(g, "end")]
                elif g[0] == "circle":
                    cx, cy = map(fnum, value(g, "center"))
                    r = fnum(atom(g, "radius"))
                    pts = [(cx - r, cy - r), (cx + r, cy + r)]
                pts = [(fnum(a), fnum(b)) for a, b in pts if a is not None]
                if pts:
                    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                    b = self.boxes.get(unit)
                    nb = (min(xs), min(ys), max(xs), max(ys))
                    self.boxes[unit] = nb if b is None else (min(b[0], nb[0]), min(b[1], nb[1]),
                                                             max(b[2], nb[2]), max(b[3], nb[3]))

    def unit_pins(self, unit):
        return self.pins.get(0, []) + self.pins.get(unit, [])

    def unit_box(self, unit):
        """Body extent in library coordinates (y up), or None."""
        bs = [b for u, b in self.boxes.items() if u in (0, unit)]
        if not bs:
            return None
        return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))

    def units(self):
        return sorted(u for u in self.pins if u) or [1]


def lib_symbols(tree):
    ls = first(tree, "lib_symbols")
    return {str(n[1]): LibSym(n) for n in every(ls, "symbol")} if ls is not None else {}


# -- placed things ---------------------------------------------------------

class Inst:
    """A placed symbol unit."""

    def __init__(self, node, libs):
        self.node = node
        self.lib_id = str(atom(node, "lib_id"))
        at = value(node, "at")
        self.x, self.y = fnum(at[0]), fnum(at[1])
        self.rot = fnum(at[2]) if len(at) > 2 else 0.0
        self.mirror = atom(node, "mirror")
        self.unit = int(atom(node, "unit", "1"))
        self.uuid = str(atom(node, "uuid", ""))
        self.dnp = flag(node, "dnp")
        self.props = {}
        for p in every(node, "property"):
            at = value(p, "at") or ["0", "0", "0"]
            eff = first(p, "effects")
            self.props[str(p[1])] = dict(value=str(p[2]), x=fnum(at[0]), y=fnum(at[1]),
                                         angle=fnum(at[2]) if len(at) > 2 else 0.0,
                                         hide=flag(p, "hide") or (eff is not None and flag(eff, "hide")),
                                         justify=list(value(eff, "justify", []) if eff is not None else []))
        inst_ref = None
        ins = first(node, "instances")
        if ins is not None:
            for proj in every(ins, "project"):
                for path in every(proj, "path"):
                    inst_ref = inst_ref or atom(path, "reference")
        self.ref = str(self.props.get("Reference", {}).get("value") or inst_ref or "?")
        self.lib = libs.get(self.lib_id)
        self.power = bool(self.lib and self.lib.power) or self.ref.startswith("#")
        self.value = self.props.get("Value", {}).get("value", "")

    def pins(self):
        """[(Pin, (x, y) end on the sheet, (dx, dy) direction from the end into the body)]"""
        if not self.lib:
            return []
        out = []
        for p in self.lib.unit_pins(self.unit):
            ox, oy = xform(p.x, p.y, self.rot, self.mirror)
            d = xdir(p.angle, self.rot, self.mirror)
            out.append((p, (round(self.x + ox, 4), round(self.y + oy, 4)), d))
        return out

    def body(self):
        """Body box on the sheet (x0, y0, x1, y1), or None."""
        b = self.lib.unit_box(self.unit) if self.lib else None
        if not b:
            return None
        a = xform(b[0], b[1], self.rot, self.mirror)
        c = xform(b[2], b[3], self.rot, self.mirror)
        return (self.x + min(a[0], c[0]), self.y + min(a[1], c[1]),
                self.x + max(a[0], c[0]), self.y + max(a[1], c[1]))


class Label:
    __slots__ = ("kind", "text", "x", "y", "rot", "shape", "uuid")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class SheetSym:
    """A sheet symbol on its parent: the child's name, file, box and pins."""

    def __init__(self, node):
        at, size = value(node, "at"), value(node, "size")
        self.x, self.y, self.w, self.h = fnum(at[0]), fnum(at[1]), fnum(size[0]), fnum(size[1])
        self.uuid = str(atom(node, "uuid", ""))
        props = {str(p[1]): str(p[2]) for p in every(node, "property")}
        self.name = props.get("Sheetname") or props.get("Sheet name", "")
        self.file = props.get("Sheetfile") or props.get("Sheet file", "")
        self.pins = []
        for p in every(node, "pin"):
            pa = value(p, "at")
            self.pins.append(dict(name=str(p[1]), shape=str(p[2]), x=fnum(pa[0]), y=fnum(pa[1]),
                                  angle=fnum(pa[2]) if len(pa) > 2 else 0.0,
                                  uuid=str(atom(p, "uuid", ""))))


class Sheet:
    """One .kicad_sch file as placed in the hierarchy."""

    def __init__(self, path, name, hpath):
        self.path, self.name, self.hpath = Path(path), name, hpath
        tree = sexpr.load(path)
        self.tree = tree
        self.version = atom(tree, "version")
        pv = value(tree, "paper") or ["A4"]
        self.paper = str(pv[0])
        self.portrait = "portrait" in pv
        tb = first(tree, "title_block")
        self.title_block = {}
        if tb is not None:
            for c in tb[1:]:
                if isinstance(c, list):
                    key = c[0] if c[0] != "comment" else f"comment{c[1]}"
                    self.title_block[key] = str(c[-1])
        self.libs = lib_symbols(tree)
        self.symbols = [Inst(n, self.libs) for n in every(tree, "symbol")]
        self.wires = []
        for w in every(tree, "wire"):
            pts = [(fnum(c[1]), fnum(c[2])) for c in first(w, "pts")[1:] if sexpr.is_node(c, "xy")]
            for a, b in zip(pts, pts[1:]):
                self.wires.append((a, b, str(atom(w, "uuid", ""))))
        self.buses = []
        for w in every(tree, "bus"):
            pts = [(fnum(c[1]), fnum(c[2])) for c in first(w, "pts")[1:] if sexpr.is_node(c, "xy")]
            self.buses += list(zip(pts, pts[1:]))
        self.labels = []
        for kind in ("label", "global_label", "hierarchical_label"):
            for n in every(tree, kind):
                at = value(n, "at")
                self.labels.append(Label(kind=kind, text=str(n[1]), x=fnum(at[0]), y=fnum(at[1]),
                                         rot=fnum(at[2]) if len(at) > 2 else 0.0,
                                         shape=atom(n, "shape"), uuid=str(atom(n, "uuid", ""))))
        self.no_connects = [(fnum(value(n, "at")[0]), fnum(value(n, "at")[1]), str(atom(n, "uuid", "")))
                            for n in every(tree, "no_connect")]
        self.junctions = [(fnum(value(n, "at")[0]), fnum(value(n, "at")[1]), str(atom(n, "uuid", "")))
                          for n in every(tree, "junction")]
        self.sheets = [SheetSym(n) for n in every(tree, "sheet")]
        self.texts = []
        for n in every(tree, "text"):
            at = value(n, "at")
            self.texts.append((str(n[1]), fnum(at[0]), fnum(at[1]), str(atom(n, "uuid", ""))))
        self.text_boxes = []                           # (text, (x0, y0, x1, y1), uuid)
        for n in every(tree, "text_box"):
            at, size = value(n, "at"), value(n, "size")
            x, y, w, h = fnum(at[0]), fnum(at[1]), fnum(size[0]), fnum(size[1])
            self.text_boxes.append((str(n[1]), (min(x, x + w), min(y, y + h), max(x, x + w), max(y, y + h)),
                                    str(atom(n, "uuid", ""))))
        self.rects = []                                # ((x0, y0, x1, y1), uuid)
        for n in every(tree, "rectangle"):
            s, e = value(n, "start"), value(n, "end")
            x0, y0, x1, y1 = fnum(s[0]), fnum(s[1]), fnum(e[0]), fnum(e[1])
            self.rects.append(((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), str(atom(n, "uuid", ""))))

    def sections(self, band=8.0):
        """The titled frames: every text box (its text is the title) and every
        rectangle, titled by the text within `band` mm of its top-left corner.
        [(title, box, uuid, 'text box' | 'rectangle')]"""
        out = [(t, b, u, "text box") for t, b, u in self.text_boxes]
        for b, u in self.rects:
            title = ""
            for t, x, y, _ in self.texts:
                if b[0] <= x <= b[0] + band and b[1] <= y <= b[1] + band:
                    title = t
                    break
            out.append((title, b, u, "rectangle"))
        return out

    @property
    def parts(self):
        return [s for s in self.symbols if not s.power]

    @property
    def power_syms(self):
        return [s for s in self.symbols if s.power]

    def size(self):
        return paper_size(self.paper, self.portrait)


class Design:
    """The board's schematic: the root and every sheet below it, in order."""

    def __init__(self, board_dir):
        self.dir = Path(board_dir)
        roots = [p for p in sorted(self.dir.glob("*.kicad_sch")) if p.stem == self.dir.name]
        if not roots:
            raise SystemExit(f"sch: {self.dir.name}/{self.dir.name}.kicad_sch not found -- the root "
                             "schematic is named after the board folder")
        self.root = roots[0]
        self.sheets = []
        self._walk(self.root, "Root", "/")

    def _walk(self, path, name, hpath):
        sh = Sheet(path, name, hpath)
        self.sheets.append(sh)
        for s in sh.sheets:
            child = self.dir / s.file
            if child.exists() and not any(x.path == child for x in self.sheets):
                self._walk(child, s.name, hpath + s.name + "/")

    @property
    def hierarchical(self):
        return len(self.sheets) > 1

    def rails(self):
        """Power nets: the names the design's power symbols carry."""
        out = set()
        for sh in self.sheets:
            for s in sh.power_syms:
                if s.lib and s.lib.power:
                    pins = s.lib.unit_pins(s.unit)
                    out.add(s.value or (pins[0].name if pins else ""))
        out.discard("")
        return out


# -- KiCad's own output ----------------------------------------------------

def kicad_cli():
    exe = shutil.which("kicad-cli") or "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
    if not os.path.exists(exe):
        raise SystemExit("sch: kicad-cli not found (KiCad 8 or later)")
    return exe


def netlist(root):
    """KiCad's netlist: ({net: [(ref, pin)]}, {ref: {value, footprint, fields, sheet}})."""
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "n.xml")
        r = subprocess.run([kicad_cli(), "sch", "export", "netlist", "--format", "kicadxml",
                            "-o", out, str(root)], capture_output=True, text=True)
        if r.returncode or not os.path.exists(out):
            raise SystemExit(f"sch: kicad-cli netlist export failed: {r.stderr.strip()}")
        x = ET.parse(out).getroot()
    nets = {}
    for n in x.iter("net"):
        nets[n.get("name")] = [(nd.get("ref"), nd.get("pin")) for nd in n.iter("node")]
    comps = {}
    for c in x.iter("comp"):
        sp = c.find("sheetpath")
        fields = {f.get("name"): (f.text or "") for f in c.iter("field")}
        props = {p.get("name"): p.get("value") for p in c.iter("property")}
        comps[c.get("ref")] = dict(value=(c.findtext("value") or ""), footprint=(c.findtext("footprint") or ""),
                                   fields=fields, props=props,
                                   sheet=sp.get("names") if sp is not None else "/")
    return nets, comps


def svg_texts(root, outdir):
    """kicad-cli's SVG of every sheet -> {sheet name: [(text, (x0, y0, x1, y1))]}.
    Each stroked text is a <g class="stroked-text"> with its string in <desc>
    and its strokes as paths: the box is the strokes' own extent."""
    r = subprocess.run([kicad_cli(), "sch", "export", "svg", "-o", str(outdir), str(root)],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"sch: kicad-cli svg export failed: {r.stderr.strip()}")
    stem = Path(root).stem
    out = {}
    num = re.compile(r"-?\d+(?:\.\d+)?")
    for f in Path(outdir).glob("*.svg"):
        name = "Root" if f.stem == stem else f.stem[len(stem) + 1:] if f.stem.startswith(stem + "-") else f.stem
        s = f.read_text(encoding="utf-8")
        boxes = []
        for m in re.finditer(r'<g class="stroked-text"><desc>(.*?)</desc>(.*?)</g>', s, re.S):
            vals = [float(v) for v in num.findall(" ".join(re.findall(r'd="([^"]*)"', m.group(2))))]
            if len(vals) < 2:
                continue
            xs, ys = vals[0::2], vals[1::2]
            text = (m.group(1).replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
                    .replace("&apos;", "'").replace("&amp;", "&"))
            boxes.append((text, (min(xs), min(ys), max(xs), max(ys))))
        out[name] = boxes
    return out


def erc(root):
    """KiCad's ERC: (errors, warnings), or (None, None) if it could not run."""
    import json
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "erc.json")
        subprocess.run([kicad_cli(), "sch", "erc", "--format", "json", "-o", out, str(root)],
                       capture_output=True, text=True)
        if not os.path.exists(out):
            return None, None
        data = json.load(open(out))
    e = w = 0
    for sh in data.get("sheets", []):
        for v in sh.get("violations", []):
            if v.get("severity") == "error":
                e += 1
            elif v.get("severity") == "warning":
                w += 1
    return e, w


def text_w(s, size=1.27):
    return len(s) * size * CHAR_W
