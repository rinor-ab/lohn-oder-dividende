# app.py – Schweizer Lohn vs. Dividende Rechner (Maximally Patched & Meticulous)
import json, math, pathlib
import streamlit as st

DATA_DIR = pathlib.Path(__file__).parent
files = {
    "steuer": "Steuerfuesse.json",
    "cant_income": "Income_Tax_Cantons.json",
    "fed_income": "Income_Tax_Confederation.json",
    "corp_tax": "Corporate_Income_Tax.json",
    "social": "Social_Security_Contributions.json",
    "div_inclusion": "Teilbesteuerung_Dividenden.json",
}

# ------------------------- Hilfsfunktionen --------------------------------------
def load_json(name, default):
    fp = DATA_DIR / files[name]
    try:
        with fp.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        st.warning(f"Datei {files[name]} nicht gefunden. Standardwerte werden verwendet.")
        return default

def is_nan(x):
    try:
        return isinstance(x, float) and math.isnan(x)
    except:
        return False

def nan_to_zero(x):
    return 0.0 if (x is None or is_nan(x)) else x

def clamp_nonneg(x):
    return max(0.0, float(x or 0.0))

# ------------------------- Daten laden ------------------------------------------
steuerfuesse          = load_json("steuer", [])
income_tax_cantons    = load_json("cant_income", {})
income_tax_conf_raw   = load_json("fed_income", [])
corporate_tax         = load_json("corp_tax", {})
social_sec            = load_json("social", {})
dividend_inclusion    = load_json("div_inclusion", {})

# --- Bundessteuer-Brackets normalisieren & sortieren ---
def normalize_fed_brackets(raw):
    cleaned = []
    seen = set()
    for d in raw:
        thr  = d.get("Taxable income for federal tax", 0)
        base = d.get("Base amount CHF", 0)
        rate = d.get("Additional %", 0)
        if thr is None or base is None or rate is None:
            continue
        if any(is_nan(v) for v in (thr, base, rate)):
            continue
        try:
            thr  = float(thr)
            base = float(base)
            rate = float(rate) / 100.0
        except:
            continue
        if thr < 0:
            continue
        if thr in seen:
            continue
        seen.add(thr)
        cleaned.append({"thr": thr, "base": base, "rate": rate})
    cleaned.sort(key=lambda x: x["thr"])
    if not cleaned or cleaned[0]["thr"] > 0:
        cleaned.insert(0, {"thr": 0.0, "base": 0.0, "rate": 0.0})
    return cleaned

income_tax_conf = normalize_fed_brackets(income_tax_conf_raw)

# ------------------------- Sozialversicherungen (Defaults aus JSON) -------------
AHV_employer   = social_sec.get("AHV_IV_EO_EmployerShare", 0.053)
AHV_employee   = social_sec.get("AHV_IV_EO_EmployeeShare", 0.053)

ALV_employer   = social_sec.get("ALV_EmployerShare", 0.011)
ALV_employee   = social_sec.get("ALV_EmployeeShare", 0.011)
ALV_ceiling    = social_sec.get("ALV_Ceiling", 148200.0)
ALV_solidarity = 0.0  # seit 2025 abgeschafft

BVG_rates = {
    "25-34": social_sec.get("BVG_Rate_25_34", 0.07),
    "35-44": social_sec.get("BVG_Rate_35_44", 0.10),
    "45-54": social_sec.get("BVG_Rate_45_54", 0.15),
    "55-65": social_sec.get("BVG_Rate_55_65", 0.18),
}
BVG_entry_threshold = social_sec.get("BVG_EntryThreshold", 22680.0)
BVG_coord_deduction = social_sec.get("BVG_CoordDeduction", 26460.0)
BVG_max_insured     = social_sec.get("BVG_MaxInsuredSalary", 90720.0)

def bvg_insured_part(salary):
    return max(0.0, min(salary, BVG_max_insured) - BVG_coord_deduction)

# ------------------------- Kantons-/Gemeindemapping -----------------------------
canton_to_communes = {}
for row in steuerfuesse:
    kanton  = row.get("Kanton")
    gemeinde= row.get("Gemeinde")
    if not kanton or not gemeinde or kanton == "Kanton":
        continue
    canton_to_communes.setdefault(kanton, []).append(gemeinde)
