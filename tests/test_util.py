import unittest
from tests import _path  # noqa: F401
from datetime import date
from ptracker.util import num, parse_date, mask_pan, norm_name, infer_plan_option


class UtilTests(unittest.TestCase):
    def test_num_indian_format(self):
        self.assertEqual(num("4,40,000.00"), 440000.0)
        self.assertEqual(num("(1,234.50)"), -1234.5)
        self.assertEqual(num("-0.83 %"), -0.83)
        self.assertIsNone(num("-"))

    def test_dates(self):
        self.assertEqual(parse_date("28/08/2024"), date(2024, 8, 28))
        self.assertEqual(parse_date("14 Aug 2024"), date(2024, 8, 14))
        self.assertIsNone(parse_date("nonsense"))

    def test_pan_is_masked(self):
        self.assertEqual(mask_pan("ABCDE1234F"), "*****1234*")
        self.assertNotIn("ABCDE", mask_pan("ABCDE1234F"))

    def test_name_normalisation_and_plan(self):
        self.assertEqual(norm_name("Invesco India Manufacturing Fund Reg (G)"),
                         "invescoindiamanufacturingfundregg")
        self.assertEqual(infer_plan_option("X Fund Reg (G)"), ("Regular", "Growth"))
        self.assertEqual(infer_plan_option("X Fund - Direct Plan - Growth"), ("Direct", "Growth"))


if __name__ == "__main__":
    unittest.main()
