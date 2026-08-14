#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_classifica_comuni.py — costruisce il confronto fra i COMUNI a partire dai
file già scaricati da Cruscotto Italia. Non tocca la rete.

COSA CAMBIA RISPETTO ALLA VERSIONE PRECEDENTE
---------------------------------------------
1. Da 11 a 28 indicatori, raccolti in NOVE FRONTI (ambiente, lavoro e redditi,
   salute e servizi, digitale, istruzione, turismo e cultura, mobilità, conti
   del Comune, popolazione). I dati erano già dentro i file scaricati: prima
   ne veniva usata meno della metà.
2. Ogni indicatore dichiara il VERSO: se è meglio in alto (differenziata),
   meglio in basso (PM10, rifiuti, disoccupazione) o se non esiste un verso
   migliore (età media, spesa per abitante). Prima si ordinava sempre in
   discesa, e "primo in rifiuti prodotti" sembrava un merito.
3. Ogni indicatore cita la FONTE A MONTE verificata (ISPRA, MEF, ACI, MIUR,
   Ministero della Salute, AGCOM, GSE, RUNTS, MiC, ISTAT, MEF-RGS SIOPE),
   non solo l'intermediario Cruscotto Italia.
4. Blocco `regioni`: ogni comune è ricollegato alla sua regione, con quanti
   comuni sono in archivio e quanti abitanti rappresentano. Serve a filtrare
   il confronto per regione e a mostrare la crescita dell'archivio giorno
   dopo giorno.

QUELLO CHE QUESTO SCRIPT NON FA, E PERCHÉ
-----------------------------------------
Non calcola MEDIE O CLASSIFICHE REGIONALI dai comuni scaricati.
Le regole d'uso di Cruscotto Italia lo vietano espressamente: i dati sono
pubblicati comune per comune, non esistono aggregati territoriali, e
ricostruirli scaricando i comuni di un territorio non è consentito. Sarebbe
anche falso: i comuni in archivio sono soprattutto capoluoghi, e un capoluogo
non rappresenta la sua regione.

La regione qui è quindi un FILTRO e un'ETICHETTA, mai il soggetto del
confronto. Gli aggregati regionali veri restano quelli delle fonti che le
regioni le pubblicano davvero (CPT, BDAP, ISTAT, Ministero della Salute).

PRO CAPITE
----------
Dove il confronto dipende dalla dimensione (spesa, posti letto, strutture) il
valore è rapportato agli abitanti. In valore assoluto vincerebbe sempre il
comune più grande, e non direbbe nulla.

Uso:
  python3 etl_classifica_comuni.py --dir ../data/comuni
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone


# Fronti del confronto: chiave, nome mostrato, icona.
FRONTI = [
    ("ambiente",   "Ambiente e aria",      "🌿"),
    ("lavoro",     "Lavoro e redditi",     "💼"),
    ("salute",     "Salute e servizi",     "🏥"),
    ("digitale",   "Digitale",             "📶"),
    ("istruzione", "Istruzione",           "🎓"),
    ("cultura",    "Turismo e cultura",    "🎭"),
    ("mobilita",   "Mobilità",             "🚗"),
    ("conti",      "Conti del Comune",     "🏛️"),
    ("popolazione","Popolazione",          "👥"),
    ("fondi",      "Fondi e opere",        "🏗️"),
]

