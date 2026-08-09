#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_classifica_comuni.py — costruisce la classifica dei COMUNI a partire dai
file già scaricati da Cruscotto Italia. Non tocca la rete.

UNA PRECISAZIONE CHE CAMBIA TUTTO
---------------------------------
I file che l'ETL scarica sono quelli dei CAPOLUOGHI, non delle intere
regioni. Sommare i dati di Milano e chiamarli "Lombardia" sarebbe falso:
Milano non è la Lombardia, è il 13% dei suoi abitanti.

Perciò qui si costruisce una classifica DEI COMUNI, dichiarata come tale.
La regione compare solo come etichetta ("capoluogo della Lombardia"), mai
come soggetto del confronto. Gli aggregati regionali veri restano quelli di
CPT e BDAP, che le regioni le pubblicano davvero.

Le regole d'uso di Cruscotto Italia vietano esplicitamente di ricostruire
medie e classifiche territoriali scaricando tutti i comuni di un territorio:
questo script non lo fa, usa solo ciò che è già stato scaricato un lotto
alla volta e confronta i comuni fra loro, che è un'altra cosa.

PRO CAPITE
----------
Dove il confronto dipende dalla dimensione (spesa, opere, appalti) la
classifica è per abitante. I valori assoluti restano visibili accanto, ma
non sono la vista principale: altrimenti si misura solo quanto è grande
un comune.

Uso:
  python3 etl_classifica_comuni.py --dir ../data/comuni
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone


# (chiave, titolo, percorso nel file, unità, per abitante?, impatto)
INDICATORI = [
    ("pnrr_importo", "PNRR · fondi assegnati",
     ("kpi", "pnrr", "importo_assegnato_eur"), "euro", True,
     "Quanti soldi del Piano di ripresa arrivano dove vivi."),
    ("opere_importo", "Opere pubbliche · valore",
     ("kpi", "opere_bdap", "importo_totale_eur"), "euro", True,
     "Il valore dei cantieri pubblici nel tuo comune."),
    ("appalti_importo", "Appalti · importo",
     ("kpi", "contratti_anac", "importo_totale_eur"), "euro", True,
     "Quanto il tuo Comune affida con gare d'appalto."),
    ("spesa_siope", "Spesa del Comune",
     ("kpi", "siope", "totale_uscite_eur"), "euro", True,
     "Quanto spende in un anno l'amministrazione comunale."),
    ("differenziata", "Raccolta differenziata",
     ("kpi", "ambiente", "raccolta_differenziata_pct"), "%", False,
     "Quanta parte dei rifiuti viene differenziata."),
    ("rifiuti", "Rifiuti prodotti",
     ("kpi", "ambiente", "rifiuti_kg_per_abitante"), "kg per abitante", False,
     "Quanti rifiuti produce ogni abitante in un anno."),
    ("ftth", "Copertura in fibra",
     ("kpi", "banda_larga", "copertura_ftth_pct"), "%", False,
     "Quante case possono avere la connessione veloce."),
    ("reddito", "Reddito medio dichiarato",
     ("kpi", "redditi", "reddito_medio_eur"), "euro", False,
     "Il reddito medio dichiarato al fisco nel tuo comune."),
    ("occupazione", "Tasso di occupazione",
     ("kpi", "lavoro", "tasso_occupazione"), "%", False,
     "Quanta parte delle persone in età da lavoro ha un impiego."),
    ("farmacie", "Farmacie",
     ("kpi", "sanita", "n_farmacie"), "farmacie", True,
     "Quante farmacie ci sono rispetto agli abitanti."),
    ("verde_ev", "Punti di ricarica elettrica",
     ("kpi", "ricarica_ev", "n_totale"), "punti", True,
     "Quanti punti di ricarica per auto elettriche."),
]


def dentro(rec, percorso):
    """Segue un percorso dentro il file, tollerando i pezzi mancanti."""
    v = rec
    for p in percorso:
        if not isinstance(v, dict):
            return None
        v = v.get(p)
    return v if isinstance(v, (int, float)) else None


