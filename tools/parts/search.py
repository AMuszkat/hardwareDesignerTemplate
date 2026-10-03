#!/usr/bin/env python
"""
DISCOVERY, not lookup.  Sweeps a DigiKey category for parts that could work,
cheapest first, instead of pricing a list somebody already wrote.

    python tools/parts/search.py --keywords "buck converter"         # find category ids
    python tools/parts/search.py --category 739 \\
        --param "Voltage - Input (Max)>=24" --param "Current - Output>=2" \\
        --max-price 3 --min-stock 100
    python tools/parts/search.py --category 739 --by-manufacturer ...  # see past 300
    python tools/parts/search.py ... --raw out.json                    # keep full records

WHY.  `fetch.py` answers "what does this part cost" -- useful, but it can only
confirm or kill a candidate somebody already thought of.  A candidate list
built from web searches and vendor selector pages keeps circling the same few
parts.  This walks the catalogue sorted by price ASCENDING and reports what
actually exists under the gates, which can return parts nobody named.

--param "<parameter name><op><value>": op is >=, <=, = or ~ (contains, case-
insensitive); numbers accept k/M suffixes and units ("43.8k", "1.1V").  The
parameter names are DigiKey's ParameterText; `--raw` shows them.

⚠ WHAT THE CATALOGUE CANNOT FILTER ON.  The parametric fields a category
exposes are rarely the gate that actually binds a design.  Filter on what IS
exposed, then read the datasheet of whatever survives.  Treat the output as a
shortlist to investigate, never as a pass list.

⚠ DigiKey refuses Offset > 300 (HTTP 400), so one price-sorted sweep stops at
the 300th cheapest part and everything above it is INVISIBLE.  --by-manufacturer
partitions the sweep by manufacturer (each one's in-stock list is usually under
300), so the whole category is seen.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market                                              # noqa: E402

DK_SEARCH = "https://api.digikey.com/products/v4/search/keyword"
DK_MAX_OFFSET = 300


def _headers():
    tok = market._token("digikey", "https://api.digikey.com/v1/oauth2/token",
                        "DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")
    return {"Authorization": f"Bearer {tok}",
            "X-DIGIKEY-Client-Id": os.environ["DIGIKEY_CLIENT_ID"],
            "X-DIGIKEY-Locale-Site": "US",
            "X-DIGIKEY-Locale-Currency": "USD",
            "Content-Type": "application/json"}


def _num(s):
    """Parametric values arrive as display strings: '197', '43.8k', '1.1V'."""
    if s is None:
        return None
    t = str(s).strip().replace(",", "")
    m = re.match(r"\s*([-+]?\d*\.?\d+)\s*([kKMGmuµnp]?)", t)
    if not m:
        return None
    mult = {"k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "m": 1e-3, "u": 1e-6,
            "µ": 1e-6, "n": 1e-9, "p": 1e-12, "": 1}[m.group(2)]
    return float(m.group(1)) * mult


def _param_filter(spec):
    m = re.fullmatch(r"(.+?)\s*(>=|<=|=|~)\s*(.+)", spec)
    if not m:
        raise SystemExit(f"--param {spec!r}: expected '<name><op><value>' with op >=, <=, = or ~")
    name, op, val = m.group(1).strip(), m.group(2), m.group(3).strip()

    def ok(params):
        have = params.get(name)
        if have is None:
            return False
        if op == "~":
            return val.lower() in str(have).lower()
        a, b = _num(have), _num(val)
        if op == "=" and (a is None or b is None):
            return str(have).strip().lower() == val.lower()
        if a is None or b is None:
            return False
        return {">=": a >= b, "<=": a <= b, "=": abs(a - b) <= 1e-9 * max(abs(b), 1)}[op]
    ok.spec = spec
    return ok


def _record(p):
    params = {q.get("ParameterText"): q.get("ValueText") for q in (p.get("Parameters") or [])}
    vs = p.get("ProductVariations") or []
    stock = max([v.get("QuantityAvailableforPackageType") or 0 for v in vs] or [0])
    k1000 = None
    for v in vs:
        for b in v.get("StandardPricing") or []:
            if b.get("BreakQuantity", 0) >= 1000:
                c = float(b["UnitPrice"])
                k1000 = c if k1000 is None else min(k1000, c)
    return {"mpn": p.get("ManufacturerProductNumber"),
            "mfr": (p.get("Manufacturer") or {}).get("Name"),
            "price1": p.get("UnitPrice"),
            "price1k_dk": k1000,
            "stock": stock,
            "status": (p.get("ProductStatus") or {}).get("Status"),
            "series": (p.get("Series") or {}).get("Name"),
            "category": _leaf(p.get("Category"))[1],
            "params": params}


def _query(H, body):
    return market._post(DK_SEARCH, body, headers=H, form=False)


def sweep(H, category, keywords="", min_stock=25, limit=DK_MAX_OFFSET, page=50,
          manufacturer=None, verbose=True):
    """Walk one category cheapest-first; returns normalised records."""
    out, offset = [], 0
    while offset < limit and offset <= DK_MAX_OFFSET:
        fo = {"SearchOptions": ["InStock"], "MinimumQuantityAvailable": min_stock}
        if category:
            fo["CategoryFilter"] = [{"Id": str(category)}]
        if manufacturer:
            fo["ManufacturerFilter"] = [{"Id": str(manufacturer)}]
        body = {"Keywords": keywords, "Limit": min(page, limit - offset), "Offset": offset,
                "FilterOptionsRequest": fo,
                "SortOptions": {"Field": "Price", "SortOrder": "Ascending"}}
        try:
            j = _query(H, body)
        except Exception as e:
            print(f"  ⚠ page at offset {offset} failed: {e}", file=sys.stderr)
            break
        prods = j.get("Products") or []
        if not prods:
            break
        out += [_record(p) for p in prods]
        offset += len(prods)
        if verbose:
            print(f"  ...{offset} scanned", file=sys.stderr)
        if offset >= (j.get("ProductsCount") or 0):
            break
        time.sleep(0.3)                      # be polite to the API
    return out


def manufacturers(H, category, keywords="", min_stock=25):
    """(id, name, count) for every manufacturer DigiKey lists in the category."""
    fo = {"SearchOptions": ["InStock"], "MinimumQuantityAvailable": min_stock,
          "CategoryFilter": [{"Id": str(category)}]}
    j = _query(H, {"Keywords": keywords, "Limit": 1, "Offset": 0, "FilterOptionsRequest": fo})
    ms = (j.get("FilterOptions") or {}).get("Manufacturers") or []
    return [(m.get("Id"), m.get("Value"), m.get("ProductCount")) for m in ms]


def _leaf(cat):
    """The deepest category on a product's Category chain: (id, path)."""
    path = []
    while cat:
        path.append(cat.get("Name") or "?")
        kids = cat.get("ChildCategories") or []
        if not kids:
            return cat.get("CategoryId"), " > ".join(path)
        cat = kids[0]
    return None, ""


