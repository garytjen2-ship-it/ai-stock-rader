import logging
import math
import os
import time
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, time as clock_time, timedelta, timezone
from decimal import Decimal, DecimalException, localcontext
from email.utils import parsedate_to_datetime
from threading import Lock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

VERSION = "0.4"
ALERT_THRESHOLD = 75
CURRENCIES = {
    "NVDA": "USD", "AMZN": "USD", "MSFT": "USD", "GOOGL": "USD",
    "META": "USD", "ASML.AS": "EUR", "ADYEN.AS": "EUR", "SAP.DE": "EUR",
    "7203.T": "JPY", "BABA": "USD", "RELIANCE.NS": "INR", "SHOP.TO": "CAD",
    "BHP.AX": "AUD", "NU": "USD", "SOFI": "USD", "PLTR": "USD",
}
SYMBOLS = list(CURRENCIES)
FINNHUB_SYMBOLS = {symbol: symbol for symbol in (
    "NVDA", "AMZN", "MSFT", "GOOGL", "META", "BABA", "NU", "SOFI", "PLTR"
)}
FINNHUB = "Finnhub"
YAHOO = "Yahoo Finance"
QUOTE_MAX_AGE = timedelta(days=5)
TARGET_MAX_AGE = timedelta(days=90)
FINNHUB_REQUEST_INTERVAL = 1.2
FINNHUB_RATE_LIMIT_PAUSE = 60
YAHOO_RATE_LIMIT_PAUSE = 60
_finnhub_lock = Lock()
_finnhub_retry_at = 0.0
_yahoo_retry_at = 0.0


def fetch(endpoint, symbol, *, api_key, diagnostics=None, **params):
    global _finnhub_retry_at
    with _finnhub_lock:
        if time.monotonic() < _finnhub_retry_at:
            add_diagnostic(diagnostics, endpoint, "Overgeslagen tijdens rate-limitpauze")
            return {}
        time.sleep(FINNHUB_REQUEST_INTERVAL)
        return _finnhub_request(endpoint, symbol, api_key=api_key, diagnostics=diagnostics, **params)


def add_diagnostic(diagnostics, step, status):
    if diagnostics is not None:
        diagnostics.append((step, status))


def _finnhub_request(endpoint, symbol, *, api_key, diagnostics, **params):
    global _finnhub_retry_at
    try:
        response = requests.get(
            f"https://finnhub.io/api/v1/{endpoint}",
            headers={"X-Finnhub-Token": api_key},
            params={"symbol": symbol, **params},
            timeout=10,
            allow_redirects=False,
        )
        if response.status_code == 429:
            delay = retry_after_seconds(response.headers.get("Retry-After"))
            _finnhub_retry_at = time.monotonic() + delay
    except requests.RequestException:
        add_diagnostic(diagnostics, endpoint, "Netwerkfout")
        return {}

    code = response.status_code
    if code != 200:
        reason = {
            401: "Authenticatie geweigerd",
            403: "Toegang geweigerd (controleer endpointrechten)",
            429: "Aanvraaglimiet bereikt",
        }.get(code, "Serverfout" if 500 <= code <= 599 else "Onverwachte HTTP-status")
        add_diagnostic(diagnostics, endpoint, f"HTTP {code} · {reason}")
        return {}
    try:
        data = response.json()
    except ValueError:
        add_diagnostic(diagnostics, endpoint, "HTTP 200 · onleesbaar antwoord")
        return {}
    if mapping(data).get("error"):
        add_diagnostic(diagnostics, endpoint, "HTTP 200 · bron meldde een fout")
        return {}
    expected_type = list if endpoint == "stock/recommendation" else Mapping
    if not isinstance(data, expected_type) or not data:
        add_diagnostic(diagnostics, endpoint, "HTTP 200 · leeg of onverwacht antwoord")
        return {}
    add_diagnostic(diagnostics, endpoint, "HTTP 200 · antwoord ontvangen")
    return data


