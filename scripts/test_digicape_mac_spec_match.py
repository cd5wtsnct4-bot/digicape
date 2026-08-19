#!/usr/bin/env python3
"""
Regression tests for digicape_mac_spec_match.py — the precise Mac SKU
matcher that upgrades a family-level Mac comparison into a genuine
same-spec comparison when a competitor's listing is specific enough and
Digicape actually sells that exact configuration.

The 14-leaf configuration tree below (SAMPLE_AVAILABLE) is a faithful
reconstruction of the REAL data pulled live from Digicape's own
"MacBook Pro 14-inch (M5 chip)" product page on 2026-08-19 (the `available`
JS object — see digicape_mac_configs.py's module docstring for how it's
extracted). The competitor listing names below are REAL rows this project
scraped from Takealot and Incredible Connection the same day — not made up
for this test. Using real names against a real price tree means these
tests catch a real regression, not just an internally-consistent one.

RUN:
    python3 scripts/test_digicape_mac_spec_match.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digicape_mac_spec_match import (  # noqa: E402
    effective_price,
    extract_chip_tier,
    extract_cpu_gpu_cores,
    extract_ram_gb,
    extract_storage_tb,
    find_matching_config,
)

# Real 14-configuration tree for "MacBook Pro 14-inch (M5 chip)", as
# extracted live from digicape.co.za on 2026-08-19.
SAMPLE_AVAILABLE = {
    "m5_pro_chip_15c_cpu/16c_gpu": {
        "2tb_ssd": {
            "24gb": {
                "silver": {"product_id": "9505", "price": 61099.0, "special": 50999.0},
                "space_black": {"product_id": "9506", "price": 61099.0, "special": 50999.0},
            }
        },
        "1tb_ssd": {
            "24gb": {
                "silver": {"product_id": "9503", "price": 50899.0, "special": None},
                "space_black": {"product_id": "9504", "price": 50899.0, "special": None},
            }
        },
    },
    "m5_chip_10c_cpu/10c_gpu": {
        "1tb_ssd": {
            "16gb": {
                "space_black": {"product_id": "8123", "price": 40699.0, "special": None},
                "silver": {"product_id": "8126", "price": 40699.0, "special": None},
            },
            "24gb": {
                "space_black": {"product_id": "8124", "price": 44699.0, "special": None},
                "silver": {"product_id": "8127", "price": 44699.0, "special": None},
            },
            "32gb": {
                "space_black": {"product_id": "9499", "price": 48899.0, "special": None},
                "silver": {"product_id": "9500", "price": 48899.0, "special": None},
            },
        }
    },
    "m5_pro_chip_18c_cpu/20c_gpu": {
        "2tb_ssd": {
            "24gb": {
                "silver": {"product_id": "9507", "price": 65199.0, "special": None},
                "space_black": {"product_id": "9508", "price": 65199.0, "special": None},
            }
        }
    },
    "m5_max_chip_18c_cpu/32c_gpu": {
        "2tb_ssd": {
            "36gb": {
                "silver": {"product_id": "9501", "price": 83699.0, "special": None},
                "space_black": {"product_id": "9502", "price": 83699.0, "special": None},
            }
        }
    },
}


class TestFieldExtraction(unittest.TestCase):
    def test_extract_cpu_gpu_cores_ampersand_style(self):
        name = "Apple MacBook Pro 14\" M5 Max Chip 18 core CPU & 32 core GPU, 36GB, 2TB SSD"
        self.assertEqual(extract_cpu_gpu_cores(name), (18, 32))

    def test_extract_cpu_gpu_cores_and_style(self):
        name = 'Apple MacBook Pro 14" M5 Pro 15 core CPU and 16 core GPU, 24GB, 2TB SSD'
        self.assertEqual(extract_cpu_gpu_cores(name), (15, 16))

    def test_extract_chip_tier_base_pro_max(self):
        self.assertEqual(extract_chip_tier("MacBook Pro 14-inch (M5 chip)"), "base")
        self.assertEqual(extract_chip_tier("Apple MacBook Pro 14\" M5 Pro 15 core CPU"), "pro")
        self.assertEqual(extract_chip_tier("Apple MacBook Pro 14\" M5 Max Chip 18 core CPU"), "max")

    def test_extract_storage_and_ram(self):
        name = 'Apple MacBook Pro 14" M5 Pro 15 core CPU and 16 core GPU, 24GB, 2TB SSD'
        self.assertEqual(extract_storage_tb(name), "2tb_ssd")
        self.assertEqual(extract_ram_gb(name), "24gb")


class TestPreciseMatching(unittest.TestCase):
    """Real Takealot/Incredible Connection listings, scraped 2026-08-19,
    matched against the real Digicape configuration tree above."""

    def test_takealot_15core_16core_1tb_matches_base_price(self):
        name = 'Apple MacBook Pro 14" M5 Pro 15 core CPU and 16 core GPU, 24GB, 1TB SSD'
        leaf = find_matching_config(name, SAMPLE_AVAILABLE)
        self.assertIsNotNone(leaf)
        self.assertEqual(effective_price(leaf), 50899.0)

    def test_takealot_15core_16core_2tb_matches_special_price(self):
        # Real bug-risk case: this SKU has an active "special" (discounted)
        # price — effective_price() must prefer it over the full price.
        name = 'Apple MacBook Pro 14" M5 Pro 15 core CPU and 16 core GPU, 24GB, 2TB SSD'
        leaf = find_matching_config(name, SAMPLE_AVAILABLE)
        self.assertIsNotNone(leaf)
        self.assertEqual(effective_price(leaf), 50999.0)

    def test_takealot_18core_20core_2tb_does_not_collide_with_15core_16core(self):
        # Real bug-risk case: this shares the same storage+RAM (24GB/2TB) as
        # the 15-core/16-core SKU above but is a different chip variant at a
        # different price (R65,199 vs R50,999) — core counts must be part of
        # the match, not just storage+RAM.
        name = 'Apple MacBook Pro 14" M5 Pro 18 core CPU and 20 core GPU, 24GB, 2TB SSD'
        leaf = find_matching_config(name, SAMPLE_AVAILABLE)
        self.assertIsNotNone(leaf)
        self.assertEqual(effective_price(leaf), 65199.0)

    def test_incredible_base_m5_16gb_1tb(self):
        name = "MacBook Pro 14-inch (M5) 16GB/1TB"
        leaf = find_matching_config(name, SAMPLE_AVAILABLE)
        self.assertIsNotNone(leaf)
        self.assertEqual(effective_price(leaf), 40699.0)

    def test_max_chip_36gb_2tb(self):
        name = 'Apple MacBook Pro 14" M5 Max Chip 18 core CPU & 32 core GPU, 36GB, 2TB SSD'
        leaf = find_matching_config(name, SAMPLE_AVAILABLE)
        self.assertIsNotNone(leaf)
        self.assertEqual(effective_price(leaf), 83699.0)

    def test_nonexistent_config_returns_none_not_a_guess(self):
        # M5 Pro at 1TB/24GB with 18-core/20-core CPU/GPU is NOT a real sold
        # configuration in this tree (only the 15c/16c variant sells at
        # 1TB) — must return None rather than guess the nearest match.
        name = 'Apple MacBook Pro 14" M5 Pro 18 core CPU and 20 core GPU, 24GB, 1TB SSD'
        self.assertIsNone(find_matching_config(name, SAMPLE_AVAILABLE))

    def test_max_chip_at_wrong_ram_returns_none(self):
        name = 'Apple MacBook Pro 14" M5 Max Chip 18 core CPU & 32 core GPU, 48GB, 2TB SSD'
        self.assertIsNone(find_matching_config(name, SAMPLE_AVAILABLE))

    def test_underspecified_name_returns_none(self):
        # No storage/RAM stated at all — not specific enough to attempt a
        # precise match; family-level matching in combine.py is still the
        # correct fallback for this case.
        self.assertIsNone(find_matching_config("MacBook Pro 14-inch (M5 chip)", SAMPLE_AVAILABLE))


class TestEffectivePrice(unittest.TestCase):
    def test_prefers_special_over_full_price(self):
        self.assertEqual(effective_price({"price": 61099.0, "special": 50999.0}), 50999.0)

    def test_falls_back_to_price_when_no_special(self):
        self.assertEqual(effective_price({"price": 40699.0, "special": None}), 40699.0)

    def test_special_of_zero_is_treated_as_no_discount(self):
        # A "special" of exactly 0 isn't a real R0 discount price — it's the
        # same "no active special" signal as None on this storefront.
        # Treating it as a real price would wrongly show every such SKU as
        # free.
        self.assertEqual(effective_price({"price": 40699.0, "special": 0}), 40699.0)


class TestCrossModelIsolation(unittest.TestCase):
    """find_matching_config() only knows the tree it's handed — it has no
    way to tell on its own whether that tree is the right model for a given
    name. That isolation has to come from the caller (combine.py's
    build_precise_mac_items(), which looks up a model's tree by the SAME
    family-level normalize_key() match already used elsewhere in the
    pipeline before ever calling find_matching_config). This test locks in
    that a name for a completely different, unrelated model correctly finds
    NO match against this (14" M5) tree, which is the case that matters in
    practice: a 13" Air name has no chip-tier/storage/RAM combination that
    happens to also exist in the 14" Pro's real sold configurations."""

    def test_unrelated_air_model_name_finds_nothing_in_pro_tree(self):
        name = "Apple MacBook Air 13-inch M5 Chip, 16GB, 256GB SSD"
        self.assertIsNone(find_matching_config(name, SAMPLE_AVAILABLE))


if __name__ == "__main__":
    unittest.main()
