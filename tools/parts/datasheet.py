#!/usr/bin/env python
"""
Get the DATASHEET PDF onto disk, and record where it came from.

    python tools/parts/datasheet.py <MPN> [<MPN> ...]   # auto: URL -> PDF
    python tools/parts/datasheet.py --refresh <MPN>      # ignore local copy
    python tools/parts/datasheet.py --url <URL> <MPN>    # a URL you found
    python tools/parts/datasheet.py --file <PATH> <MPN>  # a PDF you saved
    python tools/parts/datasheet.py --file <PATH> --mfr "<Manufacturer>" <MPN>
                                                         # ... when no distributor knows the part
    python tools/parts/datasheet.py --list               # what is registered

WHY THIS EXISTS.  The distributor APIs already answer "where is the datasheet"
-- `market.py` keeps that URL from all four sources -- but a URL is not a
file, and datasheet facts read off web pages are what CLAUDE.md forbids.  A
local PDF is also the only thing that can be CITED by page and table.

THE CHAIN, in the order it is tried.  Each step exists because the one above
it demonstrably fails on real parts:

  1. local copy in reference/vendor/, if already fetched (free, offline)
  2. the URLs in the cached market record, authority first
     (element14 = manufacturer's own, DigiKey = CDN mirror, Mouser, Nexar)
  3. a refetch of the record, if it predates datasheet capture or has no URL
  4. DigiKey's /media endpoint, which lists every document it holds and often
     has an mm.digikey.com MIRROR of a manufacturer PDF whose own host blocks
  5. the LOCAL headless Chrome, which is not fingerprint-blocked the way a
     script is.  www.analog.com drops urllib and curl in silence -- no bytes,
     at any timeout, with or without browser headers -- and answers Chrome
     200 with the identical PDF.  This needs no MCP and no browser choice.
  6. --url  : a URL from Konnect MCP get_datasheet_url (LCSC/JLCPCB side) or
              a web search -- a different URL universe from the four APIs
  7. --file : a PDF from the Chrome MCP, i.e. the user's REAL profile, for
              what headless cannot do either: a login, a click-through, or a
              JavaScript viewer that never exposes the file
              (docs.altera.com serves one of those)

Steps 6 and 7 are how a manual find gets the same filing and provenance as an
automatic one; never drop a PDF into reference/vendor/ by hand.

⚠ A 200 IS NOT A PDF.  docs.altera.com answers 200 with 2.6 kB of HTML viewer
shell for what the API calls a datasheet URL.  Every download here is checked
for the %PDF magic and an HTML body is reported as a failure with its reason,
never filed as a datasheet.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market                                              # noqa: E402

VENDOR = os.path.join(market.ROOT, "reference", "vendor")
INDEX = os.path.join(VENDOR, "datasheets.json")

# Some manufacturer sites answer 403 to urllib's default User-Agent.  This is
# not evasion of a paywall or a rate limit: the file is public and free, the
# header is simply what an ordinary browser sends.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


def _slug(s, default="unknown"):
    # ⚠ Fold diacritics first: DigiKey says "Würth Elektronik" and element14
    # "WURTH ELEKTRONIK"; without folding, one manufacturer's parts end up
    # split across two folders.
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    t = "".join(c if c.isalnum() else "-" for c in s.lower())
    while "--" in t:
        t = t.replace("--", "-")
    return t.strip("-") or default


def _safe(mpn):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in mpn)


def _cached_rec(mpn):
    """The market record WITHOUT going to the network -- for --url/--file,
    where all that is wanted is the manufacturer name to file the PDF under."""
    p = market._cache_path(mpn)
    return json.load(open(p)) if os.path.exists(p) else {}


def load_index():
    if os.path.exists(INDEX):
        return json.load(open(INDEX))
    return {}


def _save_index(ix):
    os.makedirs(VENDOR, exist_ok=True)
    json.dump(ix, open(INDEX, "w"), indent=1, sort_keys=True)


def _rel(path):
    return os.path.relpath(path, market.ROOT)


def _is_pdf(blob):
    return blob[:1024].lstrip()[:5] == b"%PDF-"


def _why_not_pdf(blob, ctype):
    head = blob[:400].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html") or b"<html" in head:
        return f"HTML not PDF ({len(blob)} B, {ctype}) -- viewer shell or block page"
    return f"not a PDF ({len(blob)} B, {ctype})"


def download(url, timeout=25):
    """(blob, None) or (None, reason).  Redirects are followed by urllib."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/pdf,application/octet-stream,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            blob = r.read()
            ctype = r.headers.get("Content-Type", "?")
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} {e.reason}"
    except TimeoutError:
        # Not a slow file: these hosts accept the TCP connection and then send
        # nothing, which is what bot protection looks like from a script.
        return None, f"no answer in {timeout}s -- bot protection, needs a browser"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    if not blob:
        return None, "empty body"
    if not _is_pdf(blob):
        return None, _why_not_pdf(blob, ctype)
    return blob, None


