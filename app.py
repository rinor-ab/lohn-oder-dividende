# app.py — Lohn vs. Dividende (devbrains 2025 + DEDUCTIONS; Ø church; strict; BVG via age)
import json, math, pathlib
import streamlit as st
from functools import lru_cache
from collections import defaultdict

# ================= Paths / Year =================
BASE_DIR = pathlib.Path(__file__).parent
PARSED_DIR = BASE_DIR / "data" / "parsed"
YEAR = "2025"  # adapt later if you add a year selector

# ================= Constants ===================
CHURCH_AVG_RATE = 0.12          # Ø Kirchensteuer-Zuschlag (12%)
AHV_ON_DEFAULT = True           # AHV/ALV/BVG standardmäßig an
DIV_PARTIAL_FED = 0.70          # vereinfachte Teilbesteuerung (ab 10% Beteiligung)
DIV_PARTIAL_CANT = 0.70

# Social security (kept from your config)
AHV_employer   = 0.053
AHV_employee   = 0.053
ALV_employer   = 0.011
ALV_employee   = 0.011
ALV_ceiling    = 148_200.0
BVG_rates = {"25-34": 0.07, "35-44": 0.10, "45-54": 0.15, "55-65": 0.18}
BVG_entry_threshold = 22_680.0
BVG_coord_deduction = 26_460.0
BVG_max_insured     = 90_720.0

# ================= Helpers =====================
def clamp(x): return max(0.0, float(x or 0.0))
def bvg_insured_part(salary): return max(0.0, min(salary, BVG_max_insured) - BVG_coord_deduction)
def age_to_band(age:int)->str:
    a = int(age or 35)
    if a<35: return "25-34"
    if a<45: return "35-44"
    if a<55: return "45-54"
    return "55-65"

@lru_cache(None)
def _load_json(p: pathlib.Path):
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

# ================= Load devbrains data =================
def load_locations():
    return _load_json(PARSED_DIR / YEAR / "locations.json")

def load_factors(canton_id: int):
    return _load_json(PARSED_DIR / YEAR / "factors" / f"{canton_id}.json")

def load_tarifs(canton_id: int):
    return _load_json(PARSED_DIR / YEAR / "tarifs" / f"{canton_id}.json")

def load_tarifs_federal_if_any():
    p = PARSED_DIR / YEAR / "tarifs" / "0.json"
    return _load_json(p) if p.exists() else []

def load_deductions(canton_id: int):
    """
    Returns tuple (bund_groups, kanton_groups)
    bund_groups: from deductions/0.json (if exists)
    kanton_groups: from deductions/{CantonID}.json (if exists)
    Each is a list of groups; a group = {'type','target','items':[...]}.
    """
    bund_path = PARSED_DIR / YEAR / "deductions" / "0.json"
    kant_path = PARSED_DIR / YEAR / "deductions" / f"{canton_id}.json"
    bund = _load_json(bund_path) if bund_path.exists() else []
    kant = _load_json(kant_path) if kant_path.exists() else []
    # Keep only income-tax groups (EINKOMMENSSTEUER)
    bund = [g for g in bund if g.get("type")=="EINKOMMENSSTEUER"]
    kant = [g for g in kant if g.get("type")=="EINKOMMENSSTEUER"]
    return bund, kant

# ================= Tariff evaluation ==================
def _find_table(tarifs, tax_type: str, group: str = "ALLE"):
    cand = [t for t in tarifs if t.get("taxType")==tax_type and (t.get("group","")==group)]
    if cand: return cand[0]
    for t in tarifs:
        if t.get("taxType")==tax_type: return t
    return None

def _eval_step_table(table_rows, amount: float) -> float:
    rem = clamp(amount)
    tax = 0.0
    for row in table_rows:
        pct = float(row.get("percent", 0.0)) / 100.0
        width = float(row.get("amount", 0.0))
        if rem <= 0: break
        use = rem if width<=0 else min(rem, width)
        tax += use * pct
        rem -= use
    return tax