# Indicatori.
#   chiave, fronte, titolo, percorso nel file, unità, per_abitante,
#   verso ('alto' = meglio in alto, 'basso' = meglio in basso,
#          'neutro' = non esiste un verso migliore),
#   fonte a monte, percorso dell'anno, impatto per il cittadino.
#
# per_abitante: False | True (valore/abitanti) | 1000 (valore ogni 1.000 ab)
INDICATORI = [
    # ---- Ambiente e aria (ISPRA) ----
    ("differenziata", "ambiente", "Raccolta differenziata",
     ("kpi", "ambiente", "raccolta_differenziata_pct"), "%", False, "alto",
     "ISPRA (via Cruscotto Italia · AgID)", None,
     "Quanta parte dei rifiuti viene raccolta in modo differenziato."),
    ("rifiuti", "ambiente", "Rifiuti prodotti",
     ("kpi", "ambiente", "rifiuti_kg_per_abitante"), "kg per abitante", False, "basso",
     "ISPRA (via Cruscotto Italia · AgID)", None,
     "Quanti rifiuti produce in un anno ogni abitante."),
    ("consumo_suolo", "ambiente", "Suolo consumato",
     ("kpi", "ambiente", "consumo_suolo_pct"), "%", False, "basso",
     "ISPRA (via Cruscotto Italia · AgID)", None,
     "Quanta parte del territorio è coperta da cemento e asfalto."),
    ("pm10", "ambiente", "Polveri sottili PM10",
     ("kpi", "aria", "pm10_media"), "µg/m³", False, "basso",
     "ISPRA SNPA (via Cruscotto Italia · AgID)", ("kpi", "aria", "anno"),
     "La media annua delle polveri sottili nell'aria che respiri."),
    ("no2", "ambiente", "Biossido di azoto NO₂",
     ("kpi", "aria", "no2_media"), "µg/m³", False, "basso",
     "ISPRA SNPA (via Cruscotto Italia · AgID)", ("kpi", "aria", "anno"),
     "L'inquinante legato soprattutto al traffico."),

    # ---- Lavoro e redditi ----
    ("occupazione", "lavoro", "Tasso di occupazione",
     ("kpi", "lavoro", "tasso_occupazione"), "%", False, "alto",
     "ISTAT — Censimento permanente (via Cruscotto Italia · AgID)", ("kpi", "lavoro", "anno"),
     "Quanta parte delle persone in età da lavoro ha un impiego."),
    ("disoccupazione", "lavoro", "Tasso di disoccupazione",
     ("kpi", "lavoro", "tasso_disoccupazione"), "%", False, "basso",
     "ISTAT — Censimento permanente (via Cruscotto Italia · AgID)", ("kpi", "lavoro", "anno"),
     "Quante persone cercano lavoro senza trovarlo."),
    ("reddito", "lavoro", "Reddito medio dichiarato",
     ("kpi", "redditi", "reddito_medio_eur"), "euro", False, "alto",
     "MEF — Dipartimento delle Finanze (via Cruscotto Italia · AgID)",
     ("kpi", "redditi", "anno_fiscale"),
     "Il reddito medio dichiarato al fisco nel comune."),
    ("imposta", "lavoro", "Imposta netta media",
     ("kpi", "redditi", "imposta_netta_media_eur"), "euro", False, "neutro",
     "MEF — Dipartimento delle Finanze (via Cruscotto Italia · AgID)",
     ("kpi", "redditi", "anno_fiscale"),
     "Quanta imposta paga in media chi dichiara un reddito qui."),
    ("imprese", "lavoro", "Imprese attive",
     ("kpi", "imprese", "ul_per_1000_ab"), "unità locali ogni 1.000 abitanti", False, "alto",
     "ISTAT ASIA (via Cruscotto Italia · AgID)", ("kpi", "imprese", "anno"),
     "Quanto è fitto il tessuto di attività economiche."),
    ("addetti_ul", "lavoro", "Dimensione media delle imprese",
     ("kpi", "imprese", "addetti_per_ul"), "addetti per unità locale", False, "neutro",
     "ISTAT ASIA (via Cruscotto Italia · AgID)", ("kpi", "imprese", "anno"),
     "Se prevalgono grandi aziende o piccole attività."),

    # ---- Salute e servizi (Ministero della Salute, RUNTS) ----
    ("farmacie", "salute", "Farmacie",
     ("kpi", "sanita", "farmacie_per_1000_ab"), "farmacie ogni 1.000 abitanti", False, "alto",
     "Ministero della Salute (via Cruscotto Italia · AgID)", None,
     "Quanto è facile trovare una farmacia vicino a casa."),
    ("posti_letto", "salute", "Posti letto ospedalieri",
     ("kpi", "sanita", "posti_letto_ospedalieri"), "posti letto", 1000, "alto",
     "Ministero della Salute (via Cruscotto Italia · AgID)", None,
     "Quanti posti letto ci sono negli ospedali del comune."),
    ("ospedali", "salute", "Ospedali",
     ("kpi", "sanita", "n_ospedali"), "ospedali", False, "alto",
     "Ministero della Salute (via Cruscotto Italia · AgID)", None,
     "Quante strutture ospedaliere hanno sede nel comune."),
    ("terzo_settore", "salute", "Enti del terzo settore",
     ("kpi", "terzo_settore", "enti_per_1000_ab"), "enti ogni 1.000 abitanti", False, "alto",
     "RUNTS (via Cruscotto Italia · AgID)", None,
     "Quante associazioni e enti no profit operano sul territorio."),

    # ---- Digitale (AGCOM, GSE) ----
    ("ftth", "digitale", "Copertura in fibra (FTTH)",
     ("kpi", "banda_larga", "copertura_ftth_pct"), "%", False, "alto",
     "AGCOM (via Cruscotto Italia · AgID)", None,
     "Quante case possono attivare la connessione in fibra."),
    ("ricarica", "digitale", "Punti di ricarica elettrica",
     ("kpi", "ricarica_ev", "punti_per_1000_ab"), "punti ogni 1.000 abitanti", False, "alto",
     "GSE (via Cruscotto Italia · AgID)", None,
     "Quanti punti di ricarica per auto elettriche sono disponibili."),

    # ---- Istruzione (ISTAT, MIUR) ----
    ("terziario", "istruzione", "Istruzione terziaria",
     ("kpi", "istruzione", "pct_terziario"), "%", False, "alto",
     "ISTAT — Censimento permanente (via Cruscotto Italia · AgID)",
     ("kpi", "istruzione", "anno"),
     "Quante persone hanno una laurea o un titolo superiore."),
    ("diploma", "istruzione", "Diploma o titolo superiore",
     ("kpi", "istruzione", "pct_diploma_oltre"), "%", False, "alto",
     "ISTAT — Censimento permanente (via Cruscotto Italia · AgID)",
     ("kpi", "istruzione", "anno"),
     "Quante persone hanno almeno il diploma."),
    ("scuole", "istruzione", "Scuole",
     ("kpi", "scuole", "scuole_per_1000_ab"), "scuole ogni 1.000 abitanti", False, "alto",
     "MIUR (via Cruscotto Italia · AgID)", ("kpi", "scuole", "anno_scolastico"),
     "Quanti plessi scolastici ci sono rispetto agli abitanti."),

    # ---- Turismo e cultura (ISTAT, MiC) ----
    ("turisticita", "cultura", "Intensità turistica",
     ("kpi", "turismo", "indice_turisticita_per_100ab"), "posti letto ogni 100 abitanti",
     False, "neutro", "ISTAT (via Cruscotto Italia · AgID)", ("kpi", "turismo", "anno"),
     "Quanto pesa il turismo rispetto a chi ci vive."),
    ("strutture", "cultura", "Strutture ricettive",
     ("kpi", "turismo", "totale_strutture"), "strutture", 1000, "neutro",
     "ISTAT (via Cruscotto Italia · AgID)", ("kpi", "turismo", "anno"),
     "Quanti alberghi, B&B e affitti brevi sono registrati."),
    ("beni_culturali", "cultura", "Luoghi della cultura",
     ("kpi", "beni_culturali", "beni_per_1000_ab"), "beni ogni 1.000 abitanti", False, "alto",
     "MiC — ArCo e Cultural-ON (via Cruscotto Italia · AgID)", None,
     "Quanti musei, chiese e monumenti censiti ci sono."),

    # ---- Mobilità (ACI, ISTAT) ----
    ("motorizzazione", "mobilita", "Auto e veicoli",
     ("kpi", "veicoli", "tasso_motorizzazione_per_1000_ab"), "veicoli ogni 1.000 abitanti",
     False, "neutro", "ACI (via Cruscotto Italia · AgID)", ("kpi", "veicoli", "anno"),
     "Quanti veicoli circolano rispetto agli abitanti."),
    ("veicoli_inquinanti", "mobilita", "Veicoli più inquinanti",
     ("kpi", "veicoli", "pct_inquinanti"), "%", False, "basso",
     "ACI (via Cruscotto Italia · AgID)", ("kpi", "veicoli", "anno"),
     "Quanta parte del parco veicoli è nelle classi Euro più vecchie."),
    ("auto_contenimento", "mobilita", "Chi lavora dove vive",
     ("kpi", "pendolarismo", "auto_contenimento_pct"), "%", False, "neutro",
     "ISTAT — Censimento 2021 (via Cruscotto Italia · AgID)", ("kpi", "pendolarismo", "anno"),
     "Quanta parte di chi vive qui non deve spostarsi per lavoro o studio."),

    # ---- Conti del Comune (MEF-RGS SIOPE) ----
    ("uscite_ab", "conti", "Spesa del Comune per abitante",
     ("kpi", "siope", "uscite_per_abitante_eur"), "euro per abitante", False, "neutro",
     "MEF-RGS · SIOPE (via Cruscotto Italia · AgID)", ("kpi", "siope", "anno"),
     "Quanto spende in un anno l'amministrazione per ogni abitante."),
    ("incassi_ab", "conti", "Incassi del Comune per abitante",
     ("kpi", "siope", "incassi_per_abitante_eur"), "euro per abitante", False, "neutro",
     "MEF-RGS · SIOPE (via Cruscotto Italia · AgID)", ("kpi", "siope", "anno"),
     "Quante risorse entrano nelle casse comunali per ogni abitante."),
    ("saldo_cassa", "conti", "Saldo di cassa",
     ("kpi", "siope", "saldo_cassa_eur"), "euro", True, "neutro",
     "MEF-RGS · SIOPE (via Cruscotto Italia · AgID)", ("kpi", "siope", "anno"),
     "Differenza fra incassi e pagamenti nell'anno, per abitante."),

    # ---- Popolazione (ISTAT) ----
    ("popolazione", "popolazione", "Abitanti",
     ("kpi", "demografia", "popolazione"), "abitanti", False, "neutro",
     "ISTAT POSAS (via Cruscotto Italia · AgID)", ("kpi", "demografia", "riferimento"),
     "Quante persone risiedono nel comune."),
    ("eta_media", "popolazione", "Età media",
     ("kpi", "demografia", "eta_media"), "anni", False, "neutro",
     "ISTAT POSAS (via Cruscotto Italia · AgID)", ("kpi", "demografia", "riferimento"),
     "L'età media di chi vive nel comune."),
    ("vecchiaia", "popolazione", "Indice di vecchiaia",
     ("kpi", "demografia", "indice_vecchiaia"), "anziani ogni 100 giovani", False, "neutro",
     "ISTAT POSAS (via Cruscotto Italia · AgID)", ("kpi", "demografia", "riferimento"),
     "Quanti over 65 ci sono ogni 100 ragazzi sotto i 15 anni."),
    ("dipendenza", "popolazione", "Indice di dipendenza",
     ("kpi", "demografia", "indice_dipendenza"), "%", False, "neutro",
     "ISTAT POSAS (via Cruscotto Italia · AgID)", ("kpi", "demografia", "riferimento"),
     "Quante persone non in età da lavoro ci sono ogni 100 che lo sono."),

    # ---- Fondi e opere (presenti solo in alcuni comuni) ----
    ("pnrr_importo", "fondi", "PNRR · fondi assegnati",
     ("kpi", "pnrr", "importo_assegnato_eur"), "euro", True, "alto",
     "ReGiS / OpenPNRR (via Cruscotto Italia · AgID)", None,
     "Quanti soldi del Piano di ripresa arrivano dove vivi."),
    ("opere_importo", "fondi", "Opere pubbliche · valore",
     ("kpi", "opere_bdap", "importo_totale_eur"), "euro", True, "neutro",
     "BDAP-MOP (via Cruscotto Italia · AgID)", None,
     "Il valore dei cantieri pubblici censiti nel comune."),
    ("appalti_importo", "fondi", "Appalti · importo",
     ("kpi", "contratti_anac", "importo_totale_eur"), "euro", True, "neutro",
     "ANAC (via Cruscotto Italia · AgID)", None,
     "Quanto il Comune affida con gare d'appalto."),
]


