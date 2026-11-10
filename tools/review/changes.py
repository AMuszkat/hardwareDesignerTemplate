"""The scope of a review and what changed since the board's previous snapshot.

Used by snapshot.py; plain python3, no pcbnew (the board file is read as text).

SCOPE (snapshot.py --scope) -- the board area a PCB review is about, in board mm:

    all | fab          everything (the default; a fab release reads every tile)
    R3  (or R3,R4)     a routing group (a milestone's groups): the copper its routing/R3.applied.json
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

OBJECT CHANGES (PCB) -- the same question answered in the board's own terms,
from two geometry.json dumps (tools/review/dump.py), so no reviewer writes a
script to find out what it is reviewing. The base is NOT the previous
snapshot but the newest earlier PCB snapshot of this board whose review got a
VERDICT (a PASS or CHANGES heading for its id in tools/handoff/reviews/
boardN.md): what the reviewers have already read is the thing to diff
against; a snapshot retaken, superseded or taken for a question is not.
CHANGES.md (read first) and changes.json list parts moved / added / removed /
changed, pads whose net changed, copper added and removed per net and layer
(segments, length), vias, zones (outline, fill islands and area), texts, the
outline and the board's rules. Objects are matched by what they ARE -- a
track by net, layer, both ends and width, a via by net, position, size and
span -- never by UUID alone: a re-routed net gets new UUIDs for copper that
did not move, and that is not a change. With no reviewed snapshot the files
say so and give the inventory: everything is new.

    python3 tools/review/changes.py OLD NEW     # any two snapshots (or geometry.json files), by hand
"""
import json
import math
import re
import subprocess
import sys
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
    """(label, box in board mm or None for everything, note). A comma list of routing or placement
    groups ("R3,R4", a milestone) is their union."""
    if not scope or scope.lower() in ("all", "fab", "fab release"):
        return (scope or "all"), None, "every tile"
    if "," in scope and not scope.startswith(("refs:", "box:")):
        got = [scope_box(board, s_.strip(), margin) for s_ in scope.split(",") if s_.strip()]
        boxes = [g[1] for g in got if g[1]]
        if len(boxes) < len(got):
            return scope, None, "a group in it names no copper: every tile"
        return scope, union(boxes), f"{len(got)} groups ({scope}) + {margin:g} mm"
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


# -- object changes: two geometry.json dumps, diffed ---------------------------------------

LIST_CAP = 60                       # rows of one kind CHANGES.md prints; changes.json holds all
HEADING = re.compile(r"^##\s+(\S+)\s+(?:—|–|--|-)\s+(.*)$")


def verdicts(path):
    """{review id: (kinds, verdict)} from a board's verdict file (tools/handoff/reviews/boardN.md).

    A review's heading is `## <id> — <kind>: <scope> — PASS` or `... — CHANGES (n must-fix)`,
    <kind> being `schematic`, `pcb` or `schematic + pcb`. A heading with any other ending
    (`SUPERSEDED (no verdict)`, a review still being written) gives no entry: that snapshot was
    never read to a verdict, so nothing may be diffed against it."""
    out = {}
    p = Path(path)
    if not p.exists():
        return out
    for ln in p.read_text().splitlines():
        m = HEADING.match(ln)
        if not m:
            continue
        parts = re.split(r"\s+(?:—|–|--)\s+", m.group(2))
        v = re.match(r"(PASS|CHANGES)\b", parts[-1].strip()) if len(parts) > 1 else None
        if v:
            out[m.group(1)] = (parts[0].split(":")[0].strip().lower(), v.group(1))
    return out


def reviews_file(root, board):
    """The verdict file of a board folder (`board1_main` -> tools/handoff/reviews/board1.md), or
    the path its handoff module names (`REVIEWS = "..."` in tools/handoff/boards/board1.py)."""
    m = re.match(r"(board\d+)", board)
    bid = m.group(1) if m else board
    mod = Path(root) / "tools" / "handoff" / "boards" / f"{bid}.py"
    if mod.exists():
        r = re.search(r"^REVIEWS\s*=\s*[\"']([^\"']+)[\"']", mod.read_text(), re.M)
        if r:
            return Path(root) / r.group(1)
    return Path(root) / "tools" / "handoff" / "reviews" / f"{bid}.md"


