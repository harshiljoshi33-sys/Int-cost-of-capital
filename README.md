# International Cost of Capital App

A Streamlit app comparing a project's cost of equity and WACC in USD and a selected foreign currency.

## Course methodology

The calculations follow the uploaded International Valuation lecture packet, slides 573–586.

### Relevering beta

\[
D/E = \frac{d}{1-d}
\]

\[
\beta_E = \beta_U[1+(1-\tau)D/E]
\]

### USD cost of equity

\[
R_E = R_f + \beta_E \times ERP
\]

### Foreign-currency cost of equity

\[
R_E^*=(1+R_E)\frac{1+R_f^*}{1+R_f}-1
\]

### Cost of debt

\[
R_D=R_f+s
\]

\[
s^*=s\frac{1+R_f^*}{1+R_f}, \qquad R_D^*=R_f^*+s^*
\]

### WACC

\[
WACC=(1-d)R_E+dR_D(1-\tau)
\]

The same project tax rate and target debt share are applied in both currencies. The foreign-currency spread can be manually overridden; otherwise it is converted using the lecture formula.

## Data sources and retrieval approach

1. **Damodaran U.S. industry betas**
   - XLS: https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls
   - The app downloads the XLS, locates the exact `Industry Name` and `Unlevered Beta` fields, and cross-checks the resulting values against Damodaran's published HTML table.
   - Data vintage is read from the Damodaran page when available.

2. **Damodaran BBB borrowing spread**
   - https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html
   - The app specifically selects the Baa2/BBB row in the **large non-financial service firms** table and extracts the corresponding spread, not the financial-services spread.
   - The page's analysis vintage is displayed.

3. **OECD statutory corporate income tax rates**
   - Course-provided 2024 report: https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html
   - Current OECD Corporate Income Tax Rates Database: https://data-explorer.oecd.org/vis?df%5Bag%5D=OECD.CTP.TPS&df%5Bds%5D=dsDisseminateFinalDMZ&df%5Bid%5D=DSD_TAX_CIT%40DF_CIT&df%5Bvs%5D=2.0
   - The app uses the OECD SDMX data service for programmatic retrieval, selecting statutory combined corporate income tax rates for general government and displaying the latest annual observation available per country.

4. **Trading Economics 10-year government bond yields**
   - https://tradingeconomics.com/bonds
   - The app scrapes the public bonds page without an API key. It only accepts rows where country, yield, and an actual observation date can be identified. It does not substitute the download date for the observation date.

## Data-quality controls

The app is intentionally fail-closed:

- No illustrative or hardcoded market-data fallback is silently used.
- Missing, malformed, undated, out-of-range, or unvalidated observations are rejected.
- Damodaran beta data is cross-checked against its HTML publication.
- BBB extraction requires the exact Baa2/BBB rating and the large non-financial service-firm table.
- OECD extraction requires the statutory combined CIT series and a minimum coverage check.
- Trading Economics extraction requires country, yield, and observation date fields, plus the U.S. 10-year benchmark.
- If a source is unavailable or its page structure changes, the user must use a manual override before calculations can proceed.
- The refresh button clears the cache and re-fetches source data.

**Important:** No scraper can be guaranteed 100% future-proof. Websites can change page structures, introduce bot protection, or revise data. The app therefore validates inputs and stops rather than quietly using questionable values. Before submitting, confirm the displayed observation dates and source vintages.

## Defaults

- Industry: Software (System & Application)
- Target country: United States
- Project tax rate: U.S. statutory corporate tax rate
- Equity risk premium: 6.5%
- Target debt share: 30%

If a live source cannot be reached at startup, the corresponding manual override must be enabled. The disabled manual-input fields show neutral starter values only; they are not used in calculations unless the override checkbox is selected.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## GitHub and Streamlit deployment

1. Create a GitHub repository.
2. Upload `app.py`, `requirements.txt`, and `README.md`.
3. In Streamlit Community Cloud, choose **New app**.
4. Select the repository, branch, and `app.py`.
5. Deploy.

## Built-in verification checks

1. Equal U.S. and foreign government yields produce equal results when the foreign spread override is off.
2. Zero debt makes WACC equal the cost of equity.
3. Changing the foreign-currency spread does not affect USD results.

## Important assumptions

- U.S. industry unlevered beta proxies for project operating risk.
- No separate country equity risk premium is added.
- Government bond yields are used as risk-free proxies.
- Debt and the interest tax shield are assumed to have zero beta in the relevering formula.
- Interest deductions are assumed fully usable.
- The same target debt share and selected project tax rate are used in both currency calculations.

## Disclaimer

Educational tool only. Confirm source observation dates, data vintages, units, and calculations independently before relying on results.
