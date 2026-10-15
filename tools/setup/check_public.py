#!/usr/bin/env python3
"""
Before a repository goes public: is there anything in it that should not be?

    ./setup.sh check-public                  # what a commit and push would publish
    ./setup.sh check-public --history        # ... and every line ever committed
    ./setup.sh check-public --install-hook   # run it (with --history) before every git push
    ./setup.sh check-public --words "Acme,Project Falcon"   # extra private words
    ./setup.sh check-public --secrets-file ~/.config/other/api.env
    python3 tools/setup/check_public.py --rev HEAD   # from a worktree: this commit and its ancestry only

It reads exactly the files git would publish -- tracked, plus untracked files
that .gitignore does not exclude -- and reports:

  FAIL  secrets: private keys, cloud and git tokens, a distributor API key
        assignment with a value, and any VALUE from your own key files
        (~/.config/hwdesign/api.env, $HWDESIGN_API_ENV, --secrets-file) found
        anywhere in the text.  Values are never printed.
  FAIL  personal identifiers: your home path (/home/<you>/...), Claude's
        per-session scratch paths, your git e-mail, your hostname and account,
        e-mail addresses in general (noreply and example.* addresses pass),
        and every word in ~/.config/hwdesign/private_words.txt and --words
        (customers, codenames, your own name: one per line, kept OUTSIDE the repo).
  FAIL  files that are never published: key files, *.env, the per-machine
        settings (settings.local.json, local.json, relay.json), venvs.
  FAIL  your git identity: commits carry user.email (GIT_COMMITTER_EMAIL in
        the environment first, as git itself).  Use the address GitHub gives
        you (<id>+<login>@users.noreply.github.com) for this repository unless
        you mean to publish yours (--allow-email).  The history scanned is the
        local branches and tags -- what a push can publish -- never the
        remote-tracking refs or a filter-branch backup, so a rewrite that
        removes an address is accepted before it is pushed.
  WARN  third-party documents (vendor datasheet PDFs, distributor API caches):
        usually copyrighted or under terms of use that forbid redistribution.
  WARN  binaries and files over 1 MB; Claude session links.

A line that is deliberately fine carries the marker  public-check: ok  and is
skipped.  Exit 1 on any FAIL (or any WARN with --strict).
"""
import argparse
import getpass
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()
CFG = Path(os.environ.get("HWDESIGN_CONFIG", "~/.config/hwdesign")).expanduser()
API_ENV = Path(os.environ.get("HWDESIGN_API_ENV", str(CFG / "api.env"))).expanduser()
WORDS_FILE = CFG / "private_words.txt"
OK_MARK = "public-check: " + "ok"          # split so this file does not mark itself

SECRETS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("API key (sk-...)", re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{24,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("distributor key assignment", re.compile(
        r"\b(NEXAR|DIGIKEY|MOUSER|ELEMENT14)_[A-Z_]*(ID|SECRET|KEY|TOKEN)\s*[=:]\s*['\"]?[A-Za-z0-9._~+/-]{8,}")),
    ("credential assignment", re.compile(
        r"(?i)\b(password|passwd|client_secret|api_key|apikey|secret_key|access_token)\s*[=:]\s*['\"][^'\"\s]{12,}['\"]")),
]
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
EMAIL_OK = re.compile(r"(@users\.noreply\.github\.com|^noreply@|@example\.(com|org|net)$|^git@github\.com$)", re.I)
SCRATCH = re.compile(r"/tmp/claude-\d+/|-home-[a-z0-9_.-]+-|claude\.ai/code/session_\w+")
NEVER = [re.compile(p) for p in (
    r"(^|/)\.env$", r"\.env$", r"\.pem$", r"\.key$", r"(^|/)id_(rsa|ed25519|ecdsa)",
    r"(^|/)settings\.local\.json$", r"^kicad_project/(local|relay)\.json$",
    r"(^|/)\.venv/", r"(^|/)__pycache__/", r"\.kicad_prl$", r"(^|/)api\.env$")]
THIRD_PARTY = [
    (re.compile(r"^reference/vendor/.*\.pdf$|/handoff/datasheets/.*\.pdf$"),
     "a vendor datasheet: usually copyrighted -- publish the MPN and the URL, not the PDF"),
    (re.compile(r"^reference/market/"), "a distributor API cache: their terms usually forbid redistributing it"),
    (re.compile(r"^kicad_project/\.claude/(skills/(konnect|kicad-)|agents/kicad-)"),
     "Konnect's generated files (AGPL-3.0): ./setup.sh konnect-sync makes them per machine"),
]
BINARY_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz", ".7z", ".step", ".stp",
              ".wrl", ".jar", ".exe", ".so", ".dll", ".bin", ".xlsx", ".docx", ".pptx"}


