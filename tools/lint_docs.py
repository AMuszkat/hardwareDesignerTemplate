"""
Enforce the repository's documentation rules across the authored docs and
the generated artefacts.  Run via `studies/run_all.py --docs` or directly:

    .venv/bin/python tools/lint_docs.py

Checks (each one exists because the failure it catches is silent):
  1. one fact, one home -- no `##` heading repeated across the authored docs,
     and each (HISTORY excepted) opens with **Scope:** and **Not here:**
  2. system sheets (generators with SYSTEM = True) carry no component refdes
     anywhere in their text: FUNCTION names.  J-designators are allowed -- on
     a system sheet a connector's refdes IS its function name.
  3. nothing under design/ references history/archive/
  4. every diagram generator regenerates its sheet byte-identically (an SVG
     that differs was hand-edited, or the generator moved without a re-run)
  5. every board's handoff is fresh (publish.py --check --all)
  6. every generator's REFLECTS names the NEWEST STATUS.md Decided row by its
     id (D1, D2, ...), i.e. somebody opened it and checked it against the
     newest decision.  Ids, not dates: two decisions on one day are two rows.
     4 asks whether the SVG matches its generator; only 6 asks whether the
     generator matches the design.
  7. every OPEN_CLAIMS tag a generator declares is still an open item
     (`{open:<tag>}`) in STATUS.md's Open section.  6 catches a sheet that
     missed a NEW decision; 7 catches a sheet still drawing an item as open
     after it was CLOSED -- which 6 cannot see.  An item marked CLOSED or
     struck through (~~) no longer counts, and keeping its tag is flagged.

  8. every number SPECS.md sources from a study is guarded: a cited study
     `sNN_<name>` exists in studies/, and a cited headline key (`tag.metric_unit`)
     is in studies/baselines/headline.json -- so `run_all.py --check` fails
     the day the model stops producing the published number.
  9. STATUS.md's generated block is current (tools/status.py --check).

Notes (never a failure): template placeholders "(Fill in" and "(example" rows
still present; baseline keys no SPECS row cites.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
DIAG = os.path.join(HERE, "diagrams")
NOT_GENERATORS = {"sheet.py", "check_diagrams.py", "test_diagrams.py"}

AUTHORED = ["CLAUDE.md", "design/SPECS.md", "design/IMPLEMENTATION.md",
            "design/STATUS.md", "design/DIAGRAMS.md", "history/HISTORY.md"]
NO_SCOPE_HEADER = {"history/HISTORY.md"}
# Component designators that may not appear on a system sheet.  Tune it to the
# project's designator scheme; J (connectors) is deliberately absent.
REFDES_RE = re.compile(r"\b(?:U|Y|IC|Q|K)\d+\b")

probs, notes = [], []


def read(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return f.read()


def generators():
    """{file: {OUTPUT, SYSTEM, REFLECTS, OPEN_CLAIMS}} parsed, never imported."""
    out = {}
    for f in sorted(os.listdir(DIAG)):
        if not f.endswith(".py") or f in NOT_GENERATORS or f.startswith("_"):
            continue
        src = open(os.path.join(DIAG, f)).read()
        g = {}
        m = re.search(r'^OUTPUT\s*=\s*["\']([^"\']+)["\']', src, re.M)
        g["OUTPUT"] = m.group(1) if m else None
        m = re.search(r"^SYSTEM\s*=\s*(True|False)", src, re.M)
        g["SYSTEM"] = (m.group(1) == "True") if m else None
        m = re.search(r'^REFLECTS\s*=\s*"(D\d+)"', src, re.M)
        g["REFLECTS"] = m.group(1) if m else None
        m = re.search(r"^OPEN_CLAIMS\s*=\s*\[(.*?)\]", src, re.M | re.S)
        g["OPEN_CLAIMS"] = re.findall(r"[\"']([a-z0-9-]+)[\"']", m.group(1)) if m else None
        for k, v in g.items():
            if v is None:
                probs.append(f"tools/diagrams/{f}: no {k} = ... line (see tools/diagrams/system.py)")
        out[f] = g
    return out


def section(text, title_re):
    m = re.search(rf"^## {title_re}.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else None


GEN = generators()
STATUS = read("design/STATUS.md")

# 1 -- headings unique across authored docs; scope headers present
seen = {}
for rel in AUTHORED:
    if not os.path.exists(os.path.join(ROOT, rel)):
        probs.append(f"{rel}: missing")
        continue
    txt = read(rel)
    head = txt[:800]
    if rel not in NO_SCOPE_HEADER:
        for tag in ("**Scope:**", "**Not here:**"):
            if tag not in head:
                probs.append(f"{rel}: no {tag} line in its header")
    for m in re.finditer(r"^## +(.+?)\s*$", txt, re.M):
        h = re.sub(r"[^a-z0-9 ]", "", m.group(1).lower()).strip()
        if len(h) < 4:
            continue
        if h in seen and seen[h] != rel:
            probs.append(f"heading '{m.group(1)}' appears in both {seen[h]} and {rel}")
        seen[h] = rel
    if "(Fill in" in txt or "(date of the last edit)" in txt:
        notes.append(f"{rel} still has '(Fill in' template placeholders")
    n_ex = len(re.findall(r"\(example\b|EXAMPLE", txt)) if rel != "CLAUDE.md" else 0
    if n_ex:
        notes.append(f"{rel} still has {n_ex} example row(s) shipped with the template")

# 2 -- no refdes on the system sheets
for gen, g in GEN.items():
    if g["SYSTEM"] and g["OUTPUT"] and os.path.exists(os.path.join(ROOT, g["OUTPUT"])):
        svg = read(g["OUTPUT"])
        text = " ".join(re.findall(r">([^<]+)<", svg))
        hits = sorted(set(REFDES_RE.findall(text)))
        if hits:
            probs.append(f"{g['OUTPUT']}: refdes on a system sheet: {', '.join(hits)}")

# 3 -- design/ never references the archive
for dirp, _, files in os.walk(os.path.join(ROOT, "design")):
    for f in files:
        if f.endswith(".md") and "archive/" in open(os.path.join(dirp, f)).read():
            probs.append(f"{os.path.relpath(os.path.join(dirp, f), ROOT)} references "
                         f"history/archive/ -- current docs must not lean on superseded artefacts")

# 4 -- diagrams regenerate identically
for gen, g in GEN.items():
    if not g["OUTPUT"]:
        continue
    cur = os.path.join(ROOT, g["OUTPUT"])
    before = open(cur, "rb").read() if os.path.exists(cur) else None
    r = subprocess.run([PY, os.path.join(DIAG, gen)], capture_output=True, text=True)
    if r.returncode != 0:
        probs.append(f"tools/diagrams/{gen} failed: {(r.stderr or r.stdout).strip()[-160:]}")
        continue
    if before is None:
        probs.append(f"{g['OUTPUT']} was missing (now generated -- commit it)")
    elif open(cur, "rb").read() != before:
        probs.append(f"{g['OUTPUT']} was out of date with tools/diagrams/{gen} "
                     f"(now regenerated -- commit the fresh one)")

# 6 -- generators declare the decision date they were last read against
dec = section(STATUS, "Decided")
ids = [int(x) for x in re.findall(r"^\|\s*D(\d+)\s*\|\s*\d{4}-\d{2}-\d{2}\s*\|", dec or "", re.M)]
if not ids:
    probs.append("design/STATUS.md: no Decided row of the form `| D<n> | YYYY-MM-DD | ... |` "
                 "-- the REFLECTS check has nothing to compare to")
else:
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        probs.append(f"design/STATUS.md: decision id(s) used twice: {', '.join(f'D{i}' for i in dup)}")
    newest = max(ids)
    for gen, g in GEN.items():
        if not g["REFLECTS"]:
            continue
        r = int(g["REFLECTS"][1:])
        if r not in ids:
            probs.append(f"tools/diagrams/{gen}: REFLECTS {g['REFLECTS']} is not a Decided row "
                         f"in STATUS.md (the newest is D{newest})")
        elif r < newest:
            probs.append(f"tools/diagrams/{gen}: REFLECTS {g['REFLECTS']} but STATUS's newest "
                         f"decision is D{newest} -- open it, check the sheet against every "
                         f"decision since, then bump it")

# 7 -- open-item claims
opn = section(STATUS, "Open")
if opn is None:
    probs.append("design/STATUS.md: cannot find the Open section -- check 7 has no registry")
else:
    registry = set()
    for para in re.split(r"\n\s*\n", opn):
        tags = re.findall(r"\{open:([a-z0-9-]+)\}", para)
        if "~~" in para or re.search(r"\bCLOSED\b", para):
            for t in tags:
                probs.append(f"design/STATUS.md: item {{open:{t}}} is marked closed but still "
                             f"sits in Open with its tag -- move it to Decided and delete it here")
            continue
        registry.update(tags)
    for gen, g in GEN.items():
        for tag in g["OPEN_CLAIMS"] or []:
            if tag not in registry:
                probs.append(f"tools/diagrams/{gen}: draws {{open:{tag}}} but STATUS's Open "
                             f"section no longer lists it -- that item was CLOSED. Redraw the "
                             f"sheet with the decision, then drop the tag")

# 5 -- handoff freshness
boards = [f for f in os.listdir(os.path.join(HERE, "handoff", "boards"))
          if f.endswith(".py") and not f.startswith("_")] \
    if os.path.isdir(os.path.join(HERE, "handoff", "boards")) else []
if boards:
    r = subprocess.run([PY, os.path.join(HERE, "handoff", "publish.py"), "--check", "--all"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        stale = [ln for ln in r.stdout.splitlines() if "STALE" in ln] or \
            [" ".join((r.stdout + r.stderr).split())[:200]]
        probs += ["handoff " + ln.strip() for ln in stale]

# 8 -- every study-sourced number in SPECS is guarded by the baseline
import json                                                       # noqa: E402
STUD = os.path.join(ROOT, "studies")
bl_path = os.path.join(STUD, "baselines", "headline.json")
baseline = json.load(open(bl_path)) if os.path.exists(bl_path) else {}
specs = read("design/SPECS.md") if os.path.exists(os.path.join(ROOT, "design/SPECS.md")) else ""
cited = set()
for name in sorted(set(re.findall(r"`(s\d+_\w+)`", specs))):
    if not os.path.exists(os.path.join(STUD, name + ".py")):
        probs.append(f"design/SPECS.md cites study `{name}`, which is not in studies/")
for row in specs.splitlines():
    if not re.search(r"`s\d+_\w+`", row):
        continue
    for key in re.findall(r"`([a-z][a-z0-9]*\.[a-z0-9_]+)`", row):
        cited.add(key)
        if key not in baseline:
            probs.append(f"design/SPECS.md cites headline `{key}`, which is not in "
                         f"studies/baselines/headline.json -- nothing guards that number "
                         f"(add it to the study's headline(), then run_all.py --check --bless)")
uncited = sorted(set(baseline) - cited)
if uncited and specs:
    notes.append(f"{len(uncited)} of {len(baseline)} baseline keys are not cited by a SPECS row "
                 f"(guarded, but published nowhere): {', '.join(uncited[:4])}"
                 + (", ..." if len(uncited) > 4 else ""))

# 9 -- STATUS.md's generated block is current
r = subprocess.run([PY, os.path.join(HERE, "status.py"), "--check"], capture_output=True, text=True)
if r.returncode != 0:
    probs.append((r.stdout.strip() or r.stderr.strip() or "tools/status.py --check failed")[-200:])

for n in notes:
    print("NOTE:", n)
if probs:
    for p in probs:
        print("DOC LINT:", p)
    sys.exit(1)
print(f"doc lint OK -- {len(AUTHORED)} authored docs, {len(GEN)} generated sheet(s), "
      f"{len(boards)} board handoff(s) current")
