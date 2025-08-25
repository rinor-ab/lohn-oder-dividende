# app.py – Lohn vs. Dividende (devbrains 2025 data; fixed tariffs, simple UI)
import json, math, pathlib
import streamlit as st

# ------------------------- Configuration -------------------------
APP_DIR = pathlib.Path(__file__).parent

# Where your devbrains data lives. This matches the structure you described:
# data/parsed/2025/{locations.json, factors/*.json, tarifs/*.json, deductions/*.json}
# The code tries a couple of sensible roots so it "just works" in your setup.
CANDIDATE_DATA_ROOTS = [
    APP_DIR / "data" / "parsed" / "2025",
    APP_DIR / "parsed" / "2025",
    APP_DIR / "2025",
    pathlib.Path("/mnt/data/2025"),  # helpful while debugging in notebooks
]

YEAR_ROOT = None
for p in CANDIDATE_DATA_ROOTS:
    if (p / "locations.json").exists():
        YEAR_ROOT = p
        break
if YEAR_ROOT is None:
    st.error("Konnte die devbrains-Daten nicht finden. Erwartet: data/parsed/2025/…")
    st.stop()

# Constants / defaults (as per your spec)
CHURCH_AVG_RATE = 0.12  # Ø Kirchensteuer auf Kanton+Gemeinde (12%)
RULE_MODE_STRIKT = "Strikt (Dividende nur bei Lohn ≥ Mindestlohn)"
AHV_ON_DEFAULT = True   # AHV/ALV/BVG standardmäßig anwenden

# ------------------------- Small helpers -------------------------
def is_nan(x):
    try:
        return isinstance(x, float) and math.isnan(x)
    except:
        return False

def nz(x, default=0.0):
    return default if (x is None or is_nan(x)) else x

def clamp_pos(x):
    try:
        return max(0.0, float(x or 0.0))
    except:
        return 0.0

# ------------------------- Load devbrains files -------------------
@st.cache_data(show_spinner=False)
def load_locations():
    with (YEAR_ROOT / "locations.json").open("r", encoding="utf-8") as f:
        locs = json.load(f)
    # Map canton -> list of municipalities (objects)
    by_canton = {}
    for r in locs:
        by_canton.setdefault(r["Canton"], []).append(r)
    for k in by_canton:
        by_canton[k].sort(key=lambda x: x["BfsName"])
    return locs, by_canton

@st.cache_data(show_spinner=False)
def load_tarifs(canton_id: int):
    # canton_id 0 = Bund, 1..26 = cantons (devbrains convention)
    with (YEAR_ROOT / "tarifs" / f"{int(canton_id)}.json").open("r", encoding="utf-8") as f:
        return json.load(f)

@st.cache_data(show_spinner=False)
def load_factors(canton_id: int):
    with (YEAR_ROOT / "factors" / f"{int(canton_id)}.json").open("r", encoding="utf-8") as f:
        return json.load(f)

# ------------------------- Tariff selection & evaluation ----------
def _pick_income_table(tariffs: list):
    """Pick the right 'EINKOMMENSSTEUER' table for a location.
       Preference: group=='ALLE' -> otherwise something with 'LEDIG' -> else first."""
    if not tariffs:
        return None
    cands = [t for t in tariffs if (t.get("taxType") or "").upper() == "EINKOMMENSSTEUER"]
    if not cands:
        return None
    # exact ALLE
    for t in cands:
        if (t.get("group") or "").strip().upper() == "ALLE":
            return t
    # any "LEDIG" grouping (common in many cantons)
    for t in cands:
        grp = (t.get("group") or "").upper()
        if "LEDIG" in grp or "ALLEINE" in grp:
            return t
    # fallback
    return cands[0]

