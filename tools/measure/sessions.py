"""
Where a session's time went: read a project's Claude Code transcripts and
write out/sessions/report.md (and report.json).

    .venv/bin/python tools/measure/sessions.py                        # this project, every session
    .venv/bin/python tools/measure/sessions.py --since 2026-11-06     # sessions and time from that day on
    .venv/bin/python tools/measure/sessions.py --project ~/otherProject --out /tmp/x
    .venv/bin/python tools/measure/sessions.py --transcripts DIR      # a folder of *.jsonl, read directly

A transcript is a JSON-lines file Claude Code keeps per session under
~/.claude/projects/<slug>/, the slug being the session's working directory
with every non-alphanumeric character replaced by "-".  A board session runs
in <project>/kicad_project, so one project owns several such folders; they
are found by the slug prefix and confirmed by the "cwd" the records carry.

Three steps, each a function that can be used on its own:

  parse(path)      one transcript -> records: the model turns, every tool call
                   (start, end, name, input, background, whether it sat on a
                   permission prompt), every wait with its cause, the relay
                   lines sent and received, the compactions.
  classify(S)      the records -> a timeline: the session's span cut at every
                   record's timestamp, each piece given exactly ONE class.
  summarise(...)   the timeline -> the tables of the report.

THE RULE THAT MAKES THE NUMBERS ADD UP.  Wall-clock is classified by the
UNION of the intervals, never by their sum.  A span is cut at every record's
timestamp; each piece takes the highest class that covers it:

    prompt stall > background wait > peer wait > human wait > tool > model

so the classes sum to the span exactly, and a call cannot be counted twice.
The case that made this necessary: the model sends an Edit and a Bash call in
one batch, the Edit waits six hours on a permission prompt, and the Bash
call's result is written when the batch ends.  Summed, that is twelve hours,
six of them "tool time" of a command that ran for two seconds.  Here the six
hours are one prompt stall, and the Bash call owns only the pieces no stalled
call covers (usually none).  Two tool calls that overlap share the time the
same way: each piece has one owner, so the per-family tool times also add up
to the tool class.

What is model time: every piece no interval covers (the model generating, a
mid-turn compaction).  One such piece longer than --gap-min (15) minutes is
not the model: it is an API outage, a suspended machine or a killed session,
and is reported as its own class, "no record".

THE REPORT HOLDS NUMBERS AND NAMES OF TOOLS AND COMMANDS ONLY.  Transcripts
are private: prompts, messages, file contents.  Nothing of a message's text
reaches the report -- a relay line contributes its tokens (board1, RV7, PASS,
Q12, a revision number) and nothing else; a Bash command contributes the
script it ran (tools/place/place.py), a subcommand only when that word is a
string literal in that script's own source, and for anything else the name of
the program.  The report can be pasted into a pull request.

Standard library only.  Read-only on the transcripts.
"""
import argparse
import bisect
import datetime
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# The classes, highest priority first.  "gap" is not painted by any interval:
# it is an uncovered piece too long to be the model (see the docstring).
PRIORITY = ["prompt", "background", "peer", "human", "tool", "model"]
CLASSES = PRIORITY + ["gap"]
RANK = {c: len(PRIORITY) - i for i, c in enumerate(PRIORITY)}
LABEL = {
    "prompt": "permission-prompt stall",
    "background": "waiting on a background job",
    "peer": "waiting on the other session",
    "human": "waiting on the user",
    "tool": "foreground tools",
    "model": "model turns",
    "gap": "no record",
}
GAP_MIN = 15.0          # an uncovered piece longer than this is "no record"
STALL_S = 60.0          # a prompt is counted as a "stall" above this
CHAIN_GAP_MIN = 120.0   # two questions nearer than this are one chain
CHAIN_MIN = 3           # a chain is reported from this many questions on

# What ended an idle period -> the class of the wait.  "compaction" and
# "local" (a local command's output, a hook's note) are not causes: an idle
# period that ends in one of them is folded into the wait that follows.
CAUSE = {"human": "human", "peer": "peer", "idle-notice": "peer",
         "task": "background", "subagent": "background"}

# tools/<dir>/ -> the stage of the work.  A folder not listed is its own
# stage ("tools/<dir>"), so a new tool folder shows up without an edit here.
DIR_STAGE = {
    "sch": "schematic", "fp": "footprints", "board_setup": "board setup",
    "board_scaffold": "board setup", "place": "placement", "route": "routing",
    "freerouting": "routing", "copper": "copper", "render": "render",
    "fab": "fab output", "handoff": "handoff", "review": "review",
    "parts": "parts", "diagrams": "diagrams", "measure": "measure",
    "setup": "setup", "session": "session tools", "template": "giveback",
}
OWN = "own scripts"     # <board>/build/*.py and ad-hoc pcbnew
NO_STAGE = "(none yet)"
# Tools whose call returns at once: beside a slow call in one batch their
# result is written late, so the slow call owns the shared time.
INSTANT = {"Read", "Edit", "Write", "Grep", "Glob", "NotebookEdit", "ToolSearch",
           "TodoWrite", "Skill", "SendMessage", "TaskOutput", "BashOutput"}
# Tools that are a question to the user, not a permission prompt.
ASKS_USER = {"AskUserQuestion", "ExitPlanMode"}
GIT_VERBS = {"status", "diff", "log", "add", "commit", "push", "pull", "fetch",
             "merge", "rebase", "checkout", "switch", "branch", "stash", "show",
             "restore", "reset", "worktree", "remote", "tag", "clone", "mv", "rm",
             "cherry-pick", "rev-parse", "ls-files", "blame", "apply"}
WRAPPERS = {"timeout", "time", "nohup", "sudo", "env", "nice", "stdbuf", "exec"}
SAFE = re.compile(r"[A-Za-z0-9_.:+*()/ -]{1,60}\Z")


# ---------------------------------------------------------------- small things

def ts(s):
    """An ISO timestamp of a record -> seconds since the epoch (UTC)."""
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def when(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%m-%d %H:%M")


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def instant(s, end=False):
    """--since / --until: YYYY-MM-DD or YYYY-MM-DDTHH:MM, UTC.  A bare date as
    --until means the end of that day, so `--until 2026-11-09` includes it."""
    s = s.strip()
    try:
        if re.fullmatch(r"\d{4}-\d\d-\d\d", s):
            d = datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
            return (d + datetime.timedelta(days=1 if end else 0)).timestamp()
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"sessions.py: not a date (YYYY-MM-DD or YYYY-MM-DDTHH:MM, UTC): {s}")
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


def safe(name, other="(other)"):
    """A name that may go into the report: a short token of plain characters.
    Anything else -- a sentence, a quoted string -- becomes `other`."""
    name = str(name or "")
    return name if SAFE.match(name) else other


def pct(xs, p):
    """The p-th percentile of a list, interpolated; 0 for an empty one."""
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = p / 100.0 * (len(xs) - 1)
    i = int(k)
    j = min(i + 1, len(xs) - 1)
    return xs[i] + (xs[j] - xs[i]) * (k - i)


def median(xs):
    return pct(xs, 50)


