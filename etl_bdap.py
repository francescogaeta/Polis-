#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_bdap.py — opere pubbliche per comune e per regione
(BDAP-MOP, Ragioneria Generale dello Stato - MEF).

DUE INSIDIE, entrambe gestite qui:

1) Il codice ISTAT del comune NON è nel dataset dei progetti. Sta in un
   dataset separato ("Localizzazione Geografica Opere Pubbliche MOP") che va
   unito ai progetti tramite il CUP. Senza questa unione le opere non sono
   collocabili sul territorio.

2) Un'opera può insistere su più comuni: nel file di localizzazione compare
   una riga per ciascun territorio. Sommare le righe conterebbe più volte lo
   stesso importo. Qui ogni CUP viene contato UNA SOLA VOLTA per territorio,
   e per i totali regionali l'importo di un'opera su più comuni della stessa
   regione non viene duplicato. Le opere che attraversano più regioni sono
   conteggiate in ciascuna regione ma segnalate a parte, perché ripartirle
   sarebbe una stima e Polis non stima.

Gli UUID dei dataset non sono scritti a mano: si ricavano dall'API CKAN.

Uso:
  python3 etl_bdap.py --out ../data/territorio            # dataset nazionale
  python3 etl_bdap.py --out ../data/territorio --regione 01
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L

BASE = "https://bdap-opendata.rgs.mef.gov.it"
CKAN = BASE + "/SpodCkanApi/api/3/action"

SLUG_PROGETTI_TOT = "progetti-opere-pubbliche-mop-totale"
SLUG_PROGETTI_REG = "spd_mop_prg_mon_reg%s_01_9999"
SLUG_LOCALIZZAZIONE = "spd_mop_loc_mon_local_01_9999"

# colonne cercate (non presunte): la fonte ha cambiato etichette nel tempo
C_CUP = ["Codice CUP", "CUP", "Cup", "codice_cup"]
C_DESCR = ["Descrizione CUP Integrale", "Descrizione CUP", "Oggetto"]
C_STATO = ["Descrizione Stato CUP", "Stato CUP", "Stato"]
C_COSTO = ["Costo Lavori Effettivo", "Costo Lavori Previsto",
           "Oneri Investimento Effettivi", "Oneri Investimento Previsti"]
C_FIN = ["Finanziamenti Statali", "Finanziamenti Europei",
         "Finanziamenti Enti Territoriali", "Finanziamenti Enti Territorial",
         "Finanziamenti Privati", "Altre fonti di finanziamento"]
C_SETTORE = ["Settore Interv Inv", "Settore", "Settore Intervento"]
# nel file di localizzazione
C_ISTAT = ["Codice Istat Comune", "Codice ISTAT Comune", "Codice Comune",
           "Cod Istat Comune", "codice_istat_comune"]
C_REGL = ["Codice Regione", "Descrizione Regione", "Regione"]


def _val(riga, nomi):
    for n in nomi:
        if n in riga and str(riga[n]).strip():
            return str(riga[n]).strip()
    norm = {k.strip().lower(): v for k, v in riga.items() if k}
    for n in nomi:
        k = n.strip().lower()
        if k in norm and str(norm[k]).strip():
            return str(norm[k]).strip()
    return None


def _num(riga, nomi):
    """Primo valore numerico disponibile fra i nomi indicati."""
    for n in nomi:
        if n in riga:
            v = L.numero(riga[n])
            if v is not None:
                return v
    norm = {k.strip().lower(): v for k, v in riga.items() if k}
    for n in nomi:
        v = L.numero(norm.get(n.strip().lower()))
        if v is not None:
            return v
    return None


def risorsa_csv(cli, slug):
    """Ricava l'URL del CSV di un dataset interrogando l'API CKAN.
    Nessun UUID scritto a mano."""
    try:
        j = cli.json("%s/package_show?id=%s" % (CKAN, slug), "bdap")
    except Exception as e:
        print("    package_show fallito (%s)" % e)
        return None
    if not j or not j.get("success"):
        return None
    for r in (j.get("result") or {}).get("resources", []):
        fmt = (r.get("format") or "").lower()
        url = r.get("url") or ""
        if "csv" in fmt or url.lower().endswith(".csv"):
            return url
    return None


def carica_localizzazione(cli):
    """CUP → elenco di (istat_comune, codice_regione). Un'opera può avere
    più righe: le teniamo tutte, la de-duplicazione avviene dopo."""
    print("[BDAP] localizzazione geografica delle opere")
    url = risorsa_csv(cli, SLUG_LOCALIZZAZIONE)
    if not url:
        print("    dataset di localizzazione non trovato via CKAN")
        return {}
    data, cambiato = cli.scarica(url, "bdap")
    if data is None:
        print("    invariato")
        return None          # None = nulla da rifare
    righe, intest = L.leggi_csv(data)
    print("    righe: %d | colonne: %s..." % (len(righe), intest[:5]))

    loc = {}
    senza = 0
    for r in righe:
        cup = _val(r, C_CUP)
        if not cup:
            continue
        istat = _val(r, C_ISTAT)
        reg = L.codice_regione(_val(r, C_REGL))
        if istat:
            istat = istat.strip().zfill(6)
        if not istat and not reg:
            senza += 1
            continue
        loc.setdefault(cup, []).append((istat, reg))
    print("    CUP localizzati: %d (%d righe senza territorio)" % (len(loc), senza))
    return loc


