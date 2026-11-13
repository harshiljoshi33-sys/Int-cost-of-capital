International Cost of Capital Calculator
A Streamlit application comparing project cost of equity and WACC in USD and a selected foreign currency, following the methodology in the course lecture packet (slides 573–586).
Deploy on Streamlit Community Cloud
Upload `app.py`, `requirements.txt`, and `README.md` to the root of a GitHub repository.
In Streamlit Community Cloud, create an app from that repository.
Set branch to `main` and main file path to `app.py`.
Deploy.
The `tests/` directory is included for local validation but is not required for Streamlit deployment.
Data sources
Damodaran U.S. industry unlevered betas: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html
Damodaran ratings and default spreads: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html
OECD Corporate Income Tax (CIT) dataset, accessed through its SDMX API: https://data-explorer.oecd.org/vis?df%5Bag%5D=OECD.CTP.TPS&df%5Bds%5D=dsDisseminateFinalDMZ&df%5Bid%5D=DSD_TAX_CIT%40DF_CIT&df%5Bvs%5D=2.0
Trading Economics 10-year government bond yields by country: https://tradingeconomics.com/country-list/government-bond-10y
The OECD query selects annual statutory (`ST`) observations for general government (`S13`), then validates the combined corporate income tax measure (`CIT_C`) in the returned data. It retains the latest annual observation for each country.
Data integrity and fallback behavior
The app does not silently replace failed live observations with illustrative defaults.
Missing observations must be entered through clearly labelled manual overrides before calculations can run.
The foreign-country selector is a fixed list and does not depend on live-source availability.
Each source's observation vintage/date is displayed where available.
Trading Economics' `Reference` value is retained as the source observation period.
Calculation methodology
All rates entered in the interface are percentage points. The calculation engine converts them to decimal rates internally and retains full precision.
`D/E = d / (1-d)`
`beta_E = beta_U * [1 + (1-tau) * D/E]`
`R_E = R_f + beta_E * ERP`
`R_E* = (1+R_E) * (1+R_f*)/(1+R_f) - 1`
`R_D = R_f + s`
`s* = s * (1+R_f*)/(1+R_f)` unless manually overridden
`R_D* = R_f* + s*`
`WACC = (1-d) * R_E + d * R_D * (1-tau)`
The same project tax rate, debt share and ERP are applied in both currencies. No separate country equity risk premium is added.
Local tests
Install requirements and run:
```bash
python -m unittest discover -s tests -v
```
The parser tests use synthetic fixtures and do not constitute a live end-to-end test of third-party services. A successful deployment should be checked for current source retrieval. If a provider blocks requests or changes its page structure, the app reports the error and requires a manual override rather than using an unverified value.
