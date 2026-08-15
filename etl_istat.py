#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_istat.py — base territoriale di Polis: raccordo comune→provincia→regione
e popolazione residente (indispensabile per i confronti pro capite).

Perché serve per primo: senza popolazione una classifica fra regioni misura
solo la loro dimensione. La Lombardia spende più del Molise perché ha dieci
milioni di abitanti, non perché spenda "meglio". Ogni classifica di Polis
deve poter essere letta pro capite, e il denominatore viene da qui.

LIMITE DELLA FONTE: ISTAT dichiara 5 query al minuto per indirizzo IP, oltre
il quale l'accesso viene bloccato per 1-2 giorni. lib_fonti tiene 13 s fra le
richieste. Questo ETL fa pochissime chiamate (2-3), quindi non è un problema.

Output (in data/territorio/):
  comuni_raccordo.json  {istat6: {nome, prov, prov_sigla, reg, reg_nome}}
  popolazione.json      {reg: {popolazione, anno}, "_comuni": {istat6: pop}}

Uso:
  python3 etl_istat.py --out ../data/territorio
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L

# Elenco ufficiale dei codici delle unità territoriali (permalink ISTAT).
# Se ISTAT cambia il nome del file, lo si scopre dal 404 e si aggiorna qui:
# la pagina di riferimento è
# https://www.istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/
RACCORDO_URL = [
    "https://www.istat.it/storage/codici-unita-amministrative/Elenco-codici-statistici-e-denominazioni-delle-unita-territoriali.zip",
    "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv",
]

# Popolazione residente al 1° gennaio, SDMX (dataflow 22_289 / DCIS_POPRES1)
SDMX = "https://esploradati.istat.it/SDMXWS/rest"
POP_DATAFLOW = "22_289"

# nomi possibili delle colonne nel file di raccordo (ISTAT li ha cambiati
# più volte negli anni: li cerchiamo, non li presumiamo)
COL_ISTAT = ["Codice Comune formato alfanumerico",
             "Codice Comune formato numerico",
             "Codice Istat del Comune \n(alfanumerico)",
             "Codice Istat del Comune (alfanumerico)"]
COL_NOME = ["Denominazione in italiano",
            "Denominazione (Italiana e straniera)",
            "Denominazione Comune"]
COL_REG = ["Codice Regione", "Codice regione"]
COL_REGNOME = ["Denominazione Regione", "Denominazione regione"]
COL_PROV = ["Denominazione dell'Unità territoriale sovracomunale \n(valida a fini statistici)",
            "Denominazione dell'Unità territoriale sovracomunale (valida a fini statistici)",
            "Denominazione Provincia", "Denominazione provincia"]
COL_SIGLA = ["Sigla automobilistica", "Sigla"]


def _prima(riga, nomi):
    for n in nomi:
        if n in riga and str(riga[n]).strip():
            return str(riga[n]).strip()
    # tentativo tollerante: confronto normalizzato
    norm = {k.strip().lower().replace("\n", " "): v for k, v in riga.items() if k}
    for n in nomi:
        k = n.strip().lower().replace("\n", " ")
        if k in norm and str(norm[k]).strip():
            return str(norm[k]).strip()
    return None


def scarica_raccordo(cli, out_dir):
    """Tabella comune → provincia → regione. È la chiave di tutto il resto."""
    print("[ISTAT] raccordo territoriale dei comuni")
    dati = None
    for url in RACCORDO_URL:
        try:
            data, cambiato = cli.scarica(url, "istat", forza=True)
            if not data:
                continue
            if url.endswith(".zip"):
                files = L.estrai_zip(data, ".csv")
                if not files:
                    continue
                # il file più grande è l'elenco dei comuni
                nome = max(files, key=lambda k: len(files[k]))
                dati = files[nome]
                print("    dallo zip: %s" % nome)
            else:
                dati = data
            break
        except Exception as e:
            print("    non disponibile (%s): provo l'alternativa" % e)
            continue
    if not dati:
        raise SystemExit(
            "Non è stato possibile scaricare il raccordo ISTAT. Verificare gli "
            "URL sulla pagina 'Codici dei comuni, delle province e delle regioni'.")

    righe, intest = L.leggi_csv(dati)
    print("    righe: %d | colonne: %d" % (len(righe), len(intest)))

    raccordo = {}
    scartate = 0
    for r in righe:
        istat = _prima(r, COL_ISTAT)
        if not istat:
            scartate += 1
            continue
        istat = istat.strip().zfill(6)
        if not istat.isdigit():
            scartate += 1
            continue
        cod_reg = L.codice_regione(_prima(r, COL_REG) or _prima(r, COL_REGNOME))
        if not cod_reg:
            scartate += 1
            continue
        raccordo[istat] = {
            "nome": _prima(r, COL_NOME) or "",
            "prov": _prima(r, COL_PROV) or "",
            "sigla": _prima(r, COL_SIGLA) or "",
            "reg": cod_reg,
            "reg_nome": L.REGIONI.get(cod_reg, ""),
        }
    print("    comuni mappati: %d (scartate %d righe)" % (len(raccordo), scartate))
    if len(raccordo) < 7000:
        print("    ATTENZIONE: attesi ~7.900 comuni. Schema forse cambiato.")
    L.scrivi_json(os.path.join(out_dir, "comuni_raccordo.json"), {
        "_generato": L.ora(),
        "_fonte": "ISTAT · Codici delle unità territoriali (CC BY 3.0 IT)",
        "_n": len(raccordo),
        "comuni": raccordo,
    })
    return raccordo