def carica_progetti(cli, regione=None):
    print("[BDAP] progetti opere pubbliche%s"
          % (" — regione %s" % regione if regione else " — nazionale"))
    slug = SLUG_PROGETTI_REG % regione if regione else SLUG_PROGETTI_TOT
    url = risorsa_csv(cli, slug)
    if not url:
        print("    dataset progetti non trovato via CKAN (slug %s)" % slug)
        return None
    data, cambiato = cli.scarica(url, "bdap")
    if data is None:
        print("    invariato")
        return None
    righe, intest = L.leggi_csv(data)
    print("    righe: %d" % len(righe))
    return righe


def aggrega(progetti, loc, raccordo):
    """Costruisce gli aggregati per comune e per regione, senza duplicare
    gli importi delle opere che insistono su più territori."""
    per_comune, per_regione = {}, {}
    multi_regione = 0
    senza_loc = 0
    visti = set()

    for r in progetti:
        cup = _val(r, C_CUP)
        if not cup or cup in visti:
            continue
        visti.add(cup)
        costo = _num(r, C_COSTO) or 0.0
        fin_ue = L.numero(_val(r, ["Finanziamenti Europei"])) or 0.0
        stato = (_val(r, C_STATO) or "").upper()
        settore = _val(r, C_SETTORE) or ""

        territori = loc.get(cup) or []
        if not territori:
            senza_loc += 1
            continue

        comuni = {t[0] for t in territori if t[0]}
        regioni = {t[1] for t in territori if t[1]}
        # se manca la regione ma c'è il comune, la deduco dal raccordo ISTAT
        for c in comuni:
            rr = (raccordo.get(c) or {}).get("reg")
            if rr:
                regioni.add(rr)
        if len(regioni) > 1:
            multi_regione += 1

        # COMUNI: l'opera è contata una volta per comune interessato.
        # L'importo NON viene diviso (sarebbe una stima): si dichiara che
        # un'opera su più comuni compare in ciascuno di essi.
        for c in comuni:
            d = per_comune.setdefault(c, _vuoto())
            _somma(d, costo, fin_ue, stato, settore)

        # REGIONI: una volta per regione, mai due volte nella stessa
        for rg in regioni:
            d = per_regione.setdefault(rg, _vuoto())
            _somma(d, costo, fin_ue, stato, settore)

    print("    opere uniche: %d | senza localizzazione: %d | su più regioni: %d"
          % (len(visti), senza_loc, multi_regione))
    for d in list(per_comune.values()) + list(per_regione.values()):
        d["settori"] = sorted(d["settori"], key=lambda s: -d["_set"][s])[:6]
        d.pop("_set", None)
    return per_comune, per_regione, {"multi_regione": multi_regione,
                                     "senza_localizzazione": senza_loc,
                                     "opere_uniche": len(visti)}


def _vuoto():
    return {"n_opere": 0, "importo": 0.0, "fondi_ue": 0.0,
            "n_concluse": 0, "n_in_corso": 0, "settori": set(), "_set": {}}


def _somma(d, costo, fin_ue, stato, settore):
    d["n_opere"] += 1
    d["importo"] += costo
    d["fondi_ue"] += fin_ue
    if "CHIUS" in stato or "CONCLUS" in stato:
        d["n_concluse"] += 1
    elif stato:
        d["n_in_corso"] += 1
    if settore:
        d["settori"].add(settore)
        d["_set"][settore] = d["_set"].get(settore, 0) + 1


def main():
    ap = argparse.ArgumentParser(description="ETL BDAP-MOP — opere pubbliche")
    ap.add_argument("--out", default="../data/territorio")
    ap.add_argument("--cache", default=".cache_etl")
    ap.add_argument("--regione", help="codice regione a 2 cifre (default: nazionale)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cli = L.Client(args.cache)

    racc = (L.leggi_json(os.path.join(args.out, "comuni_raccordo.json")) or {})
    raccordo = racc.get("comuni") or {}
    if not raccordo:
        print("ATTENZIONE: manca comuni_raccordo.json. Esegui prima etl_istat.py.\n")

    try:
        loc = carica_localizzazione(cli)
        if loc is None:
            print("Localizzazione invariata: rileggo comunque i progetti.")
            loc = {}
        progetti = carica_progetti(cli, args.regione)
    except L.FonteBloccata as e:
        print("\n!! %s" % e)
        return 2
    if progetti is None:
        print("\nNiente di nuovo da elaborare.")
        return 0
    if not loc:
        print("\nSenza il file di localizzazione le opere non sono collocabili "
              "sul territorio: mi fermo invece di indovinare.")
        return 1

    per_comune, per_regione, note = aggrega(progetti, loc, raccordo)

    L.scrivi_json(os.path.join(args.out, "bdap_opere.json"), {
        "_generato": L.ora(),
        "_fonte": "BDAP-MOP · Ragioneria Generale dello Stato (MEF), CC BY 3.0",
        "_unita": "euro",
        "_nota_metodo": ("Ogni opera (CUP) è contata una sola volta per territorio. "
                         "Gli importi delle opere che interessano più comuni non "
                         "sono ripartiti: l'opera compare intera in ciascun comune "
                         "interessato, quindi i totali comunali non vanno sommati "
                         "fra loro. I totali regionali non contengono duplicati."),
        "_note_dati": note,
        "regioni": {k: _arrotonda(v) for k, v in per_regione.items()},
        "comuni": {k: _arrotonda(v) for k, v in per_comune.items()},
    })
    print("\nScritto bdap_opere.json — %d regioni, %d comuni"
          % (len(per_regione), len(per_comune)))
    return 0


def _arrotonda(d):
    d = dict(d)
    d["importo"] = round(d["importo"])
    d["fondi_ue"] = round(d["fondi_ue"])
    return d


if __name__ == "__main__":
    sys.exit(main())
