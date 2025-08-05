import pandas as pd

FILE = "https://github.com/rinor-ab/lohn-oder-dividende/blob/6be7db40684fa9feab84356a0ffe9bc80c4c28ce/MASTER.xlsx"

# --- 2.1 Steuerfüsse -------------------------------------------------
# read the table first so we can reference its original column names
mult = pd.read_excel(FILE, "Steuerfüsse", header=2)  # skip two header rows
mult = mult.rename(columns={
    mult.columns[0]: "CantonNumber",
    mult.columns[1]: "CantonCode",
    mult.columns[2]: "CommuneID",
    mult.columns[3]: "CommuneName",
    mult.columns[4]: "IncomeKantonMult",
    mult.columns[5]: "IncomeCommuneMult",
    mult.columns[8]: "CorpProfitKantonMult",
    mult.columns[9]: "CorpProfitCommuneMult",
})
mult = mult.loc[mult["CommuneID"].notna()]

# convert 98 → 0.98
pct_cols = ["IncomeKantonMult","IncomeCommuneMult",
            "CorpProfitKantonMult","CorpProfitCommuneMult"]
mult[pct_cols] = mult[pct_cols].div(100)

# --- 2.2 Cantonal & federal brackets --------------------------------
cant_brackets = pd.read_excel(FILE, "Income Tax_Cantons")
fed_brackets  = pd.read_excel(FILE, "Income Tax_Confederation")

# normalise into numeric columns: Lower, Upper, BaseCHF, MargRate
def tidy_brackets(df, canton):
    df = df[df["Canton"]==canton].copy()
    df = df.rename(columns={
        df.columns[5]:"Upper",
        df.columns[6]:"MargRate"
    })
    df["Lower"] = df["Upper"].shift(fill_value=0)
    df["BaseCHF"] = df["Base amount CHF"].fillna(0)
    return df[["Lower","Upper","BaseCHF","MargRate"]]
    
# pre-build a dict of DataFrames keyed by canton code
cant_tables = {
    c: tidy_brackets(cant_brackets, c) 
    for c in cant_brackets["Canton"].unique()
}

fed_table = tidy_brackets(fed_brackets, "Confederation")

# --- 2.3 Corporate rates & dividend partial tax ----------------------
corp_rate = (
    pd.read_excel(FILE, "Corporate Income Tax")[["Canton / Confederation",
                                                "Proportional Income Tax Percentage"]]
      .rename(columns={"Canton / Confederation":"CantonCode",
                       "Proportional Income Tax Percentage":"EffCorpRate"})
      .set_index("CantonCode")["EffCorpRate"]   # Series for fast lookup
)

partial_div = (
    pd.read_excel(FILE, "Teilbesteuerung Dividenden")
      .set_index("Kanton")["Teilbesteuerung der Einkünfte aus Beteiligungen des Geschäftsvermögens"]
)

# --- 2.4 Social constants -------------------------------------------
soc = (
    pd.read_excel(FILE,"Social Security Contributions")
      .set_index("ParameterKey")["Value"].to_dict()
)


# 3.1  federal + cantonal progressive tax
def progressive_tax(income, table):
    row = table.loc[table["Upper"].ge(income)].iloc[0]
    base  = row["BaseCHF"]
    lower = row["Lower"]
    rate  = row["MargRate"] / 100      # convert %
    return base + (income - lower) * rate

def personal_income_tax(income, canton, commune_id):
    # cantonal part
    cant_tax = progressive_tax(income, cant_tables[canton])
    # communal multipliers
    row = mult.loc[mult["CommuneID"]==commune_id].iloc[0]
    cant_mult = row["IncomeKantonMult"]
    comm_mult = row["IncomeCommuneMult"]
    # federal part
    fed_tax = progressive_tax(income, fed_table)
    return fed_tax + cant_tax * cant_mult * comm_mult

# 3.2  corporate tax
def corporate_tax(profit, canton, commune_id):
    kant_rate = corp_rate[canton]
    row = mult.loc[mult["CommuneID"]==commune_id].iloc[0]
    # effective rate proportional: canton part already combined with commune ―
    # if your table holds *only canton part*, multiply by row["CorpProfitCommuneMult"]
    return profit * kant_rate

# 3.3  social security (simplified, employee half / employer half)
AHV = soc["AHV_IV_EO_EmployerShare"]
ALV = soc["ALV_EmployerShare"]
ALV_CEIL = soc["ALV_Ceiling"]

def social(gross, age):
    ee_rate = AHV + ALV
    er_rate = AHV + ALV
    if gross > ALV_CEIL:   # solidarity piece left out for legibility
        ee_rate -= ALV     # /!\ if you model solidarity, add 0.005 instead
        er_rate -= ALV
    # BVG
    for (lo,hi), r in {(25,34):0.07,(35,44):0.10,(45,54):0.15,(55,65):0.18}.items():
        if lo<=age<=hi:
            bvg_rate = r ; break
    insured = max(0, min(gross, 90720) - 26460)
    ee_bvg = er_bvg = insured * bvg_rate / 2
    ee = gross*ee_rate + ee_bvg
    er = gross*er_rate + er_bvg
    return ee, er



