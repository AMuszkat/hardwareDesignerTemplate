"""
Study 1 -- EXAMPLE (derive a value): the series resistor of the ADC input's
RC low-pass.

Question  Which standard resistor puts the anti-alias corner closest to the
          SPECS target, with the C0G capacitor already chosen?
Decides   R's value in the board's value bus (`proposed` until reviewed).
Inputs    designpoint: rc_fc_target_hz, rc_c_f, rc_r_series
Headline  rc.r_ohm, rc.fc_hz, rc.fc_err_pct            -> SPECS section 1

Pattern: a DERIVATION.  Compute the ideal, snap it to what can be bought,
and report what the snapped value actually gives -- the number that goes
into SPECS is the ACHIEVED one, never the target.
"""
import math

import designpoint as DP
from common import nearest


def corner_hz(r, c):
    return 1.0 / (2 * math.pi * r * c)


def design():
    k = DP.kw()
    r_ideal = 1.0 / (2 * math.pi * k["rc_fc_target_hz"] * k["rc_c_f"])
    r = nearest(r_ideal, k["rc_r_series"])
    fc = corner_hz(r, k["rc_c_f"])
    return r_ideal, r, fc, 100.0 * (fc / k["rc_fc_target_hz"] - 1)


def headline():
    _, r, fc, err = design()
    return {"rc.r_ohm": r, "rc.fc_hz": fc, "rc.fc_err_pct": err}


def main():
    k = DP.kw()
    r_ideal, r, fc, err = design()
    print(f"target corner {k['rc_fc_target_hz']:.0f} Hz with C = {k['rc_c_f'] * 1e9:.1f} nF")
    print(f"  ideal R      {r_ideal:9.1f} ohm")
    print(f"  {k['rc_r_series']} R        {r:9.1f} ohm")
    print(f"  corner       {fc:9.1f} Hz  ({err:+.2f} % from target)")
    print(f"-> R = {r / 1e3:.3g} k ({k['rc_r_series']}): goes into the value bus as `proposed`, "
          f"note 's01_rc_corner'; SPECS 1 quotes {fc:.0f} Hz.")


if __name__ == "__main__":
    main()
