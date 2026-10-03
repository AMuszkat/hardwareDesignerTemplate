#!/usr/bin/env python3
"""render.py -- the board, photographed.  ONLY when the user asks, with the
engine THE USER chose: there is no default engine.

    tools/.venv/bin/python tools/render/render.py --engine kicad            # KiCad's ray tracer
    tools/.venv/bin/python tools/render/render.py --engine blender          # Blender Cycles, photo-grade
    tools/.venv/bin/python tools/render/render.py --engine kicad --quick    # OpenGL, seconds: judge framing
    tools/.venv/bin/python tools/render/render.py --audit                   # where each body comes from
    tools/.venv/bin/python tools/render/render.py --engine kicad --views hero,top
    tools/.venv/bin/python tools/render/render.py --engine kicad --set-url <artifact URL>

The board is this session's (HW_BOARD, set by bin/board); --board boardN names
another one when the session may write it (tools/boardsel.py).

It READS the board and never writes it: the picture is taken of a scratch copy
in a temp directory (made by scratch.py, through tools/kpy, which is the only
part that needs pcbnew), and only <board>/renders/<engine>/ is written, plus
<board>/render.json on --set-url.  README.md is the whole story.
"""
import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError:
    sys.exit("render: Pillow is not importable here. Run this with the board venv "
             "(tools/.venv/bin/python tools/render/render.py ...); tools/setup.sh creates it.")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                                   # kicad_project/
sys.path.insert(0, str(HERE.parent))
import boardsel                                          # noqa: E402

ENGINES = {
    "kicad": "KiCad's own ray tracer (kicad-cli pcb render): KiCad's 3D-viewer look, several minutes",
    "blender": "Blender Cycles on KiCad's GLB export: studio light, real surfaces, slower "
               "(needs Blender: ./setup.sh install-blender)",
}

# The default set.  <board>/render.json "views" is merged over it: a view set
# to null is dropped, a new name adds one.  Keys: title, side | rotate (X,Y,Z
# degrees, as kicad-cli takes them), perspective, and optionally zoom / pan /
# pivot to take the framing away from the auto-fit.
VIEWS = {
    "hero":   {"title": "Front three-quarter", "rotate": "-55,0,-25", "perspective": True},
    "rear":   {"title": "Rear three-quarter", "rotate": "-55,0,155", "perspective": True},
    "top":    {"title": "Top", "side": "top"},
    "bottom": {"title": "Bottom", "side": "bottom"},
}
FINAL_PX = 8.3e6        # pixels per final view (a 4K frame's worth)
QUICK_PX = 1.6e6
WEB_W = 2400            # widest image the page carries
FILL = 0.87             # how much of the frame the board should span, seen flat
FILL_3D = 0.78          # ... and in a three-quarter view, which needs room for its floor shadow

BACKDROP = ((244, 246, 245), (205, 211, 208))   # centre, corners
SHADOW = (18, 24, 21)


def die(msg):
    sys.exit(f"render: {msg}")


def rel(p):
    """A path for the manifest and the page: relative to kicad_project/, or
    just its name -- never a machine path (the page may be published)."""
    p = Path(p).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return p.name


def board_title(folder):
    m = re.match(r"board(\d+)_(.+)", folder)
    if not m:
        return folder
    word = m.group(2).replace("_", " ")
    return f"Board {m.group(1)} {word.upper() if len(word) <= 3 else word.capitalize()}"


def kicad_render(pcb, out, w, h, view, zoom, quality):
    cmd = ["kicad-cli", "pcb", "render", "-o", str(out), "-w", str(int(w)), "-h", str(int(h)),
           "--quality", quality, "--background", "transparent", "--zoom", f"{zoom:.4f}"]
    for k in ("side", "rotate", "pan", "pivot"):
        if view.get(k):
            cmd += [f"--{k}", str(view[k])]
    if view.get("perspective"):
        cmd.append("--perspective")
    out.unlink(missing_ok=True)
    r = subprocess.run(cmd + [str(pcb)], capture_output=True, text=True)
    if r.returncode or not out.exists():
        die(f"kicad-cli failed ({' '.join(cmd[3:])}):\n{(r.stderr or r.stdout).strip()[-600:]}")


