
import streamlit as st
import pandas as pd
import os
from radar import scan, SYMBOLS

st.set_page_config(
    page_title="AI Stock Radar",
    page_icon="📈",
    layout="wide"
)

key = st.secrets.get("FINNHUB_API_KEY")

if key:
    os.environ["FINNHUB_API_KEY"] = key

st.title("📈 AI Stock Radar")
st.caption("Worldwide Stock Intelligence - V0.2")

if not os.environ.get("FINNHUB_API_KEY"):
    st.error("Finnhub API Key ontbreekt in Secrets.")
    st.stop()

st.sidebar.header("Instellingen")

symbols = st.sidebar.text_area(
    "Aandelen tickers",
    " ".join(SYMBOLS)
)

threshold = st.sidebar.slider(
    "Minimale kansscore",
    0, 100, 75
)

@st.cache_data(ttl=3600, show_spinner=False)
def load_data(tickers):
    return scan(tickers)

if st.sidebar.button("🔄 Vernieuw scan"):
    load_data.clear()

tickers = tuple(
    dict.fromkeys(symbols.upper().split())
)[:16]

st.sidebar.caption(
    "Maximaal 16 aandelen per scan in deze versie."
)

with st.spinner("Finnhub gegevens ophalen..."):
    df = pd.DataFrame(load_data(tickers))

if df.empty:
    st.warning("Geen aandelen beschikbaar.")
    st.stop()

for column in ["score", "price", "upside", "change_pct"]:
    df[column] = pd.to_numeric(
        df[column], errors="coerce"
    )

df = df.sort_values(
    "score",
    ascending=False,
    na_position="last"
)

valid_prices = df["price"].notna().sum()
valid_scores = df["score"].notna().sum()

opportunities = df[df["score"] >= threshold]

a, b, c, d = st.columns(4)

a.metric("Gevolgde aandelen", len(df))
b.metric("Beschikbare koersen", int(valid_prices))
c.metric("Volledige analyses", int(valid_scores))
d.metric(
    "Onderzoekskansen",
    len(opportunities) if valid_scores else "Onbekend"
)

if valid_prices == 0:
    st.error("Geen koersen ontvangen van Finnhub.")

elif valid_prices < len(df):
    st.warning(
        "Niet alle aandelen zijn beschikbaar "
        "via jouw Finnhub abonnement."
    )

if valid_scores == 0:
    st.warning(
        "Er zijn nog onvoldoende gegevens "
        "om betrouwbare scores te berekenen."
    )

st.subheader("🎯 Potentiele onderzoekskansen")

if valid_scores == 0:
    st.info("Nog geen volledige analyses beschikbaar.")

elif opportunities.empty:
    st.info("Geen berekende scores boven de ingestelde grens.")

else:
    st.dataframe(
        opportunities,
        hide_index=True,
        width="stretch"
    )

st.subheader("📊 Alle gevolgde aandelen")

st.dataframe(
    df,
    hide_index=True,
    width="stretch",
    column_config={
        "symbol": "Aandeel",
        "price": st.column_config.NumberColumn(
            "Koers", format="%.2f"
        ),
        "currency": "Valuta",
        "change_pct": st.column_config.NumberColumn(
            "Dagverschil (%)", format="%.2f"
        ),
        "target": "Koersdoel",
        "upside": st.column_config.NumberColumn(
            "Potentieel (%)", format="%.2f"
        ),
        "analysts": "Analisten",
        "revenue_growth": "Omzetgroei (%)",
        "score": "Kansscore",
        "source": "Databron",
        "status": "Status",
        "checked": "Gecontroleerd (UTC)"
    }
)

st.subheader("🏆 Hoogste scores")

ranked = df.dropna(subset=["score"]).head(10)

if not ranked.empty:
    st.bar_chart(
        ranked.set_index("symbol")["score"]
    )

st.subheader("🌐 Informatiebronnen")

st.markdown("""
- [Finnhub](https://finnhub.io/)
- [Yahoo Finance](https://finance.yahoo.com)
- [TipRanks](https://www.tipranks.com)
- [Morningstar](https://www.morningstar.com)
- [MarketScreener](https://www.marketscreener.com)
- [Stock Analysis](https://stockanalysis.com)
- [TradingView](https://www.tradingview.com)
- [Reuters](https://www.reuters.com/markets/)
- [Seeking Alpha](https://seekingalpha.com)
- [Koyfin](https://www.koyfin.com)
- [Simply Wall St](https://simplywall.st)
- [X / Twitter](https://x.com)
""")

st.caption(
    "Automatische databron: Finnhub. "
    "Overige websites zijn snelkoppelingen."
)

st.warning(
    "Experimenteel onderzoeksdashboard. "
    "Scores zijn niet historisch gevalideerd. "
    "Koersdoelen zijn geen garanties. "
    "Geen financieel advies."
)