for k in canton_to_communes:
    canton_to_communes[k].sort()

if not canton_to_communes:
    st.error("Keine Steuerdaten gefunden. Fallback aktiviert.")
    canton_to_communes = {"Zürich": ["Zürich"], "Bern": ["Bern"]}

# ------------------------- UI ---------------------------------------------------
st.title("🇨🇭 Vergleich: Lohn vs. Dividende")
st.caption("Berechnet Nettobezüge für Schweizer Unternehmer – inkl. AHV/ALV/BVG, direkter Steuern, Teilbesteuerung & Realitätschecks.")

col1, col2 = st.columns(2)
with col1:
    profit         = st.number_input("Firmengewinn **vor Lohn** [CHF]", 0.0, step=10_000.0)
    desired_income = st.number_input("Gewünschte Auszahlung an Inhaber [CHF] (optional)", 0.0, step=10_000.0)
    ahv_subject    = st.radio("AHV/ALV/BVG auf Lohn?", ["Ja", "Nein"])
    age_band = st.selectbox("Altersband (BVG)",
                            ["25-34 (7%)", "35-44 (10%)", "45-54 (15%)", "55-65 (18%)"],
                            index=1)
with col2:
    canton   = st.selectbox("Kanton", sorted(canton_to_communes.keys()))
    commune  = st.selectbox("Gemeinde", canton_to_communes.get(canton, ["Default"]))
    other_inc= st.number_input("Weitere steuerbare Einkünfte [CHF]", 0.0, step=10_000.0)
    private_deductions = st.number_input("Private Abzüge (z. B. Säule 3a, Berufsauslagen) [CHF]", 0.0, step=5_000.0)
    debug_mode = st.checkbox("Debug-Informationen anzeigen", value=False)
    st.session_state.debug_mode = debug_mode

st.markdown("### Realitätschecks & Annahmen")
col3, col4 = st.columns(2)
with col3:
    min_salary = st.number_input("Marktüblicher Mindestlohn [CHF]", 0.0, step=10_000.0, value=120_000.0)
    ahv_risk   = st.checkbox("AHV-Umqualifizierung auf Dividenden anwenden (falls Lohn < Mindestlohn)", value=True)
    share_pct  = st.number_input("Beteiligungsquote [%]", min_value=0.0, max_value=100.0, value=100.0, step=5.0)
with col4:
    fak_rate     = st.number_input("FAK (nur Arbeitgeber) [%]", 0.0, 5.0, 1.5, step=0.1) / 100.0
    uvg_ktg_rate = st.number_input("UVG/KTG (Arbeitgeber) [%]", 0.0, 5.0, 1.0, step=0.1) / 100.0
    church_rate  = st.number_input("Kirchensteuer-Zuschlag auf kant./gemeindl. Steuer [%]", 0.0, 30.0, 0.0, step=0.5) / 100.0

optimizer_on = st.checkbox("🔎 Beste Mischung (Lohn + Dividende) automatisch optimieren", value=False)

# gewünschte Auszahlung validieren
if desired_income == 0:
    desired_income = None
elif desired_income > profit:
    desired_income = profit

# ------------------------- Steuern (Funktionen) ---------------------------------
def federal_income_tax(taxable):
    """Stückweise-linear; taxable >= 0."""
    t = clamp_nonneg(taxable)
    prev_thr = 0.0
    for b in income_tax_conf:
        thr, base, rate = b["thr"], b["base"], b["rate"]
        if t <= thr:
            return base + (t - prev_thr) * rate
        prev_thr = thr
    top = income_tax_conf[-1]
    return top["base"] + (t - top["thr"]) * top["rate"]

