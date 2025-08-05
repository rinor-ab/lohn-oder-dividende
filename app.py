import json
import streamlit as st

# Load tax data from JSON files (prepared earlier)
# For this example, assume the JSON data is stored in files in the same directory.
# The JSON structure comes from the provided Excel data for cantonal/communal tax rates, etc.
with open("Steuerfuesse.json", "r") as f:
    steuerfuesse = json.load(f)        # communal and cantonal tax multipliers
with open("Income_Tax_Cantons.json", "r") as f:
    income_tax_cantons = json.load(f)  # cantonal income tax brackets
with open("Income_Tax_Confederation.json", "r") as f:
    income_tax_conf = json.load(f)     # federal income tax brackets (single, no children)
with open("Corporate_Income_Tax.json", "r") as f:
    corporate_tax = json.load(f)       # cantonal base corporate tax rates
with open("Teilbesteuerung_Dividenden.json", "r") as f:
    dividend_inclusion = json.load(f)  # partial taxation percentage for dividends by canton
with open("Social_Security_Contributions.json", "r") as f:
    social_sec = json.load(f)          # AHV/ALV rates, thresholds, etc.

# Extract social security rates and thresholds for easy reference
AHV_total = social_sec["AHV_IV_EO_TotalRate"]        # 0.106 (10.6%)
AHV_employer = social_sec["AHV_IV_EO_EmployerShare"]  # 0.053 (5.3%)
AHV_employee = social_sec["AHV_IV_EO_EmployeeShare"]  # 0.053 (5.3%)
ALV_total = social_sec["ALV_TotalRate"]              # 0.022 (2.2%)
ALV_employer = social_sec["ALV_EmployerShare"]        # 0.011 (1.1%)
ALV_employee = social_sec["ALV_EmployeeShare"]        # 0.011 (1.1%)
ALV_ceiling = social_sec["ALV_Ceiling"]               # 148200.0 CHF
# For simplicity, use a fixed pension contribution rate (e.g., 10% total) for mandatory portion
BVG_rate_total = social_sec["BVG_Rate_35_44"]         # 0.10 (10% total for age 35-44)
BVG_entry_threshold = social_sec["BVG_EntryThreshold"]   # 22680.0 CHF
BVG_coord_deduction = social_sec["BVG_CoordDeduction"]   # 26460.0 CHF
BVG_max_insured = social_sec["BVG_MaxInsuredSalary"]     # 90720.0 CHF
# Split pension rate equally between employer and employee:
BVG_rate_employee = BVG_rate_total / 2.0   # e.g., 0.05
BVG_rate_employer = BVG_rate_total / 2.0   # e.g., 0.05

# Build a mapping from canton code to communes and multipliers for quick access
canton_to_communes = {}
for entry in steuerfuesse:
    canton_code = entry["Kanton"]        # e.g. "ZH"
    commune_name = entry["Gemeinde"]     # e.g. "Zürich"
    if canton_code not in canton_to_communes:
        canton_to_communes[canton_code] = []
    canton_to_communes[canton_code].append(commune_name)

# Sort commune lists for each canton alphabetically for nicer dropdown
for cant in canton_to_communes:
    canton_to_communes[cant] = sorted(canton_to_communes[cant])

# Streamlit UI
st.title("Swiss Salary vs. Dividend Tax Calculator")

st.markdown("Enter your company profit and preferences to compare **Salary vs Dividend** scenarios for withdrawing profit. The tool will show social contributions, taxes, and net payout to the owner for each option.")

# Input widgets
col1, col2 = st.columns(2)
with col1:
    profit = st.number_input("Company Profit (before salary) [CHF]", min_value=0.0, value=0.0, step=10000.0)
    desired_income = st.number_input("Desired Total Payout to Owner (optional) [CHF]", min_value=0.0, value=0.0, step=10000.0)
    ahv_subject = st.radio("AHV/ALV Contributions Applicable?", options=["Yes", "No"], index=0, help="Select 'No' if the owner is not subject to AHV (e.g., already past retirement age).")
