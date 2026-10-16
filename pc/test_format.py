"""Checks for the 4-digit price text the sign expects. No network."""

import unittest

from prices import average, format_display


class FormatDisplayTests(unittest.TestCase):
    def test_us_pump_price(self):
        self.assertEqual(format_display(4.331, "dollars_per_gallon"), "4.331")
        self.assertEqual(format_display(4.695, "dollars_per_gallon"), "4.695")
        self.assertEqual(format_display(6.259, "dollars_per_gallon"), "6.259")

    def test_price_at_ten_dollars_uses_two_decimals(self):
        self.assertEqual(format_display(10, "dollars_per_gallon"), "10.00")
        self.assertEqual(format_display(12.345, "dollars_per_gallon"), "12.35")

    def test_uk_pence(self):
        self.assertEqual(format_display(179.9, "pence_per_litre"), "179.9")
        self.assertEqual(format_display(99.94, "pence_per_litre"), "099.9")

    def test_canadian_cents(self):
        self.assertEqual(format_display(178.9, "cents_per_litre"), "178.9")
        self.assertEqual(format_display(181.6, "cents_per_litre"), "181.6")
        self.assertEqual(format_display(average([178.9, 179.9, 180.9]), "cents_per_litre"), "179.9")

    def test_average_of_three_stations(self):
        self.assertAlmostEqual(average([3.459, 3.499, 3.479]), 3.479, places=3)
        self.assertEqual(format_display(average([3.459, 3.499, 3.479]), "dollars_per_gallon"), "3.479")

    def test_rejects_a_price_the_display_cannot_hold(self):
        with self.assertRaises(ValueError):
            format_display(100, "dollars_per_gallon")
        with self.assertRaises(ValueError):
            format_display(1000, "pence_per_litre")


if __name__ == "__main__":
    unittest.main()