def _eval_progressive_width(table_rows: list, base_amount: float) -> float:
    """
    Evaluate devbrains progressive table where 'amount' is the WIDTH of a step
    (Zurich style) OR a sentinel 0 (first row in some cantons). We:
      - SKIP rows where amount==0 (they are placeholders, not 'rest-of-income')
      - Sum stepwise: tax += min(rem, width) * (percent/100)
      - If there is still 'rem' and no auto-rest-row, apply the LAST row's percent
    This fixes the zero-tax issue seen in several cantons (e.g., SH/FR/GE/...),
    where the first row has amount=0 and percent=0.
    """
    rem = max(0.0, base_amount or 0.0)
    tax = 0.0
    if rem <= 0:
        return 0.0

    for i, row in enumerate(table_rows):
        width = float(row.get("amount") or 0.0)
        pct = float(row.get("percent") or 0.0) / 100.0
        if width == 0:
            # DO NOT consume the whole remainder at 0% — just skip this placeholder row
            continue
        use = min(rem, width)
        tax += use * pct
        rem -= use
        if rem <= 0:
            break

    # if there is leftover and no explicit 'rest' step, continue at last known marginal rate
    if rem > 0 and table_rows:
        last_pct = float(table_rows[-1].get("percent") or 0.0) / 100.0
        tax += rem * last_pct

    return tax

def eval_tariff_amount(tariff_obj: dict, taxable: float) -> float:
    """Apply splitting correctly: tax(base/s) * s."""
    if not tariff_obj:
        return 0.0
    s = int(tariff_obj.get("splitting") or 0)
    s = s if s > 0 else 1
    rows = tariff_obj.get("table") or []
    return _eval_progressive_width(rows, clamp_pos(taxable) / s) * s

# ------------------------- Income tax engines --------------------
def federal_income_tax_from_tariffs(taxable: float) -> float:
    tariffs = load_tarifs(0)  # 0 = Bund
    table = _pick_income_table(tariffs)
    return eval_tariff_amount(table, taxable)

def cantonal_income_tax_from_tariffs(taxable: float, canton_code: str, bfs_id: int) -> float:
    # find canton_id from locations
    locs, _ = load_locations()
    # one record with matching canton_code to get its CantonID
    # (any municipality of that canton gives same CantonID)
    cand = next((r for r in locs if r["Canton"] == canton_code), None)
    if not cand:
        return 0.0
    canton_id = int(cand["CantonID"])

    tariffs = load_tarifs(canton_id)
    table = _pick_income_table(tariffs)
    base_tax = eval_tariff_amount(table, taxable)  # "Einheitsteuer" / base

    # multiply by municipality multipliers
    try:
        factors = load_factors(canton_id)
        f = next((f for f in factors if f.get("Location", {}).get("BfsID") == bfs_id), None)
        if f:
            mult = (nz(f.get("IncomeRateCanton"), 0.0) + nz(f.get("IncomeRateCity"), 0.0)) / 100.0
        else:
            mult = 1.0
            st.warning("Faktoren nicht gefunden – setze Multiplikator = 1.0")
    except FileNotFoundError:
        mult = 1.0
        st.warning("Faktoren-Datei nicht gefunden – setze Multiplikator = 1.0")

    # Apply avg. church surcharge (as per your requirement)
    return base_tax * mult * (1.0 + CHURCH_AVG_RATE)

# ------------------------- AHV/ALV/BVG ---------------------------
# These could be loaded from your old Social_Security_Contributions.json if you still have it.
# Here we just use sane defaults identical to your last working version.
AHV_employer = 0.053
AHV_employee = 0.053
ALV_employer = 0.011
ALV_employee = 0.011
ALV_ceiling  = 148_200.0
BVG_rates = {"25-34": 0.07, "35-44": 0.10, "45-54": 0.15, "55-65": 0.18}
BVG_entry_threshold = 22_680.0
BVG_coord_deduction = 26_460.0
BVG_max_insured     = 90_720.0

def bvg_insured_part(salary):
    return max(0.0, min(salary, BVG_max_insured) - BVG_coord_deduction)

def age_to_band(age: int) -> str:
    a = int(age or 35)
    if a < 35:    return "25-34"
    if a < 45:    return "35-44"
    if a < 55:    return "45-54"
    return "55-65"