def slug(path):
    """Claude Code's folder name for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


# ------------------------------------------------- naming a call: family, stage

class Subcommands:
    """Is this word a subcommand of that script?  Yes only when the word is a
    string literal in the script's own source -- so `place.py labels` is
    reported and `fetch.py <a part number>` or `search.py <a query>` never is.
    Without a project to look the script up in, no subcommand is reported."""

    def __init__(self, project):
        self.project = project
        self.cache = {}

    def words(self, tooldir, script):
        key = (tooldir, script)
        if key not in self.cache:
            found = set()
            if self.project:
                for base in ("kicad_project/tools", "tools"):
                    p = os.path.join(self.project, base, tooldir, script)
                    try:
                        src = open(p, errors="replace").read()
                    except OSError:
                        continue
                    found = set(re.findall(r"""["']([a-z][a-z_-]{1,24})["']""", src))
                    break
            self.cache[key] = found
        return self.cache[key]

    def of(self, tooldir, script, rest):
        """The subcommand among the words after the script, or ''."""
        toks = rest.split()[:8]
        i = 0
        while i < len(toks):
            t = toks[i]
            if t in ("--board", "-b", "--project", "--dir"):
                i += 2
                continue
            if re.fullmatch(r"[a-z][a-z_-]{1,24}", t) and t in self.words(tooldir, script):
                return t
            return ""
        return ""


INTERPRETER = re.compile(r"(python[\d.]*|pypy3?|kpy|bash|sh|zsh|node|perl|ruby)\Z")
HEREDOC = re.compile(r"(?<!<)<<-?(?!<)\s*(['\"\\]?)(\w+)\1?")
INLINE = re.compile(r"(?<!<)<<-?(?!<)\s*['\"\\]?\w"
                    r"|(?<![\w/.-])(?:python[\d.]*|kpy|bash|sh|node|perl|ruby)\s+(?:-\w+\s+)*-\w*c\s")
LEAD_CD = re.compile(r"\s*cd\s+(\"[^\"]+\"|'[^']+'|\S+)\s*(?:&&|;|\n)")
TOOL_PATH = re.compile(r"(?:^|[\s/\"'=(:])tools/([A-Za-z_]\w*)/")
ASSIGN = re.compile(r"[A-Za-z_]\w*=")
OPENERS = {"do", "then", "else", "elif", "if", "{", "!"}
KEYWORDS = {"for", "case", "done", "fi", "esac", "in", "function", "}", "select"}


def without_heredocs(text):
    """A shell text with every heredoc BODY cut out: the line that opens the
    heredoc stays, the lines up to its terminator go, and what follows the
    terminator stays.  `cat > x.py <<'EOF' ... EOF` followed by a run of
    x.py is then two commands, and the second one names the call; a body's
    own text is never read as commands.  An unterminated heredoc drops the
    rest of the text."""
    out, pos = [], 0
    while True:
        h = HEREDOC.search(text, pos)
        if not h:
            out.append(text[pos:])
            break
        eol = text.find("\n", h.end())
        if eol < 0:
            out.append(text[pos:])
            break
        out.append(text[pos:eol + 1])
        end = re.compile(r"^[ \t]*" + re.escape(h.group(2)) + r"[ \t]*$", re.M).search(text, eol + 1)
        if not end:
            break
        pos = end.end()
    return "".join(out)


def split_commands(text):
    """A shell line -> its simple commands: cut at ; | & && || ( ) and
    newlines that stand outside quotes.  Not a shell parser -- enough to find
    the word each command starts with."""
    out, cur, quote, i = [], [], None, 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1:i + 2]
        if quote:
            cur.append(ch)
            if ch == "\\" and quote == '"' and nxt:
                cur.append(nxt)
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            cur.append(ch)
        elif ch == "\\" and nxt:
            cur.append(" " if nxt == "\n" else ch + nxt)      # a continued line is one command
            i += 1
        elif ch == "&" and (nxt == ">" or (cur and cur[-1] == ">")):
            cur.append(ch)                                   # 2>&1, &> are redirections
        elif ch in ";|&\n()":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return [c.strip() for c in out if c.strip()]


def command_words(piece):
    """A simple command's words with what stands before the program dropped:
    VAR=value, `do`/`then`/`if`, and wrappers such as `timeout 600`."""
    toks = piece.split()
    while toks:
        t = toks[0]
        if t in OPENERS or ASSIGN.match(t):
            toks = toks[1:]
        elif os.path.basename(t) in WRAPPERS:
            toks = toks[1:]
            while toks and (toks[0].startswith("-") or ASSIGN.match(toks[0])
                            or re.fullmatch(r"[\d.]+[smhd]?", toks[0])):
                toks = toks[1:]
        else:
            break
    return toks


def stage_of_path(path):
    """The stage a path belongs to, or None: tools/<dir>/ by the table above,
    a board folder's build/ as the session's own scripts."""
    m = TOOL_PATH.search(path)
    if m:
        return DIR_STAGE.get(m.group(1), "tools/" + m.group(1))
    if re.search(r"(?:^|/)kicad_project/[^/]+/build/", path) or re.match(r"build/", path):
        return OWN
    if re.search(r"(?:^|/)studies/", path):
        return "studies"
    return None


def name_script(full, rest, subs):
    """A script that is RUN (its path made absolute where it could be) ->
    (family, detail, stage).  Only the script's own name is ever taken from
    the path; a script that is no tool of the repository is the session's
    own, whatever folder it lies in, and is not named."""
    m = re.search(r"(?:^|/)tools/([A-Za-z_]\w*)/([\w-]+\.(?:py|sh))\Z", full)
    if m:
        d, s = m.group(1), m.group(2)
        return f"{d}/{s}", subs.of(d, s, rest), DIR_STAGE.get(d, "tools/" + d)
    m = re.search(r"(?:^|/)tools/([\w-]+\.(?:py|sh))\Z", full)
    if m:
        return "tools/" + m.group(1), "", None
    m = re.search(r"(?:^|/)kicad_project/[^/]+/build/([\w-]+)\.(?:py|sh)\Z", full) \
        or re.match(r"(?:[\w.-]+/)?build/([\w-]+)\.(?:py|sh)\Z", full)
    if m:
        return "build/*.py", m.group(1), OWN
    m = re.search(r"(?:^|/)studies/([\w-]+)\.py\Z", full)
    if m:
        return "studies", m.group(1), "studies"
    m = re.search(r"(?:^|/)tests/([\w-]+)\.py\Z", full)
    if m:
        return "tests", m.group(1), None
    m = re.search(r"(?:^|/)(verify|setup)\.sh\Z", full)
    if m:
        return m.group(1) + ".sh", "", None
    return "other script", "", OWN


def name_bash(cmd, cwd, subs):
    """A Bash command -> (family, detail, stage), by what it RUNS.

    The command is cut into its simple commands; the first one that runs a
    script, an inline script (an interpreter fed a heredoc, `-c` or stdin),
    kicad-cli, or waits in a poll loop (`until ...; do sleep`) names the
    call.  If none does, the first program names it (`git status`, `grep`).
    A path that is only an ARGUMENT names nothing: `grep x tools/route/route.py`
    is a grep, not a run of route.py.  A heredoc's body is not read for
    commands -- only, for an inline script, for the tool folder it works on,
    which gives it its stage; the commands AFTER its terminator are read, so
    a script written with `cat > x.py <<EOF` and run in the same call is
    named by the run.  A program that only writes or prints (`cat`, `echo`)
    never names a call that goes on to run something."""
    base = cwd or ""
    m = LEAD_CD.match(cmd)
    if m:
        base = os.path.join(base, os.path.expanduser(m.group(1).strip("\"'")))
    body = cmd[m.end():] if m else cmd
    head = without_heredocs(body)

    def inline(prog):
        t = TOOL_PATH.search(cmd)
        stage = DIR_STAGE.get(t.group(1), "tools/" + t.group(1)) if t else \
            (OWN if re.search(r"\bkpy\b|\bpcbnew\b", cmd) else None)
        return "inline script", prog, stage

    first = None
    for n, piece in enumerate(split_commands(head)):
        toks = command_words(piece)
        if not toks:
            continue
        b = os.path.basename(toks[0].strip("\"'"))
        if toks[0] in ("while", "until"):
            if re.search(r"\bsleep\b", body):
                return "poll loop", "", None
            continue
        if toks[0] in KEYWORDS:
            continue
        if b == "sleep" and n == 0:
            return "poll loop", "", None
        if INTERPRETER.match(b):
            args, j = toks[1:], 0
            while j < len(args) and args[j].startswith("-") and args[j] != "-":
                if args[j] == "-m" and j + 1 < len(args):
                    return "python -m", safe(args[j + 1].split(".")[0], ""), None
                if re.fullmatch(r"-[A-Za-z]*c", args[j]):
                    return inline(b)
                j += 2 if args[j] in ("-W", "-X") else 1
            if j >= len(args) or args[j] == "-" or args[j][0] in "<>":
                return inline(b)                         # the script comes on stdin
            tok = os.path.expanduser(args[j].strip("\"'"))
            full = os.path.normpath(tok if os.path.isabs(tok) else os.path.join(base, tok))
            return name_script(full, " ".join(args[j + 1:]), subs)
        if re.search(r"\.(?:py|sh)\Z", b):
            tok = os.path.expanduser(toks[0].strip("\"'"))
            full = os.path.normpath(tok if os.path.isabs(tok) else os.path.join(base, tok))
            return name_script(full, " ".join(toks[1:]), subs)
        if b == "kicad-cli":
            sub = []
            for t in toks[1:3]:
                if not re.fullmatch(r"[a-z][a-z-]*", t):
                    break
                sub.append(t)
            return "kicad-cli", " ".join(sub), None
        if first is None:
            if b == "git":
                first = ("git", next((t for t in toks[1:5] if t in GIT_VERBS), ""), None)
            else:
                first = ("other Bash", b if re.fullmatch(r"[A-Za-z0-9_.+-]{1,24}", b) else "", None)
    return first or ("other Bash", "", None)


