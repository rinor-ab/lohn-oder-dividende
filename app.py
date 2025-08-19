# app.py — Lohn vs. Dividende (devbrains 2025; simplified deductions UI; robust parsing)
import json, math, pathlib
import streamlit as st
from functools import lru_cache
from collections import defaultdict

# ================= Paths / Year =================
BASE_DIR   = pathlib.Path(__file__).parent
PARSED_DIR = BASE_DIR / "data" / "parsed"
YEAR       = "2025"

# ================= Constants ===================
CHURCH_AVG_RATE   = 0.12          # Ø Kirchensteuer-Zuschlag (12%)
AHV_ON_DEFAULT    = True          # AHV/ALV/BVG standardmäßig an
DIV_PARTIAL_FED   = 0.70          # vereinfachte Teilbesteuerung (≥10% Beteiligung)
DIV_PARTIAL_CANT  = 0.70

# Social security (kept from your earlier config)
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

def _norm_text(val) -> str:
    """Return a lowercased string from any JSON shape (str/dict/list/number)."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val.lower()
    if isinstance(val, (int, float)):
        return str(val).lower()
    if isinstance(val, dict):
        # prefer language/name-like keys if present
        for k in ("de", "en", "fr", "it", "name", "label", "title"):
            if k in val and isinstance(val[k], str):
                return val[k].lower()
        parts = []
        for v in val.values():
            s = _norm_text(v)
            if s: parts.append(s)
        return " ".join(parts)
    if isinstance(val, list):
        parts = []
        for v in val:
            s = _norm_text(v)
            if s: parts.append(s)
        return " ".join(parts)
    return str(val).lower()

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
    bund_path = PARSED_DIR / YEAR / "deductions" / "0.json"
    kant_path = PARSED_DIR / YEAR / "deductions" / f"{canton_id}.json"
    bund = _load_json(bund_path) if bund_path.exists() else []
    kant = _load_json(kant_path) if kant_path.exists() else []
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
    rem = clamp(amount); tax = 0.0
    for row in table_rows:
        pct = float(row.get("percent", 0.0)) / 100.0
        width = float(row.get("amount", 0.0))
        if rem <= 0: break
        use = rem if width<=0 else min(rem, width)
        tax += use * pct; rem -= use
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
    return _eval_step_table(rows, base) * split

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
    """Generic computation with MINIMUM/MAXIMUM and %; clamps to base."""
    base = clamp(base_amount)
    total = 0.0
    for scope, item in (items_with_scope or []):
        fmt      = parse_flags(item.get("format", ""))
        minimum  = float(item.get("minimum", 0) or 0.0)
        maximum  = float(item.get("maximum", 0) or 0.0)
        percent  = float(item.get("percent", 0) or 0.0)/100.0
        default_amt = float(item.get("amount", 0) or 0.0)
        iid = str(item.get("id", ""))  # ensure string key
        key = f"{scope}:{iid}"

        user_amt = float(ui_values.get(key, 0.0) or 0.0)
        raw = (user_amt if user_amt > 0 else default_amt) + base * percent

        val = raw
        if "MINIMUM" in fmt: val = max(val, minimum)
        if "MAXIMUM" in fmt and maximum > 0: val = min(val, maximum)

        val = clamp(min(val, base))
        total += val
    return min(total, base)

def _match_item(items, *keywords):
    """
    Find first (scope,item) whose name/id contains all keyword groups.
    Each element of keywords can be a string or list/tuple of synonyms.
    """
    if not items:
        return None
    groups = []
    for k in keywords:
        if isinstance(k, (list, tuple)):
            groups.append([_norm_text(x) for x in k])
        else:
            groups.append([_norm_text(k)])

    for scope, it in items:
        name_txt = _norm_text(it.get("name"))
        id_txt   = _norm_text(it.get("id"))
        haystack = f"{name_txt} {id_txt}"
        ok = True
        for group in groups:
            if not any(needle in haystack for needle in group):
                ok = False
                break
        if ok:
            return (scope, it)
    return None

def pick_curated_items(bund_groups, kant_groups):
    B = flatten_deduction_items(bund_groups)   # scope "BUND"
    K = flatten_deduction_items(kant_groups)   # scope usually "KANTON"/"GEMEINDE"

    def pick(*kws):
        hit = _match_item(K, *kws)
        return hit if hit else _match_item(B, *kws)

    curated = {
        "vers":      pick("versicherungs", ["sparkapital", "spar"]),
        "s3a":       pick(["säule 3a","saeule 3a","3a"]),
        "verp":      pick("verpflegung"),
        "fahr":      pick("fahrkosten"),
        "beruf":     pick("berufsauslagen"),
        "beruf_neb": pick("berufsauslagen", "neben"),
        "uebrige":   pick("übrige", "abzug"),
        "schuld":    pick("schuldzinsen"),
        "unterhalt": pick(["unterhalt","unterhalts"], "liegenschaft"),
        "uebrige_w": pick("übrige", "abzug"),   # may resolve same as 'uebrige'
    }
    return curated, B, K

# ================= Personal tax wrappers =================
def income_components_with_bases(taxable_fed: float, taxable_cant: float, canton_id: int, bfs_id: int):
    t_fed = clamp(taxable_fed); t_cant = clamp(taxable_cant)

    fed_tarifs = load_tarifs_federal_if_any()
    fed_table  = _find_table(fed_tarifs, "EINKOMMENSSTEUER", "ALLE")
    fed_tax    = eval_tariff(fed_table, t_fed, 1) if fed_table else 0.0

    cant_tarifs = load_tarifs(canton_id)
    cant_table  = _find_table(cant_tarifs, "EINKOMMENSSTEUER", "ALLE")
    base_cant   = eval_tariff(cant_table, t_cant, 1) if cant_table else 0.0

    fac = factors_for_bfs(canton_id, bfs_id)
    mult = (fac["IncomeRateCanton"] + fac["IncomeRateCity"]) / 100.0
    cant_city = base_cant * mult
    cant_city_church = cant_city * (1.0 + CHURCH_AVG_RATE)

    return fed_tax, cant_city_church, (fed_tax + cant_city_church)

def corporate_tax_rate(canton_id: int, bfs_id: int) -> float:
    tarifs = load_tarifs(canton_id)
    corp = _find_table(tarifs, "GEWINNSTEUER")
    if not corp: return 0.10
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
st.caption("Devbrains 2025: locations + factors + tarifs + deductions. Ø Kirchensteuer; KER/Vermögenssteuer nicht berücksichtigt.")

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

optimizer_on = st.checkbox("Optimierer – beste Mischung (Lohn + Dividende) finden", value=True)

# --------- ANNAHMEN (all controls & deductions inside expander) ----------
with st.expander("ANNAHMEN", expanded=False):
    st.subheader("Annahmen")
    colA, colB = st.columns(2)
    with colA:
        min_salary  = st.number_input("Marktüblicher Mindestlohn [CHF]", 0.0, step=10_000.0, value=120_000.0, key="min_salary")
        share_pct   = st.number_input("Beteiligungsquote [%] (Teilbesteuerung ab 10 %)", 0.0, 100.0, 100.0, step=5.0, key="share_pct")
    with colB:
        fak_rate    = st.number_input("FAK (nur Arbeitgeber) [%]", 0.0, 5.0, 1.5, step=0.1, key="fak")/100.0
        uvg_rate    = st.number_input("UVG/KTG (Arbeitgeber) [%]", 0.0, 5.0, 1.0, step=0.1, key="uvg")/100.0
        pk_buyin    = st.number_input("PK-Einkauf (privat) [CHF]", 0.0, step=1.0, key="pkbuy")

    st.markdown("---")
    st.markdown("### Abzüge")
    bund_groups, kant_groups = load_deductions(canton_id)
    curated, B_items, K_items = pick_curated_items(bund_groups, kant_groups)

    colL, colR = st.columns(2)
    with colL:
        in_vers = st.number_input("Versicherungsprämien und Zinsen von Sparkapitalien", min_value=0.0, step=100.0, value=0.0, key="ded_vers")
        in_s3a  = st.number_input("Beiträge an Säule 3a", min_value=0.0, step=100.0, value=0.0, key="ded_s3a")
        in_verp = st.number_input("Verpflegungskosten", min_value=0.0, step=50.0,  value=0.0, key="ded_verp")
        in_fahr = st.number_input("Fahrkosten", min_value=0.0, step=100.0, value=0.0, key="ded_fahr")
        mode_beruf = st.radio("Berufsauslagen", ["pauschal", "effektiv"], horizontal=True, key="ded_beruf_mode")
        in_beruf_eff = st.number_input("Berufsauslagen (effektiv)", min_value=0.0, step=100.0, value=0.0, key="ded_beruf_eff", disabled=(mode_beruf=="pauschal"))
    with colR:
        in_beruf_neb = st.number_input("Berufsauslagen Nebenerwerb", min_value=0.0, step=100.0, value=0.0, key="ded_beruf_neb")
        in_uebrige   = st.number_input("Übrige Abzüge", min_value=0.0, step=100.0, value=0.0, key="ded_uebrige")
        st.markdown("#### Weitere Abzüge")
        in_schuld = st.number_input("Schuldzinsen", min_value=0.0, step=100.0, value=0.0, key="ded_schuld")
        in_unterh = st.number_input("Unterhaltskosten für Liegenschaften", min_value=0.0, step=100.0, value=0.0, key="ded_unterhalt")
        in_uebrige_w = st.number_input("Übrige Abzüge (weitere)", min_value=0.0, step=100.0, value=0.0, key="ded_uebrige_w")

    # stash inputs for use during scenario computations
    st.session_state["_ded_ui_values"] = dict(
        vers=in_vers, s3a=in_s3a, verp=in_verp, fahr=in_fahr,
        beruf_mode=mode_beruf, beruf_eff=in_beruf_eff,
        beruf_neb=in_beruf_neb, uebrige=in_uebrige,
        schuld=in_schuld, unterhalt=in_unterh, uebrige_w=in_uebrige_w
    )
    st.session_state["_curated_sets"] = (curated, B_items, K_items)

# Normalize desired payout
if desired_income == 0:
    desired_income = None
elif desired_income > profit:
    desired_income = profit

# ---------- Curated deductions application ----------
def apply_curated_deductions(base_fed, base_cant):
    curated, B_flat, K_flat = st.session_state.get("_curated_sets", ({}, [], []))
    ui = st.session_state.get("_ded_ui_values", {})

    # chosen items (tuple or None)
    chosen = {k: curated.get(k) for k in ["vers","s3a","verp","fahr","beruf","beruf_neb","uebrige","schuld","unterhalt","uebrige_w"]}

    # Build 'scope:id' → amount map
    ui_map = {}
    for key, tup in chosen.items():
        if not tup: continue
        scope, item = tup
        iid = str(item.get("id",""))
        if key == "beruf":
            amt = 0.0 if ui.get("beruf_mode")=="pauschal" else float(ui.get("beruf_eff", 0.0) or 0.0)
        else:
            fieldname = {
                "vers":"vers","s3a":"s3a","verp":"verp","fahr":"fahr",
                "beruf_neb":"beruf_neb","uebrige":"uebrige",
                "schuld":"schuld","unterhalt":"unterhalt","uebrige_w":"uebrige_w"
            }.get(key, key)
            amt = float(ui.get(fieldname, 0.0) or 0.0)
        ui_map[f"{scope}:{iid}"] = amt

    # separate by scope
    bund_items = [(s,i) for (k,(s,i)) in chosen.items() if k and tup and (tup:=chosen.get(k)) and (tup[0] or "").upper()=="BUND"]
    kant_items = [(s,i) for (k,(s,i)) in chosen.items() if k and tup and (tup:=chosen.get(k)) and (tup[0] or "").upper()!="BUND"]

    ded_fed  = compute_deductions_total(clamp(base_fed),  bund_items, ui_map) if bund_items else 0.0
    ded_cant = compute_deductions_total(clamp(base_cant), kant_items, ui_map) if kant_items else 0.0
    return ded_fed, ded_cant

def income_components_with_applied_deductions(base_fed, base_cant, canton_id, bfs_id):
    ded_fed, ded_cant = apply_curated_deductions(base_fed, base_cant)
    taxable_fed  = clamp(base_fed  - ded_fed)
    taxable_cant = clamp(base_cant - ded_cant)
    fed_tax, cant_tax, total_tax = income_components_with_bases(taxable_fed, taxable_cant, canton_id, bfs_id)
    return ded_fed, ded_cant, fed_tax, cant_tax, total_tax

# =================== Scenarios ==============================
def scenario_salary_only(profit, desired, canton_id, bfs_id, age_key, ahv_on, other, pk_buy):
    salary = profit if desired is None else min(profit, desired)
    ag = employer_costs(salary, age_key, ahv_on, fak=fak_rate, uvg=uvg_rate)
    an = employee_deductions(salary, age_key, ahv_on)

    rest = profit - salary - ag["total"]
    if rest < 0:
        st.warning("Bruttolohn inkl. Arbeitgeberabgaben > Gewinn – Restgewinn wird auf 0 gesetzt.")
    rest = max(0.0, rest)

    corp_rate = corporate_tax_rate(canton_id, bfs_id)
    corp_tax_amt = rest * corp_rate

    base = clamp(salary - an["total"] + other - pk_buy)
    ded_fed, ded_cant, fed_tax, cant_tax, total_tax = income_components_with_applied_deductions(base, base, canton_id, bfs_id)

    net_owner = salary - an["total"] - total_tax
    return {
        "salary": salary, "dividend": 0.0,
        "corp_tax": corp_tax_amt, "income_tax": total_tax,
        "net": net_owner, "adjusted_net": net_owner,
        "retained_after_tax": max(0.0, rest - corp_tax_amt),
        "blocks": dict(ag=ag, an=an, fed=fed_tax, cant=cant_tax, ded_fed=ded_fed, ded_cant=ded_cant)
    }

def scenario_dividend(profit, desired, canton_id, bfs_id, age_key, ahv_on, other, pk_buy,
                      min_salary, share_pct):
    salary = min(min_salary, profit if desired is None else min(profit, desired))
    ag = employer_costs(salary, age_key, ahv_on, fak=fak_rate, uvg=uvg_rate)
    an = employee_deductions(salary, age_key, ahv_on)

    rest = clamp(profit - salary - ag["total"])
    corp_rate = corporate_tax_rate(canton_id, bfs_id)
    corp_tax_amt = rest * corp_rate
    after_corp = rest - corp_tax_amt

    desired_left = None if desired is None else clamp(desired - salary)
    gross_div = after_corp if desired_left is None else min(after_corp, desired_left)

    dividend = gross_div if salary >= min_salary else 0.0
    if salary < min_salary and gross_div > 0:
        st.info("Dividende nicht zulässig, da Lohn < Mindestlohn (Strikt-Modus). Ausschüttung = 0.")

    qualifies = qualifies_partial(share_pct)
    inc_fed  = DIV_PARTIAL_FED  if qualifies else 1.0
    inc_cant = DIV_PARTIAL_CANT if qualifies else 1.0

    taxable_salary = clamp(salary - an["total"])
    base_fed  = clamp(taxable_salary + dividend*inc_fed  + other - pk_buy)
    base_cant = clamp(taxable_salary + dividend*inc_cant + other - pk_buy)

    ded_fed, ded_cant, fed_tax, cant_tax, total_tax = income_components_with_applied_deductions(base_fed, base_cant, canton_id, bfs_id)

    net_owner = (salary - an["total"]) + dividend - total_tax
    return {
        "salary": salary, "dividend": dividend,
        "corp_tax": corp_tax_amt, "income_tax": total_tax,
        "net": net_owner, "adjusted_net": net_owner,
        "retained_after_tax": clamp(rest - corp_tax_amt - dividend),
        "blocks": dict(ag=ag, an=an, inc_fed=inc_fed, inc_cant=inc_cant, ded_fed=ded_fed, ded_cant=ded_cant)
    }

def optimize_mix(step=1000.0):
    age_key = age_to_band(age_input)
    qualifies = qualifies_partial(share_pct)
    inc_fed  = DIV_PARTIAL_FED  if qualifies else 1.0
    inc_cant = DIV_PARTIAL_CANT if qualifies else 1.0

    best = None; s = 0.0
    corp_rate = corporate_tax_rate(canton_id, bfs_id)
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

        ded_fed, ded_cant, fed_tax, cant_tax, total_tax = income_components_with_applied_deductions(base_fed, base_cant, canton_id, bfs_id)
        net_owner = (s - an["total"]) + dividend - total_tax

        res = {"salary": s, "dividend": dividend, "net": net_owner, "adjusted_net": net_owner,
               "income_tax": total_tax, "corp_tax": corp_tax_amt,
               "retained_after_tax": clamp(rest - corp_tax_amt - dividend)}
        if (best is None) or (net_owner > best["adjusted_net"]): best = res
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
    if (A['blocks'].get('ded_fed',0) or A['blocks'].get('ded_cant',0)):
        st.caption(f"Berücksichtigte Abzüge – Bund: CHF {A['blocks']['ded_fed']:,.0f}, Kanton/Gemeinde: CHF {A['blocks']['ded_cant']:,.0f}")
    st.write(f"Nachsteuerlicher Gewinn einbehalten: CHF {A['retained_after_tax']:,.0f}")
    st.success(f"**Netto an Inhaber (heute):** CHF {A['adjusted_net']:,.0f}")

    # ----- Display B -----
    st.subheader("Szenario B – Lohn + Dividende")
    st.write(f"Bruttolohn: **CHF {B['salary']:,.0f}** | Dividende gesamt: **CHF {B['dividend']:,.0f}**")
    st.write(f"Körperschaftssteuer (nach Lohn): CHF {B['corp_tax']:,.0f}")
    st.write(f"Einkommenssteuer (Bund + Kant./Gem. + Kirche Ø): CHF {B['income_tax']:,.0f}")
    if (B['blocks'].get('ded_fed',0) or B['blocks'].get('ded_cant',0)):
        st.caption(f"Berücksichtigte Abzüge – Bund: CHF {B['blocks']['ded_fed']:,.0f}, Kanton/Gemeinde: CHF {B['blocks']['ded_cant']:,.0f}")
    st.write(f"Nachsteuerlicher Gewinn einbehalten: CHF {B['retained_after_tax']:,.0f}")
    st.caption(f"Teilbesteuerung Dividenden: Bund {int((DIV_PARTIAL_FED if qualifies_partial(share_pct) else 1.0)*100)}%, "
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
