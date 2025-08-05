# app.py  – Swiss Salary vs Dividend calculator
import json, math, pathlib
import streamlit as st

DATA_DIR = pathlib.Path(__file__).parent        # <- folder that contains the *.json files
files = {
    "steuer":               "Steuerfuesse.json",
    "cant_income":          "Income_Tax_Cantons.json",
    "fed_income":           "Income_Tax_Confederation.json",
    "corp_tax":             "Corporate_Income_Tax.json",
    "social":               "Social_Security_Contributions.json",
    "div_inclusion":        "Teilbesteuerung_Dividenden.json",   # optional
}

# ------------------------- helpers ------------------------------------------------
def load_json(name, default):
    fp = DATA_DIR / files[name]
    try:
        with fp.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def nan_to_zero(x):
    return 0.0 if (x is None or (isinstance(x, float) and math.isnan(x))) else x

# ------------------------- load data ----------------------------------------------
steuerfuesse          = load_json("steuer",      [])                      # communal & cantonal multipliers
income_tax_cantons    = load_json("cant_income", {})
income_tax_conf       = sorted(
    load_json("fed_income", []),
    key=lambda d: d["Taxable income for federal tax"] or 0
)
corporate_tax         = load_json("corp_tax",    {})
social_sec            = load_json("social",      {})
dividend_inclusion    = load_json("div_inclusion",
                                  {})            # empty ⇒ we’ll fall back to 70 % rule later on

# ------------------------- social-security constants ------------------------------
AHV_employer   = social_sec["AHV_IV_EO_EmployerShare"]   # 0.053
AHV_employee   = social_sec["AHV_IV_EO_EmployeeShare"]   # 0.053
AHV_total      = AHV_employer + AHV_employee             # ← key missing in JSON ⇒ compute here

ALV_total      = social_sec["ALV_TotalRate"]
ALV_employer   = social_sec["ALV_EmployerShare"]
ALV_employee   = social_sec["ALV_EmployeeShare"]
ALV_ceiling    = social_sec["ALV_Ceiling"]

BVG_rate_total = social_sec["BVG_Rate_35_44"]            # pick one age band – change if needed
BVG_entry_threshold = social_sec["BVG_EntryThreshold"]
BVG_coord_deduction = social_sec["BVG_CoordDeduction"]
BVG_max_insured     = social_sec["BVG_MaxInsuredSalary"]
BVG_rate_employee   = BVG_rate_total / 2
BVG_rate_employer   = BVG_rate_total / 2

# ------------------------- canton-to-commune mapping ------------------------------
canton_to_communes = {}
for row in steuerfuesse:
    kanton  = row.get("Kanton")
    gemeinde= row.get("Gemeinde")
    if not kanton or not gemeinde or kanton == "Kanton":
        continue
    canton_to_communes.setdefault(kanton, []).append(gemeinde)
for k in canton_to_communes:
    canton_to_communes[k].sort()

# ------------------------- UI -----------------------------------------------------
st.title("Swiss Salary vs Dividend – Tax Calculator")
st.caption("Works with the raw JSON dumps shipped in the repository – no database needed.")

col1, col2 = st.columns(2)
with col1:
    profit         = st.number_input("Company profit **before** salary [CHF]", 0.0, step=10_000.0)
    desired_income = st.number_input("Owner’s desired payout (optional) [CHF]", 0.0, step=10_000.0)
    ahv_subject    = st.radio("AHV/ALV contributions apply?", ["Yes", "No"])
with col2:
    canton   = st.selectbox("Canton", sorted(canton_to_communes))
    commune  = st.selectbox("Commune", canton_to_communes[canton])
    other_inc= st.number_input("Other taxable income (optional) [CHF]", 0.0, step=10_000.0)

if desired_income == 0:
    desired_income = None
elif desired_income > profit:
    desired_income = profit

# ---------------------------------------------------------------- utilities -------
def federal_income_tax(taxable):
    prev = 0.0
    for b in income_tax_conf:
        thr   = b["Taxable income for federal tax"]
        rate  = b["Additional %"] / 100
        base  = b["Base amount CHF"]
        if taxable > thr:
            prev = thr
            continue
        return base + (taxable - prev) * rate
    # fell through top bracket
    top = income_tax_conf[-1]
    return top["Base amount CHF"] + (taxable - top["Taxable income for federal tax"]) * (top["Additional %"] / 100)

