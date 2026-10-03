---
name: datasheet
description: >
  Get a datasheet PDF onto disk with provenance, price and stock a part, or
  discover candidate parts, through tools/parts/. Triggers on "datasheet",
  "find a part", "price", "stock", "lead time", "MPN", "pin table",
  "absolute maximum", "is this part available", "alternatives for".
---

# Parts and datasheets

## Market data first, web second

    .venv/bin/python tools/parts/fetch.py <MPN> [<MPN> ...]   # price, stock, lead time
    .venv/bin/python tools/parts/fetch.py --refresh <MPN>     # ignore the 7-day cache
    .venv/bin/python tools/parts/search.py --help             # discover candidates in a category

Four distributor APIs (DigiKey, Mouser, element14, Nexar), cached with a
timestamp in `reference/market/`. A source that fails is a failure, never
"no stock". Keys: `~/.config/hwdesign/api.env` (SETUP.md §4) — never print,
log or paste one. Web search only when these have not delivered, and **never
quote a price or spec from a search snippet**.

## A datasheet is a FILE

    .venv/bin/python tools/parts/datasheet.py <MPN> [<MPN> ...]   # the whole chain
    .venv/bin/python tools/parts/datasheet.py --list
    .venv/bin/python tools/parts/datasheet.py --url <URL> <MPN>   # a URL you found
    .venv/bin/python tools/parts/datasheet.py --file <PATH> <MPN> # a PDF you saved
    #   add --mfr "<Manufacturer>" when no distributor knows the part (else it files under unknown/)

It files `reference/vendor/<mfr>/<MPN>.pdf` and indexes it (source URL, size,
sha256, date) in `reference/vendor/datasheets.json`. Read it with the Read
tool's `pages`; cite page and table when a fact is first used. Nothing else
puts a PDF in `reference/vendor/` — a hand-dropped file has no provenance.

The automatic chain: the local copy; the datasheet URLs the distributor APIs
return (manufacturer's own first); a refetch of a record that predates URL
capture; DigiKey's media list (often a mirror of a PDF whose own host blocks
scripts); then every failed URL again through **local headless Chrome** — a
host can refuse a script and answer a browser, so a scripted 403 or timeout
is never proof the file is unreachable.

**When it fails it prints one of two next steps — read which:**

- **A URL even headless Chrome could not take** (login, click-through, a
  JavaScript viewer): open it with the Chrome MCP, save the PDF, then
  `--file` it. ⚠ First `list_connected_browsers` and `select_browser` the one
  with `isLocal: true`, or the file lands on another machine.
- **No URL at all** — a catalogue problem or a wrong MPN: Konnect's
  `integration` toolset (`download_jlcpcb_database()` once per machine, then
  `get_datasheet_url(mpn=...)`), then a web search for the manufacturer's own
  page, then `--url`.

A datasheet a board needs reaches it only through the handoff: name the part
in the board's data, or add it to `DATASHEETS_EXTRA`, and publish.