def employer_costs(salary, age_key, fak=0.015, uvg=0.01):
    if salary <= 0: return dict(ahv=0.0,alv=0.0,bvg=0.0,extra=0.0,total=0.0)
    ahv = AHV_employer * salary
    alv = ALV_employer * min(salary, ALV_ceiling)
    bvg = 0.0
    if salary >= BVG_entry_threshold:
        bvg = (BVG_rates[age_key]/2.0) * bvg_insured_part(salary)
    extra = fak*salary + uvg*salary
    return dict(ahv=ahv, alv=alv, bvg=bvg, extra=extra, total=ahv+alv+bvg+extra)

def employee_deductions(salary, age_key):
    if salary <= 0: return dict(ahv=0.0,alv=0.0,bvg=0.0,total=0.0)
    ahv = AHV_employee * salary
    alv = ALV_employee * min(salary, ALV_ceiling)
    bvg = 0.0
    if salary >= BVG_entry_threshold:
        bvg = (BVG_rates[age_key]/2.0) * bvg_insured_part(salary)
    return dict(ahv=ahv, alv=alv, bvg=bvg, total=ahv+alv+bvg)

# ------------------------- Streamlit UI --------------------------
st.title("Lohn vs. Dividende")
st.caption("Steuerlogik mit devbrains-Tarifen (2025). BVG automatisch nach Alter. Kirchensteuer: Ø-Annahme.")

# --- Location pickers fed by devbrains locations.json
_, canton_to_locs = load_locations()
canton = st.selectbox("Kanton", sorted(canton_to_locs.keys()))
gemeinde_obj = st.selectbox(
    "Gemeinde",
    options=canton_to_locs[canton],
    format_func=lambda r: r["BfsName"]
)
BFS_ID = int(gemeinde_obj["BfsID"])
CANTON_ID = int(gemeinde_obj["CantonID"])

col1, col2 = st.columns(2)
with col1:
    profit         = st.number_input("Firmengewinn **vor Lohn** [CHF]", 0.0, step=10_000.0)
    desired_income = st.number_input("Gewünschte **Gesamtauszahlung** an Inhaber [CHF] (optional)", 0.0, step=10_000.0)
with col2:
    other_inc      = st.number_input("Weitere steuerbare Einkünfte [CHF]", 0.0, step=10_000.0)
    age_input      = st.number_input("Alter (für BVG-Altersband)", min_value=18, max_value=70, value=40, step=1)

# Put ALL assumptions into a single collapsible menu (as requested)
with st.expander("ANNAHMEN", expanded=True):
    st.subheader("Annahmen")
    colA, colB = st.columns(2)
    with colA:
        min_salary  = st.number_input("Marktüblicher Mindestlohn [CHF]", 0.0, step=10_000.0, value=120_000.0)
        share_pct   = st.number_input("Beteiligungsquote [%] (Teilbesteuerung ab 10 %)", 0.0, 100.0, 100.0, step=5.0)
        pk_buyin    = st.number_input("PK-Einkauf (privat) [CHF]", 0.0, step=1.0)
    with colB:
        fak_rate    = st.number_input("FAK (nur Arbeitgeber) [%]", 0.0, 5.0, 1.5, step=0.1)/100.0
        uvg_rate    = st.number_input("UVG/KTG (Arbeitgeber) [%]", 0.0, 5.0, 1.0, step=0.1)/100.0
        st.caption("AHV/ALV/BVG wird standardmäßig angewendet (ausgeblendet). Regelmodus fix **Strikt**.")
    st.markdown("---")
    st.markdown("**Abzüge (manuell, direkt vom steuerbaren Einkommen abgezogen)**")
    colD1, colD2 = st.columns(2)
    with colD1:
        fed_ded_manual  = st.number_input("Abzüge – **Bund** [CHF]", 0.0, step=100.0, value=0.0)
    with colD2:
        cant_ded_manual = st.number_input("Abzüge – **Kanton/Gemeinde** [CHF]", 0.0, step=100.0, value=0.0)

# toggles
optimizer_on = st.checkbox("Optimierer – beste Mischung (Lohn + Dividende) finden", value=True)
debug_mode   = st.checkbox("Debug-Infos anzeigen", value=False)

# desired payout normalization
if desired_income == 0: desired_income = None
elif desired_income and desired_income > profit: desired_income = profit

