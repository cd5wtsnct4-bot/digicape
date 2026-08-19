#!/usr/bin/env python3
"""
Merge data/digicape.json, data/istore.json, and data/takealot.json (whichever
of the three exist and are non-empty) into a single data/prices.json that the
comparison dashboard (docs/index.html) fetches at load time.

MATCHING IS HEURISTIC, NOT EXACT. Retailers name the same product
differently — "MacBook Pro 14-inch M5" vs "14-inch MacBook Pro M5" vs
"MacBook Pro 14″ (M5)" — and some listings are a specific SKU (a storage
size + colour, e.g. "iPhone 17 Pro 256GB - Silver") rather than the
model's base "from" price. This script normalizes names into an
order-independent token key (lowercased, inch-marks unified, storage sizes
and colour words stripped, tokens sorted) so reasonably-similar names
collapse into the same comparison row. It will occasionally either merge
two things that are subtly different configs, or fail to merge two names
that are further apart than the normalizer expects. Spot-check
data/prices.json after a run, especially for iPhone/Watch, where
promotional SKU names are the least consistent across retailers.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

RETAILER_LABELS = {
    "digicape": "Digicape",
    "istore": "iStore",
    "takealot": "Takealot",
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
    "deep", "space", "ultramarine", "teal", "rose",
    # generic filler that varies between retailers without changing the product
    "chip", "chipset", "processor", "with", "the", "and", "for", "gen",
    "generation", "wifi", "cellular",
}


def normalize_key(name):
    """Order-independent, loosely-normalized token key for fuzzy matching
    the same product across retailers' differently-worded listings."""
    s = name.lower().replace("’", "'")
    s = re.sub(r'(\d+)\s*[\"″]', r'\1in', s)       # 14"  / 14″ -> 14in
    s = re.sub(r'(\d+)\s*-?\s*inch', r'\1in', s)         # 14-inch / 14 inch -> 14in
    s = re.sub(r'(\d+)\s*gb\b', r'\1gb', s)              # normalize spacing on "256 GB"
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [t for t in s.split() if t not in STRIP_WORDS and t != "apple"]
    # drop a bare storage number only when a unit token (gb/tb) isn't also
    # present anymore (it was stripped above) — keeps "14in" but drops "256"
    tokens = [t for t in tokens if not re.fullmatch(r"\d{2,4}", t) or t.endswith("in")]
    return " ".join(sorted(set(tokens)))


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


def main():
    raw = []
    raw += load_items("digicape.json", "digicape")
    raw += load_items("istore.json", "istore")
    raw += load_items("takealot.json", "takealot")

    if not raw:
        print("[combine] no input data found in data/ — nothing to write. "
              "Run at least one scraper script first.", file=sys.stderr)
        sys.exit(1)

    grouped = {}  # (category, key) -> {"title": str, "prices": {retailer: {...}}}
    skipped_no_price = 0

    for row in raw:
        price = row.get("price")
        name = (row.get("name") or "").strip()
        retailer = row.get("retailer", "unknown")
        category = row.get("category", "unknown")
        # roll any digicape accessories:<slug> sub-category into "accessories"
        if category.startswith("accessories"):
            category = "accessories"

        if price is None or not name:
            skipped_no_price += 1
            continue

        key = normalize_key(name)
        if not key:
            skipped_no_price += 1
            continue

        group_key = (category, key)
        if group_key not in grouped:
            grouped[group_key] = {"title": name, "category": category, "prices": {}}

        entry = grouped[group_key]
        # prefer the shortest name seen so far as the display title (tends to
        # be the "from"/base listing rather than a specific colour+size SKU)
        if len(name) < len(entry["title"]):
            entry["title"] = name

        existing = entry["prices"].get(retailer)
        if existing is None or price < existing["price"]:
            entry["prices"][retailer] = {
                "price": price,
                "price_text": row.get("price_text", ""),
                "url": row.get("url", ""),
            }

    items = []
    for (category, _key), entry in grouped.items():
        items.append({
            "category": CATEGORY_LABELS.get(category, category),
            "title": entry["title"],
            "prices": entry["prices"],
        })

    items.sort(key=lambda x: (x["category"], x["title"]))

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "retailer_labels": RETAILER_LABELS,
        "items": items,
    }

    out_path = DATA_DIR / "prices.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    matched_multi = sum(1 for i in items if len(i["prices"]) > 1)
    print(f"[combine] wrote {len(items)} comparison rows to {out_path} "
          f"({matched_multi} matched across 2+ retailers, "
          f"{skipped_no_price} input rows skipped for missing name/price)")


if __name__ == "__main__":
    main()
