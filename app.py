import os
import re
from hashlib import sha256
from textwrap import dedent

import pandas as pd
import streamlit as st

from radar import ALERT_THRESHOLD, QUOTE_MAX_AGE, SYMBOLS, TARGET_MAX_AGE, VERSION, scan

@st.cache_data(ttl=900, show_spinner=False)
def load_data(tickers, credential_fingerprint, _api_key):
    return scan(tickers, api_key=_api_key)


def main():
    st.set_page_config(page_title=f"AI Stock Radar V{VERSION}", page_icon="📈", layout="wide")

    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    key_origin = "Omgeving"
    if not api_key:
        try:
            secret = st.secrets.get("FINNHUB_API_KEY")
        except FileNotFoundError:
            secret = None
        api_key = secret.strip() if isinstance(secret, str) else ""
        key_origin = "Streamlit Secrets" if api_key else "Sleutel ontbreekt"

    st.title(f"📈 AI Stock Radar V{VERSION}")
    st.caption("Internationale aandelen · koersen, koersdoelen en indicatieve onderzoeksscores")

    st.sidebar.header("Scaninstellingen")
    symbols = st.sidebar.text_area("Aandelentickers", " ".join(SYMBOLS))
    threshold = st.sidebar.slider("Minimale onderzoeksscore", 0, 100, ALERT_THRESHOLD)
    quality_threshold = st.sidebar.slider("Minimale datakwaliteit", 0, 100, 0)
    tickers = tuple(dict.fromkeys(symbols.upper().split()))
    st.sidebar.caption("Maximaal 16 tickers, gescheiden door spaties.")


    credential_fingerprint = sha256(api_key.encode()).hexdigest()


    if st.sidebar.button("Vernieuw scan"):
        load_data.clear(tickers, credential_fingerprint, api_key)

    if not tickers or len(tickers) > 16 or any(
        not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}", symbol) for symbol in tickers
    ):
        st.warning("Voer 1 tot 16 geldige tickers in, bijvoorbeeld ASML.AS of SHOP.TO.")
        st.stop()

    if not api_key:
        st.info("Yahoo Finance is actief. Voeg een Finnhub-sleutel toe aan de serverconfiguratie om beide bronnen te gebruiken.")

    with st.spinner("Marktgegevens ophalen…"):
        df = pd.DataFrame(load_data(tickers, credential_fingerprint, api_key))

    for column in ("price", "change_pct", "target", "upside", "analysts", "revenue_growth", "average", "score"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    last_checked = pd.to_datetime(df["checked"], utc=True, errors="coerce").max()
    df["price_session"] = df["price_session"].map(lambda session: session.isoformat() if session is not None else None)
    for column in ("price_at", "target_at"):
        df[column] = pd.to_datetime(df[column], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M")

    df = df.sort_values("score", ascending=False, na_position="last", kind="stable")
    ranked = df.dropna(subset=["score"])
    opportunities = ranked[(ranked["score"] >= threshold) & (ranked["quality"] >= quality_threshold)]
    current = int(df["price_age"].eq("Binnen grens").sum())
    targets = int(df["target"].notna().sum())

    a, b, c, d = st.columns(4)
    a.metric("Gevolgde aandelen", len(df))
    b.metric("Actuele koersen", f"{current}/{len(df)}")
    c.metric("Koersdoelen beschikbaar", f"{targets}/{len(df)}")
    d.metric("Berekende scores", f"{len(ranked)}/{len(df)}")
    low_quality = int(df["reliability"].eq("Laag").sum())
    if low_quality:
        st.warning(f"Lage databetrouwbaarheid bij {low_quality} van {len(df)} aandelen. Zie de status per aandeel.")
    st.caption(f"Laatste scan voltooid om {last_checked:%Y-%m-%d %H:%M} UTC · cache maximaal 15 minuten")

    columns = {
        "symbol": "Aandeel",
        "price": st.column_config.NumberColumn("Koers", format="%.2f"),
        "currency": "Handelsvaluta",
        "change_pct": st.column_config.NumberColumn("Dagverschil (%)", format="%+.2f"),
        "price_source": "Koersbron",
        "price_at": "Koerstijd (UTC)",
        "price_session": "Lokale beurssessie",
        "price_basis": "Koerstype",
        "price_age": "Actualiteit koers",
        "target": st.column_config.NumberColumn("Gemiddeld koersdoel", format="%.2f"),
        "target_low": st.column_config.NumberColumn("Laagste koersdoel", format="%.2f"),
        "target_high": st.column_config.NumberColumn("Hoogste koersdoel", format="%.2f"),
        "target_currency": "Valuta koersdoel",
        "target_source": "Bron koersdoel",
        "target_at": "Datum koersdoel (UTC)",
        "target_age": "Actualiteit koersdoel",
        "upside": st.column_config.NumberColumn("Potentieel (%)", format="%+.2f"),
        "analysts": st.column_config.NumberColumn("Analisten", format="%d"),
        "analysts_source": "Bron analistendekking",
        "recommendation_count": st.column_config.NumberColumn("Finnhub-aanbevelingen (dekking)", format="%d"),
        "quality": st.column_config.NumberColumn("Datakwaliteit", format="%d"),
        "reliability": "Betrouwbaarheid",
        "revenue_growth": st.column_config.NumberColumn("Omzetgroei (%)", format="%+.2f"),
        "growth_source": "Bron omzetgroei",
        "growth_basis": "Periode omzetgroei",
        "average": st.column_config.NumberColumn("50-daags gemiddelde", format="%.2f"),
        "average_source": "Bron gemiddelde",
        "score": st.column_config.NumberColumn("Onderzoeksscore", format="%d"),
        "status": "Status",
    }
    overview = (
        "symbol", "score", "quality", "reliability", "price", "currency", "target", "upside", "analysts", "price_source",
        "target_source", "price_age", "target_age", "status",
    )

    st.subheader("Onderzoekskansen")
    if opportunities.empty:
        st.info("Geen scores berekend." if ranked.empty else f"Geen kansen bij onderzoeksscore ≥ {threshold} en datakwaliteit ≥ {quality_threshold}.")
    else:
        st.dataframe(opportunities[list(overview)], column_config=columns, hide_index=True)

    st.subheader("Alle gevolgde aandelen")
    st.dataframe(df[list(overview)], column_config=columns, hide_index=True)

    if not ranked.empty:
        with st.expander("Hoogste onderzoeksscores"):
            st.bar_chart(ranked.head(10).set_index("symbol")["score"])

    with st.expander("Datums, brongegevens en score-invoer"):
        st.dataframe(df[list(columns)], column_config=columns, hide_index=True)

    with st.expander("Databronnen en scoreberekening"):
        usage = {
            label: df[column].fillna("Ontbreekt").value_counts()
            for label, column in (
                ("Koers", "price_source"), ("Koersdoel", "target_source"),
                ("50-daags gemiddelde", "average_source"), ("Omzetgroei", "growth_source"),
            )
        }
        st.dataframe(pd.DataFrame(usage).fillna(0).astype(int))

        st.markdown(dedent(f"""
    - [Finnhub](https://finnhub.io/docs/api) en [Yahoo Finance](https://finance.yahoo.com/) leveren marktgegevens. Finnhub wordt geraadpleegd voor de Amerikaanse noteringen uit de standaardlijst. Yahoo Finance levert ook gegevens voor de andere beurzen.
    - Een koers telt maximaal {QUOTE_MAX_AGE.days} kalenderdagen als actueel, gerekend in de beurstijdzone als die bekend is. Gedateerde koersdoelen mogen maximaal {TARGET_MAX_AGE.days} kalenderdagen oud zijn. Een ongedateerd koersdoel telt indicatief mee en krijgt het label ‘Datum onbekend’.
    - De score kent maximaal 40 punten toe voor koerspotentieel, 20 voor analistendekking, 25 voor omzetgroei en 15 als de koers boven het 50-daags gemiddelde ligt. Voor een score zijn minimaal drie analisten of Finnhub-aanbevelingen en alle vier invoerwaarden nodig. Aanbevelingen zijn een indicatie van dekking.
    - Bij Yahoo Finance kan de omzetgroei op kwartaalbasis zijn. Finnhub gebruikt twaalfmaandsomzet. Het dashboard toont de gebruikte periode.
    """))

    with st.expander("Diagnostiek databronnen"):
        st.caption(f"Finnhub-configuratie · {key_origin}")
        events = [
            (row.symbol, source, step, reason)
            for row in df.itertuples() for source, step, reason in row.diagnostics
        ]
        st.dataframe(pd.DataFrame(events, columns=["Aandeel", "Bron", "Controle", "Uitkomst"]), hide_index=True)
        details = pd.DataFrame(df["quality_points"].tolist(), index=pd.Index(df["symbol"], name="Aandeel"))
        st.dataframe(details.assign(Datakwaliteit=df["quality"].to_numpy()))
        st.caption("Bij een koers of koersdoel buiten de actualiteitsgrens of zonder datum geldt maximaal 59. Bij afgewezen valuta of bronafwijkingen geldt maximaal 79.")

    st.caption("Onderzoeksscores zijn indicatief. Ze voorspellen geen rendement en vormen geen koopadvies.")


if __name__ == "__main__":
    main()