with col2:
    canton = st.selectbox("Canton", options=sorted(canton_to_communes.keys()))
    commune = st.selectbox("Commune", options=canton_to_communes[canton] if canton else [])
    other_income = st.number_input("Other Taxable Income (optional) [CHF]", min_value=0.0, value=0.0, step=10000.0)

# If desired_income is 0 or >= profit, we'll treat it as "withdraw full profit"
if desired_income <= 0:
    desired_income = None
if desired_income and desired_income > profit:
    desired_income = profit

# Lookup partial dividend taxation rate for selected canton (default to 1.0 if not found)
if canton in dividend_inclusion:
    div_inclusion_pct = dividend_inclusion[canton]  # e.g., 0.5 means 50% taxable
else:
    div_inclusion_pct = 1.0  # if not specified, assume 100% taxable (no relief)

# Lookup corporate tax base rate for selected canton and communal multipliers
# Federal corporate tax rate (direct federal tax on profit)
fed_corp_rate = corporate_tax["Confederation"]  # 0.085 (8.5%)
# Cantonal base corporate income tax rate
if canton in corporate_tax:
    cant_base_corp_rate = corporate_tax[canton]   # e.g., 0.07 for ZH
else:
    cant_base_corp_rate = 0.0

# Find the communal and cantonal profit tax multipliers ("Gewinn") for selected commune
canton_factor_profit = 1.0
commune_factor_profit = 0.0
for entry in steuerfuesse:
    if entry["Kanton"] == canton and entry["Gemeinde"] == commune:
        # The JSON likely stores multipliers as decimals, e.g., entry["Gewinn_Kanton"], entry["Gewinn_Gemeinde"]
        canton_factor_profit = entry.get("Gewinn_Kanton", 1.0)
        commune_factor_profit = entry.get("Gewinn_Gemeinde", 0.0)
        break

# Total cantonal+communal profit tax rate = base rate * (canton_factor + commune_factor)
local_corp_rate = cant_base_corp_rate * (canton_factor_profit + commune_factor_profit)
total_corp_tax_rate = fed_corp_rate + local_corp_rate

# Personal income tax calculation functions
def calc_federal_income_tax(taxable_income):
    """Calculate federal (confederation) income tax for a single person, no children, using the bracket table."""
    tax = 0.0
    prev_threshold = 0.0
    for bracket in income_tax_conf:
        # Each bracket in JSON might have structure: {"threshold": X, "rate": Y, "base_tax": Z}
        threshold = bracket["Taxable income for federal tax"]
        rate = bracket["Additional %"] / 100.0  # convert percent to decimal
        base = bracket["Base amount CHF"]
        if taxable_income > threshold:
            # move to next bracket
            prev_threshold = threshold
            continue
        else:
            # taxable_income is within this bracket
            # find previous bracket to get base
            tax = base + (taxable_income - prev_threshold) * rate
            break
    return max(tax, 0.0)

def calc_cantonal_income_tax(taxable_income, canton_code, commune_name):
    """Calculate cantonal+communal income tax based on canton brackets and multipliers."""
    # Sum up base cantonal tax from progressive brackets
    base_tax = 0.0
    if canton_code in income_tax_cantons:
        brackets = income_tax_cantons[canton_code]  # list of bracket dicts for this canton
        remaining = taxable_income
        for bracket in brackets:
            bracket_amount = bracket["For the next CHF"]
            rate = bracket["Additional %"] / 100.0
            if remaining <= bracket_amount:
                base_tax += remaining * rate
                remaining = 0
                break
            else:
                base_tax += bracket_amount * rate
                remaining -= bracket_amount
        # If income exceeds all brackets in table, tax on excess at top rate:
        if remaining > 0:
            top_rate = brackets[-1]["Additional %"] / 100.0
            base_tax += remaining * top_rate
    else:
        base_tax = 0.0

    # Lookup multipliers for canton and commune for income tax
    canton_factor_inc = 1.0
    commune_factor_inc = 0.0
    for entry in steuerfuesse:
        if entry["Kanton"] == canton_code and entry["Gemeinde"] == commune_name:
            canton_factor_inc = entry.get("Einkommen_Kanton", 1.0)
            commune_factor_inc = entry.get("Einkommen_Gemeinde", 0.0)
            break
    # Actual cantonal tax = base_tax * canton_factor_inc
    cantonal_tax = base_tax * canton_factor_inc
    # Communal tax = base_tax * commune_factor_inc (communal multiplier is typically % of cantonal base tax)
    communal_tax = base_tax * commune_factor_inc
    total_local_income_tax = cantonal_tax + communal_tax
    return total_local_income_tax

