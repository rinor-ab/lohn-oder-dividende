# app.py  – Swiss Salary vs Dividend calculator (FIXED VERSION)
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
        st.warning(f"File {files[name]} not found. Using defaults.")
        return default

def nan_to_zero(x):
    return 0.0 if (x is None or (isinstance(x, float) and math.isnan(x))) else x

# ------------------------- load data ----------------------------------------------
steuerfuesse          = load_json("steuer",      [])                      # communal & cantonal multipliers
income_tax_cantons    = load_json("cant_income", {})
income_tax_conf       = sorted(
    load_json("fed_income", []),
    key=lambda d: d.get("Taxable income for federal tax", 0) or 0
)
corporate_tax         = load_json("corp_tax",    {})
social_sec            = load_json("social",      {})
dividend_inclusion    = load_json("div_inclusion", {})

# ------------------------- social-security constants ------------------------------
AHV_employer   = social_sec.get("AHV_IV_EO_EmployerShare", 0.053)   # 5.3%
AHV_employee   = social_sec.get("AHV_IV_EO_EmployeeShare", 0.053)   # 5.3%
AHV_total      = AHV_employer + AHV_employee

ALV_total      = social_sec.get("ALV_TotalRate", 0.022)              # 2.2%
ALV_employer   = social_sec.get("ALV_EmployerShare", 0.011)          # 1.1%
ALV_employee   = social_sec.get("ALV_EmployeeShare", 0.011)          # 1.1%
ALV_ceiling    = social_sec.get("ALV_Ceiling", 148200.0)            # From your JSON
ALV_solidarity = 0.005  # Fixed rate for high earners above ALV ceiling (0.5%)

# BVG rates by age - using your JSON structure
BVG_rates = {
    "25-34": social_sec.get("BVG_Rate_25_34", 0.07),    # 7%
    "35-44": social_sec.get("BVG_Rate_35_44", 0.10),    # 10%
    "45-54": social_sec.get("BVG_Rate_45_54", 0.15),    # 15%
    "55-65": social_sec.get("BVG_Rate_55_65", 0.18)     # 18%
}
BVG_entry_threshold = social_sec.get("BVG_EntryThreshold", 22680.0)  # From your JSON
BVG_coord_deduction = social_sec.get("BVG_CoordDeduction", 26460.0)  # From your JSON
BVG_max_insured     = social_sec.get("BVG_MaxInsuredSalary", 90720.0) # From your JSON

# Default to 35-44 age band (10% total, 5% each for employee/employer)
BVG_rate_total = BVG_rates["35-44"]
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

# Add fallback if no communes found
if not canton_to_communes:
    st.error("No tax data found in Steuerfuesse.json. Please check the file format.")
    canton_to_communes = {"Zürich": ["Zürich"], "Bern": ["Bern"]}  # fallback

# ------------------------- UI -----------------------------------------------------
st.title("Swiss Salary vs Dividend – Tax Calculator")
st.caption("Works with the raw JSON dumps shipped in the repository – no database needed.")

col1, col2 = st.columns(2)
with col1:
    profit         = st.number_input("Company profit **before** salary [CHF]", 0.0, step=10_000.0)
    desired_income = st.number_input("Owner's desired payout (optional) [CHF]", 0.0, step=10_000.0)
    ahv_subject    = st.radio("AHV/ALV contributions apply?", ["Yes", "No"])
    # Add age selection for BVG rates
    age_band = st.selectbox("Owner's age band (for BVG)", 
                           ["25-34 (7%)", "35-44 (10%)", "45-54 (15%)", "55-65 (18%)"], 
                           index=1)  # default to 35-44
with col2:
    canton   = st.selectbox("Canton", sorted(canton_to_communes.keys()))
    commune  = st.selectbox("Commune", canton_to_communes.get(canton, ["Default"]))
    other_inc= st.number_input("Other taxable income (optional) [CHF]", 0.0, step=10_000.0)
    # Add debug toggle
    debug_mode = st.checkbox("Show debug information")
    if 'debug_mode' not in st.session_state:
        st.session_state.debug_mode = debug_mode
    st.session_state.debug_mode = debug_mode

if desired_income == 0:
    desired_income = None
elif desired_income > profit:
    desired_income = profit

