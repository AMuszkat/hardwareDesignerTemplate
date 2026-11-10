#!/usr/bin/env python3
"""
PreToolUse hook on Bash -- refuses ONE pattern: a loop that waits for a
process by NAME.

    python3 <kicad_project>/tools/session/bashguard.py      (stdin: hook JSON)

The same for both session types (no side argument).  Blocked:

    while pgrep -f "netgroup.py --board board1 routing/R2_emmc_"; do sleep 20; done

i.e. `while` / `until`  +  `pgrep` with a pattern, `ps ... | grep`, or `pidof`
+  `sleep`, in one loop.  Why: `pgrep -f` matches whole command lines and the
loop's own shell carries the pattern, so the loop matches ITSELF and never
ends.  A board session sat 4 h 52 min at 0 CPU on three router runs that had
finished within 3 minutes -- no log read, no time limit.  Without `-f` the
loop only looks safer: it still ends on a name, which any other process (a
second run, an editor with the file open) can hold, and it still has no limit.
The replacement is `tools/bgwait.py wait NAME|PID --timeout S`: by pid and
start time, always bounded, and it prints state, exit code, CPU used and the
log's tail every time it returns.

NARROW ON PURPOSE.  These pass:
  * a one-shot `pgrep` / `ps | grep` / `pidof` (no loop, or no sleep in it);
  * a loop on a PID: `while kill -0 $pid`, `wait $pid`, `tail --pid=$pid`,
    `while ps -p $pid`, `pgrep -P $pid` / `-F pidfile` with no pattern;
  * a loop on a file: `until [ -f routing/R2_plan.json ]; do sleep 5; done`;
  * the pattern as TEXT: inside a quoted string (a commit message, an `echo`,
    a `grep`), a comment, or a here-document that is not fed to a shell (the
    way a README or a test is written) -- only code that will run is judged.
    A script handed to a shell as text IS code: `bash -c '...'`, `eval "..."`,
    `bash <<EOF`, and `$( ... )` inside double quotes.

It reads the command the way a shell would only roughly (quotes, comments,
here-documents, command substitution); a loop it cannot see passes.  It is a
guard against a known mistake, not a wall: any error in this script exits 0,
so it can never block Bash by failing.  Bash's `run_in_background` and plain
`&` are untouched -- how a job is STARTED is free; how it is WAITED ON is not.

Exit 2 = block, with the reason on stderr (Claude Code shows it to the model).
"""
import json
import re
import sys
from pathlib import Path

KP = Path(__file__).resolve().parents[2]
SHELLS = r"(?:bash|sh|zsh|dash|ksh)"
# pgrep options that take a value (procps and BSD): the value is not a pattern
PGREP_VALUE = set("dgGPstuUFrO")
PGREP_LONG_VALUE = ("--delimiter", "--pgroup", "--group", "--parent", "--session", "--terminal", "--euid",
                    "--uid", "--pidfile", "--runstates", "--ns", "--nslist", "--older", "--signal")


class Scan:
    """A command line reduced to the code that will run: quoted text becomes
    the word Q, comments and here-document bodies vanish, command
    substitutions stay.  `inner` collects the scripts handed to a shell as
    text, which are judged like the command itself."""

    def __init__(self, text):
        self.s = text.replace("\\\n", " ")
        self.out = []
        self.inner = []
        self.code(0, None)
        self.text = "".join(self.out)

    def simple(self):
        """The current simple command, as scanned so far."""
        t = "".join(self.out)
        return re.split(r"[;&|\n()]", t)[-1]

    def quoted(self, body):
        """A quoted word just ended: is it a script for a shell?"""
        cmd = self.simple()
        if re.search(rf"(?:^|[\s/]){SHELLS}\s(?:.*\s)?-\w*c\s*$", cmd) or re.search(r"(?:^|\s)eval\s*$", cmd):
            self.inner.append(body)
        self.out.append(" Q ")

    def heredoc(self, i):
        """`<<[-]WORD` at i: cut the body out of the text; keep it as a script
        only when the command reading it is a shell."""
        m = re.match(r"<<(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2", self.s[i:])
        if not m:
            self.out.append("<<")
            return i + 2
        nl = self.s.find("\n", i + m.end())
        if nl >= 0:
            end = re.compile(r"^" + (r"\t*" if m.group(1) else "") + re.escape(m.group(3)) + r"[ \t]*$", re.M)
            e = end.search(self.s, nl + 1)
            body = self.s[nl + 1:e.start() if e else len(self.s)]
            self.s = self.s[:nl + 1] + (self.s[e.end():] if e else "")
            if re.search(rf"(?:^|[\s/]){SHELLS}(?:\s|$)", self.simple()):
                self.inner.append(body)
        self.out.append(" Q ")
        return i + m.end()

    def code(self, i, closer):
        s = self.s
        while i < len(self.s):
            s = self.s                               # a here-document may have been cut out
            c = s[i]
            if closer and c == closer:
                return i + 1
            if c == "\\":
                self.out.append("Q")
                i += 2
            elif c == "'":
                j = s.find("'", i + 1)
                j = len(s) if j < 0 else j
                self.quoted(s[i + 1:j])
                i = j + 1
            elif c == '"':
                i = self.dquote(i + 1)
            elif c == "`":
                self.out.append(" $(")
                i = self.code(i + 1, "`")
                self.out.append(") ")
            elif c == "#" and (i == 0 or s[i - 1] in " \t\n;&|("):
                j = s.find("\n", i)
                i = len(s) if j < 0 else j
            elif s.startswith("$(", i) or c == "(":
                self.out.append(" $(" if c == "$" else "(")
                i = self.code(i + (2 if c == "$" else 1), ")")
                self.out.append(") ")
            elif s.startswith("<<", i) and not s.startswith("<<<", i):
                i = self.heredoc(i)
            else:
                self.out.append(c)
                i += 1
        return i

    def dquote(self, i):
        """Inside "...": text, except what $( ) and backticks will run."""
        start = i
        while i < len(self.s):
            s = self.s
            c = s[i]
            if c == "\\":
                i += 2
            elif c == '"':
                self.quoted(s[start:i])
                return i + 1
            elif s.startswith("$(", i):
                self.out.append(" $(")
                i = self.code(i + 2, ")")
                self.out.append(") ")
            elif c == "`":
                self.out.append(" $(")
                i = self.code(i + 1, "`")
                self.out.append(") ")
            else:
                i += 1
        self.quoted(self.s[start:])
        return i