def cantonal_income_tax(taxable, kanton, gemeinde):
    brackets = income_tax_cantons.get(kanton, [])
    remaining, base = taxable, 0.0
    for b in brackets:
        chunk = min(remaining, b["For the next CHF"])
        base += chunk * (b["Additional %"] / 100)
        remaining -= chunk
        if remaining <= 0: break
    if remaining > 0:                            # income over last bracket – use top rate
        base += remaining * (brackets[-1]["Additional %"] / 100) if brackets else 0

    kant_mult = 1.0
    comm_mult = 0.0
    for row in steuerfuesse:
        if row["Kanton"] == kanton and row["Gemeinde"] == gemeinde:
            kant_mult = nan_to_zero(row.get("Einkommen_Kanton", 1.0))
            comm_mult = nan_to_zero(row.get("Einkommen_Gemeinde", 0.0))
            break
    return base * kant_mult + base * comm_mult

# ---------------------------------------------------------------- calculations ----
if profit > 0:
    # ----- lookup rates / multipliers -------------------------------------------
    fed_corp = corporate_tax.get("Confederation", 0.085)
    cant_corp_base = nan_to_zero(corporate_tax.get(canton, 0.0))
    canton_mult, comm_mult = 1.0, 0.0
    for row in steuerfuesse:
        if row["Kanton"] == canton and row["Gemeinde"] == commune:
            canton_mult = nan_to_zero(row.get("Gewinn_Kanton", 1.0))
            comm_mult   = nan_to_zero(row.get("Gewinn_Gemeinde", 0.0))
            break
    local_corp = cant_corp_base * (canton_mult + comm_mult)
    total_corp = fed_corp + local_corp

    # partial dividend inclusion
    incl = dividend_inclusion.get(canton, 0.70)   # default: 70 % taxable

    # ----- Scenario A – Salary ---------------------------------------------------
    salary = desired_income if desired_income is not None else profit
    salary = min(salary, profit)

    if ahv_subject == "Yes":
        # employer part
        ahv_emp = AHV_employer * salary
        alv_emp = ALV_employer * min(salary, ALV_ceiling) + 0.005 * max(0, salary - ALV_ceiling)
        bvg_emp = 0.0
        if salary >= BVG_entry_threshold:
            insured = max(0, min(salary, BVG_max_insured) - BVG_coord_deduction)
            bvg_emp = BVG_rate_employer * insured
        employer_cost = ahv_emp + alv_emp + bvg_emp

        # employee part
        ahv_emp_ee = AHV_employee * salary
        alv_emp_ee = ALV_employee * min(salary, ALV_ceiling) + 0.005 * max(0, salary - ALV_ceiling)
        bvg_emp_ee = bvg_emp   # same insured salary, same rate
        employee_deductions = ahv_emp_ee + alv_emp_ee + bvg_emp_ee
    else:
        employer_cost = employee_deductions = 0.0

    profit_after_salary = max(0.0, profit - salary - employer_cost)
    corp_tax_A  = profit_after_salary * total_corp
    taxable_A   = salary + other_inc
    income_tax_A= federal_income_tax(taxable_A) + cantonal_income_tax(taxable_A, canton, commune)
    net_A       = salary - employee_deductions - income_tax_A

    # ----- Scenario B – Dividend -------------------------------------------------
    corp_tax_B  = profit * total_corp
    after_corp  = max(0.0, profit - corp_tax_B)
    dividend    = min(after_corp, desired_income) if desired_income else after_corp
    taxable_B   = dividend * incl + other_inc
    income_tax_B= federal_income_tax(taxable_B) + cantonal_income_tax(taxable_B, canton, commune)
    net_B       = dividend - income_tax_B

    # --------------------- display ----------------------------------------------
    st.subheader("Scenario A – Salary")
    st.write(f"Gross salary: **CHF {salary:,.0f}**")
    if ahv_subject == "Yes":
        st.write(f"Employer AHV/ALV/BVG: CHF {employer_cost:,.0f}")
        st.write(f"Employee AHV/ALV/BVG: CHF {employee_deductions:,.0f}")
    else:
        st.write("No social-security contributions.")
    st.write(f"Corporate tax on remaining profit: CHF {corp_tax_A:,.0f}")
    st.write(f"Personal income tax: CHF {income_tax_A:,.0f}")
    st.success(f"**Net to owner:** CHF {net_A:,.0f}")

    st.subheader("Scenario B – Dividend")
    st.write(f"Dividend paid: **CHF {dividend:,.0f}**")
    st.write(f"Corporate tax (full profit): CHF {corp_tax_B:,.0f}")
    st.write(f"Personal income tax (after {int(incl*100)} % inclusion): CHF {income_tax_B:,.0f}")
    st.success(f"**Net to owner:** CHF {net_B:,.0f}")

    st.markdown("---")
    if net_A > net_B:
        st.info(f"💡 **Salary** is better by **CHF {net_A-net_B:,.0f}**.")
    elif net_B > net_A:
        st.info(f"💡 **Dividend** is better by **CHF {net_B-net_A:,.0f}**.")
    else:
        st.info("Both options yield the same net amount.")
else:
    st.warning("Enter a profit larger than 0 to start the comparison.")
