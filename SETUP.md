# SETUP — getting a machine and a clone ready

`./setup.sh check` tests everything below and prints the fix for anything
missing. **Every item in section 1 is required** (except the one marked
*optional*): the sessions' workflows call each of them, and a missing one
surfaces mid-task as a tool that silently cannot do its step.

## 1. Requirements

| # | requirement | used by | install |
|---|---|---|---|
| 1 | **Claude Code** CLI, logged in | both sessions | https://docs.claude.com/en/docs/claude-code/setup |
| 2 | **git**, **Python ≥ 3.10** with `venv` | everything | your package manager (Debian/Ubuntu: `sudo apt install -y git python3-venv`) |
| 3 | **KiCad 10** with `kicad-cli` on `PATH` | board sessions, every PCB tool | https://www.kicad.org/download/ |
| 4 | **KiCad's `pcbnew` Python module** | `tools/board_setup`, `place`, `route` | ships with KiCad: Linux `/usr/bin/python3`; macOS the Python inside `KiCad.app`. `./setup.sh init` finds it |
| 5 | **Konnect** (KiCad MCP server) — `konnect` on `PATH` | board sessions (all KiCad edits); concept (its `integration` toolset: datasheet URLs, JLCPCB catalogue) | install the binary so `konnect --version` works (e.g. into `~/.local/bin`). You do **not** need `konnect init`: the project carries its skills, agents and hook (section 6) |
| 6 | **Java ≥ 25** | freerouting (the autorouter MCP server) | Debian/Ubuntu `sudo apt install -y openjdk-25-jre-headless` · Fedora `sudo dnf install -y java-25-openjdk-headless` · macOS `brew install openjdk@25`. Java 21 is not enough (class-file v69) |
| 7 | **freerouting 2.4.x** engine | the autorouter MCP server | `./setup.sh install` downloads it to `~/.local/share/freerouting/` and verifies its sha256 |
| 8 | **Google Chrome or Chromium** | `datasheet.py` and `fp.py` fall back to headless Chrome when a host refuses scripts | https://www.google.com/chrome/ or your package manager |
| 9 | **Claude in Chrome** extension, connected to the same Claude account | login-walled datasheets and CAD downloads (SnapEDA, Ultra Librarian, vendor portals) | https://claude.ai/chrome — the launchers start sessions with `--chrome` |
| 10 | **Inkscape** | `tools/diagrams/check_diagrams.py` (rendered text bounds) | Debian/Ubuntu `sudo apt install -y inkscape` · macOS `brew install --cask inkscape` |
| 11 | **Distributor API keys**: DigiKey, Mouser, element14, Nexar | `tools/parts/` (price, stock, lead time, datasheet URLs, catalogue search) | section 4 |
| 12 | Python packages | concept `.venv` (numpy, scipy, matplotlib); board `kicad_project/tools/.venv` (easyeda2kicad, PyMuPDF, Pillow, matplotlib) | `./setup.sh install` |
| 13 | *optional:* **Blender ≥ 4.2** | 3D renders with `render.py --engine blender` only — the user picks the engine per render, and the KiCad engine needs nothing extra | `./setup.sh install-blender` downloads Blender 4.5 LTS (Linux x86-64, sha256-verified, ~1.2 GB) into `~/.local/share/hwdesign/blender/`; `--amd-gpu` adds AMD's HIP runtime (Debian/Ubuntu, unpacked, no root). Elsewhere: install from blender.org and set `BLENDER_BIN` |

## 2. Install

```bash
./setup.sh check      # read the FAIL lines; install the system items it names
./setup.sh install    # creates both venvs, fetches freerouting, creates the key file
./setup.sh check      # until it says: All required items present
```

`setup.sh` never runs `sudo` and never installs system packages; it prints the
command instead.

## 3. Initialise the project

```bash
./setup.sh init MyProduct [--permission-mode manual|acceptEdits|auto|...] [--kicad-python PATH]
./setup.sh new-board 1 main "Main board" [--fab example_standard]
```

- **`init`** writes `kicad_project/project.json` (the project name, the session
  names `MyProduct_Concept` and `MyProduct_Kicad_boardN`, and the permission
  mode both sessions will share), `kicad_project/local.json` (this machine's
  pcbnew interpreter), `kicad_project/relay.json` (relay off), and the
  per-machine settings (section 6). 2 to 31 letters and digits: the name becomes
  part of the session addresses. The permission mode is checked against the
  installed CLI (`claude --help` lists them; `default` is the older name of
  `manual`).
- **`new-board`** creates the board on both sides of the wall — its data module
  `tools/handoff/boards/boardN.py`, its value bus
  `studies/components_boardN.py`, its answers file, and
  `kicad_project/boardN_<slug>/` with `QUESTIONS.md`, `ROUTING_PLAN.md`,
  `placement.json`, `general_routing_guide.md` — and publishes handoff rev 1. The
  KiCad project itself is created by the board session (through Konnect),
  named after the folder.
