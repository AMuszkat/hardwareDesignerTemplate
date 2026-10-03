# SPECS — the numbers

**Scope:** every specified number of the product — performance, electrical,
mechanical, environmental, protection. One row per parameter: value,
tolerance or limit, whether it is a **hard line** (**H**: a decision may not
silently cross it; moving one moves the diagrams and docs in the same pass),
and its source (a study, a datasheet page, a standard, a user decision).
**Not here:** how anything works (`IMPLEMENTATION.md`), what is open or why
(`STATUS.md` / `history/HISTORY.md`), model parameters
(`studies/designpoint.py`) or board component values
(`studies/components_boardN.py`). ⚠ **And not here: anything that is a BUILD
or LAYOUT instruction rather than a number** — fitted/DNP options, links,
footprint provisions. Those belong in `IMPLEMENTATION.md`; a row here that
names no measured quantity is in the wrong file.

State: (date of the last edit).

## 1. Performance

| parameter | value | H | source |
|---|---|---|---|
| *(example)* ADC anti-alias corner | **1.59 kHz** (target 1.6 kHz) | | `s01_rc_corner` — `rc.fc_hz` |

## 2. Electrical — supplies and interfaces

| parameter | value | H | source |
|---|---|---|---|
| *(example)* input voltage | 4.5–5.5 V | H | user requirement |
| *(example)* 3V3 load | ≤ 0.2 A | | user requirement |
| *(example)* 3V3 rail accuracy | ±3 %; **worst case 2.24 %** with 0.5 % divider resistors (1 % passes by 0.03 %, inside the 0.25 % margin rule) | H | `s02_divider_tolerance` — `divider.worst_tol0p5_pct` |

## 3. Mechanical and environmental

| parameter | value | H | source |
|---|---|---|---|
| *(example)* ambient | 0–70 °C | | user requirement |

## 4. Protection and safe state

| parameter | value | H | source |
|---|---|---|---|
| *(example)* LDO junction temperature | ≤ 105 °C (derated from 125); **100.8 °C** in DFN-6 2×2 at the corner | H | `s03_ldo_thermal` — `ldo.tj_dfn62x2_degc` |
