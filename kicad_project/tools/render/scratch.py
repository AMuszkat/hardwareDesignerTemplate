#!/usr/bin/env python3
"""scratch.py -- the pcbnew half of render.py.  Run by render.py, never by hand:

    tools/kpy tools/render/scratch.py <board.kicad_pcb> <tmp dir>

It copies the board into <tmp dir>/board/ (the original is never opened, so no
KiCad lock file appears in the board folder), gives every placed footprint of
the COPY a 3D body, saves the copy, and writes <tmp dir>/scratch.json: the
scratch board's path, one record per footprint saying where its body came
from, and the board facts the page shows.  Everything else -- the pictures,
the page -- is render.py, in the board venv, so KiCad's own Python needs
nothing beyond pcbnew.

Body sources, in the order they are tried (README.md has the table):
own, config (<board>/render.json), alt, stock, standin, dnp, none.
"""
import hashlib
import json
import math
import os
import re
import shutil
import sys
from pathlib import Path

NO_BODY = re.compile(r"^(MountingHole|SolderJumper|TestPoint|Fiducial|NetTie|Logo|Symbol)", re.I)
MODEL_EXT = (".step", ".stp", ".wrl", ".glb")
STYLES = {  # diffuse, specular, shininess
    "epoxy":   ((0.085, 0.085, 0.095), (0.20, 0.20, 0.20), 0.25),
    "metal":   ((0.74, 0.74, 0.71), (0.90, 0.90, 0.88), 0.80),
    "ferrite": ((0.17, 0.17, 0.18), (0.10, 0.10, 0.10), 0.10),
}




def stock_dir():
    for k, v in os.environ.items():
        if re.fullmatch(r"KICAD\d+_3DMODEL_DIR", k) and Path(v).is_dir():
            return Path(v)
    for cfg in sorted(Path.home().glob(".config/kicad/*/kicad_common.json"), reverse=True):
        try:
            for k, v in (json.loads(cfg.read_text()).get("environment", {}).get("vars") or {}).items():
                if re.fullmatch(r"KICAD\d+_3DMODEL_DIR", k) and Path(v).is_dir():
                    return Path(v)
        except (OSError, ValueError):
            pass
    for p in ("/usr/share/kicad/3dmodels", "/usr/local/share/kicad/3dmodels"):
        if Path(p).is_dir():
            return Path(p)
    return None


def resolve(path, prj, sdir):
    """A model path as the footprint spells it -> a Path (which may not exist)."""
    s = path.replace("${KIPRJMOD}", str(prj))
    if sdir:
        s = re.sub(r"\$\{(KICAD\d+_3DMODEL_DIR|KISYS3DMOD)\}", lambda _m: str(sdir), s)
    s = os.path.expandvars(s)
    p = Path(s)
    return p if p.is_absolute() else prj / p


DIM = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:x\d+(?:\.\d+)?)?mm")
PITCH = re.compile(r"_P(\d+(?:\.\d+)?)mm")
PINS = re.compile(r"-(\d+)(?:-(\d+)EP)?(?=_|$)|(?<!\d)(\d+)Pin")


def signature(name):
    d, p, n = DIM.search(name), PITCH.search(name), PINS.search(name)
    dims = (float(d.group(1)), float(d.group(2))) if d else None
    pins = None
    if n:
        pins = (int(n.group(1)), int(n.group(2) or 0)) if n.group(1) else (int(n.group(3)), 0)
    return dims, (float(p.group(1)) if p else None), pins