def scarica_popolazione(cli, out_dir, raccordo):
    """Popolazione residente per regione (denominatore del pro capite).

    Chiediamo l'ultimo anno disponibile con lastNObservations=1 per non
    scaricare l'intera serie storica: meno carico sulla fonte."""
    print("[ISTAT] popolazione residente")
    url = ("%s/data/%s?lastNObservations=1&dimensionAtObservation=AllDimensions"
           % (SDMX, POP_DATAFLOW))
    try:
        data, _ = cli.scarica(
            url, "istat",
            accept="application/vnd.sdmx.data+csv;version=1.0.0", forza=True)
    except Exception as e:
        print("    non disponibile (%s)" % e)
        data = None

    per_regione, per_comune, anno = {}, {}, None
    if data:
        righe, intest = L.leggi_csv(data)
        print("    osservazioni: %d" % len(righe))
        for r in righe:
            area = (r.get("REF_AREA") or "").strip()
            val = L.numero(r.get("OBS_VALUE"), decimale_virgola=False)
            per = (r.get("TIME_PERIOD") or "").strip()
            if val is None or not area:
                continue
            anno = anno or per
            # filtriamo il totale (sesso/età totali) se le dimensioni ci sono
            if r.get("SEXISTAT1") not in (None, "", "9", "T"):
                continue
            if r.get("ETA1") not in (None, "", "TOTAL", "Y_GE0"):
                continue
            if len(area) == 2 and area.isdigit():
                cod = L.codice_regione(area)
                if cod:
                    per_regione[cod] = max(per_regione.get(cod, 0), val)
            elif len(area) == 6 and area.isdigit():
                per_comune[area] = val

    # se la fonte non ha dato i totali regionali ma ha i comuni, li sommiamo
    # usando il raccordo ufficiale (non le cifre del codice)
    if not per_regione and per_comune and raccordo:
        print("    totali regionali assenti: li ricavo sommando i comuni "
              "tramite il raccordo ISTAT")
        for istat, pop in per_comune.items():
            reg = (raccordo.get(istat) or {}).get("reg")
            if reg:
                per_regione[reg] = per_regione.get(reg, 0) + pop

    print("    regioni con popolazione: %d/20 | comuni: %d"
          % (len(per_regione), len(per_comune)))
    L.scrivi_json(os.path.join(out_dir, "popolazione.json"), {
        "_generato": L.ora(),
        "_fonte": "ISTAT · Popolazione residente (dataflow %s), CC BY 3.0 IT" % POP_DATAFLOW,
        "_anno": anno,
        "_completo": len(per_regione) == 20,
        "regioni": {k: int(v) for k, v in per_regione.items()},
        "comuni": {k: int(v) for k, v in per_comune.items()},
    })
    return per_regione


def main():
    ap = argparse.ArgumentParser(description="ETL ISTAT — base territoriale Polis")
    ap.add_argument("--out", default="../data/territorio")
    ap.add_argument("--cache", default=".cache_etl")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cli = L.Client(args.cache)
    print("Limite ISTAT rispettato: %.0fs fra richieste (max 5/min dichiarati).\n"
          % L.LIMITI["istat"])
    try:
        raccordo = scarica_raccordo(cli, args.out)
        scarica_popolazione(cli, args.out, raccordo)
    except L.FonteBloccata as e:
        print("\n!! %s" % e)
        return 2
    print("\nFatto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
