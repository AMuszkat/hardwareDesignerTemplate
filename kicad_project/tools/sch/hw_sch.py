#!/usr/bin/env python3
"""Writes tools/sch/hw_sch.kicad_sym -- never edit that file by hand.

Power glyphs pointing left, right, up and down, positive (an arrow) and
ground. Each is a KiCad power symbol: its Value is the net. They exist
because a stock power symbol turned sideways draws its name vertically, and
Konnect cannot turn a field: on a dense IC side the names collide. These keep
the name horizontal whichever way the glyph points, so a rail reads like a
label in its pin's lane, and they are ordinary library symbols, so a sheet's
power glyphs are placed in one batch call.

    python3 tools/sch/hw_sch.py
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent / "hw_sch.kicad_sym"
F = "(effects (font (size 1.27 1.27)){j})"


def poly(pts):
    return ("\t\t\t(polyline (pts " + " ".join(f"(xy {x:g} {y:g})" for x, y in pts) +
            ") (stroke (width 0) (type default)) (fill (type none)))\n")


def sym(name, kind, desc, shapes, vx, vy, just):
    j = f" (justify {just})" if just else ""
    s = f'\t(symbol "{name}"\n\t\t(power)\n\t\t(pin_numbers (hide yes))\n\t\t(pin_names (offset 0) (hide yes))\n'
    s += '\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n'
    s += f'\t\t(property "Reference" "#PWR" (at 0 0 0) (hide yes) {F.format(j="")})\n'
    s += f'\t\t(property "Value" "{kind}" (at {vx:g} {vy:g} 0) {F.format(j=j)})\n'
    s += f'\t\t(property "Footprint" "" (at 0 0 0) (hide yes) {F.format(j="")})\n'
    s += f'\t\t(property "Datasheet" "" (at 0 0 0) (hide yes) {F.format(j="")})\n'
    s += f'\t\t(property "Description" "{desc}" (at 0 0 0) (hide yes) {F.format(j="")})\n'
    s += f'\t\t(symbol "{name}_0_1"\n' + "".join(poly(p) for p in shapes) + "\t\t)\n"
    s += (f'\t\t(symbol "{name}_1_1"\n\t\t\t(pin power_in line (at 0 0 0) (length 0)'
          f' (name "" {F.format(j="")}) (number "1" {F.format(j="")}))\n\t\t)\n\t)\n')
    return s


def turn(pts, d):
    """Points drawn pointing up (+y, library coordinates) -> pointing d."""
    f = {"U": lambda x, y: (x, y), "D": lambda x, y: (x, -y),
         "L": lambda x, y: (-y, x), "R": lambda x, y: (y, x)}[d]
    return [f(x, y) for x, y in pts]


ARROW = [[(0, 0), (0, 2.54)], [(-0.762, 1.27), (0, 2.54), (0.762, 1.27)]]
GROUND = [[(0, 0), (0, 1.27), (1.27, 1.27), (0, 2.54), (-1.27, 1.27), (0, 1.27)]]
VALUE = {"U": (0, 3.302, "bottom"), "D": (0, -3.302, "top"), "L": (-3.302, 0, "right"), "R": (3.302, 0, "left")}

if __name__ == "__main__":
    out = '(kicad_symbol_lib\n\t(version 20231120)\n\t(generator "sch.py")\n'
    for d in "LRUD":
        vx, vy, j = VALUE[d]
        out += sym(f"PWR_{d}", "VDD", f"A power net, its name horizontal, the glyph pointing {d}; the value is the net",
                   [turn(p, d) for p in ARROW], vx, vy, j)
        out += sym(f"GND_{d}", "GND", f"A ground net, its name horizontal, the glyph pointing {d}; the value is the net",
                   [turn(p, d) for p in GROUND], vx, vy, j)
    OUT.write_text(out + ")\n")
    print(f"wrote {OUT}")
