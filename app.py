import streamlit as st
import json

# Steuerdaten aus JSON-Dateien laden
with open('Income_Tax_Confederation.json') as f:
    income_tax_confederation = json.load(f)
with open('Income_Tax_Cantons.json') as f:
    income_tax_cantons = json.load(f)
with open('Corporate_Income_Tax.json') as f:
    corporate_income_tax = json.load(f)
with open('Social_Security_Contributions.json') as f:
    social_security = json.load(f)
with open('Steuerfuesse.json') as f:
    steuerfuesse = json.load(f)
with open('Teilbesteuerung_Dividenden.json') as f:
    teilbesteuerung_dividenden = json.load(f)

# Progressiver Tarif direkte Bundessteuer (Einkommenssteuer Bund) vorbereiten
federal_tax_brackets = income_tax_confederation

# Tarif in zwei Listen für Alleinstehende vs. Verheiratete aufteilen (falls anwendbar)
federal_tax_single = []
federal_tax_married = []
sequence = []
for bracket in federal_tax_brackets:
    # Zweiten Tarif beginnen, wenn Schwellenwert auf 0 zurückgesetzt wird
    if bracket["Taxable income for federal tax"] == 0 and bracket["Base amount CHF"] == 0.0 and bracket["Additional %"] == 0.0 and sequence:
        # Ersten Tarif abgeschlossen, nun zweite Sequenz beginnen
        federal_tax_single = sequence
        sequence = []
        sequence.append(bracket)
    else:
        sequence.append(bracket)
if sequence:
    # Verbleibende Sequenz nach Schleife zuordnen
    if not federal_tax_single:
        federal_tax_single = sequence
    else:
        federal_tax_married = sequence

# Sicherstellen, dass beide Tarife belegt sind (falls nicht, denselben für beide nutzen)
if not federal_tax_married:
    federal_tax_married = federal_tax_single

# Funktion zur Berechnung der direkten Bundessteuer (Einkommenssteuer Bund)
def berechne_bundessteuer(einkommen, verheiratet=False):
    # Passenden Tarif auswählen (Alleinstehend oder Verheiratet)
    tarif = federal_tax_married if verheiratet else federal_tax_single
    steuer = 0.0
    # Progressive Berechnung gemäß Tarif
    # Spezialfall: Einkommen liegt unter erstem Schwellenwert des Tarifs
    if tarif and einkommen <= tarif[0]["Taxable income for federal tax"]:
        if not verheiratet:
            # Bei Alleinstehenden bleibt ein Grundfreibetrag (~15'200 CHF) steuerfrei
            if einkommen <= 15200:
                return 0.0
            # Einkommen zwischen 15'200 CHF und erstem Tarifwert mit niedrigem Satz besteuern
            steuer = 0.0
            if einkommen > 15200:
                obergrenze1 = min(einkommen, 32000)
                steuer += (obergrenze1 - 15200) * 0.0077  # ca. 0.77% bis 32'000 CHF
                if einkommen > 32000:
                    steuer += (min(einkommen, tarif[0]["Taxable income for federal tax"]) - 32000) * 0.01  # ca. 1.0% bis zum ersten Tarifwert
            return steuer
        else:
            # Bei Verheirateten: unter erstem Schwellenwert (z.B. ~29'300 CHF) keine Bundessteuer
            return 0.0
    # Passende Tarifstufe finden
    for bracket in tarif:
        threshold = bracket["Taxable income for federal tax"]
        if einkommen <= threshold:
            # Steuer = Basisbetrag + (Einkommen - Schwellenwert) * Zuschlag%
            steuer = bracket["Base amount CHF"] + (einkommen - threshold) * (bracket["Additional %"] / 100.0)
            break
    else:
        # Einkommen über letzter Stufe: letzte Tarifstufe anwenden
        bracket = tarif[-1]
        threshold = bracket["Taxable income for federal tax"]
        steuer = bracket["Base amount CHF"] + (einkommen - threshold) * (bracket["Additional %"] / 100.0)
    return max(steuer, 0.0)

