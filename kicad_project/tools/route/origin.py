#!/usr/bin/env python3
"""Where a plan came from, and whether that is still true.  Board-free, standard library.

A plan file is copper waiting to be laid: `route.py apply` writes it to the board.  Nothing in
the file says which spec it was routed from or which board it was routed on, so a plan left at
its path by an EARLIER run reads exactly like the result of the run that just failed (board1's
R2: three minutes to "no solution", and at the output path a plan from before the track widths
were corrected).  This module is the record that makes the two tell apart, and the three things
built on it:

  1. THE ORIGIN RECORD.  A planner that writes `<name>.json` writes `<name>.origin.json` beside
     it (`write`): the plan's own copper hash, the spec's path, hash and normalised content, the
     board file's name and hash, the time and the command.  A SIDECAR, not keys inside the plan:
     the plan's bytes stay what they were (the harness hashes them, and a plan is also read by
     view.py, a `plane.zone`, the reviewers).

  2. THE TEST (`judge`, run by `route.py check` and `apply` before any gate).  Three questions,
     each a refusal unless `--accept-stale` names it:
       plan   is the copper in the file the copper the run wrote?  (the keys the planner wrote,
              "why" apart: a hand-added "accept" or "edge_ok" is not a change; a moved point, or
              another file copied over the path, is)
       spec   is the spec what it was?  Compared NORMALISED: parsed, "why" and "plan" and
              "_"-prefixed keys dropped, keys sorted, 1 and 1.0 the same number -- so reformatting
              the spec or rewording its reason does not refuse a plan, and a changed width does.
              The record keeps the normalised spec, so the refusal names WHAT changed.
       board  is the board what it was?  By CONTENT hash (sha256 of the .kicad_pcb), not
              modification time and size: a copy to a scratch folder, a `touch`, a git checkout
              and a restore from a backup all keep the content and change the time (false
              refusals teach the flag), and a save that changes one coordinate keeps the size.
              A board file is a few megabytes; hashing it costs milliseconds.
              One case is NOT a refusal: the board moved only by steps route.py itself recorded
              (`step`: every apply and rip appends before/after hashes to
              routing/board_steps.jsonl).  Three plans routed on one board and applied one after
              the other is how a bus on three layers is done; the second is judged by `check`
              against the first's copper, which is the test that matters.  The gate says which
              steps lie between, and refuses anything it cannot account for (a placement moved,
              a pour refilled, an edit in KiCad).

  3. RETIRING (`retire`).  A planner moves the plan already at its output path aside as
     `<name>.stale.json` (its record as `<name>.stale.origin.json`) when it STARTS, not when it
     fails: a run killed after an hour never reaches its failure code.  Moved, not deleted --
     it may be the only copy of a route that took an hour, and the run replacing it has not
     produced anything yet -- and unchanged, so renaming both files back restores it exactly
     (the spec and board tests then judge it like any plan).  `route.py` refuses a
     `.stale.json` by its name.  A run that writes its plan deletes the retired one.

A plan with no origin record (written by hand, by another planner, or before this existed)
is applied with one line saying it was not tested.
"""
import hashlib
import json
import os
import time
from pathlib import Path

