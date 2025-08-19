# app.py — Lohn vs. Dividende (devbrains 2025 schema: locations + factors + tarifs)
import json, math, pathlib
import streamlit as st
from functools import lru_cache
from collections import defaultdict

# ========== Paths / Year ==========
BASE_DIR = pathlib.Path(__file__).parent
PARSED_DIR = BASE_DIR / "data" / "parsed"
YEAR = "2025"  # adjust if you later add a year selector

# ========== Constants ==========
CHURCH_AVG_RATE = 0.12  # Ø Kirchensteuer-Zuschlag auf kant./gemeindl. Steuer (12%)
RULE_MODE_STRIKT = True
AHV_ON_DEFAULT = True

# Social security (kept from your earlier config)
AHV_employer   = 0.053
AHV_employee   = 0.053
ALV_employer   = 0.011
ALV_employee   = 0.011
ALV_ceiling    = 148_200.0
BVG_rates = {"25-34": 0.07, "35-44": 0.10, "45-54": 0.15, "55-65": 0.18}
BVG_entry_threshold = 22_680.0
BVG_coord_deduction = 26_460.0
BVG_max_insured     = 90_720.0

# Dividend partial-inclusion (simplified defaults; adjust if you later add canton-specific)
DIV_PARTIAL_FED = 0.70
DIV_PARTIAL_CANT = 0.70

# ========== Small helpers ==========
def clamp(x): return max(0.0, float(x or 0.0))
def bvg_insured_part(salary): return max(0.0, min(salary, BVG_max_insured) - BVG_coord_deduction)
def age_to_band(age:int)->str:
    a = int(age or 35)
    if a<35: return "25-34"
    if a<45: return "35-44"
    if a<55: return "45-54"
    return "55-65"

@lru_cache(None)
def _load_json(p: pathlib.Path):
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

# ========== Data loaders (devbrains schema) ==========
def load_locations():
    """data/parsed/YEAR/locations.json — flat array with BfsID, BfsName, Canton, CantonID, TaxLocationID."""
    path = PARSED_DIR / YEAR / "locations.json"
    return _load_json(path)

def load_factors(canton_id: int):
    """data/parsed/YEAR/factors/{CantonID}.json — array per location (match by Location.BfsID)."""
    path = PARSED_DIR / YEAR / "factors" / f"{canton_id}.json"
    return _load_json(path)

def load_tarifs(canton_id: int):
    """data/parsed/YEAR/tarifs/{CantonID}.json — array of tariff tables for this canton."""
    path = PARSED_DIR / YEAR / "tarifs" / f"{canton_id}.json"
    return _load_json(path)

def load_tarifs_federal_if_any():
    """Optional: federal tables live in tarifs/0.json (if included in dataset)."""
    path = PARSED_DIR / YEAR / "tarifs" / "0.json"
    return _load_json(path) if path.exists() else []

# ========== Tariff evaluation ==========
def _find_table(tarifs, tax_type: str, group: str = "ALLE"):
    """
    Pick a table for a given tax_type ('EINKOMMENSSTEUER', 'GEWINNSTEUER', ...).
    Prefer group 'ALLE' if available.
    """
    cand = [t for t in tarifs if t.get("taxType")==tax_type and (t.get("group","")==group)]
    if cand: return cand[0]
    # if no ALLE available, take first with matching type
    for t in tarifs:
        if t.get("taxType")==tax_type: return t
    return None

def _eval_step_table(table_rows, amount: float) -> float:
    """
    Generic step evaluator:
    - Each row has {'percent': <percent>, 'amount': <width or 0 for open-ended>}.
    - We compute sum(percent% * min(remaining, amount)).
    """
    rem = clamp(amount)
    tax = 0.0
    for row in table_rows:
        pct = float(row.get("percent", 0.0)) / 100.0
        width = float(row.get("amount", 0.0))
        if rem <= 0: break
        use = rem if width<=0 else min(rem, width)
        tax += use * pct
        rem -= use
    return tax

