import io
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------
# International Cost of Capital App
# Methodology: Sergio Rebelo, International Valuation lecture, slides
# 573-586 (uploaded course packet).
# ---------------------------------------------------------------------

st.set_page_config(
    page_title="International Cost of Capital",
    page_icon="🌐",
    layout="wide",
)

DAMODARAN_BETA_URL = "https://www.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
DAMODARAN_BETA_HTML_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html"
DAMODARAN_RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
OECD_TAX_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_"
    "9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_"
    "440f9286.html#chapter-d1e2342-08e74a33fb"
)
TRADING_ECONOMICS_URL = "https://tradingeconomics.com/country-list/government-bond-10y"
TRADING_ECONOMICS_BONDS_URL = "https://tradingeconomics.com/bonds"

DEFAULT_ERP = 6.5
DEFAULT_DEBT_SHARE = 30.0
DEFAULT_TAX_COUNTRY = "United States"
DEFAULT_INDUSTRY = "Software (System & Application)"

# No silent market-data fallbacks: if a live source is unavailable, the
# user must provide a clearly labelled manual override.
DEFAULT_BBB_SPREAD = None

COUNTRY_ALIASES = {
    "United States": ["United States", "United States of America", "USA", "US"],
    "United Kingdom": ["United Kingdom", "UK"],
    "South Korea": ["South Korea", "Korea"],
    "Türkiye": ["Türkiye", "Turkey"],
    "Czechia": ["Czech Republic", "Czechia"],
    "United Arab Emirates": ["UAE", "United Arab Emirates"],
    "Hong Kong": ["Hong Kong"],
}

def pct(x):
    return f"{x:.4f}%"

def safe_float(value):
    try:
        if pd.isna(value):
            return None
        if isinstance(value, str):
            value = value.replace("%", "").replace(",", "").strip()
            if value in {"", "-", "—", "N/A", "n/a"}:
                return None
        return float(value)
    except (TypeError, ValueError):
        return None

def get_html(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CostOfCapitalStudentApp/1.0)"
    }
    response = requests.get(url, headers=headers, timeout=25)
    response.raise_for_status()
    return response.text

def _normalize_column_name(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()


def _beta_table_from_html(html):
    """Parse the published Damodaran HTML table using named columns."""
    tables = pd.read_html(io.StringIO(html))
    for table in tables:
        # Damodaran's published table has a one-row header with these labels.
        cols = {_normalize_column_name(c): c for c in table.columns}
        industry_col = next((v for k, v in cols.items() if k == "industry name"), None)
        beta_col = next((v for k, v in cols.items() if k == "unlevered beta"), None)
        if industry_col is None or beta_col is None:
            continue
        df = table[[industry_col, beta_col]].copy()
        df.columns = ["Industry", "Unlevered Beta"]
        df["Industry"] = df["Industry"].astype(str).str.strip()
        df["Unlevered Beta"] = df["Unlevered Beta"].apply(safe_float)
        df = df.dropna(subset=["Unlevered Beta"])
        df = df[df["Industry"].ne("") & ~df["Industry"].str.lower().isin(["nan", "industry name"])]
        if len(df) >= 40 and df["Industry"].is_unique:
            return df.sort_values("Industry").reset_index(drop=True)
    raise ValueError("Damodaran HTML page did not contain a complete Industry Name / Unlevered Beta table.")


def fetch_damodaran_betas():
    """Use the specified XLS first, with the same publisher's HTML table as a parser fallback."""
    vintage = "Vintage not identified"
    try:
        html = get_html(DAMODARAN_BETA_HTML_URL)
        vintage_match = re.search(r"Date of Analysis:\s*Data used is as of\s*([^<\n]+)", html, re.I)
        if vintage_match:
            vintage = vintage_match.group(1).strip()
    except Exception:
        html = None

    xls_error = None
    try:
        response = requests.get(DAMODARAN_BETA_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=40)
        response.raise_for_status()
        if len(response.content) < 10_000:
            raise ValueError("Downloaded beta workbook is unexpectedly small.")
        workbook = pd.ExcelFile(io.BytesIO(response.content), engine="xlrd")
        candidates = []
        for sheet in workbook.sheet_names:
            raw = pd.read_excel(workbook, sheet_name=sheet, header=None)
            for header_row in range(min(25, len(raw))):
                vals = [_normalize_column_name(v) for v in raw.iloc[header_row].tolist()]
                industry_col = next((i for i, v in enumerate(vals) if v == "industry name"), None)
                beta_col = next((i for i, v in enumerate(vals) if v == "unlevered beta"), None)
                if industry_col is None or beta_col is None:
                    continue
                df = raw.iloc[header_row + 1:, [industry_col, beta_col]].copy()
                df.columns = ["Industry", "Unlevered Beta"]
                df["Industry"] = df["Industry"].astype(str).str.strip()
                df["Unlevered Beta"] = df["Unlevered Beta"].apply(safe_float)
                df = df.dropna(subset=["Unlevered Beta"])
                df = df[df["Industry"].ne("") & ~df["Industry"].str.lower().isin(["nan", "industry name"])]
                if len(df) >= 40 and df["Industry"].is_unique:
                    candidates.append(df)
        if candidates:
            return max(candidates, key=len).sort_values("Industry").reset_index(drop=True), vintage
        raise ValueError("XLS did not contain a complete industry/unlevered-beta table.")
    except Exception as exc:
        xls_error = exc

    if html is not None:
        try:
            return _beta_table_from_html(html), vintage + " (HTML table fallback)"
        except Exception as html_exc:
            raise ValueError(f"XLS parser failed ({xls_error}); HTML parser failed ({html_exc}).") from html_exc
    raise ValueError(f"Damodaran XLS unavailable or unreadable ({xls_error}); HTML fallback also unavailable.")


def fetch_damodaran_bbb_spread():
    """Extract the Baa2/BBB spread from the large non-financial service-firm table."""
    html = get_html(DAMODARAN_RATINGS_URL)
    soup = BeautifulSoup(html, "html.parser")
    target = None
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) >= 4 and re.search(r"\bBaa2\s*/\s*BBB\b", " ".join(cells), re.I):
                target = cells
                break
        if target:
            break
    if not target:
        # HTML table parser fallback, still requiring the exact rating label.
        for table in pd.read_html(io.StringIO(html)):
            for _, row in table.iterrows():
                vals = [str(v).strip() for v in row.tolist()]
                if any(re.search(r"\bBaa2\s*/\s*BBB\b", v, re.I) for v in vals):
                    # First spread column is the fourth cell in the large non-financial table.
                    if len(vals) >= 4:
                        target = vals[:4]
                        break
            if target:
                break
    if not target or len(target) < 4:
        raise ValueError("Exact Baa2/BBB row in the large non-financial service-firm table was not found.")
    spread = safe_float(target[3])
    if spread is None or not 0.0 < spread < 10.0:
        raise ValueError(f"BBB spread failed range validation: {target[3]}")
    vintage_match = re.search(r"Date of Analysis:\s*Data used is as of\s*([^<\n]+)", html, re.I)
    vintage = vintage_match.group(1).strip() if vintage_match else "Vintage not identified"
    return spread, "Damodaran: large non-financial service firms, Baa2/BBB", vintage


