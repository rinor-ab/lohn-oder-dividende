# app.py – Lohn vs. Dividende (devbrains data wired, no KER/Wealth; Ø church)
import json, math, pathlib
import streamlit as st
from functools import lru_cache
from collections import defaultdict

# ------------------------- Paths ----------------------------
BASE_DIR = pathlib.Path(__file__).parent
DB_DIR   = BASE_DIR / "data" / "parsed" / "2025"  # devbrains layout

# ------------------------- Constants ------------------------
CHURCH_AVG_RATE = 0.12  # Ø Kirchensteuer-Zuschlag auf kant./gemeindl. Steuer (12%)
RULE_MODE_STRIKT = "Strikt (Dividende nur bei Lohn ≥ Mindestlohn)"
AHV_ON_DEFAULT = True

# Social security (keep your earlier defaults; independent from devbrains data)
AHV_employer   = 0.053
AHV_employee   = 0.053
ALV_employer   = 0.011
ALV_employee   = 0.011
ALV_ceiling    = 148_200.0
BVG_rates = {"25-34": 0.07, "35-44": 0.10, "45-54": 0.15, "55-65": 0.18}
BVG_entry_threshold = 22_680.0
BVG_coord_deduction = 26_460.0
BVG_max_insured     = 90_720.0

# ------------------------- Helpers --------------------------
def is_nan(x):
    try:
        return isinstance(x, float) and math.isnan(x)
    except:
        return False

def clamp(x):
    return max(0.0, float(x or 0.0))

def bvg_insured_part(salary):
    return max(0.0, min(salary, BVG_max_insured) - BVG_coord_deduction)

def age_to_band(age: int) -> str:
    a = int(age or 35)
    if a < 35:    return "25-34"
    if a < 45:    return "35-44"
    if a < 55:    return "45-54"
    return "55-65"

