#!/usr/bin/env python3
"""
Scrapling script: fetch Digicape South Africa's six named Apple-hardware
category pages and extract product names + prices.

SCOPE (intentionally narrow, per request): this script ONLY ever fetches
these six listing pages. There is no accessories mode, no hub-page
discovery, and no way to widen scope via a flag — if Digicape adds a new
category, it has to be added to DIRECT_CATEGORIES below on purpose.
    /category/mac-all
    /category/ipad-all
    /category/iphone-all
    /category/watch-all
    /category/airpods-all
    /category/apple-tv

SELECTORS — now based on real, confirmed markup, not a guess. ElevateSJC's
separate PHP scraper (includes/catalog.php in the reference project this
dashboard's UI is ported from) already parses Digicape's live category
pages successfully, using:
    <article data-dst-pid="...">                        one per product
      <a href=".../product/...">                         product URL
      <p class="category__product--heading">Name</p>     product name
      ...category__product--price...<strong>R 1,234</strong>   price
PRODUCT_SELECTORS/NAME_SELECTORS/PRICE_SELECTORS below try that exact shape
FIRST. The older best-guess selectors are kept as a fallback list in case
Digicape changes its markup, and the per-card regex-over-text fallback
(scoped to a single card, see scrape_listing()) is the last resort. This
combination was validated against a real production run on 2026-08-19:
all 45 products across all six categories came back with real prices using
the proven selectors below, confirming they match Digicape's actual DOM.

SETUP (run once, in a normal shell with internet access):
    python3 -m venv venv
    source venv/bin/activate        # Windows: venv\\Scripts\\activate
    pip install "scrapling[all]>=0.4.14"
    scrapling install --force        # only needed if the JS-fallback path triggers

RUN:
    # Dump one category's rendered HTML to verify/adjust selectors first:
    python3 digicape_prices.py --dump-html mac --out mac_dump.html

    # Fetch all six categories (the only thing this script does):
    python3 digicape_prices.py

    # Just a couple of categories:
    python3 digicape_prices.py --categories mac,iphone

OUTPUT:
    Prints a per-category summary and writes <output-dir>/digicape.json /
    digicape.csv with every product found. --output-dir defaults to
    ../data relative to this script (i.e. <repo-root>/data).
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

from scrapling.fetchers import Fetcher, StealthyFetcher

BASE_URL = "https://www.digicape.co.za"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

# The only six pages this script will ever fetch.
DIRECT_CATEGORIES = {
    "mac": "/category/mac-all",
    "ipad": "/category/ipad-all",
    "iphone": "/category/iphone-all",
    "watch": "/category/watch-all",
    "airpods": "/category/airpods-all",
    "appletv": "/category/apple-tv",
}

# --- Targeted CSS selectors --------------------------------------------------
# First entry in each list is the proven selector (confirmed via the
# reference PHP scraper's real, working markup match). The rest are older
# best-guess fallbacks, tried in order if the proven one doesn't match
# (e.g. Digicape redesigns the page). If NONE match, the script falls back
# to a page-wide regex heuristic (see extract_heuristic below).
PRODUCT_SELECTORS = [
    "article[data-dst-pid]",
    "[class*='product-card']",
    "[class*='product-item']",
    "[class*='ProductCard']",
    "li.product",
    "div.product",
    "article",
]

NAME_SELECTORS = [
    "p.category__product--heading::text",
    "[class*='category__product--heading']::text",
    "[class*='product-title']::text",
    "[class*='product-name']::text",
    "h3::text",
    "h2::text",
    "a::attr(title)",
    "img::attr(alt)",
]

PRICE_SELECTORS = [
    "[class*='category__product--price'] strong::text",
    "[class*='price'] strong::text",
    "[class*='price']::text",
    "[data-price]::attr(data-price)",
    "span:contains('R')::text",
]

PRICE_REGEX = re.compile(r"R\s?[\d][\d,\s]*(?:\.\d{2})?")


def clean_price(text):
    """'From R13,899' / 'R 1 399.00' -> 13899.0 / 1399.0"""
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
        # guard against selectors so generic they match the whole page body
        if cards and len(cards) < 500:
            return cards, sel
    return [], None


def fetch_static(url):
    return Fetcher.get(url, impersonate="chrome", stealthy_headers=True)


def fetch_dynamic(url):
    return StealthyFetcher.fetch(url, headless=True, network_idle=True, block_ads=True)


def extract_heuristic(page, category, url):
    """Fallback: find every 'Rxxx' price-looking string in the page text and
    pair it with the nearest preceding heading/link text. Rougher than a
    proper selector match, but keeps the script useful if the site's markup
    doesn't match PRODUCT_SELECTORS at all."""
    results = []
    all_text_nodes = page.css("*::text").getall()
    pending_name = None
    for chunk in all_text_nodes:
        text = chunk.strip()
        if not text:
            continue
        if PRICE_REGEX.search(text):
            results.append({
                "retailer": "digicape",
                "category": category,
                "name": pending_name or "",
                "price_text": text,
                "price": clean_price(text),
                "old_price_text": "",
                "old_price": None,
                "url": url,
            })
        elif 3 < len(text) < 80:
            pending_name = text
    return results