def name_call(name, inp, cwd, subs):
    """Any tool call -> (family, detail, stage).  The stage is taken from the
    path a command runs or a file tool writes; a call without one inherits the
    stage in force (see classify)."""
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        return name_bash(str(inp.get("command") or ""), cwd, subs)
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        server, tool = (parts + ["", ""])[1:3]
        stage = None
        if re.search(r"schematic|symbol|sheet|wire|label|junction|erc|no_connect|power", tool):
            stage = "schematic"
        elif re.search(r"footprint|track|via|zone|pcb|board|drc|route|pad|layer", tool):
            stage = "pcb (mcp)"
        return "mcp " + safe(server), safe(tool, ""), stage
    if name in ("Agent", "Task"):
        kind = safe(inp.get("subagent_type") or "general-purpose", "")
        return "Agent", kind, ("review" if "review" in kind else None)
    if name == "Skill":
        return "Skill", safe(inp.get("skill"), ""), None
    stage = None
    if name in ("Edit", "Write", "NotebookEdit"):
        stage = stage_of_path(str(inp.get("file_path") or inp.get("notebook_path") or ""))
    return safe(name), "", stage


# ------------------------------------------------------------- the relay lines

Q_IDS = r"Q\d+(?:\s*,\s*Q\d+)*"
RX_VERDICT = re.compile(r"review\s+((?:RV|M)\d+)[^:\n]{0,60}:\s*(PASS|CHANGES)")
RX_REQUEST = re.compile(r"\s*board\d+\s+((?:RV|M)\d+)\s+ready\b(?:\W+(schematic|pcb)\b)?")
RX_ANSWER = re.compile(r"answers\s+(" + Q_IDS + r")|(" + Q_IDS + r")\s+(?:re-)?answered")
RX_ASK = re.compile(r"\bboard\d+\s+(" + Q_IDS + r")\s+((?:reopened\s+)?(?:BLOCKING|not blocking|waits on the user)|reopened)")


def relay_event(text):
    """The first line of a message between sessions -> its tokens, as
    kicad_project/RELAY_PROTOCOL.md writes them:

        boardN RVn ready -- schematic|pcb: ...                 request
        boardN handoff rev R -- review RVn: PASS|CHANGES (k must-fix)   verdict
        boardN Qn[,Qm] BLOCKING|not blocking -- ...            asks
        boardN Qn reopened -- ... / boardN Qn waits on the user          asks
        boardN handoff rev R -- answers Qn[,Qm]; ...           answers

    One line may carry several (a verdict that answers a question, a review
    request that rings a question too), so each is its own field; `kind` is
    the first that applies and names the line in the waits table.  The dict
    holds these tokens and nothing else: the text is dropped here."""
    line = (text or "").strip().split("\n", 1)[0]
    ev = {"kind": "other"}
    b = re.match(r"\s*(board\d+)\b", line) or re.search(r"\b(board\d+)\b", line)
    if b:
        ev["board"] = b.group(1)
    r = re.search(r"handoff rev\s+(\d+)", line)
    if r:
        ev["rev"] = int(r.group(1))
    m = RX_VERDICT.search(line)
    if m:
        ev["review"], ev["verdict"] = m.group(1), m.group(2)
        k = re.search(r"\((\d+)\s+must-fix", line)
        if k:
            ev["must_fix"] = int(k.group(1))
    m = RX_REQUEST.match(line)
    if m:
        ev["request"] = m.group(1)
        ev["what"] = m.group(2) or ""
    ids = []
    for m in RX_ANSWER.finditer(line):
        ids += re.findall(r"Q\d+", m.group(1) or m.group(2))
    if ids:
        ev["answers"] = sorted(set(ids), key=ids.index)
    asks = []
    for m in RX_ASK.finditer(line):
        st = m.group(2)
        asks += [dict(id=q, reopened="reopened" in st, blocking=st.endswith("BLOCKING") and "not" not in st,
                      user="waits on the user" in st) for q in re.findall(r"Q\d+", m.group(1))]
    if asks:
        ev["asks"] = asks
    for kind, has in (("verdict", "verdict"), ("request", "request"), ("answer", "answers"),
                      ("question", "asks"), ("handoff-rev", "rev")):
        if has in ev:
            ev["kind"] = kind
            break
    else:
        if re.search(r"\barmed\b", line):
            ev["kind"] = "armed"
    return ev


# ------------------------------------------------------------------- parse

def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict))
    return ""


def ended_by(d, text):
    """What a prompt record is: who or what woke the session."""
    o = d.get("origin") or {}
    k = o.get("kind")
    if d.get("isCompactSummary"):
        return "compaction"
    if k == "human":
        return "human"
    if k == "task-notification":
        return "task"
    if k == "peer":
        return "subagent" if (o.get("senderTaskId") or o.get("handback")) else "peer"
    if text.startswith("[Cross-session idle notice"):
        return "idle-notice"
    if k or d.get("isMeta") or d.get("promptSource") == "system" or d.get("turnOrigin") == "system":
        return "local"
    if text.startswith(("<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat>")):
        return "local"
    return "human"                # typed: a prompt, a slash command, an interrupt


