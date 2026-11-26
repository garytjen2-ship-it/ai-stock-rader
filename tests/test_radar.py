import io
import os
import sys
import unittest
from collections import UserDict
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import requests

import radar


NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)


class SimulatedYahooRateLimit(Exception):
    pass


def yahoo_modules(ticker):
    return {
        "yfinance": SimpleNamespace(Ticker=Mock(return_value=ticker)),
        "yfinance.exceptions": SimpleNamespace(YFRateLimitError=SimulatedYahooRateLimit),
    }


def quote(source=radar.YAHOO, currency="EUR", **overrides):
    return {
        "source": source, "price": 100.0, "price_at": NOW,
        "currency": currency, "change_pct": 0, **overrides,
    }


def consensus(source=radar.YAHOO, currency="EUR", **overrides):
    return {
        "source": source, "target": 140.0, "target_currency": currency,
        "analysts": 20, "average": 90.0, "average_currency": currency,
        "growth": 25.0, "growth_basis": "Testperiode", **overrides,
    }


class MergeTests(unittest.TestCase):
    def test_combine_finnhub_quote_and_yahoo_target(self):
        row = radar.merge("NVDA", [
            quote(radar.FINNHUB, "USD"),
            consensus(currency="USD"),
        ], NOW)
        self.assertEqual(row["price_source"], radar.FINNHUB)
        self.assertEqual(row["target_source"], radar.YAHOO)
        self.assertEqual(row["target_age"], "Datum onbekend")
        self.assertEqual(row["score"], 100)
        self.assertAlmostEqual(row["upside"], 40)
        self.assertEqual(row["change_pct"], 0)

    def test_international_tickers_receive_scores_in_trading_currency(self):
        for symbol, currency in radar.CURRENCIES.items():
            with self.subTest(symbol=symbol):
                row = radar.merge(symbol, [
                    {**quote(currency=currency), **consensus(currency=currency)},
                ], NOW)
                self.assertEqual(row["currency"], currency)
                self.assertEqual(row["score"], 100)

    def test_quote_fallback_prefers_valid_newer_yahoo_data(self):
        row = radar.merge("NVDA", [
            quote(radar.FINNHUB, "USD", price_at=NOW - timedelta(days=6)),
            quote(currency="USD", price=105),
        ], NOW)
        self.assertEqual(row["price_source"], radar.YAHOO)
        self.assertEqual(row["price"], 105)

    def test_currency_mismatches_exclude_quotes_targets_and_averages(self):
        row = radar.merge("SHOP.TO", [quote(currency="USD"), consensus(currency="USD")], NOW)
        self.assertIsNone(row["price"])
        self.assertIsNone(row["target"])
        self.assertIsNone(row["score"])
        row = radar.merge("ASML.AS", [
            {**quote(), **consensus(average_currency="USD")},
        ], NOW)
        self.assertIsNone(row["average"])
        self.assertIsNotNone(row["upside"])
        self.assertIsNone(row["score"])
        row = radar.merge("TEST.L", [quote(currency="GBp"), consensus(currency="GBP")], NOW)
        self.assertIsNone(row["target"])

    def test_stale_future_and_missing_quote_times_exclude_scores(self):
        for date in (NOW - timedelta(days=6), NOW + timedelta(hours=1), None):
            with self.subTest(date=date):
                row = radar.merge("ASML.AS", [{**quote(price_at=date), **consensus()}], NOW)
                self.assertIsNone(row["upside"])
                self.assertIsNone(row["score"])

    def test_stale_target_yields_to_undated_fallback(self):
        old = consensus(radar.FINNHUB, target_at=NOW - timedelta(days=91))
        row = radar.merge("ASML.AS", [quote(), old], NOW)
        self.assertIsNotNone(row["target"])
        self.assertIsNone(row["upside"])
        row = radar.merge("ASML.AS", [quote(), old, consensus(target=150)], NOW)
        self.assertEqual(row["target_source"], radar.YAHOO)
        self.assertAlmostEqual(row["upside"], 50)

    def test_fresh_dated_target_precedes_undated_target(self):
        row = radar.merge("NVDA", [
            quote(currency="USD"),
            consensus(radar.FINNHUB, "USD", target_at=NOW),
            consensus(currency="USD", target=150),
        ], NOW)
        self.assertEqual(row["target_source"], radar.FINNHUB)

    def test_missing_data_stays_empty(self):
        row = radar.merge("7203.T", [], NOW)
        self.assertEqual(row["currency"], "JPY")
        for key in ("price", "target", "upside", "score", "analysts", "revenue_growth", "price_source"):
            self.assertIsNone(row[key])
        row = radar.merge("ASML.AS", [{**quote(), **consensus(analysts=2, growth=None)}], NOW)
        self.assertIsNone(row["score"])


