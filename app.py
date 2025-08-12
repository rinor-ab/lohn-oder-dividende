# app.py – Lohn vs. Dividende vs. Thesaurierung
# Form-based UX + tax savings summary (TREX + Vontobel aligned) + subtle styling
import json, math, pathlib
import streamlit as st

# ------------------------- App Style -------------------------
def inject_style():
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] {background: #0b1020;}
        [data-testid="stHeader"] {background: rgba(0,0,0,0);}
        :root {--card-bg: #121a36; --muted:#9db0ff; --ink:#e9eeff; --accent:#7aa2ff;}
        h1,h2,h3,h4 { color: var(--ink) !important; }
        .caption, .help, p, li, label, .stRadio, .stMarkdown, .stSelectbox, .stNumberInput { color: #cfe1ff !important; }
        .pill { display:inline-block; padding:6px 10px; border-radius:999px; background:#18224a; color:#cfe1ff; border:1px solid #243064; }
        .card { background: var(--card-bg); border: 1px solid #243064; border-radius:16px; padding:16px 18px; }
        .kpi { border-radius:16px; padding:14px 16px; background:#0f1530; border:1px solid #27305c; }
        .kpi h3 { margin:0; color:#dfe7ff; font-size:16px; font-weight:600; }
        .kpi .v { font-size:20px; font-weight:700; color:#ffffff;}
        .divider { height:1px; background:linear-gradient(90deg,#1a265c,transparent); margin:14px 0 10px 0;}
        .btn-primary button { background: linear-gradient(135deg,#4361ee,#5a78ff); border:0; }
        .btn-primary button:hover { filter: brightness(1.05); }
        .accent { color: var(--accent); }
        .muted { color: var(--muted); }
        </style>
        """,
        unsafe_allow_html=True,
    )

inject_style()

# ------------------------- Data Files -------------------------
DATA_DIR = pathlib.Path(__file__).parent
files = {
    "steuer": "Steuerfuesse.json",
    "cant_income": "Income_Tax_Cantons.json",
    "fed_income": "Income_Tax_Confederation.json",
    "corp_tax": "Corporate_Income_Tax.json",
    "social": "Social_Security_Contributions.json",
    "div_inclusion": "Teilbesteuerung_Dividenden.json",
}

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

def fmt_chf(x):
    return f"CHF {x:,.0f}".replace(",", "'")

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
ALV_solidarity = 0.0  # abolished

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

# ------------------------- Tax engines ----------------------
def federal_income_tax(taxable):
    t = clamp(taxable); prev = 0.0
    for b in income_tax_conf:
        thr, base, rate = b["thr"], b["base"], b["rate"]
        if t <= thr:
            return base + (t - prev)*rate
        prev = thr
    top = income_tax_conf[-1]
    return top["base"] + (t - top["thr"]) * top["rate"]

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

def qualifies_partial(share):
    return (share or 0.0) >= 10.0

def incl_rates(qualifies, kanton):
    inc_fed  = 0.70 if qualifies else 1.00
    inc_cant = dividend_inclusion.get(kanton, 0.70) if qualifies else 1.00
    return inc_fed, inc_cant

def split_ker(dividend, ker_available):
    ker_used = min(ker_available, dividend)
    nonker   = max(0.0, dividend - ker_used)
    return ker_used, nonker

def wealth_tax_on_delta(delta, rate_pm):
    return max(0.0, delta * rate_pm)

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

# ------------------------- Scenarios -----------------------
def scenario_salary_only(profit, desired, kanton, gemeinde, age_key, ahv_on, church, other, pk_buy, wealth_pm, total_corp):
    salary = profit if desired is None else min(profit, desired)
    ag = employer_costs(salary, age_key, ahv_on, fak_rate, uvg_rate)
    an = employee_deductions(salary, age_key, ahv_on)

    profit_after_salary = profit - salary - ag["total"]
    if profit_after_salary < 0:
        st.warning("Bruttolohn inkl. Arbeitgeberabgaben > Gewinn – Restgewinn wird auf 0 gesetzt.")
    profit_after_salary = max(0.0, profit_after_salary)

    corp_tax_amt = profit_after_salary * total_corp
    retained_after_tax = profit_after_salary - corp_tax_amt

    taxable = clamp(salary - an["total"] + other - pk_buy)
    fed  = federal_income_tax(taxable)
    cant = cantonal_income_tax(taxable, kanton, gemeinde)*(1.0 + church)
    income_tax = fed + cant

    net_owner = salary - an["total"] - income_tax
    wealth_cost = wealth_tax_on_delta(retained_after_tax, wealth_pm)
    adjusted_net = net_owner - wealth_cost if apply_wealth_to_net else net_owner

    total_taxes = corp_tax_amt + income_tax
    return {
        "salary": salary, "dividend": 0.0,
        "corp_tax": corp_tax_amt, "income_tax": income_tax, "total_taxes": total_taxes,
        "net": net_owner, "retained_after_tax": retained_after_tax,
        "wealth_cost": wealth_cost, "adjusted_net": adjusted_net,
        "blocks": dict(ag=ag, an=an, fed=fed, cant=cant)
    }

def scenario_dividend(profit, desired, kanton, gemeinde, age_key, ahv_on, church, other, pk_buy, wealth_pm,
                      rule_mode, min_salary, share_pct, ker_available, total_corp):
    qualifies = qualifies_partial(share_pct)
    inc_fed, inc_cant = incl_rates(qualifies, kanton)

    # Step 1: salary logic per rule
    if rule_mode.startswith("Strikt"):
        salary = min(min_salary, profit if desired is None else min(profit, desired))
        ag = employer_costs(salary, age_key, ahv_on, fak_rate, uvg_rate)
        an = employee_deductions(salary, age_key, ahv_on)

        profit_after_salary = clamp(profit - salary - ag["total"])
        corp_tax_amt = profit_after_salary * total_corp
        after_corp = profit_after_salary - corp_tax_amt

        desired_left = None if desired is None else clamp(desired - salary)
        gross_div = after_corp if desired_left is None else min(after_corp, desired_left)
        reclass_base = 0.0
        dividend = gross_div if salary >= min_salary else 0.0
        if salary < min_salary and gross_div > 0:
            st.info("Dividende nicht zulässig, da Lohn < Mindestlohn (Strikt-Modus). Ausschüttung = 0.")
    else:
        # Risk-based: allow below min but reclass difference to salary (AHV), with corporate tax shield
        salary = min(min_salary, profit if desired is None else min(profit, desired))
        ag = employer_costs(salary, age_key, ahv_on, fak_rate, uvg_rate)
        an = employee_deductions(salary, age_key, ahv_on)

        profit_after_salary = clamp(profit - salary - ag["total"])
        corp_tax_pre = profit_after_salary * total_corp
        after_corp_pre = profit_after_salary - corp_tax_pre
        desired_left = None if desired is None else clamp(desired - salary)
        dividend_guess = after_corp_pre if desired_left is None else min(after_corp_pre, desired_left)

        reclass_base = 0.0
        if salary < min_salary and dividend_guess > 0:
            shortfall = min(min_salary - salary, dividend_guess)
            reclass_base = shortfall
            ag_reclass = AHV_employer * reclass_base
            after_corp = after_corp_pre - ag_reclass * (1.0 - total_corp)
            dividend = min(after_corp, desired_left) if desired_left is not None else after_corp
        else:
            after_corp = after_corp_pre
            dividend = dividend_guess
        corp_tax_amt = profit_after_salary * total_corp

    # Step 2: KER
    ker_used, nonker_div = split_ker(dividend, ker_available)

    # Step 3: personal tax base
    taxable_salary = clamp(salary - an["total"])
    ee_reclass = AHV_employee * reclass_base if reclass_base > 0 else 0.0
    taxable_reclass = clamp(reclass_base - ee_reclass)

    taxable_fed  = clamp(taxable_salary + taxable_reclass + nonker_div*inc_fed + other - pk_buy)
    taxable_cant = clamp(taxable_salary + taxable_reclass + nonker_div*inc_cant + other - pk_buy)

    fed_tax  = federal_income_tax(taxable_fed)
    cant_tax = cantonal_income_tax(taxable_cant, kanton, gemeinde) * (1.0 + church)
    income_tax = fed_tax + cant_tax

    retained_after_tax = clamp(profit_after_salary - corp_tax_amt - dividend)
    wealth_cost = wealth_tax_on_delta(retained_after_tax, wealth_pm)

    net_owner = (salary - an["total"]) + (dividend - (ee_reclass if reclass_base>0 else 0.0)) - income_tax
    adjusted_net = net_owner - wealth_cost if apply_wealth_to_net else net_owner

    total_taxes = corp_tax_amt + income_tax
    return {
        "salary": salary, "dividend": dividend,
        "corp_tax": corp_tax_amt, "income_tax": income_tax, "total_taxes": total_taxes,
        "net": net_owner, "retained_after_tax": retained_after_tax,
        "wealth_cost": wealth_cost, "adjusted_net": adjusted_net,
        "blocks": dict(ag=ag, an=an, fed=fed_tax, cant=cant_tax, reclass=reclass_base, ee_reclass=ee_reclass,
                       inc_fed=inc_fed, inc_cant=inc_cant, ker_used=ker_used, nonker_div=nonker_div)
    }

def scenario_retention(profit, wealth_pm, total_corp):
    corp_tax_amt = profit * total_corp
    retained_after_tax = profit - corp_tax_amt
    wealth_cost = wealth_tax_on_delta(retained_after_tax, wealth_pm)
    adjusted_net = -wealth_cost if apply_wealth_to_net else 0.0
    total_taxes = corp_tax_amt  # no personal tax today
    return {
        "salary": 0.0, "dividend": 0.0,
        "corp_tax": corp_tax_amt, "income_tax": 0.0, "total_taxes": total_taxes,
        "net": 0.0, "retained_after_tax": retained_after_tax,
        "wealth_cost": wealth_cost, "adjusted_net": adjusted_net
    }

# ------------------------- Optimizer -----------------------
def optimize_mix(profit, desired_income, kanton, gemeinde, age_key, ahv_on, church, other, pk_buyin,
                 wealth_pm, rule_mode, min_salary, share_pct, ker_amount, total_corp, step=1000.0):
    qualifies = qualifies_partial(share_pct)
    inc_fed, inc_cant = incl_rates(qualifies, kanton)
    salary_cap = profit if desired_income is None else min(profit, desired_income)

    best = None
    s = 0.0
    while s <= salary_cap + 1e-6:
        ag = employer_costs(s, age_key, ahv_on, fak_rate, uvg_rate)
        an = employee_deductions(s, age_key, ahv_on)
        profit_after_salary = clamp(profit - s - ag["total"])
        corp_tax_pre = profit_after_salary * total_corp
        after_corp_pre = profit_after_salary - corp_tax_pre
        desired_left = None if desired_income is None else clamp(desired_income - s)
        pre_dividend = after_corp_pre if desired_left is None else min(after_corp_pre, desired_left)

        reclass_base = 0.0
        if rule_mode.startswith("Strikt"):
            dividend = pre_dividend if s >= min_salary else 0.0
            corp_tax_amt = corp_tax_pre
        else:
            if s < min_salary and pre_dividend > 0:
                reclass_base = min(min_salary - s, pre_dividend)
                ag_reclass = AHV_employer * reclass_base
                after_corp = after_corp_pre - ag_reclass * (1.0 - total_corp)
                dividend = after_corp if desired_left is None else min(after_corp, desired_left)
            else:
                dividend = pre_dividend
                after_corp = after_corp_pre
            corp_tax_amt = profit_after_salary * total_corp

        ker_used, nonker_div = split_ker(dividend, ker_amount)
        taxable_salary = clamp(s - an["total"])
        ee_reclass = AHV_employee * reclass_base if reclass_base>0 else 0.0
        taxable_reclass = clamp(reclass_base - ee_reclass)

        taxable_fed  = clamp(taxable_salary + taxable_reclass + nonker_div*inc_fed + other - pk_buyin)
        taxable_cant = clamp(taxable_salary + taxable_reclass + nonker_div*inc_cant + other - pk_buyin)

        fed_tax  = federal_income_tax(taxable_fed)
        cant_tax = cantonal_income_tax(taxable_cant, kanton, gemeinde) * (1.0 + church)
        income_tax = fed_tax + cant_tax

        retained_after_tax = clamp(profit_after_salary - corp_tax_amt - dividend)
        wealth_cost = wealth_tax_on_delta(retained_after_tax, wealth_pm)
        net_owner = (s - an["total"]) + (dividend - (ee_reclass if reclass_base>0 else 0.0)) - income_tax
        adjusted_net = net_owner - wealth_cost if apply_wealth_to_net else net_owner

        total_taxes = corp_tax_amt + income_tax
        res = {"salary": s, "dividend": dividend, "net": net_owner, "adjusted_net": adjusted_net,
               "income_tax": income_tax, "corp_tax": corp_tax_amt, "retained_after_tax": retained_after_tax,
               "total_taxes": total_taxes}
        if (best is None) or (adjusted_net > best["adjusted_net"]):
            best = res
        s += step
    return best

# ------------------------- Header -------------------------
st.markdown("<div class='pill'>🇨🇭 Unternehmer-Tool · Lohn vs. Dividende vs. Thesaurierung</div>", unsafe_allow_html=True)
st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

st.markdown(
    "<h1>Bezugsstrategie</h1>"
    "<p class='muted'>Wähle deine Parameter und klicke <b>Berechnen</b>. "
    "Wir zeigen Nettos, Steuern und die <span class='accent'>Steuerersparnis</span> der optimalen Mischung.</p>",
    unsafe_allow_html=True,
)

# ------------------------- UI (FORM) -----------------------
with st.form("bezugs_form"):
    col1, col2 = st.columns(2)
    with col1:
        profit         = st.number_input("Firmengewinn **vor Lohn** [CHF]", 0.0, step=10_000.0, key="p")
        desired_income = st.number_input("Gewünschte **Gesamtauszahlung** an Inhaber [CHF] (optional)", 0.0, step=10_000.0, key="d")
        ahv_on_choice  = st.radio("AHV/ALV/BVG auf Lohn anwenden?", ["Ja", "Nein"], key="ahv")
        age_band       = st.selectbox("Altersband (BVG)", ["25-34 (7%)","35-44 (10%)","45-54 (15%)","55-65 (18%)"], index=1, key="age")

    with col2:
        # Canton selector
        canton = st.selectbox("Kanton", sorted(canton_to_communes.keys()), key="kanton")
        # Communes depend on the current canton (fix)
        communes = canton_to_communes.get(canton, ["Default"])
        if "gemeinde" not in st.session_state or st.session_state.get("last_canton") != canton or st.session_state.get("gemeinde") not in communes:
            st.session_state["gemeinde"] = communes[0] if communes else "Default"
        st.session_state["last_canton"] = canton
        commune = st.selectbox("Gemeinde", communes, key="gemeinde")

        other_inc   = st.number_input("Weitere steuerbare Einkünfte [CHF]", 0.0, step=10_000.0, key="other")
        church_rate = st.number_input("Kirchensteuer-Zuschlag auf kant./gemeindl. Steuer [%]", 0.0, 30.0, 0.0, step=0.5, key="church")/100.0

    st.markdown("#### Realitätschecks & Optionen")
    col3, col4 = st.columns(2)
    with col3:
        rule_mode   = st.radio("Regelmodus", ["Strikt (Dividende nur bei Lohn ≥ Mindestlohn)", "Risikobasiert (AHV-Umqualifizierung bei Lohn < Mindestlohn)"], key="rule")
        min_salary  = st.number_input("Marktüblicher Mindestlohn [CHF]", 0.0, step=10_000.0, value=120_000.0, key="minsal")
        share_pct   = st.number_input("Beteiligungsquote [%] (Teilbesteuerung ab 10 %)", 0.0, 100.0, 100.0, step=5.0, key="share")
        ker_amount  = st.number_input("KER verfügbar (steuerfreie Ausschüttung) [CHF]", 0.0, step=10_000.0, key="ker")
    with col4:
        fak_rate    = st.number_input("FAK (nur Arbeitgeber) [%]", 0.0, 5.0, 1.5, step=0.1, key="fak")/100.0
        uvg_rate    = st.number_input("UVG/KTG (Arbeitgeber) [%]", 0.0, 5.0, 1.0, step=0.1, key="uvg")/100.0
        pk_buyin    = st.number_input("PK-Einkauf (privat) [CHF]", 0.0, step=5_000.0, key="pk")
        wealth_rate_pm = st.number_input("Vermögenssteuer-Impact auf Eigenkapitaländerung [‰]", 0.0, 10.0, 0.0, step=0.5, key="wealth")/1000.0

    apply_wealth_to_net = st.checkbox("Vermögenssteuer-Impact vom Netto abziehen (Approximation)", value=False, key="applywealth")
    optimizer_on = st.checkbox("🧠 Optimierer – beste Mischung (Lohn + Dividende) finden", value=True, key="opt")
    debug_mode = st.checkbox("Debug-Informationen anzeigen", value=False, key="dbg")

    submitted = st.form_submit_button("🔢 Berechnen", help="Berechnet alle Szenarien und die optimale Mischung.", use_container_width=True)

# ------------------------- Run after submit ----------------
if submitted:
    ahv_on = (st.session_state.ahv == "Ja")
    if desired_income == 0:
        desired_income = None
    elif desired_income > profit:
        desired_income = profit

    fed_corp, local_corp, total_corp = corp_tax_components(canton, commune)
    age_key = age_band.split()[0]

    if profit <= 0:
        st.warning("Bitte Gewinn > 0 eingeben, um die Berechnung zu starten.")
    else:
        # Szenarien berechnen
        A = scenario_salary_only(profit, desired_income, canton, commune, age_key, ahv_on, church_rate, other_inc, pk_buyin, wealth_rate_pm, total_corp)
        B = scenario_dividend(profit, desired_income, canton, commune, age_key, ahv_on, church_rate, other_inc, pk_buyin, wealth_rate_pm,
                              rule_mode, min_salary, share_pct, ker_amount, total_corp)
        C = scenario_retention(profit, wealth_rate_pm, total_corp)

        # --- KPIs header ---
        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown(f"<div class='kpi'><h3>Corporate Tax Rate</h3><div class='v'>{(total_corp*100):.2f}%</div></div>", unsafe_allow_html=True)
        with k2:
            st.markdown(f"<div class='kpi'><h3>Bund / Kanton+Gemeinde</h3><div class='v'>{(fed_corp*100):.1f}% / {(local_corp*100):.1f}%</div></div>", unsafe_allow_html=True)
        with k3:
            st.markdown(f"<div class='kpi'><h3>Beteiligung</h3><div class='v'>{share_pct:.0f}%</div></div>", unsafe_allow_html=True)
        with k4:
            st.markdown(f"<div class='kpi'><h3>Mindestlohn</h3><div class='v'>{fmt_chf(min_salary)}</div></div>", unsafe_allow_html=True)

        # ----- A: Salary only -----
        st.markdown("### 💼 Szenario A – 100% Lohn")
        colA1, colA2 = st.columns([2,1])
        with colA1:
            st.markdown(
                f"<div class='card'>"
                f"<b>Bruttolohn:</b> {fmt_chf(A['salary'])}<br>"
                f"{'AG AHV/ALV/BVG: ' + fmt_chf(A['blocks']['ag']['ahv']+A['blocks']['ag']['alv']+A['blocks']['ag']['bvg']) if ahv_on else ''}"
                f"{'<br>AG FAK/UVG/KTG: ' + fmt_chf(A['blocks']['ag']['extra']) if ahv_on else ''}"
                f"{'<br>AN AHV/ALV/BVG (abzugsfähig): ' + fmt_chf(A['blocks']['an']['total']) if ahv_on else ''}"
                f"<br><span class='muted'>Nachsteuerlicher Gewinn einbehalten:</span> {fmt_chf(A['retained_after_tax'])}"
                f"</div>",
                unsafe_allow_html=True,
            )
        with colA2:
            st.markdown(
                f"<div class='card'><b>Körperschaftssteuer:</b> {fmt_chf(A['corp_tax'])}"
                f"<br><b>Einkommenssteuer:</b> {fmt_chf(A['income_tax'])}"
                f"<br><b>Steuern gesamt:</b> {fmt_chf(A['total_taxes'])}"
                f"<div class='divider'></div>"
                f"<b>Netto an Inhaber:</b> <span class='accent'>{fmt_chf(A['adjusted_net'])}</span></div>",
                unsafe_allow_html=True,
            )

        # ----- B: Salary + Dividend -----
        st.markdown("### 📈 Szenario B – Lohn + Dividende")
        colB1, colB2 = st.columns([2,1])
        with colB1:
            t_partial = f"Teilbesteuerung (Nicht-KER): Bund {int(B['blocks']['inc_fed']*100)}%, Kanton {int(B['blocks']['inc_cant']*100)}%"
            reclass_txt = ""
            if B["blocks"]["reclass"]>0:
                reclass_txt = f"<br><span class='muted'>AHV-Umqualifizierung:</span> Basis {fmt_chf(B['blocks']['reclass'])} (AN-AHV {fmt_chf(B['blocks']['ee_reclass'])})"
            st.markdown(
                f"<div class='card'>"
                f"<b>Bruttolohn:</b> {fmt_chf(B['salary'])}"
                f"<br><b>Dividende gesamt:</b> {fmt_chf(B['dividend'])}"
                f"<br><span class='muted'>KER steuerfrei genutzt:</span> {fmt_chf(B['blocks']['ker_used'])}"
                f"<br><span class='muted'>{t_partial}</span>"
                f"{reclass_txt}"
                f"<br><span class='muted'>Nachsteuerlicher Gewinn einbehalten:</span> {fmt_chf(B['retained_after_tax'])}"
                f"</div>",
                unsafe_allow_html=True,
            )
        with colB2:
            st.markdown(
                f"<div class='card'><b>Körperschaftssteuer:</b> {fmt_chf(B['corp_tax'])}"
                f"<br><b>Einkommenssteuer:</b> {fmt_chf(B['income_tax'])}"
                f"<br><b>Steuern gesamt:</b> {fmt_chf(B['total_taxes'])}"
                f"<div class='divider'></div>"
                f"<b>Netto an Inhaber:</b> <span class='accent'>{fmt_chf(B['adjusted_net'])}</span></div>",
                unsafe_allow_html=True,
            )

        # ----- C: Retention -----
        st.markdown("### 🏗️ Szenario C – Thesaurierung (keine Auszahlung)")
        colC1, colC2 = st.columns([2,1])
        with colC1:
            st.markdown(
                f"<div class='card'>"
                f"<b>Körperschaftssteuer:</b> {fmt_chf(C['corp_tax'])}"
                f"<br><span class='muted'>Nachsteuerlicher Gewinn einbehalten:</span> {fmt_chf(C['retained_after_tax'])}"
                f"</div>",
                unsafe_allow_html=True,
            )
        with colC2:
            st.markdown(
                f"<div class='card'><b>Steuern gesamt:</b> {fmt_chf(C['total_taxes'])}"
                f"<div class='divider'></div>"
                f"<b>Netto an Inhaber (heute):</b> <span class='accent'>{fmt_chf(C['adjusted_net'])}</span></div>",
                unsafe_allow_html=True,
            )

        # ----- Comparison -----
        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
        st.markdown("### 🔹 Vergleich (heutiger Nettozufluss)")
        c1, c2, c3 = st.columns(3)
        c1.metric("A: Lohn", fmt_chf(A["adjusted_net"]))
        c2.metric("B: Lohn+Dividende", fmt_chf(B["adjusted_net"]))
        c3.metric("C: Thesaurierung", fmt_chf(C["adjusted_net"]))

        # ----- Optimizer -----
        if optimizer_on:
            st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
            st.markdown("### 🧠 Optimierer – beste Mischung (unter gewählten Regeln)")
            best = optimize_mix(profit, desired_income, canton, commune, age_key, ahv_on, church_rate, other_inc, pk_buyin,
                                wealth_rate_pm, rule_mode, min_salary, share_pct, ker_amount, total_corp, step=1000.0)
            colO1, colO2 = st.columns([2,1])
            with colO1:
                st.markdown(
                    f"<div class='card'><b>Optimaler Lohn:</b> {fmt_chf(best['salary'])}"
                    f"<br><b>Dividende:</b> {fmt_chf(best['dividend'])}"
                    f"<br><span class='muted'>Nachsteuerlich einbehalten:</span> {fmt_chf(best['retained_after_tax'])}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with colO2:
                st.markdown(
                    f"<div class='card'><b>Körperschaftssteuer:</b> {fmt_chf(best['corp_tax'])}"
                    f"<br><b>Einkommenssteuer:</b> {fmt_chf(best['income_tax'])}"
                    f"<br><b>Steuern gesamt:</b> {fmt_chf(best['total_taxes'])}"
                    f"<div class='divider'></div>"
                    f"<b>Max. Netto (heute):</b> <span class='accent'>{fmt_chf(best['adjusted_net'])}</span></div>",
                    unsafe_allow_html=True,
                )

            # Tax savings vs A/B/C
            saved_vs_A = A["total_taxes"] - best["total_taxes"]
            saved_vs_B = B["total_taxes"] - best["total_taxes"]
            saved_vs_C = C["total_taxes"] - best["total_taxes"]
            st.markdown(
                f"<div class='card'><b>💡 Steuer-Ersparnis (heute)</b><br>"
                f"gegenüber <b>100% Lohn</b>: <span class='accent'>{fmt_chf(saved_vs_A)}</span><br>"
                f"gegenüber <b>Standard Lohn+Dividende</b>: <span class='accent'>{fmt_chf(saved_vs_B)}</span><br>"
                f"gegenüber <b>Thesaurierung</b>: <span class='accent'>{fmt_chf(saved_vs_C)}</span></div>",
                unsafe_allow_html=True,
            )

        # ----- Debug -----
        if debug_mode:
            st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
            st.markdown("#### 🔍 Debug-Informationen")
            st.write(f"Körperschaftssteuer-Satz gesamt: {total_corp:.2%} (Bund {fed_corp:.2%}, Kanton+Gemeinde {local_corp:.2%})")
            st.write(f"Teilbesteuerung aktiv: {'Ja' if qualifies_partial(share_pct) else 'Nein'} "
                     f"| Bund {int((0.70 if qualifies_partial(share_pct) else 1.00)*100)}%, "
                     f"Kanton {int((dividend_inclusion.get(canton,0.70) if qualifies_partial(share_pct) else 1.00)*100)}%")
            st.write(f"Regelmodus: {rule_mode} | Mindestlohn: {fmt_chf(min_salary)}")
            if ker_amount>0: st.write(f"KER verfügbar: {fmt_chf(ker_amount)}")
            if pk_buyin>0:  st.write(f"PK-Einkauf berücksichtigt: {fmt_chf(pk_buyin)}")
            if wealth_rate_pm>0:
                st.caption("Vermögenssteuer-Impact ist eine Approximation auf Δ Eigenkapital (heutige Sicht).")

else:
    st.info("👉 Wähle oben deine Parameter und klick auf **Berechnen**.")
    st.caption("Hinweis: Die Resultate sind Modellrechnungen und ersetzen keine individuelle Steuerberatung.")