LEDGER = "board_steps.jsonl"          # <board>/routing/: one line per apply / rip
WHAT = ("retired", "plan", "spec", "board")      # what --accept-stale can name


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canon(v):
    """Numbers as floats (a spec's 1 and 1.0 are one width), everything else as it is."""
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        return {str(k): _canon(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    return str(v)


def _sha_obj(v):
    return hashlib.sha256(json.dumps(_canon(v), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normal(spec):
    """The part of a spec that decides the route: no "why" (prose), no "plan" (where the result
    goes), no "_..." key (a comment)."""
    return _canon({k: v for k, v in spec.items() if k not in ("why", "plan") and not str(k).startswith("_")})


def spec_sha(spec):
    return _sha_obj(normal(spec))


def _plan_keys(plan):
    return sorted(k for k in plan if k != "why")


def plan_sha(plan, keys):
    return _sha_obj({k: plan.get(k) for k in keys})


def sidecar(plan_path):
    return Path(plan_path).with_suffix(".origin.json")


def stale_path(plan_path):
    return Path(plan_path).with_suffix(".stale.json")


def is_retired(plan_path):
    return Path(plan_path).name.endswith(".stale.json")


def _rel(path, board_dir):
    path, board_dir = Path(path).resolve(), Path(board_dir).resolve()
    try:
        return str(path.relative_to(board_dir))
    except ValueError:
        return str(path)


def write(plan_path, plan, spec_path, spec, board_pcb, board_sha, board_dir, by=""):
    """The record beside a plan just written.  board_sha is the hash taken when the planner READ
    the board (the plan is true of that content, whatever the file holds by the time the run
    ends).  Written whole and renamed, like the plan's partial."""
    keys = _plan_keys(plan)
    rec = {"origin": 1,
           "plan": {"file": Path(plan_path).name, "keys": keys, "sha256": plan_sha(plan, keys)},
           "spec": {"path": _rel(spec_path, board_dir), "sha256": spec_sha(spec), "normal": normal(spec)},
           "board": {"file": Path(board_pcb).name, "sha256": board_sha},
           "planned": time.strftime("%Y-%m-%d %H:%M:%S"),
           "by": by}
    sc = sidecar(plan_path)
    tmp = sc.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=1) + "\n")
    os.replace(tmp, sc)
    return sc


def read(plan_path):
    sc = sidecar(plan_path)
    try:
        rec = json.loads(sc.read_text())
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("origin") else None


def retire(plan_path):
    """Move the plan at this path (and its record) aside; the stale path, or None when there was
    no plan.  The plan first: a kill between the two renames leaves a record without a plan,
    which the next run removes, never a plan without its record."""
    p = Path(plan_path)
    if is_retired(p):
        return None
    st, sc = stale_path(p), sidecar(p)
    try:
        os.replace(p, st)
    except FileNotFoundError:
        sc.unlink(missing_ok=True)             # a record with no plan describes nothing
        return None
    if sc.exists():
        os.replace(sc, sidecar(st))
    else:
        sidecar(st).unlink(missing_ok=True)    # an older retired plan's record is not this one's
    return st


def drop_retired(plan_path):
    """A plan was written: the one retired for it is superseded.  The stale path, or None."""
    st = stale_path(plan_path)
    if not st.exists():
        sidecar(st).unlink(missing_ok=True)
        return None
    st.unlink()
    sidecar(st).unlink(missing_ok=True)
    return st


# -- the board's recorded steps ----------------------------------------------------------------
def step(board_dir, op, group, before, after, **more):
    """One line in routing/board_steps.jsonl: route.py changed the board from `before` to `after`
    (content hashes).  Append-only: a manifest is renamed by rip and rewritten by --force, and
    the trail must outlive both."""
    if before == after:
        return
    f = Path(board_dir) / "routing" / LEDGER
    f.parent.mkdir(exist_ok=True)
    row = dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), op=op, group=group, before=before, after=after, **more)
    with open(f, "a") as fh:
        fh.write(json.dumps(row) + "\n")


def steps(board_dir):
    out = []
    try:
        lines = (Path(board_dir) / "routing" / LEDGER).read_text().splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue                            # a torn last line: the step is unknown, not fatal
        if isinstance(r, dict) and r.get("before") and r.get("after"):
            out.append(r)
    return out


def trail(board_dir, start, now):
    """The recorded steps that lead from board content `start` to `now`, oldest first; [] when
    they are the same; None when no chain of recorded steps connects them."""
    if start == now:
        return []
    nxt = {}
    for r in steps(board_dir):
        nxt.setdefault(r["before"], []).append(r)
    seen, queue = {start}, [(start, [])]
    while queue:
        at, path = queue.pop(0)
        for r in nxt.get(at, []):
            if r["after"] == now:
                return path + [r]
            if r["after"] not in seen:
                seen.add(r["after"])
                queue.append((r["after"], path + [r]))
    return None


# -- the test ----------------------------------------------------------------------------------
def _show(v):
    s = json.dumps(v, separators=(",", ":")) if not isinstance(v, str) else repr(v)
    return s if len(s) <= 40 else s[:37] + "..."