def cantonal_income_tax(taxable, kanton, gemeinde):
    """Kantonale Basistarife + Multiplikatoren (Kanton+Gemeinde)."""
    t = clamp_nonneg(taxable)
    brackets = income_tax_cantons.get(kanton, [])
    cantonal_base_tax = 0.0
    remaining = t
    for bracket in brackets:
        chunk_size = (bracket.get("For the next CHF", 0) or 0)
        rate = (bracket.get("Additional %", 0) or 0) / 100.0
        if chunk_size == 0:
            cantonal_base_tax += remaining * rate
            remaining = 0
            break
        chunk = min(remaining, chunk_size)
        cantonal_base_tax += chunk * rate
        remaining -= chunk
        if remaining <= 0:
            break
    if remaining > 0 and brackets:
        cantonal_base_tax += remaining * (brackets[-1].get("Additional %", 0) / 100.0)

    kant_mult, comm_mult = 1.0, 0.0
    for row in steuerfuesse:
        if row.get("Kanton") == kanton and row.get("Gemeinde") == gemeinde:
            kant_mult = nan_to_zero(row.get("Einkommen_Kanton", 1.0))
            comm_mult = nan_to_zero(row.get("Einkommen_Gemeinde", 0.0))
            break
    return cantonal_base_tax * (kant_mult + comm_mult)

def qualifies_partial_taxation(share_pct_):
    return (share_pct_ or 0.0) >= 10.0

def get_div_incl_canton(kanton, qualifies):
    base = dividend_inclusion.get(kanton, 0.70)
    return base if qualifies else 1.00

def get_div_incl_fed(qualifies):
    return 0.70 if qualifies else 1.00

# ------------------------- Körperschaftssteuer-Setup ----------------------------
# Bund (8.5 %) + Kanton/Gemeinde (Basis aus JSON mit Multiplikatoren)
def corporate_tax_components(canton, commune):
    fed_corp = corporate_tax.get("Confederation", 0.085)
    cant_corp_data = corporate_tax.get(canton, 0.0)
    if isinstance(cant_corp_data, dict):
        cant_corp_base = nan_to_zero(cant_corp_data.get("rate", cant_corp_data.get("cantonal", 0.0)))
    else:
        cant_corp_base = nan_to_zero(cant_corp_data)
    canton_mult, comm_mult = 1.0, 0.0
    for row in steuerfuesse:
        if row.get("Kanton") == canton and row.get("Gemeinde") == commune:
            canton_mult = nan_to_zero(row.get("Gewinn_Kanton", 1.0))
            comm_mult   = nan_to_zero(row.get("Gewinn_Gemeinde", 0.0))
            break
    local_corp = cant_corp_base * (canton_mult + comm_mult)
    total_corp = fed_corp + local_corp
    return fed_corp, local_corp, total_corp

fed_corp, local_corp, total_corp = corporate_tax_components(canton, commune)

# ------------------------- Lohn: Beiträge-Helper --------------------------------
def employer_costs_on_salary(salary, ahv_on=True, fak=fak_rate, uvg=uvg_ktg_rate, include_bvg=True):
    """AG-Kosten (AHV/ALV/BVG + FAK/UVG/KTG)."""
    if not ahv_on or salary <= 0:
        return 0.0, 0.0, 0.0, 0.0  # ahv_emp, alv_emp, bvg_emp, extra_costs
    ahv_emp = AHV_employer * salary
    alv_emp = ALV_employer * min(salary, ALV_ceiling)
    bvg_emp = 0.0
    if include_bvg and salary >= BVG_entry_threshold:
        bvg_emp = (BVG_rates[age_band.split()[0]] / 2) * bvg_insured_part(salary)
    extra = fak * salary + uvg * salary
    return ahv_emp, alv_emp, bvg_emp, extra

def employee_deductions_on_salary(salary, ahv_on=True, include_bvg=True):
    """AN-Abzüge (AHV/ALV/BVG)."""
    if not ahv_on or salary <= 0:
        return 0.0, 0.0, 0.0, 0.0  # ahv_ee, alv_ee, bvg_ee, total
    ahv_ee = AHV_employee * salary
    alv_ee = ALV_employee * min(salary, ALV_ceiling)
    bvg_ee = 0.0
    if include_bvg and salary >= BVG_entry_threshold:
        bvg_ee = (BVG_rates[age_band.split()[0]] / 2) * bvg_insured_part(salary)
    total = ahv_ee + alv_ee + bvg_ee
    return ahv_ee, alv_ee, bvg_ee, total