# ---------------------------------------------------------------- utilities -------
def federal_income_tax(taxable):
    """
    FIXED: Properly calculates progressive federal income tax by accumulating through brackets
    """
    if taxable <= 0:
        return 0.0
    
    if not income_tax_conf:
        st.warning("No federal tax brackets loaded. Using simplified calculation.")
        return taxable * 0.115  # rough average rate
    
    total_tax = 0.0
    remaining_income = taxable
    prev_threshold = 0.0
    
    for bracket in income_tax_conf:
        threshold = bracket.get("Taxable income for federal tax", 0)
        rate = bracket.get("Additional %", 0) / 100
        base = bracket.get("Base amount CHF", 0)
        
        if threshold is None or threshold == 0:
            continue
            
        # Income in this bracket
        bracket_width = threshold - prev_threshold
        income_in_bracket = min(remaining_income, bracket_width)
        
        if income_in_bracket > 0:
            total_tax += income_in_bracket * rate
            remaining_income -= income_in_bracket
        
        if remaining_income <= 0:
            return total_tax + base
            
        prev_threshold = threshold
    
    # Income exceeds highest bracket - apply top marginal rate to remaining income
    if remaining_income > 0 and income_tax_conf:
        top_rate = income_tax_conf[-1].get("Additional %", 11.5) / 100
        total_tax += remaining_income * top_rate
        total_tax += income_tax_conf[-1].get("Base amount CHF", 0)
    
    return total_tax

def cantonal_income_tax(taxable, kanton, gemeinde):
    """
    Calculate cantonal and communal income tax with proper multipliers
    """
    if taxable <= 0:
        return 0.0
        
    brackets = income_tax_cantons.get(kanton, [])
    cantonal_base_tax = 0.0
    remaining = taxable
    
    # Calculate base cantonal tax using brackets
    for bracket in brackets:
        chunk_size = bracket.get("For the next CHF", 0)
        rate = bracket.get("Additional %", 0) / 100
        
        if chunk_size == 0:  # unlimited bracket
            cantonal_base_tax += remaining * rate
            break
        
        chunk = min(remaining, chunk_size)
        cantonal_base_tax += chunk * rate
        remaining -= chunk
        
        if remaining <= 0:
            break
    
    # If income exceeds all brackets, use top rate for remainder
    if remaining > 0 and brackets:
        top_rate = brackets[-1].get("Additional %", 0) / 100
        cantonal_base_tax += remaining * top_rate
    
    # Apply cantonal and communal multipliers
    kant_mult = 1.0
    comm_mult = 0.0
    
    for row in steuerfuesse:
        if row.get("Kanton") == kanton and row.get("Gemeinde") == gemeinde:
            kant_mult = nan_to_zero(row.get("Einkommen_Kanton", 1.0))
            comm_mult = nan_to_zero(row.get("Einkommen_Gemeinde", 0.0))
            break
    
    # Total = cantonal tax + communal tax
    total_cantonal_communal = cantonal_base_tax * kant_mult + cantonal_base_tax * comm_mult
    return total_cantonal_communal

def get_dividend_inclusion_rate(kanton):
    """
    Get partial dividend inclusion rate for any canton from your JSON
    Your JSON uses canton codes like ZH, BE, etc.
    """
    # Direct lookup from your Teilbesteuerung_Dividenden.json
    inclusion_rate = dividend_inclusion.get(kanton, 0.70)  # 70% fallback
    
    # Debug output
    if st.session_state.get('debug_mode', False):
        st.write(f"Debug: Canton {kanton} dividend inclusion rate: {inclusion_rate}")
    
    return inclusion_rate

