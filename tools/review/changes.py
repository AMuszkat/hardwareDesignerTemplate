"""The scope of a review and what changed since the board's previous snapshot.

Used by snapshot.py; plain python3, no pcbnew (the board file is read as text).

SCOPE (snapshot.py --scope) -- the board area a PCB review is about, in board mm:

    all | fab          everything (the default; a fab release reads every tile)
    R3                 a routing group: the copper its routing/R3.applied.json
                       manifest names (by UUID, read from the .kicad_pcb)
    <name>             a placement.json group: its parts
    refs:U1,C3         those parts
    box:X0,Y0,X1,Y1    that rectangle

plus a margin (3 mm). The PCB views are tiled only inside it; the sheet tiles
are skipped for a scoped PCB review.

CHANGES -- every page and view is also rasterised at 72 dpi (one pixel per
point) into compare/<name>.pgm, a fixed geometry: the whole page, whatever is
on it. Diffed cell by cell (CELL points square) against the same file of the
previous snapshot of the same board and kind, the changed cells give changed
REGIONS (mm boxes) and the CHANGED TILES (every tile a region touches).
changed.md lists both; a changed region no tile shows (outside the scope) is
listed as such, since a change the request did not declare is a finding.
kicad-cli renders the same file to the same pixels, so an unchanged board
lists nothing.
"""
import json
import re
import subprocess
from pathlib import Path

MARGIN = 3.0
CELL = 8                       # points (~2.8 mm) per comparison cell
PT = 72 / 25.4


# -- the board file, read as text --------------------------------------------

def blocks(text):
    """The top-level children of the root list, as strings."""
    out, depth, start, i, n = [], 0, None, 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
            if depth == 2:
                start = i
        elif ch == ")":
            if depth == 2 and start is not None:
                out.append(text[start:i + 1])
                start = None
            depth -= 1
        i += 1
    return out


XY = re.compile(r"\((?:start|end|mid|at|xy|center)\s+(-?[\d.]+)\s+(-?[\d.]+)")
UUID = re.compile(r'\(uuid\s+"?([0-9a-fA-F-]+)"?\)')
REF = re.compile(r'\(property\s+"Reference"\s+"([^"]+)"')


def footprint_box(blk):
    """A footprint's extent: its origin, grown by its farthest pad (rotation-safe) plus 0.5 mm."""
    m = re.search(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)", blk)
    if not m:
        return None
    x, y = float(m[1]), float(m[2])
    r = 0.0
    for p in re.finditer(r"\(pad\s.*?\(at\s+(-?[\d.]+)\s+(-?[\d.]+)", blk, re.S):
        r = max(r, (float(p[1]) ** 2 + float(p[2]) ** 2) ** 0.5)
    r += 0.5
    return (x - r, y - r, x + r, y + r)


def union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def scope_box(board, scope, margin=MARGIN):
    """(label, box in board mm or None for everything, note)."""
    if not scope or scope.lower() in ("all", "fab", "fab release"):
        return (scope or "all"), None, "every tile"
    pcbs = sorted(Path(board).glob("*.kicad_pcb"))
    text = pcbs[0].read_text() if pcbs else ""
    bl = blocks(text)
    if scope.startswith("box:"):
        v = [float(t) for t in scope[4:].split(",")]
        return scope, tuple(v), "the given box"
    refs = None
    if scope.startswith("refs:"):
        refs = scope[5:].split(",")
    else:
        man = Path(board) / "routing" / f"{scope}.applied.json"
        if man.exists():
            m = json.loads(man.read_text())
            want = set()
            for k in ("tracks", "vias", "zones", "polys"):
                want |= set(m.get(k, []))
            pts = []
            for b in bl:
                u = UUID.search(b)
                if u and u[1] in want:
                    pts += [(float(x), float(y)) for x, y in XY.findall(b)]
            if not pts:
                return scope, None, f"{man.name} names no copper on the board: every tile"
            box = (min(p[0] for p in pts) - margin, min(p[1] for p in pts) - margin,
                   max(p[0] for p in pts) + margin, max(p[1] for p in pts) + margin)
            return scope, box, f"routing group {scope} ({man.name}) + {margin:g} mm"
        pj = Path(board) / "placement.json"
        groups = json.loads(pj.read_text()).get("groups", {}) if pj.exists() else {}
        if scope in groups:
            refs = groups[scope]
    if refs is None:
        raise SystemExit(f"--scope {scope!r}: not all/fab, a routing group with a manifest, a placement.json "
                         "group, refs:A,B or box:X0,Y0,X1,Y1")
    have = {}
    for b in bl:
        if b.startswith("(footprint"):
            r = REF.search(b)
            if r:
                have[r[1]] = footprint_box(b)
    miss = [r for r in refs if r not in have]
    if miss:
        raise SystemExit(f"--scope {scope}: not on the board: {', '.join(miss)}")
    u = union(have[r] for r in refs)
    return scope, (u[0] - margin, u[1] - margin, u[2] + margin, u[3] + margin), \
        f"{len(refs)} part(s) + {margin:g} mm"


def to_points(box):
    return tuple(v * PT for v in box) if box else None


def clip(a, b):
    """The intersection of two point boxes, or None."""
    if b is None:
        return a
    c = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return c if c[0] < c[2] and c[1] < c[3] else None


