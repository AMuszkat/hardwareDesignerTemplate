"""
The design point -- one place, every MODEL parameter, each with the study or
source that set it.

WHY THIS EXISTS.  A parameter with a safe-looking default can silently decide
a headline number: a study that builds its own `dict(...)` of settings
inherits whatever it forgot, and the number it prints still looks plausible.
This module is the fix.

Rules:
  * `PARAMS` lists EVERY parameter the model accepts.  `kw()` raises on a name
    that is not here, so a typo or a new knob cannot silently apply a default.
  * Every row says whether the design point SETS the value or deliberately
    ACCEPTS a default, and which study or source decided it.
  * Studies perturb the point with `kw(name=value)`; they never copy it.
  * A design choice lives here only until it becomes a part on a board.  Then
    it moves to that board's value bus (`studies/components_boardN.py`, which
    the handoff publishes), this row is deleted, and studies read it back with
    `common.part(board, ref)` -- one value, one home.
  * A row that restates a SPECS value (an input range, an ambient) cites the
    SPECS section; change both in the same pass.
  * Written by the concept session only.  A change here that moves a headline
    number owes a `run_all.py --check` and, if intended, a `--bless`.

    PARAMS[name] = (value, unit, "set" | "default", "source / study")
"""

PARAMS = {
    # ===== EXAMPLE rows for the three example studies -- replace them =====
    # Operating conditions restate a SPECS row: cite it, and move both in the
    # same pass (the `decision` skill).  Datasheet constants cite a page.
    #
    # ---- s01_rc_corner: RC low-pass in front of an ADC --------------------
    "rc_fc_target_hz": (1.6e3, "Hz", "set", "SPECS 1 -- anti-alias corner"),
    "rc_c_f":          (10e-9, "F", "set", "s01 -- largest C0G stocked in 0402"),
    "rc_r_series":     ("E96", "", "set", "s01 -- 1 % resistors"),
    # ---- s02_divider_tolerance: a regulator's feedback divider ------------
    "vout_target_v":   (3.3, "V", "set", "SPECS 2 -- 3V3 rail"),
    "vout_tol_pct":    (3.0, "%", "set", "SPECS 2 (H) -- 3V3 rail accuracy"),
    "vref_v":          (0.8, "V", "set", "regulator datasheet p.? (EXAMPLE)"),
    "vref_tol":        (0.01, "", "set", "regulator datasheet p.? -- over temp (EXAMPLE)"),
    "div_r2_range_ohm": ((4.99e3, 20e3), "ohm", "set", "s02 -- divider current 40-160 uA"),
    "div_series":      ("E96", "", "set", "s02 -- 1 % and better come in E96"),
    "div_tol_classes": ((0.01, 0.005, 0.001), "", "set", "s02 -- the classes compared"),
    "hardline_margin_pct": (0.25, "%", "set", "s02 -- project rule: a hard line is met "
                            "with margin, not on paper (EXAMPLE policy)"),
    "mc_samples":      (20000, "", "set", "s02 -- yield resolution 0.005 %"),
    "mc_seed":         (1, "", "set", "s02 -- fixed so --check is deterministic"),
    # ---- s03_ldo_thermal: junction temperature budget ---------------------
    "ldo_vin_max_v":   (5.5, "V", "set", "SPECS 2 -- input range 4.5-5.5 V"),
    "ldo_vout_v":      (3.3, "V", "set", "SPECS 2 -- 3V3 rail"),
    "ldo_iout_max_a":  (0.20, "A", "set", "SPECS 2 -- 3V3 load, worst case"),
    "ldo_iq_a":        (60e-6, "A", "set", "LDO datasheet p.? (EXAMPLE)"),
    "ta_max_degc":     (70.0, "degC", "set", "SPECS 3 -- ambient"),
    "tj_limit_degc":   (105.0, "degC", "set", "SPECS 4 (H) -- derated from 125"),
    "theta_ja":        ({"SOT-23-5": 180.0, "DFN-6 2x2": 70.0, "SOT-223": 60.0},
                        "degC/W", "set", "LDO datasheet thermal table p.? (EXAMPLE values)"),
}


def get(name):
    """The design-point value of one parameter (KeyError if unknown)."""
    return PARAMS[name][0]


def kw(**over):
    """Design-point values as a dict, with `over` applied.

    Raises KeyError for an override that is not a known parameter -- the
    whole point of the module.
    """
    unknown = sorted(set(over) - set(PARAMS))
    if unknown:
        raise KeyError(f"not in designpoint.PARAMS: {', '.join(unknown)}")
    d = {k: v[0] for k, v in PARAMS.items()}
    d.update(over)
    return d


def report():
    """Print the design point, one row per parameter."""
    w = max(len(k) for k in PARAMS)
    for k, (v, unit, how, src) in PARAMS.items():
        print(f"  {k:<{w}}  {str(v):>34.34} {unit:<7} {how:<8} {src}")


if __name__ == "__main__":
    report()