class Stock:
    """KiCad's own model library, indexed by name once and only if needed."""

    def __init__(self, sdir):
        self.dir, self._idx = sdir, None

    def index(self):
        if self._idx is None:
            self._idx = []
            for lib in sorted(self.dir.glob("*.3dshapes")) if self.dir else []:
                keys = set()
                for t in lib.stem.split("_"):
                    if t.lower() not in ("package", "smd", "tht"):
                        keys.add(t.upper())
                        if len(t) > 5:
                            keys.add(t[:3].upper())
                seen = {}
                for f in sorted(lib.iterdir()):
                    if f.suffix.lower() in MODEL_EXT and (f.stem not in seen or f.suffix.lower() == ".step"):
                        seen[f.stem] = f
                for stem, f in seen.items():
                    lead = re.match(r"[A-Za-z]+", stem)
                    k = set(keys)
                    if lead:
                        k.add(lead.group(0).upper())
                        if len(lead.group(0)) > 5:
                            k.add(lead.group(0)[:3].upper())
                    self._idx.append((f, stem, k, signature(stem)))
        return self._idx

    def beside(self, wanted):
        """The named file is missing: the same stem with another extension, or
        a stem that contains / is contained in the wanted one."""
        if not wanted.parent.is_dir():
            return None
        best = None
        for f in wanted.parent.iterdir():
            if f.suffix.lower() not in MODEL_EXT:
                continue
            if f.stem == wanted.stem:
                return f
            if len(f.stem) >= 8 and (f.stem in wanted.stem or wanted.stem in f.stem):
                if best is None or len(f.stem) > len(best.stem):
                    best = f
        return best

    def match(self, name, npads):
        dims, pitch, _ = signature(name)
        if not dims:
            return None
        up, toks = name.upper(), set(re.split(r"[_\-]", name.upper()))
        best = None
        for f, stem, keys, (sd, sp, spins) in self.index():
            if not sd or spins is None:
                continue
            if not (math.isclose(sd[0], dims[0], abs_tol=0.011) and math.isclose(sd[1], dims[1], abs_tol=0.011)):
                continue
            if pitch and sp and not math.isclose(pitch, sp, abs_tol=0.001):
                continue
            if not (spins[0] <= npads <= spins[0] + max(spins[1], 1)):
                continue
            if not any(len(k) >= 2 and k in up for k in keys):
                continue
            score = (2 if pitch and sp else 0) + len(toks & set(re.split(r"[_\-]", stem.upper())))
            cand = (score, -len(stem), stem, f)
            if best is None or cand > best:
                best = cand
        return best[3] if best else None


