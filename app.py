# app.py – Lohn vs. Dividende (devbrains 2025; full canton engine incl. BL fix + plotly chart)
# -----------------------------------------------------------------------------
# Uses the devbrains "parsed/2025" dataset (locations, tarifs, factors) and
# reproduces the tariff engines (ZUERICH, BUND, FREIBURG, FLATTAX, FORMEL)
# with correct splitting + rounding. Church tax uses factors by confession.
# Dividenden-Teilbesteuerung: Bund fix 70%, Kanton dynamisch per JSON.
# -----------------------------------------------------------------------------

import json, math, pathlib, re
import streamlit as st
import matplotlib.pyplot as plt  # (unused but left untouched)
import plotly.graph_objects as go
import streamlit as st

import streamlit as st


# ------------------------- Data roots -------------------------
APP_DIR = pathlib.Path(__file__).parent
CANDIDATE_DATA_ROOTS = [
    APP_DIR / "data" / "parsed" / "2025",
    APP_DIR / "parsed" / "2025",
    APP_DIR / "2025",
    pathlib.Path("/mnt/data/2025"),  # also works when running in notebooks
]
YEAR_ROOT = None
for p in CANDIDATE_DATA_ROOTS:
    if (p / "locations.json").exists():
        YEAR_ROOT = p
        break
if YEAR_ROOT is None:
    st.error("Konnte devbrains-Daten nicht finden. Erwartet: data/parsed/2025/…")
    st.stop()

# ------------------------- Constants --------------------------
RULE_MODE_STRIKT = "Strikt (Dividende nur bei Lohn ≥ Mindestlohn)"
AHV_ON_DEFAULT = True

# devbrains gross->net for *income tax* base (AN-Seite)
AHV_IV_EO = 0.053
ALV      = 0.011
NBU      = 0.004
ALV_NBU_CEILING = 148_200.0

# BVG (nur Anzeige der Kostenblöcke)
BVG_rates = {"25-34": 0.07, "35-44": 0.10, "45-54": 0.15, "55-65": 0.18}
BVG_entry_threshold = 22_680.0
BVG_coord_deduction = 26_460.0
BVG_max_insured     = 90_720.0

# ------------------------- Small helpers ----------------------
def clamp_pos(x):
    try:
        return max(0.0, float(x or 0.0))
    except:
        return 0.0

def age_to_band(age:int)->str:
    a=int(age or 35)
    if a<35: return "25-34"
    if a<45: return "35-44"
    if a<55: return "45-54"
    return "55-65"

def bvg_insured_part(salary):
    return max(0.0, min(salary, BVG_max_insured) - BVG_coord_deduction)

def dinero_round_100_down(x: float) -> float:
    return math.floor((x or 0.0)/100.0)*100.0

# ------------------------- Loaders ----------------------------
@st.cache_data(show_spinner=False)
def load_locations():
    with (YEAR_ROOT / "locations.json").open("r", encoding="utf-8") as f:
        locs=json.load(f)
    by_canton={}
    for r in locs:
        by_canton.setdefault(r["Canton"], []).append(r)
    for k in by_canton:
        by_canton[k].sort(key=lambda x: x["BfsName"])
    return locs, by_canton

@st.cache_data(show_spinner=False)
def load_tarifs(canton_id:int):
    with (YEAR_ROOT / "tarifs" / f"{int(canton_id)}.json").open("r", encoding="utf-8") as f:
        return json.load(f)

@st.cache_data(show_spinner=False)
def load_factors(canton_id:int):
    with (YEAR_ROOT / "factors" / f"{int(canton_id)}.json").open("r", encoding="utf-8") as f:
        return json.load(f)

