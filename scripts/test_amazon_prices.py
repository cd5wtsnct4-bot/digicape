#!/usr/bin/env python3
"""
Regression test for amazon_prices.py's clean_price(), which had a real,
live bug found 2026-08-19: Amazon renders thousands separators with U+00A0
(non-breaking space) rather than a plain space, so every scraped Amazon
price came out truncated to just the leading digit group before the
separator (e.g. "R29\xa0999.00" -> 29.0 instead of 29999.0). Confirmed
directly against all 20 real prices captured that day; every single one
was wrong before this fix.

RUN:
    python3 scripts/test_amazon_prices.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from amazon_prices import clean_price  # noqa: E402


class TestCleanPrice(unittest.TestCase):
    def test_non_breaking_space_thousands_separator(self):
        # The exact real bug: Amazon's price text uses \xa0, not a plain
        # space, between the thousands and hundreds digit groups.
        self.assertEqual(clean_price("R29\xa0999.00"), 29999.0)
        self.assertEqual(clean_price("R5\xa0249.00"), 5249.0)

    def test_plain_space_thousands_separator_still_works(self):
        self.assertEqual(clean_price("R 29 999.00"), 29999.0)

    def test_no_thousands_separator(self):
        self.assertEqual(clean_price("R249.00"), 249.0)

    def test_comma_thousands_separator(self):
        self.assertEqual(clean_price("R29,999.00"), 29999.0)

    def test_none_and_empty_input(self):
        self.assertIsNone(clean_price(None))
        self.assertIsNone(clean_price(""))

    def test_no_digits_returns_none(self):
        self.assertIsNone(clean_price("Price not available"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
