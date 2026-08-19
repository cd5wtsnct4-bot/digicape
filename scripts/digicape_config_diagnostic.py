#!/usr/bin/env python3
"""
DIAGNOSTIC, not a scraper. Answers the questions that have to be answered
with real network access before a per-configuration Mac price scraper can
be designed at all — this environment has none (confirmed: direct curl and
a headless Chromium both hit a hard network block reaching digicape.co.za;
WebFetch can reach the page but only returns an AI-summarized markdown
version, not raw HTML, so it can't be trusted to confirm or rule out
structure it might have missed).

WHY THIS EXISTS: Digicape's product pages (e.g. a specific MacBook Pro
config — chip variant, CPU/GPU core count, storage, RAM, colour) are real
configurators, but a static fetch of the page showed no price and no option
links in its content — consistent with either (a) a fast path: the page
embeds a JSON blob with every configuration's price already in it (common
for WooCommerce/Shopify-style stores — look for `data-product_variations`,
`ProductJson`, `__NEXT_DATA__`, `__NUXT__`, or a `variants`/`configurations`
key), which would mean no clicking is needed at all, or (b) a slow path:
the price only appears after you interact with the page (pick options,
wait for an API call), which would need real click-simulation the same way
takealot_apple_prices.py already drives Takealot's SPA. Guessing which one
it is and building the wrong kind of scraper is how a scraper ends up like
amazon_prices.py — unverified and possibly dead on arrival. This script
answers the question directly instead.

SETUP (same as every other scraper here):
    python3 -m venv venv
    source venv/bin/activate
    pip install "scrapling[all]>=0.4.14"
    scrapling install --force

RUN:
    python3 digicape_config_diagnostic.py

    # Or against a different product URL:
    python3 digicape_config_diagnostic.py --url "https://www.digicape.co.za/product/..."

OUTPUT:
    Prints a report to the console AND saves the full rendered HTML to
    digicape_config_dump.html (--out to change the path) so it can be
    inspected by hand too. Send me both: the console report and, if
    anything below looks incomplete or wrong, the saved HTML file.
"""

import argparse
import re
import sys

from scrapling.fetchers import StealthyFetcher

DEFAULT_URL = (
    "https://www.digicape.co.za/product/"
    "macbook-pro-14-inch-m5-chip-m5-chip-15c-cpu-16c-gpu-2tb-ssd-24gb-silver"
)

PRICE_REGEX = re.compile(r"R\s?[\d][\d,\s]*(?:\.\d{2})?")