def eval_tariff(tariff_obj, amount: float, force_split_factor:int = 1) -> float:
    """
    Evaluate a tariff object. Supports:
    - 'ZUERICH' / step tables: use _eval_step_table
    - 'FLATTAX': percent on full amount
    - 'splitting': if >1, we simulate splitting (default off here)
    """
    if not tariff_obj: return 0.0
    table_type = (tariff_obj.get("tableType") or "").upper()
    split_declared = int(tariff_obj.get("splitting") or 1)
    split = 1 if force_split_factor==1 else max(1, force_split_factor)
    base = clamp(amount / split)

    if table_type == "FLATTAX":
        # One row with 'percent'
        rows = tariff_obj.get("table") or []
        pct = float(rows[0].get("percent", 0.0))/100.0 if rows else 0.0
        tax = base * pct
        return tax * split

    # Default: treat as step table
    rows = tariff_obj.get("table") or []
    tax = _eval_step_table(rows, base)
    return tax * split

# ========== Factor lookup ==========
def factors_for_bfs(canton_id: int, bfs_id: int):
    """
    Returns a dict of multipliers for the selected municipality.
    If no matching record found, we return safe defaults.
    """
    arr = load_factors(canton_id)
    for rec in arr:
        loc = rec.get("Location") or {}
        if int(loc.get("BfsID", -1)) == int(bfs_id):
            return {
                "IncomeRateCanton": float(rec.get("IncomeRateCanton", 100.0)),
                "IncomeRateCity":   float(rec.get("IncomeRateCity",   0.0)),
                "ProfitTaxRateCanton": float(rec.get("ProfitTaxRateCanton", 100.0)),
                "ProfitTaxRateCity":   float(rec.get("ProfitTaxRateCity",   0.0)),
            }
    # defaults (still produce something reasonable)
    return {"IncomeRateCanton":100.0,"IncomeRateCity":0.0,"ProfitTaxRateCanton":100.0,"ProfitTaxRateCity":0.0}

# ========== Personal tax calculators ==========
def personal_income_tax_components(taxable_income: float, canton_id: int, bfs_id: int):
    """
    Compute income tax as:
      Federal component (if tarifs/0.json exists) +
      [ BaseCantonalTariff(amount) * (IncomeRateCanton + IncomeRateCity)/100 ] * (1 + CHURCH_AVG_RATE)
    """
    t = clamp(taxable_income)

    # Federal (optional, only if present in dataset)
    fed_tarifs = load_tarifs_federal_if_any()
    fed_table = _find_table(fed_tarifs, "EINKOMMENSSTEUER", group="ALLE")
    fed_tax = eval_tariff(fed_table, t, force_split_factor=1) if fed_table else 0.0

    # Cantonal + Municipal + church
    cant_tarifs = load_tarifs(canton_id)
    cant_table = _find_table(cant_tarifs, "EINKOMMENSSTEUER", group="ALLE")
    base_cant = eval_tariff(cant_table, t, force_split_factor=1) if cant_table else 0.0

    fac = factors_for_bfs(canton_id, bfs_id)
    mult = (fac["IncomeRateCanton"] + fac["IncomeRateCity"]) / 100.0
    cant_city = base_cant * mult
    cant_city_church = cant_city * (1.0 + CHURCH_AVG_RATE)

    return fed_tax, cant_city_church, (fed_tax + cant_city_church)

# ========== Corporate tax ==========
def corporate_tax_rate(canton_id: int, bfs_id: int) -> float:
    """
    Effective corporate tax rate (decimal):
      rate = base_percent_from_tarifs("GEWINNSTEUER") * (ProfitTaxRateCanton + ProfitTaxRateCity)/100
    """
    tarifs = load_tarifs(canton_id)
    corp = _find_table(tarifs, "GEWINNSTEUER")
    if not corp:
        return 0.10  # safe fallback
    rows = corp.get("table") or []
    base_pct = float(rows[0].get("percent", 0.0))/100.0 if rows else 0.0
    fac = factors_for_bfs(canton_id, bfs_id)
    mult = (fac["ProfitTaxRateCanton"] + fac["ProfitTaxRateCity"]) / 100.0
    return base_pct * mult

# ========== Payroll side-costs ==========
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

def qualifies_partial(share_pct): return (share_pct or 0.0) >= 10.0

# ========== UI: Locations ==========
LOC = load_locations()
# Build Canton -> [(name, bfs, canton_id)]
by_canton = defaultdict(list)
for r in LOC:
    by_canton[r["Canton"]].append((r["BfsName"], int(r["BfsID"]), int(r["CantonID"])))
for k in list(by_canton.keys()):
    by_canton[k].sort(key=lambda x: x[0])

