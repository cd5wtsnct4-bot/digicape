#!/usr/bin/env python3
"""
Merge data/digicape.json, data/istore.json, data/takealot.json,
data/incredible.json, and data/amazon.json (whichever exist and are
non-empty) into a single data/prices.json that the comparison dashboard
(docs/index.html) fetches at load time.

BASELINE MODEL: Digicape is the reference retailer (this mirrors the
ElevateSJC "Digicape Price Watch" design this dashboard's UI is ported
from). A product only appears in the output if Digicape returned a real
price THIS run — no Digicape price this run means the card is dropped
entirely, not shown with "Unavailable" in the reference slot. Every other
retailer's price is carried alongside it for the frontend to compute a
delta against Digicape.

STALE CARRY-FORWARD: if a retailer's scraper comes back with nothing for a
product this run (network hiccup, selector rot, bot block) but the
*previous* data/prices.json had a real price for it, that old price is
carried forward and marked "stale": true rather than silently dropped.
The frontend shows it as "last known" rather than "Unavailable". This only
ever reads the previous output file — it never invents a number that
wasn't observed on some earlier real run.

MATCHING IS HEURISTIC, NOT EXACT. Retailers name the same product
differently — "MacBook Pro 14-inch M5" vs "14-inch MacBook Pro M5" vs
"MacBook Pro 14″ (M5)" — and some listings are a specific SKU (a storage
size + colour, e.g. "iPhone 17 Pro 256GB - Silver") rather than the
model's base "from" price. This script normalizes names into an
order-independent token key (lowercased, inch-marks unified, storage/RAM
sizes, core counts, and colour words stripped, tokens sorted) so
reasonably-similar names collapse into the same comparison row. It will
occasionally either merge two things that are subtly different configs, or
fail to merge two names that are further apart than the normalizer expects.
Spot-check data/prices.json after a run, especially for iPhone/Watch, where
promotional SKU names are the least consistent across retailers.

Because Digicape's own category pages only ever expose one generic "from"
price per model line (see has_config_markers() below), a competitor's
fully-specced listing (mentions a storage size, RAM size, or core count
Digicape's matched name doesn't) is auto-flagged rather than silently
treated as an exact price match. A row carrying a "note" field — either
hand-set by a scraper (see incredible_prices.py's on-promo notes) or set
automatically here — is treated as a known different-variant/config
caveat: the frontend shows "Different variant" for it and never flags it
as a deal or the cheapest, the same way the reference PHP app's
manually-curated variant_note field works.

PRECISE MAC SKU MATCHING (on top of the family-level matching above): if
scripts/digicape_mac_configs.py has been run, data/digicape_mac_configs.json
holds every Mac model's real per-configuration price matrix (chip tier ->
storage -> RAM -> colour -> product_id/price), scraped from the `available`
JS object embedded in each Digicape product page. When present, this script
adds EXTRA comparison rows (alongside the existing family-level ones,
untouched) for any competitor Mac listing specific enough to identify an
exact chip tier + storage + RAM combination that's a real, currently-sold
Digicape configuration — see scripts/digicape_mac_spec_match.py for the
extraction/matching logic. These rows use Digicape's real SKU price as the
baseline instead of the category page's generic "from" price, so they're a
genuine same-spec comparison with no "Different variant" caveat needed.
Colour is deliberately not part of the match (confirmed on real data: same
spec, different colour, same price), so one precise row can cover multiple
competitor colour variants. If the configs file is missing, or a specific
model/spec isn't in it, this simply adds no extra rows for that case —
family-level matching (and its "Different variant" note) is always the
fallback, never replaced.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from digicape_mac_spec_match import (
    effective_price,
    extract_chip_tier,
    extract_cpu_gpu_cores,
    extract_ram_gb,
    extract_storage_tb,
    find_matching_config,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

BASELINE_RETAILER = "digicape"

# Display order matches the reference app's meta-row: baseline first, then
# competitors in the order they're introduced there.
RETAILER_LABELS = {
    "digicape": "Digicape",
    "takealot": "Takealot",
    "amazon": "Amazon SA",
    "incredible": "Incredible Connection",
    "istore": "iStore",
}

CATEGORY_LABELS = {
    "mac": "Mac",
    "ipad": "iPad",
    "iphone": "iPhone",
    "watch": "Watch",
    "airpods": "AirPods",
    "appletv": "Apple TV",
    "apple-promo": "Apple (Takealot promo)",
}

# Storage sizes and Apple's more common colour/finish names — stripped during
# normalization so the same physical model matches across retailers even
# when one lists a specific SKU (size + colour) and another lists a bare
# "from" price. Not exhaustive — extend this list if you see near-duplicate
# rows in data/prices.json that should have merged.
STRIP_WORDS = {
    "gb", "tb",
    "silver", "black", "white", "blue", "green", "red", "yellow", "pink",
    "purple", "gold", "grey", "gray", "natural", "titanium", "midnight",
    "starlight", "orange", "graphite", "sierra", "cosmic", "sky", "stone",
    "deep", "space", "ultramarine", "teal", "rose", "mist", "jet",
    # Real bug, found 2026-08-19: Incredible Connection writes this colour
    # as one joined word ("SpaceBlack") where Digicape/others write it as
    # two ("Space Black") — "space" and "black" were already individually
    # stripped above, but the joined form is a different token entirely and
    # slipped through, leaving every SpaceBlack Mac SKU unmatched against
    # its Silver sibling of the same spec.
    "spaceblack",
    # generic filler that varies between retailers without changing the product
    "chip", "chipset", "processor", "with", "the", "and", "for", "gen",
    "generation", "wifi", "cellular",
    # Real bug, found 2026-08-19: Takealot's Mac listings spell out full specs
    # ("Apple MacBook Pro 14" M5 Pro 15 core CPU and 16 core GPU, 24GB, 2TB
    # SSD") while Digicape's category page only ever shows a generic
    # "MacBook Pro 14-inch (M5 chip)" line. normalize_key() requires the
    # SORTED TOKEN SETS to match exactly, not just overlap, so these four
    # unstripped boilerplate spec words silently blocked every Mac match
    # that had a fully-specced Takealot name — confirmed directly:
    # normalize_key() produced "14in core cpu gpu m5 macbook pro ssd" for the
    # Takealot name above vs "14in m5 macbook pro" for Digicape's, differing
    # only by these four tokens. Core counts (15/16) and storage/RAM sizes
    # (24GB/2TB) are already dropped by the numeric-token rules below, so
    # once this boilerplate is stripped too, both keys collapse to the same
    # value and the row matches.
    "core", "cores", "cpu", "gpu", "ssd", "ram",
    # Real bug, found 2026-08-19: Amazon's product titles are full marketing
    # copy (dozens of spec/feature/marketing words) while Digicape's names
    # are short and clean, e.g. "Apple MacBook Air 13-inch Laptop with M4
    # chip: Built for Apple Intelligence, 13.6-inch Liquid Retina Display,
    # 24GB Unified Memory, ..." vs Digicape's plain "MacBook Air 13-inch (M5
    # chip)". Confirmed directly: ALL 20 scraped Amazon rows failed to match
    # anything because of leftover marketing tokens like these — none of
    # them describe a materially different product, they're just Amazon's
    # copywriting. Verified against every real Amazon title captured
    # 2026-08-19 across mac/ipad/iphone/watch/airpods/appletv that this list
    # (plus the regexes below) reduces each one to exactly the same token set
    # as its real Digicape counterpart, with no accidental collisions among
    # Digicape's own distinctly-priced products (see test_combine.py).
    "laptop", "built", "intelligence", "ai", "liquid", "retina", "xdr",
    "unified", "memory", "storage", "display", "center", "stage", "camera",
    "touch", "id", "all", "day", "battery", "life", "front", "back",
    "landscape", "high", "fidelity", "sound", "wireless", "earbuds",
    "bluetooth", "headphones", "adaptive", "audio", "transparency", "mode",
    "personalised", "personalized", "spatial", "usb", "charging", "case",
    "over", "ear", "level", "smartphone", "big", "boost", "control",
    "improved", "scratch", "resistance", "group", "selfies", "smarter",
    "breakthrough", "system", "fusion", "promotion", "best", "ever", "any",
    "aluminium", "aluminum", "always", "on", "water", "resistant", "health",
    "monitoring", "fitness", "tracker", "trackers", "score", "sleep",
    "heart", "rate", "monitor", "sensing", "smartwatch", "gps", "n1",
    "works", "to", "up", "in", "wi", "fi", "iphone",
    # NOTE: "active", "noise", "cancellation", and "ethernet" are
    # deliberately NOT in this list, even though stripping them would help
    # a couple of Amazon AirPods Pro/Max/Apple TV listings match. Confirmed
    # directly this would be wrong: Digicape has real, differently-priced
    # SKUs whose name is the ONLY thing distinguishing them by exactly one
    # of these words — "AirPods 4" (R2,799) vs "AirPods 4 with Active Noise
    # Cancellation" (R3,699); "Apple TV 4K 128Gb Wifi+Ethernet" (R5,249) vs
    # "Apple TV 4K 64Gb Wifi" (R4,199) (the storage digits are already
    # stripped by the compound-token rule below, leaving "Ethernet" as the
    # only remaining differentiator). Stripping these words uniformly
    # collapsed both pairs into one row each, silently discarding a real
    # price difference — a worse outcome than the Pro/Max listings simply
    # not matching. See test_combine.py's
    # TestAmazonMarketingCopyStripping.test_anc_and_ethernet_words_not_stripped.
    "a", "c", "l", "m",
    # Single-letter tokens Amazon titles leave behind after other words are
    # stripped ("and a Big Boost" -> stray "a"; Watch band sizing "M/L" ->
    # stray "m"/"l"; "USB‑C" -> stray "c"). None of Digicape's own names
    # contain a bare single-letter word, so these can only ever be removed
    # from a competitor's key, never a false-strip on Digicape's side.
}


# Real Apple marketed Mac/iPad screen sizes (whole inches). Amazon states the
# literal panel diagonal as a decimal ("13.6-inch", "14.2-inch") while Apple
# (and Digicape, following Apple's naming) markets the model by the rounded
# whole number ("13-inch MacBook Air", "14-inch MacBook Pro"). floor() of the
# decimal always lands on the marketed number for every current Mac/iPad
# line. iPhone and iPad mini also state a decimal diagonal ("6.3-inch",
# "8.3-inch") but neither Apple nor Digicape names those models by screen
# size at all, so a decimal outside this curated list is dropped entirely
# rather than guessed at — see the decimal-inch handling below.
MAC_IPAD_WHOLE_INCH_SIZES = {"9", "10", "11", "12", "13", "14", "15", "16"}


def _decimal_inch_repl(match):
    whole = match.group(1)
    if whole in MAC_IPAD_WHOLE_INCH_SIZES:
        return f"{whole}in"
    return " "  # not a marketed Mac/iPad size (e.g. iPhone/iPad mini diagonal) — drop it


def normalize_key(name):
    """Order-independent, loosely-normalized token key for fuzzy matching
    the same product across retailers' differently-worded listings."""
    s = name.lower().replace("’", "'")
    s = re.sub(r'(\d+)\s*[\"″]', r'\1in', s)       # 14"  / 14″ -> 14in
    # Real bug, found 2026-08-19: Amazon's Mac/iPad titles state the literal
    # display diagonal as a decimal ("13.6-inch", "14.2-inch") rather than
    # Apple's marketed whole number. Confirmed directly: without this, the
    # existing bare-inch regex below only grabs the digit right before
    # "-inch" (turning "13.6-inch" into stray tokens "13" and "6in" instead
    # of the single "13in" Digicape's own name produces), so this MUST run
    # before that regex, not after.
    s = re.sub(r'(\d+)\.\d+\s*-?\s*inch', _decimal_inch_repl, s)
    s = re.sub(r'(\d+)\s*-?\s*inch', r'\1in', s)         # 14-inch / 14 inch -> 14in
    # Real bug, found 2026-08-19: Takealot's 16" MacBook Pro listings say
    # "MacBook Pro 16 M5 Max ..." — a bare "16" with no "-inch"/quote-mark
    # unit at all (unlike its 14" listings, which do say 14"). Confirmed
    # directly: normalize_key() produced "16 m5 macbook max pro" for that
    # name vs "16in m5 macbook pro" for Digicape's "MacBook Pro 16-inch (M5
    # chip)" — the bare "16" token never becomes "16in" without a unit to
    # convert, so these can never match, silently excluding the entire 16"
    # line from Takealot comparisons. Apple only ships MacBook Air/Pro in
    # 13/14/15/16-inch sizes, so a bare 13-16 immediately after "macbook
    # air"/"macbook pro" is unambiguously the screen size, not a spec
    # number — safe to convert here even though a truly bare number
    # elsewhere in a name is deliberately left alone (see the removed
    # blanket bare-number rule below).
    s = re.sub(r'\b(macbook\s+(?:air|pro))\s+(1[3-6])\b', r'\1 \2in', s)
    # Real bug, found 2026-08-19: Takealot's "MacBook Neo" listings also
    # state a bare size ("Apple MacBook Neo 13 A18 Pro ..."), but unlike
    # Air/Pro, Digicape's own "MacBook Neo" name has no size in it at all
    # (it's a single-size line) — so converting to "13in" would just add a
    # token Digicape's key doesn't have. Dropped entirely instead, since
    # there's currently only one Neo size to disambiguate from.
    s = re.sub(r'\b(macbook\s+neo)\s+(1[3-6])\b', r'\1', s)
    s = re.sub(r'(\d+)\s*gb\b', r'\1gb', s)              # normalize spacing on "256 GB"
    s = re.sub(r'(\d+)\s*tb\b', r'\1tb', s)              # normalize spacing on "2 TB"
    # Real bug, found 2026-08-19: Takealot spells out core counts as
    # "15 core CPU" / "16 core GPU" — a bare number followed by the word
    # "core". Removed as one unit (number + word together) rather than
    # relying on STRIP_WORDS + a generic bare-number rule, because a
    # generic rule can't tell a core count apart from a meaningful model
    # number (see the removed bare-number rule below). Uses [^a-z0-9]*
    # rather than a plain ASCII "-?" between the digits and "core" because
    # Incredible Connection's listings use U+2011 (non-breaking hyphen,
    # "10‑core") instead of a plain "-" — confirmed directly: with only
    # "-?" here, "10‑core CPU" left a stray bare "10" token that blocked
    # several real 14" M5 (non-Pro) matches Digicape does carry.
    s = re.sub(r'\d+[^a-z0-9]*core\b', ' ', s)
    # Real bug, found 2026-08-19: Amazon states Wi-Fi generation ("Wi-Fi 6",
    # "Wi‑Fi 6E", "Wi-Fi 7" — note some titles use U+2011 non-breaking
    # hyphen, not a plain "-") which Digicape's names never mention at all.
    # Collapsed as one unit (word + trailing generation digit/letter)
    # BEFORE the generic non-alnum strip below, otherwise the bare
    # generation number is indistinguishable from a meaningful digit once
    # "wi"/"fi" are removed as separate stopwords.
    s = re.sub(r'wi[^a-z0-9]*fi[^a-z0-9]*\d*e?\b', ' ', s)
    # Real bug, found 2026-08-19: Amazon states camera megapixels ("12MP"),
    # Watch case size ("42mm", "44mm"), display refresh rate ("120Hz"), and
    # cellular generation ("5G") — none of which Digicape's names ever
    # mention. Stripped as number+unit units (not via STRIP_WORDS) because
    # the leading digit would otherwise survive as a stray bare-number token
    # once the unit word is removed.
    s = re.sub(r'\d+\s*mp\b', ' ', s)
    s = re.sub(r'\d+\s*mm\b', ' ', s)
    s = re.sub(r'\d+\s*hz\b', ' ', s)
    s = re.sub(r'\b\d+g\b', ' ', s)
    # Real bug, found 2026-08-19: some Amazon iPhone titles end with an
    # incidental "Works with AirPods" mention. "airpods" can't go in
    # STRIP_WORDS (it would collapse Digicape's differently-priced "AirPods
    # 4" and "AirPods 4 with Active Noise Cancellation" into one row), so
    # this specific recurring marketing phrase is collapsed as a unit
    # instead, leaving real AirPods product listings untouched.
    s = re.sub(r'works with airpods', ' ', s)
    # "Pro‑Level" (AirPods Max marketing copy) is an adjective, not a real
    # "Pro" product tier — Digicape has no separate "AirPods Max Pro" line,
    # so left un-stripped this would wrongly introduce a "pro" token that
    # has no counterpart in Digicape's plain "AirPods Max" name.
    s = re.sub(r'pro[^a-z0-9]*level', ' ', s)
    # Real bug, found 2026-08-19: Amazon/Takealot always state the A-series
    # chip generation ("A16 chip", "A19 Pro Chip", "A18 Pro") while
    # Digicape's iPhone and MacBook Neo names never mention a chip at all
    # (its iPad names do — "iPad 11-inch (A16 chip)" — but symmetrically
    # stripping it there is safe: no two current Digicape iPad lines share
    # a base name and differ only by A-chip generation). Matched and
    # dropped as ONE phrase (chip number + optional "Pro"/"Max" suffix)
    # rather than as separate tokens, so "A18 Pro" doesn't leave a stray
    # "pro" behind — that stray "pro" would otherwise be indistinguishable
    # from "MacBook Pro"'s meaningful "pro". "M"-chip tokens (m2/m3/m4/m5)
    # are deliberately NOT touched — those DO distinguish real,
    # differently-priced Digicape SKUs (e.g. "iPad Air 11-inch (M3 chip)"
    # vs "... (M4 chip)").
    s = re.sub(r'\ba\d{1,2}\s*(pro|max)?\b', ' ', s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [t for t in s.split() if t not in STRIP_WORDS and t != "apple"]
    # drop compound storage/RAM tokens like "256gb", "1tb", "24gb" — a
    # retailer listing a specific config (e.g. "24GB/1TB") shouldn't stop it
    # matching another retailer's bare "from" price for the same base model
    tokens = [t for t in tokens if not re.fullmatch(r"\d{1,4}(gb|tb)", t)]
    # AirPods' "H2 Chip" -- Digicape never names an H-chip generation, so
    # it's always pure Amazon filler. Kept as a separate token-level filter
    # (rather than folded into the phrase regex above) since it has no
    # "Pro"/"Max" suffix variant to worry about.
    tokens = [t for t in tokens if not re.fullmatch(r"h\d{1,2}", t)]
    # NOTE: an earlier version of this function also dropped any leftover
    # bare 2-4 digit token ("keeps '14in' but drops '256'"). Real bug, found
    # 2026-08-19: that rule is indiscriminate — it can't distinguish a
    # leftover storage digit from a meaningful model/generation number, and
    # it was silently merging "iPhone 15", "iPhone 16", and "iPhone 17" (and
    # separately, "Apple Watch Series 10" and "Series 11") into one row,
    # because all of those numbers are bare 2-digit tokens with no unit.
    # Confirmed live in data/prices.json before this fix: the merged row was
    # mislabeled "iPhone 17" but showed the iPhone 15's price (R13,999)
    # because the combiner keeps whichever matched price is lowest. Now that
    # spaced GB/TB and core counts are joined/removed explicitly above (and
    # joined GB/TB like "256gb" is caught by the compound-token rule right
    # above), there's no remaining case that rule was needed for, and
    # removing it stops those two real products from being confused with
    # each other.
    return " ".join(sorted(set(tokens)))


# Digicape's category pages only ever show one generic "from" price per
# model line (confirmed directly: e.g. "MacBook Pro 14-inch (M5 chip)" is a
# single row covering the entire 14" Pro range, base M5 through M5 Pro/Max —
# none of Digicape's 45 product names carry a storage size, RAM size, or
# core count). Takealot and Incredible Connection, by contrast, often list a
# specific configuration (e.g. "M5 Pro 15-core CPU/16-core GPU, 24GB, 2TB
# SSD"). normalize_key() now matches these to the same row on purpose (see
# the STRIP_WORDS comment above) so the product shows up at all — but the
# match is family-level, not a same-spec price comparison: Digicape's price
# is the cheapest config in that line, not necessarily this one. Flagging
# that distinction is exactly what the existing "note" field / "Different
# variant" badge in docs/index.html was already built for (see this file's
# module docstring) — this just detects the case automatically instead of
# requiring a scraper to hand-curate it.
CONFIG_MARKER_RE = re.compile(r"\d{1,4}\s*-?\s*(?:gb|tb)\b|\d+\s*-?\s*core\b", re.IGNORECASE)


def has_config_markers(name):
    return bool(CONFIG_MARKER_RE.search(name or ""))


def load_items(filename, retailer):
    path = DATA_DIR / filename
    if not path.exists():
        print(f"[combine] {filename} not found — skipping {retailer} (run its scraper first)")
        return []
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"[combine] WARNING: {filename} is not valid JSON ({exc}) — skipping", file=sys.stderr)
        return []
    print(f"[combine] loaded {len(items)} rows from {filename}")
    return items


