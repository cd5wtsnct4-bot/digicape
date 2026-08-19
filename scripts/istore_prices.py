#!/usr/bin/env python3
"""
Scrapling script: fetch iStore South Africa category pages (Mac, iPhone,
iPad, Watch, AirPods, Apple TV, Accessories) and extract product names +
prices with targeted CSS selectors.

WHY plain Fetcher (no headless browser needed here):
Unlike Takealot, iStore's category pages are server-rendered — a plain fetch
was able to read real product names and prices straight out of the initial
HTML, with no JavaScript execution required. That's consistent with a
Magento 2 storefront (the URL pattern — `/mac`, `/mac?p=2`, product URLs
prefixed `shop-...` — is a standard Magento signature), so this script uses
the fast `Fetcher` (plain HTTP) rather than `StealthyFetcher`. If a category
ever comes back empty, the script automatically retries that page with
`StealthyFetcher` as a fallback.

SETUP (run once, in a normal shell with internet access):
    python3 -m venv venv
    source venv/bin/activate        # Windows: venv\\Scripts\\activate
    pip install "scrapling[all]>=0.4.14"
    scrapling install --force        # only needed for the StealthyFetcher fallback

RUN:
    # Dump one category's rendered HTML to check/adjust selectors first:
    python3 istore_prices.py --dump-html mac --out mac_dump.html

    # Fetch page 1 of every category (fast, good enough for "From" pricing):
    python3 istore_prices.py

    # Fetch every page of every category (slow — thousands of products,
    # e.g. accessories alone has 2,250+ items across ~94 pages):
    python3 istore_prices.py --all-pages

    # Just one or two categories:
    python3 istore_prices.py --categories mac,iphone

OUTPUT:
    Prints a per-category summary table and writes <output-dir>/istore.json
    (and istore.csv) with every product found. --output-dir defaults to
    ../data relative to this script (i.e. <repo-root>/data), so it works
    the same whether run locally or from a GitHub Actions checkout.
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

from scrapling.fetchers import Fetcher, StealthyFetcher

BASE_URL = "https://www.istore.co.za"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

# category slug -> URL path.
# /mac, /iphone, /accessories, /airpods are confirmed real Magento product
# grids (they've been returning real multi-item results in production).
#
# /ipad and /watch are NOT product grids — confirmed 2026-08-19 that they
# only ever returned 0 products in production. Checked directly: they're
# marketing/discovery landing pages (a hero banner + a curated carousel of a
# handful of featured models), not the `li.product-item` grid PRODUCT_SELECTORS
# expects. The real grids, found via search and confirmed to list every
# current model with a real price, are the "shop-*-range" pages below.
# "appletv" was missing from this dict entirely (not a bug fix, an omission)
# — added using the same discovery method. All three new URLs are the same
# Magento template family as the working ones, so the existing selectors
# should carry over, but this hasn't been confirmed against real rendered
# HTML from this environment (no network access here to istore.co.za) —
# verify with --dump-html once run somewhere with real access.
CATEGORIES = {
    "mac": "/mac",
    "iphone": "/iphone",
    "ipad": "/ipad/shop-ipad-range",
    "watch": "/apple-watch/shop-watch-range",
    "airpods": "/airpods",
    "appletv": "/music-and-tech/discover-apple-tv/shop-apple-tv",
    "accessories": "/accessories",
}

# --- Targeted CSS selectors --------------------------------------------------
# Magento 2 default product-grid markup. iStore's theme may rename a few
# classes — if PRODUCT_SELECTORS matches 0 items, run with --dump-html,
# inspect a product card in the saved file, and add the real selector to
# the top of the relevant list below.
PRODUCT_SELECTORS = [
    "li.product-item",
    ".product-item",
    "div.product-item-info",
]

NAME_SELECTORS = [
    ".product-item-link::text",
    ".product-item-name a::text",
    ".product-item-name::text",
    "a.product-item-photo::attr(title)",
]

# Magento renders a "from" price for configurable products, a regular price,
# and (on sale) both an old and a special price. We grab whichever is present
# and keep the raw text so you can see which kind it was.
PRICE_SELECTORS = [
    ".price-box .special-price .price::text",
    ".price-box .price-final_price .price::text",
    ".price-box .price-wrapper .price::text",
    ".price-box .price::text",
    "[data-price-type='finalPrice'] .price::text",
    ".price::text",
]

OLD_PRICE_SELECTORS = [
    ".price-box .old-price .price::text",
    ".old-price .price::text",
]


def clean_price(text):
    """'From R14,599' / 'R 1 399.00' -> 14599.0 / 1399.0"""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.\s]", "", text).strip().replace(" ", "").replace(",", "")
    match = re.search(r"\d+(\.\d+)?", cleaned)
    return float(match.group()) if match else None


def first_match(card, selectors):
    for sel in selectors:
        val = card.css(sel).get()
        if val:
            return val.strip()
    return None


def find_cards(page):
    for sel in PRODUCT_SELECTORS:
        cards = page.css(sel)
        if cards:
            return cards, sel
    return [], None


def fetch_static(url):
    return Fetcher.get(url, impersonate="chrome", stealthy_headers=True)


def fetch_dynamic(url):
    return StealthyFetcher.fetch(url, headless=True, network_idle=True, block_ads=True)


def fetch_page_with_fallback(url):
    page = fetch_static(url)
    cards, _ = find_cards(page)
    if cards:
        return page
    print(f"  (plain fetch found no product cards for {url}, retrying with a browser...)")
    return fetch_dynamic(url)


def scrape_category(slug, path, all_pages, delay, max_pages_cap=200):
    results = []
    page_num = 1
    while True:
        url = f"{BASE_URL}{path}" if page_num == 1 else f"{BASE_URL}{path}?p={page_num}"
        print(f"[{slug}] fetching page {page_num}: {url}")
        try:
            page = fetch_page_with_fallback(url)
        except Exception as exc:
            print(f"[{slug}] ERROR fetching page {page_num}: {exc}", file=sys.stderr)
            break
        cards, used_selector = find_cards(page)

        if not cards:
            if page_num == 1:
                print(f"[{slug}] WARNING: no product cards found at all. "
                      f"Run --dump-html {slug} and update PRODUCT_SELECTORS.", file=sys.stderr)
            break

        if page_num == 1:
            print(f"[{slug}] matched {len(cards)} cards/page using selector: {used_selector}")

        for card in cards:
            name = first_match(card, NAME_SELECTORS)
            price_text = first_match(card, PRICE_SELECTORS)
            old_price_text = first_match(card, OLD_PRICE_SELECTORS)
            if not name and not price_text:
                continue
            results.append({
                "retailer": "istore",
                "category": slug,
                "name": name or "",
                "price_text": price_text or "",
                "price": clean_price(price_text),
                "old_price_text": old_price_text or "",
                "old_price": clean_price(old_price_text),
                "url": url,
            })

        if not all_pages or page_num >= max_pages_cap:
            break
        page_num += 1
        time.sleep(delay)  # be polite between page requests

    return results


def dump_html(slug, out_path):
    path = CATEGORIES[slug]
    url = f"{BASE_URL}{path}"
    page = fetch_static(url)
    html = page.html_content if hasattr(page, "html_content") else str(page)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved {url} -> {out_path}")
    print("Open it, inspect a product card, and update the selector lists at "
          "the top of this script if extraction finds nothing.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--categories", default=",".join(CATEGORIES),
                         help="Comma-separated subset of: " + ",".join(CATEGORIES))
    parser.add_argument("--all-pages", action="store_true",
                         help="Page through every result, not just page 1 (slow).")
    parser.add_argument("--delay", type=float, default=1.0,
                         help="Seconds to wait between page requests (default 1.0).")
    parser.add_argument("--dump-html", metavar="CATEGORY",
                         help="Save one category's raw HTML for selector inspection, then exit.")
    parser.add_argument("--out", default="dump.html", help="Output path for --dump-html.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                         help=f"Directory to write istore.json/.csv into (default {DEFAULT_OUTPUT_DIR}).")
    args = parser.parse_args()

    if args.dump_html:
        if args.dump_html not in CATEGORIES:
            sys.exit(f"Unknown category '{args.dump_html}'. Choices: {', '.join(CATEGORIES)}")
        dump_html(args.dump_html, args.out)
        return

    chosen = [c.strip() for c in args.categories.split(",") if c.strip()]
    unknown = [c for c in chosen if c not in CATEGORIES]
    if unknown:
        sys.exit(f"Unknown categories: {unknown}. Choices: {', '.join(CATEGORIES)}")

    all_results = []
    for slug in chosen:
        results = scrape_category(slug, CATEGORIES[slug], args.all_pages, args.delay)
        all_results.extend(results)
        print(f"[{slug}] {len(results)} products found\n")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "istore.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    with open(out_dir / "istore.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["retailer", "category", "name", "price_text", "price",
                                                "old_price_text", "old_price", "url"])
        writer.writeheader()
        writer.writerows(all_results)

    print(f"Saved {len(all_results)} total products to {out_dir}/istore.json / istore.csv\n")

    print(f"{'Category':<14}{'Name':<55}{'Price'}")
    print("-" * 90)
    for item in all_results:
        print(f"{item['category']:<14}{item['name'][:53]:<55}{item['price_text']}")


if __name__ == "__main__":
    main()