def fetch_oecd_tax_rates():
    """Fetch combined statutory corporate income tax rates from OECD SDMX.

    The key follows the query generated by the OECD Data Explorer for annual,
    statutory, general-government observations. The measure dimension is left
    unfiltered in the URL and then explicitly filtered to CIT_C in the response.
    This avoids shifting a dimension into the wrong position in the SDMX key.
    """
    api_url = (
        "https://sdmx.oecd.org/public/rest/data/"
        "OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,2.0/"
        ".A..ST..S13...?startPeriod=2000&endPeriod=2026&"
        "dimensionAtObservation=AllDimensions&format=csvfilewithlabels"
    )
    headers = {
        "Accept": "text/csv",
        "User-Agent": "Mozilla/5.0 (compatible; InternationalCostOfCapital/1.0)",
    }
    response = requests.get(api_url, headers=headers, timeout=45)
    response.raise_for_status()
    if len(response.content) < 100:
        raise ValueError("OECD SDMX response was unexpectedly small.")
    content_type = response.headers.get("Content-Type", "").lower()
    if "html" in content_type or response.content.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise ValueError("OECD returned HTML instead of SDMX CSV (access block or invalid API request).")
    try:
        df = pd.read_csv(io.BytesIO(response.content), sep=None, engine="python")
    except Exception as exc:
        raise ValueError(f"OECD response was not a readable SDMX CSV: {exc}") from exc
    # Normalize headers, then drop duplicate names. Some SDMX CSV responses
    # include both code and label columns that normalize to the same name;
    # pandas df["NAME"] would then return a DataFrame, not a Series.
    normalized = [re.sub(r"[^A-Z0-9]+", "_", str(c).strip().upper()).strip("_") for c in df.columns]
    df.columns = normalized
    df = df.loc[:, ~pd.Index(df.columns).duplicated(keep="first")].copy()

    def col(*names):
        return next((name for name in names if name in df.columns), None)

    value_col = col("OBS_VALUE", "OBSERVATION_VALUE", "VALUE")
    time_col = col("TIME_PERIOD", "TIME")
    area_col = col("REF_AREA", "REFERENCE_AREA", "REFERENCE_AREA_LABEL", "REF_AREA_LABEL")
    if value_col is None or time_col is None or area_col is None:
        raise ValueError(
            "OECD SDMX response missing required country/time/value fields. "
            f"Columns returned: {', '.join(df.columns)}"
        )

    # Validate the requested statistical concept whenever dimensions are present.
    measure_col = col("MEASURE", "MEASURE_LABEL", "MEASURE_NAME")
    if measure_col is None:
        raise ValueError("OECD response did not expose the MEASURE dimension; refusing to assume CIT_C.")
    values = df[measure_col].astype(str).str.strip()
    keep = values.str.upper().eq("CIT_C") | values.str.contains(
        r"combined.*statutory.*corporate income tax|statutory.*corporate income tax.*combined",
        case=False, na=False,
    )
    if not keep.any():
        raise ValueError("OECD response contains no combined corporate income tax (CIT_C) observations.")
    df = df[keep].copy()

    target_col = col("TARGETING", "TARGETING_LABEL", "TARGETING_NAME")
    if target_col is None:
        raise ValueError("OECD response did not expose the TARGETING dimension; refusing to assume statutory rates.")
    values = df[target_col].astype(str).str.strip()
    df = df[values.str.upper().eq("ST") | values.str.fullmatch(r"statutory", case=False, na=False)].copy()
    sector_col = col("INSTITUTIONAL_SECTOR", "SECTOR", "INSTITUTIONAL_SECTOR_LABEL", "SECTOR_LABEL")
    if sector_col is None:
        raise ValueError("OECD response did not expose the institutional-sector dimension; refusing to assume S13.")
    values = df[sector_col].astype(str).str.strip()
    df = df[values.str.upper().eq("S13") | values.str.fullmatch(r"general government", case=False, na=False)].copy()
    if df.empty:
        raise ValueError("OECD response had no observations for statutory CIT_C at general-government level (S13).")

    area_label_col = col("REF_AREA_LABEL", "REF_AREA_NAME", "REFERENCE_AREA_LABEL", "REFERENCE_AREA_NAME", "REFERENCE_AREA")
    df["Country"] = (df[area_label_col] if area_label_col else df[area_col]).astype(str).str.strip()
    df["Tax Rate"] = pd.to_numeric(df[value_col], errors="coerce")
    df["Year"] = pd.to_numeric(df[time_col].astype(str).str.extract(r"(\d{4})")[0], errors="coerce")
    df = df.dropna(subset=["Country", "Tax Rate", "Year"])
    df = df[df["Tax Rate"].between(0, 60)]
    df = df[~df["Country"].str.lower().isin(["nan", ""])]
    if df.empty:
        raise ValueError("OECD returned no valid statutory corporate tax observations after validation.")

    # One latest annual observation per country. If OECD returns multiple rows
    # for the same country/year, keep only the exact S13/CIT_C/ST series above.
    df["Year"] = df["Year"].astype(int)
    latest = df.sort_values(["Year"]).groupby("Country", as_index=False).tail(1).copy()
    latest["Observation date"] = latest["Year"].astype(str)
    out = latest[["Country", "Tax Rate", "Year", "Observation date"]].drop_duplicates("Country")
    if len(out) < 20:
        raise ValueError(
            f"OECD returned only {len(out)} countries after strict series validation; "
            "refusing to present a partial dataset as complete."
        )
    return out.sort_values("Country").reset_index(drop=True)


