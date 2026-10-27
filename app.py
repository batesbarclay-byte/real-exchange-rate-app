Real Exchange Rate (RER) app using IMF SDMX 3.0 data.

Definitions
-----------
S(t)   = units of Country 2 currency per 1 unit of Country 1 currency
P1(t)  = Country 1 monthly CPI;  P2(t) = Country 2 monthly CPI
RER(t) = S(t) * P1(t) / P2(t)
RER*   = mean of RER(t) over valid months in the selected sample
Real Index(t) = 100 * RER(t) / RER*

IMF exchange rates (ER dataflow, XDC_USD) are local currency units per USD, so
S(t) = (C2 per USD) / (C1 per USD).
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET

import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from plotly.subplots import make_subplots

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
BASE_URL = "https://api.imf.org/external/sdmx/3.0"
AGENCY = "IMF.STA"
START_MONTH = pd.Timestamp("1976-01-01")
EURO_AREA_CODE = "G163"
HEADERS_JSON = {
    "Accept": "application/vnd.sdmx.data+json;version=2.0.0, application/json;q=0.9, */*;q=0.5",
    "User-Agent": "real-exchange-rate-app/1.0 (Streamlit; educational)",
    "Cache-Control": "no-cache",
}

st.set_page_config(page_title="Real Exchange Rate App", page_icon="💱", layout="wide")


# ----------------------------------------------------------------------------
# Country list
# ----------------------------------------------------------------------------
# Some IMF-reported economies that pycountry names awkwardly or may not include.
EXTRA_COUNTRIES = {
    EURO_AREA_CODE: "Euro area",
    "XKX": "Kosovo",
    "TWN": "Taiwan Province of China",
}
NAME_OVERRIDES = {
    "GBR": "United Kingdom",
    "USA": "United States",
    "KOR": "Korea, Republic of",
    "RUS": "Russian Federation",
    "IRN": "Iran",
    "VNM": "Vietnam",
    "BOL": "Bolivia",
    "VEN": "Venezuela",
    "TZA": "Tanzania",
    "SYR": "Syria",
    "MDA": "Moldova",
    "LAO": "Lao P.D.R.",
}


@st.cache_data(show_spinner=False)
def country_options() -> dict[str, str]:
    """Return {display label: code}. Euro area first, then countries A-Z."""
    names: dict[str, str] = {}
    for c in pycountry.countries:
        names[c.alpha_3] = NAME_OVERRIDES.get(c.alpha_3, getattr(c, "common_name", None) or c.name)
    names.update(EXTRA_COUNTRIES)
    labels = {f"{name} ({code})": code for code, name in names.items()}
    euro = f"Euro area ({EURO_AREA_CODE})"
    ordered = {euro: EURO_AREA_CODE}
    for label in sorted(k for k in labels if k != euro):
        ordered[label] = labels[label]
    return ordered


# ----------------------------------------------------------------------------
# IMF data retrieval
# ----------------------------------------------------------------------------
def _api_headers() -> dict[str, str]:
    headers = dict(HEADERS_JSON)
    # Optional: an IMF API subscription key can be added in Streamlit Secrets as
    # IMF_API_KEY = "...". Not normally required.
    try:
        key = st.secrets.get("IMF_API_KEY", None)
    except Exception:
        key = None
    if key:
        headers["Ocp-Apim-Subscription-Key"] = str(key)
    return headers


def parse_period(value: str) -> pd.Timestamp | None:
    """Convert IMF monthly periods ('2020-M01', '2020M01', '2020-01') to a month-start Timestamp."""
    if value is None:
        return None
    m = re.match(r"^\s*(\d{4})\s*-?\s*M?\s*(\d{1,2})", str(value))
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return pd.Timestamp(year=year, month=month, day=1)


def _to_float(v) -> float | None:
    if isinstance(v, list):
        v = v[0] if v else None
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if pd.notna(f) else None


