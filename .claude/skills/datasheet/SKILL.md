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

A fetch path that failed first and worked second — a portal, a viewer, a
manufacturer's URL pattern — is a lesson for `tools/parts/datasheet.py` or
this skill, and goes to the template (`kicad_project/SELF_IMPROVEMENT.md`).
The PDF and its provenance stay in this project.

## Extraction — candidates, never authority (optional)

    ./setup.sh install-ml [--pages]                       # tools/parts/.venv-ml: Docling (+ ColQwen2.5)
    python3 tools/parts/extract.py --check                # which stages this machine can run
    python3 tools/parts/extract.py <MPN> [--pages 70-95]  # beside the PDF: .tables.json .pins.csv .pages.json .land.json
    python3 tools/parts/validate_extract.py <MPN>.pins.csv <PINOUT.csv>

`datasheet.py` starts `extract.py` in the background after filing a PDF when
the venv exists (log in `out/extract/`; `HW_EXTRACT=0` turns it off), and
`publish.py` copies and hashes its outputs beside the PDF into
`handoff/datasheets/`. `tables` is Docling in text-layer mode (no OCR); its
pin table reads every table whose header row (the first row, or one under a
row of group labels) names a pin / ball column and a name column — a
continuation on the next page takes the previous table's columns, a supply
row's many balls each get its name, rows Docling merged are paired in order,
footnote digits are dropped. `pages` (ColQwen2.5) indexes the pages for "pin
assignment", "recommended land pattern", "absolute maximum ratings", "package
outline"; `land` (Qwen2.5-VL-7B through Ollama) reads the land-pattern page.

**Read by nothing until validated.** On the one hand-made pinout the
projects have — the i.MX 8M Mini's 486 balls (`IMX8MMIEC` table 66, pages
70-95, 2 min 53 s on 24 CPU cores) — the pin table matched 486 of 486. No
consumer (`symgen.py`, `fp.py verify --against`, the reviewer checklist) is
wired yet: one datasheet is the whole corpus, and `pages` and `land` have not
run on a machine with a GPU or Ollama. Validate each on more filed
datasheets before wiring it; a pinout typed from a datasheet is a new
validation pair.

