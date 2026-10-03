"""
Study 3 -- EXAMPLE (budget against a hard line): which LDO package keeps the
junction under its derated limit at the worst-case corner?

Question  Worst-case dissipation of the 3V3 LDO, and the junction temperature
          it produces in each candidate package.
Decides   the package (the smallest that passes) -> the part's MPN in the value
          bus; and a LAYOUT rule for the handoff (the copper the package's
          theta-JA assumes).
Inputs    designpoint: ldo_vin_max_v, ldo_vout_v, ldo_iout_max_a, ldo_iq_a,
          ta_max_degc, tj_limit_degc, theta_ja
Headline  ldo.p_worst_w, ldo.tj_<package>_degc           -> SPECS section 4

Patterns:
  * A BUDGET IS EVALUATED AT THE CORNER, not the typical point: maximum input,
    maximum load, maximum ambient, all at once.
  * EVERY CONSTANT CITES ITS PAGE.  theta-JA is the datasheet's, measured on
    the datasheet's test board; the result is only true if our board gives the
    package that copper -- which is why this study ends in a handoff layout
    rule, not only in a part choice.
  * SHOW THE HEADROOM, not just pass/fail: the current each package could
    carry tells the next study how much margin a load increase has.
"""
import re

import designpoint as DP


def p_worst(k):
    return ((k["ldo_vin_max_v"] - k["ldo_vout_v"]) * k["ldo_iout_max_a"]
            + k["ldo_vin_max_v"] * k["ldo_iq_a"])


def i_max(k, theta):
    """Load current at which the junction just reaches the limit."""
    p_allowed = (k["tj_limit_degc"] - k["ta_max_degc"]) / theta
    return max(0.0, (p_allowed - k["ldo_vin_max_v"] * k["ldo_iq_a"])
               / (k["ldo_vin_max_v"] - k["ldo_vout_v"]))


def key(pkg):
    return re.sub(r"[^a-z0-9]+", "", pkg.lower().replace("-", ""))


def budget():
    k = DP.kw()
    p = p_worst(k)
    rows = [(pkg, th, k["ta_max_degc"] + p * th, i_max(k, th))
            for pkg, th in sorted(k["theta_ja"].items(), key=lambda kv: -kv[1])]
    return k, p, rows


def headline():
    _, p, rows = budget()
    h = {"ldo.p_worst_w": p}
    h.update({f"ldo.tj_{key(pkg)}_degc": tj for pkg, _, tj, _ in rows})
    return h


def main():
    k, p, rows = budget()
    print(f"corner: VIN {k['ldo_vin_max_v']} V, IOUT {k['ldo_iout_max_a']} A, "
          f"TA {k['ta_max_degc']:g} degC -> P = {p:.3f} W")
    print(f"limit:  TJ <= {k['tj_limit_degc']:g} degC (SPECS 4, H)\n")
    print(f"  {'package':<10} {'theta-JA':>9} {'TJ':>8} {'margin':>8} {'I at limit':>11}")
    for pkg, th, tj, im in rows:
        print(f"  {pkg:<10} {th:>6.0f} C/W {tj:>6.1f} C {k['tj_limit_degc'] - tj:>+6.1f} C "
              f"{im * 1e3:>8.0f} mA  {'PASS' if tj <= k['tj_limit_degc'] else 'FAIL'}")
    ok = [r for r in rows if r[2] <= k["tj_limit_degc"]]
    if ok:
        pkg, th, tj, _ = ok[0]
        print(f"\n-> {pkg}: the highest theta-JA (smallest package) that passes, "
              f"{k['tj_limit_degc'] - tj:.1f} degC margin. Two handoff entries follow:")
        print(f"   value bus: the LDO's MPN in {pkg}, note 's03_ldo_thermal';")
        print(f"   10_layout.md: 'the LDO's thermal pad gets the copper area of the datasheet's "
              f"theta-JA test board ({th:.0f} C/W assumes it) -- source s03_ldo_thermal'.")
    else:
        print("\n-> no candidate passes: a negative result, and a decision for the user.")


if __name__ == "__main__":
    main()