def parse(path, subs=None):
    """One transcript -> a dict of records.

      stamps       every user/assistant record's time, sorted, unique
      calls        tool calls that returned: id, name, t0, t1, input, family,
                   detail, stage, background (+ job), prompted (+ reason),
                   timed_out, error
      unfinished   calls with no result (the session ended, or is on a prompt now)
      waits        idle periods: t0, t1, ended_by, cause, job, event (`folded`: an idle
                   compaction, given the cause and the name of the wait after it)
      relay        lines between sessions: t, dir (sent|received), kind + tokens
      jobs         background launches by job id: t0, family, detail, call
      compactions  t, trigger, seconds, pre_tokens, idle
      turns        model requests (distinct message ids); tokens summed per kind

    A wait exists only where the session was IDLE: the record before it is the
    assistant's (its turn over, no call pending) or another idle-time record.
    After a real prompt the model is working until it answers, and a prompt in
    the middle of a turn (a compaction, a hook's note) is model time.
    """
    subs = subs or Subcommands(None)
    S = dict(file=path, id=os.path.basename(path)[:-6], stamps=[], calls=[], unfinished=[],
             waits=[], relay=[], jobs={}, compactions=[], turns=0, cwd=None, name=None,
             cost_usd=None, tokens=dict(output=0, input=0, cache_read=0, cache_creation=0),
             versions=set(), skipped_lines=0)
    asked = set()        # tool_use ids a PreToolUse hook answered "ask" for
    pending = {}         # tool_use id -> call, until its result
    usage = {}           # message id -> the largest usage seen (a message is several records)
    seen = set()
    neutral = []         # waits ended by a compaction or a local record, cause not known yet
    idle = False
    prev = None
    try:
        fh = open(path, errors="replace")
    except OSError as e:
        raise SystemExit(f"sessions.py: cannot read {path}: {e}")
    for line in fh:
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            S["skipped_lines"] += 1          # a line being written while we read
            continue
        t = d.get("type")
        if t == "agent-name":
            S["name"] = d.get("agentName") or S["name"]
        elif t == "custom-title":
            S["name"] = S["name"] or d.get("customTitle")
        elif t == "cost-state":
            S["cost_usd"] = d.get("totalCostUSD")
        elif t == "attachment":
            a = d.get("attachment") or {}
            if a.get("hookEvent") == "PreToolUse" and a.get("toolUseID") \
                    and re.search(r'"permissionDecision"\s*:\s*"ask"', a.get("stdout") or ""):
                asked.add(a["toolUseID"])
                if a["toolUseID"] in pending:
                    c = pending[a["toolUseID"]]
                    c["prompted"], c["reason"] = True, "hook:PreToolUse"
        elif t == "system" and d.get("subtype") == "compact_boundary" and d.get("timestamp"):
            cm = d.get("compactMetadata") or {}
            S["compactions"].append(dict(t=ts(d["timestamp"]), trigger=safe(cm.get("trigger"), ""),
                                         seconds=(cm.get("durationMs") or 0) / 1000.0,
                                         pre_tokens=cm.get("preTokens") or 0, idle=idle))
        if t not in ("user", "assistant") or not d.get("timestamp"):
            continue
        if d.get("uuid") in seen:
            continue                          # a record copied on resume
        seen.add(d.get("uuid"))
        now = ts(d["timestamp"])
        S["stamps"].append(now)
        S["cwd"] = S["cwd"] or d.get("cwd")
        if d.get("version"):
            S["versions"].add(d["version"])
        msg = d.get("message") or {}
        content = msg.get("content")
        if t == "assistant":
            if neutral:                       # work resumed with no prompt on record
                for w in neutral:
                    w["cause"] = "human"
                neutral = []
            u = msg.get("usage") or {}
            mid = msg.get("id") or d.get("uuid")
            old = usage.get(mid, {})
            usage[mid] = {k: max(old.get(k, 0), u.get(k) or 0) for k in
                          ("output_tokens", "input_tokens", "cache_read_input_tokens",
                           "cache_creation_input_tokens")}
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                fam, det, stage = name_call(b.get("name") or "", inp, d.get("cwd"), subs)
                c = dict(id=b.get("id"), name=b.get("name") or "", t0=now, t1=None, input=inp,
                         family=fam, detail=det, stage=stage, background=False, job=None,
                         prompted=b.get("id") in asked, reason="hook:PreToolUse" if b.get("id") in asked else "",
                         timed_out=False, error=False,
                         inline=b.get("name") == "Bash" and bool(INLINE.search(str(inp.get("command") or ""))))
                pending[c["id"]] = c
                if c["name"] == "SendMessage":
                    m = inp.get("message") if isinstance(inp.get("message"), str) else inp.get("content")
                    ev = relay_event(m if isinstance(m, str) else "")
                    ev.update(t=now, dir="sent")
                    S["relay"].append(ev)
            idle = not pending
        else:
            results = [x for x in content if isinstance(x, dict) and x.get("type") == "tool_result"] \
                if isinstance(content, list) else []
            if results:
                tr = d.get("toolUseResult") if isinstance(d.get("toolUseResult"), dict) else {}
                pd = d.get("permissionDecision") if isinstance(d.get("permissionDecision"), dict) else {}
                for x in results:
                    c = pending.pop(x.get("tool_use_id"), None)
                    if not c:
                        continue
                    c["t1"] = max(now, c["t0"])
                    c["error"] = bool(x.get("is_error"))
                    if str(pd.get("source") or "").startswith("user") or c["id"] in asked:
                        c["prompted"] = True
                        c["reason"] = safe(pd.get("reasonCode"), "") or c["reason"] or "prompt"
                    job = tr.get("backgroundTaskId") or (tr.get("agentId") if tr.get("isAsync") else None)
                    if job or c["input"].get("run_in_background") in (True, "true"):
                        c["background"], c["job"] = True, job
                        if job:
                            S["jobs"][str(job)] = dict(t0=c["t0"], family=c["family"], detail=c["detail"],
                                                       stage=c["stage"], call=c["id"])
                    c["timed_out"] = bool(tr.get("timedOutAfterMs"))
                    S["calls"].append(c)
                idle = False
            else:
                text = text_of(content)
                o = d.get("origin") or {}
                what = ended_by(d, text)
                w = None
                if idle and prev is not None and now > prev:
                    w = dict(t0=prev, t1=now, ended_by=what, cause=CAUSE.get(what), job=None, event=None)
                    S["waits"].append(w)
                if what in ("peer", "idle-notice"):
                    ev = relay_event(o.get("body") if isinstance(o.get("body"), str) else text) \
                        if what == "peer" else {"kind": "idle-notice"}
                    ev.update(t=now, dir="received")
                    S["relay"].append(ev)
                    if w:
                        w["event"] = ev["kind"]
                elif what == "task":
                    m = re.search(r"<task-id>([\w-]+)</task-id>", text)
                    st = re.search(r"<status>([\w-]+)</status>", text)
                    job = S["jobs"].get(m.group(1)) if m else None
                    if job is not None:
                        job.setdefault("t1", now)
                        job["status"] = st.group(1) if st else ""
                    if w:
                        w["job"] = m.group(1) if m else None
                elif what == "subagent":
                    job = S["jobs"].get(str(o.get("senderTaskId") or o.get("from") or ""))
                    if job is not None:
                        job.setdefault("t1", now)
                    if w:
                        w["job"] = str(o.get("senderTaskId") or o.get("from") or "") or None
                if what in CAUSE:
                    for n in neutral:         # the idle compaction before this wait is this wait
                        n["cause"], n["ended_by"], n["folded"] = CAUSE[what], what, True
                        n["job"], n["event"] = (w or {}).get("job"), (w or {}).get("event")
                    neutral = []
                    idle = False
                elif w:
                    neutral.append(w)
        prev = now
    fh.close()
    for w in neutral:                         # idle to the end of the record
        w["cause"] = "human"
    S["unfinished"] = sorted(pending.values(), key=lambda c: c["t0"])
    S["calls"].sort(key=lambda c: (c["t0"], c["t1"]))
    S["stamps"] = sorted(set(S["stamps"]))
    S["turns"] = len(usage)
    for u in usage.values():
        S["tokens"]["output"] += u["output_tokens"]
        S["tokens"]["input"] += u["input_tokens"]
        S["tokens"]["cache_read"] += u["cache_read_input_tokens"]
        S["tokens"]["cache_creation"] += u["cache_creation_input_tokens"]
    S["versions"] = sorted(S["versions"])
    return S


# ---------------------------------------------------------------- classify

def call_class(c):
    if c["name"] in ASKS_USER:
        return "human"
    return "prompt" if c["prompted"] else "tool"


def classify(S, lo=None, hi=None, gap_min=GAP_MIN):
    """The timeline of a parsed transcript inside [lo, hi].

    Returns None when the session has no time in the window, else a dict:
      cuts     the window cut at every record's timestamp (n + 1 times)
      cls      the class of each of the n pieces
      owner    the call or wait that gave the piece its class (None: model, gap)
      stage    the stage of each piece
      job      the session's own background job running over the piece, if any
      seconds  class -> seconds; they sum to cuts[-1] - cuts[0] exactly
    and sets, on every call of S, `own_s` (the seconds it owns) and
    `beside_s` (the seconds of its interval that a stalled prompt of ANOTHER
    call owns).

    A wait is named by the record that ended it, with one exception: while a
    background job this session launched is still running (launch to
    notification), an idle piece is a wait on that job, whoever woke the
    session in between -- a reviewer subagent is still what the concept
    session waits for when a board session's message interrupts the wait.
    """
    st = S["stamps"]
    if len(st) < 2:
        return None
    a = st[0] if lo is None else max(lo, st[0])
    b = st[-1] if hi is None else min(hi, st[-1])
    if not a < b:
        return None
    cuts = [a] + st[bisect.bisect_right(st, a):bisect.bisect_left(st, b)] + [b]
    n = len(cuts) - 1
    cls, owner, key = [None] * n, [None] * n, [None] * n

    def span(t0, t1):
        return bisect.bisect_left(cuts, max(t0, a)), bisect.bisect_left(cuts, min(t1, b))

    def paint(t0, t1, c, ref, weight):
        # the higher class wins; inside one class a call that takes time beats
        # one that returns at once, and of two the one that started first.
        k = (RANK[c], weight, -t0)
        i, j = span(t0, t1)
        for x in range(i, j):
            if key[x] is None or k > key[x]:
                cls[x], owner[x], key[x] = c, ref, k

    for c in S["calls"]:
        c["own_s"] = c["beside_s"] = 0.0
        paint(c["t0"], c["t1"], call_class(c), c, 0 if c["name"] in INSTANT else 1)
    for w in S["waits"]:
        paint(w["t0"], w["t1"], w["cause"], w, 0)

    running = [None] * n
    for jid, j in sorted(S["jobs"].items(), key=lambda kv: -kv[1]["t0"]):
        if j.get("t1") is not None:
            i, k = span(j["t0"], j["t1"])
            running[i:k] = [jid] * (k - i)        # the earliest launch is painted last
    for x in range(n):
        if cls[x] in ("peer", "human") and running[x] is not None and "family" not in owner[x]:
            cls[x] = "background"

    changes = [(c["t0"], c["stage"]) for c in S["calls"] + S["unfinished"] if c["stage"]]
    changes.sort(key=lambda x: x[0])
    at = [t for t, _ in changes]
    stage = [None] * n
    seconds = dict.fromkeys(CLASSES, 0.0)
    for x in range(n):
        dt = cuts[x + 1] - cuts[x]
        if cls[x] is None:
            cls[x] = "model" if dt <= gap_min * 60.0 else "gap"
        seconds[cls[x]] += dt
        o = owner[x]
        if o is not None and "family" in o:
            o["own_s"] += dt
        if o is not None and o.get("stage"):
            stage[x] = o["stage"]
        else:
            i = bisect.bisect_right(at, cuts[x])
            stage[x] = changes[i - 1][1] if i else NO_STAGE
    for c in S["calls"]:
        if call_class(c) != "tool":
            continue
        i, j = span(c["t0"], c["t1"])
        c["beside_s"] = sum(cuts[x + 1] - cuts[x] for x in range(i, j)
                            if cls[x] == "prompt" and owner[x] is not c)
    return dict(cuts=cuts, cls=cls, owner=owner, stage=stage, job=running, seconds=seconds, a=a, b=b)


