#!/usr/bin/env python3
"""
Scrapling script: fetch a curated list of Amazon.co.za Apple product pages
and extract prices.

HONEST STATUS, READ BEFORE TRUSTING THIS SCRIPT'S OUTPUT:
Amazon.co.za's robots.txt disallows automated fetching of both its search
pages and its individual product pages — every fetch attempt made while
writing this script was blocked before a single byte of real markup was
seen. The CSS selectors below (PRICE_SELECTORS, TITLE_SELECTORS) are the
selectors Amazon has used for years across its storefronts
(`#corePrice_feature_div`, `span.a-price .a-offscreen`, `#productTitle`) —
well-documented, not guessed from nothing — but they have not been verified
against amazon.co.za's actual current HTML, because nothing in this
environment could load it to check. On top of that, Amazon is known for
aggressive bot detection (CAPTCHA / "Sorry, we just need to make sure
you're not a robot" interstitials) that can block even a real headless
browser, including on GitHub Actions' shared IP ranges specifically —
they're commonly flagged. Run this once by hand and read the console output
before trusting it in the scheduled workflow; if it comes back with mostly
CAPTCHA-page titles or zero prices, that's Amazon's bot defense doing its
job, not a bug to chase with more selector guesses.

PRODUCT_URLS below is a curated list (found via web search, not crawled —
Amazon's search-results pages are even more heavily protected than product
pages), and will go stale as Amazon rotates listings. Update it periodically.

SETUP (run once, in a normal shell with internet access):
    python3 -m venv venv
    source venv/bin/activate        # Windows: venv\\Scripts\\activate
    pip install "scrapling[all]>=0.4.14"
    scrapling install --force

RUN:
    python3 amazon_prices.py

OUTPUT:
    Prints a table to the console and writes <output-dir>/amazon.json.
    --output-dir defaults to ../data relative to this script.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from scrapling.fetchers import StealthyFetcher

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

# (category, url) — current-generation Apple listings found via web search.
# Amazon.co.za's own search/category pages are JS-heavy and more aggressively
# gated than individual product pages, so this curated list is the reliable
# path rather than a crawl.
PRODUCT_URLS = [
    ("mac", "https://www.amazon.co.za/Apple-MacBook-13-inch-Laptop-chip/dp/B0DZDBM3DV"),
    ("mac", "https://www.amazon.co.za/Apple-MacBook-13-inch-Laptop-10-core/dp/B0GR1P2GC5"),
    ("mac", "https://www.amazon.co.za/Apple-MacBook-15-inch-Laptop-chip/dp/B0DZDD2DKG"),
    ("mac", "https://www.amazon.co.za/Apple-MacBook-Laptop-chip-core/dp/B0FWD51XLG"),
    ("mac", "https://www.amazon.co.za/Apple-MacBook-Laptop-chip-core/dp/B0DLHFMRTT"),
    ("ipad", "https://www.amazon.co.za/Apple-iPad-A16-chip-11-inch/dp/B0DZ78275R"),
    ("ipad", "https://www.amazon.co.za/Apple-iPad-Air-13-inch-Landscape/dp/B0D3JBG3FP"),
    ("ipad", "https://www.amazon.co.za/Apple-iPad-Air-11-inch-M4/dp/B0GQWC3GV9"),
    ("ipad", "https://www.amazon.co.za/Apple-iPad-mini-A17-Pro/dp/B0DK43XKCQ"),
    ("iphone", "https://www.amazon.co.za/Apple-iPhone-256GB-ProMotion-Resistance/dp/B0FQG2GDD9"),
    ("iphone", "https://www.amazon.co.za/Apple-iPhone-Pro-256GB-Breakthrough/dp/B0FQFZM6QC"),
    ("iphone", "https://www.amazon.co.za/Apple-iPhone-Pro-Max-256GB/dp/B0FQFH63DX"),
    ("iphone", "https://www.amazon.co.za/Apple-iPhone-Plus-128-Intelligence/dp/B0DGHPQY7L"),
    ("watch", "https://amazon.co.za/Apple-Watch-Smartwatch-Aluminium-Always/dp/B0FQG1VWHW"),
    ("watch", "https://www.amazon.co.za/Apple-Cellular-Smartwatch-Starlight-Aluminium/dp/B0FQFT6XYL"),
    ("airpods", "https://www.amazon.co.za/Apple-Cancellation-Headphones-Transparency-Personalised/dp/B0DGJ7M995"),
    ("airpods", "https://www.amazon.co.za/Apple-Cancellation-Bluetooth-Headphones-High%E2%80%91Fidelity/dp/B0FQFYMLS5"),
    ("airpods", "https://www.amazon.co.za/Apple-AirPods-Max-Space-Gray/dp/B08Q4LRXCG"),
    ("appletv", "https://www.amazon.co.za/Apple-Wi%E2%80%91Fi-Ethernet-storage-generation/dp/B0CZ9G2LB8"),
    ("appletv", "https://www.amazon.co.za/Apple-2022-WiFi-storage-generation/dp/B0CZ9H8QHD"),
]

TITLE_SELECTORS = [
    "#productTitle::text",
    "h1#title span::text",
    "h1::text",
]

PRICE_SELECTORS = [
    "#corePrice_feature_div .a-price .a-offscreen::text",
    "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen::text",
    ".a-price .a-offscreen::text",
    "#priceblock_ourprice::text",
    "#priceblock_dealprice::text",
]

PRICE_REGEX = re.compile(r"R\s?[\d][\d,\s]*(?:\.\d{2})?")
CAPTCHA_MARKERS = ("robot check", "enter the characters you see", "api-services-support@amazon.com")


def clean_price(text):
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.\s]", "", text).strip().replace(" ", "").replace(",", "")
    match = re.search(r"\d+(\.\d+)?", cleaned)
    return float(match.group()) if match else None


def first_match(page, selectors):
    for sel in selectors:
        val = page.css(sel).get()
        if val:
            return val.strip()
    return None


def looks_like_captcha(page):
    text = " ".join(page.css("*::text").getall()).lower()
    return any(marker in text for marker in CAPTCHA_MARKERS)


def scrape_product(category, url):
    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True, wait=1500,
                                      block_ads=True, solve_cloudflare=True)
    except Exception as exc:
        print(f"ERROR fetching {url}: {exc}", file=sys.stderr)
        return None

    if looks_like_captcha(page):
        print(f"[amazon] {url} -> looks like a bot-check page, not the product. Skipping.", file=sys.stderr)
        return None

    title = first_match(page, TITLE_SELECTORS)
    price_text = first_match(page, PRICE_SELECTORS)

    if not price_text:
        # Same defensive fallback used after Digicape's selectors turned out
        # to be wrong in production: scan the page text for a price-shaped
        # string near the top of the document rather than returning nothing.
        for chunk in page.css("*::text").getall()[:400]:
            stripped = chunk.strip()
            if stripped and PRICE_REGEX.search(stripped):
                price_text = stripped
                break

    if not title:
        print(f"[amazon] {url} -> couldn't read a title; selectors may need updating "
              f"(or this was served a bot-check page).", file=sys.stderr)
        return None

    return {
        "retailer": "amazon",
        "category": category,
        "name": title,
        "price_text": price_text or "",
        "price": clean_price(price_text),
        "url": url,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--delay", type=float, default=2.0,
                         help="Seconds to wait between requests (default 2.0 — Amazon rate-limits aggressively).")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                         help=f"Directory to write amazon.json into (default {DEFAULT_OUTPUT_DIR}).")
    args = parser.parse_args()

    results = []
    for category, url in PRODUCT_URLS:
        row = scrape_product(category, url)
        if row:
            results.append(row)
        time.sleep(args.delay)

    priced = sum(1 for r in results if r["price"] is not None)
    print(f"\n{'Title':<55} {'Category':<10} {'Price'}")
    print("-" * 80)
    for item in results:
        print(f"{item['name'][:53]:<55} {item['category']:<10} {item['price_text']}")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "amazon.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(results)}/{len(PRODUCT_URLS)} products to {out_path} "
          f"({priced} with a usable price).")
    if len(results) < len(PRODUCT_URLS) // 2:
        print("Fewer than half the pages returned usable data — likely Amazon's bot "
              "detection, not a selector problem. Re-running won't fix that; see the "
              "module docstring.", file=sys.stderr)


if __name__ == "__main__":
    main()
