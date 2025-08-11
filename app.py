# app.py – Schweizer Lohn vs. Dividende Rechner (Patched + Realitätschecks)
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

st.markdown("### Realitätschecks")
col3, col4 = st.columns(2)
with col3:
    # AHV-Umqualifizierung / Mindestlohn
    min_salary = st.number_input("Marktüblicher Mindestlohn [CHF]", 0.0, step=10_000.0, value=120_000.0)
    ahv_risk   = st.checkbox("AHV-Umqualifizierung auf Dividenden anwenden (falls Lohn < Mindestlohn)", value=True)
    # Beteiligungsquote
    share_pct = st.number_input("Beteiligungsquote [%]", min_value=0.0, max_value=100.0, value=100.0, step=5.0)
with col4:
    # AG-Overheads (vereinfachte Sätze)
    fak_rate = st.number_input("FAK (nur Arbeitgeber) [%]", 0.0, 5.0, 1.5, step=0.1) / 100.0
    uvg_ktg_rate = st.number_input("UVG/KTG (Arbeitgeber) [%]", 0.0, 5.0, 1.0, step=0.1) / 100.0

# gewünschte Auszahlung validieren
if desired_income == 0:
    desired_income = None
elif desired_income > profit:
    desired_income = profit

# ------------------------- Bundessteuer -----------------------------------------
def federal_income_tax(taxable):
    """
    Stückweise-linear:
    Steuer = base_i + (taxable - thr_{i-1}) * rate_i, sofern taxable <= thr_i.
    Über oberster Stufe: base_top + (taxable - thr_top) * rate_top.
    """
    if taxable <= 0:
        return 0.0
    prev_thr = 0.0
    for b in income_tax_conf:
        thr, base, rate = b["thr"], b["base"], b["rate"]
        if taxable <= thr:
            return base + (taxable - prev_thr) * rate
        prev_thr = thr
    top = income_tax_conf[-1]
    return top["base"] + (taxable - top["thr"]) * top["rate"]

# ------------------------- Kantonssteuer ----------------------------------------
def cantonal_income_tax(taxable, kanton, gemeinde):
    if taxable <= 0:
        return 0.0
    brackets = income_tax_cantons.get(kanton, [])
    cantonal_base_tax = 0.0
    remaining = taxable
    for bracket in brackets:
        chunk_size = bracket.get("For the next CHF", 0) or 0
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

    # Multiplikatoren (Kanton + Gemeinde)
    kant_mult, comm_mult = 1.0, 0.0
    for row in steuerfuesse:
        if row.get("Kanton") == kanton and row.get("Gemeinde") == gemeinde:
            kant_mult = nan_to_zero(row.get("Einkommen_Kanton", 1.0))
            comm_mult = nan_to_zero(row.get("Einkommen_Gemeinde", 0.0))
            break
    return cantonal_base_tax * (kant_mult + comm_mult)

# ------------------------- Teilbesteuerung / Beteiligung ------------------------
def qualifies_partial_taxation(share_pct):
    # Vereinfachte Faustregel: >= 10 % Beteiligung → Teilbesteuerung
    return (share_pct or 0.0) >= 10.0

def get_dividend_inclusion_rate_canton(kanton, qualifies):
    base = dividend_inclusion.get(kanton, 0.70)
    return base if qualifies else 1.00

def get_dividend_inclusion_rate_federal(qualifies):
    return 0.70 if qualifies else 1.00