def eval_tariff(tariff_obj, amount: float, force_split_factor:int = 1) -> float:
    if not tariff_obj: return 0.0
    table_type = (tariff_obj.get("tableType") or "").upper()
    split = 1 if force_split_factor==1 else max(1, force_split_factor)
    base = clamp(amount / split)

    rows = tariff_obj.get("table") or []
    if table_type == "FLATTAX":
        pct = float(rows[0].get("percent", 0.0))/100.0 if rows else 0.0
        return base * pct * split

    # default: step table
    tax = _eval_step_table(rows, base)
    return tax * split

# ================= Factors lookup =====================
def factors_for_bfs(canton_id: int, bfs_id: int):
    arr = load_factors(canton_id)
    for rec in arr:
        loc = rec.get("Location") or {}
        if int(loc.get("BfsID", -1)) == int(bfs_id):
            return {
                "IncomeRateCanton": float(rec.get("IncomeRateCanton", 100.0)),
                "IncomeRateCity":   float(rec.get("IncomeRateCity",   0.0)),
                "ProfitTaxRateCanton": float(rec.get("ProfitTaxRateCanton", 100.0)),
                "ProfitTaxRateCity":   float(rec.get("ProfitTaxRateCity",   0.0)),
            }
    return {"IncomeRateCanton":100.0,"IncomeRateCity":0.0,"ProfitTaxRateCanton":100.0,"ProfitTaxRateCity":0.0}

# ================= Deductions engine ==================
def flatten_deduction_items(groups):
    """Return a list of (target, item) from deduction groups; each item has id/min/max/format/amount/percent/name{de,...}."""
    out = []
    for g in groups:
        tgt = g.get("target","")
        for it in (g.get("items") or []):
            out.append((tgt, it))
    return out

def parse_flags(fmt: str):
    flags = set((fmt or "").upper().split(",")) if fmt else set()
    return {f.strip() for f in flags if f.strip()}

def compute_deductions_total(base_amount: float, items_with_scope, ui_values: dict) -> float:
    """
    Generic computation:
      deduction = (user_amount or item.amount) + base_amount * (item.percent/100)
      then apply MINIMUM / MAXIMUM caps if specified.
    Clamped to [0, base_amount].
    """
    base = clamp(base_amount)
    total = 0.0
    for scope, item in items_with_scope:
        fmt = parse_flags(item.get("format", ""))
        minimum = float(item.get("minimum", 0) or 0.0)
        maximum = float(item.get("maximum", 0) or 0.0)
        percent = float(item.get("percent", 0) or 0.0)/100.0
        default_amt = float(item.get("amount", 0) or 0.0)

        # user override per item id (same id can appear in both layers; scope key separates UI fields)
        key = f"{scope}:{item.get('id','')}"
        user_amt = float(ui_values.get(key, 0.0) or 0.0)

        raw = user_amt if user_amt>0 else default_amt
        raw += base * percent

        # apply flags
        val = raw
        if "MINIMUM" in fmt:
            val = max(val, minimum)
        if "MAXIMUM" in fmt and maximum>0:
            val = min(val, maximum)

        val = clamp(min(val, base))  # deduction cannot exceed base
        total += val
    return min(total, base)

# ================= Personal tax wrappers =================
def income_components_with_bases(taxable_fed: float, taxable_cant: float, canton_id: int, bfs_id: int):
    """
    Returns (fed_tax, cant_commune_church_tax, total)
    Fed: tarifs/0.json (if present). Cant: tarifs/{CantonID}.json with factors * (1 + church).
    """
    t_fed = clamp(taxable_fed)
    t_cant = clamp(taxable_cant)

    # Federal
    fed_tarifs = load_tarifs_federal_if_any()
    fed_table = _find_table(fed_tarifs, "EINKOMMENSSTEUER", "ALLE")
    fed_tax = eval_tariff(fed_table, t_fed, force_split_factor=1) if fed_table else 0.0

    # Cantonal + municipal + church Ø
    cant_tarifs = load_tarifs(canton_id)
    cant_table = _find_table(cant_tarifs, "EINKOMMENSSTEUER", "ALLE")
    base_cant = eval_tariff(cant_table, t_cant, force_split_factor=1) if cant_table else 0.0

    fac = factors_for_bfs(canton_id, bfs_id)
    mult = (fac["IncomeRateCanton"] + fac["IncomeRateCity"]) / 100.0
    cant_city = base_cant * mult
    cant_city_church = cant_city * (1.0 + CHURCH_AVG_RATE)

    return fed_tax, cant_city_church, (fed_tax + cant_city_church)