# the same list tools/setup/check.py tests and kicad_project/tools/fp/fp.py uses
CHROME_BINS = tuple(b for b in (
    os.environ.get("CHROME_BIN"), "google-chrome-stable", "google-chrome",
    "chromium", "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium") if b)

# The page fetches the PDF itself and hands the bytes back as base64 in the
# DOM, because --dump-dom is the only channel headless Chrome offers without a
# DevTools client, and a cross-origin PDF navigation would just open the
# viewer with nothing saved.
_GRAB = """<html><body><pre id="o">START</pre><script>
(async () => {
  try {
    const r = await fetch(%s, {credentials: "omit"});
    const b = new Uint8Array(await r.arrayBuffer());
    let s = ""; const C = 0x8000;
    for (let i = 0; i < b.length; i += C)
      s += String.fromCharCode.apply(null, b.subarray(i, i + C));
    document.getElementById("o").textContent =
      "HWDS_OK " + r.status + " " + b.length + " " + btoa(s);
  } catch (e) { document.getElementById("o").textContent = "HWDS_ERR " + e; }
})();
</script></body></html>"""


def chrome_fetch(url, timeout=90):
    """(blob, None) or (None, reason), via the local headless Chrome.

    ⚠ --disable-web-security is what lets the local file:// page read a public
    PDF cross-origin; it is confined to a THROWAWAY profile created and deleted
    here, never the user's own, and the page is one we wrote ourselves.
    """
    exe = next((b for b in CHROME_BINS if shutil.which(b) or os.access(b, os.X_OK)), None)
    if not exe:
        return None, "no local Chrome/Chromium found"
    d = tempfile.mkdtemp(prefix="hwds-")
    try:
        page = os.path.join(d, "grab.html")
        with open(page, "w") as f:
            f.write(_GRAB % json.dumps(url))
        try:
            p = subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--disable-web-security", "--user-data-dir=" + os.path.join(d, "prof"),
                 "--virtual-time-budget=%d" % (timeout * 1000 // 3),
                 "--dump-dom", "file://" + page],
                capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, f"local Chrome gave up after {timeout}s"
        dom = p.stdout.decode("utf8", "replace")
        m = re.search(r"HWDS_OK (\d+) (\d+) ([A-Za-z0-9+/=]+)", dom)
        if not m:
            e = re.search(r"HWDS_ERR ([^<]{0,120})", dom)
            return None, ("chrome: " + e.group(1).strip()) if e else \
                         "chrome returned no payload (blocked or still loading)"
        status, n, b64 = int(m.group(1)), int(m.group(2)), m.group(3)
        blob = base64.b64decode(b64)
        if len(blob) != n:
            return None, f"chrome: truncated ({len(blob)} of {n} B)"
        if not _is_pdf(blob):
            return None, _why_not_pdf(blob, f"chrome HTTP {status}")
        return blob, None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _file_for(mpn, mfr):
    d = os.path.join(VENDOR, _slug(mfr))
    return os.path.join(d, _safe(mpn) + ".pdf")