# ------------------------- Dividends partial inclusion -----------
def qualifies_partial(share):
    return (share or 0.0) >= 10.0

def incl_rates(qualifies, canton_code):
    # devbrains does not ship inclusion; keep your previous standard
    inc_fed  = 0.70 if qualifies else 1.00
    # Cantonal partial taxation often varies; we keep a reasonable default 70% unless you ship per-canton values.
    inc_cant = 0.70 if qualifies else 1.00
    return inc_fed, inc_cant

# ------------------------- Scenarios -----------------------------
def scenario_salary_only(profit, desired, kanton_code, bfs_id, age_key, other, pk_buy):
    salary = profit if desired is None else min(profit, desired)
    ag = employer_costs(salary, age_key, fak=fak_rate, uvg=uvg_rate)
    an = employee_deductions(salary, age_key)

    profit_after_salary = profit - salary - ag["total"]
    if profit_after_salary < 0:
        st.warning("Bruttolohn inkl. Arbeitgeberabgaben > Gewinn – Restgewinn auf 0 gesetzt.")
    profit_after_salary = max(0.0, profit_after_salary)

    # Personal taxes (devbrains tariffs)
    taxable_fed  = clamp_pos(salary - an["total"] + other - pk_buy - fed_ded_manual)
    taxable_cant = clamp_pos(salary - an["total"] + other - pk_buy - cant_ded_manual)

    fed  = federal_income_tax_from_tariffs(taxable_fed)
    cant = cantonal_income_tax_from_tariffs(taxable_cant, kanton_code, bfs_id)
    income_tax = fed + cant

    net_owner = salary - an["total"] - income_tax
    return {
        "salary": salary, "dividend": 0.0,
        "income_tax": income_tax, "net": net_owner,
        "retained_after_tax": profit_after_salary,  # corp tax not modeled here (you can re-add if needed)
        "blocks": dict(ag=ag, an=an, fed=fed, cant=cant)
    }

def scenario_dividend(profit, desired, kanton_code, bfs_id, age_key, other, pk_buy,
                      min_salary, share_pct):
    inc_fed, inc_cant = incl_rates(qualifies_partial(share_pct), kanton_code)

    # Strikt: erst Lohn ≥ Mindestlohn, dann Dividende
    salary = min(min_salary, profit if desired is None else min(profit, desired))
    ag = employer_costs(salary, age_key, fak=fak_rate, uvg=uvg_rate)
    an = employee_deductions(salary, age_key)

    profit_after_salary = clamp_pos(profit - salary - ag["total"])
    # Nach Steuern im Unternehmen (Körperschaft) könntest du hier ergänzen, wenn du Firmensteuern wieder aktivierst

    desired_left = None if desired is None else clamp_pos(desired - salary)
    gross_dividend_pool = profit_after_salary  # vereinfachend: alles ausschüttbar
    dividend = gross_dividend_pool if desired_left is None else min(gross_dividend_pool, desired_left)

    # steuerbare Basis
    taxable_salary = clamp_pos(salary - an["total"])
    taxable_fed  = clamp_pos(taxable_salary + dividend*inc_fed  + other - pk_buy - fed_ded_manual)
    taxable_cant = clamp_pos(taxable_salary + dividend*inc_cant + other - pk_buy - cant_ded_manual)

    fed  = federal_income_tax_from_tariffs(taxable_fed)
    cant = cantonal_income_tax_from_tariffs(taxable_cant, kanton_code, bfs_id)
    income_tax = fed + cant

    net_owner = (salary - an["total"]) + dividend - income_tax
    return {
        "salary": salary, "dividend": dividend,
        "income_tax": income_tax, "net": net_owner,
        "retained_after_tax": clamp_pos(profit_after_salary - dividend),
        "blocks": dict(ag=ag, an=an, fed=fed, cant=cant, inc_fed=inc_fed, inc_cant=inc_cant)
    }