def corporate_tax_rate(canton_id: int, bfs_id: int) -> float:
    """
    Corporate tax effective RATE:
      rate = FLATTAX% * (ProfitTaxRateCanton + ProfitTaxRateCity)/100
    """
    tarifs = load_tarifs(canton_id)
    corp = _find_table(tarifs, "GEWINNSTEUER")
    if not corp:
        return 0.10
    rows = corp.get("table") or []
    base_pct = float(rows[0].get("percent", 0.0))/100.0 if rows else 0.0
    fac = factors_for_bfs(canton_id, bfs_id)
    mult = (fac["ProfitTaxRateCanton"] + fac["ProfitTaxRateCity"]) / 100.0
    return base_pct * mult

def qualifies_partial(share_pct): return (share_pct or 0.0) >= 10.0

# ================= Payroll blocks =======================
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

# ================= UI: Locations ========================
LOC = load_locations()
by_canton = defaultdict(list)  # Canton -> [(name, bfs, canton_id)]
for r in LOC:
    by_canton[r["Canton"]].append((r["BfsName"], int(r["BfsID"]), int(r["CantonID"])))
for k in list(by_canton.keys()):
    by_canton[k].sort(key=lambda x: x[0])

# ================= Streamlit UI =========================
st.title("Lohn vs. Dividende")
st.caption("Devbrains 2025 (locations + factors + tarifs + deductions). Ø Kirchensteuer; KER/Vermögenssteuer nicht berücksichtigt.")

col1, col2 = st.columns(2)
with col1:
    profit         = st.number_input("Firmengewinn **vor Lohn** [CHF]", 0.0, step=10_000.0)
    desired_income = st.number_input("Gewünschte **Gesamtauszahlung** an Inhaber [CHF] (optional)", 0.0, step=10_000.0)
    age_input      = st.number_input("Alter (für BVG-Altersband)", min_value=18, max_value=70, value=40, step=1)
with col2:
    canton_abbr = st.selectbox("Kanton", sorted(by_canton.keys()))
    commune_label = st.selectbox("Gemeinde", [n for (n, _b, _id) in by_canton[canton_abbr]])
    bfs_id, canton_id = next((b, cid) for (n, b, cid) in by_canton[canton_abbr] if n==commune_label)
    other_inc = st.number_input("Weitere steuerbare Einkünfte [CHF]", 0.0, step=10_000.0)
    debug_mode = st.checkbox("Debug-Informationen anzeigen", value=False)

st.markdown("### Annahmen")
col3, col4 = st.columns(2)
with col3:
    min_salary  = st.number_input("Marktüblicher Mindestlohn [CHF]", 0.0, step=10_000.0, value=120_000.0)
    share_pct   = st.number_input("Beteiligungsquote [%] (Teilbesteuerung ab 10 %)", 0.0, 100.0, 100.0, step=5.0)
with col4:
    fak_rate    = st.number_input("FAK (nur Arbeitgeber) [%]", 0.0, 5.0, 1.5, step=0.1)/100.0
    uvg_rate    = st.number_input("UVG/KTG (Arbeitgeber) [%]", 0.0, 5.0, 1.0, step=0.1)/100.0
    pk_buyin    = st.number_input("PK-Einkauf (privat) [CHF]", 0.0, step=1.0)

optimizer_on = st.checkbox("Optimierer – beste Mischung (Lohn + Dividende) finden", value=True)

# Normalize desired payout
if desired_income == 0:
    desired_income = None
elif desired_income > profit:
    desired_income = profit

# =================== ANNAHMEN (collapsible) ===================
# Dynamic DEDUCTIONS UI (optional) + footnotes
bund_groups, kant_groups = load_deductions(canton_id)
bund_items = flatten_deduction_items(bund_groups)
kant_items = flatten_deduction_items(kant_groups)