def load_previous_output():
    """Index the previous data/prices.json by (category, normalized key) so
    stale prices can be carried forward. Returns {} if there is no previous
    run (first-ever run) or it can't be parsed."""
    path = DATA_DIR / "prices.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[combine] couldn't read previous prices.json for stale carry-forward ({exc})", file=sys.stderr)
        return {}

    index = {}
    for item in prev.get("items", []):
        key = normalize_key(item.get("title", ""))
        if not key:
            continue
        index[(item.get("category"), key)] = item.get("prices", {})
    return index


def load_mac_configs():
    """Load data/digicape_mac_configs.json (written by
    scripts/digicape_mac_configs.py) if it exists, and index its models by
    the same normalize_key() used for family-level grouping, so a
    competitor row's family match (e.g. "MacBook Pro 14-inch (M5 chip)")
    can look up its precise configuration tree directly. Returns {} if the
    file doesn't exist or can't be parsed — precise matching is always
    optional, never required."""
    path = DATA_DIR / "digicape_mac_configs.json"
    if not path.exists():
        print(f"[combine] {path.name} not found — skipping precise Mac SKU "
              f"matching (run scripts/digicape_mac_configs.py to enable it)")
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[combine] couldn't read {path.name} ({exc}) — skipping precise Mac SKU matching",
              file=sys.stderr)
        return {}

    lookup = {}
    for model in data.get("models", []):
        key = normalize_key(model.get("name", ""))
        if key:
            lookup[key] = model
    print(f"[combine] loaded {len(lookup)} Mac model configuration tree(s) from {path.name}")
    return lookup


