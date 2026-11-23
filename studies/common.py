"""
Helpers every study may use.  Small on purpose: anything specific to one
study stays in that study.

    from common import OUT, out, plt, nearest, series, part

  out(name)            path in out/ (created); plots and text dumps go there
  plt()                matplotlib.pyplot on the Agg backend (no display needed)
  series("E96")        the standard values of one decade, 1.00 ... 9.76
  nearest(x, "E96")    the standard value closest to x (log distance)
  part(board, ref)     a part's NUMBERS from that board's value bus
"""
import importlib
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "out")


def out(name):
    """Path for an output file in out/ (disposable, gitignored)."""
    os.makedirs(OUT, exist_ok=True)
    return os.path.join(OUT, name)


def plt():
    """matplotlib.pyplot with the Agg backend: studies run headless."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as p
    return p


# ---------------------------------------------------------------------------
# IEC 60063 preferred numbers.  E3-E24 are the historical lists (they do not
# follow the formula); E48/E96/E192 are 10^(i/n) rounded to 3 significant
# figures, with E192's one historical exception (9.20, not 9.19).
_E = {
    "E3": [1.0, 2.2, 4.7],
    "E6": [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
    "E12": [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
    "E24": [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
            3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1],
}


def series(name="E96"):
    """The standard mantissas of one decade for an E-series."""
    if name in _E:
        return list(_E[name])
    n = int(name[1:])
    if n not in (48, 96, 192):
        raise ValueError(f"unknown series {name!r}")
    vals = [round(10 ** (i / n), 2) for i in range(n)]
    if n == 192:
        vals = [9.20 if abs(v - 9.19) < 1e-9 else v for v in vals]
    return vals


def nearest(x, name="E96"):
    """The standard value closest to `x` in log distance (any decade)."""
    if x <= 0:
        raise ValueError("standard values are positive")
    dec = math.floor(math.log10(x))
    best = None
    for d in (dec - 1, dec, dec + 1):
        for m in series(name):
            v = m * 10 ** d
            err = abs(math.log(v / x))
            if best is None or err < best[0]:
                best = (err, v)
    return float(f"{best[1]:.6g}")


# ---------------------------------------------------------------------------
def part(board, ref):
    """The numbers of one part as the board's value bus records them.

    A study that VERIFIES a board reads its parts from the bus -- never a copy
    of its own -- so it checks the design as it is drawn.  The bus row carries
    the numbers in an optional sixth field (publish.py renders only the first
    five):

        ("R1", "VOUT divider, top", "<MPN> -- 35.7 k 0.5 % 0402",
         "set", "s02_divider_tolerance: worst case 2.24 %",
         {"value": 35.7e3, "tol": 0.005})

    Returns that dict; raises if the board, the designator or the field is
    missing, so a verification can never silently fall back to a guess.
    """
    try:
        bus = importlib.import_module(f"components_{board}")
    except ImportError as e:
        raise LookupError(f"no value bus studies/components_{board}.py") from e
    for row in bus.COMPONENTS:
        if ref in _refs(row[0]):
            if len(row) < 6 or not isinstance(row[5], dict):
                raise LookupError(f"components_{board}: {row[0]} has no numeric "
                                  f"field -- add {{'value': ..., 'tol': ...}} as its "
                                  f"sixth element")
            return dict(row[5])
    raise LookupError(f"components_{board}: no line for {ref}")


def _refs(cell):
    """'R1..R4, R7' -> {'R1', 'R2', 'R3', 'R4', 'R7'}."""
    out_ = set()
    for tok in (t.strip() for t in cell.split(",")):
        m = re.fullmatch(r"([A-Z]+)(\d+)\.\.(?:[A-Z]+)?(\d+)", tok)
        if m:
            out_ |= {f"{m.group(1)}{i}" for i in range(int(m.group(2)), int(m.group(3)) + 1)}
        elif tok:
            out_.add(tok)
    return out_


def via_barrel(drill_mm, wall_mm, length_mm, rho20_ohm_mm, tcr_per_k=0.0, hot_c=20.0, k_w_mmk=None):
    """A plated via barrel carrying current along its length.
    -> dict(area mm^2, r ohm at hot_c, rise K per A^2 or None).

    area   the barrel's mean circumference times its wall, pi (d + t) t
    r      rho(hot_c) L / area, with rho = rho20 (1 + tcr (hot_c - 20))
    rise   the hottest point's rise above the copper the barrel ENDS in, for
           1 A: a uniformly heated rod cooled through ONE end only, the other
           end and the laminate round it taken as insulators (an upper bound:
           a real barrel is sunk at every plane it joins, and the current
           leaves at an inner layer).  dT = I^2 rho L^2 / (2 k area^2).

    Why not IPC-2221's trace fit: that curve is a long conductor in laminate
    with no copper to sink into; a barrel is about a millimetre long between
    planes.  Judge a via by its drop and this rise, a planar neck by the fit.
    """
    import math
    area = math.pi * (drill_mm + wall_mm) * wall_mm
    rho = rho20_ohm_mm * (1 + tcr_per_k * (hot_c - 20.0))
    r = rho * length_mm / area
    rise = rho * length_mm ** 2 / (2 * k_w_mmk * area ** 2) if k_w_mmk else None
    return dict(area=area, r=r, rise=rise)


def ipc2221_a(area_mm2, rise_k=10.0, internal=True):
    """IPC-2221's current fit for a planar conductor: I = k dT^0.44 A^0.725, A in mil^2,
    k 0.024 internal / 0.048 external."""
    return (0.024 if internal else 0.048) * rise_k ** 0.44 * (area_mm2 / 0.00064516) ** 0.725



def plane_strip_nh(h_mm, d_mm, w_mm):
    """The inductance a current adds by crossing d mm of a pour w mm wide over its return plane
    h mm away: a parallel-plate strip, L = mu0 h d / w, in nH.  w is the NARROWEST width on the
    way, so the figure is an upper bound for a wider pour; fringing (w not >> h) only lowers it."""
    import math
    return 4e-7 * math.pi * (h_mm * 1e-3) * (d_mm * 1e-3) / (w_mm * 1e-3) * 1e9


def plane_ring_nh(h_mm, r_in_mm, r_out_mm, open_frac=1.0):
    """The inductance of the ring of pour round a via: current spreading from the via's pad
    (radius r_in) out to radius r_out between the pour and its return plane h mm away,
    L = mu0 h ln(r_out / r_in) / (2 pi open_frac), in nH.  open_frac is the share of the ring
    that is copper (1 = whole, 0.5 = a via at a pour's edge).  It is what a min-cut measure of a
    pour reads as a 'neck' at a single feed via -- the ring can never be wider than the pad is
    round -- and what plane_strip_nh's w leaves out when w is wider than the via."""
    import math
    return 4e-7 * math.pi * (h_mm * 1e-3) * math.log(r_out_mm / r_in_mm) / (2 * math.pi * open_frac) * 1e9


def mounted_srf_hz(l_nh, c_uf):
    """The frequency up to which a capacitor of c_uf holds its rail through a mounting loop of
    l_nh: 1 / (2 pi sqrt(L C)).  Above it the smaller capacitors at the pins and the planes carry
    the rail, so a bulk capacitor's distance is costed AT this frequency (s16, s23)."""
    import math
    return 1 / (2 * math.pi * math.sqrt(l_nh * 1e-9 * c_uf * 1e-6))


def bulk_out_cost(h_mm, d_mm, w_mm, via_loop_nh, c_uf, stay=0):
    """A bulk capacitor moved d mm from its pins along its rail's pour, costed at its mounted
    resonance.  `stay` capacitors of the same value remain at the pins (each on via_loop_nh).
    -> dict(add_nh: the strip's inductance; srf_hz; bank_add_nh: what the bank of 1 + stay gains
    against all of them at the pins; mohm: that at srf_hz = mV for each ampere of load step)."""
    import math
    add = plane_strip_nh(h_mm, d_mm, w_mm)
    srf = mounted_srf_hz(via_loop_nh, c_uf)
    n = 1 + stay
    moved = 1 / (1 / (via_loop_nh + add) + stay / via_loop_nh)        # nH, the bank with one out
    bank_add = moved - via_loop_nh / n
    return dict(add_nh=add, srf_hz=srf, bank_add_nh=bank_add, mohm=2 * math.pi * srf * bank_add * 1e-9 * 1e3)
