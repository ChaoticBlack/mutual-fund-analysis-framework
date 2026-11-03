import unittest
from tests import _path  # noqa: F401
from ptracker.parsers.txn_statement import parse_lines, classify

SAMPLE = """
Transaction Statement
From 09/08/2024 to 26/10/2026
ASHA KUMAR SHARMA (PAN : ABCDE1234F)
Demo Equity Fund Reg (G) INF000A01234 [111/22]
09/08/2024 Opening Cost and Unit Balance 0.00 0.0000
28/08/2024 SIP 19,999.00 10.0000 1,999.9000 1,999.9000 1.00 0.00 0.00 20,000.00
28/09/2024 SIP 19,999.00 10.5000 1,904.6190 3,904.5190 1.00 0.00 0.00 20,000.00
28/10/2024 Redemption 5,000.00 11.0000 -454.5450 3,449.9740 0.00 0.00 0.00 5,000.00
NAV : 11.0000 as on 23/10/2026 Purchase Outstanding : 40,000.00 Market Value : 37,949.71
Grand Total : Purchase Outstanding : 40,000.00 Market Value : 37,949.71
""".strip().splitlines()


class TxnParserTests(unittest.TestCase):
    def setUp(self):
        self.p = parse_lines(SAMPLE)

    def test_header_and_privacy(self):
        self.assertEqual(self.p.pan_masked, "*****1234*")
        self.assertEqual(len(self.p.schemes), 1)
        self.assertEqual(self.p.schemes.iloc[0]["isin"], "INF000A01234")
        self.assertEqual(self.p.schemes.iloc[0]["plan"], "Regular")

    def test_rows_and_signs(self):
        t = self.p.transactions
        self.assertEqual(len(t), 3)
        self.assertEqual(list(t["category"]), ["investment", "investment", "redemption"])
        self.assertLess(t.iloc[2]["units"], 0)          # redemption reduces units
        self.assertLess(t.iloc[2]["total_amount"], 0)   # ...and is a cash INFLOW for the investor
        self.assertGreater(t.iloc[0]["total_amount"], 0)

    def test_unparsed_date_line_is_reported_not_dropped(self):
        lines = SAMPLE[:4] + ["29/08/2024 SIP weird layout here"] + SAMPLE[4:]
        p = parse_lines(lines)
        self.assertTrue(any("could not be parsed" in w for w in p.warnings))

    def test_classification(self):
        self.assertEqual(classify("SIP"), "investment")
        self.assertEqual(classify("Switch Out - to X"), "redemption")
        self.assertEqual(classify("Dividend Reinvest"), "dividend_reinvest")
        self.assertEqual(classify("Something new"), "other")


if __name__ == "__main__":
    unittest.main()