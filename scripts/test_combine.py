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

    def test_takealot_16inch_pro_matches_despite_missing_inch_word(self):
        # Real bug, found 2026-08-19 (live, after Phase 2 testing): unlike
        # its 14" listings, Takealot's 16" MacBook Pro names have no
        # "-inch"/quote-mark unit at all — just a bare "MacBook Pro 16 M5
        # Pro ...". Confirmed directly: normalize_key() produced
        # "16 m5 macbook pro" for that name vs "16in m5 macbook pro" for
        # Digicape's "MacBook Pro 16-inch (M5 chip)", so the entire 16" line
        # never matched Takealot at all. Apple only ships MacBook Air/Pro in
        # 13/14/15/16-inch sizes, so a bare 13-16 directly after "MacBook
        # Air"/"MacBook Pro" is safe to treat as the screen size.
        digicape = "MacBook Pro 16-inch (M5 chip)"
        takealot = "Apple MacBook Pro 16 M5 Pro 18 Core CPU 20 Core GPU 24GB RAM 1TB SSD"
        self.assertEqual(normalize_key(digicape), normalize_key(takealot))

    def test_bare_macbook_size_fix_does_not_affect_unrelated_bare_numbers(self):
        # The fix above is scoped to "macbook air/pro <13-16>" specifically —
        # it must not start treating bare numbers elsewhere (iPhone/Watch
        # generations, storage digits) as sizes too.
        self.assertNotEqual(normalize_key("iPhone 16"), normalize_key("iPhone 15"))
        self.assertEqual(
            normalize_key("Apple Watch Series 10"),
            normalize_key("Apple Watch Series 10"),
        )
        self.assertNotEqual(
            normalize_key("Apple Watch Series 10"),
            normalize_key("Apple Watch Series 11"),
        )


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


class TestAmazonMarketingCopyStripping(unittest.TestCase):
    """Real bug, found 2026-08-19: Amazon's product titles are full
    marketing copy (dozens of spec/feature/marketing words) while
    Digicape's names are short and clean, so ALL 20 scraped Amazon rows
    failed to match anything under the old STRIP_WORDS. Confirmed live:
    fixing this took Amazon from 0/20 to 15/20 matching, with the 5
    remaining misses being either a real missing Digicape SKU (M4 Mac
    configs Digicape doesn't sell) or a deliberate trade-off (see
    test_anc_and_ethernet_words_not_stripped below)."""

    def test_amazon_macbook_air_title_matches_digicape(self):
        amazon = ("Apple MacBook Air 13-inch Laptop M5 chip (10-core CPU and "
                  "8-core GPU): Built for AI, 13.6-inch Liquid Retina "
                  "Display, 16GB Unified Memory, 512GB SSD, 12MP Center "
                  "Stage, Touch ID, Wi-Fi 7; Silver")
        self.assertEqual(normalize_key(amazon), normalize_key("MacBook Air 13-inch (M5 chip)"))

    def test_amazon_ipad_title_matches_digicape(self):
        amazon = ("Apple iPad Air 11-inch (M4): Liquid Retina Display, "
                   "128GB, 12MP Front/Back Camera, Wi-Fi 7 with Apple N1, "
                   "Touch ID, All-Day Battery Life — Blue")
        self.assertEqual(normalize_key(amazon), normalize_key("iPad Air 11-inch (M4 chip)"))

    def test_amazon_iphone_title_matches_digicape_despite_chip_and_marketing_copy(self):
        amazon = ("Apple iPhone 17 Pro Max 256GB: 6.9-inch Display with "
                   "ProMotion, A19 Pro Chip, Best Battery Life in Any "
                   "iPhone Ever, Pro Fusion Camera System, Center Stage "
                   "Front Camera; Silver")
        self.assertEqual(normalize_key(amazon), normalize_key("iPhone 17 Pro Max"))

    def test_amazon_iphone_pro_still_distinct_from_base_and_max(self):
        base = normalize_key("Apple iPhone 17 256GB: 6.3-inch Display with ProMotion, A19 Chip; Mist Blue")
        pro = normalize_key("Apple iPhone 17 Pro 256GB: 6.3-inch Display with ProMotion, A19 Pro Chip; Deep Blue")
        pro_max = normalize_key("Apple iPhone 17 Pro Max 256GB: 6.9-inch Display with ProMotion, A19 Pro Chip; Silver")
        self.assertEqual(len({base, pro, pro_max}), 3)

    def test_amazon_watch_title_matches_digicape(self):
        amazon = ("Apple Watch Series 11 GPS 42mm Smartwatch with Jet Black "
                   "Aluminium Case with Black Sport Band - M/L. Sleep "
                   "Score, Fitness Tracker, Health Monitoring, Always-On "
                   "Display, Water Resistant")
        self.assertEqual(normalize_key(amazon), normalize_key("Apple Watch Series 11 Sport Band"))

    def test_amazon_appletv_title_matches_digicape(self):
        amazon = "Apple 2022 Apple TV 4K Wi‑Fi + Ethernet with 128GB storage (3rd generation)"
        self.assertEqual(normalize_key(amazon), normalize_key("Apple TV 4K 128Gb Wifi+Ethernet (3rd Gen, 2022)"))

    def test_anc_and_ethernet_words_not_stripped(self):
        # Deliberate trade-off, confirmed live: stripping "active noise
        # cancellation" (needed for Amazon's AirPods Pro/Max titles to
        # match) or "ethernet" (needed for one Apple TV title to match)
        # would silently collapse two of Digicape's own real,
        # differently-priced SKUs into one row. A missed match is an
        # acceptable cost; a silently wrong price is not.
        self.assertNotEqual(
            normalize_key("AirPods 4"),
            normalize_key("AirPods 4 with Active Noise Cancellation"),
        )
        self.assertNotEqual(
            normalize_key("Apple TV 4K 128Gb Wifi+Ethernet (3rd Gen, 2022)"),
            normalize_key("Apple TV 4K 64Gb Wifi (3rd Gen,2022)"),
        )


