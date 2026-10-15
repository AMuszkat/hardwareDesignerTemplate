#!/usr/bin/env python3
"""Put a library symbol's Reference and Value where every unit can use them
(tools/sch/README.md, "Symbols"). Konnect's create_symbol puts Value under
unit 1's body and no Konnect call moves a library field; this does, and
nothing else -- but for the two library edits Konnect gets wrong below.

    tools/.venv/bin/python tools/sch/symfields.py --board board1 SYMBOL ... [--lib NICK] [--dry-run]
    tools/.venv/bin/python tools/sch/symfields.py --board board1 --derive Device:R [--dry-run]
    tools/.venv/bin/python tools/sch/symfields.py --board board1 --delete SYMBOL ... [--dry-run]

Corner (the default): Reference 3.81 mm and Value 1.27 mm above the unit
bodies' shared top-left corner, left- and bottom-justified. A multi-unit
symbol whose units do not share that corner is refused -- draw every unit
with its top-left at one point first.

--derive LIB:NAME copies KiCad's stock symbol into the board's library under
the same name, unchanged but for its two fields, which go BESIDE the body:
Reference right of it and Value left of it, both along the body (the way
Device:R already carries its Reference). For a two-pin part whose stock Value
sits inside its body (Device:R), where any Value longer than the body runs
over the part's own wires.

--delete removes whole symbols, for a rebuild (Konnect create_symbol again,
e.g. with longer pins). Konnect's delete_symbol cannot do it once this tool
has written the library: it finds only symbols in its own two-space layout,
and kicad-cli (like KiCad's Symbol Editor) saves with tabs -- "not found".
And create_symbol on a name already there appends a second copy instead of
failing. A symbol another one `extends` is refused.

How the file is written (the parsed tree, as fp.py does for footprints):
kicad-cli resaves the library once as it stands (A) and once with the edit
(B); B is installed only if A and B are identical apart from the fields this
tool sets (and, for --derive, the new symbol is the stock one resaved, apart
from the same fields). A backup goes to <board>/.sch_backups/<timestamp>/
first. Run it with KiCad's Symbol Editor closed on this library; a schematic
picks the change up when its symbols are placed or updated from the library.
"""
import argparse
import copy
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import boardsel                                     # noqa: E402
import sexpr                                        # noqa: E402

REF_DY, VAL_DY = 3.81, 1.27
BESIDE = 1.016                     # body edge to the text's centre line, as Device:R's Reference sits


def die(msg):
    print(f"symfields: {msg}", file=sys.stderr)
    sys.exit(2)


def lib_path(board, nick):
    t = Path(board) / "sym-lib-table"
    for lib in sexpr.every(sexpr.load(t), "lib"):
        if str(sexpr.atom(lib, "name")) == nick:
            return Path(str(sexpr.atom(lib, "uri")).replace("${KIPRJMOD}", str(board)))
    die(f"no library '{nick}' in {t}")