def build_precise_mac_items(raw, mac_model_lookup):
    """Scan every non-baseline Mac row for a specific-enough spec (chip
    tier + storage + RAM) that resolves to a real Digicape SKU in
    mac_model_lookup, and produce extra, genuinely same-spec comparison
    rows for the ones that do. Purely additive: the family-level rows built
    in main() are untouched, so this can only add rows, never remove or
    change existing ones.

    Grouped by (family, chip tier, CPU/GPU core counts, storage, RAM,
    rounded Digicape price) rather than product_id, because product_id is
    colour-specific (a Silver and a Space Black SKU are two different
    product_ids at the same price) and colour isn't part of the match — see
    the module docstring. Core counts are included in the key (not just the
    title) specifically so two real configurations that share a storage+RAM
    size but differ in chip variant — confirmed to happen on real data,
    e.g. the 14" M5 Pro line sells both a 15-core-CPU/16-core-GPU and an
    18-core-CPU/20-core-GPU variant at 24GB/2TB — never collapse into one
    row just because a competitor's listing happens not to state them. The
    rounded price is still included too, as a last-resort tie-breaker for
    the rare case a competitor's listing omits core counts entirely
    (find_matching_config() already refuses to guess in that situation
    unless every candidate shares one price, so this key stays correct
    there as well).
    """
    if not mac_model_lookup:
        return []

    groups = {}  # key -> {"title": str, "category": "mac", "prices": {...}}

    for row in raw:
        if row.get("category") != "mac" or row.get("retailer") == BASELINE_RETAILER:
            continue
        name = (row.get("name") or "").strip()
        price = row.get("price")
        retailer = row.get("retailer", "unknown")
        if not name or price is None:
            continue

        model = mac_model_lookup.get(normalize_key(name))
        if not model:
            continue  # this competitor's family doesn't have a scraped config tree this run

        leaf = find_matching_config(name, model.get("available", {}))
        if not leaf:
            continue  # not specific enough, or not a real sold configuration — no guessing

        digicape_price = effective_price(leaf)
        if digicape_price is None:
            continue

        tier = extract_chip_tier(name)
        cpu, gpu = extract_cpu_gpu_cores(name)
        storage_key = extract_storage_tb(name)
        ram_key = extract_ram_gb(name)
        group_key = (normalize_key(model["name"]), tier, cpu, gpu, storage_key, ram_key, round(digicape_price))

        # Real bug, found 2026-08-19 against the live site: a leaf's own
        # "name" field is NOT a per-spec name — confirmed directly, every
        # configuration under a given Digicape product page shares the
        # exact same generic "name" as the model line itself (colour and
        # spec aren't in it). Using it as this row's title produced several
        # rows all titled e.g. "MacBook Pro 16-inch (M5 chip)" with
        # different prices and no way to tell them apart. Always build the
        # title from the extracted spec instead — see describe_precise_config.
        title = describe_precise_config(model["name"], tier, cpu, gpu, storage_key, ram_key)

        if group_key not in groups:
            digicape_cell = {
                "price": digicape_price,
                "price_text": "R " + format(digicape_price, ",.0f"),
                "url": model.get("url", ""),
                "stale": False,
            }
            groups[group_key] = {
                "title": title,
                "category": "mac",
                "prices": {BASELINE_RETAILER: digicape_cell},
            }

        entry = groups[group_key]
        existing = entry["prices"].get(retailer)
        if existing is None or price < existing["price"]:
            entry["prices"][retailer] = {
                "price": price,
                "price_text": row.get("price_text", ""),
                "url": row.get("url", ""),
                "stale": False,
            }

    return [
        {"category": CATEGORY_LABELS.get(entry["category"], entry["category"]),
         "title": entry["title"], "prices": entry["prices"]}
        for entry in groups.values()
    ]


