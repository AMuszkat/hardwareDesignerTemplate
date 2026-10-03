#!/usr/bin/env bash
# Set up this hardware-design template on this machine.  SETUP.md is the long form.
#
#   ./setup.sh check                         every requirement, exit 1 on a missing one
#   ./setup.sh install                       the project-local parts (venvs, freerouting,
#                                            API key file, per-machine settings)
#   ./setup.sh init <ProjectName> [--permission-mode MODE] [--kicad-python PATH]
#                                            name the project and its sessions
#   ./setup.sh new-board <N> <slug> "<Title>" [--fab PROFILE]
#                                            create boardN_<slug> on both sides, publish rev 1
#   ./setup.sh konnect-sync                  refresh the project's copies of Konnect's
#                                            skills/agents from the installed binary
#   ./setup.sh render-settings               re-write the per-machine settings (after a move)
#   ./setup.sh clean-examples [--dry-run]    remove the example studies and their rows
#   ./setup.sh check-public [--history]      before a repo goes public: secrets, personal
#                                            data, files that are never published
#   ./setup.sh check-public --install-hook   ... and run it before every git push
#
# ./verify.sh runs every check of a working project (numbers, docs, diagrams, handoffs).
#
# Never runs sudo and never installs system packages: `check` prints the command
# for anything missing at system level.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KP="$ROOT/kicad_project"
cd "$ROOT"
API_ENV="${HWDESIGN_API_ENV:-$HOME/.config/hwdesign/api.env}"

py() { if [ -x "$ROOT/.venv/bin/python" ]; then "$ROOT/.venv/bin/python" "$@"; else python3 "$@"; fi; }

cmd_check() { python3 tools/setup/check.py; }

cmd_render() { python3 tools/setup/render_settings.py; }

