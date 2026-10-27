"""
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
EXTRA_COUNTRI