def retry_after_seconds(header):
    seconds = number(header, positive=True)
    if seconds is not None:
        return seconds
    if header:
        try:
            retry_at = parsedate_to_datetime(header)
            remaining = (retry_at - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                return remaining
        except (TypeError, ValueError, OverflowError):
            pass
    return FINNHUB_RATE_LIMIT_PAUSE


def number(value, *, positive=False):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isfinite(value) and (not positive or value > 0):
        return value
    return None


def timestamp(value):
    try:
        if isinstance(value, str):
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return date.replace(tzinfo=date.tzinfo or timezone.utc).astimezone(timezone.utc)
        return datetime.fromtimestamp(number(value, positive=True), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def currency_code(value):
    # Preserve case: GBp/GBX denote pence, GBP denotes pounds.
    return value if isinstance(value, str) and len(value) == 3 and value.isascii() and value.isalpha() else None


def mapping(value):
    return value if isinstance(value, Mapping) else {}


def market_timezone(name):
    if isinstance(name, str):
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return None


def yahoo_data(symbol, *, now=None):
    import yfinance as yf
    from yfinance.exceptions import YFRateLimitError

    global _yahoo_retry_at
    diagnostics = []
    if time.monotonic() < _yahoo_retry_at:
        return {"source": YAHOO, "diagnostics": [("Yahoo", "Overgeslagen tijdens rate-limitpauze")]}

    try:
        ticker = yf.Ticker(symbol)
    except Exception:
        return {"source": YAHOO, "diagnostics": [("Yahoo", "Ticker initialiseren mislukt")]}
    history, metadata, info = None, {}, {}
    try:
        # yfinance propageert rate limits ook met de standaard foutconfiguratie.
        # Die configuratie laat tickerinfo bruikbare deelantwoorden combineren.
        history = ticker.history(
            period="1mo", interval="1d", auto_adjust=False, timeout=8
        )
        metadata = mapping(ticker.get_history_metadata())
        diagnostics.append(("Yahoo historie", "Metadata ontvangen" if metadata else "Geen metadata ontvangen"))
        if getattr(history, "empty", False):
            diagnostics.append(("Yahoo historie", "Geen dagkoersen ontvangen"))
    except YFRateLimitError:
        diagnostics.append(("Yahoo historie", "Aanvraaglimiet bereikt"))
        _yahoo_retry_at = time.monotonic() + YAHOO_RATE_LIMIT_PAUSE
    except Exception:
        diagnostics.append(("Yahoo historie", "Ophalen mislukt"))
    if time.monotonic() >= _yahoo_retry_at:
        try:
            info = mapping(ticker.get_info())
            diagnostics.append(("Yahoo tickerinfo", "Antwoord ontvangen" if info else "Leeg antwoord"))
        except YFRateLimitError:
            diagnostics.append(("Yahoo tickerinfo", "Aanvraaglimiet bereikt"))
            _yahoo_retry_at = time.monotonic() + YAHOO_RATE_LIMIT_PAUSE
        except Exception:
            diagnostics.append(("Yahoo tickerinfo", "Ophalen mislukt"))

    if info.get("symbol") and info["symbol"] != symbol:
        diagnostics.append(("Yahoo tickerinfo", "Afwijkende ticker afgewezen"))
        info = {}
    if metadata.get("symbol") and metadata["symbol"] != symbol:
        diagnostics.append(("Yahoo historie", "Afwijkende ticker afgewezen"))
        history, metadata = None, {}
    metadata_currency = currency_code(metadata.get("currency"))
    info_currency = currency_code(info.get("currency"))
    candidates = [
        {
            "price": number(data.get("regularMarketPrice"), positive=True),
            "price_at": timestamp(data.get("regularMarketTime")),
            "currency": currency,
            "price_basis": "Reguliere markt",
            "exchange_tz": market_timezone(data.get("exchangeTimezoneName")),
            "change_pct": number(data.get("regularMarketChangePercent")),
        }
        for data, currency in ((metadata, metadata_currency), (info, info_currency))
    ]
    if history is not None and "Close" in history and history.index.tz is not None:
        bars = [
            (session.date(), close)
            for session, value in history["Close"].items()
            if (close := number(value, positive=True)) is not None
        ]
        exchange_tz = history.index.tz
        if bars:
            session, price = bars[-1]
            candidates.append({
                "price": price, "currency": metadata_currency, "price_session": session,
                "exchange_tz": exchange_tz, "price_basis": "Dagbalk (sessiedatum)",
            })
            for candidate in candidates:
                if metadata_currency and candidate["currency"] == metadata_currency:
                    candidate["exchange_tz"] = exchange_tz
                    as_of = candidate.get("price_at")
                    market_day = as_of.astimezone(exchange_tz).date() if as_of else candidate.get("price_session")
                    previous = next((close for day, close in reversed(bars) if market_day and day < market_day), None)
                    if candidate.get("change_pct") is None and candidate["price"] is not None and previous:
                        candidate["change_pct"] = scaled_percent(candidate["price"] / previous - 1)

    priced, compatible = quote_candidates(candidates, CURRENCIES.get(symbol))
    quote = select_quote(compatible, now or datetime.now(timezone.utc))
    analysts = number(info.get("numberOfAnalystOpinions"), positive=True)
    if analysts is not None:
        analysts = int(analysts) if analysts.is_integer() else None
    return {
        "source": YAHOO, "diagnostics": diagnostics, "price": None, "price_at": None, "currency": metadata_currency or info_currency, **quote,
        "quote_currency_mismatch": len(priced) != len(compatible),
        "target": number(info.get("targetMeanPrice"), positive=True),
        "target_low": number(info.get("targetLowPrice"), positive=True),
        "target_high": number(info.get("targetHighPrice"), positive=True),
        "target_currency": info_currency,
        "average_currency": info_currency,
        "analysts": analysts,
        "average": number(info.get("fiftyDayAverage"), positive=True),
        "growth": scaled_percent(info.get("revenueGrowth")),
        "growth_basis": "Kwartaal, jaar-op-jaar",
    }


def scaled_percent(value):
    value = number(value)
    return number(value * 100) if value is not None else None


def recommendation_count(recommendations, now):
    if not isinstance(recommendations, list):
        return None
    periods = []
    for item in recommendations:
        item = mapping(item)
        date = timestamp(item.get("period"))
        if date is None or not timedelta(0) <= now - date <= TARGET_MAX_AGE:
            continue
        counts = [number(item.get(key)) for key in ("strongBuy", "buy", "hold", "sell", "strongSell")]
        if all(count is not None and count >= 0 and count.is_integer() for count in counts):
            total = sum(int(count) for count in counts)
            if total:
                periods.append((date, total))
    return max(periods, default=(None, None))[1]


def finnhub_data(symbol, api_key, now):
    diagnostics = []
    def request(endpoint, **params):
        return fetch(endpoint, symbol, api_key=api_key, diagnostics=diagnostics, **params)

    quote = mapping(request("quote"))
    profile = mapping(request("stock/profile2"))
    target = mapping(request("stock/price-target"))
    financials = mapping(request("stock/metric", metric="all"))
    recommendations = request("stock/recommendation")
    metrics = mapping(financials.get("metric"))
    currency = currency_code(profile.get("currency"))
    if profile.get("ticker") and profile["ticker"] != symbol:
        diagnostics.append(("stock/profile2", "Afwijkende ticker afgewezen"))
        currency = None
    if target.get("symbol") and target["symbol"] != symbol:
        diagnostics.append(("stock/price-target", "Afwijkende ticker afgewezen"))
        target = {}
    if financials.get("symbol") and financials["symbol"] != symbol:
        diagnostics.append(("stock/metric", "Afwijkende ticker afgewezen"))
        metrics = {}
    target_currency = currency_code(target.get("currency")) if "currency" in target else currency
    target_at = timestamp(target.get("lastUpdated"))
    coverage = recommendation_count(recommendations, now)
    return {
        "source": FINNHUB, "diagnostics": diagnostics, "price": number(quote.get("c"), positive=True),
        "price_at": timestamp(quote.get("t")), "price_basis": "Reguliere markt",
        "exchange_tz": market_timezone("America/New_York"),
        "currency": currency, "change_pct": number(quote.get("dp")),
        "target": number(target.get("targetMean"), positive=True),
        "target_low": number(target.get("targetLow"), positive=True),
        "target_high": number(target.get("targetHigh"), positive=True),
        "target_currency": target_currency,
        "average_currency": currency,
        "target_at": target_at,
        "target_invalid_date": bool(target.get("lastUpdated")) and target_at is None,
        "recommendation_count": coverage,
        # Recommendations are a coverage proxy, separate from the target consensus.
        "analysts": coverage,
        "average": number(metrics.get("50DayMovingAverage"), positive=True),
        "growth": number(metrics.get("revenueGrowthTTMYoy")),
        "growth_basis": "Afgelopen 12 maanden, jaar-op-jaar",
    }


def freshness(date, now, max_age):
    if date is None:
        return "Datum onbekend"
    age = now - date
    if age < -timedelta(minutes=5):
        return "Datum in de toekomst"
    return "Binnen grens" if age <= max_age else "Verouderd"


def quote_freshness(quote, now):
    session = quote.get("price_session")
    exchange_tz = quote.get("exchange_tz")
    as_of = quote.get("price_at")
    if exchange_tz is None or (session is None and as_of is None):
        return freshness(as_of, now, QUOTE_MAX_AGE)
    if as_of is not None and as_of - now > timedelta(minutes=5):
        return "Datum in de toekomst"
    session = session or as_of.astimezone(exchange_tz).date()
    age = now.astimezone(exchange_tz).date() - session
    if age < timedelta(0):
        return "Datum in de toekomst"
    return "Binnen grens" if age <= QUOTE_MAX_AGE else "Verouderd"


def quote_candidates(candidates, expected):
    priced = [data for data in candidates if number(data.get("price"), positive=True) is not None]
    compatible = [
        data for data in priced if data.get("currency") and
        (expected is None or data["currency"] == expected)
    ]
    return priced, compatible


def select_quote(candidates, now):
    def priority(quote):
        status = quote_freshness(quote, now)
        as_of = quote.get("price_at")
        session = quote.get("price_session")
        if session is not None:
            # Midnight is a sorting key only. The bar has no quote timestamp.
            as_of = datetime.combine(session, clock_time.min, quote["exchange_tz"])
        return (
            {"Binnen grens": 3, "Verouderd": 2, "Datum onbekend": 1, "Datum in de toekomst": 0}[status],
            as_of or datetime.min.replace(tzinfo=timezone.utc),
            quote.get("price_at") is not None,
        )

    return max(candidates, key=priority, default={})


def target_freshness(target, now):
    if target.get("target_invalid_date"):
        return "Ongeldige datum"
    return freshness(target.get("target_at"), now, TARGET_MAX_AGE)


def exceeds_relative_difference(left, right, threshold):
    try:
        low, high = Decimal(str(left)), Decimal(str(right))
        if not (low.is_finite() and high.is_finite() and low > 0 and high > 0):
            return False
        low, high = sorted((low, high))
        # Compare the smaller value directly with the exact lower bound.
        with localcontext() as context:
            context.prec = len(high.as_tuple().digits) + len(threshold.as_tuple().digits) + 2
            return low < high * (1 - threshold)
    except DecimalException:
        return False


def source_conflicts(quotes, targets, now):
    def disagreement(candidates, field, threshold, comparable):
        return any(
            left["source"] != right["source"]
            and exceeds_relative_difference(left.get(field), right.get(field), threshold)
            and comparable(left, right)
            for index, left in enumerate(candidates) for right in candidates[index + 1:]
        )

    def comparable_quotes(left, right):
        left_at, right_at = left.get("price_at"), right.get("price_at")
        return (
            left["currency"] == right["currency"]
            and quote_freshness(left, now) == quote_freshness(right, now) == "Binnen grens"
            and left_at is not None and right_at is not None
            and abs(left_at - right_at) <= timedelta(hours=1)
        )

    def comparable_targets(left, right):
        return all(target_freshness(data, now) in ("Binnen grens", "Datum onbekend")
                   for data in (left, right))

    conflicts = []
    if disagreement(quotes, "price", Decimal("0.05"), comparable_quotes):
        conflicts.append("Koersbronnen wijken meer dan 5% af")
    if disagreement(targets, "target", Decimal("0.20"), comparable_targets):
        conflicts.append("Koersdoelbronnen wijken meer dan 20% af")
    return conflicts


def data_quality(price_age, target_age, analysts, expected, quote, target, average, problems):
    points = {
        "Koersactualiteit": 30 if price_age == "Binnen grens" else 0,
        "Koersdoelactualiteit": 30 if target_age == "Binnen grens" else 0,
        "Analistendekking": min(analysts or 0, 15),
        "Handelsvaluta": 15 if expected and quote and target else (5 if quote else 0),
        "Technische gegevens": 10 if average else 0,
        "Bronafwijkingen": -min(30, 15 * len(problems)),
    }
    score = max(0, sum(points.values()))
    if price_age != "Binnen grens" or target_age != "Binnen grens":
        score = min(score, 59)
    if problems:
        score = min(score, 79)
    return score, points


def selection_reason(data, *, field, selected, currency, status):
    if data.get(field) is None:
        return "Geen bruikbare waarde"
    candidate_currency = data.get("currency" if field == "price" else "target_currency")
    if not candidate_currency:
        return "Valuta onbekend"
    if currency and candidate_currency != currency:
        return "Afwijkende handelsvaluta"
    if data is selected:
        return f"Geselecteerd · {status}"
    if not selected:
        return f"Geen vergelijkbare kandidaat · {status}"

    time_field = "price_at" if field == "price" else "target_at"
    candidate_date = data.get(time_field)
    selected_date = selected.get(time_field)
    if field == "price":
        candidate_date = candidate_date or data.get("price_session")
        selected_date = selected_date or selected.get("price_session")
    candidate_label = candidate_date.isoformat() if candidate_date else "datum onbekend"
    selected_label = selected_date.isoformat() if selected_date else "datum onbekend"
    return (
        f"Niet geselecteerd · {status} · eigen datum {candidate_label} · "
        f"gekozen bron {selected.get('source', 'onbekend')} ({selected_label})"
    )


def merge(symbol, providers, now):
    expected = CURRENCIES.get(symbol)
    priced, compatible = quote_candidates(providers, expected)
    quote = select_quote(compatible, now)
    reported = {data["currency"] for data in providers if data.get("currency")}
    currency = quote.get("currency") or expected
    if currency is None and len(reported) == 1:
        currency = reported.pop()
    target_candidates = [data for data in providers if number(data.get("target"), positive=True) is not None]
    targets = [data for data in target_candidates if currency and data.get("target_currency") == currency]
    target = max(
        targets,
        key=lambda data: (
            {"Binnen grens": 3, "Datum onbekend": 2, "Verouderd": 1,
             "Datum in de toekomst": 0, "Ongeldige datum": 0}[target_freshness(data, now)],
            data.get("target_at") or datetime.min.replace(tzinfo=timezone.utc),
        ),
        default={},
    )
    price_age = quote_freshness(quote, now)
    target_age = target_freshness(target, now)
    growth = next((data for data in providers if data.get("growth") is not None), {})
    average = next((
        data for data in providers if currency and data.get("average_currency") == currency
        and data.get("average") is not None
    ), {})
    analysts = target.get("analysts")
    upside = None
    score = None
    if price_age == "Binnen grens" and target and target_age in ("Binnen grens", "Datum onbekend"):
        upside = number(100 * (target["target"] / quote["price"] - 1))
    if upside is not None and analysts is not None and analysts >= 3 and growth and average:
        score = round(
            max(0, min(40, upside)) + min(20, analysts)
            + max(0, min(25, growth["growth"]))
            + (15 if quote["price"] > average["average"] else 0)
        )

    issues = []
    if len(priced) != len(compatible) or any(data.get("quote_currency_mismatch") for data in providers):
        issues.append("Koersbron met onbekende of afwijkende valuta overgeslagen")
    if len(target_candidates) != len(targets):
        issues.append("Koersdoel met onbekende of afwijkende valuta overgeslagen")
    issues.extend(source_conflicts(compatible, targets, now))
    low, high = target.get("target_low"), target.get("target_high")
    if target and ((low is not None and low > target["target"]) or
                   (high is not None and high < target["target"])):
        issues.append("Bandbreedte koersdoel wijkt af van gemiddelde")
        low, high = None, None
    direct_analysts = analysts if "recommendation_count" not in target else None
    quality, points = data_quality(
        price_age, target_age, direct_analysts, expected, quote, target, average, issues
    )
    reliability = "Hoog" if quality >= 80 else "Beperkt" if quality >= 60 else "Laag"

    diagnostics = []
    for data in providers:
        source = data.get("source")
        diagnostics.extend((source, step, reason) for step, reason in data.get("diagnostics", ()))
        if source and not data.get("skipped"):
            for field, label, selected, status in (
                ("price", "Koers", quote, quote_freshness(data, now)),
                ("target", "Koersdoel", target, target_freshness(data, now)),
            ):
                diagnostics.append((source, label, selection_reason(
                    data, field=field, selected=selected,
                    currency=expected if field == "price" else currency, status=status,
                )))
            if data.get("quote_currency_mismatch"):
                diagnostics.append((source, "Koers", "Koerskandidaat met onbekende of afwijkende valuta afgewezen"))

    if not quote:
        issues.append("Geen verifieerbare koers")
    elif price_age != "Binnen grens":
        issues.append(f"Koers: {price_age.lower()}")
    if not target:
        issues.append("Geen vergelijkbaar koersdoel")
    elif target_age != "Binnen grens":
        issues.append(f"Koersdoel: {target_age.lower()}")
    if target and direct_analysts is None:
        issues.append("Aantal analisten voor het koersdoel onbekend")
    if not average:
        issues.append("50-daags gemiddelde ontbreekt")
    if score is None and upside is not None:
        issues.append("Onvoldoende gegevens voor score")

    return {
        "symbol": symbol, "price": quote.get("price"), "currency": currency,
        "price_source": quote.get("source"), "price_at": quote.get("price_at"),
        "price_basis": quote.get("price_basis"), "price_session": quote.get("price_session"),
        "price_age": price_age if quote else None, "change_pct": quote.get("change_pct"),
        "target": target.get("target"), "target_currency": target.get("target_currency"),
        "target_low": low, "target_high": high,
        "target_source": target.get("source"), "target_at": target.get("target_at"),
        "target_age": target_age if target else None, "upside": upside,
        "analysts": direct_analysts,
        "analysts_source": target.get("source") if direct_analysts is not None else None,
        "recommendation_count": target.get("recommendation_count"),
        "quality": quality, "reliability": reliability, "quality_points": points,
        "diagnostics": diagnostics,
        "revenue_growth": growth.get("growth"),
        "growth_source": growth.get("source"), "growth_basis": growth.get("growth_basis"),
        "average": average.get("average"), "average_source": average.get("source"),
        "score": score, "status": " · ".join(issues) or "Gegevens beschikbaar", "checked": now,
    }


def scan(symbols=SYMBOLS, *, api_key=None):
    if api_key is None:
        api_key = os.environ.get("FINNHUB_API_KEY")
    api_key = (api_key or "").strip()
    # yfinance kan providerdiagnostiek rechtstreeks naar zijn logger schrijven.
    logging.getLogger("yfinance").disabled = True
    rows = []
    for symbol in symbols:
        providers = []
        mapped_symbol = FINNHUB_SYMBOLS.get(symbol)
        config_status = "Sleutel beschikbaar" if api_key else "Sleutel ontbreekt"
        reasons = [("Configuratie", config_status)]
        if mapped_symbol:
            reasons.append(("Ticker-mapping", f"{symbol} → {mapped_symbol}"))
        else:
            reasons.append(("Ticker-mapping", "Geen gevalideerde Finnhub-mapping"))
        if api_key and mapped_symbol:
            data = finnhub_data(mapped_symbol, api_key, datetime.now(timezone.utc))
            providers.append({**data, "diagnostics": reasons + data.get("diagnostics", [])})
        else:
            providers.append({"source": FINNHUB, "skipped": True, "diagnostics": reasons})
        providers.append(yahoo_data(symbol))
        rows.append(merge(symbol, providers, datetime.now(timezone.utc)))
    return rows


def notify(rows):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram nog niet ingesteld")
        return

    hits = [row for row in rows if row.get("score") is not None and row["score"] >= ALERT_THRESHOLD]
    if not hits:
        print("Geen geldige onderzoekssignalen")
        return

    lines = [f"AI Stock Radar V{VERSION}", "Indicatieve onderzoeksscores · geen rendementsvoorspelling of koopadvies", ""]
    for row in hits:
        lines.extend((
            f"{row['symbol']} · Onderzoeksscore {row['score']}/100 · {row['upside']:+.1f}% verschil met koersdoel",
            f"Datakwaliteit {row['quality']}/100 · Betrouwbaarheid {row['reliability'].lower()}",
            f"Koers: {row['price_source']} · Koersdoel: {row['target_source']} ({row['target_age'].lower()})",
            "",
        ))
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": "\n".join(lines)[:4000]},
            timeout=20,
            allow_redirects=False,
        )
        delivered = response.status_code == 200 and mapping(response.json()).get("ok") is True
    except (requests.RequestException, ValueError):
        delivered = False
    if not delivered:
        raise RuntimeError("Telegram verzending mislukt") from None
    print("Telegram melding verzonden")


def main():
    rows = scan()
    current = sum(row["price_age"] == "Binnen grens" for row in rows)
    print(f"Aandelen gescand: {len(rows)}")
    print(f"Actuele koersen: {current}/{len(rows)}")
    print(f"Koersdoelen beschikbaar: {sum(row['target'] is not None for row in rows)}")
    print(f"Scores berekend: {sum(row['score'] is not None for row in rows)}")
    print(f"Databronnen geselecteerd (koers): {dict(Counter(row['price_source'] or 'Geen' for row in rows))}")
    print(f"Databronnen geselecteerd (koersdoel): {dict(Counter(row['target_source'] or 'Geen' for row in rows))}")
    for (source, step, reason), count in sorted(Counter(
        event for row in rows for event in row["diagnostics"]
    ).items()):
        print(f"{source} · {step} · {reason} ({count})")
    if not current:
        raise RuntimeError("Geen actuele koersen ontvangen")
    notify(rows)


if __name__ == "__main__":
    main()
