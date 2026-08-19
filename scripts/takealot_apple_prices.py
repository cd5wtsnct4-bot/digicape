#!/usr/bin/env python3
"""
Scrapling script: fetch Takealot's Apple promotion page and extract product
prices with targeted CSS selectors.

WHY stealthy-fetch:
Takealot's /promotion/ pages are a JavaScript-rendered SPA — the server's
initial HTML response is basically an empty shell, and the product grid is
built client-side after the page loads. A plain HTTP GET (Scrapling's
`Fetcher`) will not see any products. This script uses `StealthyFetcher`,
which drives a real headless browser, waits for the page's network activity
to settle, and only then reads the DOM.

SETUP (run once, in a normal shell with internet access):
    python3 -m venv venv
    source venv/bin/activate        # Windows: venv\\Scripts\\activate
    pip install "scrapling[all]>=0.4.14"
    scrapling install --force        # downloads the browser Scrapling drives

RUN:
    # First, dump the rendered HTML so you can confirm/adjust the selectors
    # below against Takealot's actual current markup:
    python3 takealot_apple_prices.py --dump-html takealot_dump.html

    # Then run the real extraction:
    python3 takealot_apple_prices.py

OUTPUT:
    Prints a table to the console and writes <output-dir>/takealot.json.
    --output-dir defaults to ../data relative to this script
    (i.e. <repo-root>/data).
"""

import argparse
import json
import re
import sys
from pathlib import Path

from scrapling.fetchers import StealthyFetcher

URL = "https://www.takealot.com/promotion/apple"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

# --- Targeted CSS selectors --------------------------------------------------
# Takealot product cards are commonly rendered as anchors/divs carrying a
# `data-ref="product-card"` attribute, each containing a title node and a
# price node. Selectors are tried in order; the first one that returns
# results on the live page wins. If NONE of these match, run with
# --dump-html, open the file, right-click a price -> Inspect, and add the
# real selector as a new entry at the top of the relevant list.
CARD_SELECTORS = [
    "[data-ref='product-card']",
    "div.product-card",
    "li.product-card",
    "a.product-anchor",
    "article[data-ref='product-card']",
]

TITLE_SELECTORS = [
    "[data-ref='product-title']::text",
    ".product-title::text",
    "h3::text",
    "img::attr(alt)",
]

PRICE_SELECTORS = [
    "[data-ref='price']::text",
    ".currency::text",
    ".price::text",
    "[class*='price']::text",
]

WAIT_SELECTOR = "[data-ref='product-card']"  # tweak if the grid uses a different marker

# Real bug, found 2026-08-19: every row here was tagged "category": "apple-promo"
# unconditionally, while Digicape (the baseline every comparison is built
# against, see combine.py) tags its rows "mac"/"ipad"/"iphone"/"watch"/
# "airpods"/"appletv". combine.py groups by (category, normalized-name) —
# with every Takealot row in a category no Digicape row ever uses, NOT ONE
# Takealot product could ever appear in the comparison, regardless of name
# matching. This classifies each listing into the same six categories by
# keyword, so identically-named products actually get grouped together.
# Anything that doesn't match a known device family (accessories like a
# Lightning adapter, a generic promo bundle) is left as "apple-promo" and
# correctly stays out of the comparison — that part isn't a bug.
CATEGORY_KEYWORDS = [
    ("appletv", ("apple tv", "appletv", "siri remote")),
    ("airpods", ("airpods",)),
    ("iphone", ("iphone",)),
    ("ipad", ("ipad",)),
    ("watch", (" watch",)),
    ("mac", ("macbook", "imac", "mac mini", "mac studio", "mac pro", " mac ")),
]


def classify_category(name):
    lowered = f" {(name or '').lower()} "
    for category, keywords in CATEGORY_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return category
    return "apple-promo"


def extract_price(text):
    """Pull a numeric value out of strings like 'R 15,999' or 'R15 999.00'."""
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


def fetch_page():
    return StealthyFetcher.fetch(
        URL,
        headless=True,
        network_idle=True,       # wait for the XHR-driven product grid to settle
        wait=3000,               # extra buffer (ms) after network idle
        wait_selector=WAIT_SELECTOR,
        block_ads=True,
        solve_cloudflare=True,   # harmless no-op if no challenge is shown
    )


def dump_html(path):
    page = fetch_page()
    with open(path, "w", encoding="utf-8") as f:
        f.write(page.html_content if hasattr(page, "html_content") else str(page))
    print(f"Saved rendered HTML to {path}")
    print("Open it, inspect a product price, and check/update the selector lists "
          "at the top of this script if extraction below finds nothing.")


def extract():
    page = fetch_page()

    cards = []
    used_selector = None
    for sel in CARD_SELECTORS:
        found = page.css(sel)
        if found:
            cards = found
            used_selector = sel
            break

    if not cards:
        print("No product cards matched any selector. Re-run with --dump-html "
              "and update CARD_SELECTORS in this script.", file=sys.stderr)
        return []

    print(f"Matched {len(cards)} product cards using selector: {used_selector}\n")

    results = []
    for card in cards:
        title = first_match(card, TITLE_SELECTORS)
        price_text = first_match(card, PRICE_SELECTORS)
        price = extract_price(price_text)
        if title or price_text:
            results.append({
                "retailer": "takealot",
                "category": classify_category(title),
                "name": title or "",
                "price_text": price_text or "",
                "price": price,
            })
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump-html", metavar="FILE",
                         help="Save the rendered page HTML to FILE for selector inspection, then exit.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                         help=f"Directory to write takealot.json into (default {DEFAULT_OUTPUT_DIR}).")
    args = parser.parse_args()

    if args.dump_html:
        dump_html(args.dump_html)
        return

    try:
        results = extract()
    except Exception as exc:
        print(f"ERROR fetching Takealot: {exc}", file=sys.stderr)
        results = []

    print(f"{'Title':<60} {'Price'}")
    print("-" * 75)
    for item in results:
        print(f"{item['name'][:58]:<60} {item['price_text']}")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "takealot.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(results)} items to {out_path}")


if __name__ == "__main__":
    main()