- **Fab profiles** live in `tools/handoff/fabs/`. `example_standard` is an
  illustrative profile (a conservative standard prototype service — no fab's
  real numbers) so the template runs end to end; `14_fab.md` says so and
  `board_setup.py check` keeps reporting it as a QUESTION. For a real board,
  copy `_blank` to `<fab>_<service>.py`, fill every field from your fab's own
  pages with the page named, and point the board's `FAB["profile"]` at it.

- A new board starts with **nothing decided**: with `--fab`, the layer count,
  the outline and the quantity read UNSET in `14_fab.md` until you set them in
  `tools/handoff/boards/boardN.py`, and Board Setup refuses the page meanwhile.

Then fill in the `(Fill in …)` placeholders in `CLAUDE.md` and `design/`
(the doc lint lists them, and the example rows still present). The three
example studies and their rows go in one step when you are ready:
`./setup.sh clean-examples`. `./verify.sh` runs every check of the concept
side at any time.

## 4. Distributor API keys

`./setup.sh install` creates `~/.config/hwdesign/api.env` (mode 600, outside
the repository; `HWDESIGN_API_ENV` overrides the path). Fill in:

| key | where |
|---|---|
| `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET` | developer.digikey.com → My Apps → create an app with *Product Information v4* |
| `MOUSER_API_KEY` | mouser.com/api-hub → Search API |
| `ELEMENT14_API_KEY` | partner.element14.com → register for the Product Search API |
| `NEXAR_CLIENT_ID`, `NEXAR_CLIENT_SECRET` | portal.nexar.com → Apps → your app → Authorization (the free tier has a small monthly part limit) |

Never commit the file, paste a key into a chat, or echo one into a report.

## 5. Start the sessions

```bash
bin/concept                 # repo root, named <Project>_Concept
bin/board board1            # kicad_project/, named <Project>_Kicad_board1, owns board1_<slug>/
```

