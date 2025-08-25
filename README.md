# Lohn vs. Dividende – Swiss Payout Optimizer (Streamlit)

Simulate the tax impact of **salary vs. dividend** for Swiss owner-managers (AG/GmbH). The app uses canton/commune tariffs, real church-tax factors, correct splitting & rounding, and **cantonal dividend partial taxation**. It compares scenarios and shows where money “leaks” with clear visuals.

---

## Features

* Uses devbrains **parsed/2025** dataset (locations, tariffs, factors) for income tax at **Bund/Kanton/Gemeinde/Kirche** levels with proper splitting.
* Models **Teilbesteuerung Dividenden**: **Bund fixed 70%**, **Canton via JSON map** (per canton code).
* Supports **Personal-/Kopfsteuer** via tariff or factors; includes **JSON fallback** (e.g., ZH 24 CHF p.P., SO 50 CHF p.P.).
* Provides two scenarios out of the box: **A: 100% Lohn**, **B: Lohn + Dividende (Strikt)**, plus an **optimizer** for the best mix.
* Adds compact graphics: **Waterfall (Brutto → Netto)** per scenario and **A vs. B net bar**.
* Mobile-friendly charts using a single brand color **#af966d**.

---

## Quick start

```bash
# 1) Clone
git clone https://github.com/<your-org>/<repo>.git
cd <repo>

# 2) Python env
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3) Install
pip install -r requirements.txt  # or: pip install streamlit plotly

# 4) Data (devbrains parsed/2025)
# Put the dataset so that one of these paths exists:
#   data/parsed/2025/
#   parsed/2025/
#   2025/
# (Must contain: locations.json, tarifs/<CantonID>.json, factors/<CantonID>.json)

# 5) Run
streamlit run app.py
```

---

## Data files expected

```
parsed/2025/
  ├─ locations.json
  ├─ tarifs/
  │    ├─ 0.json            # Bund
  │    ├─ 26.json           # ZH
  │    └─ ...
  └─ factors/
       ├─ 26.json           # ZH
       └─ ...
```

Optional JSONs in the app folder, `data/`, or `/mnt/data`:

* **Teilbesteuerung\_Dividenden*.json*\* – cantonal inclusion map:

  ```json
  { "ZH": 0.50, "SO": 0.50, "FR": 0.70 }
  ```
* **personalsteuer\_2024.json** – personal/head tax map (provided in this repo):

  ```json
  {
    "schema_version": 1,
    "year": 2024,
    "unit": "CHF",
    "data": {
      "ZH": { "has_personal_tax": true, "level": "communal", "amount": 24.0, "rule": "per_person" },
      "SO": { "has_personal_tax": true, "level": "both", "amount_canton": 30.0,
              "commune_optional": { "range": [20.0, 50.0] }, "rule": "per_person" }
      /* … */
    }
  }
  ```

---

## Configuration

Create `.streamlit/config.toml` to match brand colors (no custom CSS needed):

```toml
[theme]
primaryColor = "#0A3C7D"
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#F6F7F9"
textColor = "#1A1A1A"
font = "sans serif"
```

---

## How it works (high level)

* **Salary net for tax base (AN-Seite):** AHV/IV/EO 5.3%, ALV 1.1% (cap 148’200), NBU 0.4% (cap 148’200), plus optional PK-Einkauf.
* **Tariff engines:** ZÜRICH, BUND, FREIBURG, FLATTAX, FORMEL (incl. BL fix) with correct split & rounding to 100.
* **Factors:** Multipliers from `factors/<CantonID>.json` for canton, commune, and church (confession-specific).
* **Dividends:** Taxable share = `dividend * inclusion_rate`. Inclusion: **Bund 70%**, **Canton from JSON map**.
* **Personalsteuer:** Found as tariff if present; else parsed from factors; else **JSON fallback** by canton.

---

## Usage tips

* Enter **Kanton/Gemeinde**, profit before owner compensation, optional **Zielauszahlung**, other income, age, PK-Einkauf and employer charges.
* Choose **Zivilstand**, **Kinder**, and **Konfession** for splitting and church tax.
* Toggle **Optimizer** to compute the best mix (Strikt: dividend only if salary ≥ Mindestlohn).
* Use the **debug** toggle to inspect tariff types, taxable bases, and engines.

---

## Visuals included

* **Waterfall (Brutto → AN-Abzüge → Steuern → Netto)** per scenario for a clear “leakage” view.
* **A vs. B Net bar** to highlight the better immediate payout.
* **Tax breakdown horizontal bar** (Bund/Kanton/Gemeinde/Kirche/Personal), all in **#af966d** and with extra mobile padding.

---

## Embedding (optional)

Embed via `<iframe src="…?embed=true">`. If a thin grey line or footer appears in your CMS, wrap the iframe in a small overscan container to mask top/bottom chrome (example provided in `docs/embed-snippet.html`). No code changes to the app are required.

---

## Project structure

```
app.py                           # Streamlit app
/parsed/2025 or data/parsed/2025 # devbrains dataset (not in repo)
/.streamlit/config.toml          # theme (optional)
personalsteuer_2024.json         # head-tax fallback mapping (optional, in repo)
Teilbesteuerung_Dividenden.json  # cantonal dividend inclusion (optional)
docs/                            # optional snippets & notes
```

---

## Known limitations

* The devbrains 2025 bundle may not ship **Personal-/Kopfsteuer**. The app supports a **JSON fallback** to ensure correct totals (ZH/SO provided).
* Exact communal head-tax amounts can vary. Adjust `personalsteuer_2024.json` if you need commune-level precision.
* BVG shown as cost blocks; the taxable base uses the devbrains **AN-Netto** approach.

---

## Development

* Python 3.10+ recommended.
* Main libs: `streamlit`, `plotly`.
* To extend cantonal dividend inclusion, drop a `Teilbesteuerung_Dividenden*.json` mapping in the app or data folder.
* To refine Personalsteuer per canton/commune, edit `personalsteuer_2024.json` (the loader auto-detects it).

---

## License

MIT. See `LICENSE`.

---

## Disclaimer

This tool is for **illustrative planning**. Tax outcomes depend on full personal/company context and current law. For decisions, consult a qualified Swiss tax advisor.