with st.expander("ANNAHMEN", expanded=False):
    st.markdown(
        f"- **Kirchensteuer:** Ø-Zuschlag von **{int(CHURCH_AVG_RATE*100)}%** auf die kant./gemeindl. Steuer.\n"
        f"- **AHV/ALV/BVG:** Standardmäßig **angewendet** (AG- & AN-Anteile berechnet).\n"
        f"- **Regelmodus:** **Strikt** – Dividenden erst zulässig, wenn der Lohn ≥ Mindestlohn ist.\n"
        f"- **BVG-Altersband:** Automatische Zuordnung anhand des Alters.\n"
        f"- **PK-Einkauf:** Reduziert das steuerbare Einkommen (Sperrfristen beachten).\n"
        f"- **Daten:** `locations.json` → BFS/CantonID; `factors/{{CantonID}}.json` → Multiplikatoren; "
        f"`tarifs/{{CantonID}}.json` → Tariftabellen; `deductions/{{CantonID}}.json`/`deductions/0.json` → Abzüge.\n"
        f"- **Nicht berücksichtigt:** KER, Vermögenssteuer-Impact."
    )
    st.markdown("---")
    use_deds = st.checkbox("Steuerabzüge anwenden (optional)", value=True,
                            help="Erlaubt die Eingabe von Beträgen für einzelne Abzugspositionen. "
                                 "Prozentsätze, Minimal-/Maximalgrenzen werden automatisch berücksichtigt.")
    ded_inputs = {}
    if use_deds:
        st.markdown("**Abzüge – Bund (tarifs/0.json, deductions/0.json)**")
        for scope, it in bund_items:
            name = (it.get("name") or {}).get("de", it.get("id",""))
            minv = it.get("minimum",0); maxv=it.get("maximum",0); pct=it.get("percent",0)
            label = f"{name} — min {minv} / max {maxv} / {pct}%"
            key = f"ded_BUND_{it.get('id','')}"
            ded_inputs[f"BUND:{it.get('id','')}"] = st.number_input(label, min_value=0.0, step=100.0, value=0.0, key=key)

        st.markdown("**Abzüge – Kanton/Gemeinde (deductions/{CantonID}.json)**")
        for scope, it in kant_items:
            name = (it.get("name") or {}).get("de", it.get("id",""))
            minv = it.get("minimum",0); maxv=it.get("maximum",0); pct=it.get("percent",0)
            label = f"{name} — min {minv} / max {maxv} / {pct}%"
            key = f"ded_KANTON_{it.get('id','')}"
            ded_inputs[f"{scope}:{it.get('id','')}"] = st.number_input(label, min_value=0.0, step=100.0, value=0.0, key=key)

# =================== Core engines ============================
def cantonal_income_tax_devbrains(taxable, canton_id, bfs_id):
    tarifs = load_tarifs(canton_id)
    table = _find_table(tarifs, "EINKOMMENSSTEUER", "ALLE")
    base = eval_tariff(table, clamp(taxable), 1) if table else 0.0
    fac = factors_for_bfs(canton_id, bfs_id)
    mult = (fac["IncomeRateCanton"] + fac["IncomeRateCity"])/100.0
    return base * mult * (1.0 + CHURCH_AVG_RATE)

def corp_tax_rate_devbrains(canton_id, bfs_id):
    return corporate_tax_rate(canton_id, bfs_id)

