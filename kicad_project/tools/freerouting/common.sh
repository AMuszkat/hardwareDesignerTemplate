# Shared helpers for the freerouting tooling. Sourced, not executed.

FR_VERSION="2.4.1"
FR_JAR="freerouting-${FR_VERSION}.jar"
FR_SHA256="251101c3eeac22d7e7dfcf6796603279e5d1000283eb82d8f093780f7afc6aa9"
FR_URL="https://github.com/freerouting/freerouting/releases/download/v${FR_VERSION}/${FR_JAR}"

# Where the jar and its runtime state live. Deliberately OUTSIDE the repo:
# the jar is 62 MB and the state is per-machine. Override with FREEROUTING_HOME.
FR_HOME="${FREEROUTING_HOME:-$HOME/.local/share/freerouting}"

# Minimum JRE. freerouting 2.4.x jars are class-file v69; Java 21 cannot load
# them and fails with UnsupportedClassVersionError.
FR_JAVA_MIN=25

fr_sha256() {
  # $1 = a file. Echoes its sha256, or returns 2 when the machine has no tool
  # for it (Linux: sha256sum; macOS: shasum).
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else return 2; fi
}

fr_verify() {
  # $1 = a jar. 0 = matches FR_SHA256, 1 = does not, 2 = cannot tell (no tool).
  local got
  got="$(fr_sha256 "$1")" || return 2
  [ "$got" = "$FR_SHA256" ]
}

fr_ports() {
  # One session per board may run the autorouter at the same time, so each gets
  # its own pair of loopback ports and its own data directory: boardN -> +N.
  # (No HW_BOARD -- a session not started with bin/board -- uses the base pair.)
  local n="${HW_BOARD#board}"
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  FR_API_PORT=$((37864 + n % 90))
  FR_MCP_PORT=$((37964 + n % 90))
  FR_DATA="$FR_HOME/data${HW_BOARD:+-$HW_BOARD}"
}

fr_java_major() {
  # $1 = path to a java binary. Echoes its major version, or nothing.
  "$1" -version 2>&1 | sed -n '1s/.*version "\([0-9]*\).*/\1/p'
}

fr_find_java() {
  # Echoes a java binary of at least $FR_JAVA_MIN, or returns 1.
  local c major
  for c in \
    ${FREEROUTING_JAVA:-} \
    /usr/lib/jvm/java-${FR_JAVA_MIN}-openjdk-amd64/bin/java \
    /usr/lib/jvm/java-${FR_JAVA_MIN}-openjdk/bin/java \
    /usr/lib/jvm/jdk-${FR_JAVA_MIN}/bin/java \
    "$FR_HOME/jre${FR_JAVA_MIN}/bin/java" \
    "$(command -v java 2>/dev/null || true)"
  do
    [ -n "$c" ] && [ -x "$c" ] || continue
    major="$(fr_java_major "$c")"
    [ -n "$major" ] && [ "$major" -ge "$FR_JAVA_MIN" ] 2>/dev/null || continue
    echo "$c"; return 0
  done
  return 1
}