@lru_cache(None)
def load_json(path: pathlib.Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

# ------------------------- Load devbrains data --------------
# locations.json = list of { BfsID, BfsName, Canton (abbr) ... }
# factors/1.json = list of per-BfsID multipliers (IncomeRateCanton, IncomeRateCity, ProfitTaxRateCanton, ProfitTaxRateCity, ...)
# tarifs/2.json  = list of tariff tables; for income, tableType often 'ZUERICH' (step table with percent & amount)
LOCATIONS = load_json(DB_DIR / "locations.json")  # BFS master
FACTORS   = load_json(DB_DIR / "factors" / "1.json")
TARIFS    = load_json(DB_DIR / "tarifs"  / "2.json")
DEDUCTIONS= load_json(DB_DIR / "deductions" / "0.json")  # not deeply used; kept for future

# Build quick indexes
BFS_BY_CANTON = defaultdict(list)
for row in LOCATIONS:
    BFS_BY_CANTON[row["Canton"]].append((row["BfsName"], row["BfsID"]))

for k in list(BFS_BY_CANTON.keys()):
    BFS_BY_CANTON[k].sort()

FACTORS_BY_BFS = {row["Location"]["BfsID"]: row for row in FACTORS}

# Extract income tariff tables by canton "type" key
# Many cantons share the "percent/amount" step form; treat them generically
INCOME_TARIFS = [t for t in TARIFS if t.get("taxType") == "EINKOMMENSSTEUER"]

# Also fetch corporate base (GEWINNSTEUER) flat percent (per canton type in file; we pick first FLATTAX entry)
CORP_TARIF = next((t for t in TARIFS if t.get("taxType") == "GEWINNSTEUER"), None)

def first_income_table_for_canton(canton_abbr: str):
    """
    Pick the first income tariff that matches the canton 'style' if present, else fallback to a generic step-table.
    In devbrains data, Zurich uses tableType 'ZUERICH'. Other cantons also provide 'table' with {percent, amount}.
    """
    # Prefer a table whose tableType name includes the canton (heuristic)
    cand = None
    for t in INCOME_TARIFS:
        tt = (t.get("tableType") or "").upper()
        if tt.startswith(canton_abbr.upper()) or (canton_abbr.upper() in tt):
            cand = t; break
        if tt in ("ZUERICH", "ZURICH") and canton_abbr.upper() == "ZH":
            cand = t; break
    if cand is None:
        # fallback to any that has a step "table"
        for t in INCOME_TARIFS:
            if isinstance(t.get("table"), list) and t["table"] and "amount" in t["table"][0]:
                cand = t; break
    return cand

def eval_step_tariff_base(tariff, taxable_income: float, splitting_factor: int = 1):
    """
    Generic evaluator for "step tables": each row has 'percent' (as %) and 'amount' (CHF width).
    For married splitting, many cantons use 'splitting': 2 -> split income, evaluate, multiply back.
    We default to 'single' (splitting_factor=1) unless you extend the UI later.
    """
    if not tariff:
        return 0.0
    steps = tariff.get("table", [])
    if not steps:
        return 0.0
    # Apply splitting
    split = max(1, int(tariff.get("splitting") or 1))
    if splitting_factor == 1:
        split = 1
    base_income = clamp(taxable_income / split)

    rem = base_income
    base_tax = 0.0
    for row in steps:
        amt = float(row.get("amount", 0) or 0.0)
        pct = float(row.get("percent", 0) or 0.0) / 100.0
        if rem <= 0: break
        use = amt if amt > 0 else rem
        chunk = min(rem, use)
        base_tax += chunk * pct
        rem -= chunk
    return base_tax * split

def income_tax_cantonal_city_church(taxable_income: float, canton_abbr: str, bfs_id: int) -> float:
    """
    Compute cantonal+municipal income tax (incl. Ø church) using devbrains tables + factors:
    Tax = BaseTariff(taxable) * (IncomeRateCanton + IncomeRateCity)/100 * (1 + CHURCH_AVG_RATE)
    """
    tariff = first_income_table_for_canton(canton_abbr)
    base = eval_step_tariff_base(tariff, taxable_income, splitting_factor=1)
    fac = FACTORS_BY_BFS.get(int(bfs_id), {})
    rate_mult = (float(fac.get("IncomeRateCanton", 0)) + float(fac.get("IncomeRateCity", 0))) / 100.0
    return base * rate_mult * (1.0 + CHURCH_AVG_RATE)

def corporate_tax_rate_total(canton_abbr: str, bfs_id: int) -> float:
    """
    Effective corporate tax RATE (as a decimal) from devbrains:
    rate = FLATTAX_percent * (ProfitTaxRateCanton + ProfitTaxRateCity)/100
    """
    if not CORP_TARIF:
        return 0.10  # fallback ~10%
    base_pct = 0.0
    # corporate FLATTAX entries store one row with a 'percent'
    table = CORP_TARIF.get("table", [])
    if table:
        base_pct = float(table[0].get("percent", 0.0)) / 100.0
    fac = FACTORS_BY_BFS.get(int(bfs_id), {})
    mult = (float(fac.get("ProfitTaxRateCanton", 0)) + float(fac.get("ProfitTaxRateCity", 0))) / 100.0
    return base_pct * mult

def qualifies_partial(share_pct):
    return (share_pct or 0.0) >= 10.0

def incl_rates(qualifies, canton_abbr):
    # Keep your earlier simplified inclusion logic (fed 70%, cant 70%) when ≥10% share
    inc_fed  = 0.70 if qualifies else 1.00
    inc_cant = 0.70 if qualifies else 1.00
    return inc_fed, inc_cant

# Employer/employee costs
def employer_costs(salary, age_key, ahv=True, fak=0.015, uvg=0.01):
    if not ahv or salary<=0: return dict(ahv=0.0,alv=0.0,bvg=0.0,extra=0.0,total=0.0)
    ahv_emp = AHV_employer * salary
    alv_emp = ALV_employer * min(salary, ALV_ceiling)
    bvg_emp = 0.0
    if salary >= BVG_entry_threshold:
        bvg_emp = (BVG_rates[age_key]/2) * bvg_insured_part(salary)
    extra = fak*salary + uvg*salary
    return dict(ahv=ahv_emp, alv=alv_emp, bvg=bvg_emp, extra=extra, total=ahv_emp+alv_emp+bvg_emp+extra)

def employee_deductions(salary, age_key, ahv=True):
    if not ahv or salary<=0: return dict(ahv=0.0,alv=0.0,bvg=0.0,total=0.0)
    ahv_ee = AHV_employee * salary
    alv_ee = ALV_employee * min(salary, ALV_ceiling)
    bvg_ee = 0.0
    if salary >= BVG_entry_threshold:
        bvg_ee = (BVG_rates[age_key]/2) * bvg_insured_part(salary)
    return dict(ahv=ahv_ee, alv=alv_ee, bvg=bvg_ee, total=ahv_ee+alv_ee+bvg_ee)

# ------------------------- UI -------------------------------
st.title("Lohn vs. Dividende")
st.caption("Jetzt mit devbrains-Daten (Tarife + Faktoren). KER & Vermögenssteuer nicht berücksichtigt. Ø Kirchensteuer angenommen.")

col1, col2 = st.columns(2)
with col1:
    profit         = st.number_input("Firmengewinn **vor Lohn** [CHF]", 0.0, step=10_000.0)
    desired_income = st.number_input("Gewünschte **Gesamtauszahlung** an Inhaber [CHF] (optional)", 0.0, step=10_000.0)
    age_input      = st.number_input("Alter (für BVG-Altersband)", min_value=18, max_value=70, value=40, step=1)
with col2:
    # new: choose canton + commune from devbrains locations
    canton_abbr = st.selectbox("Kanton", sorted(BFS_BY_CANTON.keys()))
    commune_label = st.selectbox("Gemeinde", [n for (n, _b) in BFS_BY_CANTON[canton_abbr]])
    bfs_id = next(b for (n, b) in BFS_BY_CANTON[canton_abbr] if n == commune_label)
    other_inc= st.number_input("Weitere steuerbare Einkünfte [CHF]", 0.0, step=10_000.0)
    debug_mode = st.checkbox("Debug-Informationen anzeigen", value=False)

st.markdown("### Annahmen")
col3, col4 = st.columns(2)
with col3:
    min_salary  = st.number_input("Marktüblicher Mindestlohn [CHF]", 0.0, step=10_000.0, value=120_000.0)
    share_pct   = st.number_input("Beteiligungsquote [%] (Teilbesteuerung ab 10 %)", 0.0, 100.0, 100.0, step=5.0)
with col4:
    fak_rate    = st.number_input("FAK (nur Arbeitgeber) [%]", 0.0, 5.0, 1.5, step=0.1)/100.0
    uvg_rate    = st.number_input("UVG/KTG (Arbeitgeber) [%]", 0.0, 5.0, 1.0, step=0.1)/100.0
    pk_buyin    = st.number_input("PK-Einkauf (privat) [CHF]", 0.0, step=1.0)

optimizer_on = st.checkbox("Optimierer – beste Mischung (Lohn + Dividende) finden", value=True)

# desired payout normalize
if desired_income == 0:
    desired_income = None
elif desired_income > profit:
    desired_income = profit

# ------------------------- Core engines (devbrains) ---------
def cantonal_income_tax_devbrains(taxable, canton_abbr, bfs_id):
    """Cantonal+municipal + Ø church using devbrains tables + factors."""
    return income_tax_cantonal_city_church(clamp(taxable), canton_abbr, bfs_id)

def corp_tax_rate_devbrains(canton_abbr, bfs_id):
    """Effective corporate tax rate (decimal) for remaining profit."""
    return corporate_tax_rate_total(canton_abbr, bfs_id)

def scenario_salary_only(profit, desired, canton_abbr, bfs_id, age_key, ahv_on, other, pk_buy):
    salary = profit if desired is None else min(profit, desired)
    ag = employer_costs(salary, age_key, ahv_on, fak=fak_rate, uvg=uvg_rate)
    an = employee_deductions(salary, age_key, ahv_on)

    profit_after_salary = profit - salary - ag["total"]
    if profit_after_salary < 0:
        st.warning("Bruttolohn inkl. Arbeitgeberabgaben > Gewinn – Restgewinn wird auf 0 gesetzt.")
    profit_after_salary = max(0.0, profit_after_salary)

    corp_rate = corp_tax_rate_devbrains(canton_abbr, bfs_id)
    corp_tax_amt = profit_after_salary * corp_rate

    taxable = clamp(salary - an["total"] + other - pk_buy)
    cant_city_church = cantonal_income_tax_devbrains(taxable, canton_abbr, bfs_id)

    net_owner = salary - an["total"] - cant_city_church

    return {
        "salary": salary, "dividend": 0.0,
        "corp_tax": corp_tax_amt, "income_tax": cant_city_church,
        "net": net_owner, "adjusted_net": net_owner,
        "retained_after_tax": max(0.0, profit_after_salary - corp_tax_amt),
        "blocks": dict(ag=ag, an=an)
    }

def scenario_dividend(profit, desired, canton_abbr, bfs_id, age_key, ahv_on, other, pk_buy,
                      min_salary, share_pct):
    qualifies = qualifies_partial(share_pct)
    inc_fed, inc_cant = incl_rates(qualifies, canton_abbr)

    # Strikt: pay salary up to min_salary before dividends
    salary = min(min_salary, profit if desired is None else min(profit, desired))
    ag = employer_costs(salary, age_key, ahv_on, fak=fak_rate, uvg=uvg_rate)
    an = employee_deductions(salary, age_key, ahv_on)

    profit_after_salary = clamp(profit - salary - ag["total"])
    corp_rate = corp_tax_rate_devbrains(canton_abbr, bfs_id)
    corp_tax_amt = profit_after_salary * corp_rate
    after_corp = profit_after_salary - corp_tax_amt

    desired_left = None if desired is None else clamp(desired - salary)
    gross_div = after_corp if desired_left is None else min(after_corp, desired_left)

    dividend = gross_div if salary >= min_salary else 0.0
    if salary < min_salary and gross_div > 0:
        st.info("Dividende nicht zulässig, da Lohn < Mindestlohn (Strikt-Modus). Ausschüttung = 0.")

    taxable_salary = clamp(salary - an["total"])
    # Partial inclusion only for *cantonal* side here (Bund not modeled in devbrains tariffs dataset)
    taxable_cant = clamp(taxable_salary + dividend * inc_cant + other - pk_buy)

    cant_city_church = cantonal_income_tax_devbrains(taxable_cant, canton_abbr, bfs_id)
    income_tax = cant_city_church

    net_owner = (salary - an["total"]) + dividend - income_tax

    return {
        "salary": salary, "dividend": dividend,
        "corp_tax": corp_tax_amt, "income_tax": income_tax,
        "net": net_owner, "adjusted_net": net_owner,
        "retained_after_tax": clamp(profit_after_salary - corp_tax_amt - dividend),
        "blocks": dict(ag=ag, an=an, inc_cant=inc_cant)
    }

def optimize_mix(step=1000.0):
    """Optimize mix under Strikt rule using devbrains tax."""
    age_key = age_to_band(age_input)
    qualifies = qualifies_partial(share_pct)
    _inc_fed, inc_cant = incl_rates(qualifies, canton_abbr)
    salary_cap = profit if desired_income is None else min(profit, desired_income)

    best = None
    s = 0.0
    corp_rate = corp_tax_rate_devbrains(canton_abbr, bfs_id)
    while s <= salary_cap + 1e-6:
        ag = employer_costs(s, age_key, AHV_ON_DEFAULT, fak=fak_rate, uvg=uvg_rate)
        an = employee_deductions(s, age_key, AHV_ON_DEFAULT)

        profit_after_salary = clamp(profit - s - ag["total"])
        corp_tax_pre = profit_after_salary * corp_rate
        after_corp_pre = profit_after_salary - corp_tax_pre

        desired_left = None if desired_income is None else clamp(desired_income - s)
        pre_dividend = after_corp_pre if desired_left is None else min(after_corp_pre, desired_left)

        dividend = pre_dividend if s >= min_salary else 0.0
        corp_tax_amt = corp_tax_pre

        taxable_salary = clamp(s - an["total"])
        taxable_cant = clamp(taxable_salary + dividend*inc_cant + other_inc - pk_buyin)

        cant_tax = cantonal_income_tax_devbrains(taxable_cant, canton_abbr, bfs_id)
        income_tax = cant_tax

        net_owner = (s - an["total"]) + dividend - income_tax

        res = {"salary": s, "dividend": dividend, "net": net_owner,
               "adjusted_net": net_owner, "income_tax": income_tax,
               "corp_tax": corp_tax_amt,
               "retained_after_tax": clamp(profit_after_salary - corp_tax_amt - dividend)}
        if (best is None) or (net_owner > best["adjusted_net"]):
            best = res
        s += step
    return best

# ------------------------- Run & Render ---------------------
if profit > 0:
    age_key = age_to_band(age_input)
    rule_mode = RULE_MODE_STRIKT
    ahv_on = AHV_ON_DEFAULT

    # Szenario A – Lohn
    A = scenario_salary_only(profit, desired_income, canton_abbr, bfs_id, age_key, ahv_on, other_inc, pk_buyin)

    # Szenario B – Lohn + Dividende
    B = scenario_dividend(profit, desired_income, canton_abbr, bfs_id, age_key, ahv_on, other_inc, pk_buyin,
                          min_salary, share_pct)

    # ----- Display A -----
    st.subheader("Szenario A – 100% Lohn")
    st.write(f"Bruttolohn: **CHF {A['salary']:,.0f}**")
    st.write(f"AG AHV/ALV/BVG: CHF {(A['blocks']['ag']['ahv']+A['blocks']['ag']['alv']+A['blocks']['ag']['bvg']):,.0f}")
    st.write(f"AG FAK/UVG/KTG: CHF {A['blocks']['ag']['extra']:,.0f}")
    st.write(f"AN AHV/ALV/BVG (abzugsfähig): CHF {A['blocks']['an']['total']:,.0f}")
    st.write(f"Körperschaftssteuer Restgewinn: CHF {A['corp_tax']:,.0f}")
    st.write(f"Einkommenssteuer (Kanton + Gemeinde + Kirche Ø): CHF {A['income_tax']:,.0f}")
    st.write(f"Nachsteuerlicher Gewinn einbehalten: CHF {A['retained_after_tax']:,.0f}")
    st.success(f"**Netto an Inhaber (heute):** CHF {A['adjusted_net']:,.0f}")

    # ----- Display B -----
    st.subheader("Szenario B – Lohn + Dividende")
    st.write(f"Bruttolohn: **CHF {B['salary']:,.0f}** | Dividende gesamt: **CHF {B['dividend']:,.0f}**")
    st.write(f"Körperschaftssteuer (nach Lohn): CHF {B['corp_tax']:,.0f}")
    st.write(f"Einkommenssteuer (Kanton + Gemeinde + Kirche Ø): CHF {B['income_tax']:,.0f}")
    st.write(f"Nachsteuerlicher Gewinn einbehalten: CHF {B['retained_after_tax']:,.0f}")
    st.caption(f"Teilbesteuerung Dividenden (kantonal angewandt): {int(B['blocks']['inc_cant']*100)}% (ab 10% Beteiligung).")
    st.success(f"**Netto an Inhaber (heute):** CHF {B['adjusted_net']:,.0f}")

    # ----- Vergleich -----
    st.markdown("---")
    st.subheader("Vergleich (heutiger Nettozufluss)")
    c1, c2 = st.columns(2)
    with c1: st.metric("A: Lohn", f"CHF {A['adjusted_net']:,.0f}")
    with c2: st.metric("B: Lohn + Dividende", f"CHF {B['adjusted_net']:,.0f}")

    # ----- Optimizer -----
    if optimizer_on:
        st.markdown("---")
        st.subheader("Optimierer – beste Mischung (unter Strikt-Regel)")
        best = optimize_mix(step=1000.0)
        st.write(f"**Optimaler Lohn:** CHF {best['salary']:,.0f}  |  **Dividende:** CHF {best['dividend']:,.0f}")
        st.write(f"Einkommenssteuer gesamt (Kanton+Gemeinde+Kirche Ø): CHF {best['income_tax']:,.0f}")
        st.write(f"Körperschaftssteuer: CHF {best['corp_tax']:,.0f}")
        st.write(f"Nachsteuerlich einbehalten: CHF {best['retained_after_tax']:,.0f}")
        st.success(f"**Max. Netto an Inhaber (heute):** CHF {best['adjusted_net']:,.0f}")

    # ----- Debug -----
    if debug_mode:
        st.markdown("---")
        st.subheader("Debug-Informationen")
        fac = FACTORS_BY_BFS.get(int(bfs_id), {})
        corp_rate = corp_tax_rate_devbrains(canton_abbr, bfs_id)
        st.write(f"BFS: {bfs_id} | Kanton: {canton_abbr} | Gemeinde: {commune_label}")
        st.write(f"Faktoren Einkommen: Kanton {fac.get('IncomeRateCanton')}% + Gemeinde {fac.get('IncomeRateCity')}%")
        st.write(f"Faktoren Gewinn:   Kanton {fac.get('ProfitTaxRateCanton')}% + Gemeinde {fac.get('ProfitTaxRateCity')}%")
        st.write(f"Körperschaftssteuer-Satz gesamt (effektiv): {corp_rate:.2%}")
        st.write(f"Ø Kirchensteuer-Zuschlag: {CHURCH_AVG_RATE:.0%}")
        st.caption("Tariftabellen aus data/parsed/2025/tarifs/2.json (Schritt-Logik).")
else:
    st.warning("Bitte Gewinn > 0 eingeben, um die Berechnung zu starten.")

# ----- Hinweise & Annahmen (kleiner, einklappbar) -----
with st.expander("Hinweise & Annahmen", expanded=False):
    st.markdown(
        f"- **Kirchensteuer:** Ø-Zuschlag von **{int(CHURCH_AVG_RATE*100)}%** auf die kant./gemeindl. Steuer berücksichtigt.\n"
        f"- **AHV/ALV/BVG:** Standardmäßig **angewendet** (AG- & AN-Anteile berechnet).\n"
        f"- **Regelmodus:** **Strikt** – Dividenden erst zulässig, wenn der Lohn ≥ Mindestlohn ist.\n"
        f"- **BVG-Altersband:** Automatische Zuordnung anhand des eingegebenen Alters.\n"
        f"- **PK-Einkauf:** Freie Eingabe reduziert das steuerbare Einkommen (Sperrfristen beachten).\n"
        f"- **Nicht berücksichtigt:** Bundessteuer (direkt), Kapitalreserven (KER), Vermögenssteuer-Impact.\n"
        f"- **Datenquellen (devbrains-Struktur):** locations.json → BFS-Auswahl; factors/1.json → Steuerfüsse; tarifs/2.json → Tariftabellen.\n"
    )