# --- NEW: dynamic Teilbesteuerung (cantonal) -------------------
@st.cache_data(show_spinner=False)
def load_dividend_inclusion_map():
    """
    Returns map {"ZH": 0.5, "FR": 0.7, ...} with cantonal inclusion factors.
    Searches common locations; defaults handled in incl_rates().
    """
    candidates = []
    for base in [APP_DIR, YEAR_ROOT, APP_DIR / "data", pathlib.Path("/mnt/data")]:
        if not base:
            continue
        candidates += list(base.glob("Teilbesteuerung_Dividenden*.json"))
    for fp in candidates:
        try:
            with fp.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data:
                    return data
        except Exception:
            continue
    return {}

# ------------------------- Tariff engine ----------------------
def pick_income_table(tariffs:list, tax_type="EINKOMMENSSTEUER"):
    if not tariffs: return None
    cands=[t for t in tariffs if (t.get("taxType") or "").upper()==tax_type.upper()]
    if not cands: return None
    for t in cands:
        if (t.get("group") or "").strip().upper()=="ALLE":
            return t
    for t in cands:
        g=(t.get("group") or "").upper()
        if "LEDIG" in g or "ALLEINE" in g:
            return t
    return cands[0]

def eval_zuerich(rows, taxable, split=1):
    base = taxable / max(1, split)
    rem = base; tax = 0.0
    for r in rows:
        width=float(r.get("amount") or 0.0)
        pct=(r.get("percent") or 0.0)/100.0
        if width<=0: continue
        use=min(rem,width)
        tax += use*pct
        rem -= use
        if rem<=0: break
    if rem>0 and rows:
        tax += rem * ((rows[-1].get("percent") or 0.0)/100.0)
    return tax * max(1, split)

def eval_bund(rows, taxable, split=1):
    base = taxable / max(1, split)
    last=None
    for r in rows:
        thr=float(r.get("amount") or 0.0)
        if thr<=base: last=r
        else: break
    if not last: return 0.0
    fixed=float(last.get("taxes") or 0.0)
    thr=float(last.get("amount") or 0.0)
    rate=(last.get("percent") or 0.0)/100.0
    return (fixed + (base - thr)*rate) * max(1, split)

def eval_freiburg(rows, taxable, split=1):
    base = taxable / max(1, split)
    last=None
    for r in rows:
        amount=float(r.get("amount") or 0.0)
        if amount>=base:
            if not last or (last.get("amount") or 0.0)==0: return 0.0
            last_amt=float(last.get("amount") or 0.0)
            last_pct=float(last.get("percent") or 0.0)
            pct_diff = float(r.get("percent") or 0.0) - last_pct
            part_count = amount - last_amt
            part_percentage = (pct_diff / part_count) if part_count>0 else 0.0
            part_diff = base - last_amt
            final_pct = last_pct + part_diff * part_percentage
            return taxable * (final_pct/100.0)
        last=r
    return taxable * ((last.get("percent") or 0.0)/100.0) if last else 0.0

def eval_flattax(rows, taxable, split=1):
    r = rows[0] if rows else {}
    return taxable * ((r.get("percent") or 0.0)/100.0)

def _normalize_formula(expr: str) -> str:
    """Make devbrains 'FORMEL' rows executable in Python (robust for BL)."""
    if not expr: return ""
    s = expr.replace("$wert$", "X")
    s = re.sub(r"\bln\s*\(\s*X\s*\)", "log(X)", s)
    s = re.sub(r"\blog\s*\(\s*X\s*\)", "log(X)", s)
    s = re.sub(r"\blog\s+X\b", "log(X)", s)
    return s

def eval_formel(rows, taxable, split=1):
    base = taxable / max(1, split)
    selected=None
    for r in rows:
        thr=float(r.get("amount") or 0.0)
        frm=(r.get("formula") or "").strip()
        if thr<=base and frm:
            selected=r
        elif thr>base:
            break
    if selected is None:
        for r in reversed(rows):
            if (r.get("formula") or "").strip():
                selected=r
                break
    if selected is None:
        return 0.0
    expr = _normalize_formula(selected["formula"])
    try:
        val = eval(expr, {"__builtins__": {}}, {"log": math.log, "X": base})
        return float(val) * max(1, split)
    except Exception:
        return 0.0