def reviewed_base(out, reviews):
    """The newest earlier PCB snapshot (a sibling folder of `out`) whose review id has a PASS or
    CHANGES verdict for the PCB in `reviews`: (taken, folder, id, verdict), or None.
    Also returns nothing for a snapshot whose manifest cannot be read."""
    got = verdicts(reviews)
    best = None
    for d in Path(out).parent.glob("*-pcb"):
        if d.resolve() == Path(out).resolve() or not (d / "manifest.json").exists():
            continue
        try:
            man = json.loads((d / "manifest.json").read_text())
        except ValueError:
            continue
        kinds, verdict = got.get(man.get("id"), ("", None))
        if not verdict or "pcb" not in kinds:
            continue
        if best is None or man.get("taken", "") > best[0]:
            best = (man.get("taken", ""), d, man.get("id"), verdict)
    return best


def base_geometry(base, out, kpy):
    """The geometry.json of an earlier snapshot; one taken before snapshots carried it is dumped
    now, from its frozen board (through kpy -- this is the only place this file starts pcbnew).
    Returns (path or None, note)."""
    g = Path(base) / "geometry.json"
    if g.exists():
        return g, ""
    try:
        board = json.loads((Path(base) / "manifest.json").read_text())["board"]
    except (OSError, ValueError, KeyError):
        return None, "its manifest names no board"
    src = Path(base) / "snap" / board
    if not list(src.glob("*.kicad_pcb")):
        return None, "its frozen board holds no .kicad_pcb"
    dump = Path(__file__).resolve().parent / "dump.py"
    for target in (g, Path(out) / "base-geometry.json"):      # the old snapshot first; ours if it is read-only
        try:
            r = subprocess.run([str(kpy), str(dump), str(src), str(target)], capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, f"dump.py could not run on it: {e}"
        if r.returncode == 0 and target.exists():
            return target, "its geometry.json was dumped now from its frozen board"
    return None, "dump.py failed on its frozen board: " + (r.stderr or r.stdout).strip().splitlines()[-1][:160]


def _k(v):
    """A coordinate (or a list of them) as a match key: 1 um."""
    return tuple(_k(x) for x in v) if isinstance(v, (list, tuple)) else round(v, 3)


def _track_key(t):
    ends = sorted((_k(t["a"]), _k(t["b"])))
    return (t["net"], t["layer"], ends[0], ends[1], _k(t["w"]), _k(t["mid"]) if "mid" in t else None)


def _via_key(v):
    return (v["net"], _k(v["at"]), _k(v["d"]), _k(v["drill"]), v["layers"][0], v["layers"][-1])


def _len(t):
    return t["length"] if "mid" in t else math.dist(t["a"], t["b"])


def _multi(rows, key):
    out = {}
    for r in rows:
        out.setdefault(key(r), []).append(r)
    return out


def _only(a, b):
    """The rows of multiset a that b does not hold (a key held twice in a and once in b: one)."""
    out = []
    for k, rows in a.items():
        out += rows[len(b.get(k, ())):]
    return out


def _box(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]


def _zone_facts(z):
    f = {L: (len(v["islands"]), v["area"]) for L, v in (z.get("filled") or {}).items()}
    return dict(net=z["net"], name=z.get("name", ""), layers=z["layers"], priority=z.get("priority"),
                outline=_k([[p["outline"], p["holes"]] for p in z["outline"]]), clearance=z.get("clearance"),
                min_width=z.get("min_width"), keepout=z.get("keepout"), fill=f)


def diff(old, new):
    """What differs between two geometry.json dicts (dump.py), old -> new: the dict changes.json
    holds (without its `base`).

    parts     moved (position, rotation or side differs), added, removed, changed (value,
              footprint, DNP flag or pad count), matched by reference
    pads      renetted: a pad of a part on both boards whose net differs (by pad number, and by
              order for pads sharing a number)
    copper    per (net, layer): segments and length added and removed, and the box of both; a
              track is the SAME track when net, layer, both ends, width (and an arc's midpoint)
              agree to 1 um, whatever its UUID
    vias      added / removed, the same via being one of the same net, centre, pad, drill and span
    zones     added, removed, changed (what: net, layers, priority, outline, clearance, min_width,
              keepout, and per layer the fill's islands and area), matched by UUID
    texts     moved (position, rotation, layer), changed (the text shown, visibility, size),
              added, removed, by UUID -- except a text that only followed its own part: a part
              that moved carries its texts with it, and `followed` counts those instead
    outline   changed: Edge.Cuts differs anywhere
    rules     the board's own minimums and net classes that differ
    """
    c = {}
    # parts
    po, pn_ = {p["ref"]: p for p in old.get("parts", [])}, {p["ref"]: p for p in new.get("parts", [])}
    moved, changed = [], []
    for ref in sorted(set(po) & set(pn_)):
        a, b = po[ref], pn_[ref]
        if _k(a["at"]) != _k(b["at"]) or _k(a["rot"]) != _k(b["rot"]) or a["side"] != b["side"]:
            row = dict(ref=ref, dist=round(math.dist(a["at"], b["at"]), 4), to=b["at"], rot=b["rot"], side=b["side"])
            row["from"], row["rot_from"], row["side_from"] = a["at"], a["rot"], a["side"]
            moved.append(row)
        what = {k: [a.get(k), b.get(k)] for k in ("value", "footprint", "dnp") if a.get(k) != b.get(k)}
        if what:
            changed.append(dict(ref=ref, what=what))
    brief = lambda p: dict(ref=p["ref"], value=p.get("value"), footprint=p.get("footprint"), at=p["at"], side=p["side"])
    c["parts"] = dict(moved=moved, added=[brief(pn_[r]) for r in sorted(set(pn_) - set(po))],
                      removed=[brief(po[r]) for r in sorted(set(po) - set(pn_))], changed=changed)
    # pads
    def by_part(d):
        out = {}
        for q in d.get("pads", []):
            out.setdefault(q["ref"], []).append(q)
        return out
    qo, qn = by_part(old), by_part(new)
    renet = []
    for ref in sorted(set(po) & set(pn_)):
        a, b = qo.get(ref, []), qn.get(ref, [])
        if len(a) != len(b):
            row = next((r for r in changed if r["ref"] == ref), None)
            if row is None:
                row = dict(ref=ref, what={})
                changed.append(row)
            row["what"]["pads"] = [len(a), len(b)]
            continue
        for x, y in zip(a, b):
            if x["num"] == y["num"] and x["net"] != y["net"]:
                row = {"ref": ref, "num": x["num"], "to": y["net"]}
                row["from"] = x["net"]
                renet.append(row)
    changed.sort(key=lambda r: r["ref"])
    c["pads"] = dict(renetted=renet)
    # copper
    ko = _multi(old.get("tracks", []) + old.get("arcs", []), _track_key)
    kn = _multi(new.get("tracks", []) + new.get("arcs", []), _track_key)
    added, removed = _only(kn, ko), _only(ko, kn)
    per = {}
    for rows, side in ((added, "added"), (removed, "removed")):
        for t in rows:
            e = per.setdefault((t["net"], t["layer"]), dict(net=t["net"], layer=t["layer"],
                               added=dict(segments=0, length=0.0), removed=dict(segments=0, length=0.0), pts=[]))
            e[side]["segments"] += 1
            e[side]["length"] += _len(t)
            e["pts"] += [t["a"], t["b"]]
    per_net = []
    for k in sorted(per):
        e = per[k]
        e["box"] = _box(e.pop("pts"))
        for side in ("added", "removed"):
            e[side]["length"] = round(e[side]["length"], 4)
        per_net.append(e)
    slim = lambda t: {k: t[k] for k in ("net", "layer", "a", "b", "w", "mid", "uuid") if k in t}
    c["copper"] = dict(per_net=per_net, added=[slim(t) for t in added], removed=[slim(t) for t in removed],
                       kept=sum(len(v) for v in kn.values()) - len(added))
    # vias
    vo, vn = _multi(old.get("vias", []), _via_key), _multi(new.get("vias", []), _via_key)
    c["vias"] = dict(added=_only(vn, vo), removed=_only(vo, vn))
    c["vias"]["kept"] = len(new.get("vias", [])) - len(c["vias"]["added"])
    # zones
    zo, zn = {z["uuid"]: z for z in old.get("zones", [])}, {z["uuid"]: z for z in new.get("zones", [])}
    zbrief = lambda z: dict(uuid=z["uuid"], net=z["net"], name=z.get("name", ""), layers=z["layers"], box=z.get("box"),
                            keepout=bool(z.get("keepout")))
    zch = []
    for u in zo.keys() & zn.keys():
        a, b = _zone_facts(zo[u]), _zone_facts(zn[u])
        what = {}
        for k in ("net", "name", "layers", "priority", "clearance", "min_width", "keepout"):
            if a[k] != b[k]:
                what[k] = [a[k], b[k]]
        if a["outline"] != b["outline"]:
            what["outline"] = [zo[u].get("box"), zn[u].get("box")]
        for L in sorted(set(a["fill"]) | set(b["fill"])):
            fa, fb = a["fill"].get(L), b["fill"].get(L)
            if fa is None or fb is None or fa[0] != fb[0] or abs(fa[1] - fb[1]) > 0.01:
                what.setdefault("fill", {})[L] = dict(islands=[fa[0] if fa else None, fb[0] if fb else None],
                                                      area=[fa[1] if fa else None, fb[1] if fb else None])
        if what:
            zch.append(dict(zbrief(zn[u]), what=what))
    zkey = lambda z: (z["net"], z["layers"], z["box"] or [], z["uuid"])
    c["zones"] = dict(added=sorted((zbrief(zn[u]) for u in zn.keys() - zo.keys()), key=zkey),
                      removed=sorted((zbrief(zo[u]) for u in zo.keys() - zn.keys()), key=zkey),
                      changed=sorted(zch, key=zkey))
    # texts
    to, tn = {t["uuid"]: t for t in old.get("texts", [])}, {t["uuid"]: t for t in new.get("texts", [])}
    shift = {r["ref"]: r for r in moved}
    gone = set(po) ^ set(pn_)
    tmoved, tchanged, followed = [], [], 0
    tbrief = lambda t: dict(text=t["text"], part=t.get("part"), kind=t["kind"], layer=t["layer"], at=t["at"],
                            visible=t.get("visible", True), uuid=t["uuid"])
    for u in to.keys() & tn.keys():
        a, b = to[u], tn[u]
        what = {k: [a.get(k), b.get(k)] for k in ("text", "visible", "height", "thickness") if a.get(k) != b.get(k)}
        if what:
            tchanged.append(dict(tbrief(b), what=what))
        if not (a.get("visible", True) and b.get("visible", True)):
            continue                                       # a hidden text has no place on the board
        if _k(a["at"]) == _k(b["at"]) and _k(a["rot"]) == _k(b["rot"]) and a["layer"] == b["layer"]:
            continue
        m = shift.get(b.get("part"))
        if m:
            rigid = (m["rot"] == m["rot_from"] and m["side"] == m["side_from"] and a["layer"] == b["layer"]
                     and _k(a["rot"]) == _k(b["rot"])
                     and math.dist((b["at"][0] - a["at"][0], b["at"][1] - a["at"][1]),
                                   (m["to"][0] - m["from"][0], m["to"][1] - m["from"][1])) < 0.002)
            if rigid or m["rot"] != m["rot_from"] or m["side"] != m["side_from"]:
                followed += 1                              # carried by its part (turned or flipped with it)
                continue
        row = dict(tbrief(b), dist=round(math.dist(a["at"], b["at"]), 4))
        row["from"], row["rot_from"], row["layer_from"], row["rot"] = a["at"], a["rot"], a["layer"], b["rot"]
        tmoved.append(row)
    tkey = lambda t: (t.get("part") or "", t["kind"], t["text"], t["uuid"])
    c["texts"] = dict(moved=sorted(tmoved, key=tkey), changed=sorted(tchanged, key=tkey),
                      added=sorted((tbrief(tn[u]) for u in tn.keys() - to.keys() if tn[u].get("part") not in gone),
                                   key=tkey),
                      removed=sorted((tbrief(to[u]) for u in to.keys() - tn.keys() if to[u].get("part") not in gone),
                                     key=tkey), followed=followed)
    # outline and rules
    bo, bn = old.get("board", {}), new.get("board", {})
    eo = sorted(tuple(sorted(_k(e))) for e in bo.get("edges", []))
    en = sorted(tuple(sorted(_k(e))) for e in bn.get("edges", []))
    c["outline"] = dict(changed=eo != en, box=bn.get("outline_box"))
    if eo != en:
        c["outline"]["box_from"] = bo.get("outline_box")
    rules = []
    for k in sorted(set(bo.get("rules", {})) | set(bn.get("rules", {}))):
        if bo.get("rules", {}).get(k) != bn.get("rules", {}).get(k):
            rules.append(dict(rule=k, to=bn.get("rules", {}).get(k)))
            rules[-1]["from"] = bo.get("rules", {}).get(k)
    for k in sorted(set(bo.get("netclasses", {})) | set(bn.get("netclasses", {}))):
        if bo.get("netclasses", {}).get(k) != bn.get("netclasses", {}).get(k):
            rules.append(dict(rule=f"net class {k}", to=bn.get("netclasses", {}).get(k)))
            rules[-1]["from"] = bo.get("netclasses", {}).get(k)
    for k in sorted(set(bo.get("net_class", {})) | set(bn.get("net_class", {}))):
        if bo.get("net_class", {}).get(k) != bn.get("net_class", {}).get(k):
            rules.append(dict(rule=f"class of net {k}", to=bn.get("net_class", {}).get(k)))
            rules[-1]["from"] = bo.get("net_class", {}).get(k)
    if bo.get("copper_layers") != bn.get("copper_layers"):
        rules.append(dict(rule="copper layers", to=bn.get("copper_layers")))
        rules[-1]["from"] = bo.get("copper_layers")
    c["rules"] = rules
    c["summary"] = counts(c)
    return c


def counts(c):
    cu = c["copper"]
    return dict(
        parts_moved=len(c["parts"]["moved"]), parts_added=len(c["parts"]["added"]),
        parts_removed=len(c["parts"]["removed"]), parts_changed=len(c["parts"]["changed"]),
        pads_renetted=len(c["pads"]["renetted"]),
        tracks_added=len(cu["added"]), tracks_removed=len(cu["removed"]),
        length_added=round(sum(e["added"]["length"] for e in cu["per_net"]), 4),
        length_removed=round(sum(e["removed"]["length"] for e in cu["per_net"]), 4),
        nets_with_copper_changes=len({e["net"] for e in cu["per_net"]}),
        vias_added=len(c["vias"]["added"]), vias_removed=len(c["vias"]["removed"]),
        zones_added=len(c["zones"]["added"]), zones_removed=len(c["zones"]["removed"]),
        zones_changed=len(c["zones"]["changed"]),
        texts_moved=len(c["texts"]["moved"]), texts_changed=len(c["texts"]["changed"]),
        texts_added=len(c["texts"]["added"]), texts_removed=len(c["texts"]["removed"]),
        outline_changed=bool(c["outline"]["changed"]), rules_changed=len(c["rules"]))


def inventory(g):
    """What a board holds, for a first review: counts, and the routed length per copper layer."""
    per = {}
    for t in g.get("tracks", []) + g.get("arcs", []):
        e = per.setdefault(t["layer"], dict(segments=0, length=0.0))
        e["segments"] += 1
        e["length"] += _len(t)
    order = g.get("board", {}).get("copper_layers", [])
    return dict(parts=len(g.get("parts", [])), pads=len(g.get("pads", [])),
                tracks=len(g.get("tracks", [])) + len(g.get("arcs", [])), vias=len(g.get("vias", [])),
                zones=len(g.get("zones", [])), texts=len(g.get("texts", [])),
                copper={L: dict(segments=per[L]["segments"], length=round(per[L]["length"], 4))
                        for L in sorted(per, key=lambda L_: order.index(L_) if L_ in order else 99)})


def one_line(c):
    """The SUMMARY row's text for a diff."""
    n = c["summary"]
    bits = []
    for label, keys in (("part(s) moved", ("parts_moved",)), ("part(s) added", ("parts_added",)),
                        ("part(s) removed", ("parts_removed",)), ("part(s) changed", ("parts_changed",)),
                        ("pad(s) on another net", ("pads_renetted",))):
        if n[keys[0]]:
            bits.append(f"{n[keys[0]]} {label}")
    if n["tracks_added"] or n["tracks_removed"]:
        bits.append(f"tracks +{n['tracks_added']} / -{n['tracks_removed']} on {n['nets_with_copper_changes']} net(s) "
                    f"(+{n['length_added']:.2f} / -{n['length_removed']:.2f} mm)")
    if n["vias_added"] or n["vias_removed"]:
        bits.append(f"vias +{n['vias_added']} / -{n['vias_removed']}")
    z = n["zones_added"] + n["zones_removed"] + n["zones_changed"]
    if z:
        bits.append(f"{z} zone(s) added, removed or changed")
    t = n["texts_moved"] + n["texts_changed"] + n["texts_added"] + n["texts_removed"]
    if t:
        bits.append(f"{t} text(s) moved, changed, added or removed")
    if n["outline_changed"]:
        bits.append("the outline changed")
    if n["rules_changed"]:
        bits.append(f"{n['rules_changed']} board rule(s) changed")
    return "; ".join(bits) if bits else "nothing changed"


def _pt(p):
    return f"({p[0]:.3f}, {p[1]:.3f})"


def _outside(pts, sbox):
    """True when none of the points lies in the scope box (no box: nothing is outside)."""
    return bool(sbox) and not any(sbox[0] <= p[0] <= sbox[2] and sbox[1] <= p[1] <= sbox[3] for p in pts)


def markdown(c, base_note, sbox=None, scope_label=""):
    """CHANGES.md for a diff: one table per kind of object, LIST_CAP rows each (changes.json
    holds every row). With a scope box, a change that lies wholly outside it is marked: a
    change the request did not declare is a finding."""
    out_n = [0]

    def mark(*pts):
        if _outside(pts, sbox):
            out_n[0] += 1
            return " **outside the scope**"
        return ""

    def tab(title, header, rows, total=None):
        if not rows:
            return []
        L = [f"## {title} ({total if total is not None else len(rows)})", "", "| " + " | ".join(header) + " |",
             "|" + "---|" * len(header)]
        L += ["| " + " | ".join(str(v).replace("|", "/") for v in r) + " |" for r in rows[:LIST_CAP]]
        if len(rows) > LIST_CAP:
            L += ["", f"... {len(rows) - LIST_CAP} more in `changes.json`."]
        return L + [""]

    P, B = c["parts"], []
    B += tab("Parts moved", ["ref", "from", "to", "moved mm", "rotation", "side", ""],
             [[r["ref"], _pt(r["from"]), _pt(r["to"]), f"{r['dist']:.3f}",
               f"{r['rot_from']:g} -> {r['rot']:g}" if r["rot_from"] != r["rot"] else f"{r['rot']:g}",
               f"{r['side_from']} -> {r['side']}" if r["side_from"] != r["side"] else r["side"],
               mark(r["from"], r["to"])] for r in P["moved"]])
    B += tab("Parts added", ["ref", "value", "footprint", "at", "side", ""],
             [[r["ref"], r["value"], r["footprint"], _pt(r["at"]), r["side"], mark(r["at"])] for r in P["added"]])
    B += tab("Parts removed", ["ref", "value", "footprint", "was at", "side"],
             [[r["ref"], r["value"], r["footprint"], _pt(r["at"]), r["side"]] for r in P["removed"]])
    B += tab("Parts changed", ["ref", "what", "from", "to"],
             [[r["ref"], k, v[0], v[1]] for r in P["changed"] for k, v in r["what"].items()])
    B += tab("Pads on another net", ["pad", "from", "to"],
             [[f"{r['ref']}.{r['num']}", r["from"] or "(no net)", r["to"] or "(no net)"] for r in c["pads"]["renetted"]])
    cu = c["copper"]
    B += tab("Copper added and removed, per net and layer",
             ["net", "layer", "+ segments", "+ mm", "- segments", "- mm", "box of the change", ""],
             [[e["net"] or "(no net)", e["layer"], e["added"]["segments"], f"{e['added']['length']:.3f}",
               e["removed"]["segments"], f"{e['removed']['length']:.3f}", " ".join(f"{v:.2f}" for v in e["box"]),
               mark(e["box"][:2], e["box"][2:])] for e in cu["per_net"]])
    if cu["per_net"]:
        n = c["summary"]
        B += [f"{n['tracks_added']} track(s) added ({n['length_added']:.3f} mm), {n['tracks_removed']} removed "
              f"({n['length_removed']:.3f} mm), {cu['kept']} unchanged. Each one, with its ends and width: "
              "`changes.json` (`copper.added`, `copper.removed`).", ""]
    for side, title in (("added", "Vias added"), ("removed", "Vias removed")):
        B += tab(title, ["net", "at", "pad / drill", "layers", ""],
                 [[v["net"] or "(no net)", _pt(v["at"]), f"{v['d']:g} / {v['drill']:g}",
                   f"{v['layers'][0]}..{v['layers'][-1]}", mark(v["at"]) if side == "added" else ""]
                  for v in c["vias"][side]])
    Z = c["zones"]
    zname = lambda z: (("rule area " if z["keepout"] else "") + (z["name"] or z["net"] or "(no net)"))
    zbox = lambda z: " ".join(f"{v:.2f}" for v in z["box"]) if z.get("box") else ""
    B += tab("Zones added", ["zone", "layers", "box"], [[zname(z), ", ".join(z["layers"]), zbox(z)] for z in Z["added"]])
    B += tab("Zones removed", ["zone", "layers", "box"], [[zname(z), ", ".join(z["layers"]), zbox(z)] for z in Z["removed"]])
    zrows = []
    for z in Z["changed"]:
        for k, v in z["what"].items():
            if k == "fill":
                for L, f in v.items():
                    zrows.append([zname(z), f"fill on {L}", f"{f['islands'][0]} island(s), {f['area'][0]} mm2",
                                  f"{f['islands'][1]} island(s), {f['area'][1]} mm2"])
            elif k == "outline":
                zrows.append([zname(z), "outline (box)", v[0], v[1]])
            else:
                zrows.append([zname(z), k, v[0], v[1]])
    B += tab("Zones changed", ["zone", "what", "from", "to"], zrows, len(Z["changed"]))
    T = c["texts"]
    tname = lambda t: f"'{t['text']}'" + (f" ({t['part']} {t['kind']})" if t.get("part") else " (board text)")
    B += tab("Texts moved", ["text", "layer", "from", "to", "moved mm", ""],
             [[tname(t), t["layer"] if t["layer"] == t["layer_from"] else f"{t['layer_from']} -> {t['layer']}",
               _pt(t["from"]), _pt(t["at"]), f"{t['dist']:.3f}", mark(t["from"], t["at"])] for t in T["moved"]])
    B += tab("Texts changed", ["text", "layer", "what", "from", "to"],
             [[tname(t), t["layer"], k, v[0], v[1]] for t in T["changed"] for k, v in t["what"].items()])
    B += tab("Texts added", ["text", "layer", "at", ""],
             [[tname(t), t["layer"], _pt(t["at"]), mark(t["at"])] for t in T["added"]])
    B += tab("Texts removed", ["text", "layer", "was at"], [[tname(t), t["layer"], _pt(t["at"])] for t in T["removed"]])
    if T["followed"]:
        B += [f"{T['followed']} text(s) only followed their own part (not listed).", ""]
    if c["outline"]["changed"]:
        B += ["## Outline", "", f"Edge.Cuts changed: box {c['outline'].get('box_from')} -> {c['outline']['box']}.", ""]
    B += tab("Board rules changed", ["rule", "from", "to"], [[r["rule"], r["from"], r["to"]] for r in c["rules"]])
    head = ["# What changed on the board, object by object", "", base_note, ""]
    if sbox:
        head += [f"Scope {scope_label}: " + (f"**{out_n[0]} change(s) lie wholly outside it** (marked below)."
                                             if out_n[0] else "every change listed touches it."), ""]
    head += ["**" + one_line(c)[0].upper() + one_line(c)[1:] + ".**", ""]
    if not B:
        head += ["Nothing on the board differs from that snapshot: no part, pad net, track, via, zone, text, "
                 "outline or rule.", ""]
    c["summary"]["outside_scope"] = out_n[0]
    return "\n".join(head + B) + "\n"


def objects(out, root, board, kpy, scope=None):
    """Write CHANGES.md and changes.json into a PCB snapshot (its geometry.json is there already)
    and return the SUMMARY line. scope: (label, box in board mm or None, note) from scope_box."""
    out = Path(out)
    label, sbox, _note = scope or ("all", None, "")
    new = json.loads((out / "geometry.json").read_text())
    reviews = reviews_file(root, board)
    base = reviewed_base(out, reviews)
    prev = previous(out, "pcb")
    note = ""
    if base:
        gpath, how = base_geometry(base[1], out, kpy)
        if gpath is None:
            note, base = (f"`{base[1].name}` is the last reviewed PCB snapshot ({base[3]}), but it cannot be "
                          f"diffed: {how}."), None
    if not base:
        inv = inventory(new)
        why = note or (f"No earlier PCB snapshot of this board has a verdict in `{reviews.relative_to(root)}`"
                       if reviews.is_relative_to(root) else f"No earlier PCB snapshot of this board has a verdict in `{reviews}`")
        if not note and prev:
            why += f" (`{prev[1].name}` exists, but was never reviewed to a PASS or CHANGES)"
        L = ["# What changed on the board, object by object", "", why.rstrip(".") + ".", "",
             "**Everything on the board is new to review.** What it holds:", "",
             f"- {inv['parts']} parts, {inv['pads']} pads, {inv['zones']} zones, {inv['texts']} texts",
             f"- {inv['tracks']} tracks, {inv['vias']} vias"]
        L += [f"- {L_}: {e['segments']} track(s), {e['length']:.3f} mm" for L_, e in inv["copper"].items()]
        (out / "CHANGES.md").write_text("\n".join(L) + "\n")
        (out / "changes.json").write_text(json.dumps(dict(base=None, why=why, inventory=inv), indent=1) + "\n")
        return (f"no reviewed PCB snapshot to diff against: all new -- {inv['parts']} parts, {inv['tracks']} tracks, "
                f"{inv['vias']} vias, {inv['zones']} zones (CHANGES.md)")
    taken, bdir, bid, verdict = base
    c = diff(json.loads(gpath.read_text()), new)
    base_note = (f"Against `{bdir.name}` (taken {taken}), the last PCB snapshot of this board with a verdict "
                 f"({bid}: {verdict})" + (f"; {how}" if how else "") + ". Positions are board mm. Objects are "
                 "matched by net, layer and geometry, not by UUID.")
    if prev and prev[1].resolve() != bdir.resolve() and prev[0] > taken:
        base_note += (f" `{prev[1].name}` is newer but has no verdict, so it is not the base "
                      "(`changed.md` compares the pictures against it).")
    md = markdown(c, base_note, sbox, label)
    (out / "CHANGES.md").write_text(md)
    doc = dict(base=dict(id=bid, snapshot=bdir.name, taken=taken, verdict=verdict), scope=label)
    doc.update(c)
    (out / "changes.json").write_text(json.dumps(doc, indent=1) + "\n")
    return (f"since {bid} ({verdict}): {one_line(c)}"
            + (f"; {c['summary']['outside_scope']} change(s) outside the scope" if c["summary"].get("outside_scope") else "")
            + " (CHANGES.md)")


def main(argv=None):
    """changes.py OLD NEW -- the object diff of any two PCB snapshots (or geometry.json files),
    printed as CHANGES.md is written; --json prints changes.json's content instead."""
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    if len(args) != 2:
        print(main.__doc__)
        return 2
    g = []
    for a in args:
        p = Path(a)
        p = p / "geometry.json" if p.is_dir() else p
        if not p.exists():
            print(f"changes: no {p} -- a PCB snapshot writes it; for an older one: "
                  "kicad_project/tools/kpy tools/review/dump.py <snapshot>/snap/<board> <snapshot>/geometry.json")
            return 2
        g.append(json.loads(p.read_text()))
    c = diff(g[0], g[1])
    print(json.dumps(c, indent=1) if as_json else markdown(c, f"`{args[0]}` -> `{args[1]}`. Positions are board mm."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
