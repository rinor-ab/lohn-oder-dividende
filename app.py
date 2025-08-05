import streamlit as st
import pandas as pd
# ... (Lade- und Helferfunktionen aus der letzten Antwort bleiben unverändert)

st.title("Lohn- vs. Dividenden-Optimierer (Schweiz, 2025)")

# ─────────────── Benutzer­eingaben ──────────────────
kanton = st.selectbox("Kanton auswählen", mult["CantonCode"].unique())
gemeinden = mult.loc[mult["CantonCode"] == kanton]
gemeinde_name = st.selectbox("Gemeinde auswählen", gemeinden["CommuneName"])
gemeinde_id = gemeinden.loc[gemeinden["CommuneName"] == gemeinde_name,
                            "CommuneID"].iloc[0]

gewinn = st.number_input("Unternehmens­gewinn vor Lohn (CHF)",
                         min_value=0.0, step=1_000.0, format="%.0f")
lohn = st.number_input("Bruttolohn an Inhaber (CHF)",
                       min_value=0.0, step=1_000.0, format="%.0f")
alter = st.slider("Alter des Inhabers", 18, 65, 35)

# ─────────────── Berechnung: Lohnvariante ───────────
ee_soc, er_soc = social(lohn, alter)
steuerbarer_gewinn = gewinn - lohn - er_soc
körp_steuer_lohn = corporate_tax(steuerbarer_gewinn, kanton, gemeinde_id)
einkommenssteuer_lohn = personal_income_tax(lohn - ee_soc, kanton, gemeinde_id)
netto_lohn = lohn - ee_soc - einkommenssteuer_lohn

# ─────────────── Berechnung: Dividendenvariante ─────
körp_steuer_div = corporate_tax(gewinn, kanton, gemeinde_id)
dividende_brutto = gewinn - körp_steuer_div
steuerbarer_teil = dividende_brutto * partial_div[kanton]
einkommenssteuer_div = personal_income_tax(steuerbarer_teil, kanton, gemeinde_id)
netto_dividende = dividende_brutto - einkommenssteuer_div

# ─────────────── Ergebnisdarstellung ────────────────
ergebnis = pd.DataFrame({
    "Szenario": ["Lohn", "Dividende"],
    "Netto an Inhaber [CHF]": [netto_lohn, netto_dividende],
    "Unternehmens­steuern [CHF]": [körp_steuer_lohn, körp_steuer_div],
    "Einkommens­steuern [CHF]": [einkommenssteuer_lohn, einkommenssteuer_div],
    "Sozialabgaben (AG+AN) [CHF]": [ee_soc + er_soc, 0]
})
st.subheader("Ergebnis­übersicht")
st.dataframe(ergebnis.style.format({"Netto an Inhaber [CHF]": "CHF {:,.0f}",
                                    "Unternehmens­steuern [CHF]": "CHF {:,.0f}",
                                    "Einkommens­steuern [CHF]": "CHF {:,.0f}",
                                    "Sozialabgaben (AG+AN) [CHF]": "CHF {:,.0f}"}))

differenz = netto_lohn - netto_dividende
if differenz > 0:
    st.success(f"🏆 Die **Lohnvariante** bringt {differenz:,.0f} CHF mehr Netto.")
elif differenz < 0:
    st.success(f"🏆 Die **Dividendenvariante** bringt {abs(differenz):,.0f} CHF mehr Netto.")
else:
    st.info("⚖️ Beide Varianten liefern das gleiche Netto-Ergebnis.")