def groups_for_relationship(relationship: str, children: int):
    groups=[]
    if relationship in ("m","rp"):
        groups.append("VERHEIRATET")
    else:
        if (children or 0)>0: groups.append("LEDIG_MIT_KINDER")
        if relationship=="s": groups.append("LEDIG_ALLEINE")
        elif relationship=="c": groups.append("LEDIG_KONKUBINAT")
    if not groups: groups.append("LEDIG_ALLEINE")
    return groups

def group_splitting_eligible(group: str)->bool:
    return group in ("VERHEIRATET","LEDIG_MIT_KINDER")

def pick_tarif(canton_id:int, tax_type:str, groups: list[str]):
    tarifs = load_tarifs(canton_id)
    tt = [t for t in tarifs if (t.get("taxType") or "").upper()==tax_type.upper()]
    if not tt: return None, None
    for grp in groups:
        for t in tt:
            g = t.get("group") or ""
            if g=="ALLE" or grp in g:
                return t, grp
    return tt[0], groups[0]

def eval_tariff_amount(tarif_obj, taxable: float, group: str):
    if not tarif_obj or taxable<=0: return 0.0
    table_type = (tarif_obj.get("tableType") or "").upper()
    # Zurich table sometimes carries base taxes -> treat as BUND
    if table_type=="ZUERICH" and any((row.get("taxes") or 0)>0 for row in (tarif_obj.get("table") or [])):
        table_type="BUND"
    split = int(tarif_obj.get("splitting") or 0)
    split_ok = (split>0 and group_splitting_eligible(group))
    split_val = split if split_ok else 1
    # devbrains: round down after splitting
    taxable_rounded = dinero_round_100_down(taxable / split_val) * split_val
    rows = tarif_obj.get("table") or []
    if table_type=="FLATTAX":  return eval_flattax(rows,   taxable_rounded, split_val)
    if table_type=="ZUERICH":  return eval_zuerich(rows,   taxable_rounded, split_val)
    if table_type=="BUND":     return eval_bund(rows,      taxable_rounded, split_val)
    if table_type=="FREIBURG": return eval_freiburg(rows,  taxable_rounded, split_val)
    if table_type=="FORMEL":   return eval_formel(rows,    taxable_rounded, split_val)
    return eval_zuerich(rows, taxable_rounded, split_val)

# ------------------------- Factors (multipliers) ---------------
def get_factor_for_bfs(canton_id:int, bfs_id:int):
    for f in load_factors(canton_id):
        if f.get("Location",{}).get("BfsID")==bfs_id:
            return f
    return None

def church_income_factor(confession:str, factor:dict)->float:
    if not factor: return 0.0
    if confession=="christ":      return float(factor.get("IncomeRateChrist") or 0.0)
    if confession=="roman":       return float(factor.get("IncomeRateRoman") or 0.0)
    if confession=="protestant":  return float(factor.get("IncomeRateProtestant") or 0.0)
    return 0.0  # none

# ------------------------- Payroll & tax helpers ---------------
def gross_to_net_for_tax(gross: float, pk: float)->tuple[float, dict]:
    """Devbrains gross->net used for the taxable income base (AN side only)."""
    g = clamp_pos(gross)
    ahv = g * AHV_IV_EO
    alv = min(g, ALV_NBU_CEILING) * ALV
    nbu = min(g, ALV_NBU_CEILING) * NBU
    pkd = clamp_pos(pk)
    net = g - (ahv + alv + nbu + pkd)
    return max(0.0, net), {"ahv":ahv,"alv":alv,"nbu":nbu,"pk":pkd}

