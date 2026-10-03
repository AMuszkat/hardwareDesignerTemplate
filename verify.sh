#!/usr/bin/env bash
# Every check the concept side has, in one command. Exit 0 only if all pass.
#
#   ./verify.sh
#
#   1. headline regression   studies/run_all.py --check    the numbers SPECS quotes
#   2. doc lint              tools/lint_docs.py            scope, headings, diagrams, REFLECTS,
#                                                          OPEN_CLAIMS, handoff freshness, SPECS
#                                                          keys in the baseline, STATUS block
#   3. diagram unit tests    tools/diagrams/test_diagrams.py
#   4. rendered text         tools/diagrams/check_diagrams.py   (Inkscape: text off canvas / overlapping)
#
# Run it before calling a decision, a study or a publish done.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PY=.venv/bin/python
[ -x "$PY" ] || { echo "verify: .venv is missing -- run ./setup.sh install" >&2; exit 2; }
fail=0
step() {
  local name="$1"; shift
  printf '\n== %s\n' "$name"
  if "$@"; then echo "-- $name: ok"; else echo "-- $name: FAILED"; fail=1; fi
}
step "headline regression" "$PY" studies/run_all.py --check
step "doc lint" "$PY" tools/lint_docs.py
step "diagram unit tests" "$PY" -m unittest discover -q -s tools/diagrams
if command -v inkscape >/dev/null 2>&1; then
  step "rendered text" "$PY" tools/diagrams/check_diagrams.py
else
  printf '\n== rendered text\n-- rendered text: FAILED (Inkscape is not installed; ./setup.sh check)\n'; fail=1
fi
echo
if [ "$fail" -eq 0 ]; then echo "verify: all checks pass"; else echo "verify: FAILED (see above)"; fi
exit "$fail"
