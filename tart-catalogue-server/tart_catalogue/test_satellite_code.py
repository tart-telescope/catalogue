"""Tests for the optional satellite code key (issue #4)."""

import unittest

from tart_catalogue.satellite_code import satellite_code


class TestSatelliteCode(unittest.TestCase):
    def test_gps_prn_in_name(self):
        self.assertEqual(satellite_code("GPS BIIR-2  (PRN 13)"), "PRN 13")
        self.assertEqual(satellite_code("GPS BIII-5 (PRN 24)"), "PRN 24")
        self.assertEqual(satellite_code("NAVSTAR 43 (PRN 08)"), "PRN 8")

    def test_code_in_name(self):
        self.assertEqual(satellite_code("BEIDOU-3 M15 (C14)"), "C14")
        self.assertEqual(satellite_code("C14"), "C14")
        self.assertEqual(satellite_code("E11"), "E11")
        self.assertEqual(satellite_code("J03"), "J03")
        self.assertEqual(satellite_code("G32"), "G32")

    def test_galileo_gsat_names(self):
        # Official SV IDs from the GSC constellation information table
        self.assertEqual(satellite_code("GSAT0101"), "E11")
        self.assertEqual(satellite_code("GSAT0102"), "E12")
        self.assertEqual(satellite_code("GSAT0213"), "E04")
        self.assertEqual(satellite_code("GSAT0232"), "E16")

    def test_qzss_names(self):
        # PRN codes from the QZSS constellation information table
        self.assertEqual(satellite_code("QZS-2"), "PRN 194")
        self.assertEqual(satellite_code("QZS02"), "PRN 194")
        self.assertEqual(satellite_code("QZS2"), "PRN 194")
        self.assertEqual(satellite_code("QZS-1R"), "PRN 196")
        self.assertEqual(satellite_code("QZS06"), "PRN 200")

    def test_unknown_is_omitted(self):
        # No guaranteed match -> no code (issue #4)
        self.assertIsNone(satellite_code("BEIDOU-3 M15"))
        self.assertIsNone(satellite_code("INMARSAT 4-F2"))
        self.assertIsNone(satellite_code("GPS BIIR-2"))
        self.assertIsNone(satellite_code(""))
        self.assertIsNone(satellite_code("SUN"))

    def test_code_wins_over_table(self):
        # An explicit code in the name beats any table
        self.assertEqual(satellite_code("GSAT0213 (E04)"), "E04")


if __name__ == "__main__":
    unittest.main()