def employer_costs(salary: float, age_key: str, fak=0.015, uvg=0.01):
    if salary<=0: return dict(ahv=0.0,alv=0.0,bvg=0.0,extra=0.0,total=0.0)
    ahv = 0.053 * salary
    alv = 0.011 * min(salary, ALV_NBU_CEILING)
    bvg = (BVG_rates[age_key]/2.0) * bvg_insured_part(salary) if salary>=BVG_entry_threshold else 0.0
    extra = fak*salary + uvg*salary
    return dict(ahv=ahv, alv=alv, bvg=bvg, extra=extra, total=ahv+alv+bvg+extra)

def qualifies_partial(share_pct): return (share_pct or 0.0) >= 10.0

# --- UPDATED: incl_rates -> canton from JSON, Bund fixed 70% ---
def incl_rates(qualifies: bool, canton_code: str):
    if not qualifies:
        return 1.0, 1.0
    inc_fed = 0.70
    mapping = load_dividend_inclusion_map()
    inc_cant = float(mapping.get(canton_code, 0.70))
    return inc_fed, inc_cant

def canton_tax(taxable_canton: float, canton_id:int, bfs_id:int, relationship:str, children:int, confession:str):
    groups = groups_for_relationship(relationship, children)
    tarif, grp = pick_tarif(canton_id, "EINKOMMENSSTEUER", groups)
    base = eval_tariff_amount(tarif, taxable_canton, grp)
    factor = get_factor_for_bfs(canton_id, bfs_id)
    canton = base * ((factor.get("IncomeRateCanton",0.0) or 0.0)/100.0) if factor else 0.0
    city   = base * ((factor.get("IncomeRateCity",0.0) or 0.0)/100.0)   if factor else 0.0
    church = base * (church_income_factor(confession, factor)/100.0)     if factor else 0.0

    # Optional: Personalsteuer if present as a separate tariff in some cantons
    pers_tarif, _ = pick_tarif(canton_id, "PERSONALSTEUER", groups)
    personal = eval_tariff_amount(pers_tarif, taxable_canton, grp) if pers_tarif else 0.0

    return base, canton, city, church, personal, grp, tarif

def federal_tax(taxable_bund: float, relationship:str, children:int):
    groups = groups_for_relationship(relationship, children)
    tarif, grp = pick_tarif(0, "EINKOMMENSSTEUER", groups)
    taxes = eval_tariff_amount(tarif, taxable_bund, grp)
    # devbrains: −251 CHF pro Kind auf der Bundessteuer
    taxes = max(0.0, taxes - 251.0*children)
    return taxes, grp, tarif

# ------------------------- UI ----------------------------------
st.title("Lohn vs. Dividende")
st.caption("Mit diesem Rechner können Sie die steuerlichen Unterschiede zwischen Lohn und Dividende für Ihre AG oder GmbH simulieren. Auf Basis von Kanton, Gemeinde und individuellen Annahmen zeigt das Tool, welche Variante für Sie finanziell vorteilhafter ist. So erhalten Sie eine transparente Grundlage für Ihre Ausschüttungs- und Vergütungsentscheidungen..")

# Location
_, by_canton = load_locations()
canton_code = st.selectbox("Kanton", sorted(by_canton.keys()))
gemeinde_rec = st.selectbox("Gemeinde", options=by_canton[canton_code], format_func=lambda r: r["BfsName"])
BFS_ID   = int(gemeinde_rec["BfsID"])
CANT_ID  = int(gemeinde_rec["CantonID"])

col1, col2 = st.columns(2)
with col1:
    profit         = st.number_input("Firmengewinn **vor Lohn** [CHF]", 0.0, step=10_000.0)
    desired_income = st.number_input("Gewünschte **Gesamtauszahlung** an Inhaber [CHF] (optional)", 0.0, step=10_000.0)
with col2:
    other_inc      = st.number_input("Weitere steuerbare Einkünfte [CHF]", 0.0, step=10_000.0)
    age_input      = st.number_input("Alter <br> (für BVG-Altersband)", min_value=18, max_value=70, value=40, step=1)

