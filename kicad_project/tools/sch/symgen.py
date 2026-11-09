#!/usr/bin/env python3
"""A library symbol from a pin table, drawn to the standard the first time.

    tools/.venv/bin/python tools/sch/symgen.py --board board1 PINS.csv --name STM32G0 [--ref U]
        [--lib NICK] [--footprint LIB:FP] [--datasheet URL] [--dry-run]

PINS.csv (a header row; extra columns ignored):

    number,name,type,group[,side]
    1,PA0,bidirectional,GPIO A
    17,VDD,power_in,Power,L

  type   KiCad's electrical type: input, output, bidirectional, tri_state, passive,
         free, unspecified, power_in, power_out, open_collector, open_emitter,
         no_connect
  group  the unit the pin goes in (a function, a bank, a power domain), in the
         order the groups first appear; one group = one unit
  side   L or R (optional): else inputs and power inputs left, outputs and power
         outputs right, the rest filling the shorter side in table order

What it guarantees -- the rules `sch.py check` judges a placed symbol by
(tools/sch/README.md, "Symbols"), so a sheet needs no fix-up afterwards:

  * at most 40 pin rows on a side (pins-side): a group past 80 pins becomes
    GROUP_1, GROUP_2, ... units, each split across both sides;
  * every unit's body has its top-left corner at the origin, and Reference and
    Value sit just above it, left-justified (field-far: the one field position
    every unit shares);
  * each side's pins are long enough for that side's widest number with a
    1.27 mm margin (text-graphic: a number wider than its pin crosses the body
    outline), on the 1.27 mm grid;
  * the body is as wide as the two sides' longest pin names need, on the
    2.54 mm grid; rows 2.54 mm apart.

Before anything is written it prints, pin by pin, how the new symbol differs
from the one of that name it replaces (name, type, unit; missing, extra) -- a
rebuild is where a ball silently changes type. The library is written as
symfields.py does: backup under <board>/.sch_backups/, kicad-cli resaves it,
and the resaved symbol is read back and compared with the table, every pin.
"""
import argparse
import csv
import datetime
import math
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import boardsel  # noqa: E402
import model  # noqa: E402
import sexpr  # noqa: E402
import symfields  # noqa: E402

TYPES = {"input", "output", "bidirectional", "tri_state", "passive", "free", "unspecified", "power_in",
         "power_out", "open_collector", "open_emitter", "no_connect"}
ROW, GRID, PER_SIDE = 2.54, 1.27, 40
NAME_OFF = 0.508            # pin_names offset: a name's gap from the body line


def die(msg):
    print(f"symgen: {msg}", file=sys.stderr)
    sys.exit(2)


def read_table(path):
    rows = []
    with open(path, newline="") as f:
        for i, r in enumerate(csv.DictReader(f), 2):
            r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
            if not r.get("number") or not r.get("name"):
                die(f"{path}:{i}: a row needs number and name")
            t = (r.get("type") or "passive").lower()
            if t not in TYPES:
                die(f"{path}:{i}: type '{t}' is not one of {', '.join(sorted(TYPES))}")
            side = (r.get("side") or "").upper()[:1]
            if side not in ("", "L", "R"):
                die(f"{path}:{i}: side must be L or R")
            rows.append(dict(number=r["number"], name=r["name"], type=t, group=r.get("group") or "Pins", side=side))
    nums = [r["number"] for r in rows]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        die(f"{path}: pin number(s) twice: {dup[:10]}")
    return rows


def units(rows):
    """[(unit name, [pins])] in the order the groups first appear; a group past 2 x 40 splits."""
    groups = {}
    for r in rows:
        groups.setdefault(r["group"], []).append(r)
    out = []
    for g, pins in groups.items():
        cap = 2 * PER_SIDE
        if len(pins) <= cap:
            out.append((g, pins))
        else:
            for k in range(math.ceil(len(pins) / cap)):
                out.append((f"{g}_{k + 1}", pins[k * cap:(k + 1) * cap]))
    return out


def sides(pins):
    left = [p for p in pins if p["side"] == "L"]
    right = [p for p in pins if p["side"] == "R"]
    rest = [p for p in pins if not p["side"]]
    for p in rest:
        if p["type"] in ("input", "power_in") and len(left) < PER_SIDE:
            left.append(p)
        elif p["type"] in ("output", "power_out") and len(right) < PER_SIDE:
            right.append(p)
    for p in rest:
        if p in left or p in right:
            continue
        (left if len(left) <= len(right) and len(left) < PER_SIDE else right).append(p)
    if len(left) > PER_SIDE or len(right) > PER_SIDE:
        die(f"more than {PER_SIDE} pins on a side ({len(left)} left, {len(right)} right): move some with "
            "the side column, or split the group")
    return left, right


def pin_len(pins):
    return max([2.54] + [symfields.need(p["number"]) for p in pins])


