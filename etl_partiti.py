#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_partiti.py — programmi elettorali, gruppi e votazioni della legislatura.

=====================  VINCOLO DI LICENZA (uso commerciale)  =====================
Polis ha un modello di ricavo, quindi l'uso è COMMERCIALE. Questo esclude
alcune fonti molto comode ma vietate in quel contesto:

  VIETATE QUI          motivo
  Openpolis            CC BY-NC-SA 4.0 → "NC" = non commerciale
  Radio Radicale       CC BY-NC-SA 4.0 → idem
  Manifesto Project    vieta la redistribuzione dei dati
  ANSA/AGI/Adnkronos   contenuti proprietari

  AMMESSE (attribuzione obbligatoria)
  dati.camera.it       Creative Commons Attribuzione (uso commerciale ammesso)
  dati.senato.it       CC BY 3.0: consente l'uso "anche a fini commerciali"
  Ministero Interno    documenti pubblicati per obbligo di legge
  MEF / Dip. Finanze   CC 3.0

Se qualcuno aggiunge una fonte, va prima verificata la licenza: una singola
fonte NC contamina il prodotto commerciale.
==================================================================================

DIRITTO D'AUTORE: dei programmi si salvano solo i LINK ai PDF ufficiali, mai
il testo. I programmi sono opere dei partiti; lo Stato li pubblica ma non ne
detiene i diritti.

NEUTRALITÀ: si raccolgono TUTTE le liste presenti nella pagina ministeriale,
senza selezione. Nessun commento, nessuna sintesi valutativa.

Uso:
  python3 etl_partiti.py --out ../data/politica            # tutto
  python3 etl_partiti.py --out ../data/politica --solo voti
