"""
Distributor market data: stock, price breaks and lead time, fetched from
real APIs and cached with a timestamp.

WHY THIS EXISTS.  A market number read off a rendered web page and retyped
into a table is wrong more often than not: distributor search pages are
JavaScript shells that answer a script with "0 Found", several refuse a
fetcher outright, and a search SNIPPET can say "orders ship today" for a part
whose own page shows 0 in stock and a lead time of months.  CLAUDE.md forbids
quoting a snippet; this module is how that rule becomes cheap to obey.

WHAT IT DOES NOT DO.  It does not decide anything.  It returns what each
distributor says, with the time it said it, and leaves ranking to
the caller.  A source that fails is recorded as a failure, never as "no
stock" -- those are different facts and conflating them is how a part gets
wrongly eliminated.

Credentials live in ~/.config/hwdesign/api.env (mode 600, outside the repo;
HWDESIGN_API_ENV overrides the path).  `./setup.sh install` creates the file.
No key is ever logged, printed, or written into a cache file.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

ENV_PATH = os.path.expanduser(os.environ.get("HWDESIGN_API_ENV",
                                            "~/.config/hwdesign/api.env"))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CACHE = os.path.join(ROOT, "reference", "market")

# A cached record older than this is refetched.  Stock moves daily; prices
# do not.  Seven days is a compromise that keeps a decision run offline and
# repeatable while never letting a stock figure go quietly stale for a month.
MAX_AGE_S = 7 * 24 * 3600


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------
def load_env(path=ENV_PATH):
    """Read the key file into os.environ.  Absent keys are not an error --
    a source with no key is simply skipped, and says so."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip("'\"")               # KEY="value" is the same key
        if v:
            os.environ.setdefault(k.strip(), v)


def _post(url, data, headers=None, form=True):
    if form:
        body = urllib.parse.urlencode(data).encode()
        hdr = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        body = json.dumps(data).encode()
        hdr = {"Content-Type": "application/json"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdr, method="POST")
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())


# --------------------------------------------------------------------------
# token handling
#
# The two OAuth sources have VERY different token lifetimes -- Nexar 24 h,
# DigiKey 10 MINUTES -- so both are refreshed on demand and cached in memory
# with their own expiry.  Never cache a token to disk: it is a credential.
# --------------------------------------------------------------------------
_tok = {}


def _token(name, url, cid, csec, scope=None):
    now = time.time()
    t = _tok.get(name)
    if t and t[1] > now + 30:
        return t[0]
    data = {"grant_type": "client_credentials",
            "client_id": os.environ[cid], "client_secret": os.environ[csec]}
    if scope:
        data["scope"] = scope
    j = _post(url, data)
    tok, ttl = j["access_token"], int(j.get("expires_in", 600))
    _tok[name] = (tok, now + ttl)
    return tok


def nexar_token():
    """Client credentials if present, else the 24 h portal token."""
    if os.environ.get("NEXAR_CLIENT_ID") and os.environ.get("NEXAR_CLIENT_SECRET"):
        try:
            return _token("nexar", "https://identity.nexar.com/connect/token",
                          "NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET",
                          scope="supply.domain")
        except Exception:
            pass                      # fall back to the portal token
    return os.environ.get("NEXAR_TOKEN")


# --------------------------------------------------------------------------
# sources.  Each returns {"ok": True, "offers": [...]} or
# {"ok": False, "error": "..."} -- never a silent empty.
#
# An offer is {"seller", "stock", "moq", "breaks": [[qty, usd], ...],
#              "lead_weeks", "packaging"}.
# --------------------------------------------------------------------------
NEXAR_Q = """
query ($q: String!) {
  supSearchMpn(q: $q, limit: 1, country: "US", currency: "USD") {
    results {
      part {
        mpn manufacturer { name }
        bestDatasheet { url }
        sellers { company { name }
                  offers { inventoryLevel moq factoryLeadDays packaging
                           prices { quantity price currency } } }
      }
    }
  }
}"""