# ------------------------- Optimizer ------------------------------
def optimize_mix(profit, desired_income, kanton_code, bfs_id, age_key, other_inc, pk_buyin,
                 min_salary, share_pct, step=1_000.0):
    inc_fed, inc_cant = incl_rates(qualifies_partial(share_pct), kanton_code)
    cap = profit if desired_income is None else min(profit, desired_income)
    best = None
    s = 0.0
    while s <= cap + 1e-6:
        ag = employer_costs(s, age_key, fak=fak_rate, uvg=uvg_rate)
        an = employee_deductions(s, age_key)
        profit_after_salary = clamp_pos(profit - s - ag["total"])
        desired_left = None if desired_income is None else clamp_pos(desired_income - s)
        dividend = profit_after_salary if desired_left is None else min(profit_after_salary, desired_left)
        if s < min_salary:
            dividend = 0.0  # Strikt

        taxable_salary = clamp_pos(s - an["total"])
        taxable_fed  = clamp_pos(taxable_salary + dividend*inc_fed  + other_inc - pk_buyin - fed_ded_manual)
        taxable_cant = clamp_pos(taxable_salary + dividend*inc_cant + other_inc - pk_buyin - cant_ded_manual)

        fed_tax  = federal_income_tax_from_tariffs(taxable_fed)
        cant_tax = cantonal_income_tax_from_tariffs(taxable_cant, kanton_code, bfs_id)
        income_tax = fed_tax + cant_tax
        net_owner = (s - an["total"]) + dividend - income_tax

        if (best is None) or (net_owner > best["net"]):
            best = dict(salary=s, dividend=dividend, income_tax=income_tax,
                        net=net_owner, retained_after_tax=clamp_pos(profit_after_salary - dividend))
        s += step
    return best

