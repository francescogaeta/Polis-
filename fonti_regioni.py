#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fonti_regioni.py — registro delle fonti usate per gli indicatori regionali.

QUESTO FILE È IL CANCELLO. Nessun ETL regionale scarica nulla da una fonte
che non sia registrata qui con `commerciale=True`. Polis avrà un modello di
ricavo: una licenza non commerciale (NC) o non verificata non può entrare
nell'app, e il modo più sicuro perché non entri per distrazione è che il
codice si rifiuti di scaricarla.

Tre stati possibili:
  commerciale=True   licenza verificata sul sito della fonte, uso commerciale
                     consentito con la sola attribuzione → ETL ammesso
  commerciale=None   licenza non nominata o non verificabile → ETL BLOCCATO
                     finché qualcuno non verifica e aggiorna questa riga
  commerciale=False  licenza incompatibile (NC, ND) → ETL vietato, per sempre

Ogni riga porta l'indirizzo della pagina dove la licenza è dichiarata, così
la verifica è rifacibile da chiunque senza fidarsi di questo commento.
"""

FONTI = {
    # ---------------------------------------------------------------- ammesse
    "istat": {
        "nome": "ISTAT",
        "licenza": "CC BY 3.0 IT",
        "commerciale": True,
        "verificata_su": "https://www.istat.it/informazioni-sul-copyright/",
        "nota": ("ISTAT dichiara che i dati possono essere copiati, distribuiti "
                 "e adattati liberamente, anche per fini commerciali, citando "
                 "la fonte."),
        "attribuzione": "Istat",
        "limite_s": 13.0,
        "limite_nota": ("5 query al minuto per IP dichiarate da ISTAT; oltre il "
                        "limite scatta un blocco di 1-2 giorni. 13 s fra le "
                        "richieste tiene circa 4,6 query/min."),
    },
    "salute": {
        "nome": "Ministero della Salute — dati aperti",
        "licenza": "IODL 2.0",
        "commerciale": True,
        "verificata_su": "https://www.dati.salute.gov.it/",
        "nota": "IODL 2.0 consente riuso e modifica anche a fini commerciali.",
        "attribuzione": "Ministero della Salute",
        "limite_s": 3.0,
        "limite_nota": "Nessun limite numerico pubblicato: 3 s di cortesia.",
    },
    "istruzione": {
        "nome": "Ministero dell'Istruzione e del Merito — dati aperti",
        "licenza": "IODL 2.0",
        "commerciale": True,
        "verificata_su": "https://dati.istruzione.it/opendata/",
        "nota": "Il portale dichiara il riuso anche a fini commerciali.",
        "attribuzione": "Ministero dell'Istruzione e del Merito",
        "limite_s": 3.0,
        "limite_nota": "Nessun limite numerico pubblicato: 3 s di cortesia.",
    },
    "aci": {
        "nome": "ACI — Automobile Club d'Italia",
        "licenza": "CC BY 4.0",
        "commerciale": True,
        "verificata_su": "https://aci.gov.it/attivita-e-progetti/studi-e-ricerche/open-data/",
        "nota": "ACI dichiara i dati statistici fruibili sotto CC BY 4.0.",
        "attribuzione": "ACI",
        "limite_s": 3.0,
        "limite_nota": "Nessun limite numerico pubblicato: 3 s di cortesia.",
    },
    "inps": {
        "nome": "INPS — Osservatori statistici",
        "licenza": "IODL",
        "commerciale": True,
        "verificata_su": "https://www.inps.it/it/it/dati-e-bilanci/open-data.html",
        "nota": ("Licenza IODL dichiarata sul portale. Verificare comunque la "
                 "scheda del singolo dataset prima di aggiungerlo."),
        "attribuzione": "INPS",
        "limite_s": 3.0,
        "limite_nota": "Nessun limite numerico pubblicato: 3 s di cortesia.",
    },
    "eurostat": {
        "nome": "Eurostat",
        "licenza": "Riuso autorizzato con attribuzione (Decisione 2011/833/UE)",
        "commerciale": True,
        "verificata_su": "https://ec.europa.eu/eurostat/about-us/policies/copyright",
        "nota": "Riuso libero, anche commerciale, citando la fonte.",
        "attribuzione": "Eurostat",
        "limite_s": 3.0,
        "limite_nota": "Nessun limite numerico pubblicato: 3 s di cortesia.",
    },

    # ------------------------------------------------- bloccate: da verificare
    "cpt": {
        "nome": "Conti Pubblici Territoriali (Dip. politiche di coesione)",
        "licenza": "non nominata",
        "commerciale": None,
        "verificata_su": "https://politichecoesione.governo.it/it/politica-di-coesione/"
                         "misurazione-valutazione-e-trasparenza/la-misurazione-delle-"
                         "politiche-di-coesione/conti-pubblici-territoriali-cpt/i-dati/",
        "nota": ("I file sono pubblicati in formato aperto ma la licenza non è "
                 "nominata. Serve conferma scritta prima dell'uso commerciale."),
        "attribuzione": "Conti Pubblici Territoriali",
        "limite_s": 2.0,
        "limite_nota": "File statici: 2 s di cortesia.",
    },
    "bdap": {
        "nome": "BDAP-MOP (RGS-MEF)",
        "licenza": "«licenza BDAP» (non standard)",
        "commerciale": None,
        "verificata_su": "https://bdap-opendata.rgs.mef.gov.it/",
        "nota": ("Il catalogo indica «LICENZA: BDAP», che non è una licenza "
                 "standard verificabile. Serve chiarimento da RGS."),
        "attribuzione": "BDAP-MOP · RGS-MEF",
        "limite_s": 2.0,
        "limite_nota": "File statici: 2 s di cortesia.",
    },
    "opencoesione": {
        "nome": "OpenCoesione",
        "licenza": "CC BY 4.0 secondo una pagina, CC BY-SA 3.0 secondo un'altra",
        "commerciale": None,
        "verificata_su": "https://opencoesione.gov.it/it/licenza/",
        "nota": ("Discrepanza fra la pagina «Licenza» (CC BY 4.0) e la pagina "
                 "API-FAQ (CC BY-SA 3.0). Entrambe permettono l'uso commerciale, "
                 "ma BY-SA impone il share-alike sui derivati: finché non è "
                 "chiarito, trattare come BY-SA e non integrare."),
        "attribuzione": "OpenCoesione",
        "limite_s": 5.0,
        "limite_nota": ("12 richieste al minuto per gli utenti anonimi, 60 per "
                        "quelli autenticati. 5 s tiene 12/min."),
    },
    "ispra": {
        "nome": "ISPRA / SNPA",
        "licenza": "non verificata",
        "commerciale": None,
        "verificata_su": "https://www.catasto-rifiuti.isprambiente.it/",
        "nota": "Dati aperti ma senza licenza standard dichiarata sul dataset.",
        "attribuzione": "ISPRA",
        "limite_s": 3.0,
        "limite_nota": "Nessun limite pubblicato: 3 s di cortesia.",
    },
    "inail": {
        "nome": "INAIL — open data",
        "licenza": "non verificata",
        "commerciale": None,
        "verificata_su": "https://dati.inail.it/",
        "nota": "Licenza standard non individuata sulle schede dataset.",
        "attribuzione": "INAIL",
        "limite_s": 3.0,
        "limite_nota": "Nessun limite pubblicato: 3 s di cortesia.",
    },
    "bancaditalia": {
        "nome": "Banca d'Italia — Base Dati Statistica",
        "licenza": "condizioni d'uso proprie, non verificate",
        "commerciale": None,
        "verificata_su": "https://infostat.bancaditalia.it/inquiry/",
        "nota": ("Applicazione di interrogazione, non un'API: automatizzarla "
                 "richiede comunque di verificare le condizioni d'uso."),
        "attribuzione": "Banca d'Italia",
        "limite_s": 5.0,
        "limite_nota": "Nessun limite pubblicato: 5 s di cortesia.",
    },
    "mef_finanze": {
        "nome": "MEF — Dipartimento delle Finanze",
        "licenza": "formato aperto, licenza non nominata",
        "commerciale": None,
        "verificata_su": "https://www.finanze.gov.it/it/statistiche-fiscali/",
        "nota": "Obbligo di citazione dichiarato, licenza specifica non nominata.",
        "attribuzione": "MEF — Dipartimento delle Finanze",
        "limite_s": 3.0,
        "limite_nota": "File statici: 3 s di cortesia.",
    },
    "italiadomani": {
        "nome": "Italia Domani (PNRR)",
        "licenza": "non verificata",
        "commerciale": None,
        "verificata_su": "https://www.italiadomani.gov.it/content/sogei-ng/it/it/catalogo-open-data.html",
        "nota": "Catalogo aperto ma licenza non dichiarata in modo verificabile.",
        "attribuzione": "Italia Domani",
        "limite_s": 3.0,
        "limite_nota": "File statici: 3 s di cortesia.",
    },

    # ------------------------------------------------------ vietate: licenza NC
    "openpolis": {
        "nome": "Openpolis",
        "licenza": "CC BY-NC-SA",
        "commerciale": False,
        "verificata_su": "https://www.openpolis.it/",
        "nota": "La clausola NC vieta l'uso commerciale. Esclusa in modo definitivo.",
        "attribuzione": "Openpolis",
        "limite_s": 5.0,
        "limite_nota": "—",
    },
    "cnr": {
        "nome": "CNR — data.cnr.it",
        "licenza": "CC BY-NC-ND 3.0",
        "commerciale": False,
        "verificata_su": "https://data.cnr.it/",
        "nota": "NC e ND: né uso commerciale né opere derivate.",
        "attribuzione": "CNR",
        "limite_s": 5.0,
        "limite_nota": "—",
    },
    "opencivitas": {
        "nome": "OpenCivitas (SOSE-MEF)",
        "licenza": "non verificata, accesso al dettaglio con credenziali",
        "commerciale": None,
        "verificata_su": "https://www.opencivitas.it/",
        "nota": ("Copre solo le Regioni a Statuto Ordinario: inadatta a un "
                 "confronto uniforme fra le 20 regioni, a prescindere dalla "
                 "licenza."),
        "attribuzione": "OpenCivitas — SOSE/MEF",
        "limite_s": 5.0,
        "limite_nota": "—",
    },
}


class FonteNonAmmessa(Exception):
    """La fonte non è utilizzabile per un'app commerciale."""