"""

import argparse
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L

DAIT = "https://dait.interno.gov.it"
PAGINE_PROGRAMMI = [
    ("Elezioni politiche 2022", "25 settembre 2022",
     DAIT + "/elezioni/trasparenza/elezioni-politiche-2022"),
    ("Elezioni europee 2024", "8-9 giugno 2024",
     DAIT + "/elezioni/trasparenza/elezioni-europee-2024"),
]

SPARQL_CAMERA = "https://dati.camera.it/sparql"

# Votazioni della legislatura in corso, per gruppo.
QUERY_VOTAZIONI = """
PREFIX ocd: <http://dati.camera.it/ocd/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?votazione ?titolo ?data ?esito
WHERE {
  ?votazione a ocd:votazione ;
             rdfs:label ?titolo .
  OPTIONAL { ?votazione ocd:dataVotazione ?data }
  OPTIONAL { ?votazione ocd:esito ?esito }
  FILTER(CONTAINS(STR(?votazione), "vs19_"))
}
ORDER BY DESC(?data)
LIMIT 200
"""


# ------------------------------------------------------------------ programmi

def raccogli_programmi(cli):
    """Estrae dalla pagina ministeriale l'elenco delle liste e i link ai PDF.

    Non inventa URL: prende solo i collegamenti realmente presenti nella
    pagina. Se la pagina non è raggiungibile (i portali .gov.it respingono
    i client automatici), lo dichiara invece di produrre link inventati.
    """
    out = None
    for nome, data, url in PAGINE_PROGRAMMI:
        print("[Programmi] %s" % nome)
        try:
            raw, _ = cli.scarica(url, "default", forza=True)
        except Exception as e:
            print("    pagina non raggiungibile (%s)" % e)
            continue
        if not raw:
            continue
        html = raw.decode("utf-8", "ignore")
        liste = _estrai_liste(html)
        print("    liste trovate: %d" % len(liste))
        if liste:
            out = {"elezione": nome, "data": data, "pagina": url,
                   "liste": liste, "_generato": L.ora(),
                   "_fonte": "Ministero dell'Interno · Elezioni trasparenti",
                   "_nota": ("Solo collegamenti ai documenti ufficiali. I testi "
                             "restano opera dei rispettivi partiti e non sono "
                             "riprodotti.")}
            break
    return out


def _estrai_liste(html):
    """Associa ogni PDF di programma/statuto alla lista che lo ha depositato.

    Strategia: si cercano i link ai PDF sotto /documenti/trasparenza/ e si
    risale al nome della lista nel testo che li precede. È volutamente
    prudente: se non riesce ad attribuire un PDF a una lista, lo scarta
    invece di indovinare.
    """
    liste = {}
    # blocchi tabellari o di lista che contengono un link a PDF
    for m in re.finditer(r'<(tr|li|div)[^>]*>(.{0,3000}?)</\1>', html, re.S | re.I):
        blocco = m.group(2)
        pdfs = re.findall(r'href="([^"]*/documenti/trasparenza/[^"]+\.pdf)"',
                          blocco, re.I)
        if not pdfs:
            continue
        testo = re.sub(r'<[^>]+>', ' ', blocco)
        testo = re.sub(r'\s+', ' ', testo).strip()
        if not testo:
            continue
        # il nome è ciò che precede l'indicazione del capo o i link
        nome = re.split(r'capo\s+(?:della\s+)?forza\s+politica', testo, flags=re.I)[0]
        nome = re.sub(r'\b(programma|statuto|dichiarazione di trasparenza|scarica|pdf)\b.*$',
                      '', nome, flags=re.I).strip(" -·|,;")
        if len(nome) < 2 or len(nome) > 140:
            continue
        prog = statuto = None
        for p in pdfs:
            pl = p.lower()
            u = p if p.startswith("http") else urllib.parse.urljoin(DAIT, p)
            if "progr" in pl:
                prog = prog or u
            elif "statut" in pl or "traspar" in pl or "dichiar" in pl:
                statuto = statuto or u
        if not prog and not statuto:
            continue
        capo = None
        mc = re.search(r'capo\s+(?:della\s+)?forza\s+politica[:\s]+(.{2,70})',
                       testo, re.I)
        if mc:
            capo = re.sub(r'\b(programma|statuto|dichiarazione|scarica|pdf)\b.*$', '',
                          mc.group(1), flags=re.I).strip(" -·|,;")
            capo = capo or None
        chiave = nome.lower()[:60]
        if chiave in liste:
            liste[chiave]["programma"] = liste[chiave].get("programma") or prog
            liste[chiave]["statuto"] = liste[chiave].get("statuto") or statuto
        else:
            liste[chiave] = {"nome": nome, "capo": capo,
                             "programma": prog, "statuto": statuto}
    return sorted(liste.values(), key=lambda x: x["nome"])


# ------------------------------------------------------------------ votazioni

def raccogli_votazioni(cli):
    """Votazioni della legislatura in corso dai dati aperti della Camera."""
    print("[Votazioni] interrogo dati.camera.it (SPARQL)")
    url = SPARQL_CAMERA + "?" + urllib.parse.urlencode(
        {"query": QUERY_VOTAZIONI, "format": "application/sparql-results+json"})
    try:
        raw, _ = cli.scarica(url, "default",
                             accept="application/sparql-results+json", forza=True)
    except Exception as e:
        print("    non raggiungibile (%s)" % e)
        return None
    if not raw:
        return None
    try:
        j = json.loads(raw.decode("utf-8"))
    except Exception as e:
        print("    risposta non interpretabile (%s)" % e)
        return None
    righe = (j.get("results") or {}).get("bindings", [])
    print("    votazioni ricevute: %d" % len(righe))
    vot = []
    for r in righe:
        vot.append({
            "id": (r.get("votazione") or {}).get("value"),
            "oggetto": (r.get("titolo") or {}).get("value"),
            "data": ((r.get("data") or {}).get("value") or "")[:10],
            "esito": (r.get("esito") or {}).get("value"),
            "ramo": "Camera",
        })
    if not vot:
        return None
    return {"_generato": L.ora(),
            "_fonte": "Camera dei deputati · dati.camera.it (CC BY)",
            "_licenza_commerciale": True,
            "votazioni": vot, "gruppi": []}


def main():
    ap = argparse.ArgumentParser(description="ETL partiti — programmi e votazioni")
    ap.add_argument("--out", default="../data/politica")
    ap.add_argument("--cache", default=".cache_etl")
    ap.add_argument("--solo", choices=["programmi", "voti"])
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cli = L.Client(args.cache)

    print("Fonti ammesse per uso commerciale: Camera, Senato, Ministero "
          "dell'Interno, MEF. Openpolis e Radio Radicale ESCLUSE (licenza NC).\n")

    if args.solo != "voti":
        prog = raccogli_programmi(cli)
        if prog:
            L.scrivi_json(os.path.join(args.out, "programmi.json"), prog)
            print("    scritto programmi.json (%d liste)" % len(prog["liste"]))
        else:
            print("    nessun programma raccolto: la pagina ministeriale "
                  "respinge i client automatici.\n"
                  "    Servirà un browser headless (vedi LEGGIMI).")

    if args.solo != "programmi":
        vot = raccogli_votazioni(cli)
        if vot:
            L.scrivi_json(os.path.join(args.out, "votazioni.json"), vot)
            print("    scritto votazioni.json (%d votazioni)" % len(vot["votazioni"]))
        else:
            print("    nessuna votazione raccolta in questa esecuzione.")

    print("\nFatto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
