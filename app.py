# app.py – Lohn vs. Dividende (vereinfacht, ohne KER & Vermögenssteuer, mit Ø Kirchensteuer)
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

# ---- Konstanten / Default-Annahmen (neu) ----
CHURCH_AVG_RATE = 0.12  # Ø Kirchensteuer-Zuschlag auf kant./gemeindl. Steuer (12%)
RULE_MODE_STRIKT = "Strikt (Dividende nur bei Lohn ≥ Mindestlohn)"
AHV_ON_DEFAULT = True   # AHV/ALV/BVG standardmäßig anwenden

# ------------------------- Helpers -------------------------
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

def nz(x):
    return 0.0 if (x is None or is_nan(x)) else x

def clamp(x):
    return max(0.0, float(x or 0.0))

# ------------------------- Load data -----------------------
steuerfuesse          = load_json("steuer", [])
income_tax_cantons    = load_json("cant_income", {})
income_tax_conf_raw   = load_json("fed_income", [])
corporate_tax         = load_json("corp_tax", {})
social_sec            = load_json("social", {})
dividend_inclusion    = load_json("div_inclusion", {})

def normalize_fed_brackets(raw):
    cleaned, seen = [], set()
    for d in raw:
        thr  = d.get("Taxable income for federal tax", 0)
        base = d.get("Base amount CHF", 0)
        rate = d.get("Additional %", 0)
        if thr is None or base is None or rate is None: continue
        if any(is_nan(v) for v in (thr, base, rate)): continue
        try:
            thr  = float(thr); base = float(base); rate = float(rate)/100.0
        except: continue
        if thr < 0 or thr in seen: continue
        seen.add(thr); cleaned.append({"thr":thr,"base":base,"rate":rate})
    cleaned.sort(key=lambda x: x["thr"])
    if not cleaned or cleaned[0]["thr"]>0:
        cleaned.insert(0, {"thr":0.0,"base":0.0,"rate":0.0})
    return cleaned

income_tax_conf = normalize_fed_brackets(income_tax_conf_raw)

# ------------------------- Social security ------------------
AHV_employer   = social_sec.get("AHV_IV_EO_EmployerShare", 0.053)
AHV_employee   = social_sec.get("AHV_IV_EO_EmployeeShare", 0.053)
ALV_employer   = social_sec.get("ALV_EmployerShare", 0.011)
ALV_employee   = social_sec.get("ALV_EmployeeShare", 0.011)
ALV_ceiling    = social_sec.get("ALV_Ceiling", 148200.0)
ALV_solidarity = 0.0  # abgeschafft

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

def age_to_band(age: int) -> str:
    """Mappt Alter auf BVG-Altersband für Sparbeiträge."""
    a = int(age or 35)
    if a < 35:    return "25-34"
    if a < 45:    return "35-44"
    if a < 55:    return "45-54"
    return "55-65"

# ------------------------- Canton/commune map ---------------
canton_to_communes = {}
for row in steuerfuesse:
    k = row.get("Kanton"); g = row.get("Gemeinde")
    if not k or not g or k=="Kanton": continue
    canton_to_communes.setdefault(k, []).append(g)
for k in list(canton_to_communes.keys()):
    canton_to_communes[k].sort()

if not canton_to_communes:
    st.error("Keine Steuerdaten gefunden. Fallback aktiviert.")
    canton_to_communes = {"Zürich": ["Zürich"], "Bern": ["Bern"]}

# ------------------------- UI -------------------------------
st.title("Lohn vs. Dividende")
st.caption("Regeln nach Praxis & Steuerlogik inkl. BVG und Teilbesteuerung. (Kirchensteuer: Ø-Annahme)")

col1, col2 = st.columns(2)
with col1:
    profit         = st.number_input("Firmengewinn **vor Lohn** [CHF]", 0.0, step=10_000.0)
    desired_income = st.number_input("Gewünschte **Gesamtauszahlung** an Inhaber [CHF] (optional)", 0.0, step=10_000.0)
    age_input      = st.number_input("Alter (für BVG-Altersband)", min_value=18, max_value=70, value=40, step=1)
with col2:
    canton   = st.selectbox("Kanton", sorted(canton_to_communes.keys()))
    commune  = st.selectbox("Gemeinde", canton_to_communes.get(canton, ["Default"]))
    other_inc= st.number_input("Weitere steuerbare Einkünfte [CHF]", 0.0, step=10_000.0)
    debug_mode = st.checkbox("Debug-Informationen anzeigen", value=False)

st.markdown("### Annahmen")
col3, col4 = st.columns(2)
with col3:
    # Regelmodus ausgeblendet -> fest auf Strikt
    min_salary  = st.number_input("Marktüblicher Mindestlohn [CHF]", 0.0, step=10_000.0, value=120_000.0)
    share_pct   = st.number_input("Beteiligungsquote [%] (Teilbesteuerung ab 10 %)", 0.0, 100.0, 100.0, step=5.0)
