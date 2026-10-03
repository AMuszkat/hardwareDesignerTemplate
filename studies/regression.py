"""
Recompute the headline numbers and diff them against a committed baseline.

    python studies/run_all.py --check            # compare
    python studies/run_all.py --check --bless    # accept the current values

Sits beside `tools/lint_docs.py` in intent: that one keeps the DOCUMENTS
honest, this one keeps the NUMBERS honest.

A study takes part by defining `headline()` -> {key: number}.  Every key a
study returns is one `design/SPECS.md` quotes; if a value here moves, a spec
row has to move with it -- that is the point.  Prefix keys with the study's
own tag and end them with the unit (`rc.fc_hz`): the suffix is for the reader;
the tolerance is relative (`baseline.REL_DEFAULT`).
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import baseline                                                # noqa: E402
import run_all                                                 # noqa: E402


def main():
    vals = {}
    for name in run_all.studies():
        mod = importlib.import_module(name)
        if not hasattr(mod, "headline"):
            continue
        got = mod.headline()
        dup = sorted(set(got) & set(vals))
        if dup:
            raise SystemExit(f"{name}: headline keys already produced by "
                             f"another study: {', '.join(dup)}")
        vals.update(got)
        for k in sorted(got):
            print(f"  {k:40s} {baseline._fmt(float(got[k]) if isinstance(got[k], (int, float)) and not isinstance(got[k], bool) else got[k])}", flush=True)
    return vals


def run(bless=False):
    """Returns (ok, n_checked).  Prints every drift."""
    vals = main()
    if not vals:
        print("no study defines headline() yet -- nothing to check")
        return True, 0
    n, probs = baseline.check("headline", vals, bless=bless)
    print()
    if bless:
        print(f"BLESSED {n} headline numbers into studies/baselines/headline.json")
        return True, n
    for p in probs:
        print("DRIFT:", p)
    if probs:
        print(f"\n{len(probs)} drift(s). Either the model moved and "
              f"design/SPECS.md must follow, or something broke. "
              f"--bless only after deciding which.")
        return False, n
    print(f"regression OK -- {n} headline numbers match the baseline")
    return True, n


if __name__ == "__main__":
    ok, _ = run(bless="--bless" in sys.argv)
    sys.exit(0 if ok else 1)