def describe_precise_config(model_name, tier, cpu, gpu, storage_key, ram_key):
    """Builds this row's display title from the extracted spec. Confirmed
    on real data that a leaf's own "name" field is just the model line's
    generic name repeated for every configuration (see the comment where
    this is called) — not usable as a title — so this is the only source
    of a title specific enough to tell two different real configurations
    apart on the dashboard."""
    parts = [model_name]
    if tier == "pro":
        parts.append("Pro")
    elif tier == "max":
        parts.append("Max")
    if cpu and gpu:
        parts.append(f"{cpu}-core CPU/{gpu}-core GPU")
    if ram_key:
        parts.append(ram_key.upper())
    if storage_key:
        parts.append(storage_key.replace("_ssd", "").upper() + " SSD")
    return " ".join(parts)


def main():
    raw = []
    raw += load_items("digicape.json", "digicape")
    raw += load_items("takealot.json", "takealot")
    raw += load_items("amazon.json", "amazon")
    raw += load_items("incredible.json", "incredible")
    raw += load_items("istore.json", "istore")

    if not raw:
        print("[combine] no input data found in data/ — nothing to write. "
              "Run at least one scraper script first.", file=sys.stderr)
        sys.exit(1)

    previous_index = load_previous_output()

    grouped = {}  # (category, key) -> {"title": str, "prices": {retailer: {...}}}
    skipped_no_price = 0

    for row in raw:
        price = row.get("price")
        name = (row.get("name") or "").strip()
        retailer = row.get("retailer", "unknown")
        category = row.get("category", "unknown")

        if price is None or not name:
            skipped_no_price += 1
            continue

        key = normalize_key(name)
        if not key:
            skipped_no_price += 1
            continue

        group_key = (category, key)
        if group_key not in grouped:
            grouped[group_key] = {"title": name, "category": category, "prices": {}, "baseline_name": None}

        entry = grouped[group_key]
        # prefer the shortest name seen so far as the display title (tends to
        # be the "from"/base listing rather than a specific colour+size SKU)
        if len(name) < len(entry["title"]):
            entry["title"] = name

        # Track Digicape's own raw name for this group so we can tell a
        # competitor's fully-specced listing apart from Digicape's generic
        # "from" line (see has_config_markers above). Safe to read in the
        # same pass as it's written: digicape.json is loaded before every
        # competitor file in main(), so a group's baseline_name is already
        # set by the time any competitor row for that same key is reached.
        if retailer == BASELINE_RETAILER:
            entry["baseline_name"] = name

        existing = entry["prices"].get(retailer)
        if existing is None or price < existing["price"]:
            cell = {
                "price": price,
                "price_text": row.get("price_text", ""),
                "url": row.get("url", ""),
                "stale": False,
            }
            if row.get("note"):
                cell["note"] = row["note"]
            elif (
                retailer != BASELINE_RETAILER
                and entry["baseline_name"]
                and has_config_markers(name)
                and not has_config_markers(entry["baseline_name"])
            ):
                cell["note"] = (
                    "Listed by this retailer as a specific configuration — "
                    "Digicape's price is the base 'from' price for this "
                    "model line, not necessarily this exact spec."
                )
            entry["prices"][retailer] = cell

    # Stale carry-forward: for every product that made it into `grouped`,
    # backfill any retailer that has no fresh price this run from the
    # previous output, if it had one. This runs before the has-baseline
    # filter below, so a stale Digicape carry-forward is visible for the
    # check but — matching the reference app — does NOT count as "current".
    stale_recovered = 0
    for group_key, entry in grouped.items():
        old_prices = previous_index.get(group_key)
        if not old_prices:
            continue
        for retailer, old_cell in old_prices.items():
            if retailer in entry["prices"]:
                continue  # fresh data this run wins, no carry-forward needed
            if old_cell.get("price") is None:
                continue
            carried = dict(old_cell)
            carried["stale"] = True
            entry["prices"][retailer] = carried
            stale_recovered += 1

    items = []
    dropped_no_baseline = 0
    for (category, _key), entry in grouped.items():
        baseline_cell = entry["prices"].get(BASELINE_RETAILER)
        has_fresh_baseline = bool(baseline_cell) and not baseline_cell.get("stale")
        if not has_fresh_baseline:
            dropped_no_baseline += 1
            continue
        items.append({
            "category": CATEGORY_LABELS.get(category, category),
            "title": entry["title"],
            "prices": entry["prices"],
        })

    family_level_row_count = len(items)
    mac_model_lookup = load_mac_configs()
    precise_items = build_precise_mac_items(raw, mac_model_lookup)
    items.extend(precise_items)
    if precise_items:
        print(f"[combine] added {len(precise_items)} precise Mac SKU comparison row(s) "
              f"on top of the {family_level_row_count} family-level rows")

    items.sort(key=lambda x: (x["category"], x["title"]))

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "baseline_retailer": BASELINE_RETAILER,
        "retailer_labels": RETAILER_LABELS,
        "items": items,
    }

    out_path = DATA_DIR / "prices.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    matched_multi = sum(1 for i in items if len(i["prices"]) > 1)
    print(f"[combine] wrote {len(items)} comparison rows to {out_path} "
          f"({matched_multi} matched across 2+ retailers, "
          f"{stale_recovered} stale prices carried forward, "
          f"{dropped_no_baseline} products dropped for no fresh Digicape price this run, "
          f"{skipped_no_price} input rows skipped for missing name/price)")


if __name__ == "__main__":
    main()