# ------------------------- Run & Render ---------------------------
if profit > 0:
    age_key = age_to_band(age_input)

    A = scenario_salary_only(profit, desired_income, canton, BFS_ID, age_key, other_inc, pk_buyin)
    B = scenario_dividend(profit, desired_income, canton, BFS_ID, age_key, other_inc, pk_buyin,
                          min_salary, share_pct)

    # ----- Display A -----
    st.subheader("Szenario A – 100% Lohn")
    st.write(f"Bruttolohn: **CHF {A['salary']:,.0f}**")
    st.write(f"AG AHV/ALV/BVG: CHF {(A['blocks']['ag']['ahv']+A['blocks']['ag']['alv']+A['blocks']['ag']['bvg']):,.0f}")
    st.write(f"AG FAK/UVG/KTG: CHF {A['blocks']['ag']['extra']:,.0f}")
    st.write(f"AN AHV/ALV/BVG (abzugsfähig): CHF {A['blocks']['an']['total']:,.0f}")
    st.write(f"Einkommenssteuer (Bund): CHF {A['blocks']['fed']:,.0f}")
    st.write(f"Einkommenssteuer (Kanton+Gemeinde, inkl. Kirche Ø): CHF {A['blocks']['cant']:,.0f}")
    st.success(f"**Netto an Inhaber (heute):** CHF {A['net']:,.0f}")

    # ----- Display B -----
    st.subheader("Szenario B – Lohn + Dividende (Strikt)")
    st.write(f"Bruttolohn: **CHF {B['salary']:,.0f}** | Dividende gesamt: **CHF {B['dividend']:,.0f}**")
    st.write(f"Einkommenssteuer (Bund): CHF {B['blocks']['fed']:,.0f}")
    st.write(f"Einkommenssteuer (Kanton+Gemeinde, inkl. Kirche Ø): CHF {B['blocks']['cant']:,.0f}")
    st.caption(f"Teilbesteuerung Dividenden: Bund {int(B['blocks']['inc_fed']*100)}%, "
               f"Kanton {int(B['blocks']['inc_cant']*100)}% (falls Beteiligung ≥ 10%).")
    st.success(f"**Netto an Inhaber (heute):** CHF {B['net']:,.0f}")

    # ----- Vergleich -----
    st.markdown("---")
    st.subheader("Vergleich (heutiger Nettozufluss)")
    c1, c2 = st.columns(2)
    with c1: st.metric("A: Lohn", f"CHF {A['net']:,.0f}")
    with c2: st.metric("B: Lohn + Dividende", f"CHF {B['net']:,.0f}")

    # ----- Optimizer -----
    if optimizer_on:
        st.markdown("---")
        st.subheader("Optimierer – beste Mischung (Strikt)")
        best = optimize_mix(profit, desired_income, canton, BFS_ID, age_key,
                            other_inc, pk_buyin, min_salary, share_pct, step=1_000.0)
        st.write(f"**Optimaler Lohn:** CHF {best['salary']:,.0f}  |  **Dividende:** CHF {best['dividend']:,.0f}")
        st.write(f"Einkommenssteuer gesamt: CHF {best['income_tax']:,.0f}")
        st.write(f"Nachsteuerlich einbehalten (vereinfachend): CHF {best['retained_after_tax']:,.0f}")
        st.success(f"**Max. Netto an Inhaber (heute):** CHF {best['net']:,.0f}")

    # ----- Debug / Backtest -----
    if debug_mode:
        st.markdown("---")
        st.subheader("Debug-Informationen")
        st.write(f"Location: {gemeinde_obj['BfsName']} ({canton}) | BFS: {BFS_ID} | CantonID: {CANTON_ID}")
        st.write(f"Steuerbasen – Bund: CHF {clamp_pos(A['blocks']['fed'] + B['blocks']['fed'] - B['blocks']['fed']):,.0f} "
                 f"| Kanton/Gemeinde: (multipliers aus factors/* angewendet)")
        # Quick backtest switch
        with st.expander("Backtest Beispiel anzeigen", expanded=False):
            st.caption("Beispiel: Attalens (FR) – steuerbare 135’200 (140’000 Lohn minus 4’800 Abzüge Kanton)")
            try:
                # FR / Attalens numbers (using our engine)
                fr_tar = load_tarifs(7)
                fr_tab = _pick_income_table(fr_tar)
                fr_base = eval_tariff_amount(fr_tab, 135_200)
                fr_fac  = next(f for f in load_factors(7) if f["Location"]["BfsID"] == 2321)
                fr_mult = (fr_fac["IncomeRateCanton"] + fr_fac["IncomeRateCity"]) / 100.0
                fr_cant = fr_base * fr_mult * (1.0 + CHURCH_AVG_RATE)
                fed_base = federal_income_tax_from_tariffs(135_200)
                st.write(f"Canton FR / Attalens – Basistarif: CHF {fr_base:,.2f} | "
                         f"Multiplikator: {fr_mult:.3f} | "
                         f"Kanton+Gemeinde inkl. Kirche Ø: **CHF {fr_cant:,.2f}** | "
                         f"Bund: **CHF {fed_base:,.2f}**")
            except Exception as e:
                st.warning(f"Backtest konnte nicht berechnet werden: {e}")

    # ----- Hinweise & Annahmen -----
    with st.expander("Hinweise & Annahmen", expanded=False):
        st.markdown(
            f"- **Kirchensteuer:** Es wird automatisch ein Ø-Zuschlag von **{int(CHURCH_AVG_RATE*100)}%** auf die kant./gemeindl. Steuer berücksichtigt.\n"
            f"- **AHV/ALV/BVG:** Standardmäßig **angewendet** (Arbeitgeber- und Arbeitnehmeranteile sind eingerechnet).\n"
            f"- **Regelmodus:** **Strikt** – Dividenden erst zulässig, wenn der Lohn ≥ Mindestlohn ist.\n"
            f"- **BVG-Altersband:** Automatische Zuordnung anhand des Alters (25–34 / 35–44 / 45–54 / 55–65).\n"
            f"- **PK-Einkauf:** Freie Eingabe; reduziert das steuerbare Einkommen (Sperrfristen beachten).\n"
            f"- **Abzüge:** Aktuell **manuelle** Beträge. Wenn du die vollständige Automatik pro Kanton möchtest, lesen wir die passende Deduktionslogik aus `deductions/*.json` ein.\n"
            f"- **Unternehmenssteuern:** In dieser Version nicht dargestellt (fokussiert auf Einkommenssteuern)."
        )

else:
    st.warning("Bitte Gewinn > 0 eingeben, um die Berechnung zu starten.")