def seconds_in(T, t0, t1, classes):
    """Seconds of the given classes between t0 and t1 on a timeline."""
    cuts = T["cuts"]
    i = max(0, bisect.bisect_right(cuts, t0) - 1)
    tot = 0.0
    for x in range(i, len(cuts) - 1):
        if cuts[x] >= t1:
            break
        if T["cls"][x] in classes:
            tot += max(0.0, min(cuts[x + 1], t1) - max(cuts[x], t0))
    return tot


# ---------------------------------------------------------------- summarise

def role_of(S, project):
    """board1 / concept / session: from the session's name (bin/board and
    bin/concept name it) and its working directory.  The name itself is not
    reported."""
    m = re.search(r"board\d+", S.get("name") or "", re.I)
    if m:
        return m.group(0).lower()
    cwd = (S.get("cwd") or "").rstrip("/")
    if re.search(r"concept", S.get("name") or "", re.I):
        return "concept"
    if project and cwd == project.rstrip("/"):
        return "concept"
    if cwd.endswith("/kicad_project") or "/kicad_project/" in cwd:
        return "board"
    return "session"


def relay_tables(S, T, role):
    """Reviews and questions as this session saw them.  A review is a request
    line (`boardN RVn ready`) paired with the first verdict line for the same
    id after it (`review RVn: PASS|CHANGES`); a question is a ring paired
    with the `handoff rev R -- answers Qn` line.  For a board session the
    time between is its wait; for the concept session, its turnaround."""
    a, b = T["a"], T["b"]
    evs = [e for e in S["relay"] if a <= e["t"] <= b]
    own = role if re.fullmatch(r"board\d+", role) else None
    at = [c["t0"] for c in S["calls"] if c["stage"]]
    stages = [c["stage"] for c in S["calls"] if c["stage"]]

    def stage_at(t):
        i = bisect.bisect_right(at, t)
        return stages[i - 1] if i else NO_STAGE

    known = bool(own) or role == "concept"

    def by_board(e):                  # a request and a question come from a board session,
        return not known or (e["dir"] == "sent") == bool(own)

    def by_concept(e):                # a verdict and an answer from the concept session
        return not known or (e["dir"] == "sent") != bool(own)

    reviews, pend, verdicts = [], {}, []
    questions, qrow, qpend, reopened = [], {}, {}, {}
    revs = {}
    for e in evs:
        board = e.get("board") or own or "?"
        if e.get("rev") is not None:
            revs.setdefault(board, set()).add(e["rev"])
        if "request" in e and by_board(e):
            r = pend.setdefault((board, e["request"]), dict(t=e["t"], what=e.get("what", ""), rings=0))
            r["rings"] += 1               # rung again before the verdict: the first ring counts
        if "verdict" in e and by_concept(e):
            if any(v["board"] == board and v["id"] == e["review"] and v["t"] > e["t"] - 120 for v in verdicts):
                continue                  # the same verdict sent twice
            verdicts.append(dict(board=board, id=e["review"], verdict=e["verdict"], must_fix=e.get("must_fix"),
                                 t=e["t"]))
            r = pend.pop((board, e["review"]), None)
            if r:
                reviews.append(dict(board=board, id=e["review"], what=r["what"], t=r["t"], rings=r["rings"],
                                    minutes=(e["t"] - r["t"]) / 60.0, verdict=e["verdict"],
                                    must_fix=e.get("must_fix"),
                                    active_s=seconds_in(T, r["t"], e["t"], ("model", "tool"))))
        for q in e.get("asks", []) if by_board(e) else []:
            k = (board, q["id"])
            if q["reopened"]:
                reopened[k] = reopened.get(k, 0) + 1
            if k not in qrow:
                qrow[k] = qpend[k] = dict(board=board, id=q["id"], t=e["t"], blocking=q["blocking"],
                                          for_user=q["user"], minutes=None,
                                          stage=stage_at(e["t"]) if e["dir"] == "sent" else "")
                questions.append(qrow[k])
        for q in e.get("answers", []) if by_concept(e) else []:
            row = qpend.pop((board, q), None)
            if row:
                row["minutes"] = (e["t"] - row["t"]) / 60.0
    for q in questions:
        q["reopened"] = reopened.get((q["board"], q["id"]), 0)
    chains = []
    for board in sorted({q["board"] for q in questions}):
        run = []
        for q in sorted((q for q in questions if q["board"] == board), key=lambda q: q["t"]):
            if run and (q["t"] - run[-1]["t"]) / 60.0 > CHAIN_GAP_MIN:
                chains.append(run)
                run = []
            run.append(q)
        chains.append(run)
    chains = [dict(board=r[0]["board"], first=r[0]["id"], last=r[-1]["id"], questions=len(r),
                   t=r[0]["t"], hours=(r[-1]["t"] - r[0]["t"]) / 3600.0,
                   stage=max({q["stage"] for q in r}, key=[q["stage"] for q in r].count),
                   reopened=sum(q["reopened"] for q in r))
              for r in chains if len(r) >= CHAIN_MIN]
    for v in verdicts:
        del v["t"]
    return dict(events=len(evs), reviews=reviews, verdicts=verdicts, no_verdict_yet=sorted(i for _, i in pend),
                questions=questions, chains=chains,
                handoff_revs={k: len(v) for k, v in sorted(revs.items())})


