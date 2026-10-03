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
