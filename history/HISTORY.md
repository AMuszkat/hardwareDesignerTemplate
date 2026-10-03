# HISTORY.md — the research log

**Append-only.** Every study, every negative result, every superseded
architecture, every decision and every correction to an earlier decision, in
the order they happened. Nothing here is current — `design/` is the authority
on what is true now; this file is the authority on what was tried and found.
Nothing here is ever edited or deleted afterwards.

**Read this when** you need to know *why* a number or decision is what it is,
or before re-deriving something that may already have been done.

**Entry headings** (newest at the bottom):

    ### Study N — <title>                    (studies/sNN_<name>.py)
    ### Decision D<n>, YYYY-MM-DD — <title>  (and its STATUS Decided row)
    ### Review YYYY-MM-DD — <title>          (history/reviews/<file>)
    ### Correction YYYY-MM-DD — <what was wrong, and since when>

Each entry: what was asked, what was done, the numbers with their sources,
what it means, and what it rules out. A negative result is written up as
carefully as a positive one — it is the entry that saves the next week.

---

### Study 1 — EXAMPLE: series resistor of the ADC input filter

*(Example entry shipped with the template — `./setup.sh clean-examples` removes it.)*
`studies/s01_rc_corner.py`.

**Asked:** which E96 resistor puts the anti-alias corner nearest the 1.6 kHz
target (SPECS 1) with the 10 nF C0G already chosen?

**Found:** the ideal is 9947 Ω; the nearest E96 value, 10.0 kΩ, gives 1591.5 Hz
(−0.53 %).

**Means:** SPECS quotes the achieved 1.59 kHz, not the target. **Rules out:**
nothing; the next E96 value down (9.76 k) is 1.9 % high.

### Study 2 — EXAMPLE: 3V3 divider accuracy by resistor class

*(Example entry shipped with the template — `./setup.sh clean-examples` removes it.)*
`studies/s02_divider_tolerance.py`.

**Asked:** does the 3V3 rail hold its ±3 % hard line (SPECS 2) with 1 %
divider resistors, given a ±1 % reference?

**Found:** 3.3 V from 0.8 V needs R1/R2 = 3.125, which falls halfway between
two E96 ratios; the best of 59 pairs (35.7 k / 11.5 k) is 0.50 % low before
any tolerance. Worst case: 2.97 % at 1 %, 2.24 % at 0.5 %, 1.64 % at 0.1 %;
Monte Carlo (20 000 samples per class, seeded) yields 100 % in every class.

**Means:** 1 % parts pass on paper only — 0.03 % from the line, inside the
project's 0.25 % margin rule. 0.5 % parts pass with 0.76 %.

**Rules out:** 1 % resistors at this reference and ratio, unless the
reference tightens to ±0.5 % or the ratio leaves the E96 grid.

**Status:** proposed 0.5 % resistors; that spends money, so it waits on the
user's decision before the value bus line goes from `proposed` to `set`.

### Study 3 — EXAMPLE: LDO package against the junction-temperature limit

*(Example entry shipped with the template — `./setup.sh clean-examples` removes it.)*
`studies/s03_ldo_thermal.py`.

**Asked:** which package keeps the 3V3 LDO under 105 °C (SPECS 4, H) at the
corner: 5.5 V in, 0.2 A out, 70 °C ambient?

**Found:** 0.440 W at the corner. SOT-23-5 (180 °C/W) reaches 149.3 °C;
DFN-6 2×2 (70 °C/W) 100.8 °C; SOT-223 (60 °C/W) 96.4 °C.

**Means:** DFN-6 2×2 is the smallest that passes, with 4.2 °C of margin — true
only on the copper the datasheet's θJA assumes, so the result is also a layout
rule for the board's handoff. **Rules out:** SOT-23-5 at this load.