def normalize_country(name):
    name = str(name).strip()
    for canonical, aliases in COUNTRY_ALIASES.items():
        if name.casefold() in [a.casefold() for a in aliases]:
            return canonical
    return name


def _extract_te_table(tables):
    """Normalize supported Trading Economics country-list and bonds table layouts."""
    records = []
    for table in tables:
        if isinstance(table.columns, pd.MultiIndex):
            table.columns = [" ".join(str(x) for x in col if str(x) != "nan").strip() for col in table.columns]
        cols = [re.sub(r"\s+", " ", str(c)).strip().lower() for c in table.columns]

        country_i = next((i for i, c in enumerate(cols) if c in {"country", "major10y", "major 10y"}), None)
        if country_i is None and cols and (cols[0] == "" or cols[0].startswith("unnamed")):
            country_i = 0
        yield_i = next((i for i, c in enumerate(cols) if c in {"last", "yield", "10y", "10-year yield"}), None)
        date_i = next((i for i, c in enumerate(cols) if c in {"reference", "date", "updated"}), None)
        if country_i is None or yield_i is None or date_i is None:
            continue
        for _, row in table.iterrows():
            country_name = normalize_country(row.iloc[country_i])
            value = safe_float(row.iloc[yield_i])
            reference = str(row.iloc[date_i]).strip()
            if not country_name or country_name.lower() in {"nan", "country", "report", "country list"}:
                continue
            if value is None or not -5 <= value <= 50:
                continue
            if not re.match(r"^(?:[A-Za-z]{3}/\d{2}|\d{4}-\d{2}-\d{2})$", reference):
                continue
            records.append({
                "Country": country_name,
                "Yield": float(value),
                "Observation date": reference,
                "Source": "Trading Economics: Government Bond 10y by Country",
            })
    return records


def fetch_trading_economics_yields():
    """Scrape TE's dedicated country list, then its bonds overview as a fallback."""
    errors = []
    for url in (TRADING_ECONOMICS_URL, TRADING_ECONOMICS_BONDS_URL):
        try:
            html = get_html(url)
            tables = pd.read_html(io.StringIO(html))
            records = _extract_te_table(tables)
            if records:
                df = pd.DataFrame(records).drop_duplicates("Country", keep="last")
                if find_country_value(df, "United States", "Country", "Yield") is None:
                    raise ValueError("Parsed yield table did not include the U.S. benchmark.")
                return df.sort_values("Country").reset_index(drop=True)
            errors.append(f"{url}: no recognized country/yield/date table")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise ValueError("Trading Economics parsing failed. " + " | ".join(errors))


def find_country_value(df, country, country_col, value_col):
    aliases = COUNTRY_ALIASES.get(country, [country])
    if df is None or df.empty:
        return None
    for alias in aliases:
        rows = df[df[country_col].astype(str).str.strip().str.casefold() == alias.casefold()]
        if not rows.empty:
            return safe_float(rows.iloc[0][value_col])
    return None

def find_country_year(df, country):
    if df is None or df.empty or "Year" not in df.columns:
        return None
    aliases = COUNTRY_ALIASES.get(country, [country])
    for alias in aliases:
        rows = df[df["Country"].astype(str).str.strip().str.casefold() == alias.casefold()]
        if not rows.empty:
            return int(rows.iloc[0]["Year"])
    return None


def find_country_observation_date(df, country):
    if df is None or df.empty or "Observation date" not in df.columns:
        return None
    aliases = COUNTRY_ALIASES.get(country, [country])
    for alias in aliases:
        rows = df[df["Country"].astype(str).str.strip().str.casefold() == alias.casefold()]
        if not rows.empty:
            return str(rows.iloc[0]["Observation date"])
    return None


