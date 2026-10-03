# freerouting — autorouter MCP server

Runs the [freerouting](https://github.com/freerouting/freerouting) autorouter
**entirely on this machine** and exposes it to Claude Code as an MCP server.
No account, no API key, no traffic to `api.freerouting.app`.

## Setup on a fresh clone

    ./setup.sh          # or ../setup.sh, which runs it with everything else

It fetches the engine, verifies its sha256, and checks for a Java 25 runtime.
It is idempotent — re-running it downloads nothing if the jar already verifies.
Then start the board session in `kicad_project/` and approve the server when
prompted.

Needs a JRE 25 or newer:

    Debian/Ubuntu   sudo apt install -y openjdk-25-jre-headless
    Fedora          sudo dnf install -y java-25-openjdk-headless
    macOS           brew install openjdk@25

`setup.sh` prints these if it can't find one. Java 21 is **not** enough: the
2.4.x jars are class-file v69 and older JREs fail with
`UnsupportedClassVersionError`.

## What lives where

| | |
|---|---|
| `setup.sh` | fetch + verify the engine, check the runtime |
| `freerouting-mcp.sh` | the MCP server entry point, launched by `.mcp.json` |
| `common.sh` | version, checksum, paths, Java discovery (sourced by both) |
| `../../.mcp.json` | declares the server to Claude Code (`kicad_project/.mcp.json`, the board sessions' project scope) |

**The 62 MB jar is deliberately not in the repo.** It is downloaded to
`~/.local/share/freerouting/` along with `data-<board>/` (jobs, logs, per-machine
config). Override that location with `FREEROUTING_HOME`; point at a specific
JRE with `FREEROUTING_JAVA`. Nothing in this directory needs `.gitignore`.

## Upgrading

Edit `FR_VERSION` and `FR_SHA256` in `common.sh`, then re-run `./setup.sh`.
Take the checksum from the GitHub release's own asset digest, not from a
mirror. A mismatch makes `setup.sh` delete the download and refuse to install.

## Security posture

- **stdio transport.** The server is a child process of the Claude Code
  session; nothing outside that session speaks MCP to it.
- **Both listeners are loopback-pinned** — `127.0.0.1:37864+N` (REST routing
  API) and `127.0.0.1:37964+N` (MCP HTTP), where N is the session's board
  number (`HW_BOARD`), so each board session runs its own server. Nothing
  binds `0.0.0.0`.
- **Telemetry off** via `usage_and_diagnostic_data.disable_analytics=true`,
  which otherwise reports to BigQuery/Segment.
- **Residual exposure:** authentication is disabled on both surfaces. That is
  safe because they are loopback only, but any local process on the machine
  can reach them while a session is open. On a multi-user host, enable
  authentication instead.

### Two traps worth knowing before you "harden" this

- **`mcp_server.http_allowed` must stay `true`.** freerouting's stdio transport
  is not standalone — it is a bridge that forwards each JSON-RPC message to the
  app's own MCP HTTP endpoint using an `X-Internal-Bridge-Token` header.
  Setting `http_allowed=false` does close the port, but it also leaves the
  bridge with no backend: the handshake gets no reply and stdout stays silent,
  with nothing in the log saying why. Containment comes from the loopback pin,
  not from closing that port.
- **`mcp_server.stdio=true` only works as a CLI argument** (or the
  `FREEROUTING__MCP_SERVER__STDIO` environment variable). In `freerouting.json`
  it is ignored, because the stdout redirect has to happen before logging
  initialises — the app warns about this explicitly at startup.

## Why it is not the default router — read before trusting a run

KiCad's Specctra export and this server lose things a reviewed, group-by-group
board depends on. Each of these was observed on a real board; check them on
your versions before you rely on a run:

- ⚠ **Custom `.kicad_dru` rules are not exported.** Specctra has no equivalent,
  so a rule such as "this class takes no via" or an area-scoped clearance does
  not reach the router at all.
- ⚠ **Area-scoped net-class vias are not exported.** KiCad exports only the
  classes that own nets; a class applied by an area rule (a fine-pitch BGA's
  field) owns none, and the `.dsn` carries one via size for everything.
  Offering an extra padstack does not get it used either: the export carries no
  per-class via rule.
- ⚠ **Plane layers export as signal layers**, so the router will happily slot a
  ground plane. Retyping the layer `(type power)` in the `.dsn` WAS honoured on
  the test board — the one patch that worked.
- ⚠ **`(type protect)` does not preserve existing copper.** Hand-routed segments
  came back re-routed. An autorouter pass therefore cannot add to a board whose
  earlier groups have been reviewed and accepted.
- ⚠ **Router settings sent through the MCP were not applied** on the version
  tested: `update_job_settings` and `router_settings` at enqueue both returned
  200 and the job ran at the defaults (copper-to-edge 500 µm, 12 h timeout,
  fanout and optimizer on). Verify on your version: read `router_settings` back
  from `get_job_details` after starting; a 200 means nothing on its own.
- ⚠ **`cancel_job` discards the output.** Download the `.ses` with
  `download_job_output_to_local_file` BEFORE cancelling.
- **It routes the whole `.dsn` in one pass** — no net filter, no area filter —
  so it cannot serve a review that goes one group at a time.

Where it earns its place: a scratch copy, a whole board nobody has reviewed
yet, or a quick feasibility answer ("can this placement be routed at all?").
The result is a proposal to judge, never copper to accept: import the `.ses`
into a SCRATCH copy of the board, look at it (`view.py`, `place.py drc`), and
re-express what you keep as a `route.py` plan — nothing converts a `.ses` to a
plan for you, and only a plan goes through `route.py check` and the gates.

## KiCad gotcha

KiCad's Specctra `.dsn` export omits board-edge clearance
([#558](https://github.com/freerouting/freerouting/issues/558)), so the router
will run copper right up to the board outline. The documented fix is
`router.copperToEdgeClearanceUm` per job (copper-to-edge from `14_fab.md` ×
1000) — but see above: confirm the setting is applied before relying on it.
The round trip itself is scriptable headlessly: `pcbnew.ExportSpecctraDSN` and
`pcbnew.ImportSpecctraSES` exist (run them with `tools/kpy`), though
`kicad-cli` and Konnect offer no `.dsn` export.