def local_geometry(pcbnew, fp):
    """F.Fab outline and pad 1, in the footprint's own frame (mm, y down).
    The footprint is turned to 0 degrees in place and put back -- a copy made
    through SWIG corrupts the heap."""
    pos = fp.GetPosition()
    px, py = pos.x, pos.y
    flipped = fp.IsFlipped()
    centre = pcbnew.VECTOR2I(px, py)
    if flipped:
        fp.Flip(centre, pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    rot = fp.GetOrientationDegrees()
    fp.SetOrientationDegrees(0)
    xs, ys = [], []
    for g in fp.GraphicalItems():
        if g.GetLayer() == pcbnew.F_Fab and g.GetClass() == "PCB_SHAPE":
            r, w = g.GetBoundingBox(), g.GetWidth() // 2
            xs += [r.GetLeft() + w - px, r.GetRight() - w - px]
            ys += [r.GetTop() + w - py, r.GetBottom() - w - py]
    if not xs:                                  # no fab outline: the pads' extent
        for p in fp.Pads():
            r = p.GetBoundingBox()
            xs += [r.GetLeft() - px, r.GetRight() - px]
            ys += [r.GetTop() - py, r.GetBottom() - py]
    pin1 = None
    for p in fp.Pads():
        if p.GetNumber() in ("1", "A1"):
            q = p.GetPosition()
            pin1 = (pcbnew.ToMM(q.x - px), pcbnew.ToMM(q.y - py))
            break
    here = [(pcbnew.ToMM(p.GetPosition().x - px), pcbnew.ToMM(p.GetPosition().y - py)) for p in fp.Pads()]
    fp.SetOrientationDegrees(rot)
    if flipped:
        fp.Flip(centre, pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    there = [(pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)) for p in fp.Pads()]
    if not xs:
        return None, None, None
    # Footprint frame -> board frame, solved from two pads rather than derived
    # from KiCad's flip and angle conventions: mirror (if on the back), rotate,
    # translate.  Used by the Blender engine, which places stand-ins itself.
    mx = -1.0 if flipped else 1.0
    a, b = 0, max(range(len(here)), key=lambda i: math.dist(here[i], here[0])) if here else 0
    phi = 0.0
    if here and math.dist(here[a], here[b]) > 1e-6:
        phi = (math.atan2(there[b][1] - there[a][1], there[b][0] - there[a][0])
               - math.atan2(here[b][1] - here[a][1], mx * (here[b][0] - here[a][0])))
    ox, oy = (here[a], there[a]) if here else ((0.0, 0.0), (pcbnew.ToMM(px), pcbnew.ToMM(py)))

    def to_board(x, y):
        u, v = mx * (x - ox[0]), y - ox[1]
        return (oy[0] + u * math.cos(phi) - v * math.sin(phi), oy[1] + u * math.sin(phi) + v * math.cos(phi))

    return tuple(pcbnew.ToMM(v) for v in (min(xs), min(ys), max(xs), max(ys))), pin1, to_board


def standin_height(ref, w, h):
    if re.match(r"L\d", ref):                   # inductors are tall
        return min(max(0.5 * min(w, h), 0.8), 6.0)
    return min(max(0.32 * min(w, h), 0.35), 1.5)


def standin_style(ref):
    if re.match(r"[YX]\d", ref):
        return "metal"
    if re.match(r"L\d", ref):
        return "ferrite"
    return "epoxy"


def write_standin(path, rect, height, pin1, npads, style):
    """A chamfered block with a pin-1 dot, as VRML.  KiCad reads VRML in units
    of 0.1 inch, x right, y up -- the footprint frame has y down."""
    x0, y0, x1, y1 = rect
    X0, X1, Y0, Y1 = x0, x1, -y1, -y0
    w, d = X1 - X0, Y1 - Y0
    c = min(0.12, 0.12 * min(w, d), 0.3 * height)
    z0, zm, zt = 0.03, height - c, height
    ring = lambda z, i: [(X0 + i, Y0 + i, z), (X1 - i, Y0 + i, z), (X1 - i, Y1 - i, z), (X0 + i, Y1 - i, z)]
    pts = ring(z0, 0) + ring(zm, 0) + ring(zt, c)
    faces = []
    for i in range(4):
        j = (i + 1) % 4
        faces += [(i, j, 4 + j, 4 + i), (4 + i, 4 + j, 8 + j, 8 + i)]
    faces += [(8, 9, 10, 11), (3, 2, 1, 0)]
    diff, spec, shin = STYLES.get(style, STYLES["epoxy"])

    def shape(points, polys, diff, spec, shin):
        coords = ", ".join(f"{x / 2.54:.5f} {y / 2.54:.5f} {z / 2.54:.5f}" for x, y, z in points)
        index = ", ".join(", ".join(map(str, f)) + ", -1" for f in polys)
        return ("Shape {\n appearance Appearance { material Material {"
                f" diffuseColor {diff[0]} {diff[1]} {diff[2]} specularColor {spec[0]} {spec[1]} {spec[2]}"
                f" ambientIntensity 0.25 shininess {shin} transparency 0 }} }}\n"
                " geometry IndexedFaceSet { solid FALSE creaseAngle 0.3\n"
                f"  coord Coordinate {{ point [ {coords} ] }}\n  coordIndex [ {index} ] }}\n}}\n")

    out = "#VRML V2.0 utf8\n# generated stand-in body -- tools/render/render.py\n"
    out += shape(pts, faces, diff, spec, shin)
    if pin1 and npads >= 3:
        r = min(max(0.07 * min(w, d), 0.06), 0.45)
        inset = max(2.2 * r, c + 1.5 * r)
        cx = X0 + inset if pin1[0] < (x0 + x1) / 2 else X1 - inset
        cy = Y1 - inset if pin1[1] < (y0 + y1) / 2 else Y0 + inset
        n = 20
        disc = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n), zt + 0.004)
                for k in range(n)]
        mark = (0.62, 0.62, 0.60) if style != "metal" else (0.25, 0.25, 0.25)
        out += shape(disc, [tuple(range(n))], mark, (0.1, 0.1, 0.1), 0.1)
    path.write_text(out)