def nexar(mpn):
    tok = nexar_token()
    if not tok:
        return {"ok": False, "error": "no NEXAR credentials"}
    try:
        j = _post("https://api.nexar.com/graphql",
                  {"query": NEXAR_Q, "variables": {"q": mpn}},
                  headers={"Authorization": f"Bearer {tok}"}, form=False)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if "errors" in j:
        return {"ok": False, "error": str(j["errors"])[:300]}
    res = (j.get("data") or {}).get("supSearchMpn") or {}
    results = res.get("results") or []
    if not results:
        return {"ok": True, "offers": [], "note": "no match"}
    part = results[0]["part"]
    out = []
    for s in part.get("sellers") or []:
        name = (s.get("company") or {}).get("name") or "?"
        for o in s.get("offers") or []:
            breaks = [[p["quantity"], float(p["price"])]
                      for p in (o.get("prices") or [])
                      if (p.get("currency") or "USD") == "USD"]
            lead = o.get("factoryLeadDays")
            out.append({"seller": name,
                        "stock": o.get("inventoryLevel"),
                        "moq": o.get("moq"),
                        "breaks": sorted(breaks),
                        "lead_weeks": round(lead / 7, 1) if lead else None,
                        "packaging": o.get("packaging")})
    return {"ok": True, "offers": out,
            "mfr": (part.get("manufacturer") or {}).get("name"),
            "datasheet": (part.get("bestDatasheet") or {}).get("url")}


def digikey(mpn):
    if not os.environ.get("DIGIKEY_CLIENT_ID"):
        return {"ok": False, "error": "no DIGIKEY credentials"}
    try:
        tok = _token("digikey", "https://api.digikey.com/v1/oauth2/token",
                     "DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")
        url = ("https://api.digikey.com/products/v4/search/"
               + urllib.parse.quote(mpn, safe="") + "/productdetails")
        j = _get(url, {"Authorization": f"Bearer {tok}",
                       "X-DIGIKEY-Client-Id": os.environ["DIGIKEY_CLIENT_ID"],
                       "X-DIGIKEY-Locale-Site": "US",
                       "X-DIGIKEY-Locale-Currency": "USD"})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    p = j.get("Product") or {}
    if not p:
        return {"ok": True, "offers": [], "note": "no match"}
    # ⚠ v4 puts stock, MOQ and price breaks under ProductVariations -- ONE PER
    # PACKAGING OPTION (Tray / Cut Tape / Tape & Reel) -- and leaves
    # Product.QuantityAvailable and Product.StandardPricing null.  Reading the
    # Product level (the obvious thing, and what v3 did) silently reports every
    # part as zero stock and no price.
    # Parametrics are worth keeping: they feed the fit gates without a
    # second fetch, and they are the one place a package/pitch is stated
    # by the distributor rather than inferred.
    params = {q.get("ParameterText"): q.get("ValueText")
              for q in (p.get("Parameters") or [])}
    lead = _int(p.get("ManufacturerLeadWeeks"))
    offers = []
    for v in p.get("ProductVariations") or []:
        offers.append({
            "seller": "DigiKey",
            "stock": v.get("QuantityAvailableforPackageType"),
            "moq": v.get("MinimumOrderQuantity"),
            "breaks": sorted([[b["BreakQuantity"], float(b["UnitPrice"])]
                              for b in (v.get("StandardPricing") or [])]),
            "lead_weeks": lead,
            "packaging": (v.get("PackageType") or {}).get("Name"),
            "dk_pn": v.get("DigiKeyProductNumber")})
    # ⚠ AND ONE PRODUCT IS NOT ONE LISTING.  `/productdetails` resolves an
    # exact MPN to a SINGLE DigiKey product, and DigiKey routinely splits the
    # same manufacturer part across several: "W25Q16JWSNIQ" is the TUBE
    # listing at 0 stock while "W25Q16JWSNIQ TR" holds 3 521.  Asking for the
    # bare MPN therefore reports a well-stocked part as unbuyable, which is
    # enough to move a good part off a BOM.  So sweep
    # the keyword index too and merge every listing whose MPN differs from the
    # one asked for only by a packaging suffix.
    offers += _dk_sibling_offers(mpn, tok, lead)
    return {"ok": True,
            "offers": offers,
            "status": (p.get("ProductStatus") or {}).get("Status"),
            "mfr": (p.get("Manufacturer") or {}).get("Name"),
            "params": {"package": params.get("Supplier Device Package"),
                       "io": params.get("Number of I/O"),
                       "series": params.get("Series")},
            "datasheet": p.get("DatasheetUrl")}


