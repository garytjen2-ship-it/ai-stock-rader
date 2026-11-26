import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests

import radar
from test_radar import NOW, consensus, quote


class QualityTests(unittest.TestCase):
    def test_quality_points_and_research_score_are_independent(self):
        row = radar.merge("ASML.AS", [{**quote(), **consensus(target_at=NOW)}], NOW)
        self.assertEqual(row["quality"], 100)
        self.assertEqual(row["reliability"], "Hoog")
        self.assertEqual(sum(row["quality_points"].values()), 100)

    def test_unknown_and_stale_target_dates_have_low_reliability(self):
        for target_at, status, has_score in (
            (None, "Datum onbekend", True),
            (NOW - timedelta(days=91), "Verouderd", False),
        ):
            with self.subTest(status=status):
                row = radar.merge("ASML.AS", [{**quote(), **consensus(target_at=target_at)}], NOW)
                self.assertEqual(row["target_at"], target_at)
                self.assertEqual(row["target_age"], status)
                self.assertEqual(row["score"] is not None, has_score)
                self.assertEqual(row["quality"], 59)
                self.assertEqual(row["reliability"], "Laag")

    def test_missing_analysts_and_recommendation_proxy(self):
        for coverage in ({}, {"recommendation_count": 12, "analysts": 12}):
            with self.subTest(coverage=coverage):
                row = radar.merge("ASML.AS", [
                    {**quote(), **consensus(target_at=NOW, analysts=None), **coverage},
                ], NOW)
                self.assertIsNone(row["analysts"])
                self.assertEqual(row["quality_points"]["Analistendekking"], 0)
                self.assertIn("Aantal analisten", row["status"])
                self.assertEqual(row["score"] is not None, bool(coverage))

    def test_conflicting_sources_penalize_and_newest_wins(self):
        older = NOW - timedelta(minutes=20)
        row = radar.merge("NVDA", [
            {**quote(radar.FINNHUB, "USD", price=90, price_at=older),
             **consensus(radar.FINNHUB, "USD", target=180, target_at=older)},
            {**quote(currency="USD"), **consensus(currency="USD", target=130, target_at=NOW)},
        ], NOW)
        self.assertEqual(row["price_source"], radar.YAHOO)
        self.assertEqual(row["target_source"], radar.YAHOO)
        self.assertEqual(row["quality"], 70)
        self.assertEqual(row["quality_points"]["Bronafwijkingen"], -30)
        self.assertIn("Koersdoelbronnen", row["status"])
        reason = next(reason for source, step, reason in row["diagnostics"]
                      if source == radar.FINNHUB and step == "Koers")
        self.assertIn(older.isoformat(), reason)
        self.assertIn(f"{radar.YAHOO} ({NOW.isoformat()})", reason)

    def test_quote_conflict_requires_difference_above_five_percent(self):
        for price, quality in ((95.0, 100), (94.99, 79)):
            with self.subTest(price=price):
                row = radar.merge("NVDA", [
                    quote(radar.FINNHUB, "USD", price=price),
                    {**quote(currency="USD"), **consensus(currency="USD", target_at=NOW)},
                ], NOW)
                self.assertEqual(row["quality"], quality)

    def test_decimal_quote_cutoff_is_independent_of_source_order(self):
        for price, quality in ((19.58, 100), (19.57, 100), (19.56, 79)):
            providers = [
                quote(radar.FINNHUB, "USD", price=price),
                {**quote(currency="USD", price=20.60), **consensus(currency="USD", target_at=NOW)},
            ]
            for order in (providers, providers[::-1]):
                with self.subTest(price=price, first=order[0]["source"]):
                    self.assertEqual(radar.merge("NVDA", order, NOW)["quality"], quality)

    def test_target_cutoff_is_independent_of_source_order(self):
        older = NOW - timedelta(minutes=20)
        for low, high, quality in ((80, 100, 100), (16.48, 20.60, 100),
                                   (16.49, 20.60, 100), (16.47, 20.60, 79)):
            providers = [
                consensus(radar.FINNHUB, "USD", target=low, target_at=older),
                {**quote(currency="USD"), **consensus(currency="USD", target=high, target_at=NOW)},
            ]
            for order in (providers, providers[::-1]):
                with self.subTest(low=low, high=high, first=order[0]["source"]):
                    self.assertEqual(radar.merge("NVDA", order, NOW)["quality"], quality)

    def test_conflict_comparison_skips_missing_and_invalid_values(self):
        for invalid in ({}, {"price": None, "target": None},
                        *({"price": value, "target": value}
                          for value in ("ongeldig", float("nan"), float("inf"), float("-inf"), True, 0, -1))):
            with self.subTest(invalid=invalid):
                broken = {"source": radar.FINNHUB, "currency": "USD", "price_at": NOW, **invalid}
                valid = {**quote(currency="USD"), **consensus(currency="USD", target_at=NOW)}
                for order in ([broken, valid], [valid, broken]):
                    self.assertEqual(radar.source_conflicts(order, order, NOW), [])
                    self.assertEqual(radar.merge("NVDA", order, NOW)["quality"], 100)

    def test_quotes_from_different_times_do_not_trigger_conflict(self):
        row = radar.merge("NVDA", [
            quote(radar.FINNHUB, "USD", price=80, price_at=NOW - timedelta(hours=2)),
            {**quote(currency="USD"), **consensus(currency="USD", target_at=NOW)},
        ], NOW)
        self.assertEqual(row["quality"], 100)

    def test_mismatched_currency_rejected_even_if_newer(self):
        row = radar.merge("NVDA", [
            quote(radar.FINNHUB, "EUR"),
            {**quote(currency="USD", price_at=NOW - timedelta(minutes=10)),
             **consensus(currency="USD", target_at=NOW)},
        ], NOW)
        self.assertEqual(row["price_source"], radar.YAHOO)
        self.assertEqual(row["quality"], 79)
        self.assertIn((radar.FINNHUB, "Koers", "Afwijkende handelsvaluta"), row["diagnostics"])

    def test_range_is_kept_with_its_consensus_and_invalid_range_rejected(self):
        for high, expected_high in ((180, 180), (110, None)):
            with self.subTest(high=high):
                row = radar.merge("ASML.AS", [
                    {**quote(), **consensus(target_at=NOW, target_low=95, target_high=high)},
                ], NOW)
                self.assertEqual(row["target_high"], expected_high)
                self.assertEqual(row["target_low"], 95 if expected_high else None)