def calculate_costs(beta_u, debt_share_pct, tax_pct, erp_pct, rf_us_pct,
                    rf_foreign_pct, usd_spread_pct, foreign_spread_pct=None):
    """All inputs are percentage points; outputs are decimal rates."""
    d = debt_share_pct / 100.0
    tau = tax_pct / 100.0
    erp = erp_pct / 100.0
    rf_us = rf_us_pct / 100.0
    rf_foreign = rf_foreign_pct / 100.0
    spread_us = usd_spread_pct / 100.0
    if not 0 <= d < 1:
        raise ValueError("Debt share must be at least 0% and less than 100%.")
    if beta_u <= 0:
        raise ValueError("Unlevered beta must be positive.")
    if not 0 <= tau < 1:
        raise ValueError("Tax rate must be at least 0% and less than 100%.")
    if min(rf_us, rf_foreign, spread_us, erp) <= -1:
        raise ValueError("Rates must be greater than -100%.")
    de = d / (1.0 - d)
    beta_e = beta_u * (1.0 + (1.0 - tau) * de)
    re_us = rf_us + beta_e * erp
    rd_us = rf_us + spread_us
    re_foreign = (1.0 + re_us) * (1.0 + rf_foreign) / (1.0 + rf_us) - 1.0
    spread_foreign = (
        float(foreign_spread_pct) / 100.0 if foreign_spread_pct is not None
        else spread_us * (1.0 + rf_foreign) / (1.0 + rf_us)
    )
    if spread_foreign < 0:
        raise ValueError("Foreign borrowing spread cannot be negative.")
    rd_foreign = rf_foreign + spread_foreign
    wacc_us = (1.0 - d) * re_us + d * rd_us * (1.0 - tau)
    wacc_foreign = (1.0 - d) * re_foreign + d * rd_foreign * (1.0 - tau)
    return {
        "de_ratio": de, "beta_e": beta_e, "re_us": re_us, "rd_us": rd_us,
        "after_tax_rd_us": rd_us * (1.0 - tau), "wacc_us": wacc_us,
        "spread_foreign": spread_foreign, "re_foreign": re_foreign,
        "rd_foreign": rd_foreign,
        "after_tax_rd_foreign": rd_foreign * (1.0 - tau),
        "wacc_foreign": wacc_foreign,
    }

def pct_from_decimal(x):
    return 100.0 * x

