#!/usr/bin/env bash
# Freerouting MCP server -- local/offline stdio mode.
#
# Launched by Claude Code via .mcp.json; not normally run by hand.
# Run ./setup.sh first to fetch and verify the engine.
#
# Security posture:
#   - MCP transport is stdio: the server is a child process of the editor
#     session, so nothing outside that session speaks MCP to it.
#   - freerouting's stdio transport is internally a BRIDGE that forwards to
#     its own MCP HTTP endpoint, so mcp_server.http_allowed must stay true --
#     with it false the bridge has no backend and stdout stays silent.
#     Containment comes from pinning both listeners to loopback:
#       127.0.0.1:37864+N  REST routing API
#       127.0.0.1:37964+N  MCP HTTP endpoint (the bridge's backend)
#     N is the board number of the session (HW_BOARD=boardN), so two board
#     sessions can each run their own server; each also gets its own data dir.
#     Nothing binds 0.0.0.0 and no board data leaves the machine.
#   - Analytics (BigQuery/Segment telemetry) disabled.
#   - State isolated under $FR_HOME/data-<board> so a GUI install stays independent.
#
# Authentication is off on both surfaces, which is safe because they are
# loopback only -- but any local process on the machine can reach them while
# a session is open. On a multi-user host, enable authentication instead.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
. ./common.sh

if [ ! -f "$FR_HOME/$FR_JAR" ]; then
  echo "freerouting is not installed at $FR_HOME/$FR_JAR" >&2
  echo "Run: $(pwd)/setup.sh" >&2
  exit 1
fi

if ! JAVA="$(fr_find_java)"; then
  echo "No Java >= $FR_JAVA_MIN found. Run: $(pwd)/setup.sh" >&2
  exit 1
fi

fr_ports
DATA="$FR_DATA"
mkdir -p "$DATA"

exec "$JAVA" -jar "$FR_HOME/$FR_JAR" \
  --user_data_path="$DATA" \
  --gui.enabled=false \
  --api_server.enabled=true \
  --api_server.authentication.enabled=false \
  --api_server.endpoints=http://127.0.0.1:$FR_API_PORT \
  --mcp_server.enabled=true \
  --mcp_server.authentication.enabled=false \
  --mcp_server.stdio=true \
  --mcp_server.http_allowed=true \
  --mcp_server.endpoints=http://127.0.0.1:$FR_MCP_PORT \
  --mcp_server.target_api_base_url=http://127.0.0.1:$FR_API_PORT \
  --usage_and_diagnostic_data.disable_analytics=true \
  "$@"