with st.expander("ANNAHMEN", expanded=True):
    st.subheader("Annahmen")
    cA, cB = st.columns(2)
    with cA:
        relationship = st.selectbox(
            "Zivilstand",
            options=[("s","Ledig"),("c","Konkubinat"),("m","Verheiratet"),("rp","Eingetragene Partnerschaft")],
            index=0, format_func=lambda x: x[1]
        )[0]
        children     = st.number_input("Kinder (für Splitting & Bund-Kinderabzug)", 0, step=1)
        confession   = st.selectbox(
            "Konfession (Kirchensteuer)",
            options=[("none","Keine"),("roman","Röm.-kath."),("protestant","Ref./evang."),("christ","Christkath.")],
            index=0, format_func=lambda x: x[1]
        )[0]
        share_pct    = st.number_input("Beteiligungsquote [%] (Teilbesteuerung Div. ab 10 %)", 0.0, 100.0, 100.0, step=5.0)
        min_salary   = st.number_input("Marktüblicher Mindestlohn [CHF]", 0.0, step=10_000.0, value=120_000.0)
    with cB:
        pk_buyin     = st.number_input("PK-Einkauf (privat) / PK-Abzug [CHF]", 0.0, step=1.0)
        fak_rate     = st.number_input("FAK (nur Arbeitgeber) [%]", 0.0, 5.0, 1.5, step=0.1)/100.0
        uvg_rate     = st.number_input("UVG/KTG (Arbeitgeber) [%]", 0.0, 5.0, 1.0, step=0.1)/100.0
        st.caption("AHV/ALV/BVG standardmäßig **an**. Regelmodus fix **Strikt**.")
    st.markdown("---")
    st.markdown("**Abzüge – manuell** (direkt vom steuerbaren Einkommen abgezogen):")
    d1, d2 = st.columns(2)
    with d1: fed_ded_manual  = st.number_input("Abzüge **Bund** [CHF]", 0.0, step=100.0, value=0.0)
    with d2: cant_ded_manual = st.number_input("Abzüge **Kanton/Gemeinde** [CHF]", 0.0, step=100.0, value=0.0)

optimizer_on = st.checkbox("Optimierer – beste Mischung (Lohn + Dividende) finden", value=True)
debug_mode   = st.checkbox("Debug-Informationen anzeigen", value=False)

if desired_income == 0: desired_income = None
elif desired_income and desired_income > profit: desired_income = profit

# ------------------------- Scenarios ---------------------------
def scenario_salary_only():
    age_key = age_to_band(age_input)
    salary = profit if desired_income is None else min(profit, desired_income)
    ag = employer_costs(salary, age_key, fak=fak_rate, uvg=uvg_rate)

    net_for_tax, an_parts = gross_to_net_for_tax(salary, pk_buyin)
    taxable_fed  = clamp_pos(net_for_tax + other_inc - fed_ded_manual)
    taxable_cant = clamp_pos(net_for_tax + other_inc - cant_ded_manual)

    fed_tax, fed_grp, fed_tarif = federal_tax(taxable_fed, relationship, children)
    base_cant, tax_cant, tax_city, tax_church, tax_pers, cant_grp, cant_tarif = canton_tax(
        taxable_cant, CANT_ID, BFS_ID, relationship, children, confession
    )

    income_tax_total = fed_tax + tax_cant + tax_city + tax_church + tax_pers
    net_owner = salary - (an_parts["ahv"] + an_parts["alv"] + an_parts["nbu"] + an_parts["pk"]) - income_tax_total

    return {
        "salary": salary, "dividend": 0.0,
        "income_tax": income_tax_total, "net": net_owner,
        "blocks": dict(
            ag=ag, an=an_parts,
            fed=fed_tax, fed_grp=fed_grp, fed_tarif=fed_tarif,
            base_cant=base_cant, cant=tax_cant, city=tax_city, church=tax_church, personal=tax_pers,
            cant_grp=cant_grp, cant_tarif=cant_tarif,
            taxable_fed=taxable_fed, taxable_cant=taxable_cant
        )
    }

