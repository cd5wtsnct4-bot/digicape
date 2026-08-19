#!/usr/bin/env python3
"""
Scrapling script: fetch every real per-configuration price for each Mac
model Digicape sells, by reading the `var available = {...}` object embedded
in each product page's own HTML.

WHY THIS EXISTS: Digicape's category page (/category/mac-all) only ever
shows one generic "from" price per model line — confirmed directly, none of
Digicape's Mac listing names carry a storage size, RAM size, or core count
(see combine.py's has_config_markers() and README.md's "Known limitations").
That's fine for "is this model line cheaper here or there" but not for a
competitor's listing that names a specific SKU (e.g. "M5 Pro 15-core
CPU/16-core GPU, 24GB, 2TB SSD") — comparing that price against Digicape's
generic from-price isn't apples to apples.

The fix doesn't need a headless browser driving the on-page configurator
click by click, which was the originally-feared "materially bigger project"
(see README.md). A live diagnostic (scripts/digicape_config_diagnostic.py)
found something simpler: each Mac product page embeds a
`var available = {...}` JavaScript object, directly in the page's static
HTML, containing EVERY real sold configuration for that model line — chip
tier -> storage -> RAM -> colour -> {product_id, name, price, special}. No
clicking required: one plain fetch per model page gets the complete price
matrix.

WHAT THIS SCRIPT DOES:
    1. Fetches /category/mac-all (using the same proven selectors as
       digicape_prices.py) and, for every product card, records BOTH its
       name and its product detail URL (digicape_prices.py only keeps the
       category page's own URL — this script needs the individual product
       page instead, since that's where `available` lives).
    2. Fetches each product's detail page (plain fetch first — the blob is
       server-rendered, static HTML, confirmed live; falls back to a real
       browser only if the plain fetch doesn't find the marker, in case a
       theme change moves it behind client-side rendering later).
    3. Extracts the `available` object with a balanced-brace scan (it's
       valid JSON — PHP's json_encode output — so this is just finding
       where the object starts and ends in the surrounding <script> tag;
       regex alone can't reliably handle arbitrarily nested braces).
    4. Writes every model's name, product URL, and full `available` tree to
       data/digicape_mac_configs.json.

combine.py reads that file (if present) to upgrade a competitor's
specific-SKU Mac listing from a family-level "Different variant" comparison
into a precise, same-spec price comparison — see
scripts/digicape_mac_spec_match.py and combine.py's module docstring.

If this file is absent or a model isn't in it (e.g. this script hasn't been
run yet, or it failed for one model this run), combine.py just falls back to
its existing family-level matching — nothing downstream breaks or guesses.

SETUP (same as every other scraper here):
    python3 -m venv venv
    source venv/bin/activate
    pip install "scrapling[all]>=0.4.14"
    scrapling install --force

RUN:
    python3 scripts/digicape_mac_configs.py

    # Only the first N models (useful while checking this works at all):
    python3 scripts/digicape_mac_configs.py --limit 2

    # Save one product's raw HTML for inspection if extraction finds nothing:
    python3 scripts/digicape_mac_configs.py --dump-html "https://www.digicape.co.za/product/..." --out mac_product_dump.html
"""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from scrapling.fetchers import Fetcher, StealthyFetcher

BASE_URL = "https://www.digicape.co.za"
MAC_CATEGORY_PATH = "/category/mac-all"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

# Same proven selectors as digicape_prices.py (confirmed against the live
# site 2026-08-19) — kept in sync deliberately rather than imported, so this
# script has no import-time dependency on digicape_prices.py's own CLI/main
# side effects; the two scripts are independent entry points.
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
HREF_SELECTORS = [
    "a::attr(href)",
]

AVAILABLE_MARKER = "var available"


def first_match(card, selectors):
    for sel in selectors:
        val = card.css(sel).get()
        if val:
            return val.strip()
    return None


def find_cards(page):
    for sel in PRODUCT_SELECTORS:
        cards = page.css(sel)
        if cards and len(cards) < 500:
            return cards, sel
    return [], None


def fetch_static(url):
    return Fetcher.get(url, impersonate="chrome", stealthy_headers=True)


def fetch_dynamic(url):
    return StealthyFetcher.fetch(url, headless=True, network_idle=True, block_ads=True)


def html_of(page):
    return page.html_content if hasattr(page, "html_content") else str(page)