cmd_konnect_sync() {
  command -v konnect >/dev/null || { echo "konnect is not on PATH (SETUP.md section 1, item 5)" >&2; exit 1; }
  local tmp; tmp="$(mktemp -d)"
  # Konnect installs into $HOME/.claude; pointing HOME at a scratch directory
  # makes it generate the same files without touching your user configuration.
  HOME="$tmp" konnect init --client claude </dev/null >/dev/null
  # Konnect's skills are the ones named konnect / kicad-*; a skill it no longer
  # ships is removed, the project's own (board-setup, footprint, ...) are left.
  for d in "$KP"/.claude/skills/konnect "$KP"/.claude/skills/kicad-*; do
    [ -d "$d" ] && [ ! -d "$tmp/.claude/skills/$(basename "$d")" ] && { rm -rf "$d"; echo "removed skill $(basename "$d") (no longer shipped by Konnect)"; }
  done
  for d in "$tmp"/.claude/skills/*/; do
    n="$(basename "$d")"
    rm -rf "$KP/.claude/skills/$n"
    cp -r "$d" "$KP/.claude/skills/$n"
  done
  mkdir -p "$KP/.claude/agents"
  cp "$tmp"/.claude/agents/*.md "$KP/.claude/agents/"
  konnect --version | awk '{print $NF}' > "$KP/.claude/konnect.version"
  # Its PreToolUse hook lives in the project's settings.json; say so if the
  # installed Konnect now wants a different one.
  python3 - "$tmp/.claude/settings.json" "$KP/.claude/settings.json" <<'PYEOF' || true
import json, sys
def hooks(p):
    try:
        j = json.load(open(p))
    except Exception:
        return None
    return sorted((h.get("matcher", ""), x.get("command", ""))
                  for h in j.get("hooks", {}).get("PreToolUse", [])
                  for x in h.get("hooks", []) if "konnect" in x.get("command", ""))
def norm(hs):          # "/home/<you>/.local/bin/konnect skill x" == "konnect skill x"
    return None if hs is None else sorted((m, " ".join([c.split()[0].rsplit("/", 1)[-1]] + c.split()[1:]))
                                          for m, c in hs)
want, have = norm(hooks(sys.argv[1])), norm(hooks(sys.argv[2]))
if want is None:
    print("Its PreToolUse hook is declared in kicad_project/.claude/settings.json (could not compare).")
elif want == have:
    print("Its PreToolUse hook in kicad_project/.claude/settings.json matches this Konnect.")
else:
    print("WARNING: this Konnect installs a different PreToolUse hook than "
          "kicad_project/.claude/settings.json declares. Update the matcher there to:")
    for m, c in want:
        print(f"  matcher: {m}\n  command: {c}")
PYEOF
  rm -rf "$tmp"
  echo "Konnect $(cat "$KP/.claude/konnect.version"): skills and subagents copied into kicad_project/.claude/"
}

cmd_install() {
  echo "== concept venv (.venv)"
  [ -x .venv/bin/python ] || python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
  echo "   ok"
  echo "== board tools (kicad_project/tools/setup.sh: venv + freerouting)"
  "$KP/tools/setup.sh" || echo "   kicad_project/tools/setup.sh reported a problem -- see above"
  echo "== distributor API key file"
  if [ -f "$API_ENV" ]; then
    echo "   $API_ENV exists (not touched)"
  else
    mkdir -p "$(dirname "$API_ENV")"
    ( umask 077; cat > "$API_ENV" <<'KEYS'
# Distributor API credentials for tools/parts/ (market.py, fetch.py, search.py,
# datasheet.py).  Lives OUTSIDE the repo on purpose; mode 600.  Never commit,
# never paste into a chat, never echo into a report.  SETUP.md section 4 says
# where each one comes from.

# Nexar / Octopart: portal.nexar.com -> Apps -> <app> -> Authorization
NEXAR_CLIENT_ID=
NEXAR_CLIENT_SECRET=

# DigiKey: developer.digikey.com -> My Apps (Product Information v4)
DIGIKEY_CLIENT_ID=
DIGIKEY_CLIENT_SECRET=

# Mouser: mouser.com/api-hub -> Search API
MOUSER_API_KEY=

# Farnell / element14: partner.element14.com
ELEMENT14_API_KEY=

# Optional 24-hour Nexar portal token, for testing only.
NEXAR_TOKEN=
KEYS
    )
    echo "   created $API_ENV (mode 600) -- fill in the values"
  fi
  if [ ! -f "$KP/.claude/skills/konnect/SKILL.md" ] && command -v konnect >/dev/null; then
    echo "== Konnect skills"; cmd_konnect_sync
  fi
  if [ -f "$KP/project.json" ]; then echo "== per-machine settings"; cmd_render; fi
  echo
  cmd_check || true
}

cmd_init() {
  local name="${1:-}"; shift || true
  [ -n "$name" ] || { echo "usage: ./setup.sh init <ProjectName> [--permission-mode MODE] [--kicad-python PATH]" >&2; exit 2; }
  local mode="" kpy=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --permission-mode|--kicad-python)
        [ $# -ge 2 ] || { echo "$1 needs a value" >&2; exit 2; }
        if [ "$1" = --permission-mode ]; then mode="$2"; else kpy="$2"; fi
        shift 2 ;;
      *) echo "unknown option $1" >&2; exit 2 ;;
    esac
  done
  # A mode the CLI does not know would only fail when a launcher starts it.
  if [ -n "$mode" ] && command -v claude >/dev/null 2>&1; then
    claude --permission-mode "$mode" --version >/dev/null 2>&1 || {
      echo "'$mode' is not a permission mode this Claude Code accepts:" >&2
      claude --permission-mode "$mode" --version 2>&1 | head -2 >&2
      exit 2; }
  fi
  if [ -z "$kpy" ]; then
    for c in "${KICAD_PYTHON:-}" /usr/bin/python3 \
      "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"; do
      [ -n "$c" ] && [ -x "$c" ] && "$c" -c 'import pcbnew' 2>/dev/null && { kpy="$c"; break; }
    done
  fi
  [ -n "$kpy" ] || { echo "WARNING: no Python that imports pcbnew found; pass --kicad-python" >&2; kpy=/usr/bin/python3; }
  args=(init "$name" --kicad-python "$kpy")
  [ -n "$mode" ] && args+=(--permission-mode "$mode")
  python3 "$KP/tools/session/relay.py" "${args[@]}"
  cmd_render
  [ -d .git ] || git init -q && echo "git repository: $(git rev-parse --show-toplevel)"
  cat <<NEXT

Initialised. Next:
  ./setup.sh new-board 1 main "Main board"     # first board (repeat per board)
  bin/concept                                  # the concept session (repo root)
  bin/board board1                             # a board session, in another terminal
  bin/relay on                                 # optional: let the sessions ring each other
NEXT
}

cmd_new_board() {
  [ -f "$KP/project.json" ] || { echo "run ./setup.sh init <ProjectName> first" >&2; exit 1; }
  py tools/handoff/new_board.py "$@"
}

case "${1:-}" in
  check) cmd_check ;;
  install) cmd_install ;;
  init) shift; cmd_init "$@" ;;
  new-board) shift; cmd_new_board "$@" ;;
  konnect-sync) cmd_konnect_sync ;;
  render-settings) cmd_render ;;
  clean-examples) shift; python3 tools/setup/clean_examples.py "$@" ;;
  check-public) shift; python3 tools/setup/check_public.py "$@" ;;
  *) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 2 ;;
esac
