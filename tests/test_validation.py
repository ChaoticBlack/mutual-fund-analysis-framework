import unittest
from tests import _path  # noqa: F401
from ptracker.parsers.txn_statement import parse_lines
from ptracker.validation import validate_txn, summarize
from tests.test_txn_parser import SAMPLE


class ValidationTests(unittest.TestCase):
    def test_clean_sample_has_no_failures(self):
        # sample purchase outstanding is deliberately self-consistent only for balance/NAV checks
        p = parse_lines(SAMPLE)
        failed = [c for c in validate_txn(p) if not c.ok and c.severity == "error"]
        self.assertEqual([c.name for c in failed], [])

    def test_broken_unit_balance_is_caught(self):
        bad = [l.replace("3,904.5190", "9,999.9999") for l in SAMPLE]
        checks = validate_txn(parse_lines(bad))
        self.assertTrue(any((not c.ok) and "Unit balance" in c.name for c in checks))
        self.assertGreater(summarize(checks)[1], 0)

    def test_wrong_market_value_is_caught(self):
        bad = [l.replace("Market Value : 37,949.71", "Market Value : 99,999.00") for l in SAMPLE]
        checks = validate_txn(parse_lines(bad))
        self.assertTrue(any((not c.ok) and "market value" in c.name.lower() for c in checks))


if __name__ == "__main__":
    unittest.main()