# ------------------------- Szenario A: Reiner Lohn ------------------------------
def scenario_salary_only():
    salary = min(desired_income if desired_income is not None else profit, profit)
    # AG-Kosten
    ahv_emp, alv_emp, bvg_emp, extra = employer_costs_on_salary(salary, ahv_on=(ahv_subject == "Ja"))
    employer_cost = ahv_emp + alv_emp + bvg_emp + extra

    # AN-Abzüge
    ahv_ee, alv_ee, bvg_ee, ee_total = employee_deductions_on_salary(salary, ahv_on=(ahv_subject == "Ja"))

    profit_after_salary = profit - salary - employer_cost
    if profit_after_salary < 0:
        st.warning(
            "Der Bruttolohn inkl. **Arbeitgeberabgaben** übersteigt den Gewinn – "
            "der steuerbare Firmengewinn wird auf 0 gesetzt. Für realistische Vergleiche Lohn reduzieren."
        )
    profit_after_salary = max(0.0, profit_after_salary)

    corp_tax = profit_after_salary * total_corp

    # Steuerbar (fix): Brutto − AN-Abzüge + weitere Einkünfte − private Abzüge
    taxable_A = clamp_nonneg(salary - ee_total + other_inc - private_deductions)

    fed_tax   = federal_income_tax(taxable_A)
    cant_tax  = cantonal_income_tax(taxable_A, canton, commune)
    cant_tax *= (1.0 + church_rate)
    income_tax = fed_tax + cant_tax

    net = salary - ee_total - income_tax
    return {
        "salary": salary,
        "employer_cost": employer_cost,
        "ee_total": ee_total,
        "corp_tax": corp_tax,
        "income_tax": income_tax,
        "net": net,
        "components": dict(ahv_emp=ahv_emp, alv_emp=alv_emp, bvg_emp=bvg_emp, extra=extra,
                           ahv_ee=ahv_ee, alv_ee=alv_ee, bvg_ee=bvg_ee,
                           fed_tax=fed_tax, cant_tax=cant_tax)
    }

# ------------------------- Szenario B: Reine Dividende --------------------------
def scenario_dividend_only():
    # Erst ohne Umqualifizierung
    qualifies = qualifies_partial_taxation(share_pct)
    incl_fed = get_div_incl_fed(qualifies)
    incl_cat = get_div_incl_canton(canton, qualifies)

    # Ausgangspunkt: ohne AG-Kosten
    after_corp_base = profit * (1.0 - total_corp)
    dividend_guess = min(after_corp_base, desired_income) if desired_income else after_corp_base

    # Iteration: AG-AHV auf umqualifizierten Div-Teil reduziert ausschüttbaren Betrag,
    # aber mit Körperschaftsteuer-Schild: Nachsteuer-Abgang = C * (1 - total_corp)
    reclass_base = 0.0
    for _ in range(30):
        want_reclass = ahv_risk and dividend_guess > 0 and min_salary > 0
        shortfall = max(0.0, min_salary)  # Ziel-Lohn
        # In reiner Div-Variante ist "Lohn" = 0
        if want_reclass and 0.0 < min_salary:
            reclass_base = min(min_salary - 0.0, dividend_guess)
        else:
            reclass_base = 0.0

        ag_reclass_cost = AHV_employer * reclass_base  # nur AHV/IV/EO; FAK/UVG typischerweise nicht
        after_corp_new = (profit - ag_reclass_cost) * (1.0 - total_corp)
        dividend_new = min(after_corp_new, desired_income) if desired_income else after_corp_new
        if abs(dividend_new - dividend_guess) < 0.5:
            dividend_guess = dividend_new
            break
        dividend_guess = dividend_new

    dividend = dividend_guess
    ee_reclass = AHV_employee * reclass_base if reclass_base > 0 else 0.0

    # Steuerbar (B): Dividende teilbesteuert + reklassifizierter Teil als Lohn (100%),
    # wobei AN-AHV auf reklassifiziertem Teil abzugsfähig ist.
    taxable_fed  = clamp_nonneg(dividend * incl_fed + max(0.0, reclass_base - ee_reclass) + other_inc - private_deductions)
    taxable_cant = clamp_nonneg(dividend * incl_cat + max(0.0, reclass_base - ee_reclass) + other_inc - private_deductions)

    fed_tax  = federal_income_tax(taxable_fed)
    cant_tax = cantonal_income_tax(taxable_cant, canton, commune)
    cant_tax *= (1.0 + church_rate)
    income_tax = fed_tax + cant_tax

    # Netto: Dividendenauszahlung minus Steuer minus AN-AHV auf reklassifizierten Teil
    net = dividend - income_tax - ee_reclass

    # AG-AHV ist bereits in der Iteration durch reduzierten Nachsteuergewinn berücksichtigt (via Tax Shield).
    corp_tax = (profit - AHV_employer * reclass_base) * total_corp  # für Anzeige (annähernd)

    return {
        "dividend": dividend,
        "reclass_base": reclass_base,
        "ee_reclass": ee_reclass,
        "corp_tax": corp_tax,
        "income_tax": income_tax,
        "net": net,
        "components": dict(incl_fed=incl_fed, incl_cat=incl_cat, fed_tax=fed_tax, cant_tax=cant_tax)
    }