# ========== Streamlit UI ==========
st.title("Lohn vs. Dividende")
st.caption("Devbrains-Parser (2025): locations + factors + tarifs. Ø Kirchensteuer; keine KER/Vermögenssteuer.")

col1, col2 = st.columns(2)
with col1:
    profit         = st.number_input("Firmengewinn **vor Lohn** [CHF]", 0.0, step=10_000.0)
    desired_income = st.number_input("Gewünschte **Gesamtauszahlung** an Inhaber [CHF] (optional)", 0.0, step=10_000.0)
    age_input      = st.number_input("Alter (für BVG-Altersband)", min_value=18, max_value=70, value=40, step=1)
with col2:
    canton_abbr = st.selectbox("Kanton", sorted(by_canton.keys()))
    commune_label = st.selectbox("Gemeinde", [n for (n, _b, _id) in by_canton[canton_abbr]])
    bfs_id, canton_id = next((b, cid) for (n, b, cid) in by_canton[canton_abbr] if n==commune_label)
    other_inc = st.number_input("Weitere steuerbare Einkünfte [CHF]", 0.0, step=10_000.0)
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

# Normalize desired payout
if desired_income == 0:
    desired_income = None
elif desired_income > profit:
    desired_income = profit

# ========== Scenarios ==========
def scenario_salary_only(profit, desired, canton_id, bfs_id, age_key, ahv_on, other, pk_buy):
    salary = profit if desired is None else min(profit, desired)
    ag = employer_costs(salary, age_key, ahv_on, fak=fak_rate, uvg=uvg_rate)
    an = employee_deductions(salary, age_key, ahv_on)

    rest = profit - salary - ag["total"]
    if rest < 0:
        st.warning("Bruttolohn inkl. Arbeitgeberabgaben > Gewinn – Restgewinn wird auf 0 gesetzt.")
    rest = max(0.0, rest)

    corp_rate = corporate_tax_rate(canton_id, bfs_id)
    corp_tax_amt = rest * corp_rate

    taxable = clamp(salary - an["total"] + other - pk_buy)
    fed_tax, cant_tax, total_tax = personal_income_tax_components(taxable, canton_id, bfs_id)

    net_owner = salary - an["total"] - total_tax

    return {
        "salary": salary, "dividend": 0.0,
        "corp_tax": corp_tax_amt, "income_tax": total_tax,
        "net": net_owner, "adjusted_net": net_owner,
        "retained_after_tax": max(0.0, rest - corp_tax_amt),
        "blocks": dict(ag=ag, an=an, fed=fed_tax, cant=cant_tax)
    }

def scenario_dividend(profit, desired, canton_id, bfs_id, age_key, ahv_on, other, pk_buy,
                      min_salary, share_pct):
    # Strict: must reach min_salary before any dividend
    salary = min(min_salary, profit if desired is None else min(profit, desired))
    ag = employer_costs(salary, age_key, ahv_on, fak=fak_rate, uvg=uvg_rate)
    an = employee_deductions(salary, age_key, ahv_on)

    rest = clamp(profit - salary - ag["total"])
    corp_rate = corporate_tax_rate(canton_id, bfs_id)
    corp_tax_amt = rest * corp_rate
    after_corp = rest - corp_tax_amt

    desired_left = None if desired is None else clamp(desired - salary)
    gross_div = after_corp if desired_left is None else min(after_corp, desired_left)

    dividend = gross_div if salary >= min_salary else 0.0
    if salary < min_salary and gross_div > 0:
        st.info("Dividende nicht zulässig, da Lohn < Mindestlohn (Strikt-Modus). Ausschüttung = 0.")

    # Partial inclusion (federal+cantonal) – using simplified 70%/70% when share ≥ 10%
    qualifies = qualifies_partial(share_pct)
    inc_fed = DIV_PARTIAL_FED if qualifies else 1.0
    inc_cant = DIV_PARTIAL_CANT if qualifies else 1.0

    taxable_salary = clamp(salary - an["total"])
    taxable_fed  = clamp(taxable_salary + dividend*inc_fed  + other - pk_buy)
    taxable_cant = clamp(taxable_salary + dividend*inc_cant + other - pk_buy)

    # Compute components with the devbrains tables
    fed_tax, _cant_dummy, _ = personal_income_tax_components(taxable_fed, canton_id, bfs_id)
    _fed_dummy, cant_tax, _ = personal_income_tax_components(taxable_cant, canton_id, bfs_id)
    total_tax = fed_tax + cant_tax

    net_owner = (salary - an["total"]) + dividend - total_tax

    return {
        "salary": salary, "dividend": dividend,
        "corp_tax": corp_tax_amt, "income_tax": total_tax,
        "net": net_owner, "adjusted_net": net_owner,
        "retained_after_tax": clamp(rest - corp_tax_amt - dividend),
        "blocks": dict(ag=ag, an=an, inc_fed=inc_fed, inc_cant=inc_cant)
    }