with col4:
    fak_rate    = st.number_input("FAK (nur Arbeitgeber) [%]", 0.0, 5.0, 1.5, step=0.1)/100.0
    uvg_rate    = st.number_input("UVG/KTG (Arbeitgeber) [%]", 0.0, 5.0, 1.0, step=0.1)/100.0
    pk_buyin    = st.number_input("PK-Einkauf (privat) [CHF]", 0.0, step=1.0)  # freie Eingabe (keine Sprünge)

optimizer_on = st.checkbox("Optimierer – beste Mischung (Lohn + Dividende) finden", value=True)

# desired payout
if desired_income == 0:
    desired_income = None
elif desired_income > profit:
    desired_income = profit

# ------------------------- Tax engines ----------------------
# BUGFIX (>200k Einkommen): Richtige Bundessteuer-Berechnung:
# Tax = base_at_thr + (t - thr) * rate_at_thr  (statt vorher fehlerhaftes Intervall)
def federal_income_tax(taxable):
    t = clamp(taxable)
    bracket = income_tax_conf[0]
    for b in income_tax_conf:
        if t >= b["thr"]:
            bracket = b
        else:
            break
    return bracket["base"] + (t - bracket["thr"]) * bracket["rate"]

def cantonal_income_tax(taxable, kanton, gemeinde):
    t = clamp(taxable)
    brackets = income_tax_cantons.get(kanton, [])
    base = 0.0; rem = t
    for br in brackets:
        chunk = (br.get("For the next CHF", 0) or 0)
        rate  = (br.get("Additional %", 0) or 0)/100.0
        if chunk == 0:
            base += rem*rate; rem = 0; break
        use = min(rem, chunk)
        base += use*rate; rem -= use
        if rem <= 0: break
    if rem > 0 and brackets:
        base += rem * (brackets[-1].get("Additional %", 0)/100.0)
    km, gm = 1.0, 0.0
    for row in steuerfuesse:
        if row.get("Kanton")==kanton and row.get("Gemeinde")==gemeinde:
            km = nz(row.get("Einkommen_Kanton", 1.0))
            gm = nz(row.get("Einkommen_Gemeinde", 0.0))
            break
    return base * (km + gm)

def corp_tax_components(kanton, gemeinde):
    fed = corporate_tax.get("Confederation", 0.085)
    cd  = corporate_tax.get(kanton, 0.0)
    if isinstance(cd, dict):
        cant_base = nz(cd.get("rate", cd.get("cantonal", 0.0)))
    else:
        cant_base = nz(cd)
    km, gm = 1.0, 0.0
    for row in steuerfuesse:
        if row.get("Kanton")==kanton and row.get("Gemeinde")==gemeinde:
            km = nz(row.get("Gewinn_Kanton", 1.0))
            gm = nz(row.get("Gewinn_Gemeinde", 0.0))
            break
    local = cant_base * (km + gm)
    return fed, local, fed + local

fed_corp, local_corp, total_corp = corp_tax_components(canton, commune)

def qualifies_partial(share):
    return (share or 0.0) >= 10.0

def incl_rates(qualifies, kanton):
    inc_fed  = 0.70 if qualifies else 1.00
    inc_cant = dividend_inclusion.get(kanton, 0.70) if qualifies else 1.00
    return inc_fed, inc_cant

# Employer/employee cost blocks
def employer_costs(salary, age_key, ahv=True, fak=fak_rate, uvg=uvg_rate):
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

# ------------------------- Szenarien -----------------------
def scenario_salary_only(profit, desired, kanton, gemeinde, age_key, ahv_on, other, pk_buy):
    salary = profit if desired is None else min(profit, desired)
    ag = employer_costs(salary, age_key, ahv_on)
    an = employee_deductions(salary, age_key, ahv_on)

    profit_after_salary = profit - salary - ag["total"]
    if profit_after_salary < 0:
        st.warning("Bruttolohn inkl. Arbeitgeberabgaben > Gewinn – Restgewinn wird auf 0 gesetzt.")
    profit_after_salary = max(0.0, profit_after_salary)

    corp_tax_amt = profit_after_salary * total_corp

    taxable = clamp(salary - an["total"] + other - pk_buy)
    fed  = federal_income_tax(taxable)
    cant = cantonal_income_tax(taxable, kanton, gemeinde)*(1.0 + CHURCH_AVG_RATE)
    income_tax = fed + cant

    net_owner = salary - an["total"] - income_tax

    return {
        "salary": salary, "dividend": 0.0,
        "corp_tax": corp_tax_amt, "income_tax": income_tax,
        "net": net_owner, "adjusted_net": net_owner,
        "retained_after_tax": max(0.0, profit_after_salary - corp_tax_amt),
        "blocks": dict(ag=ag, an=an, fed=fed, cant=cant)
    }

