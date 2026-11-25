# AI Stock Radar V0.3

Het Nederlandstalige dashboard volgt standaard 16 internationale aandelen:

`NVDA AMZN MSFT GOOGL META ASML.AS ADYEN.AS SAP.DE 7203.T BABA RELIANCE.NS SHOP.TO BHP.AX NU SOFI PLTR`

## Lokaal draaien

Gebruik Python 3.11, zoals in de automatische scan.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
streamlit run app.py
```

Stel `FINNHUB_API_KEY` in via de omgeving of via `.streamlit/secrets.toml` voor het dashboard. Zonder sleutel werkt de scan met Yahoo Finance. De CLI gebruikt omgevingsvariabelen. Stel voor Telegram ook `TELEGRAM_BOT_TOKEN` en `TELEGRAM_CHAT_ID` in. Het dashboard toont de resultaten zonder een bericht te sturen.

```sh
python radar.py
```

De GitHub Actions-workflow ondersteunt handmatige uitvoering en scant iedere vier uur op minuut 17 (UTC). Stel de drie genoemde variabelen in als Actions Secrets. De workflow draait de softwaretests vóór de scan en voert maximaal één automatische of handmatige scan tegelijk uit. Pull requests naar main draaien de softwaretests zonder de live scan of Telegram. Finnhub-aanvragen binnen één proces worden minstens 1,2 seconde gespreid. Na HTTP 429 slaat de scan Finnhub-aanvragen tijdelijk over, met de wachttijd uit de Retry-After-header of standaard 60 seconden. Yahoo Finance blijft als tweede bron beschikbaar. Bij een door yfinance gemelde API-limiet slaat het proces nieuwe Yahoo-aanvragen 60 seconden over. Telegram meldt scores vanaf 75 en vermeldt de koersbron, koersdoelbron en de actualiteitsstatus van het koersdoel. De CLI meldt een fout als er geen actuele koers is of als een Telegram-verzending mislukt.

## Bronnen en controles

- [Finnhub](https://finnhub.io/docs/api) levert waar beschikbaar koersen, bedrijfsprofielen, consensusdoelen, aanbevelingen en financiële ratio’s voor de Amerikaanse noteringen uit de standaardlijst. De profielvaluta dient als controle voor de koers en, wanneer het koersdoel zelf geen valuta opgeeft, als valuta van dat Amerikaanse koersdoel. De analistendekking komt uit Finnhubs recentste bruikbare aanbevelingsperiode en is een maatstaf voor dekking.
- [Yahoo Finance](https://finance.yahoo.com/) wordt via [yfinance](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.html) geraadpleegd voor elke ticker en levert waar beschikbaar koersen, consensusdoelen, analistendekking en score-invoer. Voor een dagbalk toont het dashboard de lokale sessiedatum in een aparte kolom. Een regulier koerstijdstip wordt in UTC getoond. Yahoo's koersdoel krijgt het label ‘Datum onbekend’ wanneer er geen verifieerbare publicatiedatum is.
- De standaardlijst legt de verwachte handelsvaluta per notering vast. De bron moet een overeenkomende valuta leveren. Voor zelf ingevoerde tickers geldt de door Yahoo opgegeven handelsvaluta. Koersdoel en 50-daags gemiddelde moeten in dezelfde handelsvaluta staan als de koers. Er vindt geen valutaomrekening plaats.
- Bij meerdere bruikbare koersen krijgt de recentste voorrang. Een koers geldt maximaal vijf kalenderdagen als actueel, gerekend in de beurstijdzone wanneer die bekend is. Zonder beurstijdzone geldt een grens van vijf etmalen. Gedateerde koersdoelen gelden maximaal negentig kalenderdagen. Oudere waarden blijven zichtbaar met hun status. Een ongedateerd koersdoel mag indicatief meetellen en behoudt zijn status. Een koersdoel met een onleesbare datum telt niet mee. De scandatum staat apart van de brongegevens.

## Onderzoeksscore

Voor een score zijn een actuele koers, een vergelijkbaar koersdoel, minstens drie analisten, omzetgroei en een 50-daags gemiddelde vereist. Ontbrekende gegevens blijven leeg.

| Onderdeel | Punten |
| --- | ---: |
| Koerspotentieel in procenten, begrensd op 0–40 | 40 |
| Analistendekking, begrensd op 20 | 20 |
| Omzetgroei in procenten, begrensd op 0–25 | 25 |
| Koers boven het 50-daags gemiddelde | 15 |

Yahoo rapporteert omzetgroei op kwartaalbasis en Finnhub gebruikt de afgelopen twaalf maanden. Beide zijn jaar-op-jaar. Het dashboard vermeldt de gebruikte periode. De score is indicatief en niet historisch gevalideerd.