class FinnhubDiagnosticsTests(unittest.TestCase):
    def test_missing_key_and_unsupported_mapping(self):
        with patch.dict("os.environ", {}, clear=True), patch("radar.yahoo_data", return_value={}),                 patch("radar.finnhub_data") as finnhub:
            domestic, foreign = radar.scan(["NVDA", "ASML.AS"])
        finnhub.assert_not_called()
        self.assertIn((radar.FINNHUB, "Configuratie", "Sleutel ontbreekt"), domestic["diagnostics"])
        self.assertIn((radar.FINNHUB, "Ticker-mapping", "Geen gevalideerde Finnhub-mapping"),
                      foreign["diagnostics"])

    def test_http_statuses_and_cooldown_are_reported_without_secret(self):
        for code, reason in ((401, "Authenticatie"), (403, "endpointrechten"),
                             (429, "Aanvraaglimiet"), (500, "Serverfout"), (503, "Serverfout")):
            with self.subTest(code=code), patch("radar._finnhub_retry_at", 0), patch("radar.time.sleep"),                     patch("radar.requests.get", return_value=Mock(
                        status_code=code, headers={}, text="TEST-SECRET"
                    )) as get:
                events = []
                radar.fetch("quote", "NVDA", api_key="TEST-SECRET", diagnostics=events)
                self.assertIn(f"HTTP {code}", events[0][1])
                self.assertIn(reason, events[0][1])
                if code == 429:
                    radar.fetch("stock/profile2", "NVDA", api_key="TEST-SECRET", diagnostics=events)
                    self.assertIn("rate-limitpauze", events[1][1])
                get.assert_called_once()
                self.assertNotIn("TEST-SECRET", repr(events))

    def test_payload_and_provider_exceptions_are_not_returned(self):
        cases = [
            Mock(status_code=200, **{"json.return_value": {"error": "TEST-SECRET"}}),
            Mock(status_code=200, **{"json.side_effect": ValueError("TEST-SECRET")}),
            requests.ConnectionError("TEST-SECRET"),
        ]
        for case in cases:
            with self.subTest(case_type=type(case)), patch("radar.time.sleep"),                     patch("radar.requests.get", side_effect=[case]):
                events = []
                self.assertEqual(radar.fetch("quote", "NVDA", api_key="TEST-SECRET", diagnostics=events), {})
                self.assertTrue(events)
                self.assertNotIn("TEST-SECRET", repr(events))

    def test_cli_reports_fallback_without_leaking_provider_response(self):
        with patch.dict("os.environ", {"FINNHUB_API_KEY": "TEST-SECRET"}, clear=True),                 patch("radar.requests.get", return_value=Mock(status_code=403, text="TEST-SECRET")),                 patch("radar.time.sleep"), patch("radar.yahoo_data", return_value=quote(currency="USD", price_at=datetime.now(timezone.utc))),                 redirect_stdout(io.StringIO()) as output, patch("radar.notify") as notify:
            rows = radar.scan(["NVDA"])
            with patch("radar.scan", return_value=rows):
                radar.main()
        self.assertEqual(rows[0]["price_source"], radar.YAHOO)
        self.assertIn("HTTP 403", output.getvalue())
        self.assertNotIn("TEST-SECRET", output.getvalue())
        notify.assert_called_once_with(rows)