def scenario_dividend():
    age_key = age_to_band(age_input)
    qualifies = qualifies_partial(share_pct)
    # UPDATED: pass canton_code for cantonal inclusion
    inc_fed, inc_cant = incl_rates(qualifies, canton_code)

    salary = min(min_salary, profit if desired_income is None else min(profit, desired_income))
    ag = employer_costs(salary, age_key, fak=fak_rate, uvg=uvg_rate)

    pool = clamp_pos(profit - salary - ag["total"])
    desired_left = None if desired_income is None else clamp_pos(desired_income - salary)
    dividend = pool if desired_left is None else min(pool, desired_left)
    if salary < min_salary: dividend = 0.0

    net_for_tax, an_parts = gross_to_net_for_tax(salary, pk_buyin)
    taxable_fed  = clamp_pos(net_for_tax + dividend*inc_fed  + other_inc - fed_ded_manual)
    taxable_cant = clamp_pos(net_for_tax + dividend*inc_cant + other_inc - cant_ded_manual)

    fed_tax, fed_grp, fed_tarif = federal_tax(taxable_fed, relationship, children)
    base_cant, tax_cant, tax_city, tax_church, tax_pers, cant_grp, cant_tarif = canton_tax(
        taxable_cant, CANT_ID, BFS_ID, relationship, children, confession
    )

    income_tax_total = fed_tax + tax_cant + tax_city + tax_church + tax_pers
    net_owner = (salary - (an_parts["ahv"] + an_parts["alv"] + an_parts["nbu"] + an_parts["pk"])) + dividend - income_tax_total

    return {
        "salary": salary, "dividend": dividend,
        "income_tax": income_tax_total, "net": net_owner,
        "blocks": dict(
            ag=ag, an=an_parts,
            fed=fed_tax, fed_grp=fed_grp, fed_tarif=fed_tarif,
            base_cant=base_cant, cant=tax_cant, city=tax_city, church=tax_church, personal=tax_pers,
            cant_grp=cant_grp, cant_tarif=cant_tarif,
            inc_fed=inc_fed, inc_cant=inc_cant,
            taxable_fed=taxable_fed, taxable_cant=taxable_cant
        )
    }

def optimize_mix(step=1_000.0):
    best=None
    age_key = age_to_band(age_input)
    qualifies = qualifies_partial(share_pct)
    # UPDATED: pass canton_code
    inc_fed, inc_cant = incl_rates(qualifies, canton_code)

    cap = profit if desired_income is None else min(profit, desired_income)
    s=0.0
    while s<=cap+1e-6:
        ag = employer_costs(s, age_key, fak=fak_rate, uvg=uvg_rate)
        pool = clamp_pos(profit - s - ag["total"])
        desired_left = None if desired_income is None else clamp_pos(desired_income - s)
        div = pool if desired_left is None else min(pool, desired_left)
        if s < min_salary: div=0.0

        net_for_tax, an_parts = gross_to_net_for_tax(s, pk_buyin)
        taxable_fed  = clamp_pos(net_for_tax + div*inc_fed  + other_inc - fed_ded_manual)
        taxable_cant = clamp_pos(net_for_tax + div*inc_cant + other_inc - cant_ded_manual)

        fed_tax,_grp,_tf = federal_tax(taxable_fed, relationship, children)
        base_cant, tax_cant, tax_city, tax_church, tax_pers, _cg, _ct = canton_tax(
            taxable_cant, CANT_ID, BFS_ID, relationship, children, confession
        )
        total = fed_tax + tax_cant + tax_city + tax_church + tax_pers
        net = (s - (an_parts["ahv"] + an_parts["alv"] + an_parts["nbu"] + an_parts["pk"])) + div - total
        if (best is None) or (net>best["net"]):
            best=dict(salary=s, dividend=div, income_tax=total, net=net,
                      retained_after_tax=max(0.0, pool - div))
        s+=step
    return best