def optimize_mix(step=1000.0):
    """Optimize under strict rule, using devbrains income/corp tax."""
    age_key = age_to_band(age_input)
    qualifies = qualifies_partial(share_pct)
    inc_fed = DIV_PARTIAL_FED if qualifies else 1.0
    inc_cant = DIV_PARTIAL_CANT if qualifies else 1.0

    best = None
    s = 0.0
    corp_rate = corporate_tax_rate(canton_id, bfs_id)
    while s <= (profit if desired_income is None else min(profit, desired_income)) + 1e-6:
        ag = employer_costs(s, age_key, AHV_ON_DEFAULT, fak=fak_rate, uvg=uvg_rate)
        an = employee_deductions(s, age_key, AHV_ON_DEFAULT)

        rest = clamp(profit - s - ag["total"])
        corp_tax_amt = rest * corp_rate
        after_corp = rest - corp_tax_amt

        desired_left = None if desired_income is None else clamp(desired_income - s)
        pre_div = after_corp if desired_left is None else min(after_corp, desired_left)

        dividend = pre_div if s >= min_salary else 0.0

        taxable_salary = clamp(s - an["total"])
        taxable_fed  = clamp(taxable_salary + dividend*inc_fed  + other_inc - pk_buyin)
        taxable_cant = clamp(taxable_salary + dividend*inc_cant + other_inc - pk_buyin)

        fed_tax, _, _ = personal_income_tax_components(taxable_fed, canton_id, bfs_id)
        _, cant_tax, _ = personal_income_tax_components(taxable_cant, canton_id, bfs_id)
        income_tax = fed_tax + cant_tax

        net_owner = (s - an["total"]) + dividend - income_tax
        res = {"salary": s, "dividend": dividend, "net": net_owner, "adjusted_net": net_owner,
               "income_tax": income_tax, "corp_tax": corp_tax_amt,
               "retained_after_tax": clamp(rest - corp_tax_amt - dividend)}
        if (best is None) or (net_owner > best["adjusted_net"]):
            best = res
        s += step
    return best