# =================== Scenarios ==============================
def scenario_salary_only(profit, desired, canton_id, bfs_id, age_key, ahv_on, other, pk_buy):
    salary = profit if desired is None else min(profit, desired)
    ag = employer_costs(salary, age_key, ahv_on, fak=fak_rate, uvg=uvg_rate)
    an = employee_deductions(salary, age_key, ahv_on)

    rest = profit - salary - ag["total"]
    if rest < 0:
        st.warning("Bruttolohn inkl. Arbeitgeberabgaben > Gewinn – Restgewinn wird auf 0 gesetzt.")
    rest = max(0.0, rest)

    corp_rate = corp_tax_rate_devbrains(canton_id, bfs_id)
    corp_tax_amt = rest * corp_rate

    # Base pre-deduction
    base = clamp(salary - an["total"] + other - pk_buy)

    # Apply deductions (separately for federal and canton)
    if bund_items or kant_items:
        if 'use_deds' in locals() or True:
            fed_items = bund_items
            cant_items_scoped = kant_items
            fed_ui = {k.replace("BUND:","BUND:"):v for k,v in (ded_inputs or {}).items()}
            cant_ui = {k:v for k,v in (ded_inputs or {}).items() if not k.startswith("BUND:")}
            fed_ded = compute_deductions_total(base, fed_items, fed_ui) if (use_deds and fed_items) else compute_deductions_total(base, fed_items, {})
            cant_ded= compute_deductions_total(base, cant_items_scoped, cant_ui) if (use_deds and cant_items_scoped) else compute_deductions_total(base, cant_items_scoped, {})
        else:
            fed_ded=cant_ded=0.0
    else:
        fed_ded=cant_ded=0.0

    taxable_fed  = clamp(base - fed_ded)
    taxable_cant = clamp(base - cant_ded)

    fed_tax, cant_tax, total_tax = income_components_with_bases(taxable_fed, taxable_cant, canton_id, bfs_id)

    net_owner = salary - an["total"] - total_tax

    return {
        "salary": salary, "dividend": 0.0,
        "corp_tax": corp_tax_amt, "income_tax": total_tax,
        "net": net_owner, "adjusted_net": net_owner,
        "retained_after_tax": max(0.0, rest - corp_tax_amt),
        "blocks": dict(ag=ag, an=an, fed=fed_tax, cant=cant_tax,
                       ded_fed=fed_ded, ded_cant=cant_ded)
    }

def scenario_dividend(profit, desired, canton_id, bfs_id, age_key, ahv_on, other, pk_buy,
                      min_salary, share_pct):
    # Strict: salary ≥ min_salary before dividends
    salary = min(min_salary, profit if desired is None else min(profit, desired))
    ag = employer_costs(salary, age_key, ahv_on, fak=fak_rate, uvg=uvg_rate)
    an = employee_deductions(salary, age_key, ahv_on)

    rest = clamp(profit - salary - ag["total"])
    corp_rate = corp_tax_rate_devbrains(canton_id, bfs_id)
    corp_tax_amt = rest * corp_rate
    after_corp = rest - corp_tax_amt

    desired_left = None if desired is None else clamp(desired - salary)
    gross_div = after_corp if desired_left is None else min(after_corp, desired_left)

    dividend = gross_div if salary >= min_salary else 0.0
    if salary < min_salary and gross_div > 0:
        st.info("Dividende nicht zulässig, da Lohn < Mindestlohn (Strikt-Modus). Ausschüttung = 0.")

    qualifies = qualifies_partial(share_pct)
    inc_fed = DIV_PARTIAL_FED if qualifies else 1.0
    inc_cant = DIV_PARTIAL_CANT if qualifies else 1.0

    taxable_salary = clamp(salary - an["total"])
    base_fed  = clamp(taxable_salary + dividend*inc_fed  + other - pk_buy)
    base_cant = clamp(taxable_salary + dividend*inc_cant + other - pk_buy)

    # Apply deductions on respective bases
    if bund_items or kant_items:
        fed_ui = {k:v for k,v in (ded_inputs or {}).items() if k.startswith("BUND:")}
        cant_ui = {k:v for k,v in (ded_inputs or {}).items() if not k.startswith("BUND:")}
        fed_ded = compute_deductions_total(base_fed, bund_items, fed_ui) if (use_deds and bund_items) else compute_deductions_total(base_fed, bund_items, {})
        cant_ded= compute_deductions_total(base_cant, kant_items, cant_ui) if (use_deds and kant_items) else compute_deductions_total(base_cant, kant_items, {})
    else:
        fed_ded=cant_ded=0.0

    taxable_fed  = clamp(base_fed - fed_ded)
    taxable_cant = clamp(base_cant - cant_ded)

    fed_tax, _, _ = income_components_with_bases(taxable_fed, taxable_cant=0.0, canton_id=canton_id, bfs_id=bfs_id)
    _, cant_tax, _ = income_components_with_bases(taxable_fed=0.0, taxable_cant=taxable_cant, canton_id=canton_id, bfs_id=bfs_id)
    total_tax = fed_tax + cant_tax

    net_owner = (salary - an["total"]) + dividend - total_tax

    return {
        "salary": salary, "dividend": dividend,
        "corp_tax": corp_tax_amt, "income_tax": total_tax,
        "net": net_owner, "adjusted_net": net_owner,
        "retained_after_tax": clamp(rest - corp_tax_amt - dividend),
        "blocks": dict(ag=ag, an=an, inc_fed=inc_fed, inc_cant=inc_cant,
                       ded_fed=fed_ded, ded_cant=cant_ded)
    }