# Known embedded-data patterns from common storefront platforms. If any of
# these show up, the page almost certainly carries every configuration's
# price already, in one blob, server-rendered — the fast path.
JSON_BLOB_MARKERS = [
    ("WooCommerce variable-product data", r'data-product_variations\s*=\s*(["\'])(.*?)\1'),
    ("WooCommerce variable-product data (unquoted script)", r'"product_variations"\s*:\s*(\[.*?\])'),
    ("Shopify ProductJson script tag", r'<script[^>]*id="ProductJson[^"]*"[^>]*>(.*?)</script>'),
    ("Shopify product.variants inline", r'"variants"\s*:\s*(\[.*?\])'),
    ("Next.js __NEXT_DATA__", r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>'),
    ("Nuxt __NUXT__ state", r'window\.__NUXT__\s*=\s*(.*?);?\s*</script>'),
    ("Generic 'configurations' key", r'"configurations"\s*:\s*(\[.*?\])'),
]

# Class/id name fragments that commonly wrap a configurator's option
# controls, tried loosely (substring match) since we don't know the real
# theme's naming.
OPTION_CONTAINER_HINTS = [
    "variation", "variant", "option", "configurator", "swatch",
    "attribute", "config-option", "product-options",
]


# Populated by the page.on("response", ...) listener registered in
# page_setup below. Deliberately records only cheap, always-safe properties
# (url/status/resource_type/content-type) — NOT response bodies. Reading a
# body from inside a Playwright sync-API event handler risks a reentrancy
# deadlock (the handler fires from an internal dispatcher thread while the
# main thread may be blocked elsewhere); this is a one-shot diagnostic the
# user runs and waits on, so it needs to reliably finish, not just be
# thorough. If this shows a promising API URL, reading its actual response
# shape is a fast, low-risk follow-up on its own — no need to gamble that
# here.
_captured_requests = []


def _page_setup(page):
    def on_response(response):
        try:
            if response.request.resource_type in ("xhr", "fetch"):
                _captured_requests.append({
                    "url": response.url,
                    "status": response.status,
                    "resource_type": response.request.resource_type,
                    "content_type": response.headers.get("content-type", ""),
                })
        except Exception:
            pass  # never let a diagnostic listener take down the whole probe

    page.on("response", on_response)


def fetch(url):
    print(f"Fetching {url} with a real headless browser (network_idle, extra wait)...")
    _captured_requests.clear()
    return StealthyFetcher.fetch(
        url,
        headless=True,
        network_idle=True,
        wait=4000,  # extra buffer after network idle in case price loads via a slow API call
        block_ads=True,
        solve_cloudflare=True,  # harmless no-op if no challenge is shown
        page_setup=_page_setup,  # registers the response listener before navigation starts
    )


def check_network_requests():
    print("\n=== 0. XHR/fetch calls the page made after loading ===")
    if not _captured_requests:
        print("  --> No XHR/fetch requests observed at all. Either everything needed "
              "was in the initial HTML (unlikely, given no price/options were found "
              "there — see sections below) or price/config data arrives some other "
              "way (e.g. embedded and revealed by JS without a network round-trip, "
              "or only after a real user interaction this passive load never triggers).")
        return
    print(f"  {len(_captured_requests)} XHR/fetch call(s) observed:")
    interesting_keywords = ("price", "product", "variant", "config", "sku", "graphql", "api", "cart")
    for req in _captured_requests:
        flag = " <-- looks price/product-related" if any(k in req["url"].lower() for k in interesting_keywords) else ""
        print(f"    [{req['status']}] {req['resource_type']:5s} {req['content_type']:30s} {req['url']}{flag}")
    print("  --> Any URL flagged above is worth fetching directly (e.g. with WebFetch, "
          "or curl from a real network) to see if it returns a clean JSON price for "
          "the selected configuration — if so, that's the fast, reliable path for a "
          "real scraper: call that endpoint directly, no browser needed at all.")


def html_of(page):
    return page.html_content if hasattr(page, "html_content") else str(page)


def check_json_blobs(html):
    print("\n=== 1. Embedded JSON / framework state blobs ===")
    found_any = False
    for label, pattern in JSON_BLOB_MARKERS:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m:
            found_any = True
            snippet = m.group(0)
            print(f"  FOUND: {label}")
            print(f"    First 300 chars: {snippet[:300]!r}")
        else:
            print(f"  not found: {label}")
    if not found_any:
        print("  --> No known embedded-data pattern matched. Either the theme is "
              "custom (a hand inspection of the saved HTML is needed), or "
              "pricing genuinely only arrives via a live API call after the "
              "page loads / after you interact with it.")
    return found_any


def check_visible_prices(page, html):
    print("\n=== 2. Any Rand price visible in the rendered HTML at all ===")
    matches = [m for m in PRICE_REGEX.findall(html)]
    if matches:
        print(f"  FOUND {len(matches)} price-looking string(s), first 10: {matches[:10]}")
    else:
        print("  --> No 'R<number>' price string appears anywhere in the rendered HTML, "
              "even after waiting for network idle + 4s. This means pricing is not just "
              "slow to load — it likely requires an explicit interaction (a click) that "
              "a passive page-load, however long you wait, will never trigger.")


def check_option_controls(page):
    print("\n=== 3. Option controls (chip / storage / RAM / colour selectors) ===")
    found_any = False
    for hint in OPTION_CONTAINER_HINTS:
        for tag_selector in (f"[class*='{hint}']", f"[id*='{hint}']"):
            try:
                elements = page.css(tag_selector)
            except Exception:
                continue
            if elements:
                found_any = True
                print(f"  selector {tag_selector!r} matched {len(elements)} element(s)")
                for el in elements[:3]:
                    text = " ".join((el.css("::text").getall() or [])).strip()
                    href = el.css("::attr(href)").get()
                    data_attrs = {}
                    # Scrapling elements don't expose a generic "all attributes"
                    # call uniformly across versions, so just probe the common
                    # ones a configurator would plausibly use.
                    for attr in ("data-value", "data-price", "data-variation-id", "value"):
                        val = el.css(f"::attr({attr})").get()
                        if val:
                            data_attrs[attr] = val
                    print(f"    text={text[:60]!r} href={href!r} data={data_attrs}")
    if not found_any:
        print("  --> None of the generic class/id name guesses matched anything. "
              "The saved HTML file needs a manual look — search it for the visible "
              "option label text (e.g. 'M5 Pro') to find the real element structure, "
              "then send me what you find.")


def check_option_hrefs(page):
    print("\n=== 4. Do any on-page links point at sibling configuration URLs? ===")
    links = page.css("a::attr(href)").getall()
    product_links = [l for l in links if l and "/product/" in l]
    same_family = [l for l in product_links if "macbook-pro-14-inch" in l or "macbook" in l.lower()]
    print(f"  {len(product_links)} total /product/ links on the page, "
          f"{len(same_family)} that look like sibling MacBook configs.")
    if same_family:
        print("  Examples:")
        for l in same_family[:8]:
            print(f"    {l}")
        print("  --> If these are real sibling-configuration URLs, that's the fast, "
              "reliable path: fetch each one directly (like this page) instead of "
              "simulating clicks on a single page.")
    else:
        print("  --> No sibling-config links found. If pricing is also only reachable "
              "via interaction (see section 2), a real per-config scraper would need "
              "to simulate clicking through the option controls found in section 3, "
              "which is slower and more fragile — confirm this is worth building "
              "before I write it.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help="Product URL to probe.")
    parser.add_argument("--out", default="digicape_config_dump.html",
                         help="Path to save the full rendered HTML (default digicape_config_dump.html).")
    args = parser.parse_args()

    try:
        page = fetch(args.url)
    except Exception as exc:
        print(f"ERROR fetching {args.url}: {exc}", file=sys.stderr)
        sys.exit(1)

    html = html_of(page)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved full rendered HTML ({len(html)} chars) to {args.out}\n")

    check_network_requests()
    check_json_blobs(html)
    check_visible_prices(page, html)
    check_option_controls(page)
    check_option_hrefs(page)

    print("\n=== Done ===")
    print(f"Send me this console output plus {args.out} (or at least: does 'grep -i "
          f"\"variation\\|configurator\\|R[0-9]\" {args.out}' turn up anything interesting?)")


if __name__ == "__main__":
    main()