Always use the launchers. They set the session name (the relay's address),
the shared permission mode, the Chrome integration, and `HW_BOARD`, which tells
the hooks which board folder a board session may write. A hook prints each
session's name and the relay state into its context at start. **Exactly one
concept session**: a second one at the root is a scratch session and must not
publish.

## 6. What is local to the project, and what lives on the machine

| item | where | committed | made by |
|---|---|---|---|
| concept skills `decision`, `answer-question`, `datasheet`, `study` | `.claude/skills/` | yes | template |
| board skills `schematic`, `board-setup`, `footprint`, `placement`, `routing`, `render` | `kicad_project/.claude/skills/` | yes | template |
| Konnect skills `konnect`, `kicad-*` and subagents `kicad-design-review-agent`, `kicad-schematic-build-agent` | `kicad_project/.claude/skills/`, `kicad_project/.claude/agents/` | no — Konnect is AGPL-3.0 and generates them; only `konnect.version` (the version tested) is committed | `./setup.sh konnect-sync` (run by `install`) |
| hooks: session brief, SendMessage guard, the board wall (own folder only), the concept wall (no edits to generated files or the boards' files), Konnect's PCB-IPC hook | `.claude/settings.json`, `kicad_project/.claude/settings.json`, scripts in `kicad_project/tools/session/` | yes | template |
| board selection for every PCB tool (this session's board by default; another session's refused) | `kicad_project/tools/boardsel.py` | yes | template |
| MCP servers `konnect` (both), `freerouting` (board) | `.mcp.json`, `kicad_project/.mcp.json` | yes | template; approve them on first start |
| deny rules and the CLAUDE.md exclusion (absolute paths) | `.claude/settings.local.json`, `kicad_project/.claude/settings.local.json` | no | `./setup.sh render-settings` (run by `init`/`install`) |
| relay switch, pcbnew interpreter | `kicad_project/relay.json`, `kicad_project/local.json` | no | `init`, `bin/relay` |
| venvs | `.venv/`, `kicad_project/tools/.venv/` | no | `./setup.sh install` |
| freerouting jar and its data | `~/.local/share/freerouting/` | — | `./setup.sh install` |
| Blender (optional) and AMD's HIP runtime | `~/.local/share/hwdesign/blender/` (`HWDESIGN_BLENDER_HOME`) | — | `./setup.sh install-blender [--amd-gpu]` |
| API keys | `~/.config/hwdesign/api.env` | — | you (section 4) |
| Konnect binary, KiCad, Java, Chrome + extension, Inkscape | the machine | — | you (section 1) |

⚠ **Absolute paths in permission rules need two slashes** (`Read(//home/…)`).
A single leading slash is resolved relative to the settings file and silently
matches nothing; `render_settings.py` writes them correctly, and `check`
verifies they name this clone.

If you have also run `konnect init` for your user account, the Konnect skills
exist twice (the project-scoped copies apply inside `kicad_project/`) and its
hook fires twice; both are harmless. Updating Konnect: install the new binary,
then `./setup.sh konnect-sync`.

**Moved or re-cloned the repo, or added a top-level folder?**
`./setup.sh render-settings` (it also drops the rules of the old location;
`check` says when it is needed).

**Two board sessions at once** each get their own freerouting server (ports
37864+N / 37964+N for boardN). KiCad itself is one per machine: only one board
session drives it at a time (`kicad_project/CLAUDE.md`).

## 7. The relay (optional; off by default)

The sessions always communicate through files (`QUESTIONS.md` → the handoff's
answers and CHANGELOG). The relay only adds the doorbell: with it on, a stuck
board session rings the concept session and concept rings back with the
revision, so you no longer carry the news between terminals.

```bash
bin/relay on        # then tell the concept session: "arm the relay"
bin/relay status
bin/relay off       # any time; nothing is lost
```

It works only when both sessions run on this machine under the same account,
were started with the launchers (same permission mode), have their terminals
open, and neither is sitting on a permission prompt. To verify: turn it on, ask
concept to arm it, and expect `boardN armed` back from each board session.
`kicad_project/RELAY_PROTOCOL.md` is the protocol, including what every
failure looks like.

## 8. Taking template updates, and giving fixes back

A project made from this template keeps it as a git remote, `template`
(once: `git remote add template <the template's URL>`). The template's own files — the tools, the skills, the
board contract, the publisher — improve over time; a project takes the
improvements by merging, never by copying files:

```bash
python3 kicad_project/tools/template/giveback.py merge   # commit your own work first
```

It fetches, computes the merge before anything moves, and refuses over an
uncommitted edit it would lose. A conflict is in a file both sides changed:
merge by hand (`git merge template/main`), keep the project's facts and the
template's mechanism, and run `./verify.sh` after. After the template's
history is rewritten (same trees, new ids: a privacy re-commit), a plain
`git merge` takes a days-old base and conflicts everywhere; `giveback.py
merge` merges on the rewritten twin of the commit the project last merged.

The reverse direction is the rule, not a courtesy: **an improvement to a
template-owned file (a board tool, a skill, a contract, `publish.py`) found
in a project is committed to the template remote and pushed, then merged into
the project** — the template first, the project by merge, so every project
started from it gets it and none carries a private copy. The template remote
is `https://github.com/AMuszkat/hardwareDesignerTemplate` (`template` in a
project's remotes). `kicad_project/SELF_IMPROVEMENT.md` says when such a fix
is owed — the moment a second method works where the first failed — and where
each kind of lesson goes; `kicad_project/tools/template/giveback.py` does the
round trip (`status`, `push`, `open`, `finish`, `merge`).

## 9. Publishing the repository

Before a repository becomes public — and, with the hook, before every push:

```bash
./setup.sh check-public                 # what a commit would publish
./setup.sh check-public --history       # every line ever committed, too
./setup.sh check-public --install-hook  # git pre-push hook that runs it
```

It reads exactly what git would publish and fails on secrets (token shapes,
and any value from your own key file), your home path and Claude's scratch
paths, e-mail addresses, files that are never published (`*.env`, the
per-machine settings), and on a git `user.email` that is your real address —
every commit carries it, so use GitHub's
`<id>+<login>@users.noreply.github.com` for the repo
(`git config user.email …`). List the words that must never appear — your
name, customers, codenames — one per line in
`~/.config/hwdesign/private_words.txt` (outside the repo). It warns about
third-party documents: vendor datasheet PDFs in `reference/vendor/` and the
distributor caches in `reference/market/` are usually not yours to
redistribute; a public project publishes the MPN and the URL instead.

A line that is deliberately fine carries the marker `public-check: ok`.

## 10. Troubleshooting

| symptom | fix |
|---|---|
| `tools/kpy: … cannot import pcbnew` | `./setup.sh init <Name> --kicad-python <path>` or `export KICAD_PYTHON=…` |
| a board session reads the root `CLAUDE.md` or files outside `kicad_project/` | per-machine settings missing or stale: `./setup.sh render-settings`, restart the session |
| `freerouting` MCP fails to start | `./setup.sh check` (Java 25, the jar); `kicad_project/tools/freerouting/README.md` |
| the relay "does nothing" | `bin/relay status`; both sessions started with the launchers; see RELAY_PROTOCOL §8 |
| `[Cross-session delivery notice]` | the two sessions run in different permission modes: set `permission_mode` in `kicad_project/project.json`, restart both with the launchers |
| `fetch.py` shows a source as FAILED | that distributor's key or quota; the other sources still answer |
| `render.py` stops: "choose the engine" | by design: there is no default; say `--engine kicad` or `--engine blender` |
| a Blender render says `Cycles on: CPU` | no usable GPU backend: NVIDIA needs its driver, AMD `./setup.sh install-blender --amd-gpu`, Intel its Level Zero runtime; `./setup.sh check` shows the GPU it sees |