class ProviderTests(unittest.TestCase):
    def test_all_default_tickers_preserved_without_key(self):
        expected = (
            "NVDA AMZN MSFT GOOGL META ASML.AS ADYEN.AS SAP.DE 7203.T BABA "
            "RELIANCE.NS SHOP.TO BHP.AX NU SOFI PLTR"
        ).split()
        with patch("radar.yahoo_data", return_value={}) as yahoo, patch("radar.finnhub_data") as fh:
            rows = radar.scan(api_key="")
        self.assertEqual([row["symbol"] for row in rows], expected)
        self.assertEqual(yahoo.call_count, 16)
        fh.assert_not_called()

    def test_scan_uses_both_sources_for_known_us_listing(self):
        with patch("radar.yahoo_data", return_value={}) as yahoo, patch(
            "radar.finnhub_data", return_value={}
        ) as fh:
            radar.scan(["NVDA", "ASML.AS"], api_key="demo-key")
        self.assertEqual(yahoo.call_count, 2)
        self.assertEqual(fh.call_count, 1)
        self.assertEqual(fh.call_args.args[:2], ("NVDA", "demo-key"))

    def test_yahoo_chart_and_consensus_use_trading_currency(self):
        ticker = Mock()
        ticker.history.return_value = None
        ticker.get_history_metadata.return_value = UserDict({
            "regularMarketPrice": 100, "regularMarketTime": NOW.timestamp(), "currency": "CAD"
        })
        ticker.get_info.return_value = {
            "symbol": "SHOP.TO", "currency": "CAD", "financialCurrency": "USD",
            "targetMeanPrice": 135, "numberOfAnalystOpinions": 8,
            "revenueGrowth": 0.18, "fiftyDayAverage": 95,
        }
        with patch.dict(sys.modules, yahoo_modules(ticker)):
            data = radar.yahoo_data("SHOP.TO")
        self.assertEqual(data["currency"], "CAD")
        self.assertEqual(data["target_currency"], "CAD")
        self.assertEqual(data["price_at"], NOW)
        self.assertEqual(data["growth"], 18)
        ticker.history.assert_called_once_with(
            period="1mo", interval="1d", auto_adjust=False, timeout=8
        )

    def test_yahoo_preserves_partial_ticker_info_with_default_error_handling(self):
        from pandas import DataFrame

        ticker = Mock()
        ticker.history.return_value = DataFrame()
        ticker.get_history_metadata.return_value = {}
        modules = yahoo_modules(ticker)
        debug = SimpleNamespace(hide_exceptions=True)
        modules["yfinance"].config = SimpleNamespace(debug=debug)

        def get_info():
            # An HTTP failure in Yahoo's second endpoint leaves the first usable.
            if not debug.hide_exceptions:
                raise requests.HTTPError("Quote endpoint unavailable")
            return {"currency": "USD", "targetMeanPrice": 140, "numberOfAnalystOpinions": 12}

        ticker.get_info.side_effect = get_info
        with patch.dict(sys.modules, modules):
            data = radar.yahoo_data("NVDA", now=NOW)
        self.assertEqual(data["target"], 140)
        self.assertEqual(data["analysts"], 12)
        self.assertIn(("Yahoo historie", "Geen dagkoersen ontvangen"), data["diagnostics"])
        self.assertTrue(debug.hide_exceptions)

    def test_yahoo_info_quote_fallback_and_invalid_values(self):
        ticker = Mock()
        ticker.history.side_effect = RuntimeError("provider diagnostic")
        ticker.get_info.return_value = {
            "currency": "JPY", "regularMarketPrice": 3000,
            "regularMarketTime": NOW.timestamp(), "targetMeanPrice": float("nan"),
            "fiftyDayAverage": 0, "revenueGrowth": False,
        }
        with patch.dict(sys.modules, yahoo_modules(ticker)):
            data = radar.yahoo_data("7203.T")
        self.assertEqual(data["price"], 3000)
        self.assertEqual(data["currency"], "JPY")
        for field in ("target", "average", "growth"):
            self.assertIsNone(data[field])

    def test_yahoo_compares_complete_regular_market_quotes(self):
        scenarios = (
            ("verouderd", NOW - timedelta(days=6), "CAD", 100),
            ("valuta", NOW, "USD", 100),
            ("toekomst", NOW + timedelta(hours=1), "CAD", 100),
            ("ongeldig", NOW, "CAD", float("inf")),
            ("ouder", NOW - timedelta(hours=2), "CAD", 100),
        )
        for label, chart_time, chart_currency, chart_price in scenarios:
            with self.subTest(label=label):
                ticker = Mock()
                ticker.history.return_value = None
                ticker.get_history_metadata.return_value = {
                    "currency": chart_currency, "regularMarketPrice": chart_price,
                    "regularMarketTime": chart_time.timestamp(),
                }
                ticker.get_info.return_value = {
                    "currency": "CAD", "regularMarketPrice": 105,
                    "regularMarketTime": (NOW - timedelta(hours=1)).timestamp(),
                    "targetMeanPrice": 140, "fiftyDayAverage": 100,
                    "numberOfAnalystOpinions": 10, "revenueGrowth": 0.1,
                }
                with patch.dict(sys.modules, yahoo_modules(ticker)):
                    data = radar.yahoo_data("SHOP.TO", now=NOW)
                row = radar.merge("SHOP.TO", [data], NOW)
                self.assertEqual(row["price"], 105)
                self.assertEqual(row["currency"], "CAD")
                self.assertIsNotNone(row["score"])

    def test_yahoo_daily_bar_keeps_local_session_through_five_day_cutoff(self):
        tokyo = ZoneInfo("Asia/Tokyo")
        session = datetime(2026, 11, 19, tzinfo=tokyo)
        ticker = Mock()
        history = {"Close": {session - timedelta(days=1): 90, session: 100}}
        ticker.history.return_value = type(
            "History", (dict,), {"index": SimpleNamespace(tz=tokyo)}
        )(history)
        ticker.get_history_metadata.return_value = {
            "currency": "JPY", "regularMarketPrice": 85,
            "regularMarketTime": (session - timedelta(days=2)).timestamp(),
        }
        ticker.get_info.return_value = {
            "currency": "JPY", "targetMeanPrice": 140, "numberOfAnalystOpinions": 8,
            "revenueGrowth": 0.18, "fiftyDayAverage": 95,
        }
        with patch.dict(sys.modules, yahoo_modules(ticker)):
            data = radar.yahoo_data("7203.T", now=NOW)
        row = radar.merge("7203.T", [data], NOW)
        self.assertEqual(row["price"], 100)
        self.assertEqual(row["price_session"].isoformat(), "2026-11-19")
        self.assertIsNone(row["price_at"])
        self.assertEqual(row["price_age"], "Binnen grens")
        self.assertIsNotNone(row["score"])
        # Tokyo has reached the sixth day while UTC is still on the fifth.
        stale = radar.merge("7203.T", [data], NOW + timedelta(hours=3))
        self.assertEqual(stale["price_age"], "Verouderd")
        self.assertIsNone(stale["score"])

    def test_finnhub_skips_unusable_recommendations_before_ranking(self):
        counts = {"buy": 5, "hold": 1, "sell": 0, "strongBuy": 0, "strongSell": 0}
        usable = {"period": (NOW - timedelta(days=90)).isoformat(), **counts}
        rows = [
            {**counts, "period": "2026-11-20", "sell": None},
            usable,
            {"period": "2026-11-22", **dict.fromkeys(counts, 0)},
            {**counts, "period": "2026-11-23", "hold": 1.5},
            {**counts, "period": "2026-11-25"},
            {**counts, "period": (NOW - timedelta(days=91)).isoformat()},
        ]
        self.assertEqual(radar.recommendation_count(rows, NOW), 6)
        self.assertIsNone(radar.recommendation_count([row for row in rows if row != usable], NOW))

    def test_finnhub_token_header_only_and_errors_are_discarded(self):
        response = Mock(status_code=403)
        with patch("radar.time.sleep"), patch("radar.requests.get", return_value=response) as get:
            self.assertEqual(radar.fetch("quote", "NVDA", api_key="demo-key"), {})
        self.assertEqual(get.call_args.kwargs["headers"], {"X-Finnhub-Token": "demo-key"})
        self.assertEqual(get.call_args.kwargs["params"], {"symbol": "NVDA"})
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertNotIn("demo-key", get.call_args.args[0])

    def test_finnhub_429_pauses_requests_until_retry_after(self):
        responses = [
            Mock(status_code=429, headers={"Retry-After": "90"}),
            Mock(status_code=200, **{"json.return_value": {"c": 100}}),
        ]
        with (
            patch("radar._finnhub_retry_at", 0),
            patch("radar.time.sleep"),
            patch("radar.time.monotonic", return_value=100) as clock,
            patch("radar.requests.get", side_effect=responses) as get,
        ):
            self.assertEqual(radar.fetch("quote", "NVDA", api_key="demo-key"), {})
            clock.return_value = 189
            self.assertEqual(radar.fetch("quote", "MSFT", api_key="demo-key"), {})
            self.assertEqual(get.call_count, 1)
            clock.return_value = 190
            self.assertEqual(radar.fetch("quote", "MSFT", api_key="demo-key"), {"c": 100})

    def test_finnhub_accepts_http_date_and_defaults_for_invalid_retry_after(self):
        with patch("radar.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            self.assertEqual(radar.retry_after_seconds("Tue, 24 Nov 2026 12:02:00 GMT"), 120)
        self.assertEqual(radar.retry_after_seconds("invalid"), 60)
        self.assertEqual(radar.retry_after_seconds(None), 60)

    def test_yahoo_rate_limit_skips_quote_info_and_next_ticker(self):
        ticker = Mock()
        ticker.history.side_effect = SimulatedYahooRateLimit
        modules = yahoo_modules(ticker)
        with (
            patch("radar._yahoo_retry_at", 0),
            patch("radar.time.monotonic", return_value=100),
            patch.dict(sys.modules, modules),
        ):
            radar.yahoo_data("NVDA", now=NOW)
            radar.yahoo_data("MSFT", now=NOW)
        ticker.get_info.assert_not_called()
        modules["yfinance"].Ticker.assert_called_once_with("NVDA")

    def test_finnhub_malformed_target_date_cannot_generate_score(self):
        responses = {
            "stock/profile2": {"ticker": "NVDA", "currency": "USD"},
            "stock/price-target": {"targetMean": 140, "lastUpdated": "invalid"},
        }
        with patch("radar.fetch", side_effect=lambda endpoint, *args, **kwargs: responses.get(endpoint)):
            data = radar.finnhub_data("NVDA", "demo-key", NOW)
        row = radar.merge("NVDA", [quote(currency="USD"), data], NOW)
        self.assertEqual(row["target"], 140)
        self.assertEqual(row["target_age"], "Ongeldige datum")
        self.assertIsNone(row["upside"])
        self.assertIsNone(row["score"])

    def test_finnhub_parses_dated_target_and_latest_complete_recommendations(self):
        responses = {
            "quote": {"c": 110, "dp": 0, "t": NOW.timestamp()},
            "stock/profile2": {"ticker": "NVDA", "currency": "USD"},
            "stock/price-target": {"targetMean": 140, "lastUpdated": "2026-11-22 10:00:00"},
            "stock/metric": {"metric": {"50DayMovingAverage": 100, "revenueGrowthTTMYoy": 18}},
            "stock/recommendation": [
                None,
                {"period": "2026-10-01", "buy": 8, "sell": 0, "strongBuy": 2, "strongSell": 0, "hold": 0},
                {"period": "2026-11-01", "buy": 3, "sell": 0, "strongBuy": 2, "strongSell": 0, "hold": 1},
            ],
        }
        with patch("radar.fetch", side_effect=lambda endpoint, *args, **kwargs: responses[endpoint]):
            data = radar.finnhub_data("NVDA", "demo-key", NOW)
        self.assertEqual(data["analysts"], 6)
        self.assertEqual(data["price_at"], NOW)
        self.assertEqual(data["target_at"], NOW.replace(day=22, hour=10))
        self.assertEqual(data["target_currency"], "USD")


class AutomationTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "demo-token", "TELEGRAM_CHAT_ID": "123"})
        env.start()
        self.addCleanup(env.stop)

    def test_telegram_threshold_sources_and_delivery(self):
        row = radar.merge("ASML.AS", [{**quote(), **consensus()}], NOW)
        with patch("radar.requests.post") as post, redirect_stdout(io.StringIO()):
            post.return_value = Mock(status_code=200, **{"json.return_value": {"ok": True}})
            radar.notify([{**row, "score": 74}])
            post.assert_not_called()
            radar.notify([{**row, "score": 75}])
            post.assert_called_once()
            message = post.call_args.kwargs["json"]["text"]
        self.assertIn("Yahoo Finance", message)
        self.assertIn("datum onbekend", message)
        self.assertNotIn("demo-token", message)

    def test_telegram_network_error_does_not_reveal_token(self):
        row = radar.merge("ASML.AS", [{**quote(), **consensus()}], NOW)
        with patch("radar.requests.post", side_effect=requests.ConnectionError("demo-token")):
            with self.assertRaisesRegex(RuntimeError, "^Telegram verzending mislukt$") as error:
                radar.notify([row])
        self.assertTrue(error.exception.__suppress_context__)

    def test_cli_reports_empty_feed_as_failure(self):
        row = radar.merge("ASML.AS", [], NOW)
        with patch("radar.scan", return_value=[row]), patch("radar.notify") as notify:
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "Geen actuele koersen"):
                radar.main()
            notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