def stock_dir():
    for v in ("KICAD10_SYMBOL_DIR", "KICAD9_SYMBOL_DIR", "KICAD8_SYMBOL_DIR"):
        if os.environ.get(v):
            return Path(os.environ[v])
    for p in (Path("/usr/share/kicad/symbols"), Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")):
        if p.exists():
            return p
    die("KiCad's symbol directory not found")


def resave(src, out):
    r = subprocess.run(["kicad-cli", "sym", "upgrade", "--force", "-o", str(out), str(src)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not Path(out).exists():
        die(f"kicad-cli refused {src}:\n{r.stdout}{r.stderr}")
    return sexpr.load(out)


def symbols(tree):
    return {str(s[1]): s for s in sexpr.every(tree, "symbol")}


def bodies(sym):
    """{unit: [x0, y0, x1, y1]} of each unit's rectangles, library coordinates (y up)."""
    out = {}
    for u in sexpr.every(sym, "symbol"):
        for r in sexpr.every(u, "rectangle"):
            (x0, y0), (x1, y1) = [[float(v) for v in sexpr.value(r, k)] for k in ("start", "end")]
            unit = str(u[1]).rsplit("_", 2)[-2]
            b = out.setdefault(unit, [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)])
            b[:] = [min(b[0], x0, x1), min(b[1], y0, y1), max(b[2], x0, x1), max(b[3], y0, y1)]
    if not out:
        die(f"{sym[1]}: no rectangle body")
    return out


def corner_spots(sym):
    """Reference / Value just above the bodies' shared top-left corner."""
    bs = bodies(sym)
    multi = {u: b for u, b in bs.items() if u != "0"}
    pts = {(round(b[0], 3), round(b[3], 3)) for b in (multi or bs).values()}
    if len(pts) > 1:
        die(f"{sym[1]}: the units' top-left corners differ ({sorted(bs.items())}); "
            "draw every unit with its top-left at one point first")
    x, y = pts.pop()
    return {"Reference": (x, y + REF_DY, 0, ["left", "bottom"]), "Value": (x, y + VAL_DY, 0, ["left", "bottom"])}


def beside_spots(sym):
    """Reference right of a single body, Value left of it, both along it."""
    bs = bodies(sym)
    if len(bs) != 1:
        die(f"{sym[1]}: --derive places fields beside one body; it has {len(bs)}")
    x0, y0, x1, y1 = next(iter(bs.values()))
    cy = round((y0 + y1) / 2, 4)
    return {"Reference": (x1 + BESIDE, cy, 90, None), "Value": (x0 - BESIDE, cy, 90, None)}


def place(prop, x, y, angle, justify):
    """(at x y angle) and the justification on a property node, in place."""
    at = sexpr.first(prop, "at")
    at[1:] = [f"{x:g}", f"{y:g}", f"{angle:g}"]
    eff = sexpr.first(prop, "effects")
    if eff is None:
        eff = ["effects", ["font", ["size", "1.27", "1.27"]]]
        prop.append(eff)
    eff[:] = [c for c in eff if not sexpr.is_node(c, "justify")] + ([["justify"] + justify] if justify else [])


def strip(tree, names):
    """The tree without what this tool may change: the two fields' at / justify."""
    t = copy.deepcopy(tree)
    for name, s in symbols(t).items():
        if name not in names:
            continue
        for p in sexpr.every(s, "property"):
            if str(p[1]) in ("Reference", "Value"):
                p[:] = [c for c in p if not sexpr.is_node(c, "at")]
                eff = sexpr.first(p, "effects")
                if eff is not None:
                    eff[:] = [c for c in eff if not sexpr.is_node(c, "justify")]
    return t


def without(tree, names):
    t = copy.deepcopy(tree)
    t[:] = [c for c in t if not (sexpr.is_node(c, "symbol") and str(c[1]) in names)]
    return t


def stock_symbol(lib_id, tmp):
    """KiCad's stock symbol, resaved on its own by kicad-cli (its canonical text)."""
    libname, name = lib_id.split(":", 1)
    f = stock_dir() / f"{libname}.kicad_sym"
    syms = symbols(sexpr.load(f))
    if name not in syms:
        die(f"{lib_id} not found in {f}")
    if sexpr.atom(syms[name], "extends"):
        die(f"{lib_id} extends another symbol; derive that one")
    (tmp / "s_in.kicad_sym").write_text(f'(kicad_symbol_lib (version 20251024) (generator "symfields")\n'
                                        f'{sexpr.dump(syms[name], 1)}\n)\n')
    return symbols(resave(tmp / "s_in.kicad_sym", tmp / "s.kicad_sym"))[name]


def delete(a, board, nick, path, tmp, A):
    names = list(dict.fromkeys(a.symbols))
    syms = symbols(A)
    for name in names:
        if name not in syms:
            die(f"{name} is not in {path}")
        heirs = [n for n, s in syms.items() if str(sexpr.atom(s, "extends") or "") == name and n not in names]
        if heirs:
            die(f"{name} is extended by {', '.join(heirs)} -- delete those too, or keep it")
    edited = without(A, set(names))
    (tmp / "b_in.kicad_sym").write_text(sexpr.dump(edited) + "\n")
    B = resave(tmp / "b_in.kicad_sym", tmp / "b.kicad_sym")
    if B != edited:
        die(f"the resaved library differs beyond the deleted symbols -- nothing written (scratch: {tmp})")
    for name in names:
        pins = sum(len(list(sexpr.every(u, "pin"))) for u in sexpr.every(syms[name], "symbol"))
        print(f"{name:28} deleted ({pins} pins)")
    if a.dry_run:
        print(f"dry run: proven, nothing written (scratch: {tmp})")
        return
    bak = board / ".sch_backups" / time.strftime("%Y%m%d-%H%M%S")
    bak.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, bak / path.name)
    shutil.copy2(tmp / "b.kicad_sym", path)
    if sexpr.load(path) != B:
        die(f"read-back differs -- restore {bak / path.name}")
    print(f"wrote {path} (backup {bak / path.name}); {len(names)} symbol(s) deleted, nothing else changed")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--board")
    ap.add_argument("--lib", help="library nickname in the board's sym-lib-table (default: the board's stem)")
    ap.add_argument("--derive", metavar="LIB:NAME", help="copy KiCad's stock symbol in, fields beside its body")
    ap.add_argument("--delete", action="store_true", help="remove the named symbols (to rebuild them with Konnect)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if bool(a.symbols) == bool(a.derive):
        die("name the board's symbols, or --derive one stock symbol")
    if a.delete and a.derive:
        die("--delete takes the board's symbols by name, not --derive")
    board = Path(boardsel.resolve(a.board))
    nick = a.lib or board.name.split("_")[0]
    path = lib_path(board, nick)
    tmp = Path(tempfile.mkdtemp(prefix="symfields-"))
    A = resave(path, tmp / "a.kicad_sym")
    edited = copy.deepcopy(A)
    stock = None
    if a.derive:
        stock = stock_symbol(a.derive, tmp)
        name = str(stock[1])
        if name in symbols(A):
            die(f"{nick}:{name} already exists -- delete it first (--delete) to derive it again")
        edited.append(copy.deepcopy(stock))
        names, spots_of = [name], beside_spots
    elif a.delete:
        return delete(a, board, nick, path, tmp, A)
    else:
        names, spots_of = a.symbols, corner_spots
    syms = symbols(edited)
    want = {}
    for name in names:
        if name not in syms:
            die(f"{name} is not in {path}")
        s = syms[name]
        want[name] = spots_of(s)
        props = {str(p[1]): p for p in sexpr.every(s, "property")}
        for f, (x, y, ang, j) in want[name].items():
            old = sexpr.value(props[f], "at")
            place(props[f], x, y, ang, j)
            print(f"{name:28} {f:9} ({', '.join(old)}) -> ({x:g}, {y:g}, {ang:g}) {' '.join(j or ['centre'])}")
    (tmp / "b_in.kicad_sym").write_text(sexpr.dump(edited) + "\n")
    B = resave(tmp / "b_in.kicad_sym", tmp / "b.kicad_sym")
    if stock is not None:
        if strip(without(A, names), set()) != strip(without(B, names), set()):
            die(f"the resaved library differs beyond the derived symbol -- nothing written (scratch: {tmp})")
        if strip(["lib", stock], names) != strip(["lib", symbols(B)[names[0]]], names):
            die(f"the derived symbol differs from KiCad's beyond its two fields -- nothing written (scratch: {tmp})")
    elif strip(A, set(names)) != strip(B, set(names)):
        die(f"the resaved library differs beyond the two fields -- nothing written (scratch: {tmp})")
    for name in names:
        props = {str(p[1]): p for p in sexpr.every(symbols(B)[name], "property")}
        for f, (x, y, ang, _) in want[name].items():
            got = [float(v) for v in sexpr.value(props[f], "at")[:3]]
            if abs(got[0] - x) > 1e-3 or abs(got[1] - y) > 1e-3 or abs(got[2] - ang) > 1e-3:
                die(f"{name} {f}: KiCad resaved it at {got} -- nothing written")
    if a.dry_run:
        print(f"dry run: proven, nothing written (scratch: {tmp})")
        return
    bak = board / ".sch_backups" / time.strftime("%Y%m%d-%H%M%S")
    bak.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, bak / path.name)
    shutil.copy2(tmp / "b.kicad_sym", path)
    if strip(sexpr.load(path), set(names)) != strip(B, set(names)):
        die(f"read-back differs -- restore {bak / path.name}")
    what = f"{nick}:{names[0]} derived from {a.derive}" if stock is not None else \
        f"only Reference / Value of {len(names)} symbol(s) moved"
    print(f"wrote {path} (backup {bak / path.name}); {what}")


if __name__ == "__main__":
    main()
