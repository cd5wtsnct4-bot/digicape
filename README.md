# Digicape Price Watch

A live-updating comparison of Digicape's Apple prices against four other South
African retailers — Takealot, Amazon SA, Incredible Connection, and iStore —
built as a static dashboard backed by a scheduled GitHub Actions scraper.
Digicape is the reference price for every product; every other retailer's
price is shown as a delta against it (cheaper, dearer, same, or flagged as a
different variant when it's not a like-for-like config). The visual design
and this baseline-vs-competitor model are ported from the ElevateSJC internal
"Digicape Price Watch" PHP tool.

## How it works

1. `.github/workflows/update-prices.yml` runs on a schedule (every 6 hours,
   plus a manual "Run workflow" button in the Actions tab) and executes the
   scraper scripts in `scripts/`.
2. Each scraper writes its retailer's results to `data/<retailer>.json`.
3. `scripts/combine.py` merges the files into `data/prices.json`,
   fuzzy-matching the same product across retailers so the dashboard can show
   a side-by-side comparison. Matching is heuristic — see the docstring at the
   top of that script for known limitations (iPhone/Watch SKU names vary the
   most between retailers). A product only appears in the output if Digicape
   returned a real price *in that run* — no fresh Digicape price means the
   product is dropped entirely, matching the reference design's rule that
   every card needs a current baseline. If a competitor's scraper comes back
   empty but had a price in the previous run, that price is carried forward
   and marked `"stale": true`; the dashboard shows it as "last known" rather
   than "Unavailable".
4. The workflow commits `data/prices.json` back to the repo.
5. `docs/index.html` (the dashboard) fetches that file directly from
   `raw.githubusercontent.com` every time it's opened, so it always shows
   the latest committed data. If the fetch fails — for example before the
   workflow has ever run — it falls back to a snapshot baked into the page
   at build time.

## Viewing the dashboard

Turn on GitHub Pages for this repo (Settings → Pages → Deploy from a branch →
`main` / `/docs`) and it will be served at
`https://cd5wtsnct4-bot.github.io/digicape/`. Until Pages is enabled, you can
still open `docs/index.html` directly — the live fetch works from any origin
because `raw.githubusercontent.com` sends permissive CORS headers.

## Running a scraper manually

Each script in `scripts/` can be run locally:

```bash
pip install "scrapling[all]>=0.4.14"
scrapling install --force
python scripts/digicape_prices.py
python scripts/istore_prices.py
python scripts/takealot_apple_prices.py
python scripts/incredible_prices.py
python scripts/amazon_prices.py
python scripts/combine.py
```

## Known limitations

- Product-name matching across retailers is heuristic, not exact. Some
  identical products may show up as separate unmatched cards; occasionally
  two different configurations may merge into one row. Spot-check
  `data/prices.json` after a run.
- Accessories catalogs are large and only partially represented.
- Retailer HTML structure changes over time — if a scraper starts returning
  zero results, the CSS selectors at the top of that script need updating
  (each script has a `--dump-html` flag to help re-calibrate them).
- **Digicape's selectors need re-verification against the live site.** The
  first production run of `digicape_prices.py` returned product names but no
  prices — the CSS selectors were written against a text-summarized view of
  the site, not the real DOM. A per-card regex fallback was added, but it
  hasn't yet been confirmed to actually recover prices on a real run. Since
  every card on the dashboard requires a fresh Digicape price to appear at
  all, this is the single most important thing to verify next — until it's
  fixed, the dashboard will show very few products.
- `amazon_prices.py` is unverified end-to-end: Amazon.co.za blocks automated
  fetching aggressively (robots.txt disallows it outright, and it's known for
  CAPTCHA-based bot detection). The selectors are Amazon's long-standing,
  well-documented ones, not guessed from nothing, but nothing in this
  project's development environment could load a real amazon.co.za page to
  confirm them. Run it once by hand and read its console output before
  trusting it in the scheduled workflow.