def summarise(S, T, role):
    """One session's tables, from its records and its timeline.  Everything
    returned is a number, a time, or a name that passed safe()."""
    a, b = T["a"], T["b"]
    sec = T["seconds"]
    calls = [c for c in S["calls"] if a <= c["t0"] < b]
    stalls = [c for c in calls if call_class(c) == "prompt"]

    stages = {}
    for x in range(len(T["cls"])):
        if T["cls"][x] in ("model", "tool", "background", "prompt"):
            row = stages.setdefault(T["stage"][x], dict(model=0.0, tool=0.0, background=0.0, prompt=0.0, calls=0))
            row[T["cls"][x]] += T["cuts"][x + 1] - T["cuts"][x]
    last = NO_STAGE
    for c in calls:                               # a call's stage: its own, else the one in force
        last = c["stage"] or last
        stages.setdefault(last, dict(model=0.0, tool=0.0, background=0.0, prompt=0.0, calls=0))["calls"] += 1

    fams, dets = {}, {}
    for c in calls:
        if call_class(c) != "tool":
            continue
        for table, k in ((fams, c["family"]), (dets, (c["family"], c["detail"]))):
            if table is dets and not c["detail"]:
                continue
            row = table.setdefault(k, dict(calls=0, own_s=0.0, runs=[], timed_out=0, background=0,
                                           beside=0, beside_s=0.0, errors=0))
            row["calls"] += 1
            row["own_s"] += c["own_s"]
            row["timed_out"] += c["timed_out"]
            row["background"] += c["background"]
            row["errors"] += c["error"]
            if c["beside_s"] > 0:
                row["beside"] += 1
                row["beside_s"] += c["beside_s"]
            else:
                row["runs"].append(c["t1"] - c["t0"])

    def rows(table, name):
        out = []
        for k, r in table.items():
            out.append(dict(name(k), calls=r["calls"], seconds=r["own_s"], p50_s=pct(r["runs"], 50),
                            p90_s=pct(r["runs"], 90), max_s=max(r["runs"] or [0.0]),
                            timed_out=r["timed_out"], background=r["background"], errors=r["errors"],
                            beside_stall=r["beside"], beside_stall_s=r["beside_s"]))
        return sorted(out, key=lambda r: (-r["seconds"], -r["calls"], str(r)))

    jobs = {}
    for jid, j in S["jobs"].items():
        if not a <= j["t0"] < b:
            continue
        row = jobs.setdefault(j["family"], dict(launched=0, ended=0, runs=[], wait_s=0.0, waits=0))
        row["launched"] += 1
        if j.get("t1") is not None:
            row["ended"] += 1
            row["runs"].append(j["t1"] - j["t0"])
    waits, counted = {}, set()
    for x, o in enumerate(T["owner"]):
        if o is None or "family" in o:
            continue
        c, dt = T["cls"][x], T["cuts"][x + 1] - T["cuts"][x]
        row = waits.setdefault((c, o.get("event") or o["ended_by"]), dict(n=0, s=0.0))
        row["s"] += dt
        if (c, id(o)) not in counted and not o.get("folded"):
            counted.add((c, id(o)))
            row["n"] += 1
        if c == "background":
            jid = str(o["job"]) if str(o["job"]) in S["jobs"] else T["job"][x]
            fam = S["jobs"][jid]["family"] if jid in S["jobs"] else "(not linked to a launch)"
            row = jobs.setdefault(fam, dict(launched=0, ended=0, runs=[], wait_s=0.0, waits=0, seen=set()))
            row["wait_s"] += dt
            if id(o) not in row.setdefault("seen", set()):
                row["seen"].add(id(o))
                row["waits"] += 1

    comp = [c for c in S["compactions"] if a <= c["t"] <= b]
    return dict(
        id=S["id"][:8], role=role, start=iso(a), end=iso(b), span_s=b - a,
        seconds={c: sec[c] for c in CLASSES},
        calls=len(calls), unfinished_calls=len(S["unfinished"]),
        stalls=dict(n=len(stalls), over_1min=sum(1 for c in stalls if c["own_s"] > STALL_S),
                    by_tool=sorted(({"tool": safe(k), "n": len(v), "over_1min": sum(1 for c in v if c["own_s"] > STALL_S),
                                     "seconds": sum(c["own_s"] for c in v), "longest_s": max(c["own_s"] for c in v)}
                                    for k, v in _group(stalls, lambda c: c["name"]).items()),
                                   key=lambda r: -r["seconds"]),
                    by_reason=sorted(({"reason": safe(k) or "prompt", "n": len(v), "seconds": sum(c["own_s"] for c in v)}
                                      for k, v in _group(stalls, lambda c: c["reason"]).items()),
                                     key=lambda r: -r["seconds"]),
                    longest=[dict(t=iso(c["t0"]), tool=safe(c["name"]), minutes=c["own_s"] / 60.0,
                                  reason=safe(c["reason"]) or "prompt")
                             for c in sorted(stalls, key=lambda c: -c["own_s"])[:12] if c["own_s"] > STALL_S]),
        beside_stall=dict(calls=sum(1 for c in calls if c.get("beside_s", 0) > 0),
                          seconds=sum(c.get("beside_s", 0.0) for c in calls)),
        stages=sorted(({"stage": k, **v} for k, v in stages.items()),
                      key=lambda r: -(r["model"] + r["tool"] + r["background"] + r["prompt"])),
        families=rows(fams, lambda k: {"family": k}),
        details=rows(dets, lambda k: {"family": k[0], "detail": k[1]}),
        background_jobs=sorted(({"family": k, "launched": v["launched"], "ended": v["ended"],
                                 "median_min": median(v["runs"]) / 60.0, "max_min": max(v["runs"] or [0.0]) / 60.0,
                                 "run_s": sum(v["runs"]), "waits": v["waits"], "wait_s": v["wait_s"]}
                                for k, v in jobs.items()), key=lambda r: (-r["wait_s"], -r["run_s"], r["family"])),
        waits=sorted(({"cause": k[0], "ended_by": k[1], "n": v["n"], "seconds": v["s"]} for k, v in waits.items()),
                     key=lambda r: -r["seconds"]),
        relay=relay_tables(S, T, role),
        compactions=dict(n=len(comp), idle=sum(1 for c in comp if c["idle"]),
                         seconds=sum(c["seconds"] for c in comp),
                         max_pre_tokens=max([c["pre_tokens"] for c in comp] or [0])),
        turns=S["turns"], tokens=dict(S["tokens"]), cost_usd=S["cost_usd"],
        claude_code=S["versions"][-3:], skipped_lines=S["skipped_lines"],
    )


def _group(items, key):
    out = {}
    for i in items:
        out.setdefault(key(i), []).append(i)
    return out


def subagent_row(path, subs, lo, hi):
    """One subagents/agent-*.jsonl -> its run: type, the tokens of its
    description that name a review (board, id), minutes, tokens, calls.  A
    subagent that was continued later has idle time between; `minutes` is the
    time it worked or stalled, `span_min` the whole of it."""
    S = parse(path, subs)
    if len(S["stamps"]) < 2 or not ((lo is None or S["stamps"][-1] >= lo) and (hi is None or S["stamps"][0] < hi)):
        return None
    T = classify(S)
    meta = {}
    try:
        meta = json.load(open(path[:-6] + ".meta.json"))
    except (OSError, ValueError):
        pass
    desc = str(meta.get("description") or "")
    board = re.search(r"\bboard\d+\b", desc)
    rid = re.search(r"\b(?:RV|M)\d+\b", desc)
    sec = T["seconds"]
    work = sec["model"] + sec["tool"] + sec["prompt"]
    return dict(type=safe(meta.get("agentType") or "general-purpose"), board=board.group(0) if board else "",
                review=rid.group(0) if rid else "", start=iso(T["a"]), span_min=(T["b"] - T["a"]) / 60.0,
                minutes=work / 60.0, model_min=sec["model"] / 60.0, tool_min=sec["tool"] / 60.0,
                prompt_min=sec["prompt"] / 60.0, gap_min=sec["gap"] / 60.0,
                output_tokens=S["tokens"]["output"], tool_calls=len(S["calls"]),
                inline_scripts=sum(1 for c in S["calls"] if c["inline"]),
                call=meta.get("toolUseId") or "")


# ------------------------------------------------------------ finding the files

def find_transcripts(project, config_dir=None):
    """The transcript folders of a project: ~/.claude/projects/<slug> and
    every <slug>-* beside it (the board sessions run in <project>/kicad_project)."""
    base = os.path.join(config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
                        "projects")
    s = slug(project)
    out = []
    if os.path.isdir(base):
        for d in sorted(os.listdir(base)):
            if d == s or d.startswith(s + "-"):
                out.append(os.path.join(base, d))
    return base, out


def load(folders, project, lo, hi, gap_min, strict_cwd):
    """Every session in the folders that has time in the window."""
    subs = Subcommands(project)
    sessions = []
    for folder in folders:
        for path in sorted(glob.glob(os.path.join(folder, "*.jsonl"))):
            S = parse(path, subs)
            if strict_cwd and S["cwd"] and project and not (S["cwd"] + "/").startswith(project.rstrip("/") + "/"):
                continue                      # a sibling project whose name shares the prefix
            T = classify(S, lo, hi, gap_min)
            if not T:
                continue
            row = summarise(S, T, role_of(S, project))
            runs = []
            for sp in sorted(glob.glob(os.path.join(path[:-6], "subagents", "*.jsonl"))):
                r = subagent_row(sp, subs, lo, hi)
                if r:
                    r.pop("call")
                    runs.append(r)
            row["subagents"] = sorted(runs, key=lambda r: r["start"])
            sessions.append(row)
    return sorted(sessions, key=lambda r: (r["start"], r["id"]))


# ------------------------------------------------------------------ the report

