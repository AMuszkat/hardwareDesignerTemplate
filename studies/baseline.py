"""
Numeric regression for the model's headline numbers.

WHY THIS EXISTS.  Every number in `design/SPECS.md` is supposed to be
reproducible by running the model.  Without a check, a published headline and
the model that produced it drift apart silently, and nothing notices until
somebody re-derives the figure by hand.  This is `REFLECTS` for numbers: the
diagrams have a staleness check, and so do the numbers.

Make the model deterministic (explicit seeds) so tolerances can be tight: a
drift is then a real change, not noise -- either the model moved and the docs
must follow, or the model broke.

    python studies/run_all.py --check          # compare against the baselines
    python studies/run_all.py --check --bless  # accept current values

`--bless` is a claim that somebody looked at the deltas and decided they are
correct.  Blessing without looking is the one failure this cannot catch.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "baselines")

# The tolerance is RELATIVE: a headline may move by this fraction of its
# baseline value before it counts as a drift.  The model is deterministic, so
# the allowance is only for library-version noise in the last digits -- never
# for measurement spread.  Relative, because an absolute allowance sized for
# volts lets a 60 uA figure double unnoticed.
REL_DEFAULT = 1e-6
# Exceptions, by exact key or by key suffix -> relative tolerance.  A number
# that legitimately wobbles more (an iterative solver, a fitted value) gets a
# line here with the reason, rather than a looser default for everything.
REL = {
    # "loop.phase_margin_deg": 1e-3,   # fsolve tolerance
}
ABS_FLOOR = 1e-12        # two values this close are equal (a baseline of exactly 0)


def _tol(key, base):
    """Absolute allowance for one key at its baseline value."""
    rel = REL.get(key)
    if rel is None:
        rel = next((t for suf, t in REL.items() if key.endswith(suf)), REL_DEFAULT)
    return max(abs(base) * rel, ABS_FLOOR)


def _fmt(v):
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def check(name, values, bless=False):
    """Compare `values` against baselines/<name>.json.

    Returns (n_checked, [drift strings]).  Unknown keys are reported as NEW
    rather than silently accepted -- a renamed metric must be a deliberate
    bless, not an accident.
    """
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, f"{name}.json")
    # numbers as floats; None ("no value at this design point") and text (a
    # chosen package, a verdict) are compared for equality
    values = {k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else
                  (v if v is None else str(v))) for k, v in values.items()}

    if bless:
        with open(path, "w") as f:
            json.dump(values, f, indent=2, sort_keys=True)
            f.write("\n")
        return len(values), []

    if not os.path.exists(path):
        return 0, [f"{name}: NO BASELINE -- run with --bless once, having "
                   f"checked the values are right"]

    with open(path) as f:
        base = json.load(f)
    probs = []
    for k, v in sorted(values.items()):
        if k not in base:
            probs.append(f"{name}.{k}: NEW ({_fmt(v)}) -- not in the baseline")
            continue
        b = base[k]
        if not isinstance(b, float) or not isinstance(v, float):
            if b != v:
                probs.append(f"{name}.{k}: {_fmt(b)} -> {_fmt(v)}")
            continue
        t = _tol(k, b)
        if abs(v - b) > t:
            probs.append(f"{name}.{k}: {b:.6g} -> {v:.6g} "
                         f"(drift {v - b:+.3g}, {abs(v - b) / abs(b) if b else float('inf'):.2g} "
                         f"of the value; allowed {t / abs(b) if b else 0:.0e})")
    for k in sorted(base):
        if k not in values:
            probs.append(f"{name}.{k}: GONE -- the baseline has it, this run "
                         f"did not produce it")
    return len(values), probs