def git(*args):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return r.returncode, r.stdout


def files():
    rc, out = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if rc != 0:
        sys.exit("check-public: not a git repository (git init first, so .gitignore decides "
                 "what would be published)")
    return sorted(f for f in out.split("\0") if f and (ROOT / f).is_file())


def key_values(paths):
    """{value: 'file:KEY'} for every non-trivial value in the key files."""
    out = {}
    for p in paths:
        p = Path(p).expanduser()
        if not p.is_file():
            continue
        for line in p.read_text(errors="replace").splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                v = m.group(2).strip("'\"")
                if len(v) >= 8:
                    out[v] = f"{p.name}:{m.group(1)}"
    return out


def personal(words_arg):
    rc, name = git("config", "user.name")
    rc, mail = git("config", "user.email")
    name = os.environ.get("GIT_COMMITTER_NAME") or name          # git's own precedence
    mail = os.environ.get("GIT_COMMITTER_EMAIL") or mail
    words = {w.strip() for w in (words_arg or "").split(",")}
    if WORDS_FILE.is_file():
        words |= {w.strip() for w in WORDS_FILE.read_text().splitlines() if not w.startswith("#")}
    user = getpass.getuser()
    host = socket.gethostname().split(".")[0]
    generic = {"localhost", "ubuntu", "debian", "fedora", "user", "root", "admin", "desktop",
               "laptop", "computer", "workstation", "home", "linux", "macbook", "pc"}
    # Your host and account are matched EXACTLY (case included).  Your git
    # user.name is not a private word: every commit publishes it anyway -- the
    # report says which name that is, so you can change it before the first push.
    exact = {w for w in (host, user if len(user) >= 4 else "")
             if len(w) >= 3 and w.lower() not in generic}
    words.add(mail.strip())
    words = {w for w in words if len(w) >= 3 and w.lower() not in generic}
    return (sorted(words, key=len, reverse=True), sorted(exact - words, key=len, reverse=True),
            user, mail.strip(), name.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", action="store_true", help="also scan every line ever committed")
    ap.add_argument("--words", help="comma-separated private words, in addition to private_words.txt")
    ap.add_argument("--secrets-file", action="append", default=[], help="another KEY=value file whose values must not appear")
    ap.add_argument("--allow-email", action="store_true", help="your git user.email may be published")
    ap.add_argument("--rev", help="scan the history of this revision only (giveback.py, from a worktree), "
                                  "not every local branch")
    ap.add_argument("--strict", action="store_true", help="WARN fails too")
    ap.add_argument("--install-hook", action="store_true", help="install a git pre-push hook that runs this")
    a = ap.parse_args()

    if a.install_hook:
        hook = ROOT / ".git" / "hooks" / "pre-push"
        if not (ROOT / ".git").is_dir():
            sys.exit("check-public: no .git here")
        if hook.exists() and "check_public.py" not in hook.read_text():
            sys.exit(f"check-public: {hook} exists and is someone else's; add this line to it:\n"
                     f"  python3 \"$(git rev-parse --show-toplevel)/tools/setup/check_public.py\" --history")
        hook.write_text("#!/bin/sh\n# installed by ./setup.sh check-public --install-hook\n"
                        "exec python3 \"$(git rev-parse --show-toplevel)/tools/setup/check_public.py\" --history\n")
        hook.chmod(0o755)
        print(f"installed {hook.relative_to(ROOT)}: every git push runs the check first")
        return 0

    fails, warns = [], []
    words, exact, user, mail, gname = personal(a.words)
    secrets = key_values([API_ENV, *a.secrets_file])
    home = str(Path.home())
    homes = {home, f"/home/{user}", f"/Users/{user}"}
    word_re = [(w, re.compile(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", re.I)) for w in words]
    word_re += [(w, re.compile(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])")) for w in exact]

    def scan_line(where, line, fl, wn):
        if OK_MARK in line:
            return
        for label, rx in SECRETS:
            m = rx.search(line)
            if m:
                fl.append(f"{where}  {label}: {m.group(0)[:6]}...")
        for v, src in secrets.items():
            if v in line:
                fl.append(f"{where}  the value of {src} (a real credential) appears here")
        for h in homes:
            if h + "/" in line or line.rstrip().endswith(h):
                fl.append(f"{where}  your home path {h}/... (write ~ or a repo-relative path)")
        m = SCRATCH.search(line)
        if m:
            (wn if "session_" in m.group(0) else fl).append(
                f"{where}  a Claude session path or link: {m.group(0)[:40]}")
        for e in EMAIL.findall(line):
            if not EMAIL_OK.search(e) and not (a.allow_email and e.lower() == mail.lower()):
                fl.append(f"{where}  e-mail address {e}")
        for w, rx in word_re:
            if rx.search(line) and not (a.allow_email and w.lower() == mail.lower()):
                fl.append(f"{where}  private word '{w}'")

    fs = files()
    for f in fs:
        for rx in NEVER:
            if rx.search(f):
                fails.append(f"{f}  is never published (credential, per-machine or generated file): "
                             "add it to .gitignore, and `git rm --cached` it if it is tracked")
                break
        for rx, why in THIRD_PARTY:
            if rx.search(f) and not f.endswith(".gitkeep"):
                warns.append(f"{f}  {why}")
        p = ROOT / f
        size = p.stat().st_size
        if size > 1_000_000:
            warns.append(f"{f}  {size / 1e6:.1f} MB")
        if p.resolve() == SELF:
            continue
        data = p.read_bytes()
        if b"\0" in data[:4096] or p.suffix.lower() in BINARY_EXT:
            if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif"):
                warns.append(f"{f}  a binary file: not scanned for text")
            continue
        for n, line in enumerate(data.decode("utf8", "replace").splitlines(), 1):
            scan_line(f"{f}:{n}", line, fails, warns)

    # the identity every commit will carry
    if not mail:
        warns.append("git user.email is not set: commits would use an address git guesses from this machine")
    elif not a.allow_email and not mail.lower().endswith("@users.noreply.github.com"):
        fails.append(f"git user.email is {mail}: every commit publishes it. For this repo: "
                     "git config user.email <id>+<login>@users.noreply.github.com "
                     "(GitHub -> Settings -> Emails), or pass --allow-email")
    print(f"note: commits will carry the name '{gname or getpass.getuser()}' and the address "
          f"'{mail or '(unset)'}'" + ("" if gname else " (git user.name is unset: git uses your account name)"))
    hist = [a.rev] if a.rev else ["--branches", "--tags"]
    rc, authors = git("log", *hist, "--format=%ae%n%ce")
    for e in sorted(set(authors.split())):
        if not EMAIL_OK.search(e) and not (a.allow_email and e.lower() == mail.lower()):
            fails.append(f"history: a commit carries the address {e} (rewriting history is the only fix)")

    if a.history:
        rc, log = git("log", *hist, "-p", "--no-color", "--format=commit %h")
        commit = "?"
        cur = "?"
        for line in log.splitlines():
            if line.startswith("commit "):
                commit = line.split()[1]
            elif line.startswith("+++ b/"):
                cur = line[6:]
            elif line.startswith("+") and not line.startswith("+++") and cur != str(SELF.relative_to(ROOT)):
                scan_line(f"history {commit} {cur}", line[1:], fails, warns)

    for w in warns:
        print("WARN ", w)
    for f in fails:
        print("FAIL ", f)
    n = len(fs)
    print(f"\n{n} file(s) would be published"
          + (" (history included)" if a.history else "")
          + f": {len(fails)} FAIL, {len(warns)} WARN."
          + ("" if words else "  Tip: list private words in " + str(WORDS_FILE).replace(home, "~")))
    return 1 if fails or (a.strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
