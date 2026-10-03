#!/usr/bin/env bash
# The board session's tools, set up on this machine. Idempotent: re-run any time.
#
#   kicad_project/tools/setup.sh            # from anywhere
#
# 1. kicad_project/tools/.venv from requirements.txt (easyeda2kicad, PyMuPDF)
# 2. freerouting: the jar (sha256-verified) and a Java 25 runtime
# 3. checks, all REQUIRED: kicad-cli, a python that imports pcbnew (tools/kpy),
#    Java >= 25, Chrome or Chromium
#
# It installs nothing system-wide and never runs sudo: a missing system tool is
# reported with the command to install it, and the script exits 1.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
TOOLS="$(pwd)"
fail=0
ok()   { printf '  %-22s %s\n' "$1" "$2"; }
bad()  { printf '  %-22s MISSING -- %s\n' "$1" "$2" >&2; fail=1; }

echo "board-session tools: $TOOLS"

# --- 1. the venv -------------------------------------------------------------
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  bad "python3" "install Python >= 3.10"
else
  if [ ! -x .venv/bin/python ]; then
    "$PY" -m venv .venv || bad ".venv" "python3 -m venv failed (Debian/Ubuntu: sudo apt install python3-venv)"
  fi
  if [ -x .venv/bin/python ]; then
    .venv/bin/pip install --quiet --upgrade pip >/dev/null 2>&1
    if .venv/bin/pip install --quiet -r requirements.txt; then
      ok ".venv" "$(.venv/bin/python -c 'import pymupdf,sys; print("PyMuPDF", pymupdf.VersionBind, "| python", sys.version.split()[0])')"
      ok "easyeda2kicad" "$(.venv/bin/pip show easyeda2kicad 2>/dev/null | sed -n 's/^Version: //p')"
    else
      bad ".venv" "pip install -r requirements.txt failed (network?)"
    fi
  fi
fi

# --- 2. freerouting ----------------------------------------------------------
log="$(mktemp)"
if ./freerouting/setup.sh >"$log" 2>&1; then
  ok "freerouting" "$(grep -E 'jar:|java:' "$log" | sed 's/^ *//' | tr '\n' ' ')"
else
  sed 's/^/    /' "$log" >&2
  bad "freerouting" "see above (Java >= 25 is required)"
fi
rm -f "$log"

# --- 3. KiCad and Chrome -----------------------------------------------------
if command -v kicad-cli >/dev/null 2>&1; then
  ok "kicad-cli" "$(kicad-cli --version 2>/dev/null)"
else
  bad "kicad-cli" "install KiCad 10 (https://www.kicad.org/download/); on macOS add KiCad.app/Contents/MacOS to PATH"
fi
if v="$(./kpy -c 'import pcbnew; print(pcbnew.Version())' 2>/dev/null)"; then
  ok "pcbnew (tools/kpy)" "$v"
else
  ./kpy -c 'import pcbnew' 2>&1 | tail -2 | sed 's/^/    /' >&2
  bad "pcbnew (tools/kpy)" "no interpreter imports pcbnew; set kicad_python in kicad_project/local.json (./setup.sh init) or KICAD_PYTHON"
fi
chrome=""
for c in "${CHROME_BIN:-}" google-chrome-stable google-chrome chromium chromium-browser \
         "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
  [ -n "$c" ] || continue
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then chrome="$c"; break; fi
done
if [ -n "$chrome" ]; then
  ok "chrome" "$("$chrome" --version 2>/dev/null | head -1)"
else
  bad "chrome" "install Google Chrome or Chromium (headless fallback for CAD and datasheet downloads)"
fi

echo
if [ "$fail" -ne 0 ]; then
  echo "board-session tools: NOT ready (see MISSING above)" >&2
  exit 1
fi
echo "board-session tools: ready"