def scenario_dividend(profit, desired, kanton, gemeinde, age_key, ahv_on, other, pk_buy,
                      rule_mode, min_salary, share_pct):
    qualifies = qualifies_partial(share_pct)
    inc_fed, inc_cant = incl_rates(qualifies, kanton)

    # Schritt 1: Lohn nach Regelmodus (Strikt)
    salary = min(min_salary, profit if desired is None else min(profit, desired))
    ag = employer_costs(salary, age_key, ahv_on)
    an = employee_deductions(salary, age_key, ahv_on)

    profit_after_salary = clamp(profit - salary - ag["total"])
    corp_tax_amt = profit_after_salary * total_corp
    after_corp = profit_after_salary - corp_tax_amt

    desired_left = None if desired is None else clamp(desired - salary)
    gross_div = after_corp if desired_left is None else min(after_corp, desired_left)

    if rule_mode.startswith("Strikt") and salary < min_salary:
        dividend = 0.0
        if gross_div > 0:
            st.info("Dividende nicht zulässig, da Lohn < Mindestlohn (Strikt-Modus). Ausschüttung = 0.")
    else:
        dividend = gross_div

    # Schritt 2: persönliche Steuern
    taxable_salary = clamp(salary - an["total"])
    taxable_fed  = clamp(taxable_salary + dividend * inc_fed  + other - pk_buy)
    taxable_cant = clamp(taxable_salary + dividend * inc_cant + other - pk_buy)

    fed  = federal_income_tax(taxable_fed)
    cant = cantonal_income_tax(taxable_cant, kanton, gemeinde) * (1.0 + CHURCH_AVG_RATE)
    income_tax = fed + cant

    # Schritt 3: Nettozufluss heute
    net_owner = (salary - an["total"]) + dividend - income_tax

    return {
        "salary": salary, "dividend": dividend,
        "corp_tax": corp_tax_amt, "income_tax": income_tax,
        "net": net_owner, "adjusted_net": net_owner,
        "retained_after_tax": clamp(profit_after_salary - corp_tax_amt - dividend),
        "blocks": dict(ag=ag, an=an, fed=fed, cant=cant,
                       inc_fed=inc_fed, inc_cant=inc_cant)
    }

# ------------------------- Optimizer -----------------------
def optimize_mix(step=1000.0):
    """Suche beste Mischung (Lohn/Dividende) unter Strikt-Regel."""
    age_key = age_to_band(age_input)
    qualifies = qualifies_partial(share_pct)
    inc_fed, inc_cant = incl_rates(qualifies, canton)
    salary_cap = profit if desired_income is None else min(profit, desired_income)

    best = None
    s = 0.0
    while s <= salary_cap + 1e-6:
        ag = employer_costs(s, age_key, AHV_ON_DEFAULT)
        an = employee_deductions(s, age_key, AHV_ON_DEFAULT)

        profit_after_salary = clamp(profit - s - ag["total"])
        corp_tax_pre = profit_after_salary * total_corp
        after_corp_pre = profit_after_salary - corp_tax_pre

        desired_left = None if desired_income is None else clamp(desired_income - s)
        pre_dividend = after_corp_pre if desired_left is None else min(after_corp_pre, desired_left)

        # Strikt: Dividende nur wenn s ≥ Mindestlohn
        dividend = pre_dividend if s >= min_salary else 0.0
        corp_tax_amt = corp_tax_pre

        taxable_salary = clamp(s - an["total"])
        taxable_fed  = clamp(taxable_salary + dividend*inc_fed  + other_inc - pk_buyin)
        taxable_cant = clamp(taxable_salary + dividend*inc_cant + other_inc - pk_buyin)

        fed_tax  = federal_income_tax(taxable_fed)
        cant_tax = cantonal_income_tax(taxable_cant, canton, commune) * (1.0 + CHURCH_AVG_RATE)
        income_tax = fed_tax + cant_tax

        net_owner = (s - an["total"]) + dividend - income_tax

        res = {"salary": s, "dividend": dividend, "net": net_owner,
               "adjusted_net": net_owner, "income_tax": income_tax,
               "corp_tax": corp_tax_amt,
               "retained_after_tax": clamp(profit_after_salary - corp_tax_amt - dividend)}
        if (best is None) or (net_owner > best["adjusted_net"]):
            best = res
        s += step
    return best