def main():
    ap = argparse.ArgumentParser(
        description="Classifica dei comuni dai file già scaricati")
    ap.add_argument("--dir", default="../data/comuni")
    ap.add_argument("--out", help="file di uscita (default: <dir>/../classifica_comuni.json)")
    args = ap.parse_args()

    if not os.path.isdir(args.dir):
        print("Cartella inesistente: %s" % os.path.abspath(args.dir))
        return 1

    comuni = []
    for nome_file in sorted(os.listdir(args.dir)):
        if not nome_file.endswith(".json") or nome_file.startswith("_"):
            continue
        if nome_file == "index.json":
            continue
        try:
            with open(os.path.join(args.dir, nome_file), encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            continue
        if not isinstance(rec, dict) or not rec.get("kpi"):
            continue
        kpi = rec["kpi"]
        ana = kpi.get("anagrafica") or {}
        pop = (kpi.get("demografia") or {}).get("popolazione")
        comuni.append({
            "istat": rec.get("istat") or nome_file[:-5],
            "nome": rec.get("nome") or ana.get("nome") or "",
            "regione": rec.get("regione") or ana.get("regione") or "",
            "pop": pop if isinstance(pop, (int, float)) else None,
            "rec": rec,
        })

    if not comuni:
        print("Nessun file di comune valido in %s" % os.path.abspath(args.dir))
        print("Esegui prima l'ETL dei comuni.")
        return 1

    print("Comuni in archivio: %d" % len(comuni))
    senza_pop = sum(1 for c in comuni if not c["pop"])
    if senza_pop:
        print("  senza popolazione (niente pro capite): %d" % senza_pop)

    indicatori = []
    for chiave, titolo, percorso, unita, procapite, impatto in INDICATORI:
        assoluto, per_ab = [], []
        for c in comuni:
            v = dentro(c["rec"], percorso)
            if v is None:
                continue
            assoluto.append({"istat": c["istat"], "nome": c["nome"],
                             "regione": c["regione"], "valore": v})
            if procapite and c["pop"]:
                per_ab.append({"istat": c["istat"], "nome": c["nome"],
                               "regione": c["regione"],
                               "valore": round(v / c["pop"], 2)})
        if len(assoluto) < 2:
            continue          # con un solo comune non è una classifica
        assoluto.sort(key=lambda x: -x["valore"])
        per_ab.sort(key=lambda x: -x["valore"])

        limiti = []
        if procapite and not per_ab:
            limiti.append("Manca la popolazione: il dato per abitante non è "
                          "calcolabile.")
        if len(assoluto) < len(comuni):
            limiti.append("Dato disponibile per %d comuni su %d in archivio: "
                          "la classifica è parziale."
                          % (len(assoluto), len(comuni)))
        limiti.append("Si confrontano CAPOLUOGHI fra loro, non intere regioni: "
                      "la regione è indicata solo per orientarsi.")

        indicatori.append({
            "chiave": chiave, "titolo": titolo,
            "impatto_cittadino": impatto,
            "unita": unita,
            "unita_procapite": ("%s per abitante" % unita) if procapite else unita,
            "fonte": "Cruscotto Italia · AgID",
            "anno": None,
            "limiti": limiti,
            "n_regioni": len(assoluto),      # riusa il campo dell'app
            "completo": len(assoluto) == len(comuni),
            "assoluto": assoluto[:60],
            "procapite": per_ab[:60],
        })

    per_regione = {}
    for c in comuni:
        r = c["regione"] or "(non indicata)"
        per_regione[r] = per_regione.get(r, 0) + 1

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
            "_avvertenza": (
                "Questa classifica confronta i COMUNI presenti in archivio "
                "(soprattutto capoluoghi), non le regioni. Un capoluogo non "
                "rappresenta la sua regione. Cresce a ogni aggiornamento dei "
                "dati comunali."),
            "n_comuni": len(comuni),
            "copertura_regionale": per_regione,
            "indicatori": indicatori,
        }, f, ensure_ascii=False, separators=(",", ":"))

    print("\n=== classifica dei comuni ===")
    print("  indicatori costruiti: %d" % len(indicatori))
    for i in indicatori:
        primo = (i["procapite"] or i["assoluto"])[0]
        print("    %-28s %2d comuni · primo: %s"
              % (i["titolo"], i["n_regioni"], primo["nome"]))
    print("  scritto: %s" % destinazione)
    return 0


if __name__ == "__main__":
    sys.exit(main())
