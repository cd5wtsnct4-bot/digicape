#!/usr/bin/env python3
"""
Regression tests for combine.py's normalize_key(), the function that decides
whether two retailers' listings are "the same product" for the comparison
dashboard.

WHY THIS EXISTS: two real, live bugs shipped in normalize_key() before
either was caught by a human — both found only by directly comparing its
output against real scraped names (see README.md's "Known limitations" for
the 2026-08-19 entry). Neither bug crashed anything or threw an error; they
silently produced a wrong-but-plausible-looking result (a matched row with
the wrong price, or a genuinely different product failing to match at all).
That's exactly the class of bug a unit test catches immediately and a
manual read-through does not. This file exists so a future change to
normalize_key() can't reintroduce either bug without a test failing.

No third-party dependencies (stdlib unittest only) — matches combine.py's
own zero-dependency design, so this runs anywhere Python 3 runs, no `pip
install` required.

RUN:
    python3 scripts/test_combine.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from combine import normalize_key  # noqa: E402


class TestMacBoilerplateStripping(unittest.TestCase):
    """Real bug, found 2026-08-19: Takealot's fully-specced Mac names
    ('15 core CPU', '16 core GPU', '2TB SSD') never matched Digicape's
    generic 'from' price line because of leftover core/cpu/gpu/ssd tokens."""

    def test_takealot_m5_pro_matches_digicape_generic_line(self):
        digicape = "MacBook Pro 14-inch (M5 chip)"
        takealot = 'Apple MacBook Pro 14" M5 Pro 15 core CPU and 16 core GPU, 24GB, 2TB SSD'
        self.assertEqual(normalize_key(digicape), normalize_key(takealot))

    def test_different_storage_ram_config_still_matches(self):
        # Two Takealot SKUs for the same chip tier, different RAM/storage —
        # these should collapse to the SAME key (family-level match is the
        # intended behaviour; combine.py's has_config_markers() flags the
        # accuracy caveat separately rather than blocking the match).
        a = 'Apple MacBook Pro 14" M5 Pro 15 core CPU and 16 core GPU, 24GB, 2TB SSD'
        b = 'Apple MacBook Pro 14" M5 Pro 15 core CPU and 16 core GPU, 24GB, 1TB SSD'
        self.assertEqual(normalize_key(a), normalize_key(b))

    def test_m5_max_does_not_collapse_into_base_m5_line(self):
        # A materially different (much more expensive) chip tier should NOT
        # silently match the base line just because most of its boilerplate
        # is stripped — "max" has no counterpart in Digicape's generic name.
        digicape = "MacBook Pro 14-inch (M5 chip)"
        takealot_max = 'Apple MacBook Pro 14" M5 Max Chip 18 core CPU & 32 core GPU, 36GB, 2TB SSD'
        self.assertNotEqual(normalize_key(digicape), normalize_key(takealot_max))


class TestModelNumberNotStripped(unittest.TestCase):
    """Real bug, found 2026-08-19: a rule meant to drop leftover storage
    digits ('drop any bare 2-4 digit token') couldn't tell a storage digit
    apart from a meaningful model/generation number, and was silently
    merging iPhone 15/16/17 (and separately, Apple Watch Series 10/11) into
    one comparison row. Confirmed live: the merged row was mislabeled
    'iPhone 17' but showed the iPhone 15's price, because the combiner
    always keeps whichever match has the lowest price."""

    def test_iphone_generations_stay_distinct(self):
        keys = {normalize_key(f"iPhone {n}") for n in (15, 16, 17)}
        self.assertEqual(len(keys), 3, f"iPhone 15/16/17 collapsed: {keys}")

    def test_watch_series_generations_stay_distinct(self):
        keys = {
            normalize_key("Apple Watch Series 10"),
            normalize_key("Apple Watch Series 11"),
        }
        self.assertEqual(len(keys), 2, f"Watch Series 10/11 collapsed: {keys}")

    def test_iphone_pro_max_variants_stay_distinct_from_base(self):
        base = normalize_key("iPhone 17")
        pro = normalize_key("iPhone 17 Pro")
        pro_max = normalize_key("iPhone 17 Pro Max")
        self.assertEqual(len({base, pro, pro_max}), 3)


class TestExistingBehaviourUnaffected(unittest.TestCase):
    """Sanity checks that the two fixes above didn't disturb the
    normalizer's existing, already-working behaviour."""

    def test_inch_marks_unify(self):
        self.assertEqual(
            normalize_key('iPad Pro 11"'),
            normalize_key("iPad Pro 11-inch"),
        )

    def test_colour_and_storage_suffix_ignored(self):
        self.assertEqual(
            normalize_key("iPhone 17 Pro"),
            normalize_key("iPhone 17 Pro 256GB - Silver"),
        )

    def test_apple_prefix_ignored(self):
        self.assertEqual(
            normalize_key("AirPods 4"),
            normalize_key("Apple AirPods 4"),
        )

    def test_spaced_and_joined_storage_both_stripped(self):
        self.assertEqual(
            normalize_key("MacBook Air 13-inch (M5) 24GB/1TB"),
            normalize_key("MacBook Air 13-inch (M5) 24 GB / 1 TB"),
        )

    def test_different_products_do_not_match(self):
        self.assertNotEqual(
            normalize_key("MacBook Pro 14-inch (M5 chip)"),
            normalize_key("MacBook Pro 16-inch (M5 chip)"),
        )
        self.assertNotEqual(
            normalize_key("iPad Air 11-inch (M4 chip)"),
            normalize_key("iPad Pro 11-inch (M4 chip)"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
