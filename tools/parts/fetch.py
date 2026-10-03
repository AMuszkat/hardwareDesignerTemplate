#!/usr/bin/env python
"""
CLI over market.py.

    python tools/parts/fetch.py <MPN> [<MPN> ...]      table, cache-first
    python tools/parts/fetch.py --refresh <MPN>         force a refetch
    python tools/parts/fetch.py --raw <MPN>             full JSON record
    python tools/parts/fetch.py --only nexar <MPN>      one source
    python tools/parts/fetch.py --datasheet <MPN>       download the PDF

Prints AUTHORIZED distribution only (see market.AUTHORIZED).  Broker stock is
fetched and cached but never shown here, because it is not what an assembler buys
and its prices are not real.
"""
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import datasheet                                           # noqa: E402
import market                                              # noqa: E402


def main(argv):
    if "--datasheet" in argv:
        # One chain, one place it is filed: see datasheet.py.
        argv.remove("--datasheet")
        return datasheet.main(argv)
    refresh = "--refresh" in argv
    raw = "--raw" in argv
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        only = [argv[i + 1]]
        del argv[i:i + 2]
    mpns = [a for a in argv if not a.startswith("--")]
    if not mpns:
        print(__doc__)
        return 1
    market.load_env()

    if raw:
        for m in mpns:
            print(json.dumps(market.fetch(m, only, refresh), indent=1))
        return 0

    print(f"{'MPN':<22} {'authorized stock':<28} {'best @1k':<26} age")
    print("-" * 92)
    for m in mpns:
        r = market.fetch(m, only, refresh)
        stock, p1k, _, _ = market.authorized_view(r)
        price, seller, brk = p1k
        have = {k: v for k, v in stock.items() if v > 0}
        ss = ", ".join(f"{k.split('/')[0][:8]}:{v}" for k, v in
                       sorted(have.items(), key=lambda x: -x[1])[:2]) or "-- none --"
        if price is None:
            ps = "no quote"
        else:
            ps = f"${price:.2f} ({seller.split('/')[0][:8]} @{brk}+)"
            if brk < 1000:
                ps += " ~"          # not a real volume quote
        age = "cached" if r.get("from_cache") else r.get("fetched", "")[:10]
        print(f"{m:<22} {ss:<28} {ps:<26} {age}")
        for name, err in market.failed_sources(r).items():
            print(f"{'':<22} ⚠ {name} FAILED: {str(err)[:60]}")
    print("\n~ = seller publishes no break at/above 1 000; the figure is a "
          "lower-quantity\n    price carried forward, NOT a volume quote.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