def categories(H, keywords, sample=50):
    """Leaf categories of the first `sample` keyword hits -- to find an id.
    (DigiKey's response carries no category facets, so they are counted from
    the products themselves.)"""
    j = _query(H, {"Keywords": keywords, "Limit": sample, "Offset": 0})
    seen = {}
    for p in j.get("Products") or []:
        cid, path = _leaf(p.get("Category"))
        if cid is not None:
            n, _ = seen.get(cid, (0, path))
            seen[cid] = (n + 1, path)
    return sorted(((cid, path, n) for cid, (n, path) in seen.items()), key=lambda r: -r[2])


def viable(recs, filters, max_price=None):
    keep = []
    for r in recs:
        if r["status"] and r["status"].lower() in ("obsolete", "discontinued", "last time buy",
                                                   "not for new designs"):
            continue
        if max_price is not None and (r["price1"] or 1e9) > max_price:
            continue
        if all(f(r["params"]) for f in filters):
            keep.append(r)
    return keep


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--category", help="DigiKey category id (find one with --keywords alone)")
    ap.add_argument("--keywords", default="")
    ap.add_argument("--param", action="append", default=[], metavar="NAME OP VALUE")
    ap.add_argument("--max-price", type=float, help="qty-1 price ceiling, USD")
    ap.add_argument("--min-stock", type=int, default=25)
    ap.add_argument("--limit", type=int, default=DK_MAX_OFFSET)
    ap.add_argument("--by-manufacturer", action="store_true",
                    help="sweep each manufacturer separately (past the 300-offset ceiling)")
    ap.add_argument("--show", action="append", default=[], metavar="PARAM",
                    help="extra parametric columns to print")
    ap.add_argument("--raw", metavar="PATH", help="write the full records as JSON")
    a = ap.parse_args(argv)
    market.load_env()
    H = _headers()

    if not a.category:
        if not a.keywords:
            ap.error("give --category, or --keywords alone to find a category id")
        print(f"{'id':>8}  {'hits':>5}  category (of the first 50 keyword hits)")
        for cid, path, n in categories(H, a.keywords):
            print(f"{str(cid):>8}  {n:>5}  {path}")
        return 0

    filters = [_param_filter(s) for s in a.param]
    if a.by_manufacturer:
        recs, seen = [], set()
        for mid, name, n in manufacturers(H, a.category, a.keywords, a.min_stock):
            got = sweep(H, a.category, a.keywords, a.min_stock, manufacturer=mid, verbose=False)
            print(f"  {str(name)[:24]:<24} {len(got):>4} in stock", file=sys.stderr)
            for r in got:
                if r["mpn"] not in seen:
                    seen.add(r["mpn"])
                    recs.append(r)
    else:
        recs = sweep(H, a.category, a.keywords, a.min_stock, limit=a.limit)
        if len(recs) > DK_MAX_OFFSET - 1:
            print("  ⚠ hit the 300-offset ceiling: dearer parts are invisible -- "
                  "add --by-manufacturer", file=sys.stderr)

    v = viable(recs, filters, a.max_price)
    gates = [f.spec for f in filters] + ([f"qty1 <= ${a.max_price}"] if a.max_price else [])
    print(f"\nscanned {len(recs)} in-stock parts (>= {a.min_stock} pcs), cheapest first")
    print(f"{len(v)} pass the catalogue-answerable gates"
          + (f" ({'; '.join(gates)})" if gates else "") + "\n")
    cols = a.show or [f.spec.split(">")[0].split("<")[0].split("=")[0].split("~")[0].strip()
                      for f in filters]
    head = f"{'MPN':<28} {'mfr':<14} {'$1':>7} {'$1k':>7} {'stock':>8}"
    print(head + "".join(f"  {c[:18]:<18}" for c in cols))
    print("-" * (len(head) + 20 * len(cols)))
    for r in sorted(v, key=lambda x: x["price1"] or 1e9):
        k = f"{r['price1k_dk']:.2f}" if r["price1k_dk"] else "-"
        p1 = f"{r['price1']:.2f}" if r["price1"] is not None else "-"
        print(f"{(r['mpn'] or '')[:28]:<28} {(r['mfr'] or '')[:14]:<14} {p1:>7} {k:>7} "
              f"{r['stock']:>8}" + "".join(f"  {str(r['params'].get(c, ''))[:18]:<18}"
                                           for c in cols))
    if a.raw:
        with open(a.raw, "w") as f:
            json.dump(recs, f, indent=1)
        print(f"\nfull records -> {a.raw}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