def is_flat(view):
    """A straight-on view: the floor shadow would only be a halo round the board."""
    return bool(view.get("side")) and not view.get("rotate") and not view.get("perspective")


def fit(pcb, view, px, tmp):
    """Frame size and zoom at which the board spans its share of the frame.
    Found with OpenGL probes of about a second each; the camera is the
    raytracer's.  The zoom acts about the frame centre and a three-quarter
    view sits off-centre, so the frame is then grown until the board itself
    has the pixels asked for -- rays that hit nothing cost almost nothing."""
    probe = tmp / "probe.png"
    fill = FILL if is_flat(view) else FILL_3D

    def look(w, h, zoom):
        kicad_render(pcb, probe, w, h, view, zoom, "basic")
        im = Image.open(probe)
        bb = im.getchannel("A").getbbox()
        if not bb:
            die("the probe render is empty -- nothing of the board is in the frame")
        return im.size, bb

    _, bb = look(640, 360, 1.0)
    aspect = min(max((bb[2] - bb[0]) / (bb[3] - bb[1]), 0.6), 2.6)
    w = round(math.sqrt(px * aspect) / 32) * 32
    h = round(w / aspect / 32) * 32
    zoom = float(view.get("zoom") or 1.0)
    if view.get("zoom"):
        return w, h, zoom
    for _ in range(6):
        (W, H), bb = look(max(w // 6, 192), max(h // 6, 192), zoom)
        if bb[0] <= 1 or bb[1] <= 1 or bb[2] >= W - 1 or bb[3] >= H - 1:
            zoom *= 0.8                          # clipped: the true extent is unknown
            continue
        span = max(max(W / 2 - bb[0], bb[2] - W / 2) / (W / 2), max(H / 2 - bb[1], bb[3] - H / 2) / (H / 2))
        if fill - 0.06 <= span <= fill + 0.05:
            break
        zoom *= fill / span
    grow = min(1.7, FILL / max((bb[2] - bb[0]) / W, (bb[3] - bb[1]) / H))
    if grow > 1.05:
        w, h = round(w * grow / 32) * 32, round(h * grow / 32) * 32
    return w, h, zoom


def blender_bin():
    """(blender, HIP library folder or None), as setup.sh --where finds them --
    one search, in one place."""
    r = subprocess.run(["sh", str(HERE / "setup.sh"), "--where"], capture_output=True, text=True)
    lines = r.stdout.split()
    if r.returncode or not lines:
        die("Blender is not installed. The user chose the Blender engine, so ask them to run "
            "`./setup.sh install-blender` (about 1.2 GB, no root; `--amd-gpu` adds AMD's GPU "
            "runtime), or set BLENDER_BIN to a Blender 4.2 or newer. Or render with --engine kicad.")
    return lines[0], (lines[1] if len(lines) > 1 else None)


def view_angles(view):
    """A view as elevation above the board plane and azimuth round it, from
    the same `side` / `rotate` keys the KiCad engine takes."""
    side = view.get("side")
    if view.get("rotate"):
        x, _, z = (float(v) for v in str(view["rotate"]).split(","))
        return 90.0 - abs(x), -z
    return {"top": (90.0, 0.0), "bottom": (-90.0, 0.0), "front": (0.0, 0.0), "back": (0.0, 180.0),
            "left": (0.0, -90.0), "right": (0.0, 90.0)}.get(side, (90.0, 0.0))


def blender_render(scratch, views, bodies, info, px, samples, tmp, device="auto"):
    """Export the scratch board as GLB and have Blender render every view.
    Returns ({view name: raw RGBA png}, the device Cycles used)."""
    exe, hip = blender_bin()
    glb = tmp / "board.glb"
    cmd = ["kicad-cli", "pcb", "export", "glb", "-f", "-o", str(glb), "--subst-models", "--no-dnp",
           "--include-tracks", "--include-pads", "--include-zones", "--include-silkscreen",
           "--include-soldermask", str(scratch)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode or not glb.exists():
        die(f"kicad-cli GLB export failed:\n{(r.stderr or r.stdout).strip()[-600:]}")
    raws, jobs = {}, []
    for name, view in views.items():
        el, az = view_angles(view)
        raws[name] = tmp / f"{name}.png"
        jobs.append({"name": name, "elevation": el, "azimuth": az, "flat": is_flat(view),
                     "fill": FILL if is_flat(view) else FILL_3D, "out": str(raws[name])})
    finish = re.search(r'\(copper_finish "([^"]*)"', Path(scratch).read_text(errors="ignore"))
    job = {"glb": str(glb), "views": jobs, "px": px, "samples": samples, "device": device,
           "finish": finish.group(1) if finish else "",
           "top_z_mm": 0.0, "bottom_z_mm": -info["thickness_mm"],
           "standins": [b["block"] for b in bodies if "block" in b]}
    (tmp / "job.json").write_text(json.dumps(job))
    env = dict(os.environ)
    if hip:                                     # AMD's GPU runtime, unpacked by setup.sh --amd-gpu
        env["LD_LIBRARY_PATH"] = f"{hip}:{env.get('LD_LIBRARY_PATH', '')}"
    r = subprocess.run([exe, "-b", "--factory-startup", "--python", str(HERE / "blender_scene.py"),
                        "--", str(tmp / "job.json")], capture_output=True, text=True, env=env)
    used = re.findall(r"^DEVICE (.+)$", r.stdout, re.M)
    missing = [n for n, p in raws.items() if not p.exists()]
    if r.returncode or missing:
        die(f"Blender failed (views not written: {missing}):\n{(r.stderr or r.stdout).strip()[-900:]}")
    return raws, (used[-1] if used else "CPU")


def compose(raw, mask=None):
    """Crop to the board and set it on a neutral backdrop.

    The raytracer draws its floor shadows into the alpha channel.  In a
    three-quarter view they are the picture's own shadow and are kept, faded
    out toward the frame edge so one that runs out of the frame does not end
    in a line.  In a flat view they are a grey halo of the outline, one per
    light; `mask` is the same frame in OpenGL, which has no shadows, and cuts
    them away -- a soft drawn shadow takes their place."""
    im = Image.open(raw).convert("RGBA")
    alpha = im.getchannel("A")
    if mask is not None:
        cut = Image.open(mask).getchannel("A")
        if cut.size != alpha.size:
            cut = cut.resize(alpha.size, Image.BICUBIC)
        alpha = ImageChops.darker(alpha, cut.filter(ImageFilter.MaxFilter(3)))
    else:
        W, H = im.size
        edge = round(0.02 * max(W, H))
        ramp = Image.new("L", (W, H), 0)
        ramp.paste(255, (edge, edge, W - edge, H - edge))
        alpha = ImageChops.multiply(alpha, ramp.filter(ImageFilter.GaussianBlur(edge / 2)))
    im.putalpha(alpha)
    im = im.crop(alpha.getbbox())
    w, h = im.size
    big = max(w, h)
    m = round(0.075 * big)
    W, H = w + 2 * m, h + 2 * m
    back = ImageOps.colorize(Image.radial_gradient("L").resize((W, H), Image.BICUBIC), BACKDROP[0], BACKDROP[1])
    solid = im.getchannel("A").point(lambda v: 255 if v >= 250 else 0)
    layers, drop = (((0.010, 0.30), (0.040, 0.18)), 0.005) if mask is not None else (((0.045, 0.15),), 0.012)
    for radius, strength in layers:
        sh = Image.new("L", (W, H), 0)
        sh.paste(solid, (m, m + round(drop * big)))
        sh = sh.filter(ImageFilter.GaussianBlur(radius * big)).point(lambda v, k=strength: int(v * k))
        back.paste(SHADOW, mask=sh)
    back.paste(im, (m, m), im)
    return back


def contact_sheet(shots, path):
    cell, pad, label = 760, 16, 34
    cols = 2 if len(shots) > 1 else 1
    thumbs = []
    for name, title, im in shots:
        t = im.copy()
        t.thumbnail((cell, cell))
        thumbs.append((f"{name} -- {title}", t))
    rows = [thumbs[i:i + cols] for i in range(0, len(thumbs), cols)]
    heights = [max(t.height for _, t in row) + label for row in rows]
    sheet = Image.new("RGB", (cols * cell + (cols + 1) * pad, sum(heights) + (len(rows) + 1) * pad), (255, 255, 255))
    draw, font = ImageDraw.Draw(sheet), ImageFont.load_default(size=20)
    y = pad
    for row, rh in zip(rows, heights):
        for i, (text, t) in enumerate(row):
            x = pad + i * (cell + pad)
            draw.text((x, y + 4), text, fill=(20, 20, 20), font=font)
            sheet.paste(t, (x, y + label))
        y += rh + pad
    sheet.save(path, "JPEG", quality=86)


# ---- the page ---------------------------------------------------------------

def build_page(man, out):
    e = html.escape
    views = man["views"]
    hero, rest = views[0], views[1:]

    def fig(v, cls):
        return (f'<figure class="{cls}"><button type="button" class="shot" data-full="{e(v["web"])}" '
                f'aria-label="Enlarge: {e(v["title"])}"><img src="{e(v["web"])}" width="{v["web_size"][0]}" '
                f'height="{v["web_size"][1]}" alt="{e(man["title"])}, {e(v["title"].lower())}" '
                f'loading="{"eager" if cls == "hero" else "lazy"}"></button>'
                f'<figcaption>{e(v["title"])}</figcaption></figure>')

    b = man["board"]
    sides = f'{b["parts_top"]} top' + (f', {b["parts_bottom"]} bottom' if b["parts_bottom"] else "")
    facts = [
        ("Outline", f'{b["width_mm"]:.1f} × {b["height_mm"]:.1f} mm'),
        ("Stack", f'{b["layers"]} layers, {b["thickness_mm"]:.2f} mm'),
        ("Parts", f'{b["parts"]} ({sides})'),
        ("Render", {"high": f'KiCad {e(man["kicad"])}, ray traced', "basic": f'KiCad {e(man["kicad"])}, OpenGL draft',
                    "blender": "Blender Cycles, from KiCad's export", "blender-draft": "Blender Cycles, draft"}[man["quality"]]),
        ("Board file", f'saved {e(man["pcb_saved"])}'),
    ]
    if b.get("revision"):
        facts.insert(0, ("Revision", e(b["revision"])))
    facts_html = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in facts)

    groups = {}
    for r in man["bodies"]:
        if r["source"] not in ("own", "none"):
            groups.setdefault((r["source"], r["footprint"], r["body"]), []).append(r["ref"])
    label = {"config": "Set in render.json", "alt": "Library model, near name", "stock": "Library model, same package",
             "standin": "Generated block", "dnp": "Not fitted"}
    order = ["stock", "alt", "config", "standin", "dnp"]
    rows = "".join(
        f'<tr><td class="refs">{e(", ".join(refs))}</td><td>{e(fpn)}</td><td>{label[src]}</td><td>{e(body)}</td></tr>'
        for (src, fpn, body), refs in sorted(groups.items(), key=lambda kv: (order.index(kv[0][0]), kv[1])))
    c = man["counts"]
    drawn = sum(c.get(k, 0) for k in ("own", "config", "alt", "stock", "standin"))
    if rows:
        bodies = (f'<p>{c.get("own", 0)} of the {drawn} bodies drawn are the footprint’s own 3D model. '
                  f'The rest are listed here, so a substituted body is not read as vendor CAD.</p>'
                  f'<div class="scroll"><table><thead><tr><th>Parts</th><th>Footprint</th><th>Body</th>'
                  f'<th>Detail</th></tr></thead><tbody>{rows}</tbody></table></div>')
    else:
        bodies = f'<p>All {drawn} bodies drawn are the footprints’ own 3D models.</p>'
    warn = "".join(f"<li>{e(w)}</li>" for w in man["warnings"])
    fill = {
        "TITLE": e(man["title"] + (" Blender" if man["quality"].startswith("blender") else "")),
        "SUB": e(man.get("subtitle") or f'{man["folder"]} · rendered {man["date"]}'),
        "HERO": fig(hero, "hero"),
        "GALLERY": "".join(fig(v, "view") for v in rest),
        "FACTS": facts_html,
        "BODIES": bodies,
        "WARNINGS": f'<ul class="warn">{warn}</ul>' if warn else "",
        "FOOT": e(f'{man["pcb"]} · sha256 {man["pcb_sha256"][:12]} · tools/render/render.py --engine {man["engine"]}'),
    }
    page = (HERE / "page.html").read_text()
    for k, v in fill.items():
        page = page.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{[A-Z]+\}\}", page)
    if left:
        die(f"page.html has placeholders render.py does not fill: {left}")
    (out / "index.html").write_text(page)


# ---- main -------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Render the board and build its gallery page -- with the engine the user chose.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="engines (no default -- the user picks one every time):\n"
               + "\n".join(f"  {k:8} {v}" for k, v in ENGINES.items()))
    ap.add_argument("--board", help="boardN, the folder name, or a path (default: this session's board)")
    ap.add_argument("--engine", choices=sorted(ENGINES), help="REQUIRED for a picture: kicad or blender")
    ap.add_argument("--views", help="comma-separated subset of the views (default: all)")
    ap.add_argument("--device", choices=["auto", "gpu", "cpu"], default="auto",
                    help="blender: auto = any usable GPU together with the CPU (default); gpu = GPU only; cpu")
    ap.add_argument("--quick", action="store_true", help="low resolution, seconds -- to judge framing; never published")
    ap.add_argument("--audit", action="store_true", help="report where each body comes from and stop")
    ap.add_argument("--set-url", metavar="URL", help="record this engine's published page in <board>/render.json and stop")
    ap.add_argument("--keep", action="store_true", help="keep the scratch directory (prints its path)")
    a = ap.parse_args()
    if not a.engine and not a.audit:
        ap.error("choose the engine -- there is no default, the user picks one:\n  "
                 + "\n  ".join(f"--engine {k:8} {v}" for k, v in ENGINES.items()))

    prj = boardsel.resolve(a.board)
    pcb = boardsel.one_pcb(prj)
    cfg_path = prj / "render.json"
    try:
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    except ValueError as e:
        die(f"{cfg_path.name} is not valid JSON: {e}")

    url_key = f"artifact_url_{a.engine}"
    url = cfg.get(url_key) or (cfg.get("artifact_url") if a.engine == "kicad" else None)
    if a.set_url:
        cfg[url_key] = a.set_url
        cfg.pop("artifact_url", None) if a.engine == "kicad" else None
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"recorded in {rel(cfg_path)} ({url_key}): {a.set_url}")
        return 0

    views = {k: dict(v) for k, v in VIEWS.items()}
    for k, v in (cfg.get("views") or {}).items():
        if v is None:
            views.pop(k, None)
        else:
            views[k] = {**views.get(k, {"title": k}), **v}
    if a.views:
        want = [v.strip() for v in a.views.split(",")]
        bad = [v for v in want if v not in views]
        if bad:
            die(f"no such view: {bad} (have {list(views)})")
        views = {k: views[k] for k in want}
    if not views:
        die("no views left (render.json drops them all)")
    if a.engine == "blender":
        blender_bin()                           # fail now, not after the scratch board

    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="render_"))
    try:
        r = subprocess.run([str(HERE.parent / "kpy"), str(HERE / "scratch.py"), str(pcb), str(tmp)],
                           capture_output=True, text=True)
        if r.returncode or not (tmp / "scratch.json").exists():
            die("preparing the scratch board failed:\n" + (r.stderr or r.stdout).strip()[-900:])
        sj = json.loads((tmp / "scratch.json").read_text())
        scratch, bodies, info = Path(sj["scratch"]), sj["bodies"], sj["info"]
        counts = {}
        for b in bodies:
            counts[b["source"]] = counts.get(b["source"], 0) + 1

        warnings, notes = [], []
        if list(prj.glob(f"~{pcb.name}.lck")) or list(prj.glob(f"~{pcb.stem}.kicad_pro.lck")):
            warnings.append("KiCad had this board open: the picture is of the board file as last saved.")
        if not sj["model_library"]:
            warnings.append("KiCad's 3D model library is not installed: every library part got a generated block.")
        if counts.get("standin"):
            refs = [b["ref"] for b in bodies if b["source"] == "standin"]
            notes.append(f"{len(refs)} part(s) drawn as a generated block, height ASSUMED: {', '.join(refs)}. "
                         "A real height goes in <board>/render.json (tools/render/README.md).")

        print(f"{rel(pcb)}: {len(bodies)} footprints -- " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
        for b in bodies:
            if b["source"] not in ("own", "none"):
                print(f"  {b['ref']:<7} {b['source']:<8} {b['body']}   [{b['footprint']}]")
        if a.audit:
            for n in notes:
                print(f"NOTE: {n}")
            return 0

        out = prj / "renders" / a.engine
        if out.exists():
            shutil.rmtree(out)                  # generated: a view dropped since last time must not linger
        (out / "web").mkdir(parents=True)
        quality, px = ("basic", QUICK_PX) if a.quick else ("high", FINAL_PX)
        shots, vrecs, device = [], [], None
        if a.engine == "blender":
            quality = "blender-draft" if a.quick else "blender"
            t1 = time.time()
            raws, device = blender_render(scratch, views, bodies, info, px, 48 if a.quick else 160, tmp, a.device)
            each = (time.time() - t1) / len(views)
            print(f"  Cycles on: {device}")
        for name, view in views.items():
            t1 = time.time()
            zoom = 1.0
            if a.engine == "blender":
                im = compose(raws[name])
                t1 -= each
            else:
                w, h, zoom = fit(scratch, view, px, tmp)
                raw = tmp / f"{name}.png"
                kicad_render(scratch, raw, w, h, view, zoom, quality)
                mask = None
                if is_flat(view) and quality != "basic":
                    mask = tmp / f"{name}_mask.png"
                    kicad_render(scratch, mask, w, h, view, zoom, "basic")
                im = compose(raw, mask)
            im.save(out / f"{name}.png")
            web = im.copy()
            web.thumbnail((WEB_W, WEB_W))
            web.save(out / "web" / f"{name}.webp", "WEBP", quality=90, method=6)
            shots.append((name, view["title"], web))
            vrecs.append({"name": name, "title": view["title"], "file": f"{name}.png", "size": list(im.size),
                          "web": f"web/{name}.webp", "web_size": list(web.size), "zoom": round(zoom, 4),
                          **{k: view[k] for k in ("side", "rotate", "perspective", "pan", "pivot") if k in view}})
            print(f"  {name:<8} {im.size[0]} x {im.size[1]}  zoom {zoom:.2f}  {time.time() - t1:.0f} s")
        contact_sheet(shots, out / "contact.jpg")

        kv = subprocess.run(["kicad-cli", "version"], capture_output=True, text=True).stdout.strip()
        man = {
            "title": cfg.get("title") or board_title(prj.name),
            "subtitle": cfg.get("subtitle"),
            "folder": prj.name,
            "engine": a.engine,
            "device": device,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "pcb": rel(pcb),
            "pcb_sha256": hashlib.sha256(pcb.read_bytes()).hexdigest(),
            "pcb_saved": datetime.fromtimestamp(pcb.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "kicad": kv,
            "quality": quality,
            "board": info,
            "views": vrecs,
            "counts": counts,
            "bodies": bodies,
            "warnings": warnings,
            "seconds": round(time.time() - t0),
        }
        (out / "manifest.json").write_text(json.dumps(man, indent=1) + "\n")
        build_page(man, out)

        call = {"file_path": rel(out / "index.html"), "root": rel(out), "files": [v["web"] for v in vrecs]}
        if url:
            call["url"] = url
        else:
            call["icon"] = "chip"
        call["description"] = (f"3D render ({a.engine}) of {man['title']}: {info['width_mm']:.0f} x "
                               f"{info['height_mm']:.0f} mm, {info['layers']} layers, {info['parts']} parts, "
                               f"board file saved {man['pcb_saved']}.")
        print(f"\nwrote {rel(out)}/ in {man['seconds']} s: " + ", ".join(v["file"] for v in vrecs)
              + ", web/, contact.jpg, index.html, manifest.json")
        for w in warnings:
            print(f"WARNING: {w}")
        for n in notes:
            print(f"NOTE: {n}")
        if a.quick:
            print("\nDRAFT (--quick): not for publishing. Run again without --quick.")
        else:
            print("\nPUBLISH -- the Artifact tool, with exactly this:\n" + json.dumps(call, indent=1))
            if not url:
                print(f"then record the link:  tools/.venv/bin/python tools/render/render.py "
                      f"--engine {a.engine} --set-url <the artifact URL>")
        return 0
    finally:
        if a.keep:
            print(f"scratch kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
