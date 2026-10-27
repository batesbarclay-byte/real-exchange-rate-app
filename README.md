# Real Exchange Rate App

A Streamlit app that computes the bilateral **real exchange rate (RER)** for any two IMF countries (or the Euro area) using monthly IMF data from January 1976 where available.

## Economics

| Symbol | Definition |
|---|---|
| S(t) | Units of Country 2 currency per **1 unit of Country 1** currency |
| P1(t), P2(t) | Monthly CPI of Country 1 and Country 2 |
| RER(t) | S(t) × P1(t) / P2(t) |
| RER* | Average of RER(t) over valid months in the selected sample |
| Real Index(t) | 100 × RER(t) / RER* (averages 100 over the sample) |

IMF exchange rates are local currency per USD, so the app converts them:
`S(t) = (Country 2 per USD) / (Country 1 per USD)`. The U.S. rate is 1 by definition.

Example: Country 1 = United Kingdom, Country 2 = United States → S(t) is **USD per GBP**.

## Features

- Choose Country 1 and Country 2 from IMF economies and the Euro area (`G163`).
- Pick start and end months; RER* and the Real Index are recalculated automatically.
- Chart view: **Real**, **Nominal** (S in actual currency units, not normalized), or **Both** (separate y-axes).
- Download a CSV of all inputs and calculations for the selected sample.

## Data

IMF SDMX 3.0 API: `https://api.imf.org/external/sdmx/3.0`

- CPI: dataflow `IMF.STA/CPI`, key `{ISO3}.CPI._T.IX.M`
- Exchange rate: dataflow `IMF.STA/ER`, key `{ISO3}.XDC_USD.PA_RT.M` (period average)

No API key is normally needed. Optionally, add `IMF_API_KEY = "..."` in Streamlit Secrets. Coverage varies by country and live downloads can occasionally fail; months with missing inputs are excluded from RER*.

## Files

- `app.py` — the Streamlit app
- `requirements.txt` — Python dependencies (Python 3.12)
- `README.md` — this file

## Run

Deploy on [Streamlit Community Cloud](https://share.streamlit.io) with main file `app.py` and Python 3.12, or locally:

```bash
pip install -r requirements.txt
streamlit run app.py
```