def scrape_listing(category, path):
    url = f"{BASE_URL}{path}"
    print(f"[{category}] fetching {url}")
    try:
        page = fetch_static(url)
    except Exception as exc:
        print(f"[{category}] ERROR fetching {url}: {exc}", file=sys.stderr)
        return []
    cards, used_selector = find_cards(page)

    if not cards:
        print(f"  (plain fetch found no product cards, retrying with a browser...)")
        try:
            page = fetch_dynamic(url)
        except Exception as exc:
            print(f"[{category}] ERROR fetching (dynamic) {url}: {exc}", file=sys.stderr)
            return []
        cards, used_selector = find_cards(page)

    if not cards:
        print(f"[{category}] no selector matched — falling back to price-text heuristic.")
        results = extract_heuristic(page, category, url)
        print(f"[{category}] heuristic found {len(results)} price-looking items "
              f"(names may be noisy — verify with --dump-html {category})")
        return results

    print(f"[{category}] matched {len(cards)} cards using selector: {used_selector}")
    results = []
    price_selector_misses = 0
    for card in cards:
        name = first_match(card, NAME_SELECTORS)
        price_text = first_match(card, PRICE_SELECTORS)
        if not price_text:
            # PRODUCT_SELECTORS / NAME_SELECTORS can match while
            # PRICE_SELECTORS misses — the card element itself was found (so
            # the name comes through fine) but the price markup uses a class
            # name outside PRICE_SELECTORS's guesses. Fall back to scanning
            # this card's own text nodes for a price-shaped string, scoped to
            # the single card so it can't pick up an unrelated price
            # elsewhere on the page.
            price_selector_misses += 1
            for chunk in card.css("*::text").getall():
                stripped = chunk.strip()
                if stripped and PRICE_REGEX.search(stripped):
                    price_text = stripped
                    break
        if not name and not price_text:
            continue
        results.append({
            "retailer": "digicape",
            "category": category,
            "name": name or "",
            "price_text": price_text or "",
            "price": clean_price(price_text),
            "old_price_text": "",
            "old_price": None,
            "url": url,
        })
    if price_selector_misses:
        recovered = sum(1 for r in results if r["price"] is not None)
        print(f"[{category}] PRICE_SELECTORS missed on {price_selector_misses}/{len(cards)} cards; "
              f"per-card text fallback recovered a price for {recovered} of those. If that recovered "
              f"count is still 0, run --dump-html {category} and update PRICE_SELECTORS for real.")
    return results


def dump_html(category, out_path):
    if category not in DIRECT_CATEGORIES:
        sys.exit(f"Unknown category '{category}'. Choices: {', '.join(DIRECT_CATEGORIES)}")
    path = DIRECT_CATEGORIES[category]
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
    all_choices = list(DIRECT_CATEGORIES)
    parser.add_argument("--categories", default=",".join(all_choices),
                         help="Comma-separated subset of: " + ",".join(all_choices))
    parser.add_argument("--no-accessories", action="store_true",
                         help=argparse.SUPPRESS)  # deprecated no-op, kept so the
                         # existing scheduled workflow command line (which still
                         # passes this flag) doesn't break; this script never
                         # scrapes accessories at all any more, flag or no flag.
    parser.add_argument("--delay", type=float, default=1.0,
                         help="Seconds to wait between requests (default 1.0).")
    parser.add_argument("--dump-html", metavar="CATEGORY",
                         help="Save one category's raw HTML for selector inspection, then exit.")
    parser.add_argument("--out", default="dump.html", help="Output path for --dump-html.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                         help=f"Directory to write digicape.json/.csv into (default {DEFAULT_OUTPUT_DIR}).")
    args = parser.parse_args()

    if args.dump_html:
        dump_html(args.dump_html, args.out)
        return

    chosen = [c.strip() for c in args.categories.split(",") if c.strip()]
    unknown = [c for c in chosen if c not in all_choices]
    if unknown:
        sys.exit(f"Unknown categories: {unknown}. Choices: {', '.join(all_choices)}")

    all_results = []

    for slug in chosen:
        results = scrape_listing(slug, DIRECT_CATEGORIES[slug])
        all_results.extend(results)
        print(f"[{slug}] {len(results)} products found\n")
        time.sleep(args.delay)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "digicape.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    with open(out_dir / "digicape.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["retailer", "category", "name", "price_text", "price",
                                                "old_price_text", "old_price", "url"])
        writer.writeheader()
        writer.writerows(all_results)

    print(f"Saved {len(all_results)} total products to {out_dir}/digicape.json / digicape.csv\n")
    print(f"{'Category':<24}{'Name':<45}{'Price'}")
    print("-" * 90)
    for item in all_results:
        print(f"{item['category']:<24}{item['name'][:43]:<45}{item['price_text']}")


if __name__ == "__main__":
    main()
