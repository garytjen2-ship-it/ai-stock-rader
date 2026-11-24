

import streamlit as st
import pandas as pd
from radar import scan, SYMBOLS

st.set_page_config(
    page_title="AI Stock Radar",
    page_icon="📈",
    layout="wide"
)

st.title("📈 AI Stock Radar")
st.caption("Worldwide Stock Intelligence")

st.sidebar.header("Instellingen")

symbols = st.sidebar.text_area(
    "Aandelen tickers",
    " ".join(SYMBOLS)
)

threshold = st.sidebar.slider(
    "Minimale kansscore",
    0, 100, 75
)


@st.cache_data(ttl=3600)
def load_data(tickers):
    return scan(tickers)


if st.sidebar.button("🔄 Vernieuw scan"):
    load_data.clear()

tickers = tuple(
    dict.fromkeys(symbols.upper().split())
)[:50]

with st.spinner("Financiele gegevens ophalen..."):
    rows = load_data(tickers)

df = pd.DataFrame(rows)

if df.empty:
    st.warning("Geen aandelen beschikbaar.")
    st.stop()

df["score"] = pd.to_numeric(
    df["score"], errors="coerce"
)

df = df.sort_values(
    "score",
    ascending=False,
    na_position="last"
)

opportunities = df[df["score"] >= threshold]

a, b, c = st.columns(3)

a.metric("Gevolgde aandelen", len(df))
b.metric("Potentiele kansen", len(opportunities))
c.metric("Databronnen", 1)

st.subheader("🎯 Potentiele kansen")

if opportunities.empty:
    st.info("Geen aandelen boven de ingestelde score.")
else:
    st.dataframe(
        opportunities,
        hide_index=True,
        use_container_width=True
    )

st.subheader("📊 Alle gevolgde aandelen")

st.dataframe(
    df,
    hide_index=True,
    use_container_width=True
)

st.subheader("🏆 Hoogste scores")

ranked = df.dropna(subset=["score"]).head(10)

if not ranked.empty:
    st.bar_chart(
        ranked.set_index("symbol")["score"]
    )

st.subheader("🌐 Informatiebronnen")

st.markdown("""
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
    "Automatische data: Yahoo Finance via yfinance. "
    "Overige websites zijn voorlopig snelkoppelingen."
)

st.warning(
    "Dit is een prototype. Scores zijn niet historisch "
    "gevalideerd. Koersdoelen zijn geen garanties. "
    "Geen financieel advies."
)
