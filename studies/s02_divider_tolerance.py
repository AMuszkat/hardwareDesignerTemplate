"""
Study 2 -- EXAMPLE (prove robustness): does the 3V3 rail meet its accuracy
hard line with 1 % resistors, or does it need better ones?

Question  Worst-case and statistical accuracy of VOUT = VREF (1 + R1/R2) for
          each resistor tolerance class, against SPECS' +-3 % (H).
Decides   R1/R2 values and their tolerance class (the cheapest that passes)
          -> the board's value bus.
Inputs    designpoint: vout_target_v, vout_tol_pct, vref_v, vref_tol,
          div_r2_range_ohm, div_series, div_tol_classes, hardline_margin_pct,
          mc_samples, mc_seed
Headline  divider.r1_ohm, divider.r2_ohm, divider.vout_nom_v, and per class
          divider.worst_tol<X>_pct, divider.yield_tol<X>_pct -> SPECS section 2

Patterns:
  * SNAP FIRST, THEN SPEND THE BUDGET.  Standard values put the nominal off
    target before any tolerance.  Here 3.3 V from 0.8 V needs R1/R2 = 3.125,
    which sits halfway between two E96 ratios: even the best of ~60 pairs is
    0.5 % low, and that half percent is spent before a single part drifts.
  * A HARD LINE IS MET WITH MARGIN.  A class that passes by a few hundredths
    of a percent passes on paper only; the project's margin rule
    (hardline_margin_pct) decides, and the study says which classes it cost.
  * WORST CASE AND STATISTICS TOGETHER.  Corners bound what CAN happen; Monte
    Carlo says how OFTEN.  Against a hard line the worst case decides -- a 100 %
    yield does not excuse a corner that crosses it.
  * PARALLEL AND DETERMINISTIC.  par.pmap runs one job per class; each draws
    from its own seeded generator, so the numbers are identical serial or
    parallel, run after run -- which is what lets --check guard them.
  * A PLOT IN out/ (disposable); the numbers go to SPECS and the baseline.

Model: each part lies within +-tol, drawn normal with 3 sigma = tol and
truncated at +-tol (a datasheet tolerance is a limit, not a sigma).
"""
import numpy as np

import designpoint as DP
import par
from common import nearest, out, plt, series


def pick_pair(k):
    """The standard R1/R2 pair whose NOMINAL output is closest to target."""
    lo, hi = k["div_r2_range_ohm"]
    ratio = k["vout_target_v"] / k["vref_v"] - 1
    best = None
    for dec in (1e3, 1e4, 1e5):
        for m in series(k["div_series"]):
            r2 = m * dec
            if not lo <= r2 <= hi:
                continue
            r1 = nearest(r2 * ratio, k["div_series"])
            v = k["vref_v"] * (1 + r1 / r2)
            if best is None or abs(v - k["vout_target_v"]) < abs(best[2] - k["vout_target_v"]):
                best = (r1, r2, v)
    return best


def worst_pct(k, r1, r2, tol):
    """Largest |VOUT/target - 1| over every tolerance corner, in %."""
    vt = k["vout_target_v"]
    worst = 0.0
    for sv in (-1, 1):
        for s1 in (-1, 1):
            for s2 in (-1, 1):
                v = k["vref_v"] * (1 + sv * k["vref_tol"]) * \
                    (1 + r1 * (1 + s1 * tol) / (r2 * (1 + s2 * tol)))
                worst = max(worst, abs(v / vt - 1))
    return 100.0 * worst


def _draw(rng, n, tol):
    return np.clip(rng.normal(0.0, tol / 3, n), -tol, tol)


def _mc(job):
    """One tolerance class: (tol, index) -> sampled VOUT array.  Module level,
    so par.pmap can fork it; its own seed, so the order of jobs is irrelevant."""
    tol, idx = job
    k = DP.kw()
    r1, r2, _ = pick_pair(k)
    rng = np.random.default_rng([k["mc_seed"], idx])
    n = k["mc_samples"]
    vref = k["vref_v"] * (1 + _draw(rng, n, k["vref_tol"]))
    return vref * (1 + r1 * (1 + _draw(rng, n, tol)) / (r2 * (1 + _draw(rng, n, tol))))