def scheda(chiave):
    f = FONTI.get(chiave)
    if f is None:
        raise FonteNonAmmessa(
            "Fonte «%s» non registrata in fonti_regioni.py. Registrala con la "
            "sua licenza verificata prima di usarla." % chiave)
    return f


def consentita(chiave):
    """True solo se la licenza è verificata E consente l'uso commerciale."""
    return scheda(chiave).get("commerciale") is True


def pretendi(chiave):
    """Da chiamare in testa a ogni ETL. Interrompe se la fonte non è ammessa."""
    f = scheda(chiave)
    stato = f.get("commerciale")
    if stato is True:
        return f
    if stato is False:
        raise FonteNonAmmessa(
            "%s ha licenza %s: l'uso commerciale è vietato. Questo ETL non "
            "deve esistere." % (f["nome"], f["licenza"]))
    raise FonteNonAmmessa(
        "%s: licenza «%s» non verificata per l'uso commerciale.\n"
        "  Motivo: %s\n"
        "  Da controllare su: %s\n"
        "  Finché la riga in fonti_regioni.py non viene aggiornata con "
        "commerciale=True, questo ETL non scarica nulla."
        % (f["nome"], f["licenza"], f["nota"], f["verificata_su"]))


def limiti_per_client():
    """Restituisce {chiave: secondi} da innestare in lib_fonti.LIMITI."""
    return {k: v.get("limite_s", 3.0) for k, v in FONTI.items()}