def h(s):
    return f"{s / 3600.0:.1f}" if s >= 359.5 else (f"{s / 3600.0:.2f}" if s >= 0.5 else "0")


def mins(s):
    return f"{s / 60.0:.1f}" if s else "0"


def table(head, rows, right=None):
    right = right if right is not None else set(range(1, len(head)))
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---:" if i in right else "---" for i in range(len(head))) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out + [""]


def agent_summary(runs):
    by = _group(runs, lambda r: r["type"])
    rows = []
    for k in sorted(by, key=lambda k: -sum(r["minutes"] for r in by[k])):
        v = by[k]
        rows.append([f"`{k}`", len(v), f"{median([r['minutes'] for r in v]):.1f}",
                     f"{max(r['minutes'] for r in v):.1f}", h(60 * sum(r["minutes"] for r in v)),
                     h(60 * sum(r["model_min"] for r in v)), h(60 * sum(r["tool_min"] for r in v)),
                     h(60 * sum(r["prompt_min"] for r in v)),
                     f"{sum(r['output_tokens'] for r in v) / 1000.0:.0f}",
                     f"{median([r['tool_calls'] for r in v]):.0f}",
                     f"{median([r['inline_scripts'] for r in v]):.0f} ({max(r['inline_scripts'] for r in v)})"])
    return rows


def render(R):
    """report.json's content -> report.md."""
    L = [f"# Session time report — {R['project']}", "",
         "*Generated by `tools/measure/sessions.py` from the Claude Code transcripts — do not edit. "
         "Numbers and the names of tools and commands only; no message text.*", "",
         f"Window: {R['window']['since'] or 'the first record'} to {R['window']['until'] or 'the last record'} (UTC). "
         f"{len(R['sessions'])} session(s). Every second of a session is in exactly one class, by priority "
         "prompt stall > background wait > peer wait > human wait > tool > model "
         f"(`tools/measure/README.md`); an uncovered piece over {R['gap_min']:g} min is \"no record\".", ""]
    if not R["sessions"]:
        return "\n".join(L + ["No session has a record in the window.", ""])
    lab = {s["id"]: f"{s['role']} `{s['id']}`" for s in R["sessions"]}

    L += ["## 1. Wall-clock per session", "",
          "Hours. \"Active\" is model turns plus foreground tools, as a share of the span.", ""]
    rows = []
    for s in R["sessions"]:
        x = s["seconds"]
        act = 100.0 * (x["model"] + x["tool"]) / s["span_s"] if s["span_s"] else 0.0
        rows.append([lab[s["id"]], s["start"][5:16].replace("T", " "), h(s["span_s"]),
                     f"{h(x['prompt'])} ({s['stalls']['over_1min']} over 1 min)", h(x["peer"]),
                     h(x["background"]), h(x["model"]), f"{h(x['tool'])} ({s['calls']} calls)",
                     h(x["human"]), h(x["gap"]), f"{act:.0f} %"])
    L += table(["session", "from", "span", LABEL["prompt"], LABEL["peer"], LABEL["background"], LABEL["model"],
                LABEL["tool"], LABEL["human"], LABEL["gap"], "active"], rows)

    L += ["## 2. Per stage", "",
          "Hours. A call's stage comes from the path it runs or writes (`tools/place/` is placement, "
          f"`<board>/build/` is \"{OWN}\"); model time and waits take the stage of the last such call.", ""]
    for s in R["sessions"]:
        rows = [[r["stage"], h(r["model"]), h(r["tool"]), h(r["background"]), h(r["prompt"]), r["calls"]]
                for r in s["stages"] if r["model"] + r["tool"] + r["background"] + r["prompt"] >= 36 or r["calls"] >= 20]
        if rows:
            L += [f"**{lab[s['id']]}**", ""]
            L += table(["stage", "model", "tool", "background wait", "prompt stall", "calls"], rows)

    L += ["## 3. Foreground tool time by family", "",
          "Minutes each family OWNS: a second covered by two calls, or by a call and a stalled prompt, is "
          "counted once, so the rows sum to the tool class. p50/p90/max are single calls, in seconds, "
          "calls that returned beside a stalled prompt left out.", ""]
    for s in R["sessions"]:
        fam = [r for r in s["families"] if r["seconds"] >= 30 or r["calls"] >= 20]
        if not fam:
            continue
        L += [f"**{lab[s['id']]}** — {s['beside_stall']['calls']} call(s) returned beside a stalled prompt; "
              f"{h(s['beside_stall']['seconds'])} h of their intervals is prompt stall, not tool time.", ""]
        L += table(["family", "calls", "min", "p50 s", "p90 s", "max s", "timed out", "sent to background"],
                   [[f"`{r['family']}`", r["calls"], mins(r["seconds"]), f"{r['p50_s']:.1f}", f"{r['p90_s']:.1f}",
                     f"{r['max_s']:.0f}", r["timed_out"] or "", r["background"] or ""] for r in fam])
        det = [r for r in s["details"] if r["seconds"] >= 60][:25]
        if det:
            L += table(["command", "runs", "min", "p50 s", "p90 s", "max s"],
                       [[f"`{r['family']}` `{r['detail']}`", r["calls"], mins(r["seconds"]), f"{r['p50_s']:.1f}",
                         f"{r['p90_s']:.1f}", f"{r['max_s']:.0f}"] for r in det])

    L += ["## 4. Background jobs", "",
          "A job runs from its launch to its notification (an upper bound: a notification is delivered at "
          "the next turn boundary). \"Waited\" is the session's idle time while the job ran, counted "
          "once when several ran together.", ""]
    for s in R["sessions"]:
        if s["background_jobs"]:
            L += [f"**{lab[s['id']]}**", ""]
            L += table(["family", "launched", "ended", "median min", "max min", "ran h", "waits", "waited h"],
                       [[f"`{r['family']}`", r["launched"], r["ended"], f"{r['median_min']:.1f}", f"{r['max_min']:.1f}",
                         h(r["run_s"]), r["waits"], h(r["wait_s"])] for r in s["background_jobs"]])

    L += ["## 5. Permission prompts", "",
          "A call that sat on a permission prompt (the harness recorded a user's decision, or a "
          "PreToolUse hook answered `ask`). Its whole interval is the stall: the record does not say when "
          "the prompt was answered.", ""]
    for s in R["sessions"]:
        st = s["stalls"]
        if not st["n"]:
            continue
        L += [f"**{lab[s['id']]}** — {st['n']} prompted call(s), {st['over_1min']} over 1 min; by reason: "
              + ", ".join(f"`{r['reason']}` {r['n']} ({h(r['seconds'])} h)" for r in st["by_reason"]) + ".", ""]
        L += table(["tool", "prompts", "over 1 min", "hours", "longest min"],
                   [[f"`{r['tool']}`", r["n"], r["over_1min"], h(r["seconds"]), mins(r["longest_s"])] for r in st["by_tool"]])
        if st["longest"]:
            L += table(["longest stalls, start (UTC)", "tool", "min", "reason"],
                       [[r["t"][5:16].replace("T", " "), f"`{r['tool']}`", f"{r['minutes']:.0f}", f"`{r['reason']}`"]
                        for r in st["longest"]], right={2})

    L += ["## 6. Reviews", "",
          "`RVn ready` paired with the first `review RVn: PASS|CHANGES` after it, as each session saw them: "
          "for a board session the minutes are its wait, for the concept session its turnaround. "
          "\"Active\" is model plus tool time of that session while the review was pending.", ""]
    rows, each = [], []
    for s in R["sessions"]:
        rel = s["relay"]
        for board in sorted({r["board"] for r in rel["reviews"]} | {v["board"] for v in rel["verdicts"]}):
            rv = [r for r in rel["reviews"] if r["board"] == board]
            vd = [v for v in rel["verdicts"] if v["board"] == board]
            m = [r["minutes"] for r in rv]
            mf = [v["must_fix"] for v in vd if v["must_fix"] is not None]
            rows.append([lab[s["id"]], board, len(vd), sum(1 for v in vd if v["verdict"] == "CHANGES"),
                         sum(mf) if mf else "", len(rv), f"{median(m):.1f}" if m else "",
                         f"{max(m):.1f}" if m else "", h(60 * sum(m)), h(sum(r["active_s"] for r in rv))])
            each += [[lab[s["id"]], board, r["id"], r["what"], when(r["t"]), f"{r['minutes']:.1f}", r["verdict"],
                      "" if r["must_fix"] is None else r["must_fix"]] for r in rv]
    if rows:
        L += table(["session", "board", "verdicts", "CHANGES", "must-fix (as rung)", "paired", "median min",
                    "max min", "sum h", "active h"], rows, right=set(range(2, 10)))
        L += table(["session", "board", "review", "kind", "rung (UTC)", "min", "verdict", "must-fix"], each[:200],
                   right={5, 7})
    else:
        L += ["No review line in the window.", ""]

    L += ["## 7. Subagent runs", "",
          "Minutes are the time a subagent worked (model, tools, its own prompt stalls); output tokens in "
          "thousands; \"inline scripts\" are a run's Bash calls with a heredoc or an interpreter's `-c`.", ""]
    for s in R["sessions"]:
        runs = s["subagents"]
        if not runs:
            continue
        L += [f"**{lab[s['id']]}** — {len(runs)} run(s)", ""]
        L += table(["agent type", "runs", "median min", "max min", "sum h", "model h", "tool h", "prompt stall h",
                    "output k tokens", "median tool calls", "inline scripts, median (max)"], agent_summary(runs))
        rv = [r for r in runs if "review" in r["type"]]
        if rv:
            L += table(["start (UTC)", "agent type", "board", "review", "min", "model min", "tool min", "stall min",
                        "output k tokens", "tool calls", "inline scripts"],
                       [[r["start"][5:16].replace("T", " "), f"`{r['type']}`", r["board"], r["review"],
                         f"{r['minutes']:.1f}", f"{r['model_min']:.1f}", f"{r['tool_min']:.1f}",
                         f"{r['prompt_min']:.1f}" if r["prompt_min"] else "", f"{r['output_tokens'] / 1000.0:.0f}",
                         r["tool_calls"], r["inline_scripts"]] for r in rv[:120]], right=set(range(4, 11)))

    L += ["## 8. Questions", "",
          "A question's first ring (`Qn BLOCKING|not blocking`, `Qn waits on the user`) paired with the "
          "line that answers it (`answers Qn`, `Qn answered`). A chain is "
          f"{CHAIN_MIN} or more questions of one board, each rung within {CHAIN_GAP_MIN:g} min of the one "
          "before; its stage is the ringing session's stage at the time (blank in the concept session's view).", ""]
    rows, chains = [], []
    for s in R["sessions"]:
        rel = s["relay"]
        for board in sorted({q["board"] for q in rel["questions"]}):
            qs = [q for q in rel["questions"] if q["board"] == board]
            m = [q["minutes"] for q in qs if q["minutes"] is not None]
            rows.append([lab[s["id"]], board, len(qs), sum(1 for q in qs if q["blocking"]),
                         sum(q["reopened"] for q in qs), len(m), f"{median(m):.1f}" if m else "",
                         h(60 * sum(m)), rel["handoff_revs"].get(board, "")])
        chains += [[lab[s["id"]], c["board"], f"{c['first']} → {c['last']}", c["questions"], when(c["t"]),
                    f"{c['hours']:.1f}", c["stage"], c["reopened"] or ""] for c in rel["chains"]]
    if rows:
        L += table(["session", "board", "questions", "blocking", "reopened", "answered", "median min to answer",
                    "sum h", "handoff revisions seen"], rows, right=set(range(2, 9)))
    else:
        L += ["No question line in the window.", ""]
    if chains:
        L += table(["session", "board", "chain", "questions", "first rung (UTC)", "hours", "stage", "reopened"],
                   chains, right={3, 5, 7})

    L += ["## 9. Waits, by what ended them", "",
          "An idle period is named by the record that ended it (for a message from the other session, by "
          "the kind of line); one that ended in a compaction or a local command is folded into the wait that "
          "follows; idle time while the session's own background job still ran is a wait on that job, "
          "whoever woke the session in between.", ""]
    for s in R["sessions"]:
        if s["waits"]:
            L += [f"**{lab[s['id']]}**", ""]
            L += table(["class", "ended by", "waits", "hours"],
                       [[LABEL[r["cause"]], r["ended_by"], r["n"], h(r["seconds"])] for r in s["waits"]], right={2, 3})

    L += ["## 10. Model usage", ""]
    L += table(["session", "requests", "output M tokens", "cache read M", "cache write M", "compactions (idle)",
                "compaction min", "cost USD", "subagent output M tokens"],
               [[lab[s["id"]], s["turns"], f"{s['tokens']['output'] / 1e6:.2f}", f"{s['tokens']['cache_read'] / 1e6:.0f}",
                 f"{s['tokens']['cache_creation'] / 1e6:.1f}", f"{s['compactions']['n']} ({s['compactions']['idle']})",
                 mins(s["compactions"]["seconds"]), "" if s["cost_usd"] is None else f"{s['cost_usd']:.0f}",
                 f"{sum(r['output_tokens'] for r in s['subagents']) / 1e6:.2f}"] for s in R["sessions"]])
    notes = [f"{lab[s['id']]}: {s['unfinished_calls']} call(s) with no result on record (not counted)"
             for s in R["sessions"] if s["unfinished_calls"]]
    notes += [f"{lab[s['id']]}: {s['skipped_lines']} unreadable line(s) skipped" for s in R["sessions"] if s["skipped_lines"]]
    if notes:
        L += ["Notes: " + "; ".join(notes) + ".", ""]
    return "\n".join(L)


