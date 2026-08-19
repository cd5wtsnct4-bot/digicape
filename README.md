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
python scripts/digicape_mac_configs.py   # optional — see "Precise Mac SKU matching" below
python scripts/combine.py
```

## Precise Mac SKU matching (Phase 2)

`scripts/digicape_config_diagnostic.py` (see the "Known limitations" entry
below) answered the question of whether Digicape's per-configuration Mac
prices could be scraped without driving a headless browser through the
on-page configurator: yes. Every Mac product page embeds a `var available =
{...}` JavaScript object, directly in the page's static HTML, containing
every real sold configuration for that model line (chip tier → storage →
RAM → colour → product ID + price). No clicking required — one plain fetch
per model page gets the complete price matrix.

`scripts/digicape_mac_configs.py` is the scraper built on that finding: it
walks the Mac category page for each product's URL, fetches each product
page, and extracts its `available` tree into `data/digicape_mac_configs.json`.
It's a separate, optional script (not part of the standard six-scraper run
above) because it's slower — one extra page fetch per Mac model — and the
dashboard works fine without it, just with the existing family-level
matching described below.

When `data/digicape_mac_configs.json` is present, `combine.py` uses it to
add EXTRA comparison rows for any competitor Mac listing specific enough to
name an exact chip tier + storage + RAM combination that Digicape actually
sells — see `scripts/digicape_mac_spec_match.py`. These rows use Digicape's
real per-SKU price as the baseline instead of the category page's generic
"from" price, so they're a genuine same-spec comparison with no "Different
variant" caveat. This is purely additive: the existing family-level rows
are untouched, so a competitor row can show up in both a family-level row
(imprecise, but always available) and, when a real matching SKU exists, a
second, more precise row alongside it. If the configs file is missing, or a
specific model/spec isn't in it (a scraper failure for one model, or a
config a competitor lists that Digicape doesn't actually sell),
`combine.py` simply adds no extra row for that case rather than guessing —
family-level matching is always the fallback, never replaced.

Validated (2026-08-19) against a real reconstructed 14-configuration tree
for "MacBook Pro 14-inch (M5 chip)" and the real Takealot/Incredible
Connection listings already in this repo's `data/`: correctly produced
distinct, correctly-priced rows for three different M5 Pro configurations
that share overlapping storage/RAM sizes but are different real chip
variants at different real prices (15-core/16-core CPU/GPU at R50,899 vs
R50,999-on-special, and 18-core/20-core CPU/GPU at R65,199), and for the
base M5 chip at 16GB/1TB (R40,699) — all match the real numbers extracted
live from digicape.co.za. Also correctly declined to match configurations
that aren't real (e.g. an 18-core/20-core M5 Pro at 1TB, or an M5 Max at
48GB) rather than guessing the nearest one.

`scripts/digicape_mac_configs.py` has now been run against the live site
(2026-08-19, `--limit 2`: MacBook Air 15-inch and MacBook Pro 16-inch) and
found real configuration data for both — 12 and 8 real SKUs respectively.
Running the rest of the pipeline against that real output surfaced two more
real bugs, both fixed the same day:
1. Takealot's 16-inch MacBook Pro listings have no `-inch`/quote-mark unit
   at all (just `"MacBook Pro 16 M5 Pro ..."`, unlike its 14-inch listings)
   — confirmed directly, the bare `"16"` never became `"16in"` and the
   entire 16-inch line silently never matched Digicape at the family level.
   Fixed in `normalize_key()`: a bare 13/14/15/16 immediately after
   "MacBook Air"/"MacBook Pro" is now treated as the screen size, since
   Apple only ships those two lines in those four sizes.
2. A Digicape SKU leaf's own `"name"` field turned out, on the real data,
   to just repeat the model line's generic name for every configuration
   under it (not a per-spec name as originally assumed) — using it as a
   precise-match row's title produced several rows with an identical title
   and different prices. `build_precise_mac_items()` now always builds the
   title from the extracted spec instead of trusting the leaf's name field.

If a model's page structure doesn't match what the diagnostic found,
`digicape_mac_configs.py` prints a per-model failure without affecting
anything else — family-level matching still applies to that model.

**Now wired into `.github/workflows/update-prices.yml`** (2026-08-19), as a
"Scrape Digicape Mac configurations" step before "Combine into
data/prices.json" — after two manual runs against the live site confirmed
it: a `--limit 2` smoke test (2 models, 20 configurations), then a full run
(all 10 Mac product lines, 74 real configurations, zero failures), each
validated by running the rest of the pipeline against the real output.
Like every other scraper step in that workflow, it's `continue-on-error:
true` — if it fails or Digicape changes its page structure, the scheduled
run still completes and `combine.py` falls back to family-level matching
for that run rather than the whole job breaking.

## Known limitations

- **Mac matching against Takealot showed zero results, and iPhone/Watch
  generation numbers could silently merge — both now fixed (2026-08-19).**
  Two separate bugs in `combine.py`'s `normalize_key()`, found while
  investigating a user report that Takealot's MacBook listings weren't
  matching Digicape at all:
  1. Takealot spells out full specs (`"Apple MacBook Pro 14" M5 Pro 15 core
     CPU and 16 core GPU, 24GB, 2TB SSD"`) while Digicape's category page
     only shows a generic `"MacBook Pro 14-inch (M5 chip)"` line. Matching
     requires the two names' token sets to be *identical*, and the leftover
     `core`/`cpu`/`gpu`/`ssd` boilerplate words blocked every Mac match that
     had a fully-specced Takealot name — confirmed directly by comparing the
     two names' computed keys. Fixed by stripping that boilerplate (and
     removing core counts as a `"<number> core"` unit) before matching.
  2. Fixing that surfaced a second, worse bug: the normalizer's rule for
     dropping leftover bare numbers (`"drop any 2-4 digit token that isn't a
     unit"`) couldn't tell a leftover storage digit apart from a meaningful
     model number, and was silently merging `"iPhone 15"`, `"iPhone 16"`,
     and `"iPhone 17"` into a single row — confirmed live in
     `data/prices.json` before this fix, where the merged row was mislabeled
     "iPhone 17" but showed the iPhone 15's price, because the combiner
     always keeps whichever retailer's price is lowest. The same rule was
     also merging `"Apple Watch Series 10"` and `"Series 11"`. Removed that
     rule now that spaced storage sizes and core counts are handled
     explicitly instead.
  One accuracy caveat this doesn't (and can't, from a category-listing page
  alone) fully solve: Digicape's category pages only ever expose one generic
  "from" price per model line — confirmed directly, none of Digicape's 45
  product names carry a storage size, RAM size, or core count. So a Takealot
  or Incredible Connection row that lists a specific config (e.g. the M5 Pro
  15-core/16-core/24GB/1TB MacBook Pro 14") now correctly *appears* next to
  Digicape's base 14" Pro price, but it's a family-level match, not a
  same-spec one — the two prices aren't for an identical configuration.
  `combine.py` now flags this automatically: when a competitor's listing
  mentions a storage size, RAM size, or core count that Digicape's matched
  name doesn't, that cell gets a `"note"` and the dashboard shows a
  "Different variant" badge instead of treating it as an exact price match
  or a "cheapest" deal (the same mechanism already used for Incredible
  Connection's hand-curated promo notes). Checked Digicape's own live
  product page for a 14" MacBook Pro config
  (`/product/macbook-pro-14-inch-m5-chip-m5-chip-15c-cpu-16c-gpu-2tb-ssd-24gb-silver`):
  it's a real chip/storage/RAM/colour configurator, but the price isn't in
  the page's static HTML — it's loaded client-side after you pick options,
  the same way Takealot's own SPA works. Scraping *every* real per-config
  price would mean driving a headless browser through that selector for
  every valid combination on every Mac model, which is a materially bigger
  project than a matching-key fix — flagging it here rather than building it
  without being asked, since it needs a decision on whether that scrape cost
  is worth it before committing to it. Decided it's worth investigating:
  `scripts/digicape_config_diagnostic.py` is step one, a diagnostic (not a
  scraper) that answers the question this development environment has no
  network access to answer itself — does Digicape's configurator load every
  configuration's price in one embedded JSON blob (fast, reliable path) or
  only after a live interaction (slow path, needs real click-simulation) —
  by capturing the page's actual XHR/fetch calls and inspecting its rendered
  HTML for known embedded-data patterns. Run it once with real network
  access and its output decides how the real per-config scraper gets built.
- **iStore was missing iPad, Watch, and Apple TV entirely, now fixed (pending
  verification).** Incredible Connection turned out fine on inspection — its
  rows already use the same six categories Digicape does, and it was
  contributing real matches. iStore's `/mac`, `/iphone`, `/accessories`, and
  `/airpods` pages are real Magento product grids and scraped correctly, but
  `/ipad` and `/watch` are marketing/discovery landing pages (a hero banner
  plus a handful of featured tiles), not the grid `PRODUCT_SELECTORS`
  expects — confirmed by inspecting them directly, they returned 0 products
  every run, silently. Apple TV wasn't in the category list at all, a plain
  omission. Swapped in the real grid URLs
  (`/ipad/shop-ipad-range`, `/apple-watch/shop-watch-range`,
  `/music-and-tech/discover-apple-tv/shop-apple-tv`), confirmed via direct
  inspection to list every current model with a real price. These are the
  same Magento template family as the working pages, so the existing
  selectors should carry over, but this environment has no network access to
  istore.co.za to confirm against real rendered HTML — check the next
  scheduled run's output for these three categories before trusting it fully.
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
- **Amazon SA: assumption revised (2026-08-19) — working, including from
  GitHub Actions.** The original assumption here (a direct fetch reliably
  gets blocked, matching what the ElevateSJC reference PHP tool's README
  documents) turned out not to hold. Verified twice against the live site:
  first by hand from a residential connection (20/20 curated URLs, real
  prices, zero CAPTCHA hits), then by the scheduled GitHub Actions workflow
  itself on its first real run with the new step (same result: 20/20,
  `data/amazon.json` from the 2026-08-19 18:50 UTC run) — so it isn't
  blocked from Actions' shared IP range either, which was the open question
  after the first test. `amazon_prices.py` now has a `continue-on-error`
  step in the scheduled workflow like every other scraper. Two clean runs
  aren't a permanent guarantee — Amazon's bot detection can vary over time
  or by IP reputation, so it's worth an occasional glance at that step's
  log — but there's no reason left to treat this retailer as unworkable. If
  it ever does start getting blocked, the reference tool's fix is a paid
  scraping/rendering proxy (it documents ScrapingAnt specifically) that
  executes JavaScript from a residential/datacenter IP and waits for the
  real product card before returning HTML; that would need an account and
  API key from whoever runs this repo. Nothing here should be interpreted
  as an attempt to bypass Amazon's bot protection — the script backs off
  the moment it detects a CAPTCHA/bot-check page rather than trying to get
  past it, and nothing here does otherwise.
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