# ------------------------- Chart helper ------------------------
def tax_breakdown_chart(title: str, fed: float, kant: float, city: float, church: float, personal: float):
    labels = ["Bund", "Kanton", "Gemeinde", "Kirche", "Personal"]
    values = [float(fed or 0), float(kant or 0), float(city or 0), float(church or 0), float(personal or 0)]
    total = sum(values)

    # Blue gradient palette (light → dark)
    palette = ["#DBEAFE", "#93C5FD", "#60A5FA", "#3B82F6", "#1D4ED8"]

    # % of total + outside labels
    pct = [(v / total * 100.0) if total > 0 else 0.0 for v in values]
    text_outside = [f"CHF {v:,.0f}  ({p:.1f}%)" if v > 0 else "" for v, p in zip(values, pct)]

    fig = go.Figure(go.Bar(
        x=values,
        y=labels,
        orientation="h",
        text=text_outside,
        textposition="outside",
        marker=dict(color=palette, line=dict(width=0)),
        hovertemplate="<b>%{y}</b><br>CHF %{x:,.0f}<br>% vom Total: %{customdata:.1f}%<extra></extra>",
        customdata=pct,
    ))

    # Minimal layout, NO background, NO grid
    fig.update_layout(
        title=title,
        template=None,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        height=300,
        bargap=0.45,
        margin=dict(l=80, r=20, t=50, b=10),
        xaxis_title="CHF",
        yaxis=dict(categoryorder="array", categoryarray=labels),
        showlegend=False,
        hoverlabel=dict(namelength=-1, font=dict(size=12)),
        font=dict(size=12),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, showline=False, ticks="")
    fig.update_yaxes(showgrid=False, zeroline=False, showline=False, ticks="")

    if total > 0:
        fig.add_annotation(
            x=total, y=-0.6,
            text=f"<b>Total Steuern:</b> CHF {total:,.0f}",
            showarrow=False, font=dict(size=12), xanchor="right"
        )

    st.plotly_chart(fig, use_container_width=True, theme=None)