def resolve_bodies(pcbnew, board, prj, cfg, tmp):
    """Give every footprint of the (in-memory) board the body it will be drawn
    with, and return one record per footprint."""
    sdir = stock_dir()
    stock = Stock(sdir)
    over = cfg.get("bodies", {})
    made = tmp / "standins"
    made.mkdir(exist_ok=True)
    recs = []
    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
        ref = fp.GetReference()
        name = str(fp.GetFPID().GetUniStringLibItemName())
        rec = {"ref": ref, "footprint": name, "side": "bottom" if fp.IsFlipped() else "top"}
        recs.append(rec)
        models = fp.Models()
        named = [str(m.m_Filename) for m in models if m.m_Show]
        o = over.get(ref, over.get(name))

        def use(path, source, note, offset=None, rotate=None, scale=None):
            models.clear()
            m = pcbnew.FP_3DMODEL()
            m.m_Filename, m.m_Show = str(path), True
            for attr, v in (("m_Offset", offset), ("m_Rotation", rotate), ("m_Scale", scale)):
                if v:
                    setattr(m, attr, pcbnew.VECTOR3D(*map(float, v)))
            fp.Add3DModel(m)
            rec.update(source=source, body=note)

        def standin(height=None, style=None, source="standin"):
            rect, pin1, to_board = local_geometry(pcbnew, fp)
            if rect is None:
                models.clear()
                rec.update(source="none", body="no outline to build a body from")
                return
            w, d = rect[2] - rect[0], rect[3] - rect[1]
            height = height or standin_height(ref, w, d)
            style = style or standin_style(ref)
            npads = len({p.GetNumber() for p in fp.Pads() if p.GetNumber()})
            key = hashlib.sha1(repr((rect, height, pin1, npads, style)).encode()).hexdigest()[:10]
            path = made / f"{key}.wrl"
            if not path.exists():
                write_standin(path, rect, height, pin1, npads, style)
            use(path, source, f"generated block {w:.2f} x {d:.2f} x {height:.2f} mm")
            x0, y0, x1, y1 = rect
            block = {"ref": ref, "side": rec["side"], "height": height, "style": style,
                     "corners": [to_board(*c) for c in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]}
            if pin1 and npads >= 3:
                r = min(max(0.07 * min(w, d), 0.06), 0.45)
                inset = max(2.2 * r, 0.12 + 1.5 * r)
                block["dot"] = {"r": r, "at": to_board(x0 + inset if pin1[0] < (x0 + x1) / 2 else x1 - inset,
                                                       y0 + inset if pin1[1] < (y0 + y1) / 2 else y1 - inset)}
            rec["block"] = block

        if fp.IsDNP():
            models.clear()
            rec.update(source="dnp", body="not fitted")
            continue
        if o is not None:
            if o.get("none"):
                models.clear()
                rec.update(source="none", body="render.json: no body")
            elif o.get("model"):
                p = resolve(o["model"], prj, sdir)
                if not p.exists():
                    die(f"render.json: body for {ref} names {o['model']}, which does not exist")
                use(p, "config", f"render.json: {p.name}", o.get("offset"), o.get("rotate"), o.get("scale"))
            else:
                standin(o.get("height"), o.get("style"), "config")
            continue
        if named:
            paths = [resolve(n, prj, sdir) for n in named]
            if all(p.exists() for p in paths):
                # The scratch copy lives elsewhere, so ${KIPRJMOD} must become
                # a real path.  The entries are rebuilt, not edited in place.
                kept = []
                for m in models:
                    if m.m_Show:
                        n = pcbnew.FP_3DMODEL()
                        n.m_Filename, n.m_Show = str(resolve(str(m.m_Filename), prj, sdir)), True
                        n.m_Offset, n.m_Rotation, n.m_Scale = m.m_Offset, m.m_Rotation, m.m_Scale
                        n.m_Opacity = m.m_Opacity
                        kept.append(n)
                models.clear()
                for n in kept:
                    fp.Add3DModel(n)
                rec.update(source="own", body=", ".join(p.name for p in paths))
                continue
            alt = stock.beside(next(p for p in paths if not p.exists())) if len(paths) == 1 else None
            if alt:
                use(alt, "alt", f"{alt.name} (the footprint names {paths[0].name}, which is not installed)")
                continue
            rec["missing"] = ", ".join(p.name for p in paths if not p.exists())
        attrs = fp.GetAttributes()
        placed = attrs & (pcbnew.FP_SMD | pcbnew.FP_THROUGH_HOLE) and not attrs & pcbnew.FP_EXCLUDE_FROM_POS_FILES
        if not placed or NO_BODY.match(name):
            models.clear()
            rec.update(source="none", body="nothing to draw")
            continue
        npads = len({p.GetNumber() for p in fp.Pads() if p.GetNumber()})
        hit = stock.match(name, npads)
        if hit:
            use(hit, "stock", f"KiCad library {hit.parent.stem}/{hit.name}")
        else:
            standin()
    return recs, sdir