def pgrep_by_name(args):
    """Does this pgrep name a pattern?  `pgrep -P 123` and `pgrep -F run.pid`
    select by pid; anything left after the options is a pattern."""
    toks = args.split()
    k = 0
    while k < len(toks):
        t = toks[k]
        if t in (">", ">>", "<", "2>", "&>") or re.fullmatch(r"\d*[<>]&?\S*", t):
            k += 1 if re.search(r"[<>]&?\S+$", t) else 2      # a redirection and its target
            continue
        if t.startswith("--"):
            if t.split("=")[0] in ("--pidfile",):
                return False
            k += 1 if "=" in t or t not in PGREP_LONG_VALUE else 2
            continue
        if t.startswith("-") and len(t) > 1:
            if "F" in t:
                return False                                   # a pid file: by pid
            k += 2 if t[-1] in PGREP_VALUE else 1
            continue
        return True
    return False


def name_probe(seg):
    """The by-name process test in a loop's text, or None."""
    for m in re.finditer(r"(?<![\w./-])pgrep\b([^;|&\n)]*)", seg):
        if pgrep_by_name(m.group(1)):
            return "pgrep -f <pattern>" if re.search(r"(?:^|\s)-\w*f", m.group(1)) else "pgrep <name>"
    for m in re.finditer(r"(?<![\w./-])pidof\b([^;|&\n)]*)", seg):
        if any(not t.startswith("-") for t in m.group(1).split()):
            return "pidof <name>"
    for m in re.finditer(r"(?<![\w./-])ps\b([^;|&\n)]*)\|[^;&\n]*?(?<![\w./-])[ef]?grep\b", seg):
        if not re.search(r"(?:^|\s)(?:-\w*[pq]\b|--pid\b|--ppid\b|--quick-pid\b)", m.group(1)):
            return "ps | grep <name>"
    return None


LOOP = re.compile(r"(?:^|[;&|(){}\n]|\bdo\b|\bthen\b|\belse\b)\s*(while|until)\b")


def loops(text):
    """Each `while` / `until` loop of the scanned text, keyword to its own
    `done` (nested loops counted), so a probe between two loops belongs to
    neither."""
    for m in LOOP.finditer(text):
        depth, end = 0, len(text)
        for w in re.finditer(r"(?<![\w./-])(do|done)(?![\w./-])", text[m.end(1):]):
            depth += 1 if w.group(1) == "do" else -1
            if depth <= 0:
                end = m.end(1) + w.end()
                break
        yield m.group(1), text[m.start(1):end]


def verdict(command, depth=0):
    """The reason this command is refused, or None."""
    sc = Scan(command)
    for word, seg in loops(sc.text):
        probe = name_probe(seg)
        if probe and re.search(r"(?<![\w./-])sleep\b", seg):
            return f"a `{word}` loop that polls `{probe}` and sleeps"
    if depth < 3:
        for body in sc.inner:
            why = verdict(body, depth + 1)
            if why:
                return why
    return None


def main():
    try:
        event = json.load(sys.stdin)
        if (event.get("tool_name") or "Bash") != "Bash":
            return 0
        command = (event.get("tool_input") or {}).get("command")
        if not isinstance(command, str) or not command.strip():
            return 0
        why = verdict(command)
    except Exception:                       # a guard that fails must not block Bash
        return 0
    if not why:
        return 0
    bg = KP / "tools" / "bgwait.py"
    print(f"Blocked: {why} -- a wait for a process by NAME. `pgrep -f` matches whole command lines, and this "
          "loop's own shell carries the pattern, so it matches ITSELF and never ends (a board session sat "
          "4 h 52 min on three runs that had finished within 3 minutes); a name also matches any other "
          "process that holds it, and the loop has no time limit.\n"
          "Wait by PID, with a limit:\n"
          f"    python3 {bg} wait NAME|PID ... --timeout 100\n"
          "It returns when all ended or at the timeout (exit 124) and prints each job's state, exit code, "
          "CPU used, CPU in the last interval and log tail. Start a job so that its pid and exit code are "
          "recorded:\n"
          f"    python3 {bg} run --name NAME -- cmd ...\n"
          f"(`python3 {bg} status` lists this session's jobs.) A one-shot pgrep, and a loop on a pid "
          "(`kill -0 $pid`, `tail --pid=$pid`) or on a file, are not blocked.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