def dentro(rec, percorso):
    """Segue un percorso dentro il file, tollerando i pezzi mancanti."""
    v = rec
    for p in percorso:
        if not isinstance(v, dict):
            return None
        v = v.get(p)
    return v


def numero(v):
    if isinstance(v, bool):
        return None
    return v if isinstance(v, (int, float)) else None


def leggi_comuni(cartella):
    comuni = []
    for nome_file in sorted(os.listdir(cartella)):
        if not nome_file.endswith(".json") or nome_file.startswith("_"):
            continue
        if nome_file == "index.json":
            continue
        try:
            with open(os.path.join(cartella, nome_file), encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            continue
        if not isinstance(rec, dict) or not rec.get("kpi"):
            continue
        kpi = rec["kpi"]
        ana = kpi.get("anagrafica") or {}
        pop = numero((kpi.get("demografia") or {}).get("popolazione"))
        comuni.append({
            "istat": rec.get("istat") or nome_file[:-5],
            "nome": rec.get("nome") or ana.get("nome") or "",
            "regione": rec.get("regione") or ana.get("regione") or "",
            "provincia": rec.get("provincia") or ana.get("provincia") or "",
            "pop": pop,
            "aggiornato": rec.get("_fetched_at") or rec.get("_source_generated_at"),
            "rec": rec,
        })
    return comuni


def costruisci_indicatori(comuni):
    indicatori = []
    for (chiave, fronte, titolo, percorso, unita, per_ab, verso,
         fonte, percorso_anno, impatto) in INDICATORI:
        valori = []
        anni = []
        for c in comuni:
            v = numero(dentro(c["rec"], percorso))
            if v is None:
                continue
            if per_ab:
                if not c["pop"]:
                    continue                     # niente popolazione, niente rapporto
                fattore = 1000 if per_ab == 1000 else 1
                v = v / c["pop"] * fattore
                v = round(v, 2 if abs(v) >= 1 else 4)
            valori.append({"istat": c["istat"], "nome": c["nome"],
                           "regione": c["regione"], "valore": v})
            if percorso_anno:
                a = dentro(c["rec"], percorso_anno)
                if a is not None:
                    anni.append(str(a))

        if len(valori) < 2:
            continue                             # con un solo comune non è un confronto

        # l'ordinamento segue il verso dichiarato: primo = migliore, dove
        # "migliore" ha un senso; altrimenti solo dal più alto al più basso
        valori.sort(key=lambda x: x["valore"], reverse=(verso != "basso"))

        limiti = []
        if len(valori) < len(comuni):
            limiti.append("Dato presente per %d comuni sui %d in archivio: il "
                          "confronto è parziale e cresce a ogni aggiornamento."
                          % (len(valori), len(comuni)))
        if per_ab:
            limiti.append("Valore rapportato agli abitanti: in valore assoluto "
                          "vincerebbe sempre il comune più grande.")
        if verso == "neutro":
            limiti.append("Per questo indicatore non esiste un valore "
                          "«migliore»: l'ordine va dal più alto al più basso, "
                          "non dal migliore al peggiore.")
        limiti.append("Si confrontano COMUNI fra loro, in gran parte capoluoghi. "
                      "La regione è indicata per orientarsi, non è il soggetto "
                      "del confronto.")

        # l'unità mostrata deve dire che il valore è un rapporto, altrimenti
        # "posti letto: 10,99" sembra il numero di letti dell'ospedale
        unita_mostrata = unita
        if per_ab == 1000 and "ogni" not in unita:
            unita_mostrata = "%s ogni 1.000 abitanti" % unita
        elif per_ab is True and "abitante" not in unita:
            unita_mostrata = "%s per abitante" % unita

        anni_unici = sorted(set(anni))
        indicatori.append({
            "chiave": chiave,
            "fronte": fronte,
            "titolo": titolo,
            "impatto_cittadino": impatto,
            "unita": unita_mostrata,
            "unita_procapite": unita_mostrata,
            "unita_base": unita,
            "verso": verso,
            "per_abitante": bool(per_ab),
            "fonte": fonte,
            "anno": anni_unici[0] if len(anni_unici) == 1 else
                    ("%s–%s" % (anni_unici[0], anni_unici[-1]) if anni_unici else None),
            "limiti": limiti,
            "n_regioni": len(valori),            # riusa il campo già letto dall'app
            "n_comuni": len(valori),
            "completo": len(valori) == len(comuni),
            "assoluto": valori[:120],
            "procapite": [],                     # il rapporto è già applicato sopra
        })
    return indicatori


def costruisci_regioni(comuni):
    """Ricollega ogni comune alla sua regione.
    Sono CONTEGGI, non medie: quanti comuni abbiamo e quanti abitanti
    rappresentano. Nessun aggregato regionale viene calcolato."""
    reg = {}
    for c in comuni:
        nome = c["regione"] or "(non indicata)"
        r = reg.setdefault(nome, {"n_comuni": 0, "abitanti_in_archivio": 0,
                                  "comuni": []})
        r["n_comuni"] += 1
        if c["pop"]:
            r["abitanti_in_archivio"] += int(c["pop"])
        r["comuni"].append({"istat": c["istat"], "nome": c["nome"],
                            "provincia": c["provincia"], "pop": c["pop"]})
    for r in reg.values():
        r["comuni"].sort(key=lambda x: -(x["pop"] or 0))
    return dict(sorted(reg.items(), key=lambda kv: (-kv[1]["n_comuni"], kv[0])))


def main():
    ap = argparse.ArgumentParser(
        description="Confronto fra i comuni dai file già scaricati")
    ap.add_argument("--dir", default="../data/comuni")
    ap.add_argument("--out", help="file di uscita (default: <dir>/../classifica_comuni.json)")
    args = ap.parse_args()

    if not os.path.isdir(args.dir):
        print("Cartella inesistente: %s" % os.path.abspath(args.dir))
        return 1

    comuni = leggi_comuni(args.dir)
    if not comuni:
        print("Nessun file di comune valido in %s" % os.path.abspath(args.dir))
        print("Esegui prima l'ETL dei comuni.")
        return 1

    print("Comuni in archivio: %d" % len(comuni))
    senza_pop = sum(1 for c in comuni if not c["pop"])
    if senza_pop:
        print("  senza popolazione (niente rapporto per abitante): %d" % senza_pop)

    indicatori = costruisci_indicatori(comuni)
    regioni = costruisci_regioni(comuni)

    fronti_usati = []
    for chiave, nome, icona in FRONTI:
        n = sum(1 for i in indicatori if i["fronte"] == chiave)
        if n:
            fronti_usati.append({"chiave": chiave, "nome": nome,
                                 "icona": icona, "n_indicatori": n})

    destinazione = args.out or os.path.join(
        os.path.dirname(os.path.abspath(args.dir.rstrip("/"))),
        "classifica_comuni.json")
    os.makedirs(os.path.dirname(destinazione), exist_ok=True)
    with open(destinazione, "w", encoding="utf-8") as f:
        json.dump({
            "_generato": datetime.now(timezone.utc).isoformat(),
            "aggiornato": datetime.now(timezone.utc).strftime("%d/%m/%Y"),
            "fonte": "Cruscotto Italia · AgID (dati.gov.it)",
            "_soggetto": "comuni",
            "_versione": 2,
            "_avvertenza": (
                "Questo confronto mette a paragone i COMUNI presenti in "
                "archivio, in gran parte capoluoghi. Non è una classifica "
                "delle regioni: un capoluogo non rappresenta la sua regione, "
                "e Cruscotto Italia non pubblica aggregati regionali. "
                "L'archivio cresce a ogni aggiornamento."),
            "n_comuni": len(comuni),
            "fronti": fronti_usati,
            "regioni": regioni,
            "copertura_regionale": {k: v["n_comuni"] for k, v in regioni.items()},
            "indicatori": indicatori,
        }, f, ensure_ascii=False, separators=(",", ":"))

    print("\n=== confronto fra i comuni ===")
    print("  fronti attivi: %d" % len(fronti_usati))
    for fr in fronti_usati:
        print("    %s %-22s %2d indicatori" % (fr["icona"], fr["nome"], fr["n_indicatori"]))
    print("  indicatori costruiti: %d" % len(indicatori))
    print("  regioni rappresentate: %d" % len(regioni))
    for nome, r in regioni.items():
        print("    %-22s %2d comuni · %s abitanti in archivio"
              % (nome, r["n_comuni"], format(r["abitanti_in_archivio"], ",d").replace(",", ".")))
    print("  scritto: %s" % destinazione)
    return 0


if __name__ == "__main__":
    sys.exit(main())