# ------------------------- Optimizer: Lohn + Dividende Mischung -----------------
def compute_net_for_salary_mix(salary_choice):
    """Allgemeine Mischung:
       - zahlt Lohn = salary_choice (mit AG/AN Beiträgen & Overheads)
       - schüttet Rest als Dividende aus (inkl. AHV-Umqualifizierung, falls salary_choice < min_salary)
    """
    salary_choice = clamp_nonneg(min(salary_choice, profit))
    # AG-Kosten & AN-Abzüge für den Lohnteil
    ahv_emp, alv_emp, bvg_emp, extra = employer_costs_on_salary(salary_choice, ahv_on=(ahv_subject == "Ja"))
    employer_cost = ahv_emp + alv_emp + bvg_emp + extra
    ahv_ee, alv_ee, bvg_ee, ee_total = employee_deductions_on_salary(salary_choice, ahv_on=(ahv_subject == "Ja"))

    # Gewinn nach Lohn
    profit_after_salary = clamp_nonneg(profit - salary_choice - employer_cost)

    # Körperschaftsteuer auf Restgewinn (vor evtl. AG-AHV auf Umqualifizierung)
    corp_tax_pre = profit_after_salary * total_corp
    after_corp_pre = profit_after_salary - corp_tax_pre

    # Obergrenze für Dividende gemäss gewünschter Auszahlung
    desired_left = None
    if desired_income is not None:
        desired_left = clamp_nonneg(desired_income - salary_choice)

    qualifies = qualifies_partial_taxation(share_pct)
    incl_fed = get_div_incl_fed(qualifies)
    incl_cat = get_div_incl_canton(canton, qualifies)

    # Iteration für AHV-Umqualifizierung auf Dividenden-Teil
    dividend_guess = min(after_corp_pre, desired_left) if desired_left is not None else after_corp_pre
    reclass_base = 0.0
    for _ in range(30):
        want_reclass = ahv_risk and (salary_choice < min_salary) and (dividend_guess > 0)
        if want_reclass:
            shortfall = clamp_nonneg(min_salary - salary_choice)
            reclass_base = min(shortfall, dividend_guess)
        else:
            reclass_base = 0.0

        ag_reclass_cost = AHV_employer * reclass_base
        # Tax Shield auf AG-AHV: Nachsteuer-Abgang = C * (1 - total_corp)
        after_corp_new = after_corp_pre - ag_reclass_cost * (1.0 - total_corp)
        dividend_new = min(after_corp_new, desired_left) if desired_left is not None else after_corp_new
        if abs(dividend_new - dividend_guess) < 0.5:
            dividend_guess = dividend_new
            break
        dividend_guess = dividend_new

    dividend = clamp_nonneg(dividend_guess)
    ee_reclass = AHV_employee * reclass_base if reclass_base > 0 else 0.0

    # Steuerbar: (Lohnteil netto-steuerbar) + (reklassifizierter Div.-Teil als Lohn) + (Div.-Teil teilbesteuert) + weitere Einkünfte − private Abzüge
    taxable_salary_part = clamp_nonneg(salary_choice - ee_total)
    taxable_reclass     = clamp_nonneg(reclass_base - ee_reclass)

    taxable_fed  = clamp_nonneg(taxable_salary_part + taxable_reclass + dividend * incl_fed + other_inc - private_deductions)
    taxable_cant = clamp_nonneg(taxable_salary_part + taxable_reclass + dividend * incl_cat + other_inc - private_deductions)

    fed_tax  = federal_income_tax(taxable_fed)
    cant_tax = cantonal_income_tax(taxable_cant, canton, commune) * (1.0 + church_rate)
    income_tax = fed_tax + cant_tax

    net = (salary_choice - ee_total) + (dividend - ee_reclass) - income_tax

    # Für Anzeige: Körperschaftssteuer nach AG-AHV-Umqualifizierung
    corp_tax = corp_tax_pre + 0.0  # näherungsweise; präziser wäre: (profit_after_salary - ag_reclass_cost) * total_corp
    corp_tax = (profit_after_salary - AHV_employer * reclass_base) * total_corp

    return {
        "salary": salary_choice,
        "dividend": dividend,
        "reclass_base": reclass_base,
        "ee_salary": ee_total,
        "ee_reclass": ee_reclass,
        "employer_cost": employer_cost,
        "corp_tax": corp_tax,
        "income_tax": income_tax,
        "net": net,
        "components": dict(incl_fed=incl_fed, incl_cat=incl_cat, fed_tax=fed_tax, cant_tax=cant_tax)
    }

