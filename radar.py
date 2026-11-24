Python

import os
import requests
import yfinance as yf
from datetime import datetime, timezone

SYMBOLS = """
NVDA AMZN MSFT GOOGL META
ASML.AS ADYEN.AS SAP.DE
7203.T BABA RELIANCE.NS
SHOP.TO BHP.AX NU SOFI PLTR
""".split()


def scan(symbols=SYMBOLS):
    rows = []

    for symbol in symbols:
        try:
            stock = yf.Ticker(symbol)
            info = stock.info

            price = (
                info.get("currentPrice")
                or info.get("regularMarketPrice")
            )
            target = info.get("targetMeanPrice")
            analysts = info.get("numberOfAnalystOpinions") or 0
            growth = info.get("revenueGrowth")
            average = info.get("fiftyDayAverage")

            upside = None
            score = None

            if price and target:
                upside = (target / price - 1) * 100

            if (
                upside is not None
                and analysts >= 3
                and growth is not None
                and average
                and price
            ):
                score = round(
                    max(0, min(40, upside))
                    + min(20, analysts)
                    + max(0, min(25, growth * 100))
                    + (15 if price > average else 0)
                )

            rows.append({
                "symbol": symbol,
                "price": price,
                "currency": info.get("currency"),
                "target": target,
                "upside": upside,
                "analysts": analysts,
                "score": score,
                "status": (
                    "OK" if score is not None
                    else "Onvoldoende data"
                ),
                "checked": datetime.now(
                    timezone.utc
                ).isoformat()
            })

        except Exception:
            rows.append({
                "symbol": symbol,
                "score": None,
                "status": "Bron niet beschikbaar"
            })

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
        print("Geen nieuwe onderzoekssignalen")
        return

    message = (
        "AI STOCK RADAR\n"
        "Potentiele onderzoekskansen\n"
        "Geen koopadvies\n\n"
    )

    for row in hits:
        message += (
            f"{row['symbol']}\n"
            f"Score: {row['score']}/100\n"
            f"Koerspotentieel: {row['upside']:.1f}%\n\n"
        )

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat,
            "text": message
        },
        timeout=20
    )

    if not response.ok:
        raise RuntimeError("Telegram verzending mislukt")

    print("Telegram melding verzonden")


if __name__ == "__main__":
    results = scan()
    print(f"Scan voltooid: {len(results)} aandelen")
    notify(results)