def rounded(x):
    """Seconds to the millisecond, so report.json is stable and readable."""
    if isinstance(x, float):
        return round(x, 3)
    if isinstance(x, dict):
        return {k: rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [rounded(v) for v in x]
    return x


def build(project=None, transcripts=None, since=None, until=None, gap_min=GAP_MIN, config_dir=None):
    """The whole report as a dict (what report.json holds)."""
    project = os.path.abspath(os.path.expanduser(project)) if project else None
    if transcripts:
        folders, strict = [os.path.abspath(os.path.expanduser(t)) for t in transcripts], False
        for f in folders:
            if not os.path.isdir(f):
                raise SystemExit(f"sessions.py: --transcripts {f}: not a folder")
    else:
        base, folders = find_transcripts(project, config_dir)
        strict = True
        if not folders:
            raise SystemExit(f"sessions.py: no transcripts for {project}: looked for {slug(project)}* in {base}\n"
                             "  (--transcripts DIR reads a folder of .jsonl directly)")
    lo = instant(since) if since else None
    hi = instant(until, end=True) if until else None
    sessions = load(folders, project, lo, hi, gap_min, strict)
    return rounded(dict(project=safe(os.path.basename(project)) if project else "(transcripts)",
                        window=dict(since=iso(lo) if lo else None, until=iso(hi) if hi else None),
                        gap_min=gap_min, priority=PRIORITY, sessions=sessions))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Where the sessions' time went, from the Claude Code transcripts.")
    ap.add_argument("--project", help="the project whose sessions to read (default: this repository)")
    ap.add_argument("--transcripts", action="append", metavar="DIR",
                    help="read this folder of *.jsonl instead of looking the project up (repeatable)")
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="count from this day (or YYYY-MM-DDTHH:MM), UTC")
    ap.add_argument("--until", metavar="YYYY-MM-DD", help="count to the end of this day (or to YYYY-MM-DDTHH:MM), UTC")
    ap.add_argument("--out", metavar="DIR", help="where report.md and report.json go (default: out/sessions here)")
    ap.add_argument("--gap-min", type=float, default=GAP_MIN,
                    help="an uncovered piece longer than this is 'no record', not model time (default %(default)s)")
    a = ap.parse_args(argv)
    project = a.project or (None if a.transcripts else ROOT)
    R = build(project, a.transcripts, a.since, a.until, a.gap_min)
    out = os.path.abspath(a.out or os.path.join(ROOT, "out", "sessions"))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "report.json"), "w") as f:
        json.dump(R, f, indent=1, sort_keys=True)
        f.write("\n")
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write(render(R))
    for s in R["sessions"]:
        x = s["seconds"]
        print(f"{s['role']:8s} {s['id']}  span {h(s['span_s']):>6s} h   " +
              "  ".join(f"{c} {h(x[c])}" for c in CLASSES if x[c] >= 0.5))
    print(f"sessions: {len(R['sessions'])} session(s) -> {os.path.join(out, 'report.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