# ------------------------- Run & render ------------------------
if profit > 0:
    A = scenario_salary_only()
    B = scenario_dividend()
    with st.container(border=True):
      st.subheader("Szenario A – 100% Lohn")
      st.write(f"Bruttolohn: **CHF {A['salary']:,.0f}**")
      st.write(f"AG AHV/ALV/BVG: CHF {(A['blocks']['ag']['ahv']+A['blocks']['ag']['alv']+A['blocks']['ag']['bvg']):,.0f}")
      st.write(f"AG FAK/UVG/KTG: CHF {A['blocks']['ag']['extra']:,.0f}")
      st.write(f"AN AHV/ALV/NBU/PK: CHF {(A['blocks']['an']['ahv']+A['blocks']['an']['alv']+A['blocks']['an']['nbu']+A['blocks']['an']['pk']):,.0f}")
      st.write(f"Einkommenssteuer **Bund**: CHF {A['blocks']['fed']:,.0f}")
      st.write(f"Einkommenssteuer **Kanton**: CHF {A['blocks']['cant']:,.0f}  | **Gemeinde**: CHF {A['blocks']['city']:,.0f}  | **Kirche**: CHF {A['blocks']['church']:,.0f}  | **Personal**: CHF {A['blocks']['personal']:,.0f}")
      st.success(f"**Netto an Inhaber (heute):** CHF {A['net']:,.0f}")

      tax_breakdown_chart(
          "Steueraufteilung (Szenario A)",
          A["blocks"]["fed"], A["blocks"]["cant"], A["blocks"]["city"], A["blocks"]["church"], A["blocks"]["personal"]
      )
    st.divider()
    with st.container(border=True):
      st.subheader("Szenario B – Lohn + Dividende (Strikt)")
      st.write(f"Bruttolohn: **CHF {B['salary']:,.0f}** | Dividende gesamt: **CHF {B['dividend']:,.0f}**")
      st.write(f"Einkommenssteuer **Bund**: CHF {B['blocks']['fed']:,.0f}")
      st.write(f"Einkommenssteuer **Kanton**: CHF {B['blocks']['cant']:,.0f}  | **Gemeinde**: CHF {B['blocks']['city']:,.0f}  | **Kirche**: CHF {B['blocks']['church']:,.0f}  | **Personal**: CHF {B['blocks']['personal']:,.0f}")
      st.caption(f"Teilbesteuerung Dividenden: Bund {int(B['blocks']['inc_fed']*100)}%, Kanton {int(B['blocks']['inc_cant']*100)}% (ab 10% Beteiligung).")
      st.success(f"**Netto an Inhaber (heute):** CHF {B['net']:,.0f}")
  
      tax_breakdown_chart(
          "Steueraufteilung (Szenario B)",
          B["blocks"]["fed"], B["blocks"]["cant"], B["blocks"]["city"], B["blocks"]["church"], B["blocks"]["personal"]
      )

    st.markdown("---")
    st.subheader("Vergleich (heutiger Nettozufluss)")
    c1,c2=st.columns(2)
    with c1: st.metric("A: Lohn", f"CHF {A['net']:,.0f}")
    with c2: st.metric("B: Lohn + Dividende", f"CHF {B['net']:,.0f}")

    if optimizer_on:
        st.markdown("---")
        st.subheader("Optimierer – beste Mischung (Strikt)")
        best = optimize_mix()
        st.write(f"**Optimaler Lohn:** CHF {best['salary']:,.0f}  |  **Dividende:** CHF {best['dividend']:,.0f}")
        st.write(f"Einkommenssteuer gesamt: CHF {best['income_tax']:,.0f}")
        st.write(f"Nachsteuerlich einbehalten (vereinfachend): CHF {best['retained_after_tax']:,.0f}")
        st.success(f"**Max. Netto an Inhaber (heute):** CHF {best['net']:,.0f}")

    if debug_mode:
        st.markdown("---")
        st.subheader("Debug-Informationen")
        st.write(f"Ort: {gemeinde_rec['BfsName']} ({canton_code}) | BFS: {BFS_ID} | CantonID: {CANT_ID}")
        st.write(f"Taxable Bund: CHF {A['blocks']['taxable_fed']:,.0f} | Taxable Kanton: CHF {A['blocks']['taxable_cant']:,.0f}")
        st.write(f"Tariftypen: Bund {(A['blocks']['fed_tarif'] or {}).get('tableType')}, Kanton {(A['blocks']['cant_tarif'] or {}).get('tableType')}")
        if canton_code == "BL":
            st.caption("BL FORMEL-Engine aktiv – Formel normalisiert (log/ln) und mit Splitting + Rundung ausgewertet.")

    with st.expander("Hinweise & Annahmen", expanded=False):
        st.markdown(
            "- **Kirchensteuer:** echte Ortsfaktoren (röm./ref./christkath.) aus `factors/`.\n"
            "- **AHV/ALV/NBU/PK (AN):** 5.3% / 1.1% / 0.4% (bis 148’200) + PK-Einkauf (frei).\n"
            "- **Splitting & Gruppe:** gemäss Zivilstand/Kinder und Tariftabelle (nur wenn zulässig).\n"
            "- **Bund:** zusätzlicher Kinderabzug −251 CHF/Kind auf der Bundessteuer.\n"
            "- **BVG-Anzeige (AG/AN):** als Kostblöcke aufgeführt; Steuerbasis nutzt devbrains-Netto.\n"
            "- **Abzüge:** bis der vollständige Abzugskatalog portiert ist, stehen zwei manuelle Felder (Bund / Kanton) zur Verfügung."
        )
else:
    st.warning("Bitte Gewinn > 0 eingeben, um die Berechnung zu starten.")

