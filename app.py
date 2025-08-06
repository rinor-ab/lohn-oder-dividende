# app.py – Schweizer Lohn vs. Dividende Rechner (Beste Version)
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

def nan_to_zero(x):
    return 0.0 if (x is None or (isinstance(x, float) and math.isnan(x))) else x

# ------------------------- Daten laden ------------------------------------------
steuerfuesse          = load_json("steuer", [])
income_tax_cantons    = load_json("cant_income", {})
income_tax_conf       = sorted(
    load_json("fed_income", []),
    key=lambda d: d.get("Taxable income for federal tax", 0) or 0
)
corporate_tax         = load_json("corp_tax", {})
social_sec            = load_json("social", {})
dividend_inclusion    = load_json("div_inclusion", {})

# ------------------------- Sozialversicherungen ---------------------------------
AHV_employer   = social_sec.get("AHV_IV_EO_EmployerShare", 0.053)
AHV_employee   = social_sec.get("AHV_IV_EO_EmployeeShare", 0.053)

ALV_employer   = social_sec.get("ALV_EmployerShare", 0.011)
ALV_employee   = social_sec.get("ALV_EmployeeShare", 0.011)
ALV_ceiling    = social_sec.get("ALV_Ceiling", 148200.0)
# Ab 2025 kein ALV-Solidaritätsbeitrag mehr
ALV_solidarity = 0.0  

BVG_rates = {
    "25-34": social_sec.get("BVG_Rate_25_34", 0.07),
    "35-44": social_sec.get("BVG_Rate_35_44", 0.10),
    "45-54": social_sec.get("BVG_Rate_45_54", 0.15),
    "55-65": social_sec.get("BVG_Rate_55_65", 0.18)
}
BVG_entry_threshold = social_sec.get("BVG_EntryThreshold", 22680.0)
BVG_coord_deduction = social_sec.get("BVG_CoordDeduction", 26460.0)
BVG_max_insured     = social_sec.get("BVG_MaxInsuredSalary", 90720.0)

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
st.caption("Berechnet Nettobezüge für Schweizer Unternehmer – inkl. AHV/ALV/BVG und direkter Steuern.")

col1, col2 = st.columns(2)
with col1:
    profit         = st.number_input("Firmengewinn **vor Lohn** [CHF]", 0.0, step=10_000.0)
    desired_income = st.number_input("Gewünschte Auszahlung an Inhaber [CHF] (optional)", 0.0, step=10_000.0)
    ahv_subject    = st.radio("AHV/ALV-Beiträge?", ["Ja", "Nein"])
    age_band = st.selectbox("Altersband (BVG)", 
                           ["25-34 (7%)", "35-44 (10%)", "45-54 (15%)", "55-65 (18%)"], 
                           index=1)
with col2:
    canton   = st.selectbox("Kanton", sorted(canton_to_communes.keys()))
    commune  = st.selectbox("Gemeinde", canton_to_communes.get(canton, ["Default"]))
    other_inc= st.number_input("Weitere steuerbare Einkünfte [CHF]", 0.0, step=10_000.0)
    debug_mode = st.checkbox("Debug-Informationen anzeigen", value=False)
    st.session_state.debug_mode = debug_mode

if desired_income == 0:
    desired_income = None
elif desired_income > profit:
    desired_income = profit

# ------------------------- Bundessteuer korrekt ---------------------------------
def federal_income_tax(taxable):
    if taxable <= 0: return 0.0
    if not income_tax_conf:
        return taxable * 0.115

    # Finde die passende Stufe
    prev_threshold = 0
    for bracket in income_tax_conf:
        threshold = bracket["Taxable income for federal tax"]
        base = bracket["Base amount CHF"]
        rate = bracket["Additional %"]/100
        if taxable <= threshold:
            return base + (taxable - prev_threshold) * rate
        prev_threshold = threshold
    # Über oberster Stufe
    top = income_tax_conf[-1]
    return top["Base amount CHF"] + (taxable - prev_threshold) * (top["Additional %"]/100)

# ------------------------- Kantonssteuer ----------------------------------------
def cantonal_income_tax(taxable, kanton, gemeinde):
    if taxable <= 0: return 0.0
    brackets = income_tax_cantons.get(kanton, [])
    cantonal_base_tax = 0.0
    remaining = taxable
    for bracket in brackets:
        chunk_size = bracket.get("For the next CHF", 0)
        rate = bracket.get("Additional %", 0)/100
        if chunk_size == 0:
            cantonal_base_tax += remaining*rate
            remaining = 0
            break
        chunk = min(remaining, chunk_size)
        cantonal_base_tax += chunk*rate
        remaining -= chunk
        if remaining <= 0: break
    if remaining>0 and brackets:
        cantonal_base_tax += remaining*(brackets[-1].get("Additional %",0)/100)
    # Multiplier anwenden
    kant_mult, comm_mult = 1.0, 0.0
    for row in steuerfuesse:
        if row.get("Kanton")==kanton and row.get("Gemeinde")==gemeinde:
            kant_mult = nan_to_zero(row.get("Einkommen_Kanton",1.0))
            comm_mult = nan_to_zero(row.get("Einkommen_Gemeinde",0.0))
            break
    return cantonal_base_tax*kant_mult + cantonal_base_tax*comm_mult

# ------------------------- Dividendenteilbesteuerung ----------------------------
def get_dividend_inclusion_rate(kanton):
    return dividend_inclusion.get(kanton,0.70)

