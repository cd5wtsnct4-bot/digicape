#!/usr/bin/env python3
"""
Scrapling script: fetch Digicape South Africa category pages (Mac, iPad,
iPhone, Watch, AirPods, Apple TV, Accessories) and extract product names +
prices with targeted CSS selectors.

WHAT'S CONFIRMED vs GUESSED (checked live via one-off fetches before this
script was written, without running Scrapling itself against the real
markup — treat the selectors below as a strong starting point, verify with
--dump-html before trusting the numbers):
  - CONFIRMED: /category/mac-all, /category/ipad-all, /category/iphone-all,
    /category/watch-all, /category/airpods-all, /category/apple-tv are real,
    server-rendered listing pages (products are in the initial HTML, no JS
    execution needed).
  - CONFIRMED: each category shows all products on one page with NO
    pagination — small, curated catalog per category.
  - CONFIRMED: /category/accessories is a HUB page, not a listing — it
    links out to subcategories like "Mac Accessories", "iPad Accessories",
    "iPhone Accessories" via "VIEW" links, with no products of its own.
  - GUESSED: the exact CSS classes for product cards/names/prices.

SETUP (run once, in a normal shell with internet access):
    python3 -m venv venv
    source venv/bin/activate        # Windows: venv\\Scripts\\activate
    pip install "scrapling[all]>=0.4.14"
    scrapling install --force        # only needed if the JS-fallback path triggers

RUN:
    # Dump one category's rendered HTML to verify/adjust selectors first:
    python3 digicape_prices.py --dump-html mac --out mac_dump.html

    # Fetch every named category, plus auto-discovered accessories subpages:
    python3 digicape_prices.py

    # Just a couple of categories:
    python3 digicape_prices.py --categories mac,iphone

    # Skip the accessories hub (it can add many subpages) — used by the
    # scheduled GitHub Actions run to keep runtime short:
    python3 digicape_prices.py --no-accessories

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

DIRECT_CATEGORIES = {
    "mac": "/category/mac-all",
    "ipad": "/category/ipad-all",
    "iphone": "/category/iphone-all",
    "watch": "/category/watch-all",
    "airpods": "/category/airpods-all",
    "appletv": "/category/apple-tv",
}
ACCESSORIES_HUB = "/category/accessories"

# --- Targeted CSS selectors --------------------------------------------------
# Best-guess selectors for a modern storefront. Tried in order; first match
# wins. If NONE match on a page, the script falls back to a regex heuristic
# that scans for "R 1,234" style price text (see extract_heuristic below) so
# you still get data while you fix the selectors.
PRODUCT_SELECTORS = [
    "[class*='product-card']",
    "[class*='product-item']",
    "[class*='ProductCard']",
    "li.product",
    "div.product",
    "article",
]

NAME_SELECTORS = [
    "[class*='product-title']::text",
    "[class*='product-name']::text",
    "h3::text",
    "h2::text",
    "a::attr(title)",
    "img::attr(alt)",
]

PRICE_SELECTORS = [
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
            # the name comes through fine) but Digicape's price markup uses
            # a class name outside PRICE_SELECTORS's guesses. Rather than
            # silently returning a name with no price (which is what was
            # happening — 45/45 rows fetched, 0 with a usable price), fall
            # back to scanning this card's own text nodes for a price-shaped
            # string. This is scoped to the single card, so it can't pick up
            # an unrelated price elsewhere on the page.
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


def discover_accessory_subcategories(max_links=25):
    """Digicape's /category/accessories is a hub, not a listing — find the
    subcategory links it points to (Mac Accessories, iPhone Accessories,
    etc.) so we can scrape each one for actual products."""
    url = f"{BASE_URL}{ACCESSORIES_HUB}"
    print(f"[accessories] fetching hub page {url}")
    page = fetch_static(url)

    hrefs = page.css("a::attr(href)").getall()
    subcat_paths = []
    seen = set()
    for href in hrefs:
        if not href:
            continue
        if href.startswith("http") and not href.startswith(BASE_URL):
            continue
        path = href if href.startswith("/") else f"/{href}"
        if not path.startswith("/category/"):
            continue
        if path in (ACCESSORIES_HUB, "/category/accessories/"):
            continue
        if path in seen:
            continue
        seen.add(path)
        subcat_paths.append(path)
        if len(subcat_paths) >= max_links:
            break

    if not subcat_paths:
        print("[accessories] no subcategory links discovered — hub page markup may "
              "differ from expected. Run --dump-html to inspect and adjust "
              "discover_accessory_subcategories().", file=sys.stderr)
    else:
        print(f"[accessories] discovered {len(subcat_paths)} subcategories: {subcat_paths}")
    return subcat_paths


def dump_html(category, out_path):
    if category in DIRECT_CATEGORIES:
        path = DIRECT_CATEGORIES[category]
    elif category == "accessories":
        path = ACCESSORIES_HUB
    else:
        sys.exit(f"Unknown category '{category}'")
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
    all_choices = list(DIRECT_CATEGORIES) + ["accessories"]
    parser.add_argument("--categories", default=",".join(all_choices),
                         help="Comma-separated subset of: " + ",".join(all_choices))
    parser.add_argument("--no-accessories", action="store_true",
                         help="Skip the accessories hub (it can add several subpages).")
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
    if args.no_accessories and "accessories" in chosen:
        chosen.remove("accessories")

    all_results = []

    for slug in chosen:
        if slug == "accessories":
            continue
        results = scrape_listing(slug, DIRECT_CATEGORIES[slug])
        all_results.extend(results)
        print(f"[{slug}] {len(results)} products found\n")
        time.sleep(args.delay)

    if "accessories" in chosen:
        subcats = discover_accessory_subcategories()
        for path in subcats:
            slug = "accessories:" + path.rsplit("/", 1)[-1]
            results = scrape_listing(slug, path)
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