def esc(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def symbol_text(name, ref, us, footprint, datasheet):
    """The symbol, KiCad library coordinates (y up): each body x 0..W, y 0..-H."""
    eff = "(effects (font (size 1.27 1.27)) (justify left bottom))"
    hid = "(effects (font (size 1.27 1.27)) (hide yes))"
    L = [f"\t(symbol {esc(name)}", "\t\t(pin_names (offset 0.508))", "\t\t(exclude_from_sim no) (in_bom yes) (on_board yes)",
         f"\t\t(property \"Reference\" {esc(ref)} (at 0 3.81 0) {eff})",
         f"\t\t(property \"Value\" {esc(name)} (at 0 1.27 0) {eff})",
         f"\t\t(property \"Footprint\" {esc(footprint)} (at 0 -2.54 0) {hid})",
         f"\t\t(property \"Datasheet\" {esc(datasheet)} (at 0 -5.08 0) {hid})",
         "\t\t(property \"Description\" \"\" (at 0 -7.62 0) " + hid + ")"]
    geo = []
    for k, (uname, pins) in enumerate(us, 1):
        left, right = sides(pins)
        ll, rl = pin_len(left), pin_len(right)
        wl = max([0.0] + [model.text_w(p["name"]) for p in left])
        wr = max([0.0] + [model.text_w(p["name"]) for p in right])
        w = max(5.08, math.ceil((wl + wr + 2 * NAME_OFF + 2.54) / ROW) * ROW)
        rows = max(len(left), len(right))
        h = (rows + 1) * ROW
        geo.append((uname, len(left), len(right), ll, rl, w, h))
        L.append(f"\t\t(symbol {esc(f'{name}_{k}_1')}")
        L.append(f"\t\t\t(rectangle (start 0 0) (end {w:g} {-h:g}) (stroke (width 0.254) (type default)) "
                 "(fill (type background)))")
        for i, p in enumerate(left):
            L.append(f"\t\t\t(pin {p['type']} line (at {-ll:g} {-(i + 1) * ROW:g} 0) (length {ll:g}) "
                     f"(name {esc(p['name'])} (effects (font (size 1.27 1.27)))) "
                     f"(number {esc(p['number'])} (effects (font (size 1.27 1.27)))))")
        for i, p in enumerate(right):
            L.append(f"\t\t\t(pin {p['type']} line (at {w + rl:g} {-(i + 1) * ROW:g} 180) (length {rl:g}) "
                     f"(name {esc(p['name'])} (effects (font (size 1.27 1.27)))) "
                     f"(number {esc(p['number'])} (effects (font (size 1.27 1.27)))))")
        L.append("\t\t)")
    L.append("\t)")
    return "\n".join(L) + "\n", geo


def pins_of(sym):
    """{number: (name, type, unit)} of a library symbol."""
    out = {}
    for u in sexpr.every(sym, "symbol"):
        unit = str(u[1]).rsplit("_", 2)[-2]
        for p in sexpr.every(u, "pin"):
            out[str(sexpr.atom(p, "number", ""))] = (str(sexpr.atom(p, "name", "")), str(p[1]), unit)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("table")
    ap.add_argument("--board")
    ap.add_argument("--name", required=True, help="the symbol's name in the library")
    ap.add_argument("--ref", default="U")
    ap.add_argument("--lib", help="library nickname in the board's sym-lib-table (default: the board's folder name)")
    ap.add_argument("--footprint", default="")
    ap.add_argument("--datasheet", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    board = Path(boardsel.resolve(a.board))
    rows = read_table(a.table)
    us = units(rows)
    text, geo = symbol_text(a.name, a.ref, us, a.footprint, a.datasheet)
    for uname, nl, nr, ll, rl, w, h in geo:
        print(f"  unit {uname}: {nl} left / {nr} right, pins {ll:g} / {rl:g} mm, body {w:g} x {h:g} mm")
    nick = a.lib or board.name
    path = symfields.lib_path(board, nick)
    want = {r["number"]: (r["name"], r["type"]) for r in rows}
    unit_of = {p["number"]: str(k) for k, (_u, pins) in enumerate(us, 1) for p in pins}
    old = symfields.symbols(sexpr.load(path)).get(a.name) if path.exists() else None
    if old is not None:
        have = pins_of(old)
        diff = []
        for n in sorted(set(want) | set(have), key=model.natkey if hasattr(model, "natkey") else str):
            if n not in have:
                diff.append(f"  + {n} {want[n][0]} ({want[n][1]})")
            elif n not in want:
                diff.append(f"  - {n} {have[n][0]} ({have[n][1]}) -- not in the table")
            elif have[n][:2] != want[n] or have[n][2] != unit_of[n]:
                diff.append(f"  ~ {n}: {have[n][0]} ({have[n][1]}, unit {have[n][2]}) -> "
                            f"{want[n][0]} ({want[n][1]}, unit {unit_of[n]})")
        print(f"{a.name} replaces the library's symbol of that name: {len(diff)} pin difference(s)")
        for d in diff:
            print(d)
    else:
        print(f"{a.name}: new in {path.name}")
    if a.dry_run:
        print("(dry run: nothing written)")
        return 0
    base = path.read_text() if path.exists() else "(kicad_symbol_lib (version 20241209) (generator \"symgen\")\n)\n"
    tree = sexpr.parse(base)
    tree[:] = [c for c in tree if not (sexpr.is_node(c, "symbol") and str(c[1]) == a.name)]
    body = sexpr.dump(tree).rstrip()
    assert body.endswith(")")
    new = body[:-1].rstrip() + "\n" + text + ")\n"
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.kicad_sym"
        src.write_text(new)
        B = symfields.resave(src, Path(td) / "out.kicad_sym")
        sym = symfields.symbols(B).get(a.name)
        if sym is None:
            die("kicad-cli's resave lost the symbol -- nothing written")
        got = pins_of(sym)
        bad = [n for n in want if got.get(n, (None, None, None))[:2] != want[n] or got[n][2] != unit_of[n]]
        bad += [n for n in got if n not in want]
        if bad:
            die(f"read back after kicad-cli's resave: pins {bad[:10]} differ from the table -- nothing written")
        if path.exists():
            bak = board / ".sch_backups" / f"symgen-{datetime.datetime.now():%Y%m%d-%H%M%S}"
            bak.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, bak / path.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(td) / "out.kicad_sym", path)
    print(f"wrote {a.name} into {path} ({len(rows)} pins, {len(us)} unit(s)); read back after kicad-cli's resave: "
          "every pin as the table says")
    return 0


if __name__ == "__main__":
    sys.exit(main())