def optimizer_best_mix():
    step = 1000.0
    max_salary = profit if desired_income is None else min(profit, desired_income)
    best = None
    s = 0.0
    while s <= max_salary + 1e-6:
        res = compute_net_for_salary_mix(s)
        if (best is None) or (res["net"] > best["net"]):
            best = res
        s += step
    return best

# ------------------------- Main Rendering ---------------------------------------
if profit > 0:
    # Szenario A
    A = scenario_salary_only()

    # Szenario B
    B = scenario_dividend_only()

    # Anzeige Szenario A
    st.subheader("💼 Szenario A – Lohn (100 %)")
    st.write(f"Bruttolohn: **CHF {A['salary']:,.0f}**")
    if ahv_subject == "Ja":
        st.write(f"Arbeitgeber AHV/ALV/BVG: CHF {(A['components']['ahv_emp']+A['components']['alv_emp']+A['components']['bvg_emp']):,.0f}")
        st.write(f"Arbeitgeber-Overheads FAK/UVG/KTG: CHF {A['components']['extra']:,.0f}")
        st.write(f"Arbeitnehmer AHV/ALV/BVG (steuerlich abzugsfähig): CHF {A['ee_total']:,.0f}")
    else:
        st.write("Keine Sozialabgaben.")
    st.write(f"Körperschaftssteuer Restgewinn: CHF {A['corp_tax']:,.0f}")
    st.write(f"Einkommenssteuer (Bund+Kanton/Gemeinde{(' + Kirche' if church_rate>0 else '')}): CHF {A['income_tax']:,.0f}")
    st.success(f"**Netto an Inhaber:** CHF {A['net']:,.0f}")

    # Anzeige Szenario B
    st.subheader("📈 Szenario B – Dividende (100 %)")
    st.write(f"Dividende: **CHF {B['dividend']:,.0f}**")
    st.write(f"Körperschaftssteuer (nach evtl. AG-AHV auf Umqualifizierung): CHF {B['corp_tax']:,.0f}")
    qualifies = qualifies_partial_taxation(share_pct)
    incl_fed = get_div_incl_fed(qualifies)
    incl_cat = get_div_incl_canton(canton, qualifies)
    teil_txt = f"Bund {int(incl_fed*100)} % / Kanton {int(incl_cat*100)} % (Beteiligung {share_pct:.0f} %)"
    st.write(f"Private Steuer (Teilbesteuerung): {teil_txt} ⇒ CHF {B['income_tax']:,.0f}")
    if B["reclass_base"] > 0:
        st.write(
            f"AHV-Umqualifizierung (Basis: CHF {B['reclass_base']:,.0f}) – "
            f"AN-AHV: CHF {B['ee_reclass']:,.0f} (AG-AHV via Gewinn & Steuerschild bereits berücksichtigt)"
        )
    st.success(f"**Netto an Inhaber:** CHF {B['net']:,.0f}")

    # Vergleich
    st.markdown("---")
    st.subheader("🔹 Vergleich (100 % Lohn vs. 100 % Dividende)")
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("Netto Lohn", f"CHF {A['net']:,.0f}")
    with c2: st.metric("Netto Dividende", f"CHF {B['net']:,.0f}")
    diff = B["net"] - A["net"]
    better = "Dividende" if diff > 0 else ("Lohn" if diff < 0 else "–")
    with c3: st.metric("Vorteil", better, f"CHF {abs(diff):,.0f}")

    # Optimizer
    if optimizer_on:
        st.markdown("---")
        st.subheader("🧠 Optimierer – beste Mischung (Lohn + Dividende)")
        best = optimizer_best_mix()
        st.write(
            f"**Optimaler Lohn:** CHF {best['salary']:,.0f} | "
            f"**Dividende:** CHF {best['dividend']:,.0f} "
            f"{'(inkl. AHV-Umqualifizierung von CHF ' + f'{best['reclass_base']:,.0f}' + ')' if best['reclass_base']>0 else ''}"
        )
        st.write(f"Einkommenssteuer gesamt: CHF {best['income_tax']:,.0f}")
        st.success(f"**Max. Netto an Inhaber:** CHF {best['net']:,.0f}")

    # Debug
    if debug_mode:
        st.markdown("---")
        st.subheader("🔍 Debug-Informationen")
        st.write(
            f"**Körperschaftssteuer gesamt:** {total_corp:.2%} "
            f"(Bund {fed_corp:.2%}, Kanton+Gemeinde {local_corp:.2%})"
        )
        # A
        st.write(
            f"**Szenario A – steuerbar:** "
            f"CHF {clamp_nonneg(A['salary'] - A['ee_total'] + other_inc - private_deductions):,.0f}  "
            f"(Brutto {A['salary']:,.0f} − AN-Beiträge {A['ee_total']:,.0f} + weitere {other_inc:,.0f} − Abzüge {private_deductions:,.0f})"
        )
        st.write(
            f"**A – Steueraufteilung:** Bund CHF {A['components']['fed_tax']:,.0f} | "
            f"Kanton/Gemeinde{(' + Kirche' if church_rate>0 else '')}: CHF {A['components']['cant_tax']:,.0f}"
        )
        # B
        st.write(
            f"**Szenario B – steuerbar (Bund):** "
            f"CHF {clamp_nonneg(B['dividend']*incl_fed + max(0.0, B['reclass_base'] - B['ee_reclass']) + other_inc - private_deductions):,.0f}"
        )
        st.write(
            f"**Szenario B – steuerbar (Kanton):** "
            f"CHF {clamp_nonneg(B['dividend']*incl_cat + max(0.0, B['reclass_base'] - B['ee_reclass']) + other_inc - private_deductions):,.0f}"
        )
        st.write(
            f"**B – Steueraufteilung:** Bund CHF {B['components']['fed_tax']:,.0f} | "
            f"Kanton/Gemeinde{(' + Kirche' if church_rate>0 else '')}: CHF {B['components']['cant_tax']:,.0f}"
        )
        st.caption(
            "Hinweise: Verrechnungssteuer (35 %) ist ein Liquiditätsthema, wird i. d. R. via Veranlagung zurückgefordert. "
            "AHV-Umqualifizierung vereinfacht (ALV/BVG typ. nicht auf Dividenden)."
        )

else:
    st.warning("Bitte Gewinn > 0 eingeben, um die Berechnung zu starten.")