def die(msg):
    sys.exit(f"render: {msg}")


def load_pcbnew():
    """pcbnew prints a page of wx assertion noise to stderr on import."""
    keep, null = os.dup(2), os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)
    try:
        import pcbnew
    except ImportError:
        die(f"cannot import pcbnew under {sys.executable} (render.py runs this through tools/kpy)")
    finally:
        os.dup2(keep, 2)
        os.close(null)
        os.close(keep)
    return pcbnew


def main(argv):
    if len(argv) != 2:
        die("usage: tools/kpy tools/render/scratch.py <board.kicad_pcb> <tmp dir>")
    pcb, tmp = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    prj = pcb.parent
    cfg_path = prj / "render.json"
    try:
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    except ValueError as e:
        die(f"{cfg_path.name} is not valid JSON: {e}")
    pcbnew = load_pcbnew()
    scratch = tmp / "board" / pcb.name
    scratch.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(pcb, scratch)
    pro = pcb.with_suffix(".kicad_pro")
    if pro.exists():                            # text variables live in the project file
        shutil.copy(pro, scratch.with_suffix(".kicad_pro"))
    board = pcbnew.LoadBoard(str(scratch))
    bodies, sdir = resolve_bodies(pcbnew, board, prj, cfg, tmp)

    xs, ys = [], []
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts:
            r, w = d.GetBoundingBox(), d.GetWidth() // 2
            xs += [r.GetLeft() + w, r.GetRight() - w]
            ys += [r.GetTop() + w, r.GetBottom() - w]
    if not xs:
        die(f"{pcb.name} has no board outline on Edge.Cuts: nothing to photograph yet")
    drawn = [r for r in bodies if r["source"] not in ("none", "dnp")]
    info = {
        "width_mm": pcbnew.ToMM(max(xs) - min(xs)),
        "height_mm": pcbnew.ToMM(max(ys) - min(ys)),
        "layers": board.GetCopperLayerCount(),
        "thickness_mm": pcbnew.ToMM(board.GetDesignSettings().GetBoardThickness()),
        "parts": len(drawn),
        "parts_top": sum(r["side"] == "top" for r in drawn),
        "parts_bottom": sum(r["side"] == "bottom" for r in drawn),
        "revision": str(board.GetTitleBlock().GetRevision()),
    }
    if not pcbnew.SaveBoard(str(scratch), board):
        die("could not save the scratch copy")
    (tmp / "scratch.json").write_text(json.dumps({
        "scratch": str(scratch), "bodies": bodies, "info": info,
        "model_library": sdir is not None}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