# Funktion zur Berechnung der kantonalen Einkommenssteuer (inkl. Gemeindesteuer via Steuerfuss)
def berechne_kantonale_steuer(zumessbares_einkommen, canton_code):
    grundtarif = income_tax_cantons.get(canton_code)
    if not grundtarif:
        return 0.0
    basissteuer = 0.0
    rest = zumessbares_einkommen
    # Progressiven Kantons-/Gemeindetarif anwenden
    for abschnitt in grundtarif:
        betrag = abschnitt["For the next CHF"]
        satz = abschnitt["Additional %"] / 100.0
        if rest <= betrag:
            basissteuer += rest * satz
            rest = 0
            break
        else:
            basissteuer += betrag * satz
            rest -= betrag
    if rest > 0:
        basissteuer += rest * (grundtarif[-1]["Additional %"] / 100.0)
    # Steuerfuss von Kanton und Gemeinde berücksichtigen (sofern Daten vorhanden)
    steuerfuss_kanton = 1.0
    steuerfuss_gemeinde = 1.0
    if canton_code in steuerfuesse:
        sf = steuerfuesse[canton_code]
        if isinstance(sf, dict):
            if "income_canton" in sf:
                steuerfuss_kanton = 1 + sf["income_canton"] / 100.0
            if "income_commune" in sf:
                steuerfuss_gemeinde = 1 + sf["income_commune"] / 100.0
        elif isinstance(sf, (int, float)):
            steuerfuss_kanton = 1 + sf / 100.0
    return basissteuer * steuerfuss_kanton * steuerfuss_gemeinde

# Funktion zur Berechnung der Sozialversicherungsabgaben (AHV/IV/EO, ALV - ohne BVG)
def berechne_sozialabgaben(lohn):
    # AHV/IV/EO (je 5.3% AN + AG = 10.6% total)
    ahv_total = lohn * (social_security["AHV_IV_EO_EmployerShare"] + social_security["AHV_IV_EO_EmployeeShare"])
    # ALV (je 1.1% AN + AG = 2.2% total) bis zur Beitragsgrenze
    alv_total = min(lohn, social_security["ALV_Ceiling"]) * social_security["ALV_TotalRate"]
    # Ab 2025 kein Solidaritätsbeitrag über der ALV-Grenze mehr
    return ahv_total + alv_total

# Haupt-Anwendung
st.title("Vergleich: Lohn oder Dividende")
st.write("Diese Anwendung vergleicht die Nettobezüge, wenn ein Unternehmensgewinn entweder als Lohn ausbezahlt oder als Dividende ausgeschüttet wird.")

# Eingabeparameter
canton_codes = list(corporate_income_tax.keys())
canton = st.selectbox("Kanton auswählen", options=canton_codes, format_func=lambda x: x)
gewinn = st.number_input("Gewinn vor Steuern der Firma (CHF)", value=100000, step=1000)
lohn = st.slider("Geplanter Lohnanteil (CHF)", min_value=0, max_value=int(gewinn), value=int(gewinn/2), step=1000)
alter = st.number_input("Alter (für BVG-Berechnung)", value=30, min_value=18, max_value=70)
verheiratet = st.checkbox("Verheiratet (gemeinsame Veranlagung für Steuern)", value=False)

# Berechnung beider Szenarien
dividende = gewinn - lohn
# Körperschaftssteuer auf Gewinn (kantonal abhängig)
corp_tax_rate = corporate_income_tax.get(canton, 0)
unternehmenssteuer = max(gewinn - lohn, 0) * corp_tax_rate

# Sozialabgaben beim Lohn
sozialabgaben = berechne_sozialabgaben(lohn)

# Einkommenssteuer (privat) im Lohn-Szenario
lohn_bundessteuer = berechne_bundessteuer(lohn, verheiratet)
lohn_kantonsteuer = berechne_kantonale_steuer(lohn, canton)
gesamt_lohnsteuer = lohn_bundessteuer + lohn_kantonsteuer

# Einkommenssteuer (privat) im Dividenden-Szenario mit Teilbesteuerung
steuerbarer_anteil_dividend_bund = 0.7  # 70% der Dividende steuerbar für Bund
steuerbarer_anteil_dividend_kanton = teilbesteuerung_dividenden.get(canton, 0.5)
dividend_taxable_federal = dividende * steuerbarer_anteil_dividend_bund
dividend_taxable_cantonal = dividende * steuerbarer_anteil_dividend_kanton
dividende_bundessteuer = berechne_bundessteuer(dividend_taxable_federal, verheiratet)
dividende_kantonsteuer = berechne_kantonale_steuer(dividend_taxable_cantonal, canton)
gesamt_dividendensteuer = dividende_bundessteuer + dividende_kantonsteuer

# Nettoresultate
netto_lohn = gewinn - unternehmenssteuer - gesamt_lohnsteuer - sozialabgaben
netto_dividende = gewinn - unternehmenssteuer - gesamt_dividendensteuer

# Ausgabe der Ergebnisse
st.subheader("Ergebnisse")
col1, col2 = st.columns(2)
col1.metric("Netto bei 100% Lohn", f"{netto_lohn:,.0f} CHF")
col2.metric("Netto bei 100% Dividende", f"{netto_dividende:,.0f} CHF")
st.write(f"Gesamte Steuern und Abgaben (Lohnvariante): {gesamt_lohnsteuer + sozialabgaben:,.0f} CHF")
st.write(f"Gesamte Steuern (Dividendenvariante): {gesamt_dividendensteuer:,.0f} CHF")