class TestDecimalInchHandling(unittest.TestCase):
    """Real bug, found 2026-08-19: Amazon states the literal display
    diagonal as a decimal ("13.6-inch", "6.3-inch") instead of Apple's
    marketed whole number. For Mac/iPad, Digicape's own name uses the
    marketed whole number, so the decimal must floor to it. For iPhone/iPad
    mini, Digicape's name has no screen-size token at all, so the decimal
    must be dropped entirely rather than guessed at."""

    def test_mac_decimal_inch_floors_to_marketed_size(self):
        self.assertEqual(
            normalize_key("MacBook Air 13.6-inch (M5 chip)"),
            normalize_key("MacBook Air 13-inch (M5 chip)"),
        )
        self.assertEqual(
            normalize_key("MacBook Pro 14.2-inch (M5 chip)"),
            normalize_key("MacBook Pro 14-inch (M5 chip)"),
        )

    def test_iphone_decimal_inch_dropped_not_guessed(self):
        self.assertEqual(
            normalize_key("iPhone 17 6.3-inch Display"),
            normalize_key("iPhone 17"),
        )

    def test_ipad_mini_decimal_inch_dropped_not_guessed(self):
        self.assertEqual(
            normalize_key("iPad mini (A17 Pro) 8.3-inch Display"),
            normalize_key("iPad mini (A17 Pro)"),
        )


class TestIncredibleConnectionFixes(unittest.TestCase):
    """Real bugs, found 2026-08-19, that kept several Incredible Connection
    Mac SKUs from matching Digicape rows they should have."""

    def test_joined_spaceblack_matches_two_word_space_black(self):
        self.assertEqual(
            normalize_key("Apple MacBook Pro 14 M5 Pro 15 Core CPU 16 Core GPU 24GB 2TB SSD SpaceBlack"),
            normalize_key("Apple MacBook Pro 14\" M5 Pro 15 core CPU and 16 core GPU, 24GB, 2TB SSD Space Black"),
        )

    def test_non_breaking_hyphen_in_core_count_still_stripped(self):
        # Incredible Connection uses U+2011 (non-breaking hyphen) instead of
        # a plain "-" in "10‑core CPU" -- confirmed directly this left
        # a stray bare "10" token with only an ASCII "-?" in the regex.
        amazon_style = "Apple MacBook Pro 14\" M5 10‑core CPU 24GB 1TB SSD 10‑core GPU Silver"
        self.assertEqual(normalize_key(amazon_style), normalize_key("MacBook Pro 14-inch (M5 chip)"))

    def test_macbook_neo_bare_size_dropped_not_converted(self):
        # Unlike Air/Pro, Digicape's "MacBook Neo" name has no size in it,
        # so Takealot's bare "MacBook Neo 13" must drop the "13" entirely
        # rather than gain a "13in" token Digicape's key doesn't have.
        self.assertEqual(
            normalize_key("Apple MacBook Neo 13 A18 Pro 6 Core CPU 5 Core GPU 8GB RAM 512GB SSD"),
            normalize_key("MacBook Neo"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