# ========== Run & Render ==========
if profit > 0:
    age_key = age_to_band(age_input)

    A = scenario_salary_only(profit, desired_income, canton_id, bfs_id, age_key, AHV_ON_DEFAULT, other_inc, pk_buyin)
    B = scenario_dividend(profit, desired_income, canton_id, bfs_id, age_key, AHV_ON_DEFAULT, other_inc, pk_buyin,
                          min_salary, share_pct)

    st.subheader("Szenario A – 100% Lohn")
    st.write(f"Bruttolohn: **CHF {A['salary']:,.0f}**")
    st.write(f"AG AHV/ALV/BVG: CHF {(A['blocks']['ag']['ahv']+A['blocks']['ag']['alv']+A['blocks']['ag']['bvg']):,.0f}")
    st.write(f"AG FAK/UVG/KTG: CHF {A['blocks']['ag']['extra']:,.0f}")
    st.write(f"AN AHV/ALV/BVG (abzugsfähig): CHF {A['blocks']['an']['total']:,.0f}")
    st.write(f"Körperschaftssteuer Restgewinn: CHF {A['corp_tax']:,.0f}")
    st.write(f"Einkommenssteuer (Bund + Kanton + Gemeinde + Kirche Ø): CHF {A['income_tax']:,.0f}")
    st.write(f"Nachsteuerlicher Gewinn einbehalten: CHF {A['retained_after_tax']:,.0f}")
    st.success(f"**Netto an Inhaber (heute):** CHF {A['adjusted_net']:,.0f}")

    st.subheader("Szenario B – Lohn + Dividende")
    st.write(f"Bruttolohn: **CHF {B['salary']:,.0f}** | Dividende gesamt: **CHF {B['dividend']:,.0f}**")
    st.write(f"Körperschaftssteuer (nach Lohn): CHF {B['corp_tax']:,.0f}")
    st.write(f"Einkommenssteuer (Bund + Kanton + Gemeinde + Kirche Ø): CHF {B['income_tax']:,.0f}")
    st.write(f"Nachsteuerlicher Gewinn einbehalten: CHF {B['retained_after_tax']:,.0f}")
    st.caption(f"Teilbesteuerung Dividenden (vereinfachte Annahme): Bund {int((DIV_PARTIAL_FED if qualifies_partial(share_pct) else 1.0)*100)}%, "
               f"Kanton {int((DIV_PARTIAL_CANT if qualifies_partial(share_pct) else 1.0)*100)}% (ab 10% Beteiligung).")
    st.success(f"**Netto an Inhaber (heute):** CHF {B['adjusted_net']:,.0f}")

    st.markdown("---")
    st.subheader("Vergleich (heutiger Nettozufluss)")
    c1, c2 = st.columns(2)
    with c1: st.metric("A: Lohn", f"CHF {A['adjusted_net']:,.0f}")
    with c2: st.metric("B: Lohn + Dividende", f"CHF {B['adjusted_net']:,.0f}")

    if optimizer_on:
        st.markdown("---")
        st.subheader("Optimierer – beste Mischung (unter Strikt-Regel)")
        best = optimize_mix(step=1000.0)
        st.write(f"**Optimaler Lohn:** CHF {best['salary']:,.0f}  |  **Dividende:** CHF {best['dividend']:,.0f}")
        st.write(f"Einkommenssteuer gesamt (Bund + Kant./Gem.+Kirche Ø): CHF {best['income_tax']:,.0f}")
        st.write(f"Körperschaftssteuer: CHF {best['corp_tax']:,.0f}")
        st.write(f"Nachsteuerlich einbehalten: CHF {best['retained_after_tax']:,.0f}")
        st.success(f"**Max. Netto an Inhaber (heute):** CHF {best['adjusted_net']:,.0f}")

    if debug_mode:
        st.markdown("---")
        st.subheader("Debug-Informationen")
        fac = factors_for_bfs(canton_id, bfs_id)
        st.write(f"BFS: {bfs_id} | Kanton: {canton_abbr} | CantonID: {canton_id} | Gemeinde: {commune_label}")
        st.write(f"Faktoren Einkommen: Kanton {fac['IncomeRateCanton']}% + Gemeinde {fac['IncomeRateCity']}%")
        st.write(f"Faktoren Gewinn:   Kanton {fac['ProfitTaxRateCanton']}% + Gemeinde {fac['ProfitTaxRateCity']}%")
        cr = corporate_tax_rate(canton_id, bfs_id)
        st.write(f"Körperschaftssteuer-Satz gesamt (effektiv): {cr:.2%}")
        fed_tarifs = load_tarifs_federal_if_any()
        fed_present = any(t.get('taxType')=='EINKOMMENSSTEUER' for t in fed_tarifs)
        st.write(f"Bundestarife vorhanden: {'Ja' if fed_present else 'Nein'} (tarifs/0.json)")
else:
    st.warning("Bitte Gewinn > 0 eingeben, um die Berechnung zu starten.")

with st.expander("Hinweise & Annahmen", expanded=False):
    st.markdown(
        f"- **Kirchensteuer:** Ø-Zuschlag von **{int(CHURCH_AVG_RATE*100)}%** auf die kant./gemeindl. Steuer.\n"
        f"- **AHV/ALV/BVG:** Standardmäßig **angewendet**.\n"
        f"- **Regelmodus:** **Strikt** – Dividenden erst zulässig, wenn der Lohn ≥ Mindestlohn ist.\n"
        f"- **BVG-Altersband:** Automatische Zuordnung anhand des Alters.\n"
        f"- **PK-Einkauf:** Reduziert das steuerbare Einkommen (Sperrfristen beachten).\n"
        f"- **Datengrundlage:** `locations.json` → BFS/CantonID; `factors/{{CantonID}}.json` → Multiplikatoren je Gemeinde; "
        f"`tarifs/{{CantonID}}.json` → Tariftabellen je Kanton; optional `tarifs/0.json` für Bund.\n"
        f"- **Nicht berücksichtigt:** KER, Vermögenssteuer-Impact."
    )