def _register(mpn, path, blob, url, source, mfr):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(blob)
    ix = load_index()
    ix[mpn] = {"path": _rel(path), "url": url, "source": source,
               "mfr": mfr, "bytes": len(blob),
               "sha256": hashlib.sha256(blob).hexdigest()[:16],
               "fetched": time.strftime("%Y-%m-%d")}
    _save_index(ix)
    return ix[mpn]


def get(mpn, refresh=False, url=None, path=None, mfr=None):
    """Run the chain for one MPN.  Returns a dict:

      {"mpn", "status": local|downloaded|registered|failed, "entry"?,
       "attempts": [(source, url, reason), ...], "note"?}
    """
    ix = load_index()
    if not refresh and not url and not path and mpn in ix:
        e = ix[mpn]
        if os.path.exists(os.path.join(market.ROOT, e["path"])):
            return {"mpn": mpn, "status": "local", "entry": e, "attempts": []}

    # step 7 -- a file somebody already pulled down (Chrome MCP, browser)
    if path:
        if not os.path.isfile(os.path.expanduser(path)):
            return {"mpn": mpn, "status": "failed", "attempts": [("--file", path, "no such file")]}
        blob = open(os.path.expanduser(path), "rb").read()
        if not _is_pdf(blob):
            return {"mpn": mpn, "status": "failed", "attempts":
                    [("--file", path, _why_not_pdf(blob, "local file"))]}
        rec = _cached_rec(mpn)
        mfr = mfr or (market.mfr_of(rec) if rec else None)
        # the file's NAME only: the index is published into every handoff, and
        # "/home/<you>/Downloads/..." is nobody's business and no use to a reader
        e = _register(mpn, _file_for(mpn, mfr), blob, url or "local file " + os.path.basename(path),
                      "manual", mfr)
        return {"mpn": mpn, "status": "registered", "entry": e, "attempts": []}

    attempts, note = [], None
    if url:                                     # step 6 -- a URL somebody found
        cands = [("manual", url)]
        rec = _cached_rec(mpn)
    else:
        market.load_env()
        rec = market.fetch(mpn)                 # step 2 -- cache-first
        cands = market.datasheet_urls(rec)
        if not cands:                           # step 3 -- record predates capture
            rec = market.fetch(mpn, refresh=True)
            cands = market.datasheet_urls(rec)
            note = "record refetched: the cached one carried no datasheet URL"
    mfr = mfr or (market.mfr_of(rec) if rec else None)

    tried, mirrors_tried = set(), bool(url)   # --url means: just that one
    chrome_tried = False
    while cands:
        source, u = cands.pop(0)
        tried.add(u)
        blob, why = (chrome_fetch(u) if source.startswith("chrome:")
                     else download(u))
        if blob:
            e = _register(mpn, _file_for(mpn, mfr), blob, u, source, mfr)
            return {"mpn": mpn, "status": "downloaded", "entry": e,
                    "attempts": attempts, "note": note}
        attempts.append((source, u, why))
        if not cands and not mirrors_tried:     # step 4 -- mirrors, once
            mirrors_tried = True
            cands = [("dk-media", m) for m in market.digikey_media_urls(mpn)
                     if m not in tried]
        if not cands and not chrome_tried:      # step 5 -- the local browser
            # Every URL has now been refused a SCRIPT.  The same URLs, in
            # Chrome, are a different client and usually just work, so retry
            # them all rather than escalating to a human.
            chrome_tried = True
            cands = [("chrome:" + src, u2) for src, u2, _ in attempts
                     if u2 != "-"]
    for name, err in market.failed_sources(rec or {}).items():
        attempts.append((name, "-", f"source FAILED: {str(err)[:70]}"))
    return {"mpn": mpn, "status": "failed", "attempts": attempts, "note": note}


