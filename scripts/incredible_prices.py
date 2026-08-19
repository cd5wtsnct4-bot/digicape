#!/usr/bin/env python3
"""
Scrapling script: fetch Incredible Connection's Apple product pages and
extract prices with targeted CSS selectors.

COVERAGE NOTE: unlike Digicape and iStore (dedicated Apple resellers with one
category page per product line), Incredible Connection is a general
electronics retailer. Its Apple range is scattered across individual product
pages and category listings that mix brands, and several of its category
pages (iPad, Apple Watch, some AirPods) block plain HTTP fetches or require
JavaScript to render the grid. This script covers what's reliably reachable:
the MacBook laptop category page, the Apple TV streaming-devices category
(Apple-only items filtered by name), and a curated list of individual Apple
product pages for iPad/iPhone/Watch/AirPods models that don't have a clean
category listing. Extend PRODUCT_PAGES below as Incredible Connection adds or
renames SKUs — there's no reliable category page to crawl for those lines.

SETUP (run once, in a normal shell with internet access):
    python3 -m venv venv
    source venv/bin/activate        # Windows: venv\\Scripts\\activate
    pip install "scrapling[all]>=0.4.14"
    scrapling install --force        # downloads the browser Scrapling drives

RUN:
    python3 incredible_prices.py

OUTPUT:
    Prints a table to the console and writes <output-dir>/incredible.json.
    --output-dir defaults to ../data relative to this script
    (i.e. <repo-root>/data).
"""

import argparse
import json
import re
import sys
from pathlib import Path

from scrapling.fetchers import Fetcher, StealthyFetcher

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

MAC_CATEGORY_URL = "https://www.incredible.co.za/products/computers-printers-accessories/laptops/macbook"
APPLETV_CATEGORY_URL = "https://www.incredible.co.za/products/tv-audio/streaming-devices"

# Individual product pages — the reliable path for lines without a clean,
# fetchable Apple-only category page on this site. (name, category, url)
PRODUCT_PAGES = [
    ("ipad", "https://www.incredible.co.za/apple-ipad-11inch"),
    ("ipad", "https://www.incredible.co.za/apple-ipad-mini-8-3-inch-a17-pro-128gb-wifi-space-grey"),
    ("iphone", "https://www.incredible.co.za/apple-iphone-17e"),
    ("iphone", "https://www.incredible.co.za/apple-iphone-air"),
    ("watch", "https://www.incredible.co.za/apple-watch-se3"),
    ("watch", "https://www.incredible.co.za/apple-watch-se-gps-44mm-midnight-alu-case-with-ink-sport-loop"),
    ("airpods", "https://www.incredible.co.za/apple-airpods-4"),
    ("airpods", "https://www.incredible.co.za/apple-airpods-pro"),
    ("airpods", "https://www.incredible.co.za/apple-airpods-max-2-blue"),
    ("airpods", "https://www.incredible.co.za/apple-airpods-max-usb-c-purple"),
    ("airpods", "https://www.incredible.co.za/apple-airpods-max-usb-c-blue"),
]

CARD_SELECTORS = [
    "div.product-item",
    "li.product-item",
    "div.product-card",
    "article.product",
]
TITLE_SELECTORS = [
    ".product-item-link::text",
    ".product-name::text",
    "h2::text",
    "img::attr(alt)",
]
PRICE_SELECTORS = [
    ".price-box .price::text",
    ".special-price .price::text",
    ".price::text",
    "[class*='price']::text",
]

PRODUCT_TITLE_SELECTORS = [
    "h1::text",
    ".product-name h1::text",
    "[class*='product-title']::text",
]
PRODUCT_PRICE_SELECTORS = [
    ".special-price .price::text",
    ".price-box .price::text",
    ".price::text",
    "[class*='price']::text",
]


def extract_price(text):
    """Pull a numeric value out of strings like 'R 15,999' or 'R15 999.00'."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.\s]", "", text).strip().replace(" ", "").replace(",", "")
    match = re.search(r"\d+(\.\d+)?", cleaned)
    return float(match.group()) if match else None


def first_match(node, selectors):
    for sel in selectors:
        val = node.css(sel).get()
        if val:
            return val.strip()
    return None


def fetch(url):
    try:
        page = Fetcher.get(url, stealthy_headers=True)
        if page.status == 200:
            return page
    except Exception:
        pass
    # Fallback to a real headless browser if the plain request is blocked
    return StealthyFetcher.fetch(url, headless=True, network_idle=True, wait=1500, block_ads=True)


def scrape_category(url, category):
    page = fetch(url)
    cards = []
    used_selector = None
    for sel in CARD_SELECTORS:
        found = page.css(sel)
        if found:
            cards = found
            used_selector = sel
            break

    if not cards:
        print(f"[incredible] no cards matched on {url} — selectors may be stale", file=sys.stderr)
        return []

    print(f"[incredible] {url}: matched {len(cards)} cards using {used_selector}")

    results = []
    for card in cards:
        title = first_match(card, TITLE_SELECTORS)
        price_text = first_match(card, PRICE_SELECTORS)
        price = extract_price(price_text)
        if not title:
            continue
        if category == "appletv" and "apple" not in title.lower():
            continue  # this category page mixes brands — keep only Apple TV
        results.append({
            "retailer": "incredible",
            "category": category,
            "name": title,
            "price_text": price_text or "",
            "price": price,
            "url": url,
        })
    return results


def scrape_product_page(url, category):
    page = fetch(url)
    title = first_match(page, PRODUCT_TITLE_SELECTORS)
    price_text = first_match(page, PRODUCT_PRICE_SELECTORS)
    price = extract_price(price_text)
    if not title:
        print(f"[incredible] couldn't read title on {url} — selectors may be stale", file=sys.stderr)
        return None
    return {
        "retailer": "incredible",
        "category": category,
        "name": title,
        "price_text": price_text or "",
        "price": price,
        "url": url,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                         help=f"Directory to write incredible.json into (default {DEFAULT_OUTPUT_DIR}).")
    args = parser.parse_args()

    results = []

    try:
        results += scrape_category(MAC_CATEGORY_URL, "mac")
    except Exception as exc:
        print(f"ERROR scraping Mac category: {exc}", file=sys.stderr)

    try:
        results += scrape_category(APPLETV_CATEGORY_URL, "appletv")
    except Exception as exc:
        print(f"ERROR scraping Apple TV category: {exc}", file=sys.stderr)

    for category, url in PRODUCT_PAGES:
        try:
            row = scrape_product_page(url, category)
            if row:
                results.append(row)
        except Exception as exc:
            print(f"ERROR scraping {url}: {exc}", file=sys.stderr)

    print(f"\n{'Title':<55} {'Category':<10} {'Price'}")
    print("-" * 80)
    for item in results:
        print(f"{item['name'][:53]:<55} {item['category']:<10} {item['price_text']}")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "incredible.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(results)} items to {out_path}")


if __name__ == "__main__":
    main()