# ---------------------------------------------------------------- calculations ----
if profit > 0:
    # ----- lookup rates / multipliers -------------------------------------------
    fed_corp = corporate_tax.get("Confederation", 0.085)
    
    # Handle both old and new corporate tax JSON formats
    cant_corp_data = corporate_tax.get(canton, 0.0)
    if isinstance(cant_corp_data, dict):
        cant_corp_base = cant_corp_data.get("rate", cant_corp_data.get("cantonal", 0.0))
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

    # Extract age band for BVG calculation
    age_key = age_band.split()[0]  # "25-34", "35-44", etc.
    selected_bvg_rate = BVG_rates.get(age_key, BVG_rates["35-44"])
    bvg_employee_rate = selected_bvg_rate / 2
    bvg_employer_rate = selected_bvg_rate / 2

    # FIXED: Get partial dividend inclusion from your JSON
    dividend_incl_rate = get_dividend_inclusion_rate(canton)

    # ----- Scenario A – Salary ---------------------------------------------------
    salary = desired_income if desired_income is not None else profit
    salary = min(salary, profit)

    if ahv_subject == "Yes":
        # employer part
        ahv_emp = AHV_employer * salary
        alv_emp = ALV_employer * min(salary, ALV_ceiling) + ALV_solidarity * max(0, salary - ALV_ceiling)
        bvg_emp = 0.0
        if salary >= BVG_entry_threshold:
            insured = max(0, min(salary, BVG_max_insured) - BVG_coord_deduction)
            bvg_emp = bvg_employer_rate * insured  # Use selected age rate
        employer_cost = ahv_emp + alv_emp + bvg_emp

        # employee part - calculate separately with your JSON values
        ahv_emp_ee = AHV_employee * salary
        alv_emp_ee = ALV_employee * min(salary, ALV_ceiling) + ALV_solidarity * max(0, salary - ALV_ceiling)
        bvg_emp_ee = 0.0
        if salary >= BVG_entry_threshold:
            insured_ee = max(0, min(salary, BVG_max_insured) - BVG_coord_deduction)
            bvg_emp_ee = bvg_employee_rate * insured_ee  # Use selected age rate
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
    taxable_B   = dividend * dividend_incl_rate + other_inc  # FIXED: use JSON rate
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
    st.write(f"Personal income tax (after {int(dividend_incl_rate*100)}% inclusion): CHF {income_tax_B:,.0f}")
    st.success(f"**Net to owner:** CHF {net_B:,.0f}")

    st.markdown("---")
    st.subheader("📊 Comparison")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Salary Net", f"CHF {net_A:,.0f}")
    with col2:
        st.metric("Dividend Net", f"CHF {net_B:,.0f}")
    with col3:
        difference = net_B - net_A
        better = "Dividend" if difference > 0 else "Salary"
        st.metric("Advantage", f"{better}", f"CHF {abs(difference):,.0f}")
    
    if net_A > net_B:
        st.info(f"💡 **Salary** is better by **CHF {net_A-net_B:,.0f}**.")
    elif net_B > net_A:
        st.info(f"💡 **Dividend** is better by **CHF {net_B-net_A:,.0f}**.")
    else:
        st.info("✅ Both options yield the same net amount.")
        
    # Debug info (optional)
    if debug_mode:
        st.subheader("🔍 Debug Information")
        st.write(f"**Data Loaded:**")
        st.write(f"- Social security data: {len(social_sec)} fields")
        st.write(f"- Dividend inclusion data: {len(dividend_inclusion)} cantons")
        st.write(f"- Federal tax brackets: {len(income_tax_conf)} brackets")
        
        st.write(f"**Tax Rates Used:**")
        st.write(f"- Corporate tax total: {total_corp:.1%} (Fed: {fed_corp:.1%}, Cantonal+Communal: {local_corp:.1%})")
        st.write(f"- Dividend inclusion rate: {dividend_incl_rate:.1%}")
        st.write(f"- Canton multipliers: Cantonal {canton_mult:.2f}, Communal {comm_mult:.2f}")
        st.write(f"- BVG rate used: {selected_bvg_rate:.1%} (age band: {age_key})")
        
        st.write(f"**Social Insurance Thresholds:**")
        st.write(f"- ALV ceiling: CHF {ALV_ceiling:,.0f}")
        st.write(f"- BVG entry threshold: CHF {BVG_entry_threshold:,.0f}")
        st.write(f"- BVG coordination deduction: CHF {BVG_coord_deduction:,.0f}")
        st.write(f"- BVG max insured salary: CHF {BVG_max_insured:,.0f}")
        
        if ahv_subject == "Yes" and salary > 0:
            insured_salary = max(0, min(salary, BVG_max_insured) - BVG_coord_deduction)
            st.write(f"**BVG Calculation for salary CHF {salary:,.0f}:**")
            st.write(f"- Insured salary: CHF {insured_salary:,.0f}")
            st.write(f"- Employee BVG: CHF {bvg_emp_ee:,.0f}")
            st.write(f"- Employer BVG: CHF {bvg_emp:,.0f}")
        
else:
    st.warning("Enter a profit larger than 0 to start the comparison.")