# Two different failures, two different next steps -- printing one ladder for
# both sends you to the wrong tool half the time.  A URL that was tried and
# refused is a HOST problem (browser); no URL at all is a CATALOGUE problem
# (another catalogue, or a search).
ESC_BLOCKED = """
The URL(s) above exist; neither a script nor the LOCAL HEADLESS Chrome got the
file.  That leaves a login, a click-through, or a viewer that never exposes the
PDF.  Next, in order -- both routes file it with provenance like an automatic
fetch:

  1. Chrome MCP -- the user's REAL profile, with its cookies and sessions.
     ⚠ Several browsers may be connected to the account; use the one on THIS
     machine (list_connected_browsers -> isLocal: true), or a download lands on
     a different computer.  Open the failing URL, save the PDF, then
       python tools/parts/datasheet.py --file ~/Downloads/<file>.pdf {mpn}
  2. WebSearch for the manufacturer's own datasheet page (never quote the
     snippet -- CLAUDE.md), then
       python tools/parts/datasheet.py --url <URL> {mpn}
"""

ESC_NO_URL = """
No distributor here has a datasheet URL for this MPN at all.  Next, in order:

  1. Konnect MCP, a catalogue the four APIs do not cover (LCSC/JLCPCB):
       load_toolset("integration")
       download_jlcpcb_database()        # once per machine, the lookup is local
       get_datasheet_url(mpn="{mpn}")
     then: python tools/parts/datasheet.py --url <URL> {mpn}
  2. WebSearch for the manufacturer's own datasheet page, then --url it.
  3. Chrome MCP (the browser on THIS machine -- isLocal: true), then
       python tools/parts/datasheet.py --file ~/Downloads/<file>.pdf {mpn}

⚠ Check the MPN first: no URL from four distributors usually means the part
number is wrong or obsolete, not that the datasheet is hard to find.
"""


def main(argv):
    if "--list" in argv:
        ix = load_index()
        for m in sorted(ix):
            e = ix[m]
            print(f"{m:<24} {e['bytes']/1e6:6.2f} MB  {e['source']:<10} "
                  f"{e['fetched']}  {e['path']}")
        print(f"\n{len(ix)} datasheet(s) in {_rel(VENDOR)}")
        return 0
    refresh = "--refresh" in argv
    url = pathopt = None
    mfr = None
    for flag in ("--url", "--file", "--mfr"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 >= len(argv):
                print(f"{flag} needs a value")
                return 1
            val = argv[i + 1]
            del argv[i:i + 2]
            if flag == "--url":
                url = val
            elif flag == "--file":
                pathopt = val
            else:
                mfr = val
    mpns = [a for a in argv if not a.startswith("--")]
    if not mpns:
        print(__doc__)
        return 1
    if (url or pathopt) and len(mpns) != 1:
        print("--url/--file take exactly one MPN")
        return 1

    bad = 0
    for m in mpns:
        r = get(m, refresh=refresh, url=url, path=pathopt, mfr=mfr)
        if r.get("note"):
            print(f"  note: {r['note']}")
        if r["status"] == "failed":
            bad += 1
            print(f"{m:<24} FAILED")
            for source, u, why in r["attempts"]:
                print(f"  {source:<10} {why}\n             {u}")
            srcs = [a[0] for a in r["attempts"]]
            if srcs == ["--file"] and r["attempts"][0][2] == "no such file":
                pass
            elif srcs == ["--file"]:
                print("  that file is not a PDF -- if it came from a browser, "
                      "it is probably the block page, not the datasheet.")
            else:
                tried_a_url = any(u != "-" for _, u, _ in r["attempts"])
                print((ESC_BLOCKED if tried_a_url else ESC_NO_URL).format(mpn=m))
        else:
            e = r["entry"]
            tag = {"local": "have", "downloaded": "got",
                   "registered": "filed"}[r["status"]]
            print(f"{m:<24} {tag:<6} {e['bytes']/1e6:6.2f} MB  "
                  f"{e['source']:<10} {e['path']}")
            for source, u, why in r["attempts"]:
                print(f"  ⚠ {source} failed first: {why}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