# Perform calculations for each scenario if a profit is provided
if profit and profit > 0:
    # Determine salary and dividend amounts based on desired payout
    if desired_income is None:
        # withdraw full profit in each scenario
        salary_take = profit   # scenario A: take full profit as salary
        dividend_take = profit # scenario B: (gross profit perspective) full profit for dividends
    else:
        salary_take = desired_income  # pay desired amount as salary (if <= profit, else capped above)
        dividend_take = desired_income  # distribute desired amount as dividend (capped by profit after tax later)

    # SCENARIO A: Salary
    salary = min(salary_take, profit)
    # Calculate social contributions if applicable
    if ahv_subject == "Yes":
        # Employer contributions
        # AHV on full salary
        ahv_employer_amt = AHV_employer * salary
        # ALV employer
        if salary <= ALV_ceiling:
            alv_employer_amt = ALV_employer * salary
        else:
            alv_employer_amt = ALV_employer * ALV_ceiling + 0.005 * max(0.0, salary - ALV_ceiling)  # 0.5% on excess
        # BVG employer (if salary above threshold)
        if salary >= BVG_entry_threshold:
            insured_salary = min(salary, BVG_max_insured) - BVG_coord_deduction
            if insured_salary < 0:
                insured_salary = 0.0
            bvg_employer_amt = BVG_rate_employer * insured_salary
        else:
            bvg_employer_amt = 0.0
        employer_contrib_total = ahv_employer_amt + alv_employer_amt + bvg_employer_amt

        # Employee contributions
        ahv_employee_amt = AHV_employee * salary
        if salary <= ALV_ceiling:
            alv_employee_amt = ALV_employee * salary
        else:
            alv_employee_amt = ALV_employee * ALV_ceiling + 0.005 * max(0.0, salary - ALV_ceiling)
        if salary >= BVG_entry_threshold:
            insured_salary = min(salary, BVG_max_insured) - BVG_coord_deduction
            if insured_salary < 0:
                insured_salary = 0.0
            bvg_employee_amt = BVG_rate_employee * insured_salary
        else:
            bvg_employee_amt = 0.0
        employee_contrib_total = ahv_employee_amt + alv_employee_amt + bvg_employee_amt
    else:
        # If not subject to AHV, no contributions
        ahv_employer_amt = alv_employer_amt = bvg_employer_amt = 0.0
        ahv_employee_amt = alv_employee_amt = bvg_employee_amt = 0.0
        employer_contrib_total = 0.0
        employee_contrib_total = 0.0

    # Company profit remaining after paying salary and employer contributions
    profit_after_salary = profit - salary - employer_contrib_total
    if profit_after_salary < 0:
        profit_after_salary = 0.0  # profit can't go negative (if salary exceeds profit, assume company makes zero profit)

    # Corporate tax in salary scenario
    corp_tax_salary_scenario = profit_after_salary * total_corp_tax_rate

    # Owner's personal taxable income in salary scenario = salary + other_income (dividends = 0 in this scenario)
    personal_taxable_A = salary + other_income
    # Personal income taxes (federal + cantonal+communal)
    federal_tax_A = calc_federal_income_tax(personal_taxable_A)
    cantonal_communal_tax_A = calc_cantonal_income_tax(personal_taxable_A, canton, commune)
    income_tax_A = federal_tax_A + cantonal_communal_tax_A

    # Net payout to owner in scenario A:
    # Owner received salary, but employee contributions were deducted, and then pays income tax.
    net_salary_after_contrib = salary - employee_contrib_total  # take-home pay before income tax
    net_payout_A = net_salary_after_contrib - income_tax_A

    # SCENARIO B: Dividend
    # Company profit (no salary) = full profit
    profit_no_salary = profit
    # Corporate tax in dividend scenario (on full profit)
    corp_tax_div_scenario = profit_no_salary * total_corp_tax_rate
    # Profit after corporate tax = amount available for dividends
    profit_after_corp = profit_no_salary - corp_tax_div_scenario
    if profit_after_corp < 0:
        profit_after_corp = 0.0

    # Determine dividend distribution (if desired_income given, don't exceed that; otherwise use full profit_after_corp)
    if desired_income is None:
        dividend_distribution = profit_after_corp
    else:
        # If user specified a desired payout, limit the dividend to that amount (or the available profit after tax if smaller)
        dividend_distribution = min(desired_income, profit_after_corp)

    # Personal taxable income from dividend = partial inclusion of the dividend + other income
    taxable_div_income = div_inclusion_pct * dividend_distribution
    personal_taxable_B = taxable_div_income + other_income
    # Personal income taxes in scenario B
    federal_tax_B = calc_federal_income_tax(personal_taxable_B)
    cantonal_communal_tax_B = calc_cantonal_income_tax(personal_taxable_B, canton, commune)
    income_tax_B = federal_tax_B + cantonal_communal_tax_B

    # Net payout to owner in scenario B = dividend received - income taxes on it 
    # (No social contributions on dividends)
    net_payout_B = dividend_distribution - income_tax_B

    # Display Results
    st.subheader("Scenario A: Salary Payout")
    st.markdown(f"- **Gross Salary Paid to Owner:** CHF {salary:,.0f}")
    if ahv_subject == "Yes":
        st.markdown(f"  - Employer AHV/ALV/BVG contributions (company cost): CHF {employer_contrib_total:,.0f}")
        st.markdown(f"  - Employee AHV/ALV/BVG contributions (deducted from salary): CHF {employee_contrib_total:,.0f}")
    else:
        st.markdown("  - No AHV/ALV/BVG contributions (owner not subject to social insurances).")
    st.markdown(f"  - **Company profit after salary** (taxable at corporate rate): CHF {profit_after_salary:,.0f}")
    st.markdown(f"  - Corporate taxes on profit: CHF {corp_tax_salary_scenario:,.0f}")
    st.markdown(f"  - Personal income tax on salary + other income: CHF {income_tax_A:,.0f}")
    st.markdown(f"  - **Net payout to owner (after all taxes & contributions):** CHF {net_payout_A:,.0f}")

    st.subheader("Scenario B: Dividend Payout")
    st.markdown(f"- **Dividend Distributed to Owner:** CHF {dividend_distribution:,.0f}")
    st.markdown(f"  - Corporate taxes on profit: CHF {corp_tax_div_scenario:,.0f}")
    st.markdown(f"  - Personal income tax on dividend + other income (after {int(div_inclusion_pct*100)}% inclusion): CHF {income_tax_B:,.0f}")
    st.markdown(f"  - **Net payout to owner (after all taxes):** CHF {net_payout_B:,.0f}")

    # Conclusion: which scenario is better
    st.markdown("**Result:** ", unsafe_allow_html=True)
    if net_payout_A > net_payout_B:
        diff = net_payout_A - net_payout_B
        st.success(f"Paying a salary yields **CHF {diff:,.0f}** more net income to the owner than dividends.")
    elif net_payout_B > net_payout_A:
        diff = net_payout_B - net_payout_A
        st.success(f"Paying out dividends yields **CHF {diff:,.0f}** more net income to the owner than a salary.")
    else:
        st.info("Both options result in the same net income to the owner.")
else:
    st.info("Please enter a company profit above 0 to calculate the scenarios.")
