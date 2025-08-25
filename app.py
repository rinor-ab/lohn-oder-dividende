# app.py – Lohn vs. Dividende (devbrains 2025: correct canton engines, splitting & church, simplified deductions)
import json, math, pathlib
import streamlit as st

# ------------------------- Data roots -------------------------
APP_DIR = pathlib.Path(__file__).parent
CANDIDATE_DATA_ROOTS = [
    APP_DIR / "data" / "parsed" / "2025",
    APP_DIR / "parsed" / "2025",
    APP_DIR / "2025",
    pathlib.Path("/mnt/data/2025"),  # works if you run from a notebook
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

# devbrains gross→net for income tax base
AHV_IV_EO = 0.053
ALV      = 0.011
NBU      = 0.004
ALV_NBU_CEILING = 148_200.0

# BVG (nur für Arbeitgeber/Arbeitnehmer Anzeige)
BVG_rates = {"25-34": 0.07, "35-44": 0.10, "45-54": 0.15, "55-65": 0.18}
BVG_entry_threshold = 22_680.0
BVG_coord_deduction = 26_460.0
BVG_max_insured     = 90_720.0

def clamp_pos(x): 
    try: return max(0.0, float(x or 0.0))
    except: return 0.0

def age_to_band(age:int)->str:
    a=int(age or 35)
    if a<35: return "25-34"
    if a<45: return "35-44"
    if a<55: return "45-54"
    return "55-65"

def bvg_insured_part(salary):
    return max(0.0, min(salary, BVG_max_insured) - BVG_coord_deduction)

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

# ------------------------- Tariff engine (devbrains parity) ---
def dinero_round_100_down(x: float) -> float:
    return math.floor((x or 0.0)/100.0)*100.0

def eval_zuerich(rows, taxable, split=1):
    base = taxable / max(1, split)
    rem = base
    tax = 0.0
    for r in rows:
        amount=float(r.get("amount") or 0.0)
        pct=(r.get("percent") or 0.0)/100.0
        if amount<=0: continue
        use=min(rem, amount)
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

def eval_formel(rows, taxable, split=1):
    base = taxable / max(1, split)
    selected=None
    for r in rows:
        am=float(r.get("amount") or 0.0)
        if am<=base: selected=r
        else: break
    expr=(selected or {}).get("formula") or ""
    safe = expr.replace("$wert$", "X").replace("log X","log(X)")
    try:
        val = eval(safe, {"__builtins__":{}}, {"log":math.log, "X":base})
        return float(val) * max(1, split)
    except Exception:
        return 0.0

def groups_for_relationship(relationship: str, children: int):
    groups=[]
    if relationship in ("m","rp"): groups.append("VERHEIRATET")
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
    # devbrains workaround: some ZUERICH tables actually carry base taxes -> treat as BUND
    if table_type=="ZUERICH" and any((row.get("taxes") or 0)>0 for row in (tarif_obj.get("table") or [])):
        table_type="BUND"
    split = int(tarif_obj.get("splitting") or 0)
    split_ok = (split>0 and group_splitting_eligible(group))
    split_val = split if split_ok else 1
    # IMPORTANT: devbrains rounds after splitting
    taxable_rounded = dinero_round_100_down(taxable / split_val) * split_val

    rows = tarif_obj.get("table") or []
    if table_type=="FLATTAX":  return eval_flattax(rows,   taxable_rounded, split_val)
    if table_type=="ZUERICH":  return eval_zuerich(rows,   taxable_rounded, split_val)
    if table_type=="BUND":     return eval_bund(rows,      taxable_rounded, split_val)
    if table_type=="FREIBURG": return eval_freiburg(rows,  taxable_rounded, split_val)
    if table_type=="FORMEL":   return eval_formel(rows,    taxable_rounded, split_val)
    # fallback: treat as zürich
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

# ------------------------- UI ----------------------------------
st.title("Lohn vs. Dividende")
st.caption("Devbrains 2025 Tarife – korrekte Auswertung pro Kanton (BUND/ZH/FR/Flat/Formel), Splitting & Kirchensteuer nach Konfession. BVG nach Alter.")

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
    age_input      = st.number_input("Alter (für BVG-Altersband)", min_value=18, max_value=70, value=40, step=1)

with st.expander("ANNAHMEN", expanded=True):
    st.subheader("Annahmen")
    cA, cB = st.columns(2)
    with cA:
        relationship = st.selectbox("Zivilstand", options=[("s","Ledig"),("c","Konkubinat"),("m","Verheiratet"),("rp","Eingetragene Partnerschaft")], index=0, format_func=lambda x: x[1])[0]
        children     = st.number_input("Kinder (für Splitting & Bund-Kinderabzug)", 0, step=1)
        confession   = st.selectbox("Konfession (Kirchensteuer)", options=[("none","Keine"),("roman","Röm.-kath."),("protestant","Ref./evang."),("christ","Christkath.")], index=0, format_func=lambda x: x[1])[0]
        share_pct    = st.number_input("Beteiligungsquote [%] (Teilbesteuerung Div. ab 10 %)", 0.0, 100.0, 100.0, step=5.0)
        min_salary   = st.number_input("Marktüblicher Mindestlohn [CHF]", 0.0, step=10_000.0, value=120_000.0)
    with cB:
        pk_buyin     = st.number_input("PK-Einkauf (privat) / PK-Abzug [CHF]", 0.0, step=1.0)
        fak_rate     = st.number_input("FAK (nur Arbeitgeber) [%]", 0.0, 5.0, 1.5, step=0.1)/100.0
        uvg_rate     = st.number_input("UVG/KTG (Arbeitgeber) [%]", 0.0, 5.0, 1.0, step=0.1)/100.0
        st.caption("AHV/ALV/BVG standardmäßig **an**. Regelmodus fix **Strikt**.")
    st.markdown("---")
    st.markdown("**Abzüge – manuell** (direkt vom steuerbaren Einkommen abgezogen; solange der komplette Abzugskatalog nicht 1:1 portiert ist):")
    d1, d2 = st.columns(2)
    with d1: fed_ded_manual  = st.number_input("Abzüge **Bund** [CHF]", 0.0, step=100.0, value=0.0)
    with d2: cant_ded_manual = st.number_input("Abzüge **Kanton/Gemeinde** [CHF]", 0.0, step=100.0, value=0.0)

optimizer_on = st.checkbox("Optimierer – beste Mischung (Lohn + Dividende) finden", value=True)
debug_mode   = st.checkbox("Debug-Informationen anzeigen", value=False)

if desired_income == 0: desired_income = None
elif desired_income and desired_income > profit: desired_income = profit

# ------------------------- Helpers for this app ---------------
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
def incl_rates(qualifies):
    return (0.70 if qualifies else 1.0, 0.70 if qualifies else 1.0)

def canton_tax(taxable_canton: float, canton_id:int, bfs_id:int, relationship:str, children:int, confession:str):
    groups = groups_for_relationship(relationship, children)
    tarif, grp = pick_tarif(canton_id, "EINKOMMENSSTEUER", groups)
    base = eval_tariff_amount(tarif, taxable_canton, grp)
    factor = get_factor_for_bfs(canton_id, bfs_id)
    canton = base * ((factor.get("IncomeRateCanton",0.0) or 0.0)/100.0) if factor else 0.0
    city   = base * ((factor.get("IncomeRateCity",0.0) or 0.0)/100.0)   if factor else 0.0
    church = base * (church_income_factor(confession, factor)/100.0)     if factor else 0.0
    return base, canton, city, church, grp, tarif

def federal_tax(taxable_bund: float, relationship:str, children:int):
    groups = groups_for_relationship(relationship, children)
    tarif, grp = pick_tarif(0, "EINKOMMENSSTEUER", groups)
    taxes = eval_tariff_amount(tarif, taxable_bund, grp)
    # devbrains: minus 251 CHF per child on federal income tax
    taxes = max(0.0, taxes - 251.0*children)
    return taxes, grp, tarif

# ------------------------- Scenarios ---------------------------
def scenario_salary_only():
    age_key = age_to_band(age_input)
    # salary choice
    salary = profit if desired_income is None else min(profit, desired_income)
    ag = employer_costs(salary, age_key, fak=fak_rate, uvg=uvg_rate)

    # taxable base using devbrains gross->net + manual deductions
    net_for_tax, an_parts = gross_to_net_for_tax(salary, pk_buyin)
    taxable_fed  = clamp_pos(net_for_tax + other_inc - fed_ded_manual)
    taxable_cant = clamp_pos(net_for_tax + other_inc - cant_ded_manual)

    fed_tax, fed_grp, fed_tarif = federal_tax(taxable_fed, relationship, children)
    base_cant, tax_cant, tax_city, tax_church, cant_grp, cant_tarif = canton_tax(
        taxable_cant, CANT_ID, BFS_ID, relationship, children, confession
    )

    income_tax_total = fed_tax + tax_cant + tax_city + tax_church
    net_owner = salary - (an_parts["ahv"] + an_parts["alv"] + an_parts["nbu"] + an_parts["pk"]) - income_tax_total

    return {
        "salary": salary, "dividend": 0.0,
        "income_tax": income_tax_total, "net": net_owner,
        "blocks": dict(
            ag=ag, an=an_parts,
            fed=fed_tax, fed_grp=fed_grp, fed_tarif=fed_tarif,
            base_cant=base_cant, cant=tax_cant, city=tax_city, church=tax_church,
            cant_grp=cant_grp, cant_tarif=cant_tarif
        )
    }

def scenario_dividend():
    age_key = age_to_band(age_input)
    qualifies = qualifies_partial(share_pct)
    inc_fed, inc_cant = incl_rates(qualifies)

    # Lohn unter Strikt
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
    base_cant, tax_cant, tax_city, tax_church, cant_grp, cant_tarif = canton_tax(
        taxable_cant, CANT_ID, BFS_ID, relationship, children, confession
    )

    income_tax_total = fed_tax + tax_cant + tax_city + tax_church
    net_owner = (salary - (an_parts["ahv"] + an_parts["alv"] + an_parts["nbu"] + an_parts["pk"])) + dividend - income_tax_total

    return {
        "salary": salary, "dividend": dividend,
        "income_tax": income_tax_total, "net": net_owner,
        "blocks": dict(
            ag=ag, an=an_parts,
            fed=fed_tax, fed_grp=fed_grp, fed_tarif=fed_tarif,
            base_cant=base_cant, cant=tax_cant, city=tax_city, church=tax_church,
            cant_grp=cant_grp, cant_tarif=cant_tarif,
            inc_fed=inc_fed, inc_cant=inc_cant
        )
    }

def optimize_mix(step=1_000.0):
    best=None
    age_key = age_to_band(age_input)
    qualifies = qualifies_partial(share_pct)
    inc_fed, inc_cant = incl_rates(qualifies)

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
        base_cant, tax_cant, tax_city, tax_church, _cg, _ct = canton_tax(
            taxable_cant, CANT_ID, BFS_ID, relationship, children, confession
        )
        total = fed_tax + tax_cant + tax_city + tax_church
        net = (s - (an_parts["ahv"] + an_parts["alv"] + an_parts["nbu"] + an_parts["pk"])) + div - total
        if (best is None) or (net>best["net"]):
            best=dict(salary=s, dividend=div, income_tax=total, net=net,
                      retained_after_tax=max(0.0, pool - div))
        s+=step
    return best

# ------------------------- Run & render ------------------------
if profit > 0:
    A = scenario_salary_only()
    B = scenario_dividend()

    st.subheader("Szenario A – 100% Lohn")
    st.write(f"Bruttolohn: **CHF {A['salary']:,.0f}**")
    st.write(f"AG AHV/ALV/BVG: CHF {(A['blocks']['ag']['ahv']+A['blocks']['ag']['alv']+A['blocks']['ag']['bvg']):,.0f}")
    st.write(f"AG FAK/UVG/KTG: CHF {A['blocks']['ag']['extra']:,.0f}")
    st.write(f"AN AHV/ALV/NBU/PK: CHF {(A['blocks']['an']['ahv']+A['blocks']['an']['alv']+A['blocks']['an']['nbu']+A['blocks']['an']['pk']):,.0f}")
    st.write(f"Einkommenssteuer **Bund**: CHF {A['blocks']['fed']:,.0f}")
    st.write(f"Einkommenssteuer **Kanton**: CHF {A['blocks']['cant']:,.0f}  | **Gemeinde**: CHF {A['blocks']['city']:,.0f}  | **Kirche**: CHF {A['blocks']['church']:,.0f}")
    st.success(f"**Netto an Inhaber (heute):** CHF {A['net']:,.0f}")

    st.subheader("Szenario B – Lohn + Dividende (Strikt)")
    st.write(f"Bruttolohn: **CHF {B['salary']:,.0f}** | Dividende gesamt: **CHF {B['dividend']:,.0f}**")
    st.write(f"Einkommenssteuer **Bund**: CHF {B['blocks']['fed']:,.0f}")
    st.write(f"Einkommenssteuer **Kanton**: CHF {B['blocks']['cant']:,.0f}  | **Gemeinde**: CHF {B['blocks']['city']:,.0f}  | **Kirche**: CHF {B['blocks']['church']:,.0f}")
    st.caption(f"Teilbesteuerung Dividenden: Bund {int(B['blocks']['inc_fed']*100)}%, Kanton {int(B['blocks']['inc_cant']*100)}% (ab 10% Beteiligung).")
    st.success(f"**Netto an Inhaber (heute):** CHF {B['net']:,.0f}")

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
        st.write(f"Tarif Bund Gruppe: {A['blocks']['fed_grp']} | Kanton Gruppe: {A['blocks']['cant_grp']}")
        st.write(f"Tariftypen: Bund {(A['blocks']['fed_tarif'] or {}).get('tableType')}, Kanton {(A['blocks']['cant_tarif'] or {}).get('tableType')}")
        st.caption("Bund: −251 CHF pro Kind; Splitting gemäss Tariftabelle und Gruppe (Verheiratet / Ledig mit Kindern).")

    with st.expander("Hinweise & Annahmen", expanded=False):
        st.markdown(
            "- **Kirchensteuer:** echte Ortsfaktoren (röm./ref./christkath.).\n"
            "- **AHV/ALV/NBU/PK (AN):** 5.3% / 1.1% / 0.4% (bis 148’200) + PK-Einkauf.\n"
            "- **Splitting & Gruppe:** gemäss Zivilstand/Kinder und Tariftabelle.\n"
            "- **Bund:** zusätzlicher Kinderabzug −251 CHF/Kind auf der Bundessteuer.\n"
            "- **BVG-Anzeige (AG/AN):** als Kostblöcke aufgeführt; für die Steuerbasis wird devbrains-Netto verwendet.\n"
            "- **Abzüge:** solange der vollständige Abzugskatalog nicht portiert ist, stehen zwei manuelle Felder (Bund / Kanton) zur Verfügung."
        )
else:
    st.warning("Bitte Gewinn > 0 eingeben, um die Berechnung zu starten.")
