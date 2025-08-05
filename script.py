import pandas as pd

FILE = "https://github.com/rinor-ab/lohn-oder-dividende/blob/6be7db40684fa9feab84356a0ffe9bc80c4c28ce/MASTER.xlsx"

# --- 2.1 Steuerfüsse -------------------------------------------------
mult = (
    pd.read_excel(FILE, "Steuerfüsse", header=2)          # skip two header rows
      .rename(columns={
          mult.columns[0]: "CantonNumber",
          mult.columns[1]: "CantonCode",
          mult.columns[2]: "CommuneID",
          mult.columns[3]: "CommuneName",
          mult.columns[4]: "IncomeKantonMult",
          mult.columns[5]: "IncomeCommuneMult",
          mult.columns[8]: "CorpProfitKantonMult",
          mult.columns[9]: "CorpProfitCommuneMult",
      })
      .loc[lambda d: d["CommuneID"].notna()]
)

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
