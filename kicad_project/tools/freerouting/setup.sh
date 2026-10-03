#!/usr/bin/env bash
# Fetch and verify the freerouting engine for the MCP autorouter.
# Idempotent: safe to re-run. Downloads nothing if the jar already verifies.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
. ./common.sh

echo "freerouting ${FR_VERSION} setup"
echo "  install dir: $FR_HOME"

mkdir -p "$FR_HOME"

if ! fr_sha256 /dev/null >/dev/null; then
  echo "ERROR: neither sha256sum nor shasum is on PATH; the jar cannot be verified." >&2
  echo "Nothing was changed. Install coreutils (or use macOS's shasum) and re-run." >&2
  exit 1
fi

if [ -f "$FR_HOME/$FR_JAR" ] && fr_verify "$FR_HOME/$FR_JAR"; then
  echo "  jar:         already present, sha256 OK"
else
  [ -f "$FR_HOME/$FR_JAR" ] && echo "  jar:         present but sha256 MISMATCH -- refetching" >&2
  echo "  jar:         downloading $FR_URL"
  curl -fL --progress-bar -o "$FR_HOME/$FR_JAR.part" "$FR_URL"
  # the old jar stays until the new one has verified
  if ! fr_verify "$FR_HOME/$FR_JAR.part"; then
    echo >&2
    echo "ERROR: sha256 mismatch after download. Expected:" >&2
    echo "  $FR_SHA256" >&2
    echo "Got:" >&2
    echo "  $(fr_sha256 "$FR_HOME/$FR_JAR.part")" >&2
    echo "Refusing to install. The release asset may have been replaced." >&2
    rm -f "$FR_HOME/$FR_JAR.part"
    exit 1
  fi
  mv "$FR_HOME/$FR_JAR.part" "$FR_HOME/$FR_JAR"
  echo "  jar:         downloaded, sha256 OK"
fi

if JAVA="$(fr_find_java)"; then
  echo "  java:        $JAVA ($("$JAVA" -version 2>&1 | head -1))"
else
  echo >&2
  echo "ERROR: no Java >= $FR_JAVA_MIN found." >&2
  echo "freerouting ${FR_VERSION} is compiled for Java ${FR_JAVA_MIN}; older JREs fail" >&2
  echo "with UnsupportedClassVersionError (class file version 69)." >&2
  echo >&2
  echo "  Debian/Ubuntu:  sudo apt install -y openjdk-${FR_JAVA_MIN}-jre-headless" >&2
  echo "  Fedora:         sudo dnf install -y java-${FR_JAVA_MIN}-openjdk-headless" >&2
  echo "  macOS:          brew install openjdk@${FR_JAVA_MIN}" >&2
  echo "  Other:          install any JRE ${FR_JAVA_MIN}+ and set FREEROUTING_JAVA" >&2
  echo "                  to its java binary." >&2
  exit 1
fi

echo
echo "Done. The MCP server is declared in kicad_project/.mcp.json; start the"
echo "board session in kicad_project/ and approve it when prompted."