# -- rasters and their diff --------------------------------------------------

def raster(pdf, page, dest):
    """One page at 72 dpi, grayscale, as a binary PGM (pixel = point)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftoppm", "-gray", "-r", "72", "-f", str(page), "-l", str(page), "-singlefile",
                    str(pdf), str(dest.with_suffix(""))], capture_output=True)
    return dest.with_suffix(".pgm") if dest.with_suffix(".pgm").exists() else None


def read_pgm(p):
    data = Path(p).read_bytes()
    m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", data)
    return (int(m[1]), int(m[2]), data[m.end():]) if m else None


def changed_cells(a, b):
    """Cells (i, j) whose pixels differ; None when the page size differs."""
    if a[0] != b[0] or a[1] != b[1]:
        return None
    w, h, pa, pb = a[0], a[1], a[2], b[2]
    if pa == pb:
        return set()
    cells = set()
    for y in range(h):
        ra, rb = pa[y * w:(y + 1) * w], pb[y * w:(y + 1) * w]
        if ra == rb:
            continue
        for x in range(w):
            if ra[x] != rb[x]:
                cells.add((x // CELL, y // CELL))
    return cells


def regions(cells):
    """Connected changed cells (8-neighbour) as point boxes."""
    left, out = set(cells), []
    while left:
        stack = [left.pop()]
        comp = []
        while stack:
            c = stack.pop()
            comp.append(c)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    n = (c[0] + di, c[1] + dj)
                    if n in left:
                        left.remove(n)
                        stack.append(n)
        xs, ys = [c[0] for c in comp], [c[1] for c in comp]
        out.append((min(xs) * CELL, min(ys) * CELL, (max(xs) + 1) * CELL, (max(ys) + 1) * CELL))
    return sorted(out, key=lambda r: (r[1], r[0]))


def previous(out, kind):
    """The newest earlier snapshot of the same board and kind (a sibling folder of `out`)."""
    best = None
    for d in Path(out).parent.glob(f"*-{kind}"):
        if d.resolve() == Path(out).resolve() or not (d / "manifest.json").exists():
            continue
        try:
            taken = json.loads((d / "manifest.json").read_text()).get("taken", "")
        except ValueError:
            continue
        if best is None or taken > best[0]:
            best = (taken, d)
    return best


def report(out, kind, sources, tiles, scope_note):
    """Write changed.md. sources: [(name, pdf, page)]; tiles: {name: [(tile, box in points)]}.
    Returns the SUMMARY line."""
    cmp_dir = Path(out) / "compare"
    for name, pdf, page in sources:
        raster(pdf, page, cmp_dir / f"{name}.pgm")
    prev = previous(out, kind)
    mm = 25.4 / 72
    fmt = lambda r: f"x {r[0] * mm:.1f}–{r[2] * mm:.1f}, y {r[1] * mm:.1f}–{r[3] * mm:.1f} mm"
    n_tiles = sum(len(v) for v in tiles.values())
    L = ["# What changed", "", f"Scope: {scope_note}.", ""]
    if not prev:
        L += ["No earlier snapshot of this board and kind: everything is new — read every tile in scope."]
        (Path(out) / "changed.md").write_text("\n".join(L) + "\n")
        return f"no earlier {kind} snapshot: all {n_tiles} tiles new (changed.md)"
    taken, pdir = prev
    L += [f"Against `{pdir.name}` (taken {taken}). Positions are mm from the page's top-left corner "
          f"(on a PCB view: board mm). A region is the box round connected {CELL * mm:.1f} mm cells "
          "whose pixels differ.", ""]
    total_changed, outside = 0, 0
    for name, _pdf, _page in sources:
        cur = cmp_dir / f"{name}.pgm"
        old = pdir / "compare" / f"{name}.pgm"
        mine = tiles.get(name, [])
        if not cur.exists():
            continue
        if not old.exists():
            L += [f"## {name}", "", f"New since `{pdir.name}`: read all {len(mine)} tile(s).", ""]
            total_changed += len(mine)
            continue
        cells = changed_cells(read_pgm(cur), read_pgm(old))
        if cells is None:
            L += [f"## {name}", "", f"The page size changed: read all {len(mine)} tile(s).", ""]
            total_changed += len(mine)
            continue
        if not cells:
            L += [f"## {name}", "", f"Unchanged ({len(mine)} tile(s)).", ""]
            continue
        regs = regions(cells)
        hit = [t for t, b in mine if any(clip(b, r) for r in regs)]
        unseen = [r for r in regs if not any(clip(b, r) for _t, b in mine)]
        total_changed += len(hit)
        outside += len(unseen)
        L += [f"## {name}", "", f"{len(regs)} changed region(s); {len(hit)} of {len(mine)} tile(s) show them.", ""]
        L += [f"- tiles/{t}.png" for t in hit]
        L += [f"- {fmt(r)}" + ("" if any(clip(b, r) for _t, b in mine) else " — **outside every tile in scope**")
              for r in regs]
        L.append("")
    (Path(out) / "changed.md").write_text("\n".join(L) + "\n")
    return (f"{total_changed} of {n_tiles} tiles changed since {pdir.name}"
            + (f"; {outside} changed region(s) outside the scope" if outside else "") + " (changed.md)")
