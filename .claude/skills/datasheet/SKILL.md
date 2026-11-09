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
    python3 tools/parts/extract.py <MPN> [--pages 70-95|auto]  # beside the PDF: .tables.json .pins.csv .pages.json .land.json
    python3 tools/parts/validate_extract.py <MPN>.pins.csv <PINOUT.csv>     # against a hand-made pinout
    python3 tools/parts/validate_extract.py <MPN>.pins.csv --kicad <MPN>    # against KiCad's stock symbol
    python3 tools/parts/kicad_pins.py <MPN>               # which stock symbol, its pins
    python3 tools/parts/extract_corpus.py [-v]            # the extractor measured on 30 vendors' datasheets

`datasheet.py` starts `extract.py` in the background after filing a PDF when
the venv exists (log in `out/extract/`; `HW_EXTRACT=0` turns it off), and
`publish.py` copies and hashes its outputs beside the PDF into
`handoff/datasheets/`. `tables` is Docling in text-layer mode (no OCR); a PDF
with no text layer (a scan) is skipped with one line. Past 40 pages it reads
only the pin-table pages (`--pages auto`): the pages whose text names a pin
table, the pages that continue one and up to two before it (the i.MX 8M Mini:
24 of 96 pages, the 486 balls in 2 min). Its pin table reads every table
whose header — up to three stacked rows — names a pin / ball / package column
and a name column; one column per package ("Pin Number" over QFN | TQFP,
"121 BGA | 64 LQFP") gives one set of pins each (`pins.csv`'s `package`
column); a continuation takes the previous table's columns, a supply row's
many balls each get its name, rows Docling merged are paired in order, a
range ("DB6–DB1" over "2–7") is expanded, footnote digits are dropped.
`pages` (ColQwen2.5) indexes the pages for "pin assignment", "recommended
land pattern", "absolute maximum ratings", "package outline"; `land`
(Qwen2.5-VL-7B through Ollama) reads the land-pattern page.

**Read by nothing: it has not passed.** Two ground truths: a board's
hand-made PINOUT (the i.MX 8M Mini: 486 of 486), and KiCad's stock symbols,
drawn by its librarians from the same PDFs — `extract_corpus.py` picks 3
symbols a vendor from 10 vendors, fetches their datasheets into
`out/extract_corpus/` (never the vendor store) and grades. 2026-11-09: 19 of
30 fetched (ST, Silicon Labs and FTDI refuse even headless Chrome), 461 of 728
pins named alike, 2 parts exact (MKL46Z256, MAX98396), five within 1–3 pins
(mostly KiCad's renaming: `VDDA` for the datasheet's `VDDAD`, `EP` for
"PowerPAD"). What it cannot read: a pinout that is only a drawing (ATmega324PB,
ISSI SRAMs, LTC1562 — 4 of 19), prose in a table (MAX22208), rows Docling
merges across pins (PIC18), a ball map grid (MSP430 YFF), a row naming eight
pins with no per-pin order (MAX7221). So no consumer (`symgen.py`,
`fp.py verify --against`, the reviewer checklist) is wired; re-run
`extract_corpus.py` after any parser change and wire one only when it
passes. `pages` and `land` have not run (Ollama not installed here).