# ------------------------- Run & Render --------------------
if profit > 0:
    age_key = age_to_band(age_input)
    rule_mode = RULE_MODE_STRIKT
    ahv_on = AHV_ON_DEFAULT

    # Szenario A – Lohn
    A = scenario_salary_only(profit, desired_income, canton, commune, age_key, ahv_on, other_inc, pk_buyin)

    # Szenario B – Lohn + Dividende
    B = scenario_dividend(profit, desired_income, canton, commune, age_key, ahv_on, other_inc, pk_buyin,
                          rule_mode, min_salary, share_pct)

    # ----- Display A -----
    st.subheader("Szenario A – 100% Lohn")
    st.write(f"Bruttolohn: **CHF {A['salary']:,.0f}**")
    st.write(f"AG AHV/ALV/BVG: CHF {(A['blocks']['ag']['ahv']+A['blocks']['ag']['alv']+A['blocks']['ag']['bvg']):,.0f}")
    st.write(f"AG FAK/UVG/KTG: CHF {A['blocks']['ag']['extra']:,.0f}")
    st.write(f"AN AHV/ALV/BVG (abzugsfähig): CHF {A['blocks']['an']['total']:,.0f}")
    st.write(f"Körperschaftssteuer Restgewinn: CHF {A['corp_tax']:,.0f}")
    st.write(f"Einkommenssteuer (Bund + Kanton + Kirche Ø): CHF {A['income_tax']:,.0f}")
    st.write(f"Nachsteuerlicher Gewinn einbehalten: CHF {A['retained_after_tax']:,.0f}")
    st.success(f"**Netto an Inhaber (heute):** CHF {A['adjusted_net']:,.0f}")

    # ----- Display B -----
    st.subheader("Szenario B – Lohn + Dividende")
    st.write(f"Bruttolohn: **CHF {B['salary']:,.0f}** | Dividende gesamt: **CHF {B['dividend']:,.0f}**")
    st.write(f"Körperschaftssteuer (nach Lohn): CHF {B['corp_tax']:,.0f}")
    st.write(f"Einkommenssteuer (Bund + Kanton + Kirche Ø): CHF {B['income_tax']:,.0f}")
    st.write(f"Nachsteuerlicher Gewinn einbehalten: CHF {B['retained_after_tax']:,.0f}")
    st.caption(f"Teilbesteuerung Dividenden: Bund {int(B['blocks']['inc_fed']*100)}%, "
               f"Kanton {int(B['blocks']['inc_cant']*100)}% (falls Beteiligung ≥ 10%).")
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
        st.write(f"Einkommenssteuer gesamt: CHF {best['income_tax']:,.0f}")
        st.write(f"Körperschaftssteuer: CHF {best['corp_tax']:,.0f}")
        st.write(f"Nachsteuerlich einbehalten: CHF {best['retained_after_tax']:,.0f}")
        st.success(f"**Max. Netto an Inhaber (heute):** CHF {best['adjusted_net']:,.0f}")

    # ----- Debug -----
    if debug_mode:
        st.markdown("---")
        st.subheader("Debug-Informationen")
        st.write(f"Körperschaftssteuer-Satz gesamt: {(total_corp):.2%} (Bund {fed_corp:.2%}, Kanton+Gemeinde {local_corp:.2%})")
        st.write(f"Teilbesteuerung aktiv: {'Ja' if qualifies_partial(share_pct) else 'Nein'} "
                 f"| Bund {int((0.70 if qualifies_partial(share_pct) else 1.00)*100)}%, "
                 f"Kanton {int((dividend_inclusion.get(canton,0.70) if qualifies_partial(share_pct) else 1.00)*100)}%")
        st.write(f"Regelmodus: {rule_mode} | Mindestlohn: CHF {min_salary:,.0f}")
        if pk_buyin>0:  st.write(f"PK-Einkauf berücksichtigt: CHF {pk_buyin:,.0f}")

    # ----- Hinweise & Annahmen (kleiner, einklappbar) -----
    with st.expander("Hinweise & Annahmen", expanded=False):
        st.markdown(
            f"- **Kirchensteuer:** Es wird automatisch ein Ø-Zuschlag von **{int(CHURCH_AVG_RATE*100)}%** auf die kant./gemeindl. Steuer berücksichtigt.\n"
            f"- **AHV/ALV/BVG:** Standardmäßig **angewendet** (Arbeitgeber- und Arbeitnehmeranteile sind eingerechnet).\n"
            f"- **Regelmodus:** **Strikt** – Dividenden erst zulässig, wenn der Lohn ≥ Mindestlohn ist.\n"
            f"- **BVG-Altersband:** Automatische Zuordnung anhand des eingegebenen Alters (25–34 / 35–44 / 45–54 / 55–65).\n"
            f"- **PK-Einkauf:** Freie Eingabe, reduziert das steuerbare Einkommen (Sperrfristen beachten).\n"
            f"- **Nicht berücksichtigt:** Kapitalreserven (KER) und Vermögenssteuer-Impact.\n"
        )

else:
    st.warning("Bitte Gewinn > 0 eingeben, um die Berechnung zu starten.")
