#!/bin/sh
# Blender, for `render.py --engine blender` ONLY.  Optional: nothing else in the
# project needs it, and nothing installs it unless you ask.
#
#   kicad_project/tools/render/setup.sh            install Blender 4.5 LTS (Linux x86-64; ~1.2 GB)
#   kicad_project/tools/render/setup.sh --amd-gpu  ... and AMD's HIP runtime, so Cycles can use
#                                                  a Radeon (Debian/Ubuntu packages, unpacked, no root)
#   kicad_project/tools/render/setup.sh --where    print the Blender render.py would use (exit 1 if none)
#   kicad_project/tools/render/setup.sh --check    one status line (./setup.sh check prints it)
#
# (from the repo root: ./setup.sh install-blender [--amd-gpu])
#
# Where it goes: $HWDESIGN_BLENDER_HOME, default ~/.local/share/hwdesign/blender
# -- OUTSIDE the repo (1.2 GB, per machine).  A Blender you already have is used
# instead when it is 4.2 or newer: $BLENDER_BIN, then `blender` on PATH, then
# /Applications/Blender.app on macOS.  No root, no account, nothing system-wide.
set -eu
VER=4.5.14
SERIES=4.5
MIN_MAJOR=4
MIN_MINOR=2
BHOME="${HWDESIGN_BLENDER_HOME:-$HOME/.local/share/hwdesign/blender}"

say() { printf '%s\n' "$*"; }

blender_version() {      # $1 = binary -> "4 5" (major minor), or nothing
  "$1" --version 2>/dev/null | sed -n '1s/^Blender \([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p'
}

usable() {               # $1 = binary: executable and >= MIN_MAJOR.MIN_MINOR
  [ -n "$1" ] && [ -x "$1" ] || return 1
  v="$(blender_version "$1")"
  [ -n "$v" ] || return 1
  set -- $v
  [ "$1" -gt "$MIN_MAJOR" ] || { [ "$1" -eq "$MIN_MAJOR" ] && [ "$2" -ge "$MIN_MINOR" ]; }
}

find_blender() {
  for c in "${BLENDER_BIN:-}" "$BHOME/blender" "$(command -v blender 2>/dev/null || true)" \
           "/Applications/Blender.app/Contents/MacOS/Blender"; do
    if usable "$c"; then say "$c"; return 0; fi
  done
  return 1
}

hip_dir() {              # the unpacked HIP runtime's library folder, or nothing
  for f in "$BHOME"/rocm/root/usr/lib/*/libamdhip64.so; do
    [ -e "$f" ] && { dirname "$f"; return 0; }
  done
  return 1
}

gpu_vendor() {           # NVIDIA / AMD / Intel / Apple / none -- informational
  case "$(uname -s)" in Darwin) say "Apple (Metal)"; return ;; esac
  v=""
  for f in /sys/class/drm/card*/device/vendor; do
    [ -r "$f" ] && v="$v $(cat "$f")"
  done
  case "$v" in
    *0x10de*) say "NVIDIA (CUDA/OptiX: needs the NVIDIA driver)" ;;
    *0x1002*) say "AMD (HIP: needs the HIP runtime -- setup.sh --amd-gpu)" ;;
    *0x8086*) say "Intel (oneAPI: needs Intel's Level Zero runtime)" ;;
    *) say "none found (CPU)" ;;
  esac
}

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else return 2; fi
}

install_hip() {
  R="$BHOME/rocm"
  if hip_dir >/dev/null; then say "HIP runtime already in $R"; return 0; fi
  command -v apt-get >/dev/null 2>&1 && command -v dpkg >/dev/null 2>&1 || {
    say "no apt/dpkg here: install AMD's HIP runtime per AMD's ROCm docs, or render on the CPU"; return 0; }
  mkdir -p "$R/debs"
  # Ubuntu's own packages, DOWNLOADED and UNPACKED (dpkg -x) -- not installed.
  if ! ( cd "$R/debs" && apt-get download libamdhip64-7 libamd-comgr3 libhsa-runtime64-1 libhsakmt1 >/dev/null 2>&1 ); then
    say "this release has no HIP runtime packages under those names: Cycles will use the CPU"
    rm -rf "$R"; return 0
  fi
  for f in "$R"/debs/*.deb; do dpkg -x "$f" "$R/root"; done
  L="$(dirname "$(ls "$R"/root/usr/lib/*/libamdhip64.so.7* | head -1)")"
  ln -sf "$(cd "$L" && ls libamdhip64.so.7* | head -1)" "$L/libamdhip64.so"
  ln -sf libamdhip64.so "$L/libamdhip64.so.6"
  say "HIP runtime unpacked into $R"
}

install_blender() {
  if b="$(find_blender)"; then
    say "Blender already available: $b ($("$b" --version 2>/dev/null | head -1))"
    return 0
  fi
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) ;;
    *) say "Automatic install is for Linux x86-64 only. Install Blender $SERIES LTS from"
       say "https://www.blender.org/download/lts/ and, if it is not on PATH, export BLENDER_BIN=<its binary>."
       return 1 ;;
  esac
  command -v curl >/dev/null 2>&1 || { say "curl is needed to download Blender"; return 1; }
  sha256 /dev/null >/dev/null || { say "neither sha256sum nor shasum is on PATH: cannot verify the download"; return 1; }
  TAR="blender-$VER-linux-x64.tar.xz"
  BASE="https://download.blender.org/release/Blender$SERIES"
  mkdir -p "$(dirname "$BHOME")"
  TMP="$(mktemp -d "$(dirname "$BHOME")/.blender-dl.XXXXXX")"
  trap 'rm -rf "$TMP"' EXIT
  say "fetching $BASE/$TAR (about 350 MB)"
  curl -fL --progress-bar -o "$TMP/$TAR" "$BASE/$TAR"
  want="$(curl -fsL "$BASE/blender-$VER.sha256" | grep " $TAR\$" | cut -d' ' -f1 || true)"
  have="$(sha256 "$TMP/$TAR")"
  if [ -z "$want" ] || [ "$want" != "$have" ]; then
    say "sha256 mismatch (published '$want', downloaded '$have'): nothing installed"; return 1
  fi
  mkdir -p "$TMP/x"
  tar -xJf "$TMP/$TAR" -C "$TMP/x" --strip-components=1
  rm -rf "$BHOME.old"
  [ -d "$BHOME" ] && mv "$BHOME" "$BHOME.old"
  mv "$TMP/x" "$BHOME"
  if [ -d "$BHOME.old/rocm" ]; then mv "$BHOME.old/rocm" "$BHOME/rocm"; fi
  rm -rf "$BHOME.old"
  say "installed: $("$BHOME/blender" --version | head -1) in $BHOME"
}

case "${1:-}" in
  --where)
    find_blender && { hip_dir || true; } ;;
  --check)
    if b="$(find_blender)"; then
      gpu="$(gpu_vendor)"
      case "$gpu" in AMD*) hip_dir >/dev/null && gpu="AMD (HIP runtime ready)" ;; esac
      say "ok $("$b" --version 2>/dev/null | head -1) -- $b; GPU: $gpu"
    else
      say "missing"
    fi ;;
  --amd-gpu) install_blender && install_hip ;;
  "") install_blender ;;
  *) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