# Packaging tokens DigiKey appends to make a separate listing of the same part.
_DK_PKG_SUFFIX = (" tr", " t&r", " ct", " cut tape", " reel", "-tr", "-t&r")


def _dk_norm(m):
    t = (m or "").strip().lower()
    for suf in _DK_PKG_SUFFIX:
        if t.endswith(suf):
            t = t[: -len(suf)].strip()
    return t


def _dk_sibling_offers(mpn, tok, lead):
    """Offers from OTHER DigiKey listings of the same manufacturer part."""
    try:
        j = _post("https://api.digikey.com/products/v4/search/keyword",
                  {"Keywords": mpn, "Limit": 10, "Offset": 0},
                  headers={"Authorization": f"Bearer {tok}",
                           "X-DIGIKEY-Client-Id":
                               os.environ["DIGIKEY_CLIENT_ID"],
                           "X-DIGIKEY-Locale-Site": "US",
                           "X-DIGIKEY-Locale-Currency": "USD",
                           "Content-Type": "application/json"},
                  form=False)
    except Exception:
        return []                       # metadata is already in hand; be quiet
    want = _dk_norm(mpn)
    out = []
    for prod in (j.get("Products") or []):
        listing = prod.get("ManufacturerProductNumber") or ""
        if _dk_norm(listing) != want or listing.strip().lower() == want:
            continue                    # not the same part, or the one we have
        for v in prod.get("ProductVariations") or []:
            out.append({
                "seller": "DigiKey",
                "stock": v.get("QuantityAvailableforPackageType"),
                "moq": v.get("MinimumOrderQuantity"),
                "breaks": sorted([[b["BreakQuantity"], float(b["UnitPrice"])]
                                  for b in (v.get("StandardPricing") or [])]),
                "lead_weeks": _int(prod.get("ManufacturerLeadWeeks")) or lead,
                "packaging": (v.get("PackageType") or {}).get("Name"),
                "listing": listing,
                "dk_pn": v.get("DigiKeyProductNumber")})
    return out


def _weeks(v):
    """Lead time to WEEKS.  The string carries its own unit and the sources
    disagree: Mouser says "310 Days", DigiKey gives a bare week count.  The
    first version of this stripped non-digits and returned 310 -- a six-year
    lead time -- for what is really 44 weeks."""
    if not v:
        return None
    txt = str(v).lower()
    d = "".join(c if c.isdigit() else " " for c in txt).split()
    if not d:
        return None
    n = int(d[0])
    if "day" in txt:
        return round(n / 7, 1)
    if "month" in txt:
        return round(n * 4.345, 1)
    return n