def _parse_sdmx_json(payload: dict) -> pd.Series:
    """Parse an SDMX-JSON (1.0 or 2.0) data message into a monthly Series."""
    data = payload.get("data", payload)
    structure = data.get("structure")
    if structure is None:
        structures = data.get("structures") or []
        structure = structures[0] if structures else {}
    dims = structure.get("dimensions", {})

    obs_dims = dims.get("observation", [])
    time_values = []
    for d in obs_dims:
        if d.get("id") == "TIME_PERIOD" or d.get("role") == "time" or len(obs_dims) == 1:
            time_values = [v.get("value", v.get("id")) for v in d.get("values", [])]
            break

    points: dict[pd.Timestamp, float] = {}
    for ds in data.get("dataSets", []):
        for series in (ds.get("series") or {}).values():
            for idx, obs in (series.get("observations") or {}).items():
                i = int(str(idx).split(":")[-1])
                if i >= len(time_values):
                    continue
                t = parse_period(time_values[i])
                val = _to_float(obs)
                if t is not None and val is not None:
                    points[t] = val
    return pd.Series(points, dtype="float64").sort_index()


def _parse_sdmx_xml(text: str) -> pd.Series:
    """Parse SDMX-ML (structure-specific or generic) into a monthly Series."""
    root = ET.fromstring(text)
    points: dict[pd.Timestamp, float] = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag != "Obs":
            continue
        period = el.attrib.get("TIME_PERIOD")
        value = el.attrib.get("OBS_VALUE")
        # Generic format stores these in child elements.
        for child in el:
            ctag = child.tag.split("}")[-1]
            if ctag == "ObsDimension" and period is None:
                period = child.attrib.get("value")
            elif ctag == "ObsValue" and value is None:
                value = child.attrib.get("value")
        t = parse_period(period)
        val = _to_float(value)
        if t is not None and val is not None:
            points[t] = val
    return pd.Series(points, dtype="float64").sort_index()


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def fetch_imf_series(dataflow: str, key: str) -> pd.Series:
    """Download one IMF series. Returns an empty Series if the IMF has no data."""
    url = f"{BASE_URL}/data/dataflow/{AGENCY}/{dataflow}/+/{key}"
    params = {"dimensionAtObservation": "TIME_PERIOD", "attributes": "dsd", "measures": "all"}
    resp = requests.get(url, headers=_api_headers(), params=params, timeout=60)
    if resp.status_code == 404:
        return pd.Series(dtype="float64")  # no data for this key
    if resp.status_code in (400, 406):
        # Retry without optional query parameters / content negotiation.
        resp = requests.get(url, headers={"User-Agent": HEADERS_JSON["User-Agent"]}, timeout=60)
        if resp.status_code == 404:
            return pd.Series(dtype="float64")
    resp.raise_for_status()

    text = resp.text.strip()
    if not text:
        return pd.Series(dtype="float64")
    if text.startswith("{"):
        s = _parse_sdmx_json(json.loads(text))
    elif text.startswith("<"):
        s = _parse_sdmx_xml(text)
    else:  # CSV fallback
        df = pd.read_csv(io.StringIO(text))
        cols = {c.upper(): c for c in df.columns}
        if "TIME_PERIOD" not in cols or "OBS_VALUE" not in cols:
            return pd.Series(dtype="float64")
        t = df[cols["TIME_PERIOD"]].map(parse_period)
        s = pd.Series(pd.to_numeric(df[cols["OBS_VALUE"]], errors="coerce").values, index=t)
        s = s[s.index.notna()].dropna().sort_index()
    s = s[s.index >= START_MONTH]
    s = s[s > 0]  # CPI and FX must be positive
    return s[~s.index.duplicated(keep="last")]


def get_cpi(code: str) -> pd.Series:
    return fetch_imf_series("CPI", f"{code}.CPI._T.IX.M")


def get_fx_per_usd(code: str) -> pd.Series:
    """Local currency units per USD. The United States is 1.0 by definition."""
    if code == "USA":
        idx = pd.date_range(START_MONTH, pd.Timestamp.today().normalize(), freq="MS")
        return pd.Series(1.0, index=idx)
    return fetch_imf_series("ER", f"{code}.XDC_USD.PA_RT.M")