_TUTTE = object()


def elenco(stato=_TUTTE):
    """Elenco leggibile delle fonti, eventualmente filtrato per stato.
    `stato` può valere True, False, None (da verificare) oppure essere
    omesso per avere tutte le fonti. None è uno stato vero e proprio, non
    l'assenza di filtro: per questo serve un valore sentinella."""
    out = []
    for k, f in sorted(FONTI.items()):
        if stato is not _TUTTE and f.get("commerciale") is not stato:
            continue
        out.append((k, f["nome"], f["licenza"], f.get("commerciale")))
    return out


def main():
    def riga(k, nome, lic, st):
        segno = {True: "ammessa    ", False: "VIETATA    ",
                 None: "da verificare"}[st]
        print("  %-14s %-13s %-46s %s" % (k, segno, nome[:46], lic))
    print("Fonti registrate per gli indicatori regionali\n")
    print("AMMESSE (licenza verificata, uso commerciale consentito)")
    for r in elenco(True):
        riga(*r)
    print("\nBLOCCATE (licenza non verificata: nessun download)")
    for r in elenco(None):
        riga(*r)
    print("\nVIETATE (licenza incompatibile)")
    for r in elenco(False):
        riga(*r)
    print("\nOgni riga riporta l'indirizzo dove la licenza è dichiarata: la "
          "verifica è rifacibile da chiunque.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