def optimize_mix(step=1000.0):
    age_key = age_to_band(age_input)
    qualifies = qualifies_partial(share_pct)
    inc_fed = DIV_PARTIAL_FED if qualifies else 1.0
    inc_cant = DIV_PARTIAL_CANT if qualifies else 1.0

    best = None
    s = 0.0
    corp_rate = corp_tax_rate_devbrains(canton_id, bfs_id)
    while s <= (profit if desired_income is None else min(profit, desired_income)) + 1e-6:
        ag = employer_costs(s, age_key, AHV_ON_DEFAULT, fak=fak_rate, uvg=uvg_rate)
        an = employee_deductions(s, age_key, AHV_ON_DEFAULT)

        rest = clamp(profit - s - ag["total"])
        corp_tax_amt = rest * corp_rate
        after_corp = rest - corp_tax_amt

        desired_left = None if desired_income is None else clamp(desired_income - s)
        pre_div = after_corp if desired_left is None else min(after_corp, desired_left)
        dividend = pre_div if s >= min_salary else 0.0

        taxable_salary = clamp(s - an["total"])
        base_fed  = clamp(taxable_salary + dividend*inc_fed  + other_inc - pk_buyin)
        base_cant = clamp(taxable_salary + dividend*inc_cant + other_inc - pk_buyin)

        # deductions
        fed_ui = {k:v for k,v in (ded_inputs or {}).items() if k.startswith("BUND:")}
        cant_ui= {k:v for k,v in (ded_inputs or {}).items() if not k.startswith("BUND:")}
        fed_ded = compute_deductions_total(base_fed, bund_items, fed_ui) if (use_deds and bund_items) else compute_deductions_total(base_fed, bund_items, {})
        cant_ded= compute_deductions_total(base_cant, kant_items, cant_ui) if (use_deds and kant_items) else compute_deductions_total(base_cant, kant_items, {})

        taxable_fed  = clamp(base_fed  - fed_ded)
        taxable_cant = clamp(base_cant - cant_ded)

        fed_tax, _, _  = income_components_with_bases(taxable_fed, 0.0, canton_id, bfs_id)
        _, cant_tax, _ = income_components_with_bases(0.0, taxable_cant, canton_id, bfs_id)
        income_tax = fed_tax + cant_tax

        net_owner = (s - an["total"]) + dividend - income_tax

        res = {"salary": s, "dividend": dividend, "net": net_owner, "adjusted_net": net_owner,
               "income_tax": income_tax, "corp_tax": corp_tax_amt,
               "retained_after_tax": clamp(rest - corp_tax_amt - dividend)}
        if (best is None) or (net_owner > best["adjusted_net"]):
            best = res
        s += step
    return best