# ----------------------------------------------------------------------------
# Calculations
# ----------------------------------------------------------------------------
def build_dataset(fx1_usd: pd.Series, fx2_usd: pd.Series, cpi1: pd.Series, cpi2: pd.Series) -> pd.DataFrame:
    """Combine inputs and compute S(t) and RER(t) for every month."""
    df = pd.concat(
        {
            "FX1_per_USD": fx1_usd,
            "FX2_per_USD": fx2_usd,
            "CPI1": cpi1,
            "CPI2": cpi2,
        },
        axis=1,
    ).sort_index()
    df.index.name = "Month"
    df["S"] = df["FX2_per_USD"] / df["FX1_per_USD"]  # C2 per 1 unit of C1
    df["RER"] = df["S"] * df["CPI1"] / df["CPI2"]
    return df


def apply_sample(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, float]:
    """Restrict to the sample, compute RER* over valid months and the Real Index."""
    out = df.loc[(df.index >= start) & (df.index <= end)].copy()
    valid = out["RER"].notna()
    rer_star = float(out.loc[valid, "RER"].mean()) if valid.any() else float("nan")
    out["RER_star"] = rer_star
    out["Real_Index"] = 100.0 * out["RER"] / rer_star
    out["Valid_RER_month"] = valid
    return out, rer_star


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.title("💱 Real Exchange Rate App")
st.caption(
    "Monthly IMF data (CPI and exchange rates) from January 1976 where available. "
    "S(t) = units of Country 2 currency per 1 unit of Country 1 currency; "
    "RER(t) = S(t) × P1(t) / P2(t)."
)

options = country_options()
labels = list(options.keys())
default_1 = labels.index("United Kingdom (GBR)") if "United Kingdom (GBR)" in labels else 0
default_2 = labels.index("United States (USA)") if "United States (USA)" in labels else 1

c1, c2 = st.columns(2)
with c1:
    label1 = st.selectbox("Country 1 (base currency)", labels, index=default_1)
with c2:
    label2 = st.selectbox("Country 2 (quote currency)", labels, index=default_2)
code1, code2 = options[label1], options[label2]
name1, name2 = label1.rsplit(" (", 1)[0], label2.rsplit(" (", 1)[0]

if code1 == code2:
    st.warning("Please choose two different countries.")
    st.stop()

with st.spinner("Downloading IMF data…"):
    errors = []
    results = {}
    for tag, fn, code in [
        ("FX1", get_fx_per_usd, code1),
        ("FX2", get_fx_per_usd, code2),
        ("CPI1", get_cpi, code1),
        ("CPI2", get_cpi, code2),
    ]:
        try:
            results[tag] = fn(code)
        except Exception as exc:  # network / API failure
            errors.append(f"{tag} ({code}): {exc}")
            results[tag] = pd.Series(dtype="float64")

if errors:
    st.error("Some IMF downloads failed. The IMF API can be temporarily unavailable — try again shortly.\n\n" + "\n".join(f"- {e}" for e in errors))

missing = [
    msg
    for msg, s in [
        (f"exchange rate for {name1}", results["FX1"]),
        (f"exchange rate for {name2}", results["FX2"]),
        (f"CPI for {name1}", results["CPI1"]),
        (f"CPI for {name2}", results["CPI2"]),
    ]
    if s.empty
]
if missing:
    st.error("The IMF returned no monthly data for: " + "; ".join(missing) + ". Try another country pair.")
    st.stop()

full = build_dataset(results["FX1"], results["FX2"], results["CPI1"], results["CPI2"])
valid_months = full.index[full["RER"].notna()]
if len(valid_months) == 0:
    st.error("No months where both CPIs and both exchange rates are available. Try another pair.")
    st.stop()

first_valid, last_valid = valid_months.min(), valid_months.max()
month_list = list(pd.date_range(first_valid, last_valid, freq="MS"))
fmt = lambda d: d.strftime("%Y-%m")  # noqa: E731

st.subheader("Sample period")
s1, s2, s3 = st.columns([1, 1, 1])
with s1:
    start = st.selectbox("Start month", month_list, index=0, format_func=fmt)
with s2:
    end = st.selectbox("End month", month_list, index=len(month_list) - 1, format_func=fmt)
with s3:
    view = st.radio("Chart", ["Real", "Nominal", "Both"], index=2, horizontal=True)

if start > end:
    st.error("Start month must be on or before the end month.")
    st.stop()

sample, rer_star = apply_sample(full, start, end)
n_valid = int(sample["Valid_RER_month"].sum())
if n_valid == 0:
    st.error("No valid RER observations in the selected period.")
    st.stop()