def mouser(mpn):
    key = os.environ.get("MOUSER_API_KEY")
    if not key:
        return {"ok": False, "error": "no MOUSER_API_KEY"}
    try:
        # The key goes in the QUERY STRING, not a header -- Mouser's own
        # design.  Keep it out of anything logged.
        j = _post("https://api.mouser.com/api/v1/search/partnumber?apiKey=" + key,
                  {"SearchByPartRequest": {"mouserPartNumber": mpn,
                                           "partSearchOptions": "Exact"}},
                  form=False)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    errs = j.get("Errors") or []
    if errs:
        return {"ok": False, "error": str(errs)[:300]}
    parts = ((j.get("SearchResults") or {}).get("Parts")) or []
    if not parts:
        return {"ok": True, "offers": [], "note": "no match"}
    p = parts[0]
    breaks = []
    for b in p.get("PriceBreaks") or []:
        try:
            breaks.append([int(b["Quantity"]),
                           float(str(b["Price"]).replace("$", "")
                                 .replace(",", "").strip())])
        except (KeyError, ValueError):
            continue
    return {"ok": True,
            "offers": [{"seller": "Mouser",
                        "stock": _int(p.get("AvailabilityInStock")),
                        "moq": _int(p.get("Min")),
                        "breaks": sorted(breaks),
                        "lead_weeks": _weeks(p.get("LeadTime")),
                        "packaging": None}],
            "mfr": p.get("Manufacturer"),
            "datasheet": p.get("DataSheetUrl") or None}


def _int(v):
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def element14(mpn, store="www.newark.com"):
    """Newark by default, NOT uk.farnell.com: same company, same catalogue
    API, but Farnell UK quotes GBP and Newark quotes USD, and every other
    source here is USD.  Mixing them silently compares 34.92 GBP against
    37.07 USD as if they were the same number."""
    key = os.environ.get("ELEMENT14_API_KEY")
    if not key:
        return {"ok": False, "error": "no ELEMENT14_API_KEY"}
    q = urllib.parse.urlencode({
        "term": "manuPartNum:" + mpn, "storeInfo.id": store,
        "resultsSettings.offset": 0, "resultsSettings.numberOfResults": 1,
        "resultsSettings.responseGroup": "large",
        "callInfo.omitXmlSchema": "false", "callInfo.responseDataFormat": "json",
        "versionNumber": "1.2", "callInfo.apiKey": key})
    try:
        j = _get("https://api.element14.com/catalog/products?" + q)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    r = j.get("manufacturerPartNumberSearchReturn") or j.get("premierFarnellPartNumberReturn") or {}
    prods = r.get("products") or []
    if not prods:
        return {"ok": True, "offers": [], "note": "no match"}
    p = prods[0]
    breaks = [[int(b["from"]), float(b["cost"])] for b in (p.get("prices") or [])]
    st = p.get("stock") or {}
    lead_d = st.get("leastLeadTime")          # ⚠ DAYS, not weeks
    return {"ok": True,
            "offers": [{"seller": "Newark/element14" if "newark" in store
                                  else "Farnell/element14",
                        "stock": st.get("level"),
                        "moq": p.get("translatedMinimumOrderQuality"),
                        "breaks": sorted(breaks),
                        "lead_weeks": round(lead_d / 7, 1) if lead_d else None,
                        "packaging": None}],
            "mfr": p.get("brandName"),
            "store": store,
            # `datasheets` needs responseGroup=large (already asked for) and is
            # the only source here that hands back the MANUFACTURER's own URL
            # rather than a distributor mirror, so it leads the download order
            # in datasheet.py.
            "datasheet": next((d.get("url") for d in (p.get("datasheets") or [])
                               if d.get("url")), None)}


SOURCES = {"nexar": nexar, "digikey": digikey,
           "mouser": mouser, "element14": element14}


# --------------------------------------------------------------------------
# cache + top level
# --------------------------------------------------------------------------
def _cache_path(mpn):
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in mpn)
    return os.path.join(CACHE, safe + ".json")


def fetch(mpn, only=None, refresh=False):
    """Return the merged record for one MPN, using the cache when fresh."""
    path = _cache_path(mpn)
    rec = {"mpn": mpn, "sources": {}}
    if os.path.exists(path) and not refresh:
        rec = json.load(open(path))
        age = time.time() - rec.get("fetched_epoch", 0)
        if age < MAX_AGE_S and not only:
            rec["from_cache"] = True
            return rec
    os.makedirs(CACHE, exist_ok=True)
    for name, fn in SOURCES.items():
        if only and name not in only:
            continue
        rec["sources"][name] = fn(mpn)
    rec["fetched_epoch"] = time.time()
    rec["fetched"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(rec["fetched_epoch"]))
    rec["from_cache"] = False
    json.dump(rec, open(path, "w"), indent=1, sort_keys=True)
    return rec