def tag(tol):
    return "tol" + f"{tol * 100:g}".replace(".", "p")


def analyse():
    k = DP.kw()
    r1, r2, vnom = pick_pair(k)
    classes = k["div_tol_classes"]
    samples = par.pmap(_mc, [(t, i) for i, t in enumerate(classes)], note="Monte Carlo")
    lim = k["vout_tol_pct"] / 100 * k["vout_target_v"]
    rows = []
    for tol, v in zip(classes, samples):
        ok = np.abs(v - k["vout_target_v"]) <= lim
        rows.append((tol, worst_pct(k, r1, r2, tol), 100.0 * ok.mean(), v))
    return k, r1, r2, vnom, rows


def headline():
    k, r1, r2, vnom, rows = analyse()
    h = {"divider.r1_ohm": r1, "divider.r2_ohm": r2, "divider.vout_nom_v": vnom}
    for tol, worst, yld, _ in rows:
        h[f"divider.worst_{tag(tol)}_pct"] = worst
        h[f"divider.yield_{tag(tol)}_pct"] = yld
    return h


def main():
    k, r1, r2, vnom, rows = analyse()
    spec = k["vout_tol_pct"]
    print(f"pair: R1 = {r1:g} ohm, R2 = {r2:g} ohm ({k['div_series']}) -> nominal "
          f"{vnom:.4f} V ({100 * (vnom / k['vout_target_v'] - 1):+.2f} % before any tolerance)")
    print(f"VREF {k['vref_v']} V +-{k['vref_tol'] * 100:g} %; hard line +-{spec:g} %; "
          f"{k['mc_samples']} samples per class\n")
    margin = k["hardline_margin_pct"]
    print(f"  {'resistors':>9}  {'worst case':>10}  {'MC yield':>9}  verdict "
          f"(hard line {spec:g} %, margin rule {margin:g} %)")
    for tol, worst, yld, _ in rows:
        verdict = ("PASS" if worst <= spec - margin else
                   f"on paper only: {spec - worst:.2f} % to the line" if worst <= spec else
                   "FAIL: the worst case crosses the hard line")
        print(f"  {tol * 100:>8g}%  {worst:>9.2f}%  {yld:>8.2f}%  {verdict}")
    passing = [r for r in rows if r[1] <= spec - margin]
    if passing:
        tol = max(r[0] for r in passing)
        print(f"\n-> {tol * 100:g} % resistors: the cheapest class that holds +-{spec:g} % at "
              f"every corner with the {margin:g} % margin. R1/R2 go into the value bus as "
              f"`proposed`, note 's02_divider_tolerance'.")
        thin = [r for r in rows if spec - margin < r[1] <= spec]
        if thin:
            print(f"   ({', '.join(f'{r[0] * 100:g} %' for r in thin)} passes on paper only: the "
                  f"{k['div_series']} grid cost {abs(100 * (vnom / k['vout_target_v'] - 1)):.2f} % "
                  f"before any tolerance. A tighter VREF, or a ratio off the {k['div_series']} grid, "
                  f"would bring it back.)")
    else:
        print("\n-> no class passes: this is a NEGATIVE RESULT for HISTORY, and a "
              "decision for the user (a tighter VREF, a trimmed divider, or a wider spec).")

    p = plt()
    fig, ax = p.subplots(figsize=(7, 3.6))
    for tol, _, _, v in rows:
        ax.hist(v, bins=120, histtype="step", label=f"{tol * 100:g} % resistors")
    for sgn in (-1, 1):
        ax.axvline(k["vout_target_v"] * (1 + sgn * spec / 100), color="k", ls="--", lw=1)
    ax.axvline(k["vout_target_v"], color="0.5", ls=":", lw=1, label="target")
    ax.set_xlabel("VOUT (V)")
    ax.set_ylabel("samples")
    ax.set_title("3V3 rail accuracy by resistor class (dashed: hard line)")
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(out("s02_divider_tolerance.png"), dpi=120)
    print(f"plot: out/s02_divider_tolerance.png")


if __name__ == "__main__":
    main()
