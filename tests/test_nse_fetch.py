import json, os, tempfile, unittest
from datetime import date
from unittest import mock
from tests import _path  # noqa: F401
import pandas as pd
from ptracker.indexes import nse_fetch
from ptracker.indexes.files import IndexStore
from ptracker.indexes.spec import parse_benchmark


class FakeResp:
    def __init__(self, rows): self._rows = rows
    def raise_for_status(self): pass
    def json(self): return {"d": json.dumps(self._rows)}


def rows(*triples):
    return [{"RequestNumber": "x", "Index Name": "n", "Date": d, "TotalReturnsIndex": str(v), "NTR_Value": str(v - 1)}
            for d, v in triples]


class FetchTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch("time.sleep"); p.start(); self.addCleanup(p.stop)

    def test_request_format_matches_the_documented_wire_spec(self):
        calls = []
        def post(url, headers, data, timeout):
            calls.append((url, headers, json.loads(data)))
            return FakeResp(rows(("05 Sep 2024", 100.0), ("04 Sep 2024", 99.0)))
        df = nse_fetch.NseFetcher(post=post).fetch(parse_benchmark("Nifty 50 TRI"), date(2024, 9, 1), date(2024, 9, 6))
        url, headers, body = calls[0]
        self.assertTrue(url.endswith("getTotalReturnIndexString"))
        self.assertTrue(headers["User-Agent"].startswith("Mozilla/5.0"))
        self.assertEqual(headers["X-Requested-With"], "XMLHttpRequest")
        self.assertIsInstance(body["cinfo"], str)                        # a STRING with single quotes, not nested JSON
        self.assertIn("'name':'NIFTY 50'", body["cinfo"])
        self.assertIn("'startDate':'01-Sep-2024'", body["cinfo"])
        self.assertEqual(list(df["Date"].dt.date), [date(2024, 9, 4), date(2024, 9, 5)])   # ascending

    def test_alternative_spelling_is_tried_when_first_is_empty(self):
        seen = []
        def post(url, headers, data, timeout):
            name = json.loads(data)["cinfo"].split("'name':'")[1].split("'")[0]
            seen.append(name)
            return FakeResp([] if " 100 " in name else rows(("05 Sep 2024", 1.0)))
        df = nse_fetch.NseFetcher(post=post).fetch(parse_benchmark("Nifty 100 Low Volatility 30 TRI"),
                                                    date(2024, 9, 1), date(2024, 9, 6))
        self.assertEqual(seen, ["NIFTY 100 LOW VOLATILITY 30", "NIFTY100 LOW VOLATILITY 30"])
        self.assertEqual(df["Index Name"].iloc[0], "NIFTY100 LOW VOLATILITY 30")

    def test_unknown_index_raises_helpful_error(self):
        f = nse_fetch.NseFetcher(post=lambda *a: FakeResp([]))
        with self.assertRaises(nse_fetch.IndexNotFound):
            f.fetch(parse_benchmark("Nifty Nonexistent TRI"), date(2024, 9, 1), date(2024, 9, 6))

    def test_long_ranges_are_chunked_and_failures_retried_then_raised(self):
        n = []
        def post(url, headers, data, timeout):
            n.append(1); return FakeResp(rows(("05 Sep 2024", 1.0)))
        nse_fetch.NseFetcher(post=post, chunk_days=365).fetch(parse_benchmark("Nifty 50 TRI"), date(2024, 1, 1), date(2026, 1, 1))
        self.assertEqual(len(n), 3)
        def boom(*a): raise ConnectionError("blocked")
        with self.assertRaises(RuntimeError):
            nse_fetch.NseFetcher(post=boom, max_retries=2, log=lambda *_: None).fetch(
                parse_benchmark("Nifty 50 TRI"), date(2024, 9, 1), date(2024, 9, 6))

    def test_non_nse_or_non_tri_is_refused(self):
        f = nse_fetch.NseFetcher(post=lambda *a: FakeResp([]))
        for name in ("S&P BSE 500 TRI", "Nifty 50"):
            with self.assertRaises(ValueError):
                f.fetch(parse_benchmark(name), date(2024, 9, 1), date(2024, 9, 6))

    def test_fetch_missing_writes_a_file_the_loader_understands(self):
        plan = pd.DataFrame([{"benchmark": "NIFTY 50 TRI", "source": "NSE", "status": "missing", "have_from": None,
                              "have_to": None, "need_from": date(2024, 9, 4), "need_to": date(2024, 9, 5),
                              "used_by": "x", "fetchable": True, "action": ""}])
        with tempfile.TemporaryDirectory() as d:
            f = nse_fetch.NseFetcher(post=lambda *a: FakeResp(rows(("05 Sep 2024", 100.0), ("04 Sep 2024", 99.0))))
            rep = nse_fetch.fetch_missing(plan, d, fetcher=f, today=date(2026, 1, 1))
            self.assertIn("saved 2 rows", rep.iloc[0]["result"])
            self.assertTrue(IndexStore(d).has(parse_benchmark("Nifty 50 TRI")))

    def test_dry_run_makes_no_requests(self):
        plan = pd.DataFrame([{"benchmark": "NIFTY 50 TRI", "source": "NSE", "status": "missing", "have_from": None,
                              "have_to": None, "need_from": date(2024, 9, 4), "need_to": date(2024, 9, 5),
                              "used_by": "x", "fetchable": True, "action": ""}])
        def never(*a): raise AssertionError("network used in dry run")
        rep = nse_fetch.fetch_missing(plan, "/nonexistent", fetcher=nse_fetch.NseFetcher(post=never), dry_run=True)
        self.assertIn("dry run", rep.iloc[0]["result"])


if __name__ == "__main__":
    unittest.main()