def price_at(rec, qty):
    """Lowest unit price any seller offers at >= `qty`, and who offers it.
    Returns (price, seller, break_qty) or (None, None, None).

    NOTE the break semantics: a break list [[1, 30.0], [100, 25.0]] means
    25.00 applies from 100 up.  So the price AT 1000 is the last break whose
    quantity is <= 1000, not the break nearest 1000."""
    best = (None, None, None)
    for s in rec.get("sources", {}).values():
        if not s.get("ok"):
            continue
        for o in s.get("offers", []):
            appl = [b for b in o.get("breaks") or [] if b[0] <= qty]
            if not appl:
                continue
            p = appl[-1]
            if best[0] is None or p[1] < best[0]:
                best = (p[1], o["seller"], p[0])
    return best


def total_stock(rec, sellers=None):
    """Stock per seller, de-duplicated: Nexar reports the same DigiKey
    inventory the DigiKey API does, so counting both double-counts."""
    out = {}
    for s in rec.get("sources", {}).values():
        if not s.get("ok"):
            continue
        for o in s.get("offers", []):
            n = o.get("stock")
            if n is None:
                continue
            name = o["seller"]
            if sellers and not any(k.lower() in name.lower() for k in sellers):
                continue
            out[name] = max(out.get(name, 0), int(n))
    return out


# --------------------------------------------------------------------------
# authorized vs broker
#
# ⚠ THIS FILTER IS NOT OPTIONAL.  Nexar aggregates independent/broker stock
# alongside franchised distribution, and the brokers quote impossible prices
# on exactly the parts we care about: LFE5U-45F-6BG256C came back at $2.34
# with 43 462 in stock from a seller nobody has heard of, against DigiKey's
# $37.07 with none.  A 16x undercut on a part the franchise chain cannot
# supply is a counterfeit/remark signal, not a bargain -- and an assembler buys
# from the franchised chain (their own page names Digi-Key, Mouser, Farnell
# element14, Arrow, Avnet), so broker stock does not even help us.
#
# Gate 5 in the sourcing plan counts ONLY these.
# --------------------------------------------------------------------------
AUTHORIZED = ("digikey", "mouser", "arrow", "newark", "farnell", "element14",
              "avnet", "rs compon", "tti", "future elec", "verical")


def is_authorized(seller):
    s = (seller or "").lower()
    return any(a in s for a in AUTHORIZED)


def failed_sources(rec):
    """Which sources errored, so a caller never reads 'no stock' off what is
    really 'we did not get an answer'.  These are different facts."""
    return {k: v.get("error") for k, v in rec.get("sources", {}).items()
            if not v.get("ok")}


def authorized_view(rec):
    """Stock and price restricted to franchised distribution.
    Returns (stock_by_seller, price_at_1k, price_at_100, price_at_1)."""
    stock, breaks = {}, []
    for s in rec.get("sources", {}).values():
        if not s.get("ok"):
            continue
        for o in s.get("offers", []):
            if not is_authorized(o.get("seller")):
                continue
            if o.get("stock") is not None:
                stock[o["seller"]] = max(stock.get(o["seller"], 0),
                                         int(o["stock"]))
            for b in o.get("breaks") or []:
                breaks.append((b[0], b[1], o["seller"]))

    def at(q):
        """Best price applicable at quantity q, WITH the break it came from.

        ⚠ The break quantity is returned because DigiKey caps its published
        tiers at 100 for these parts: asking for the price at 1 000 then
        returns the qty-1 figure, and printing that as "$12.75 @1k" invents a
        volume price that nobody quoted.  A caller MUST show the break."""
        appl = {}
        for qty, price, seller in breaks:
            if qty <= q:
                if seller not in appl or qty > appl[seller][0]:
                    appl[seller] = (qty, price)
        if not appl:
            return (None, None, None)
        seller = min(appl, key=lambda k: appl[k][1])
        return (appl[seller][1], seller, appl[seller][0])

    return stock, at(1000), at(100), at(1)