# =================== Run & Render ===========================
if profit > 0:
    age_key = age_to_band(age_input)

    A = scenario_salary_only(profit, desired_income, canton_id, bfs_id, age_key, AHV_ON_DEFAULT, other_inc, pk_buyin)
    B = scenario_dividend(profit, desired_income, canton_id, bfs_id, age_key, AHV_ON_DEFAULT, other_inc, pk_buyin,
                          min_salary, share_pct)

    # ----- Display A -----
    st.subheader("Szenario A – 100% Lohn")
    st.write(f"Bruttolohn: **CHF {A['salary']:,.0f}**")
    st.write(f"AG AHV/ALV/BVG: CHF {(A['blocks']['ag']['ahv']+A['blocks']['ag']['alv']+A['blocks']['ag']['bvg']):,.0f}")
    st.write(f"AG FAK/UVG/KTG: CHF {A['blocks']['ag']['extra']:,.0f}")
    st.write(f"AN AHV/ALV/BVG (abzugsfähig): CHF {A['blocks']['an']['total']:,.0f}")
    st.write(f"Körperschaftssteuer Restgewinn: CHF {A['corp_tax']:,.0f}")
    st.write(f"Einkommenssteuer (Bund + Kant./Gem. + Kirche Ø): CHF {A['income_tax']:,.0f}")
    # Optional: show total applied deductions
    if (A["blocks"].get("ded_fed",0) or A["blocks"].get("ded_cant",0)):
        st.caption(f"Berücksichtigte Abzüge – Bund: CHF {A['blocks']['ded_fed']:,.0f}, Kanton/Gemeinde: CHF {A['blocks']['ded_cant']:,.0f}")
    st.write(f"Nachsteuerlicher Gewinn einbehalten: CHF {A['retained_after_tax']:,.0f}")
    st.success(f"**Netto an Inhaber (heute):** CHF {A['adjusted_net']:,.0f}")

    # ----- Display B -----
    st.subheader("Szenario B – Lohn + Dividende")
    st.write(f"Bruttolohn: **CHF {B['salary']:,.0f}** | Dividende gesamt: **CHF {B['dividend']:,.0f}**")
    st.write(f"Körperschaftssteuer (nach Lohn): CHF {B['corp_tax']:,.0f}")
    st.write(f"Einkommenssteuer (Bund + Kant./Gem. + Kirche Ø): CHF {B['income_tax']:,.0f}")
    if (B["blocks"].get("ded_fed",0) or B["blocks"].get("ded_cant",0)):
        st.caption(f"Berücksichtigte Abzüge – Bund: CHF {B['blocks']['ded_fed']:,.0f}, Kanton/Gemeinde: CHF {B['blocks']['ded_cant']:,.0f}")
    st.write(f"Nachsteuerlicher Gewinn einbehalten: CHF {B['retained_after_tax']:,.0f}")
    st.caption(f"Teilbesteuerung Dividenden (vereinfachte Annahme): Bund {int((DIV_PARTIAL_FED if qualifies_partial(share_pct) else 1.0)*100)}%, "
               f"Kanton {int((DIV_PARTIAL_CANT if qualifies_partial(share_pct) else 1.0)*100)}% (ab 10% Beteiligung).")
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
        st.write(f"Einkommenssteuer gesamt (Bund + Kant./Gem. + Kirche Ø): CHF {best['income_tax']:,.0f}")
        st.write(f"Körperschaftssteuer: CHF {best['corp_tax']:,.0f}")
        st.write(f"Nachsteuerlich einbehalten: CHF {best['retained_after_tax']:,.0f}")
        st.success(f"**Max. Netto an Inhaber (heute):** CHF {best['adjusted_net']:,.0f}")

    # ----- Debug -----
    if debug_mode:
        st.markdown("---")
        st.subheader("Debug-Informationen")
        fac = factors_for_bfs(canton_id, bfs_id)
        st.write(f"BFS: {bfs_id} | Kanton: {canton_abbr} | CantonID: {canton_id} | Gemeinde: {commune_label}")
        st.write(f"Faktoren Einkommen: Kanton {fac['IncomeRateCanton']}% + Gemeinde {fac['IncomeRateCity']}%")
        st.write(f"Faktoren Gewinn:   Kanton {fac['ProfitTaxRateCanton']}% + Gemeinde {fac['ProfitTaxRateCity']}%")
        cr = corporate_tax_rate(canton_id, bfs_id)
        st.write(f"Körperschaftssteuer-Satz gesamt (effektiv): {cr:.2%}")
        fed_present = any(t.get('taxType')=='EINKOMMENSSTEUER' for t in load_tarifs_federal_if_any())
        st.write(f"Bundestarife vorhanden: {'Ja' if fed_present else 'Nein'} (tarifs/0.json)")
else:
    st.warning("Bitte Gewinn > 0 eingeben, um die Berechnung zu starten.")