# ------------------------- Berechnung -------------------------------------------
if profit>0:
    # Körperschaftssteuer
    fed_corp = corporate_tax.get("Confederation", 0.085)
    cant_corp_data = corporate_tax.get(canton,0.0)
    if isinstance(cant_corp_data,dict):
        cant_corp_base = cant_corp_data.get("rate", cant_corp_data.get("cantonal",0.0))
    else:
        cant_corp_base = nan_to_zero(cant_corp_data)
    canton_mult, comm_mult = 1.0,0.0
    for row in steuerfuesse:
        if row.get("Kanton")==canton and row.get("Gemeinde")==commune:
            canton_mult = nan_to_zero(row.get("Gewinn_Kanton",1.0))
            comm_mult   = nan_to_zero(row.get("Gewinn_Gemeinde",0.0))
            break
    local_corp = cant_corp_base*(canton_mult+comm_mult)
    total_corp = fed_corp+local_corp

    # BVG
    age_key = age_band.split()[0]
    selected_bvg_rate = BVG_rates.get(age_key,BVG_rates["35-44"])
    bvg_employee_rate = selected_bvg_rate/2
    bvg_employer_rate = selected_bvg_rate/2

    # Szenario A: Lohn
    salary = min(desired_income if desired_income is not None else profit, profit)
    if ahv_subject=="Ja":
        # Arbeitgeber
        ahv_emp = AHV_employer*salary
        alv_emp = ALV_employer*min(salary,ALV_ceiling)
        bvg_emp = 0.0
        if salary>=BVG_entry_threshold:
            insured = max(0,min(salary,BVG_max_insured)-BVG_coord_deduction)
            bvg_emp = bvg_employer_rate*insured
        employer_cost = ahv_emp+alv_emp+bvg_emp
        # Arbeitnehmer
        ahv_ee = AHV_employee*salary
        alv_ee = ALV_employee*min(salary,ALV_ceiling)
        bvg_ee = 0.0
        if salary>=BVG_entry_threshold:
            insured = max(0,min(salary,BVG_max_insured)-BVG_coord_deduction)
            bvg_ee = bvg_employee_rate*insured
        employee_deductions = ahv_ee+alv_ee+bvg_ee
    else:
        employer_cost = employee_deductions = 0.0

    profit_after_salary = max(0.0,profit-salary-employer_cost)
    corp_tax_A  = profit_after_salary*total_corp
    taxable_A   = salary+other_inc
    income_tax_A= federal_income_tax(taxable_A)+cantonal_income_tax(taxable_A,canton,commune)
    net_A       = salary-employee_deductions-income_tax_A

    # Szenario B: Dividende
    corp_tax_B  = profit*total_corp
    after_corp  = max(0.0, profit-corp_tax_B)
    dividend    = min(after_corp, desired_income) if desired_income else after_corp
    # Bundessteuer 70%, Kanton JSON
    income_tax_B = (
        federal_income_tax(dividend*0.70+other_inc)
        + cantonal_income_tax(dividend*get_dividend_inclusion_rate(canton)+other_inc,canton,commune)
    )
    net_B       = dividend-income_tax_B

    # ------------------------- Anzeige ------------------------------------------
    st.subheader("💼 Szenario A – Lohn")
    st.write(f"Bruttolohn: **CHF {salary:,.0f}**")
    if ahv_subject=="Ja":
        st.write(f"Arbeitgeber AHV/ALV/BVG: CHF {employer_cost:,.0f}")
        st.write(f"Arbeitnehmer AHV/ALV/BVG: CHF {employee_deductions:,.0f}")
    else:
        st.write("Keine Sozialabgaben.")
    st.write(f"Körperschaftssteuer Restgewinn: CHF {corp_tax_A:,.0f}")
    st.write(f"Einkommenssteuer: CHF {income_tax_A:,.0f}")
    st.success(f"**Netto an Inhaber:** CHF {net_A:,.0f}")

    st.subheader("📈 Szenario B – Dividende")
    st.write(f"Dividende: **CHF {dividend:,.0f}**")
    st.write(f"Körperschaftssteuer (auf Gewinn): CHF {corp_tax_B:,.0f}")
    st.write(f"Private Steuer (Bund 70% / Kanton {int(get_dividend_inclusion_rate(canton)*100)}%): CHF {income_tax_B:,.0f}")
    st.success(f"**Netto an Inhaber:** CHF {net_B:,.0f}")

    st.markdown("---")
    st.subheader("🔹 Vergleich")
    col1,col2,col3=st.columns(3)
    with col1: st.metric("Netto Lohn", f"CHF {net_A:,.0f}")
    with col2: st.metric("Netto Dividende", f"CHF {net_B:,.0f}")
    with col3:
        diff=net_B-net_A
        better="Dividende" if diff>0 else "Lohn"
        st.metric("Vorteil", better, f"CHF {abs(diff):,.0f}")

    if net_A>net_B:
        st.info(f"💡 **Lohn** ist besser um **CHF {net_A-net_B:,.0f}**.")
    elif net_B>net_A:
        st.info(f"💡 **Dividende** ist besser um **CHF {net_B-net_A:,.0f}**.")
    else:
        st.info("✅ Beide Varianten ergeben denselben Nettobetrag.")

    if debug_mode:
        st.subheader("🔍 Debug-Informationen")
        st.write(f"**Körperschaftssteuer gesamt:** {total_corp:.2%} (Bund {fed_corp:.2%}, Kanton+Gemeinde {local_corp:.2%})")
        st.write(f"**Dividenden-Teilbesteuerung:** Bund 70%, Kanton {get_dividend_inclusion_rate(canton):.0%}")
        st.write(f"**BVG:** Satz {selected_bvg_rate:.0%}, Eintritt {BVG_entry_threshold:,.0f} CHF, Koord. {BVG_coord_deduction:,.0f} CHF")
else:
    st.warning("Bitte Gewinn > 0 eingeben, um die Berechnung zu starten.")