@st.cache_data(ttl=3600, show_spinner=False)
def load_all_data(refresh_token):
    """Fetch source datasets. refresh_token changes when user presses Refresh."""
    result = {
         "betas": None, "beta_vintage": None, "taxes": None, "yields": None,
        "bbb_spread": None, "bbb_source": None, "bbb_date": None,
        "errors": [],
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    try:
        result["betas"], result["beta_vintage"] = fetch_damodaran_betas()
    except Exception as exc:
        result["errors"].append(f"Damodaran industry betas: {exc}")
    try:
        result["taxes"] = fetch_oecd_tax_rates()
    except Exception as exc:
        result["errors"].append(f"OECD corporate tax rates: {exc}")
    try:
        result["yields"] = fetch_trading_economics_yields()
    except Exception as exc:
        result["errors"].append(f"Trading Economics yields: {exc}")
    try:
        spread, source, vintage = fetch_damodaran_bbb_spread()
        result["bbb_spread"] = spread
        result["bbb_source"] = source
        result["bbb_date"] = vintage
    except Exception as exc:
        result["errors"].append(f"Damodaran BBB spread: {exc}")
    return result

# ---------------------------------------------------------------------
# Header and data refresh
# ---------------------------------------------------------------------
st.title("🌐 International Cost of Capital")
st.caption("Compare project cost of equity and WACC in USD and a selected foreign currency.")

if "refresh_token" not in st.session_state:
    st.session_state.refresh_token = 0

top_left, top_right = st.columns([4, 1])
with top_left:
    st.markdown(
        "This calculator follows the international valuation methodology in "
        "the course lecture packet (slides 573–586). All calculations retain "
        "full precision; displayed results are rounded."
    )
with top_right:
    if st.button("↻ Refresh source data", use_container_width=True):
        st.session_state.refresh_token += 1
        st.cache_data.clear()
        st.rerun()

with st.spinner("Loading the latest available source data..."):
    data = load_all_data(st.session_state.refresh_token)

if data["errors"]:
    with st.expander("Data-source status and fallback information", expanded=True):
        st.warning("One or more live sources could not be parsed. The app will not substitute guessed values. Check each source error and use a manual override only when you have a verified observation.")
        for err in data["errors"]:
            st.write("• " + err)
        st.caption(
            "Fallback values, where shown, are clearly identified as lecture-packet "
            "illustrations and are not represented as live market observations."
        )
else:
    st.success(f"Source data loaded. Last refresh: {data['fetched_at']}")

# ---------------------------------------------------------------------
# Input controls
# ---------------------------------------------------------------------
st.header("1. Project assumptions")

beta_df = data["betas"]
if beta_df is not None and not beta_df.empty:
    industry_options = beta_df["Industry"].tolist()
else:
    industry_options = [DEFAULT_INDUSTRY]

default_industry_index = (
    industry_options.index(DEFAULT_INDUSTRY)
    if DEFAULT_INDUSTRY in industry_options else 0
)

# Fixed selector choices: the UI must remain usable even if one or more
# providers are temporarily unavailable. Data availability is shown separately.
COUNTRY_CHOICES = [
    "Argentina", "Australia", "Austria", "Belgium", "Brazil", "Canada",
    "Chile", "China", "Colombia", "Croatia", "Czech Republic", "Denmark",
    "Finland", "France", "Germany", "Greece", "Hong Kong", "Hungary",
    "Iceland", "India", "Indonesia", "Ireland", "Israel", "Italy", "Japan",
    "Malaysia", "Mexico", "Netherlands", "New Zealand", "Norway", "Pakistan",
    "Peru", "Philippines", "Poland", "Portugal", "Romania", "Russia",
    "Saudi Arabia", "Singapore", "South Africa", "South Korea", "Spain",
    "Sweden", "Switzerland", "Taiwan", "Thailand", "Türkiye",
    "United Arab Emirates", "United Kingdom", "United States", "Vietnam",
]
available_countries = set(COUNTRY_CHOICES)
if data["taxes"] is not None:
    available_countries |= set(data["taxes"]["Country"].astype(str))
if data["yields"] is not None:
    available_countries |= set(data["yields"]["Country"].astype(str))

with st.sidebar:
    st.header("Inputs")
    st.subheader("Project selection")
    industry = st.selectbox("Industry", industry_options, index=default_industry_index)
    country_options = sorted(set(available_countries) - {"United States"})
    country = st.selectbox("Foreign target country", country_options, index=country_options.index("Colombia") if "Colombia" in country_options else 0)
    st.caption("The U.S. is the fixed domestic benchmark; select the foreign country to compare against USD.")

    st.subheader("Capital structure and risk premium")
    erp_pct = st.number_input(
        "Equity risk premium (%)", min_value=0.0, max_value=30.0,
        value=DEFAULT_ERP, step=0.1, format="%.2f"
    )
    debt_share_pct = st.slider(
        "Target debt share (%)", min_value=0.0, max_value=90.0,
        value=float(DEFAULT_DEBT_SHARE), step=1.0
    )

    st.subheader("Project tax rate")
    tax_source_choice = st.radio(
        "Apply which country's corporate tax rate to BOTH currency calculations?",
        ["United States", "Selected country"],
        index=0,
    )

# Get fetched beta and allow override.
auto_beta = None
if beta_df is not None:
    match = beta_df[beta_df["Industry"] == industry]
    if not match.empty:
        auto_beta = safe_float(match.iloc[0]["Unlevered Beta"])

with st.sidebar:
    st.subheader("Manual overrides")
    beta_override_on = st.checkbox("Override unlevered beta", value=False)
    beta_override = st.number_input(
        "Unlevered beta", min_value=0.01, max_value=5.0,
        value=float(auto_beta if auto_beta is not None else 0.80),
        step=0.01, format="%.4f", disabled=not beta_override_on
    )

# Tax lookup
auto_tax_us = find_country_value(data["taxes"], "United States", "Country", "Tax Rate") if data["taxes"] is not None else None
auto_tax_foreign = find_country_value(data["taxes"], country, "Country", "Tax Rate") if data["taxes"] is not None else None
# Missing live observations remain missing; manual override is required.

with st.sidebar:
    tax_override_on = st.checkbox("Override selected project tax rate", value=False)
    selected_auto_tax = auto_tax_us if tax_source_choice == "United States" else auto_tax_foreign
    tax_override = st.number_input(
        "Manual project tax rate (%)", min_value=0.0, max_value=60.0,
        value=float(selected_auto_tax if selected_auto_tax is not None else 25.0),
        step=0.1, format="%.2f", disabled=not tax_override_on
    )

# Yield lookup
auto_us_yield = find_country_value(data["yields"], "United States", "Country", "Yield") if data["yields"] is not None else None
auto_foreign_yield = find_country_value(data["yields"], country, "Country", "Yield") if data["yields"] is not None else None
# Missing live observations remain missing; manual override is required.
bbb_auto = data["bbb_spread"]

with st.sidebar:
    st.subheader("Market data overrides")
    us_yield_override_on = st.checkbox("Override U.S. 10-year yield", value=False)
    us_yield_override = st.number_input(
        "U.S. 10-year yield (%)", min_value=-5.0, max_value=50.0,
        value=float(auto_us_yield if auto_us_yield is not None else 4.0),
        step=0.05, format="%.3f", disabled=not us_yield_override_on
    )
    foreign_yield_override_on = st.checkbox("Override selected-country 10-year yield", value=False)
    foreign_yield_override = st.number_input(
        f"{country} 10-year yield (%)", min_value=-5.0, max_value=50.0,
        value=float(auto_foreign_yield if auto_foreign_yield is not None else 4.0),
        step=0.05, format="%.3f", disabled=not foreign_yield_override_on
    )
    usd_spread_override_on = st.checkbox("Override USD BBB borrowing spread", value=False)
    usd_spread_override = st.number_input(
        "USD BBB spread (%)", min_value=0.0, max_value=30.0,
        value=float(bbb_auto if bbb_auto is not None else 1.5), step=0.05, format="%.3f",
        disabled=not usd_spread_override_on
    )
    foreign_spread_override_on = st.checkbox("Override foreign-currency BBB spread", value=False)
    # Default value shown is the automatically converted spread.
    _rf_us_for_display = (us_yield_override if us_yield_override_on else (auto_us_yield or 0.0)) / 100
    _rf_for_for_display = (foreign_yield_override if foreign_yield_override_on else (auto_foreign_yield or 0.0)) / 100
    _usd_spread_for_display = (usd_spread_override if usd_spread_override_on else (bbb_auto if bbb_auto is not None else 1.5)) / 100
    auto_foreign_spread_display = (
        _usd_spread_for_display * (1 + _rf_for_for_display) / (1 + _rf_us_for_display)
    ) * 100
    foreign_spread_override = st.number_input(
        "Foreign BBB spread (%)", min_value=0.0, max_value=40.0,
        value=float(auto_foreign_spread_display),
        step=0.05, format="%.3f",
        disabled=not foreign_spread_override_on
    )

# Resolve actual inputs and source labels.
beta_u = beta_override if beta_override_on else auto_beta
beta_source = "Manual override" if beta_override_on else (
    "Damodaran U.S. industry betas" if auto_beta is not None else "Unavailable — manual override required"
)

tax_us_source = "OECD SDMX dataset" if data["taxes"] is not None and find_country_value(data["taxes"], "United States", "Country", "Tax Rate") is not None else "Unavailable — manual override required"
tax_foreign_source = "OECD SDMX dataset" if data["taxes"] is not None and find_country_value(data["taxes"], country, "Country", "Tax Rate") is not None else "Unavailable — manual override required"

if tax_override_on:
    tax_pct = tax_override
    tax_source = "Manual override"
else:
    tax_pct = auto_tax_us if tax_source_choice == "United States" else auto_tax_foreign
    tax_source = tax_us_source if tax_source_choice == "United States" else tax_foreign_source

rf_us_pct = us_yield_override if us_yield_override_on else auto_us_yield
rf_foreign_pct = foreign_yield_override if foreign_yield_override_on else auto_foreign_yield
spread_us_pct = usd_spread_override if usd_spread_override_on else bbb_auto

if foreign_spread_override_on:
    spread_foreign_pct = foreign_spread_override
    spread_foreign_source = "Manual override"
else:
    spread_foreign_pct = (
        spread_us_pct * (1 + rf_foreign_pct / 100) / (1 + rf_us_pct / 100)
        if spread_us_pct is not None and rf_foreign_pct is not None and rf_us_pct is not None
        else None
    )
    spread_foreign_source = "Automatically converted from USD spread using lecture formula"

# Main content: data inputs and provenance
st.subheader("Source data and selected assumptions")
source_rows = [
    ["Industry unlevered beta", beta_u, beta_source, "Damodaran beta workbook", f"Damodaran vintage: {data.get('beta_vintage') or 'Unavailable'}"],
    ["Project corporate tax rate", tax_pct, f"{tax_source}; chosen: {tax_source_choice}", "OECD statutory corporate tax rates", f"U.S. year: {find_country_year(data['taxes'], 'United States')}; selected-country year: {find_country_year(data['taxes'], country)}"],
    ["U.S. 10-year government yield", rf_us_pct, "Trading Economics" if data["yields"] is not None and not us_yield_override_on else ("Manual override" if us_yield_override_on else "Unavailable"), "Trading Economics bonds", (find_country_observation_date(data["yields"], "United States") or "Unavailable")],
    [f"{country} 10-year government yield", rf_foreign_pct, "Trading Economics" if data["yields"] is not None and not foreign_yield_override_on else ("Manual override" if foreign_yield_override_on else "Unavailable"), "Trading Economics bonds", (find_country_observation_date(data["yields"], country) or "Unavailable")],
    ["USD BBB borrowing spread", spread_us_pct, "Damodaran ratings page" if data["bbb_spread"] is not None and not usd_spread_override_on else ("Manual override" if usd_spread_override_on else "Unavailable"), "Damodaran Baa2/BBB", data['bbb_date'] or f"Retrieved {data['fetched_at']}"],
    ["Foreign BBB borrowing spread", spread_foreign_pct, spread_foreign_source, "Converted USD spread or manual input", "Calculated from current selected yields unless manually overridden"],
    ["Equity risk premium", erp_pct, "User assumption (default 6.5%)", "User input", "User assumption"],
    ["Target debt share", debt_share_pct, "User assumption (default 30%)", "User input", "User assumption"],
]
source_df = pd.DataFrame(source_rows, columns=["Input", "Value (%) / beta", "Selected value source", "Reference", "Date / vintage"])
source_df["Value (%) / beta"] = source_df["Value (%) / beta"].apply(
    lambda x: "Unavailable" if x is None else (f"{x:.4f}" if isinstance(x, (int, float)) else str(x))
)
st.dataframe(source_df, use_container_width=True, hide_index=True)

with st.expander("Data-source links and dates"):
    st.markdown(f"- [Damodaran industry betas]({DAMODARAN_BETA_URL})")
    st.markdown(f"- [Damodaran ratings / BBB spread]({DAMODARAN_RATINGS_URL})")
    st.markdown(f"- [OECD Corporate Tax Statistics 2024 report]({OECD_TAX_URL})")
    st.markdown("- [OECD Corporate Income Tax Rates Database (live dataset)](https://data-explorer.oecd.org/vis?df%5Bag%5D=OECD.CTP.TPS&df%5Bds%5D=dsDisseminateFinalDMZ&df%5Bid%5D=DSD_TAX_CIT%40DF_CIT&df%5Bvs%5D=2.0)")
    st.markdown(f"- [Trading Economics 10-year government yields]({TRADING_ECONOMICS_URL})")
    st.write(f"Fetch attempt: {data['fetched_at']}")
    st.write(f"Damodaran beta data vintage: {data.get('beta_vintage') or 'Unavailable'}")
    st.write(f"Damodaran BBB spread data vintage: {data['bbb_date'] or 'Unavailable'}")
    st.write("Trading Economics yields retain the observation date parsed from the source table.")
    st.write("OECD statutory tax rates retain the latest available annual observation returned by the OECD SDMX dataset.")

# Guard missing data
if beta_u is None or tax_pct is None or rf_us_pct is None or rf_foreign_pct is None or spread_us_pct is None or (foreign_spread_override_on and spread_foreign_pct is None):
    st.error("A required source observation is unavailable. Enter a manual override for each missing input before calculating. No illustrative fallback values are used.")
    st.stop()

if not (0 <= debt_share_pct < 100):
    st.error("Debt share must be less than 100%.")
    st.stop()

# Calculate the USD baseline once, then calculate foreign currency with optional spread override.
try:
    # The calculation function uses a single spread for both currencies by default.
    base_results = calculate_costs(
        beta_u, debt_share_pct, tax_pct, erp_pct,
        rf_us_pct, rf_foreign_pct, spread_us_pct,
        foreign_spread_pct if foreign_spread_override_on else None,
    )
except Exception as exc:
    st.error(f"Unable to calculate: {exc}")
    st.stop()

# ---------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------
st.header("2. Cost of capital comparison")
st.caption(f"Project tax rate applied to both currencies: {tax_pct:.2f}% ({tax_source_choice}).")

usd_col, foreign_col = st.columns(2)
with usd_col:
    st.subheader("🇺🇸 U.S. dollars (USD)")
    st.metric("Cost of equity", pct_from_decimal(base_results["re_us"]))
    st.metric("Pre-tax cost of debt", pct_from_decimal(base_results["rd_us"]))
    st.metric("After-tax cost of debt", pct_from_decimal(base_results["after_tax_rd_us"]))
    st.metric("WACC", pct_from_decimal(base_results["wacc_us"]))
with foreign_col:
    st.subheader(f"🌍 {country} currency")
    st.metric("Cost of equity", pct_from_decimal(base_results["re_foreign"]))
    st.metric("Pre-tax cost of debt", pct_from_decimal(base_results["rd_foreign"]))
    st.metric("After-tax cost of debt", pct_from_decimal(base_results["after_tax_rd_foreign"]))
    st.metric("WACC", pct_from_decimal(base_results["wacc_foreign"]))

st.subheader("Calculation bridge")
bridge_df = pd.DataFrame([
    ["Debt / equity (D/E)", base_results["de_ratio"], base_results["de_ratio"]],
    ["Relevered equity beta", base_results["beta_e"], base_results["beta_e"]],
    ["Risk-free yield", rf_us_pct / 100, rf_foreign_pct / 100],
    ["Borrowing spread", spread_us_pct / 100, spread_foreign_pct / 100],
    ["Cost of equity", base_results["re_us"], base_results["re_foreign"]],
    ["Pre-tax cost of debt", base_results["rd_us"], base_results["rd_foreign"]],
    ["After-tax cost of debt", base_results["after_tax_rd_us"], base_results["after_tax_rd_foreign"]],
    ["WACC", base_results["wacc_us"], base_results["wacc_foreign"]],
], columns=["Metric", "USD", f"{country} currency"])
for col in ["USD", f"{country} currency"]:
    bridge_df[col] = bridge_df[col].map(lambda x: f"{x:.4%}" if isinstance(x, float) else x)
st.dataframe(bridge_df, use_container_width=True, hide_index=True)

with st.expander("Show formulas and calculation details", expanded=True):
    st.markdown("**1. Relever beta**")
    st.latex(r"D/E = \frac{d}{1-d}")
    st.latex(r"\beta_E = \beta_U[1+(1-\tau)D/E]")
    st.write(
        f"D/E = {debt_share_pct:.2f}% / (1 − {debt_share_pct:.2f}%) "
        f"= {base_results['de_ratio']:.6f}"
    )
    st.write(
        f"βE = {beta_u:.4f} × [1 + (1 − {tax_pct/100:.4f}) × "
        f"{base_results['de_ratio']:.6f}] = {base_results['beta_e']:.6f}"
    )

    st.markdown("**2. Cost of equity**")
    st.latex(r"R_E = R_f + \beta_E \times ERP")
    st.latex(r"R_E^*=(1+R_E)\frac{1+R_f^*}{1+R_f}-1")
    st.write(f"USD cost of equity = {rf_us_pct:.4f}% + {base_results['beta_e']:.6f} × {erp_pct:.4f}% = {pct_from_decimal(base_results['re_us']):.4f}%")
    st.write(f"Foreign cost of equity = (1 + {base_results['re_us']:.6f}) × (1 + {rf_foreign_pct/100:.6f}) / (1 + {rf_us_pct/100:.6f}) − 1 = {pct_from_decimal(base_results['re_foreign']):.4f}%")

    st.markdown("**3. Cost of debt**")
    st.latex(r"R_D=R_f+s")
    st.latex(r"s^*=s\frac{1+R_f^*}{1+R_f}, \qquad R_D^*=R_f^*+s^*")
    st.write(f"USD borrowing cost = {rf_us_pct:.4f}% + {spread_us_pct:.4f}% = {pct_from_decimal(base_results['rd_us']):.4f}%")
    st.write(f"Foreign borrowing spread = {spread_foreign_pct:.4f}% ({spread_foreign_source})")
    st.write(f"Foreign borrowing cost = {rf_foreign_pct:.4f}% + {spread_foreign_pct:.4f}% = {pct_from_decimal(base_results['rd_foreign']):.4f}%")

    st.markdown("**4. WACC**")
    st.latex(r"WACC=(1-d)R_E+dR_D(1-\tau)")
    st.write(f"USD WACC = {pct_from_decimal(base_results['wacc_us']):.4f}%")
    st.write(f"Foreign WACC = {pct_from_decimal(base_results['wacc_foreign']):.4f}%")

st.subheader("Main assumptions")
st.markdown(
    f"""
- U.S. industry unlevered beta is used as a proxy for project operating risk: **{beta_u:.4f}**.
- The selected project tax rate is **{tax_pct:.2f}%**, applied in both currencies.
- Target debt share is **{debt_share_pct:.2f}%** and equity risk premium is **{erp_pct:.2f}%**.
- U.S. and selected-country 10-year government yields are used as risk-free proxies.
- No separate country equity risk premium is added.
- The foreign cost of equity and borrowing spread are converted using the ratio of gross risk-free rates.
- Interest deductions are assumed fully usable, and the relevering formula assumes debt beta is zero.
"""
)

# ---------------------------------------------------------------------
# All-industry comparison
# ---------------------------------------------------------------------
st.header("3. All-industry comparison")
st.write(
    "This table holds the selected country, tax rate, ERP, debt share, yields, "
    "and borrowing spreads constant while varying only the industry unlevered beta."
)
if beta_df is not None and not beta_df.empty:
    industry_results = []
    for _, row in beta_df.iterrows():
        b = safe_float(row["Unlevered Beta"])
        if b is None:
            continue
        try:
            r = calculate_costs(
                b, debt_share_pct, tax_pct, erp_pct,
                rf_us_pct, rf_foreign_pct, spread_us_pct,
                spread_foreign_pct if foreign_spread_override_on else None,
            )
            industry_results.append({
                "Industry": row["Industry"],
                "Unlevered beta": b,
                "Relevered beta": r["beta_e"],
                "USD cost of equity": pct_from_decimal(r["re_us"]),
                "USD WACC": pct_from_decimal(r["wacc_us"]),
                f"{country} cost of equity": pct_from_decimal(r["re_foreign"]),
                f"{country} WACC": pct_from_decimal(r["wacc_foreign"]),
            })
        except Exception:
            continue
    industry_result_df = pd.DataFrame(industry_results)
    if not industry_result_df.empty:
        st.dataframe(
            industry_result_df.style.format({
                "Unlevered beta": "{:.4f}",
                "Relevered beta": "{:.4f}",
                "USD cost of equity": "{:.4f}%",
                "USD WACC": "{:.4f}%",
                f"{country} cost of equity": "{:.4f}%",
                f"{country} WACC": "{:.4f}%",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No industry beta rows were available for comparison.")
else:
    st.warning("The Damodaran industry beta workbook could not be retrieved, so the all-industry comparison is unavailable. Refresh or enter a manual beta for the selected-industry calculation.")

# ---------------------------------------------------------------------
# Verification checks
# ---------------------------------------------------------------------
st.header("4. Built-in verification checks")
st.caption("These checks are designed around the assignment's required invariants.")

# Equal yields => same currency results when spread override is off.
equal_yield_test = calculate_costs(
    beta_u, debt_share_pct, tax_pct, erp_pct,
    rf_us_pct, rf_us_pct, spread_us_pct
)
equal_yield_pass = (
    abs(equal_yield_test["re_us"] - equal_yield_test["re_foreign"]) < 1e-12
    and abs(equal_yield_test["rd_us"] - equal_yield_test["rd_foreign"]) < 1e-12
    and abs(equal_yield_test["wacc_us"] - equal_yield_test["wacc_foreign"]) < 1e-12
)

# Zero debt => WACC equals cost of equity in both currencies.
zero_debt_test = calculate_costs(
    beta_u, 0.0, tax_pct, erp_pct,
    rf_us_pct, rf_foreign_pct, spread_us_pct,
    spread_foreign_pct if foreign_spread_override_on else None,
)
zero_debt_pass = (
    abs(zero_debt_test["wacc_us"] - zero_debt_test["re_us"]) < 1e-12
    and abs(zero_debt_test["wacc_foreign"] - zero_debt_test["re_foreign"]) < 1e-12
)

# Foreign spread override must not alter USD-side outputs.
usd_before = calculate_costs(
    beta_u, debt_share_pct, tax_pct, erp_pct,
    rf_us_pct, rf_foreign_pct, spread_us_pct,
    spread_foreign_pct if foreign_spread_override_on else None,
)
_test_foreign_spread = max(0.0, spread_foreign_pct + 1.0)
usd_after = calculate_costs(
    beta_u, debt_share_pct, tax_pct, erp_pct,
    rf_us_pct, rf_foreign_pct, spread_us_pct, _test_foreign_spread,
)
foreign_override_invariance_pass = (
    abs(usd_before["re_us"] - usd_after["re_us"]) < 1e-12
    and abs(usd_before["rd_us"] - usd_after["rd_us"]) < 1e-12
    and abs(usd_before["wacc_us"] - usd_after["wacc_us"]) < 1e-12
)

checks = [
    ("Equal government yields", equal_yield_pass,
     "With identical yields and the automatic foreign-spread conversion, USD and foreign-currency results match."),
    ("Zero debt share", zero_debt_pass,
     "At 0% debt, WACC equals cost of equity in both currencies."),
    ("Foreign spread override isolation", foreign_override_invariance_pass,
     "Changing the foreign spread is isolated from USD cost of debt and USD WACC."),
]
for name, passed, detail in checks:
    col1, col2 = st.columns([1, 5])
    with col1:
        if name == "Equal government yields" and foreign_spread_override_on:
            st.info("N/A")
        elif passed:
            st.success("PASS")
        else:
            st.error("FAIL")
    with col2:
        if name == "Equal government yields" and foreign_spread_override_on:
            st.write(f"**{name}** — Not applicable while the foreign spread is manually overridden.")
        else:
            st.write(f"**{name}** — {detail}")

# ---------------------------------------------------------------------
# Downloadable results
# ---------------------------------------------------------------------
st.header("5. Download results")
summary_df = pd.DataFrame([
    ["Industry", industry],
    ["Target country", country],
    ["Unlevered beta", beta_u],
    ["Target debt share (%)", debt_share_pct],
    ["Project tax rate (%)", tax_pct],
    ["Tax-rate selection", tax_source_choice],
    ["Equity risk premium (%)", erp_pct],
    ["U.S. 10-year yield (%)", rf_us_pct],
    [f"{country} 10-year yield (%)", rf_foreign_pct],
    ["USD BBB spread (%)", spread_us_pct],
    ["Foreign BBB spread (%)", spread_foreign_pct],
    ["Relevered beta", base_results["beta_e"]],
    ["USD cost of equity (%)", pct_from_decimal(base_results["re_us"])],
    ["Foreign cost of equity (%)", pct_from_decimal(base_results["re_foreign"])],
    ["USD pre-tax cost of debt (%)", pct_from_decimal(base_results["rd_us"])],
    ["Foreign pre-tax cost of debt (%)", pct_from_decimal(base_results["rd_foreign"])],
    ["USD WACC (%)", pct_from_decimal(base_results["wacc_us"])],
    ["Foreign WACC (%)", pct_from_decimal(base_results["wacc_foreign"])],
], columns=["Input / output", "Value"])

csv_bytes = summary_df.to_csv(index=False).encode("utf-8")
st.download_button(
    "⬇ Download selected-project results (CSV)",
    data=csv_bytes,
    file_name="international_cost_of_capital_results.csv",
    mime="text/csv",
)

if beta_df is not None and not beta_df.empty and "industry_result_df" in locals() and not industry_result_df.empty:
    industry_csv = industry_result_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇ Download all-industry comparison (CSV)",
        data=industry_csv,
        file_name="international_cost_of_capital_industry_comparison.csv",
        mime="text/csv",
    )

st.divider()
st.caption(
    "Educational tool. Verify fetched observations and assumptions before using "
    "results. Source websites may change their page structure or block automated requests."
)
