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
   `scripts/digicape_prices.py` is intentionally narrow in scope: it only
   ever fetches Digicape's six named category pages (Mac, iPad, iPhone,
   Watch, AirPods, Apple TV) — no accessories, no other categories. That's
   the baseline dataset every comparison is built from.
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

- **Takealot showed zero matches until 2026-08-19, now fixed.** Every row from
  `takealot_apple_prices.py` was tagged `"category": "apple-promo"`
  unconditionally, while Digicape (the baseline) tags rows `mac`/`ipad`/
  `iphone`/`watch`/`airpods`/`appletv`. Since `combine.py` groups by
  `(category, normalized name)`, no Takealot row could ever land in the same
  group as a Digicape row, regardless of the name — Takealot was structurally
  excluded from the comparison the whole time this retailer has existed, not
  just a bad scrape. Fixed by classifying each Takealot listing into the same
  six categories by keyword (`classify_category()`) before it's written to
  `data/takealot.json`. Takealot's promo page lists specific high-end
  configs (e.g. "MacBook Pro 16 M5 Max ... 48GB RAM 1TB SSD") rather than
  Digicape's generic "from" price, so most MacBook rows still won't match a
  base config — that's correct behaviour, not a bug — but AirPods, whose
  names are simple, now match reliably.
- Amazon SA: **no working scraper yet, and none is planned without a paid
  proxy.** `amazon_prices.py` exists but Amazon.co.za's robots.txt disallows
  automated fetching outright and it runs real CAPTCHA-based bot detection —
  a direct server-side fetch reliably gets blocked, which is also what the
  ElevateSJC reference PHP tool's own README documents for this retailer. The
  reference tool's actual fix is a paid scraping/rendering proxy (it
  documents ScrapingAnt specifically) that executes JavaScript from a
  residential/datacenter IP and waits for the real product card before
  returning HTML — plain requests don't get that far regardless of the
  selectors used. Wiring that in would need an account and API key from
  whoever runs this repo; nothing here should be interpreted as an attempt to
  bypass Amazon's bot protection, and nothing here does.
- Product-name matching across retailers is heuristic, not exact. Some
  identical products may show up as separate unmatched cards; occasionally
  two different configurations may merge into one row. Spot-check
  `data/prices.json` after a run.
- Digicape accessories are out of scope by design (see "How it works" above)
  — the dashboard only ever compares the six named Apple hardware
  categories, never third-party or accessory listings.
- Retailer HTML structure changes over time — if a scraper starts returning
  zero results, the CSS selectors at the top of that script need updating
  (each script has a `--dump-html` flag to help re-calibrate them).
- **Digicape's price selectors are now confirmed working against the live
  site** (as of the 2026-08-19 12:41 UTC run — all 45 products across all
  six categories returned real prices). The current selectors are the same
  card/name/price shape ElevateSJC's own working PHP scraper uses
  (`article[data-dst-pid]` → `p.category__product--heading` →
  `.category__product--price strong`), not a guess — a per-card regex
  fallback still exists underneath in case Digicape's markup shifts again.
- **That same 12:41 UTC run also surfaced a real extraction bug, since
  fixed**: on cards showing a savings badge (e.g. "Save R3,800") next to the
  real price, the extractor sometimes grabbed the badge amount instead of the
  price itself — nine products (iPad Pro 13" M5, three iPhones, two Watch
  bands, three AirPods) briefly showed prices as low as R130. Both
  `digicape_prices.py`'s selector logic and its per-card text fallback now
  explicitly reject any candidate containing "save"/"off"/"discount"/"was"
  before accepting it as a price, and a per-category minimum-price check
  (e.g. an iPhone must be ≥ R5,000) drops anything still implausible rather
  than publish it. The nine affected rows were manually nulled out in
  `data/digicape.json` rather than left wrong; they'll repopulate with real
  numbers on the next scrape run.
- `amazon_prices.py` is unverified end-to-end: Amazon.co.za blocks automated
  fetching aggressively (robots.txt disallows it outright, and it's known for
  CAPTCHA-based bot detection). The selectors are Amazon's long-standing,
  well-documented ones, not guessed from nothing, but nothing in this
  project's development environment could load a real amazon.co.za page to
  confirm them. Run it once by hand and read its console output before
  trusting it in the scheduled workflow.