def spec_diff(old, new, top=6):
    """What changed between two normalised specs, as short strings: a top-level key with both
    values, or `nets.NAME` / `field.ref` one level down."""
    out = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        if isinstance(a, dict) and isinstance(b, dict):
            sub = [s for s in sorted(set(a) | set(b)) if a.get(s) != b.get(s)]
            out.append(f"{k}: " + ", ".join(sub[:5]) + (f" (+{len(sub) - 5} more)" if len(sub) > 5 else ""))
        elif k not in old:
            out.append(f"{k} added ({_show(b)})")
        elif k not in new:
            out.append(f"{k} removed (was {_show(a)})")
        else:
            out.append(f"{k} {_show(a)} -> {_show(b)}")
    return out[:top] + ([f"(+{len(out) - top} more keys)"] if len(out) > top else [])


def judge(plan_path, board_dir, board_pcb):
    """The plan against its origin record, now.  Returns dict(
         origin  the record, or None,
         lines   what to print: one line each for plan, spec and board when there is a record,
         stale   {what: one-line reason} for every test that failed ("retired", "plan", "spec",
                 "board") -- empty when the plan is what its record says and nothing moved)."""
    p = Path(plan_path)
    stale, lines = {}, []
    if is_retired(p):
        stale["retired"] = (f"{p.name} was moved aside by a later run of its planner (which then gave "
                            "no plan, or it would have deleted this)")
        lines.append(f"   retired  {stale['retired']}")
    rec = read(p)
    if rec is None:
        if not stale:
            lines.append(f"note: {p.name} has no origin record ({sidecar(p).name}): written by hand, by another "
                         "tool, or before plans recorded one -- not tested against its spec or its board")
        return dict(origin=None, lines=lines, stale=stale)
    h = lambda s: (s or "?")[:12]
    # plan
    try:
        plan = json.loads(p.read_text())
        now = plan_sha(plan, rec["plan"].get("keys") or _plan_keys(plan))
    except (OSError, ValueError):
        now = None
    if now == rec["plan"].get("sha256"):
        lines.append(f"   plan   {p.name}: as written {rec.get('planned', '?')}")
    else:
        stale["plan"] = (f"{p.name} is not the copper the run wrote ({h(rec['plan'].get('sha256'))} then, "
                         f"{h(now)} now): edited by hand, or another file was moved over it")
        lines.append(f"   plan   {stale['plan']}")
    # spec
    sp = Path(rec["spec"]["path"])
    sp = sp if sp.is_absolute() else Path(board_dir) / sp
    try:
        cur = json.loads(sp.read_text())
    except (OSError, ValueError) as e:
        cur = None
        stale["spec"] = f"{rec['spec']['path']} cannot be read now ({type(e).__name__}): the plan's spec is gone"
    if cur is not None:
        now = spec_sha(cur)
        if now == rec["spec"]["sha256"]:
            lines.append(f"   spec   {rec['spec']['path']}: unchanged ({h(now)})")
        else:
            diff = spec_diff(rec["spec"].get("normal") or {}, normal(cur)) if rec["spec"].get("normal") else []
            stale["spec"] = (f"{rec['spec']['path']} CHANGED since the plan was made "
                             f"({h(rec['spec']['sha256'])} then, {h(now)} now)"
                             + (": " + "; ".join(diff) if diff else ""))
    if "spec" in stale:
        lines.append(f"   spec   {stale['spec']}")
    # board
    now = sha_file(board_pcb)
    was = rec["board"]["sha256"]
    via = trail(board_dir, was, now)
    name = Path(board_pcb).name
    if via == []:
        lines.append(f"   board  {name}: unchanged ({h(now)})")
    elif via:
        lines.append(f"   board  {name}: moved since ({h(was)} then, {h(now)} now) only by recorded steps -- "
                     + ", ".join(f"{r.get('op')} {r.get('group')} ({r.get('at', '?')[:16]})" for r in via)
                     + " -- so the check judges the plan against that copper")
    else:
        stale["board"] = (f"{name} CHANGED since the plan was made ({h(was)} then, {h(now)} now) by something "
                          f"route.py did not record (a placement, a pour, another tool, an edit in KiCad)")
        lines.append(f"   board  {stale['board']}")
    return dict(origin=rec, lines=lines, stale=stale)