def list_mac_products(delay):
    """Returns a list of {"name": ..., "url": ...} for every card on the Mac
    category page, deduplicated by URL (a card sometimes wraps more than one
    matching anchor)."""
    url = f"{BASE_URL}{MAC_CATEGORY_PATH}"
    print(f"[mac-configs] fetching category listing: {url}")
    try:
        page = fetch_static(url)
    except Exception as exc:
        print(f"[mac-configs] ERROR fetching {url}: {exc}", file=sys.stderr)
        return []
    cards, used_selector = find_cards(page)
    if not cards:
        print("  (plain fetch found no product cards, retrying with a browser...)")
        try:
            page = fetch_dynamic(url)
        except Exception as exc:
            print(f"[mac-configs] ERROR fetching (dynamic) {url}: {exc}", file=sys.stderr)
            return []
        cards, used_selector = find_cards(page)

    if not cards:
        print("[mac-configs] no selector matched the category page — nothing to scrape. "
              "Run digicape_prices.py --dump-html mac to check if Digicape's markup changed.")
        return []

    print(f"[mac-configs] matched {len(cards)} cards using selector: {used_selector}")
    seen_urls = set()
    products = []
    for card in cards:
        name = first_match(card, NAME_SELECTORS)
        href = first_match(card, HREF_SELECTORS)
        if not name or not href:
            continue
        full_url = urljoin(BASE_URL, href)
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        products.append({"name": name, "url": full_url})
    return products


def extract_available_json(html):
    """Balanced-brace scan for `var available = { ... };`. Regex alone can't
    reliably match arbitrarily nested JSON braces, so this walks the string
    character by character, tracking string-literal state (so a brace inside
    a quoted product name/description doesn't get counted) and nesting
    depth, stopping at the brace that closes the one the marker opened."""
    idx = html.find(AVAILABLE_MARKER)
    if idx == -1:
        return None
    start = html.find("{", idx)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    i = start
    while i < len(html):
        ch = html[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = html[start:i + 1]
                    try:
                        return json.loads(blob)
                    except json.JSONDecodeError as exc:
                        print(f"  couldn't parse the extracted 'available' blob as JSON: {exc}",
                              file=sys.stderr)
                        return None
        i += 1
    return None  # ran off the end without the braces balancing — malformed/truncated


def scrape_model(name, url):
    print(f"[mac-configs] fetching product page: {name} ({url})")
    try:
        page = fetch_static(url)
        html = html_of(page)
    except Exception as exc:
        print(f"  ERROR fetching {url}: {exc}", file=sys.stderr)
        return None

    available = extract_available_json(html)
    if available is None:
        print("  'var available' not found (or didn't parse) in the plain fetch — "
              "retrying with a real browser in case it's rendered client-side...")
        try:
            page = fetch_dynamic(url)
            html = html_of(page)
        except Exception as exc:
            print(f"  ERROR fetching (dynamic) {url}: {exc}", file=sys.stderr)
            return None
        available = extract_available_json(html)

    if available is None:
        print(f"  --> no 'available' configuration data found for '{name}'. Either this "
              f"model doesn't use the configurator theme, or the page structure changed — "
              f"run --dump-html against this URL and inspect it by hand.")
        return None

    leaf_count = sum(
        1
        for by_storage in available.values()
        for by_ram in by_storage.values()
        for by_colour in by_ram.values()
        for _ in by_colour.values()
    )
    print(f"  --> found {leaf_count} configuration(s) for '{name}'")
    return {"name": name, "url": url, "available": available}


def dump_html(url, out_path):
    page = fetch_static(url)
    html = html_of(page)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved {url} -> {out_path}")
    print(f"Search it for {AVAILABLE_MARKER!r} by hand if extraction found nothing.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None,
                         help="Only scrape the first N models found on the category page (for testing).")
    parser.add_argument("--delay", type=float, default=1.5,
                         help="Seconds to wait between product-page fetches (default 1.5).")
    parser.add_argument("--dump-html", metavar="URL",
                         help="Save one product page's raw HTML for inspection, then exit.")
    parser.add_argument("--out", default="mac_product_dump.html", help="Output path for --dump-html.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                         help=f"Directory to write digicape_mac_configs.json into (default {DEFAULT_OUTPUT_DIR}).")
    args = parser.parse_args()

    if args.dump_html:
        dump_html(args.dump_html, args.out)
        return

    products = list_mac_products(args.delay)
    if not products:
        print("[mac-configs] no Mac products found — nothing to do.", file=sys.stderr)
        sys.exit(1)

    if args.limit:
        products = products[:args.limit]

    models = []
    failed = []
    for product in products:
        result = scrape_model(product["name"], product["url"])
        if result:
            models.append(result)
        else:
            failed.append(product["name"])
        time.sleep(args.delay)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "digicape_mac_configs.json"

    from datetime import datetime, timezone
    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "models": models,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    total_leaves = sum(
        1
        for m in models
        for by_storage in m["available"].values()
        for by_ram in by_storage.values()
        for by_colour in by_ram.values()
        for _ in by_colour.values()
    )
    print(f"\n[mac-configs] wrote {len(models)}/{len(products)} model(s), "
          f"{total_leaves} total configuration(s), to {out_path}")
    if failed:
        print(f"[mac-configs] no configuration data found for: {', '.join(failed)} "
              f"(family-level matching still works for these in combine.py, "
              f"just no precise-SKU upgrade this run)")


if __name__ == "__main__":
    main()
