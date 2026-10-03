#!/usr/bin/env python3
"""
Check this machine and this clone against everything the two sessions use.

    python3 tools/setup/check.py          # (./setup.sh check runs this)

Every item is REQUIRED: a missing one prints FAIL with the command that fixes
it, and the script exits 1.  MANUAL items cannot be tested from a shell and
are listed for you to confirm once.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KP = ROOT / "kicad_project"
API_ENV = Path(os.environ.get("HWDESIGN_API_ENV", "~/.config/hwdesign/api.env")).expanduser()
API_KEYS = ["NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET", "DIGIKEY_CLIENT_ID",
            "DIGIKEY_CLIENT_SECRET", "MOUSER_API_KEY", "ELEMENT14_API_KEY"]
KONNECT_SKILLS = ["konnect", "kicad-schematic", "kicad-pcb", "kicad-manufacture",
                  "kicad-review", "kicad-library"]
KONNECT_AGENTS = ["kicad-design-review-agent.md", "kicad-schematic-build-agent.md"]
PROJECT_SKILLS_BOARD = ["board-setup", "footprint", "placement", "routing"]
# the same list tools/parts/datasheet.py and kicad_project/tools/fp/fp.py search
CHROMES = [c for c in (os.environ.get("CHROME_BIN"), "google-chrome-stable", "google-chrome",
                       "chromium", "chromium-browser",
                       "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                       "/Applications/Chromium.app/Contents/MacOS/Chromium") if c]
PROJECT_SKILLS_CONCEPT = ["decision", "answer-question", "datasheet", "study"]
HOOK_SCRIPTS = ["kicad_project/tools/session/relay.py", "kicad_project/tools/session/guard.py",
                "kicad_project/tools/session/wall.py", "kicad_project/tools/boardsel.py"]
LAUNCHERS = ["bin/concept", "bin/board", "bin/relay", "setup.sh", "verify.sh",
             "kicad_project/tools/kpy", "kicad_project/tools/setup.sh",
             "kicad_project/tools/freerouting/freerouting-mcp.sh"]
PCBNEW_PYTHONS = ["/usr/bin/python3",
                  "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/"
                  "Versions/Current/bin/python3"]

results = []


def add(state, what, detail="", fix=""):
    results.append((state, what, detail, fix))


def run(cmd, **kw):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def version_of(cmd):
    rc, out = run(cmd)
    return out.splitlines()[0] if rc == 0 and out else None


def major(text):
    m = re.search(r"(\d+)(?:\.(\d+))?", text or "")
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def load_json(p):
    try:
        with open(p) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def pcbnew_python():
    local = load_json(KP / "local.json") or {}
    cands = [os.environ.get("KICAD_PYTHON"), local.get("kicad_python")] + PCBNEW_PYTHONS
    for c in [c for c in cands if c]:
        rc, out = run([c, "-c", "import pcbnew; print('PCBNEW', pcbnew.Version())"])
        m = re.search(r"^PCBNEW (\S+)", out, re.M)
        if rc == 0 and m:
            return c, m.group(1)
    return None, None


def system():
    v = version_of(["claude", "--version"])
    add("ok" if v else "FAIL", "Claude Code CLI", v or "not on PATH",
        "https://docs.claude.com/en/docs/claude-code/setup")
    v = version_of(["git", "--version"])
    add("ok" if v else "FAIL", "git", v or "not on PATH", "install git from your package manager")
    pv = sys.version_info
    add("ok" if pv >= (3, 10) else "FAIL", "python3 >= 3.10", sys.version.split()[0],
        "install Python 3.10 or newer")

    v = version_of(["kicad-cli", "--version"])
    ok = v and major(v) >= (10, 0)
    add("ok" if ok else "FAIL", "KiCad 10 (kicad-cli)", v or "not on PATH",
        "https://www.kicad.org/download/ -- KiCad 10.x; kicad-cli must be on PATH")
    py, pv = pcbnew_python()
    add("ok" if py else "FAIL", "pcbnew Python module", f"{py} -> pcbnew {pv}" if py else
        "no interpreter can import pcbnew",
        "comes with KiCad (Linux: /usr/bin/python3); set KICAD_PYTHON or re-run ./setup.sh init")

    v = version_of(["konnect", "--version"])
    pinned = (KP / ".claude" / "konnect.version").read_text().strip() \
        if (KP / ".claude" / "konnect.version").exists() else None
    if not v:
        add("FAIL", "Konnect (KiCad MCP server)", "not on PATH",
            "install the konnect binary so `konnect --version` works (e.g. ~/.local/bin/konnect)")
    else:
        have = v.split()[-1]
        add("ok" if have == pinned else "WARN", "Konnect (KiCad MCP server)",
            f"{v}; project copies are {pinned}" + ("" if have == pinned else
                                                   " -- run ./setup.sh konnect-sync"))

    rc, out = run(["bash", "-c", f". '{KP}/tools/freerouting/common.sh' && "
                   "fr_find_java && echo \"$FR_HOME/$FR_JAR\" && echo \"$FR_SHA256\""])
    lines = out.splitlines() if rc == 0 else []
    if len(lines) >= 3:
        java, jar, sha = lines[-3], lines[-2], lines[-1]
        add("ok", "Java >= 25 (freerouting)", f"{java} ({version_of([java, '-version'])})")
        jar_ok = Path(jar).exists() and run(
            ["bash", "-c", f". '{KP}/tools/freerouting/common.sh' && fr_verify '{jar}'"])[0] == 0
        add("ok" if jar_ok else "FAIL", "freerouting engine", jar if jar_ok else
            f"{jar} missing or checksum mismatch", "./setup.sh install")
    else:
        add("FAIL", "Java >= 25 (freerouting)", "no Java 25+ found",
            "Debian/Ubuntu: sudo apt install -y openjdk-25-jre-headless | "
            "Fedora: sudo dnf install -y java-25-openjdk-headless | macOS: brew install openjdk@25")
        add("FAIL", "freerouting engine", "needs Java first", "./setup.sh install")

    chrome = next((c for c in CHROMES if shutil.which(c) or Path(c).exists()), None)
    add("ok" if chrome else "FAIL", "Chrome / Chromium (headless downloads)",
        chrome or "none found",
        "install Google Chrome or Chromium (datasheet.py and fp.py fall back to it)")
    v = version_of(["inkscape", "--version"])
    add("ok" if v else "FAIL", "Inkscape (diagram text check)", v or "not on PATH",
        "Debian/Ubuntu: sudo apt install -y inkscape | macOS: brew install --cask inkscape")


def venvs():
    py = ROOT / ".venv" / "bin" / "python"
    rc, out = run([str(py), "-c", "import numpy, scipy, matplotlib; print('ok')"]) \
        if py.exists() else (1, "missing")
    add("ok" if rc == 0 else "FAIL", "concept venv (.venv)", "numpy, scipy, matplotlib"
        if rc == 0 else out.splitlines()[-1], "./setup.sh install")
    py = KP / "tools" / ".venv" / "bin" / "python"
    rc, out = run([str(py), "-c", "import fitz, easyeda2kicad; print('ok')"]) \
        if py.exists() else (1, "missing")
    add("ok" if rc == 0 else "FAIL", "board venv (kicad_project/tools/.venv)",
        "easyeda2kicad, PyMuPDF" if rc == 0 else out.splitlines()[-1], "./setup.sh install")


def keys():
    if not API_ENV.exists():
        add("FAIL", "distributor API keys", f"{API_ENV} missing",
            "./setup.sh install creates it (mode 600); fill in the values -- SETUP.md section 4")
        return
    mode = stat.S_IMODE(API_ENV.stat().st_mode)
    vals = {}
    for line in API_ENV.read_text().splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if m and not line.lstrip().startswith("#"):
            vals[m.group(1)] = m.group(2).strip("'\"")     # tools/parts/market.py strips them too
    empty = [k for k in API_KEYS if not vals.get(k)]
    add("ok" if not empty else "FAIL", "distributor API keys",
        f"{API_ENV}: all {len(API_KEYS)} set" if not empty else
        f"{API_ENV}: empty {', '.join(empty)}", "fill them in -- SETUP.md section 4")
    add("ok" if mode & 0o077 == 0 else "FAIL", "API key file permissions", oct(mode),
        f"chmod 600 {API_ENV}")


def project():
    proj = load_json(KP / "project.json")
    add("ok" if proj else "FAIL", "project initialised",
        f"{proj.get('project')} ({proj.get('concept_session')}, {proj.get('board_session_prefix')}boardN, "
        f"mode {proj.get('permission_mode')})" if proj else "kicad_project/project.json missing",
        "./setup.sh init <ProjectName>")
    relay = load_json(KP / "relay.json") or {}
    add("ok", "relay switch", "ON" if relay.get("enabled") else "OFF (default; bin/relay on)")
    rc, out = run([sys.executable, str(ROOT / "tools/setup/render_settings.py"), "--check"])
    add("ok" if rc == 0 else "FAIL", "per-machine settings (deny rules, CLAUDE.md exclude)",
        "rendered for this path" if rc == 0 else "; ".join(out.splitlines()),
        "./setup.sh render-settings" if proj else "./setup.sh init <ProjectName> (renders them)")
    sk = KP / ".claude" / "skills"
    miss = [s for s in KONNECT_SKILLS if not (sk / s / "SKILL.md").exists()]
    miss += [a for a in KONNECT_AGENTS if not (KP / ".claude" / "agents" / a).exists()]
    add("ok" if not miss else "FAIL", "Konnect skills and subagents (project copies)",
        "all present" if not miss else "missing: " + ", ".join(miss), "./setup.sh konnect-sync")
    miss = [s for s in PROJECT_SKILLS_BOARD if not (sk / s / "SKILL.md").exists()]
    add("ok" if not miss else "FAIL", "project skills (board session)",
        "all present" if not miss else "missing: " + ", ".join(miss),
        "they ship with the template: git checkout -- kicad_project/.claude/skills")
    miss = [s for s in PROJECT_SKILLS_CONCEPT
            if not (ROOT / ".claude" / "skills" / s / "SKILL.md").exists()]
    add("ok" if not miss else "FAIL", "project skills (concept session)",
        "all present" if not miss else "missing: " + ", ".join(miss),
        "they ship with the template: git checkout -- .claude/skills")
    miss = [h for h in HOOK_SCRIPTS if not (ROOT / h).exists()]
    miss += [f"{s} (no hooks)" for s in (".claude/settings.json", "kicad_project/.claude/settings.json")
             if "PreToolUse" not in ((load_json(ROOT / s) or {}).get("hooks") or {})]
    add("ok" if not miss else "FAIL", "hooks (session brief, walls, message guard)",
        "declared and present" if not miss else "missing: " + ", ".join(miss),
        "they ship with the template: git checkout -- .claude kicad_project/.claude kicad_project/tools/session")
    miss = [x for x in LAUNCHERS if not os.access(ROOT / x, os.X_OK)]
    add("ok" if not miss else "FAIL", "launchers and scripts are executable",
        "all" if not miss else "not executable: " + ", ".join(miss),
        "chmod +x " + " ".join(miss))
    boards = sorted(d.name for d in KP.iterdir() if re.fullmatch(r"board\d+_\w+", d.name))
    add("ok" if boards else "WARN", "boards", ", ".join(boards) or
        "none yet -- ./setup.sh new-board 1 main \"Main board\"")


def manual():
    add("MANUAL", "Claude in Chrome extension",
        "installed in your local Chrome and connected to this Claude account; with more "
        "than one browser connected, sessions must select the one with isLocal: true",
        "https://claude.ai/chrome")
    add("MANUAL", "both sessions on this machine, same account",
        "the relay addresses sessions by name through ListAgents")


def main():
    system()
    venvs()
    keys()
    project()
    manual()
    w = max(len(r[1]) for r in results)
    for state, what, detail, fix in results:
        print(f"{state:6} {what:<{w}}  {detail}")
        if state in ("FAIL", "WARN") and fix:
            print(f"{'':6} {'':<{w}}  -> {fix}")
    fails = sum(1 for r in results if r[0] == "FAIL")
    print(f"\n{fails} required item(s) missing." if fails else
          "\nAll required items present. Confirm the MANUAL items once.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