s_label = f"S(t): {name2} currency per 1 {name1} currency"
m1, m2, m3, m4 = st.columns(4)
m1.metric("Valid months in sample", f"{n_valid:,}")
m2.metric("RER* (sample mean of RER)", f"{rer_star:,.4f}")
m3.metric("Mean Real Index", f"{sample['Real_Index'].mean():.2f}")
m4.metric(f"Latest S ({fmt(sample['S'].last_valid_index())})", f"{sample['S'].dropna().iloc[-1]:,.4f}")

if (sample.index.min() > start) or sample["RER"].isna().any():
    st.info(f"{len(sample) - n_valid} month(s) in the sample have missing inputs and are excluded from RER*.")

# --- Chart ------------------------------------------------------------------
if view == "Both":
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(x=sample.index, y=sample["Real_Index"], name="Real Index (avg = 100)", line=dict(color="#1f5fa8")),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(x=sample.index, y=sample["S"], name=s_label, line=dict(color="#d1652a")),
        secondary_y=True,
    )
    fig.add_hline(y=100, line_dash="dot", line_color="gray", secondary_y=False)
    fig.update_yaxes(title_text="Real Index (sample mean = 100)", secondary_y=False)
    fig.update_yaxes(title_text=f"{name2} per {name1} (actual units)", secondary_y=True, showgrid=False)
elif view == "Real":
    fig = go.Figure(go.Scatter(x=sample.index, y=sample["Real_Index"], name="Real Index", line=dict(color="#1f5fa8")))
    fig.add_hline(y=100, line_dash="dot", line_color="gray")
    fig.update_yaxes(title_text="Real Index (sample mean = 100)")
else:
    fig = go.Figure(go.Scatter(x=sample.index, y=sample["S"], name=s_label, line=dict(color="#d1652a")))
    fig.update_yaxes(title_text=f"{name2} per {name1} (actual units)")

fig.update_layout(
    title=f"{name1} vs {name2}: {view.lower()} exchange rate, {fmt(start)} to {fmt(end)}",
    hovermode="x unified",
    height=520,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    margin=dict(l=10, r=10, t=80, b=10),
)
fig.update_xaxes(title_text="Month")
st.plotly_chart(fig, use_container_width=True)

st.markdown(
    f"**Interpretation:** a higher Real Index means {name1}'s goods are relatively more expensive than "
    f"{name2}'s (a real appreciation of the {name1} currency) relative to the sample average."
)

# --- Table & download ------------------------------------------------------------
export = sample.reset_index()
export["Month"] = export["Month"].dt.strftime("%Y-%m")
export = export.rename(
    columns={
        "FX1_per_USD": f"FX1_{code1}_per_USD",
        "FX2_per_USD": f"FX2_{code2}_per_USD",
        "CPI1": f"P1_CPI_{code1}",
        "CPI2": f"P2_CPI_{code2}",
        "S": f"S_{code2}_per_{code1}",
    }
)
export.insert(1, "Country1", code1)
export.insert(2, "Country2", code2)

with st.expander("Data table (selected sample)"):
    st.dataframe(export, use_container_width=True, hide_index=True)

st.download_button(
    "⬇️ Download CSV (selected sample)",
    data=export.to_csv(index=False).encode("utf-8"),
    file_name=f"rer_{code1}_{code2}_{fmt(start)}_{fmt(end)}.csv",
    mime="text/csv",
)

with st.expander("Method and data sources"):
    st.markdown(
        f"""
- **Exchange rates:** IMF `ER` dataflow, key `{{ISO3}}.XDC_USD.PA_RT.M` (period-average, local currency per USD).
  The U.S. rate is set to 1. S(t) = FX2/FX1 = {name2} currency per 1 {name1} currency.
- **Prices:** IMF `CPI` dataflow, key `{{ISO3}}.CPI._T.IX.M` (all-items CPI index).
- **RER(t)** = S(t) × P1(t) / P2(t). **RER\\*** = average RER over valid months in the selected sample.
  **Real Index(t)** = 100 × RER(t) / RER\\*, so it averages exactly 100 over the sample.
- Euro area uses IMF code `G163`. Data begin January 1976 where available; gaps are left as missing.
- Source: IMF SDMX 3.0 API, `{BASE_URL}`.
