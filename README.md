# AI Stock Radar V0.4

Het Nederlandstalige dashboard volgt standaard 16 internationale aandelen.

`NVDA AMZN MSFT GOOGL META ASML.AS ADYEN.AS SAP.DE 7203.T BABA RELIANCE.NS SHOP.TO BHP.AX NU SOFI PLTR`

V0.4 toont onderzoekspotentieel en databetrouwbaarheid apart. De filters op minimale onderzoeksscore en datakwaliteit gelden voor **Onderzoekskansen**. De tabel **Alle gevolgde aandelen** blijft alle ingevoerde tickers tonen. Onder de uitklappanelen staan de bronverdeling, koersdoelmetadata, selectie-uitkomsten en punten per kwaliteitsonderdeel.

## Lokaal draaien

GitHub Actions test met Python 3.11 en 3.14. De geplande scan draait op Python 3.11.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
streamlit run app.py
```

Stel `FINNHUB_API_KEY` in via de serveromgeving of als sleutel op het hoogste niveau in Streamlit Secrets. Het dashboard leest eerst de omgeving en gebruikt daarna Streamlit Secrets. Streamlit kan geheimen op het hoogste niveau ook als omgevingsvariabele beschikbaar maken. De CLI leest alleen de omgeving. GitHub Actions geeft het repositorygeheim met dezelfde naam door aan de scanstap. Bij een ontbrekende sleutel blijft Yahoo Finance beschikbaar.

```sh
python radar.py
```

GitHub Actions test bij pushes naar `upgrade-v0.4`, pull requests naar `main`, handmatige runs en geplande runs. De live scan draait uitsluitend na geslaagde tests bij een handmatige run of iedere vier uur op minuut 17 UTC. Er draait maximaal één scan tegelijk. De tests gebruiken gesimuleerde marktgegevens. De serveropstarttest opent alleen een lokale healthcheck. Als het systeem het aanmaken of binden van een loopback-socket expliciet weigert, meldt de lokale suite deze test als overgeslagen. Met `REQUIRE_STREAMLIT_HTTP=1` is de healthcheck verplicht, zoals in GitHub Actions. Telegram blijft optioneel en gebruikt pas `TELEGRAM_BOT_TOKEN` en `TELEGRAM_CHAT_ID` als beide beschikbaar zijn. Meldingen vanaf onderzoeksscore 75 geven de onderzoeksscore, datakwaliteit, betrouwbaarheid en koersdoelactualiteit. Het dashboard verstuurt geen berichten.

## Databronnen en diagnostiek

- Finnhub wordt geraadpleegd voor de negen Amerikaanse noteringen die expliciet aan hetzelfde Finnhub-symbool zijn gekoppeld. Voor de overige zeven standaardtickers en zelf ingevoerde tickers gebruikt de scan Yahoo Finance. Een expliciete mapping voorkomt dat bijvoorbeeld een lokale notering en een Amerikaanse ADR als hetzelfde instrument worden behandeld. [Finnhub API](https://finnhub.io/docs/api)
- Finnhub levert een koerstijd via `quote`, valuta via `stock/profile2`, en waar toegankelijk gemiddelde, laagste en hoogste koersdoel en de door Finnhub gerapporteerde laatste update via `stock/price-target`. Een expliciete koersdoelvaluta krijgt voorrang op de profielvaluta. Het totaal uit de meest recente bruikbare aanbevelingsperiode is een benadering van dekking voor de onderzoeksscore. Het daadwerkelijke aantal analisten achter het Finnhub-koersdoel blijft leeg. [Finnhub Price Target](https://finnhub.io/docs/api/price-target)
- Yahoo Finance levert via yfinance de koers, handelsvaluta, het gemiddelde, laagste en hoogste koersdoel en waar beschikbaar `numberOfAnalystOpinions`. De gebruikte Yahoo-consensusvelden geven geen verifieerbare koersdoeldatum. Bij een dagbalk staat de lokale sessiedatum apart. De scantijd wordt afzonderlijk weergegeven. [yfinance Ticker](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.html)
- De scan kiest op actualiteit en vervolgens op recentheid. Voor een actuele beursdag krijgt een koers met een tijdstip voorrang op een dagbalk uit dezelfde sessie. Geldige recente gedateerde koersdoelen krijgen voorrang op ongedateerde doelen. Oudere of ongedateerde waarden kunnen met hun status zichtbaar blijven. De standaardtickers moeten overeenkomen met hun verwachte handelsvaluta. Voor andere tickers gebruikt de scan de valuta van de bron en kent daarvoor minder kwaliteitspunten toe. De scan rekent geen valuta om.
- Een koers geldt maximaal vijf kalenderdagen als actueel in de beurstijdzone, of vijf etmalen als die zone ontbreekt. Een koersdoel met een bekende datum geldt maximaal 90 etmalen als actueel. Ongedateerde koersdoelen kunnen indicatief meetellen voor onderzoek. Verouderde, onleesbaar gedateerde of toekomstige doelen buiten de tijdstolerantie van vijf minuten tellen niet mee voor het koerspotentieel.
- Een HTTP 401 wijst op geweigerde authenticatie. Een 403 betekent geweigerde toegang en kan afhangen van het endpoint of abonnement. Een 429 activeert de bestaande Finnhub-pauze volgens `Retry-After` of 60 seconden. De minimale spreiding blijft 1,2 seconde per aanvraag. HTTP 5xx, netwerkfouten en lege of onleesbare antwoorden worden apart gemeld. Bij een door yfinance gemelde limiet pauzeert Yahoo 60 seconden. Een geldig Yahoo-antwoord kan voorrang krijgen, ook wanneer Finnhub toegankelijk is. [Finnhub-abonnementen](https://finnhub.io/pricing)

De diagnostiek toont vaste meldingen per endpoint en ticker, inclusief ontbrekende configuratie, mapping, valutacontrole en kandidaatselectie. De CLI logt dezelfde uitkomsten en een telling per geselecteerde bron. Finnhub-tokens gaan alleen in de HTTP-header. De meldingen bevatten geen providerbody's, verzoekheaders of foutteksten. Houd sleutelbestanden lokaal en controleer accountrechten in de omgeving waar de scan draait.

## Onderzoeksscore

Voor een score zijn een actuele koers, een vergelijkbaar koersdoel, minimaal drie gerapporteerde analisten of Finnhub-aanbevelingen als dekkingsmaat, omzetgroei en een 50-daags gemiddelde vereist.

| Onderdeel | Maximum |
| --- | ---: |
| Procentueel verschil tussen koers en gemiddeld koersdoel, begrensd op 0–40 | 40 |
| Analistendekking, één punt per analist of Finnhub-aanbeveling | 20 |
| Omzetgroei in procenten, begrensd op 0–25 | 25 |
| Koers boven het 50-daags gemiddelde | 15 |

Yahoo levert jaar-op-jaar groei op kwartaalbasis en Finnhub jaar-op-jaar groei op basis van twaalfmaandsomzet. Het dashboard vermeldt de gebruikte periode. Onderzoeksscores zijn indicatief. Ze voorspellen geen rendement en vormen geen koopadvies.

## Datakwaliteit

De punten hieronder beoordelen de gegevens achter de onderzoeksscore. De gewichten geven voorrang aan de actualiteit van koers en koersdoel. De overige punten wegen controleerbare dekking, handelsvaluta en technische invoer mee.

| Onderdeel | Punten |
| --- | ---: |
| Actuele koers | 30 |
| Actueel koersdoel met bekende datum | 30 |
| Gerapporteerd aantal analisten bij het gebruikte koersdoel | 1 per analist, maximaal 15 |
| Koers én doel passen bij de bekende handelsvaluta | 15 |
| Anders, als een koers met bronvaluta beschikbaar is | 5 |
| Bruikbaar 50-daags gemiddelde in dezelfde valuta | 10 |
| Geconstateerde afwijking | −15 per categorie, maximaal −30 |

De score heeft een ondergrens van 0. Bij een ontbrekende of niet-actuele koers of koersdoeldatum is de score maximaal 59. Bij afgewezen koers- of koersdoelvaluta, tegenstrijdige bronnen of een inconsistente koersdoelbandbreedte is het maximum 79. De statussen zijn **Laag** (0–59), **Beperkt** (60–79) en **Hoog** (80–100).

De scan vergelijkt actuele koersen in dezelfde valuta met bekende tijdstippen die maximaal een uur uiteenliggen. Een verschil groter dan 5% telt als afwijking. Bij vergelijkbare koersdoelen met een actuele of onbekende datum geldt een grens van 20%. Beide percentages delen het absolute verschil door de hoogste waarde. Dagbalken zonder tijdstip blijven buiten de conflictvergelijking. Met één bruikbare bron ontbreekt een onafhankelijke vergelijking. Een hoge score is een indicatie van bruikbaarheid op basis van beschikbare metadata.
