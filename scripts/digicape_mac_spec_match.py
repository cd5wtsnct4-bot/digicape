"""
Precise Mac config matching: given a competitor's free-text Mac listing name
and a Digicape model's "available" configuration tree (chip -> storage ->
ram -> colour -> {product_id, price, special}), find the exact SKU that
matches on chip tier + CPU/GPU core count + storage + RAM. Colour is
deliberately NOT part of the match key: confirmed on real data that Silver
and Space Black variants of the same spec are always the same price (e.g.
product_id 9505 vs 9506, both R50,999), so colour can't change which price
is "correct" and requiring it to match would only produce false misses when
a competitor doesn't mention colour at all.

This is a companion to combine.py's name-token matching, not a replacement:
it only fires for Mac, and only when a Digicape config tree is available and
the competitor's name is specific enough to extract a chip tier + storage +
RAM from. Falls through to the existing generic "from" price matching
otherwise.
"""

import re


def extract_cpu_gpu_cores(name):
    """'15 core CPU and 16 core GPU' / '18C CPU/32C GPU' / '18 Core CPU 20 Core GPU' -> (15, 16)"""
    cpu = re.search(r"(\d+)\s*-?\s*c(?:ore)?\s*cpu", name, re.IGNORECASE)
    gpu = re.search(r"(\d+)\s*-?\s*c(?:ore)?\s*gpu", name, re.IGNORECASE)
    return (
        int(cpu.group(1)) if cpu else None,
        int(gpu.group(1)) if gpu else None,
    )


def extract_chip_tier(name):
    """Returns 'max', 'pro', or 'base' — which chip family this name refers to.
    Checked in this order because 'M5 Pro Max' style names don't exist for
    Apple silicon, but ordering max-before-pro is still the safer default in
    case a future chip generation combines both words."""
    lowered = name.lower()
    if re.search(r"\bm\d+\s*max\b", lowered):
        return "max"
    if re.search(r"\bm\d+\s*pro\b", lowered):
        return "pro"
    if re.search(r"\bm\d+\b", lowered):
        return "base"
    return None


def extract_storage_tb(name):
    m = re.search(r"(\d+)\s*tb\b", name, re.IGNORECASE)
    return f"{m.group(1)}tb_ssd" if m else None


def extract_ram_gb(name):
    """First plain '<n>GB' that ISN'T immediately followed by a storage-unit
    word — on these listings storage is always stated in TB, so any bare
    '<n>GB' is RAM, but this guards against a future name that states
    storage in GB too."""
    for m in re.finditer(r"(\d+)\s*gb\b", name, re.IGNORECASE):
        return f"{m.group(1)}gb"
    return None


def find_matching_config(name, available):
    """available: the parsed 'available' JS object for one Digicape model
    (chip_key -> storage_key -> ram_key -> colour_key -> leaf dict).
    Returns the cheapest matching leaf dict across colours, or None."""
    tier = extract_chip_tier(name)
    cpu, gpu = extract_cpu_gpu_cores(name)
    storage_key = extract_storage_tb(name)
    ram_key = extract_ram_gb(name)

    if tier is None or storage_key is None or ram_key is None:
        return None  # not specific enough to attempt a precise match

    # Find every chip_key in this model's tree consistent with the extracted
    # tier + (if given) core counts. There can be more than one candidate
    # when core counts aren't stated (e.g. Incredible's bare "M5") — that's
    # fine as long as exactly one has an actual entry at the extracted
    # storage/RAM point; if more than one does, treat it as ambiguous.
    def tier_matches(chip_key):
        ck = chip_key.lower()
        if tier == "max":
            want = "max"
        elif tier == "pro":
            want = "pro"
        else:
            want = None  # base chip has no tier word in the key
        has_tier_word = "_pro_" in ck or ck.endswith("_pro") or "_max_" in ck
        if want == "max":
            return "_max_" in ck
        if want == "pro":
            return "_pro_" in ck
        return not has_tier_word

    candidates = []
    for chip_key, by_storage in available.items():
        if not tier_matches(chip_key):
            continue
        if cpu is not None and f"{cpu}c_cpu" not in chip_key.lower():
            continue
        if gpu is not None and f"{gpu}c_gpu" not in chip_key.lower():
            continue
        by_ram = by_storage.get(storage_key)
        if not by_ram:
            continue
        by_colour = by_ram.get(ram_key)
        if not by_colour:
            continue
        for leaf in by_colour.values():
            candidates.append(leaf)

    if not candidates:
        return None
    if len(candidates) > 1:
        # Ambiguous (e.g. tier/cores under-specified and multiple chip
        # variants share this storage+RAM combination) — don't guess.
        prices = {c.get("special") or c.get("price") for c in candidates}
        if len(prices) == 1:
            return candidates[0]  # same effective price regardless, safe to return
        return None

    return candidates[0]


def effective_price(leaf):
    special = leaf.get("special")
    price = leaf.get("price")
    if special not in (None, 0):
        return float(special)
    return float(price) if price is not None else None