# ------------------------- Berechnung -------------------------------------------
if profit > 0:
    # Körperschaftssteuer (Bund fix 8.5% wenn nicht in JSON)
    fed_corp = corporate_tax.get("Confederation", 0.085)

    # Kantonalbasisrate: erlaubt dict mit 'rate' oder direkter float
    cant_corp_data = corporate_tax.get(canton, 0.0)
    if isinstance(cant_corp_data, dict):
        cant_corp_base = nan_to_zero(cant_corp_data.get("rate", cant_corp_data.get("cantonal", 0.0)))
    else:
        cant_corp_base = nan_to_zero(cant_corp_data)

    # Lokale Multiplikatoren für Gewinnsteuer (Kanton + Gemeinde)
    canton_mult, comm_mult = 1.0, 0.0
    for row in steuerfuesse:
        if row.get("Kanton") == canton and row.get("Gemeinde") == commune:
            canton_mult = nan_to_zero(row.get("Gewinn_Kanton", 1.0))
            comm_mult   = nan_to_zero(row.get("Gewinn_Gemeinde", 0.0))
            break
    local_corp = cant_corp_base * (canton_mult + comm_mult)
    total_corp = fed_corp + local_corp

    # BVG Sätze (AG/AN je hälftig)
    age_key = age_band.split()[0]
    selected_bvg_rate = BVG_rates.get(age_key, BVG_rates["35-44"])
    bvg_employee_rate = selected_bvg_rate / 2
    bvg_employer_rate = selected_bvg_rate / 2

    # ----------------- Szenario A: Lohn -----------------
    salary = min(desired_income if desired_income is not None else profit, profit)

    # Sozialabgaben vorbereiten (AN/AG)
    ahv_ee = alv_ee = bvg_ee = 0.0
    ahv_emp = alv_emp = bvg_emp = 0.0
    fak_cost = uvg_cost = 0.0

    if ahv_subject == "Ja":
        # Arbeitgeber
        ahv_emp = AHV_employer * salary
        alv_emp = ALV_employer * min(salary, ALV_ceiling)
        insured_emp = 0.0
        if salary >= BVG_entry_threshold:
            insured_emp = max(0.0, min(salary, BVG_max_insured) - BVG_coord_deduction)
            bvg_emp = bvg_employer_rate * insured_emp
        # zusätzliche Overheads
        fak_cost = fak_rate * salary
        uvg_cost = uvg_ktg_rate * salary
        employer_cost = ahv_emp + alv_emp + bvg_emp + fak_cost + uvg_cost

        # Arbeitnehmer
        ahv_ee = AHV_employee * salary
        alv_ee = ALV_employee * min(salary, ALV_ceiling)
        insured_ee = 0.0
        if salary >= BVG_entry_threshold:
            insured_ee = max(0.0, min(salary, BVG_max_insured) - BVG_coord_deduction)
            bvg_ee = bvg_employee_rate * insured_ee
        employee_deductions = ahv_ee + alv_ee + bvg_ee
    else:
        employer_cost = 0.0
        employee_deductions = 0.0

    profit_after_salary = profit - salary - employer_cost
    if profit_after_salary < 0:
        st.warning(
            "Der Bruttolohn inkl. **Arbeitgeberabgaben** übersteigt den Gewinn – "
            "der steuerbare Firmengewinn wird auf 0 gesetzt. Für realistische Vergleiche Lohn reduzieren."
        )
    profit_after_salary = max(0.0, profit_after_salary)

    corp_tax_A   = profit_after_salary * total_corp

    # *** WICHTIGER FIX: Lohn steuerbar = Brutto - abzugsfähige AN-Beiträge + weitere Einkünfte ***
    taxable_A    = max(0.0, salary - employee_deductions) + other_inc
    income_tax_A = federal_income_tax(taxable_A) + cantonal_income_tax(taxable_A, canton, commune)
    net_A        = salary - employee_deductions - income_tax_A

    # ----------------- Szenario B: Dividende -----------------
    qualifies = qualifies_partial_taxation(share_pct)

    corp_tax_B  = profit * total_corp
    after_corp  = max(0.0, profit - corp_tax_B)
    dividend_base = min(after_corp, desired_income) if desired_income else after_corp

    incl_fed = get_dividend_inclusion_rate_federal(qualifies)
    incl_cat = get_dividend_inclusion_rate_canton(canton, qualifies)

    income_tax_B = (
        federal_income_tax(dividend_base * incl_fed + other_inc) +
        cantonal_income_tax(dividend_base * incl_cat + other_inc, canton, commune)
    )

    # AHV-Umqualifizierung, wenn Lohn < Mindestlohn
    ahv_reclass_base = 0.0
    ahv_emp_reclass = ahv_ee_reclass = 0.0
    if ahv_risk and salary < min_salary and dividend_base > 0:
        shortfall = min_salary - salary
        # konservativ: Umqualifizierung max. bis zur Höhe der Dividende
        ahv_reclass_base = min(shortfall, dividend_base)
        # AHV auf reklassifizierten Anteil (ALV typischerweise nicht auf Dividende)
        ahv_emp_reclass = AHV_employer * ahv_reclass_base
        ahv_ee_reclass  = AHV_employee * ahv_reclass_base
        # Vereinfachung: steuerliche Behandlung der reklassifizierten Portion bleibt wie oben
        # (konservativ in Richtung Lohn könnte man diesen Teil als Lohn veranlagen – komplexer)

    # Netto Dividende: Bruttodividende minus private Steuer minus AN-AHV der Umqualifizierung.
    # AG-AHV der Umqualifizierung reduziert wirtschaftlich die Ausschüttung – wir ziehen sie ebenfalls ab.
    net_B = dividend_base - income_tax_B - ahv_ee_reclass - ahv_emp_reclass

    # ------------------------- Anzeige ------------------------------------------
    st.subheader("💼 Szenario A – Lohn")
    st.write(f"Bruttolohn: **CHF {salary:,.0f}**")
    if ahv_subject == "Ja":
        st.write(f"Arbeitgeber AHV/ALV/BVG: CHF {(ahv_emp+alv_emp+bvg_emp):,.0f}")
        st.write(f"Arbeitgeber-Overheads FAK/UVG/KTG: CHF {(fak_cost+uvg_cost):,.0f}")
        st.write(f"Arbeitnehmer AHV/ALV/BVG (steuerlich abzugsfähig): CHF {employee_deductions:,.0f}")
    else:
        st.write("Keine Sozialabgaben.")
    st.write(f"Körperschaftssteuer Restgewinn: CHF {corp_tax_A:,.0f}")
    st.write(f"Einkommenssteuer (Bund+Kanton/Gemeinde): CHF {income_tax_A:,.0f}")
    st.success(f"**Netto an Inhaber:** CHF {net_A:,.0f}")

    st.subheader("📈 Szenario B – Dividende")
    st.write(f"Dividende: **CHF {dividend_base:,.0f}**")
    st.write(f"Körperschaftssteuer (auf Gewinn): CHF {corp_tax_B:,.0f}")
    teil_txt = f"Bund {int(incl_fed*100)} % / Kanton {int(incl_cat*100)} % (bei Beteiligung {share_pct:.0f} %)"
    st.write(f"Private Steuer (Teilbesteuerung): {teil_txt} ⇒ CHF {income_tax_B:,.0f}")
    if ahv_reclass_base > 0:
        st.write(
            f"AHV-Umqualifizierung (Basis: CHF {ahv_reclass_base:,.0f}) – "
            f"AG-Anteil: CHF {ahv_emp_reclass:,.0f}, AN-Anteil: CHF {ahv_ee_reclass:,.0f}"
        )
    st.success(f"**Netto an Inhaber:** CHF {net_B:,.0f}")

    st.markdown("---")
    st.subheader("🔹 Vergleich")
    col1c, col2c, col3c = st.columns(3)
    with col1c: st.metric("Netto Lohn", f"CHF {net_A:,.0f}")
    with col2c: st.metric("Netto Dividende", f"CHF {net_B:,.0f}")
    with col3c:
        diff = net_B - net_A
        better = "Dividende" if diff > 0 else ("Lohn" if diff < 0 else "–")
        st.metric("Vorteil", better, f"CHF {abs(diff):,.0f}")

    if net_A > net_B:
        st.info(f"💡 **Lohn** ist besser um **CHF {net_A - net_B:,.0f}**.")
    elif net_B > net_A:
        st.info(f"💡 **Dividende** ist besser um **CHF {net_B - net_A:,.0f}**.")
    else:
        st.info("✅ Beide Varianten ergeben denselben Nettobetrag.")

    if debug_mode:
        st.subheader("🔍 Debug-Informationen")
        st.write(
            f"**Körperschaftssteuer gesamt:** {total_corp:.2%} "
            f"(Bund {fed_corp:.2%}, Kanton+Gemeinde {local_corp:.2%})"
        )
        st.write(
            f"**Einkommen (Lohn) steuerbar:** CHF {taxable_A:,.0f}  "
            f"(Brutto {salary:,.0f} − AN-Beiträge {employee_deductions:,.0f} + weitere Einkünfte {other_inc:,.0f})"
        )
        st.write(
            f"**Teilbesteuerung aktiv:** {'Ja' if qualifies else 'Nein'} | "
            f"Bund {incl_fed:.0%}, Kanton {incl_cat:.0%}"
        )
        if ahv_reclass_base > 0:
            st.write(
                f"**AHV-Umqualifizierung:** Basis {ahv_reclass_base:,.0f} | "
                f"AG {ahv_emp_reclass:,.0f} | AN {ahv_ee_reclass:,.0f}"
            )
        st.write(
            f"**BVG-Parameter:** Satz gesamt {selected_bvg_rate:.0%}, "
            f"Eintritt {BVG_entry_threshold:,.0f} CHF, Koord.-Abzug {BVG_coord_deduction:,.0f} CHF, "
            f"Max vers. Lohn {BVG_max_insured:,.0f} CHF"
        )
        st.caption("Hinweise: Verrechnungssteuer (35 %) = Liquiditätsthema; "
                   "Umqualifizierung vereinfacht (Steuerlogik der reklassifizierten Portion nicht separat modelliert).")
else:
    st.warning("Bitte Gewinn > 0 eingeben, um die Berechnung zu starten.")
