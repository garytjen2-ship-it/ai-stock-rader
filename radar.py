
import os
import time
import requests
from datetime import datetime, timezone

SYMBOLS = """
NVDA AMZN MSFT GOOGL META
ASML.AS ADYEN.AS SAP.DE
7203.T BABA RELIANCE.NS
SHOP.TO BHP.AX NU SOFI PLTR
""".split()

API_URL = "https://finnhub.io/api/v1"


def fetch(endpoint, symbol, **params):
    key = os.environ.get("FINNHUB_API_KEY")

    if not key:
        return None, "API Key ontbreekt"

    time.sleep(1.2)

    try:
        response = requests.get(
            f"{API_URL}/{endpoint}",
            headers={"X-Finnhub-Token": key},
            params={"symbol": symbol, **params},
            timeout=15
        )

        if response.status_code == 429:
            return None, "API limiet bereikt"

        if response.status_code == 403:
            return None, "Geen toegang binnen abonnement"

        if response.status_code == 401:
            return None, "API Key geweigerd"

        if not response.ok:
            return None, f"HTTP {response.status_code}"

        data = response.json()

        if isinstance(data, dict) and data.get("error"):
            return None, "Finnhub geeft een foutmelding"

        return data, None

    except Exception:
        return None, "Verbindingsfout"


def scan(symbols=SYMBOLS):
    rows = []

    for symbol in symbols:
        row = {
            "symbol": symbol,
            "price": None,
            "currency": None,
            "change_pct": None,
            "target": None,
            "upside": None,
            "analysts": None,
            "revenue_growth": None,
            "score": None,
            "source": "Finnhub",
            "status": "Onvoldoende data",
            "checked": datetime.now(
                timezone.utc
            ).isoformat()
        }

        quote, error = fetch("quote", symbol)

        if error:
            row["status"] = error
            rows.append(row)
            continue

        if not isinstance(quote, dict) or not quote.get("c"):
            row["status"] = "Geen koers beschikbaar"
            rows.append(row)
            continue

        row["price"] = quote["c"]
        row["change_pct"] = quote.get("dp")

        profile, _ = fetch("stock/profile2", symbol)

        if isinstance(profile, dict):
            row["currency"] = profile.get("currency")

        recommendations, _ = fetch(
            "stock/recommendation", symbol
        )

        if isinstance(recommendations, list) and recommendations:
            latest = max(
                recommendations,
                key=lambda item: item.get("period", "")
            )

            row["analysts"] = sum(
                latest.get(name, 0) or 0
                for name in [
                    "strongBuy",
                    "buy",
                    "hold",
                    "sell",
                    "strongSell"
                ]
            )

        target, _ = fetch("stock/price-target", symbol)

        if isinstance(target, dict):
            row["target"] = target.get("targetMean")

        if row["target"] and row["price"]:
            row["upside"] = (
                row["target"] / row["price"] - 1
            ) * 100

        financials, _ = fetch(
            "stock/metric",
            symbol,
            metric="all"
        )

        metrics = {}

        if isinstance(financials, dict):
            metrics = financials.get("metric") or {}

        growth = metrics.get("revenueGrowthTTMYoy")
        average = metrics.get("50DayMovingAverage")

        row["revenue_growth"] = growth

        if (
            row["upside"] is not None
            and row["analysts"] is not None
            and row["analysts"] >= 3
            and growth is not None
            and average
            and row["price"]
            and row["currency"] == "USD"
            and "." not in symbol
        ):
            row["score"] = round(
                max(0, min(40, row["upside"]))
                + min(20, row["analysts"])
                + max(0, min(25, growth))
                + (15 if row["price"] > average else 0)
            )

            row["status"] = "OK"

        else:
            row["status"] = "Koers OK - analyse onvolledig"

        rows.append(row)

    return rows


def notify(rows):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat:
        print("Telegram nog niet ingesteld")
        return

    hits = [
        row for row in rows
        if row.get("score") is not None
        and row["score"] >= 75
    ]

    if not hits:
        print("Geen geldige onderzoekssignalen")
        return

    message = (
        "AI STOCK RADAR\n"
        "Onderzoekssignalen - geen koopadvies\n\n"
    )

    for row in hits:
        message += (
            f"{row['symbol']}\n"
            f"Score: {row['score']}/100\n"
            f"Koerspotentieel: {row['upside']:.1f}%\n\n"
        )

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat,
                "text": message[:4000]
            },
            timeout=20
        )
    except requests.RequestException:
        raise RuntimeError(
            "Telegram verbindingsfout"
        ) from None

    if not response.ok:
        raise RuntimeError("Telegram verzending mislukt")

    print("Telegram melding verzonden")


if __name__ == "__main__":
    results = scan()

    valid = sum(
        row["price"] is not None
        for row in results
    )

    print(f"Aandelen gescand: {len(results)}")
    print(f"Koersen ontvangen: {valid}")

    if valid == 0:
        raise RuntimeError("Geen koersen ontvangen")

    notify(results)
