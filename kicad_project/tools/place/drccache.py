"""KiCad's DRC (schematic parity on), run once per board state.

    import drccache
    run = drccache.start(pcb)            # starts kicad-cli now, or finds the cached report
    ...                                  # other work while it runs
    report, msg = run.result()           # the JSON report (None if none written); kicad-cli's stderr + stdout

A board's DRC report depends only on the files kicad-cli reads, so the report
is cached under `<board>/routing/.drc_cache/<key>.json`, keyed by the sha256 of
every one of them: the `.kicad_pcb`, the `.kicad_pro`, the `.kicad_dru`, every
`.kicad_sch` (the parity test reads the schematic), the project's library
tables and the symbols and footprints under `lib/` (the library-mismatch tests
read them), the user's global footprint table, and kicad-cli itself (path,
size and mtime: an upgrade is a new key). Change one byte of them and the next
run is a miss. The directory carries its own `.gitignore`; it is safe to
delete at any time, and `HW_DRC_CACHE=0` turns the cache off.

`start(..., cache=False)` is a plain run (a scratch copy no later run sees
again). Every run says on STDERR whether it ran kicad-cli or read the cache,
so a caller's stdout stays exactly what it was before the cache existed.
"""
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KEEP = 8          # newest reports kept per board

PROJECT_GLOBS = ("*.kicad_pro", "*.kicad_dru", "*.kicad_sch", "fp-lib-table", "sym-lib-table")
LIB_SUFFIXES = (".kicad_mod", ".kicad_sym")


def _cli_identity():
    exe = shutil.which("kicad-cli") or "kicad-cli"
    try:
        st = os.stat(exe)
        return f"{exe}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        return exe


def _global_tables():
    home = Path.home()
    pats = [home / ".config" / "kicad" / "*" / "fp-lib-table",
            home / "Library" / "Preferences" / "kicad" / "*" / "fp-lib-table"]
    if os.environ.get("APPDATA"):
        pats.append(Path(os.environ["APPDATA"]) / "kicad" / "*" / "fp-lib-table")
    return sorted(Path(p) for pat in pats for p in glob.glob(str(pat)))


def inputs(pcb, board_dir=None):
    """Every file the DRC of `pcb` reads, in a fixed order."""
    pcb = Path(pcb).resolve()
    d = Path(board_dir).resolve() if board_dir else pcb.parent
    files = [pcb]
    for pat in PROJECT_GLOBS:
        files += sorted(p for p in d.glob(pat) if p.resolve() != pcb)
    lib = d / "lib"
    if lib.is_dir():
        files += sorted(p for p in lib.rglob("*") if p.is_file() and p.suffix in LIB_SUFFIXES)
    return files + _global_tables()


def key(pcb, board_dir=None):
    h = hashlib.sha256()
    h.update(_cli_identity().encode())
    base = Path(board_dir).resolve() if board_dir else Path(pcb).resolve().parent
    for f in inputs(pcb, board_dir):
        try:
            rel = str(f.resolve().relative_to(base))
        except ValueError:
            rel = str(f)
        h.update(b"\0" + rel.encode() + b"\0")
        h.update(f.read_bytes())
    return h.hexdigest()


def cache_dir(pcb, board_dir=None):
    d = (Path(board_dir) if board_dir else Path(pcb).parent) / "routing" / ".drc_cache"
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n")
    return d


def _note(msg):
    print(f"drc: {msg}", file=sys.stderr, flush=True)


class Run:
    """One DRC, started at construction; `result()` waits for it."""

    def __init__(self, pcb, board_dir=None, cache=True):
        self.pcb = Path(pcb)
        self.cached = self.path = self.key = None
        if cache and os.environ.get("HW_DRC_CACHE", "1") != "0":
            self.key = key(pcb, board_dir)
            self.path = cache_dir(pcb, board_dir) / f"{self.key[:32]}.json"
            if self.path.exists():
                try:
                    self.cached = json.loads(self.path.read_text())
                except ValueError:
                    self.cached = None
        if self.cached is not None:
            return
        self.td = tempfile.TemporaryDirectory()
        self.out = Path(self.td.name) / "drc.json"
        self.t0 = time.monotonic()
        self.proc = subprocess.Popen(["kicad-cli", "pcb", "drc", "--schematic-parity", "--format", "json",
                                      "-o", str(self.out), str(self.pcb)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def result(self):
        if self.cached is not None:
            _note(f"{self.pcb.name}: report from the cache ({self.path.name[:12]}), no kicad-cli run")
            os.utime(self.path)
            return self.cached["report"], self.cached["msg"]
        so, se = self.proc.communicate()
        msg = (se or "") + (so or "")
        try:
            rep = json.loads(self.out.read_text()) if self.out.exists() else None
        finally:
            self.td.cleanup()
        _note(f"{self.pcb.name}: kicad-cli run, {time.monotonic() - self.t0:.1f} s"
              + ("" if self.path else " (not cached)"))
        if rep is not None and self.path is not None:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(key=self.key, pcb=self.pcb.name, report=rep, msg=msg)))
            os.replace(tmp, self.path)
            olds = sorted(self.path.parent.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for p in olds[KEEP:]:
                p.unlink(missing_ok=True)
        return rep, msg


def start(pcb, board_dir=None, cache=True):
    return Run(pcb, board_dir, cache)


def report(pcb, board_dir=None, cache=True):
    return Run(pcb, board_dir, cache).result()