# --------------------------------------------------------------------------
# datasheet URLs
#
# Every source carries one and they disagree: element14 returns the
# MANUFACTURER's own URL, DigiKey a mirror on its CDN, Mouser often an empty
# string, Nexar whatever the aggregator resolved.  The order below is
# authority first, mirror second -- a mirror can be a superseded revision --
# and `datasheet.py` walks the whole list because any single host can be down,
# Cloudflare-gated, or a JavaScript viewer shell.
# --------------------------------------------------------------------------
DATASHEET_ORDER = ("element14", "digikey", "mouser", "nexar")


def datasheet_urls(rec):
    """[(source, url)] for one record, best authority first, deduped.

    Protocol-relative URLs are repaired here: DigiKey returns
    '//mm.digikey.com/...' and urllib cannot open that.
    """
    out, seen = [], set()
    srcs = rec.get("sources", {})
    for name in DATASHEET_ORDER:
        s = srcs.get(name) or {}
        u = s.get("datasheet") if s.get("ok") else None
        if not u:
            continue
        u = u.strip()
        if u.startswith("//"):
            u = "https:" + u
        if not u.lower().startswith(("http://", "https://")):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append((name, u))
    return out


# ⚠ NOT the datasheet order.  element14 is first for the datasheet URL because
# its URL is the manufacturer's own -- but its keyword search is fuzzy, and it
# has answered one manufacturer's MPN with another manufacturer's catalogue
# page, manufacturer field and all.  DigiKey's product search matches the MPN
# exactly, so for the NAME it goes first; walking DATASHEET_ORDER here files
# datasheets under the wrong manufacturer and the BOM says so too.
MFR_ORDER = ("digikey", "mouser", "element14", "nexar")


def mfr_of(rec):
    """Manufacturer name as the distributors report it, first source that has
    one, in MFR_ORDER (exact-match catalogues before fuzzy ones)."""
    srcs = rec.get("sources", {})
    for name in MFR_ORDER:
        s = srcs.get(name) or {}
        if s.get("ok") and s.get("mfr"):
            return s["mfr"]
    return None


def digikey_media_urls(mpn):
    """Extra datasheet URLs from DigiKey's /media endpoint.

    Worth a second call only when every URL in the record has failed: /media
    lists EVERY document DigiKey holds for the part, so it sometimes carries a
    mm.digikey.com mirror (which never bot-blocks) of a manufacturer PDF whose
    own host does, and sometimes a second datasheet for the family.  Returns []
    on any failure -- this is a fallback, never a reason to fail louder.
    """
    if not os.environ.get("DIGIKEY_CLIENT_ID"):
        return []
    try:
        tok = _token("digikey", "https://api.digikey.com/v1/oauth2/token",
                     "DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")
        j = _get("https://api.digikey.com/products/v4/search/"
                 + urllib.parse.quote(mpn, safe="") + "/media",
                 {"Authorization": f"Bearer {tok}",
                  "X-DIGIKEY-Client-Id": os.environ["DIGIKEY_CLIENT_ID"],
                  "X-DIGIKEY-Locale-Site": "US",
                  "X-DIGIKEY-Locale-Currency": "USD"})
    except Exception:
        return []
    out = []
    for m in j.get("MediaLinks") or []:
        if (m.get("MediaType") or "") != "Datasheets":
            continue
        u = (m.get("Url") or "").strip()
        if u.startswith("//"):
            u = "https:" + u
        if u.lower().startswith(("http://", "https://")):
            out.append(u)
    return out
