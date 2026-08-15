#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_assemblee.py — sedute e votazioni di Camera e Senato, classificate per
tema, per l'aggiornamento quotidiano di Polis.

DUE MODI DI FUNZIONARE
----------------------
1) AUTOMATICO: interroga l'endpoint SPARQL della Camera e i dati aperti del
   Senato. Funziona quando i portali rispondono ai client automatici.

2) MANUALE (la rete di sicurezza): i portali istituzionali respingono spesso
   gli accessi da server. Dal browser, invece, il download funziona sempre.
   Quindi puoi scaricare i file a mano, metterli in una cartella e lanciare:

       python3 etl_assemblee.py --locale ~/Downloads/sedute

   Lo script legge CSV, JSON e XML che trova lì dentro, li normalizza e
   produce lo stesso risultato della modalità automatica.

   Dove scaricare a mano (aprire nel browser):
     Camera  · https://dati.camera.it/it/download
               (dataset "Resoconti" e "Votazioni", legislatura in corso)
     Senato  · https://dati.senato.it/sito/scarica_i_dati
               (dataset votazioni e sedute, CSV/JSON)
     Senato  · https://github.com/SenatoDellaRepubblica/OpenData
               (cartella della legislatura, file dump-votazioni e dump-sedute)

PERCHÉ CLASSIFICARE PER TEMA
----------------------------
Un cittadino non ha bisogno di sapere tutto quello che è successo in Aula:
ha bisogno di sapere cosa tocca la sua vita. Qui ogni punto trattato viene
etichettato per tema (lavoro, famiglia, casa, sanità, scuola, fisco,
pensioni, imprese, trasporti, ambiente, giustizia). L'app poi mostra a
ciascuno i temi coerenti col suo profilo.

La classificazione è per PAROLE CHIAVE, non interpretativa: riconosce di
cosa si parla, non cosa è stato deciso né se è un bene o un male. Il titolo
originale resta sempre visibile.

LICENZE: Camera e Senato pubblicano con licenza Creative Commons
Attribuzione, che consente anche l'uso commerciale citando la fonte.

Uso:
  python3 etl_assemblee.py --out ../dati/assemblee.json
  python3 etl_assemblee.py --out ../dati/assemblee.json --locale ~/Downloads/sedute
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L

SPARQL_CAMERA = "https://dati.camera.it/sparql"

QUERY = """
PREFIX ocd: <http://dati.camera.it/ocd/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?votazione ?titolo ?data ?esito
WHERE {
  ?votazione a ocd:votazione ; rdfs:label ?titolo .
  OPTIONAL { ?votazione ocd:dataVotazione ?data }
  OPTIONAL { ?votazione ocd:esito ?esito }
  FILTER(CONTAINS(STR(?votazione), "vs19_"))
}
ORDER BY DESC(?data)
LIMIT 300
"""

# ---------------------------------------------------------------- temi
# Parole chiave per riconoscere l'argomento. Non interpretano il merito:
# dicono soltanto di che cosa si sta parlando.
TEMI = {
    "lavoro": ["lavoro", "occupazione", "contratto collettivo", "licenziament",
               "salario", "sciopero", "disoccupazione", "naspi", "assunzion",
               "apprendistato", "sicurezza sul lavoro", "infortuni"],
    "famiglia": ["famiglia", "figli", "natalità", "assegno unico", "maternità",
                 "paternità", "congedo", "asili nido", "minori", "adozion"],
    "casa": ["casa", "abitazion", "affitto", "locazion", "mutuo", "edilizia",
             "sfratto", "canone", "urbanistica"],
    "sanita": ["sanit", "salute", "ospedal", "medic", "farmac", "liste di attesa",
               "ticket", "assistenza sanitaria", "vaccin"],
    "scuola": ["scuola", "istruzione", "student", "universit", "docent",
               "formazione", "diritto allo studio", "borse di studio"],
    "fisco": ["fiscale", "imposta", "irpef", "iva", "tassa", "tributar",
              "detrazion", "deduzion", "agenzia delle entrate", "accise",
              "evasione", "aliquota"],
    "pensioni": ["pension", "previdenz", "quota 10", "inps", "trattamento di quiescenza",
                 "assegno sociale"],
    "imprese": ["impres", "pmi", "partita iva", "autonomi", "artigian",
                "commercio", "startup", "industria", "credito d'imposta"],
    "trasporti": ["trasport", "ferroviar", "autostrad", "mobilit", "patente",
                  "codice della strada", "tpl", "aeroport", "porti"],
    "ambiente": ["ambient", "clima", "energia", "rifiuti", "inquinament",
                 "rinnovabil", "acqua", "dissesto", "parchi"],
    "giustizia": ["giustizia", "penale", "processo", "magistratur", "carcer",
                  "codice civile", "tribunal", "avvocat"],
    "sicurezza": ["sicurezza pubblica", "ordine pubblico", "forze dell'ordine",
                  "immigrazion", "protezione civile"],
    "bilancio": ["bilancio", "manovra", "legge di stabilit", "deficit",
                 "debito pubblico", "spending", "fondo"],
}

# quali temi interessano un profilo: usato dall'app per filtrare
RILEVANZA = {
    "situazione": {
        "studio": ["scuola", "lavoro", "casa"],
        "dipendente": ["lavoro", "fisco", "pensioni"],
        "autonomo": ["imprese", "fisco", "pensioni"],
        "cerca": ["lavoro", "scuola"],
        "pensione": ["pensioni", "sanita", "fisco"],
    },
    "figli": {
        "si_minori": ["famiglia", "scuola", "sanita"],
        "si_21": ["famiglia", "scuola"],
        "si_grandi": ["famiglia"],
    },
    "casa": {
        "affitto": ["casa", "fisco"],
        "proprieta": ["casa", "fisco"],
        "cerco": ["casa"],
    },
}


_CACHE_RE = {}


def classifica(testo):
    """Riconosce i temi presenti in un titolo. Zero temi è un esito
    legittimo: significa che non sappiamo dire di cosa parla, e l'app
    lo mostrerà solo nella vista completa.

    Il confronto è su PAROLE INTERE (o loro inizio), non su sottostringhe:
    altrimenti "porti" verrebbe trovato dentro "rapporti" e una legge sui
    rapporti con una confessione religiosa finirebbe sotto "trasporti".
    Errore reale, riscontrato sui dati veri della Camera."""
    t = (testo or "").lower()
    trovati = []
    for tema, chiavi in TEMI.items():
        rx = _CACHE_RE.get(tema)
        if rx is None:
            parti = [r"\b" + re.escape(k) for k in chiavi]
            rx = re.compile("|".join(parti))
            _CACHE_RE[tema] = rx
        if rx.search(t):
            trovati.append(tema)
    return trovati


# ---------------------------------------------------------------- fonti

def da_camera(cli):
    print("[Camera] interrogo l'endpoint SPARQL")
    url = SPARQL_CAMERA + "?" + urllib.parse.urlencode(
        {"query": QUERY, "format": "application/sparql-results+json"})
    try:
        raw, _ = cli.scarica(url, "default",
                             accept="application/sparql-results+json", forza=True)
    except Exception as e:
        print("    non raggiungibile (%s)" % str(e)[:70])
        return []
    if not raw:
        return []
    try:
        j = json.loads(raw.decode("utf-8"))
    except Exception as e:
        print("    risposta non interpretabile (%s)" % str(e)[:60])
        return []
    righe = (j.get("results") or {}).get("bindings", [])
    print("    ricevute %d votazioni" % len(righe))
    return [{
        "id": (r.get("votazione") or {}).get("value", ""),
        "titolo": (r.get("titolo") or {}).get("value", ""),
        "data": ((r.get("data") or {}).get("value") or "")[:10],
        "esito": (r.get("esito") or {}).get("value"),
        "ramo": "Camera",
    } for r in righe]


def da_cartella(percorso):
    """Legge i file scaricati a mano. Accetta CSV, JSON e XML/RDF, e cerca
    di riconoscere i campi invece di presumerli, perché i tracciati di
    Camera e Senato sono diversi fra loro."""
    print("[Manuale] leggo i file in %s" % percorso)
    if not os.path.isdir(percorso):
        print("    cartella inesistente")
        return []
    out = []
    for nome in sorted(os.listdir(percorso)):
        fp = os.path.join(percorso, nome)
        if not os.path.isfile(fp):
            continue
        est = nome.lower().rsplit(".", 1)[-1]
        if est not in ("csv", "json", "xml", "rdf"):
            continue
        ramo = "Senato" if re.search(r"senato|sen", nome, re.I) else "Camera"
        try:
            with open(fp, "rb") as f:
                raw = f.read()
        except Exception as e:
            print("    %s: non leggibile (%s)" % (nome, e))
            continue

        voci = []
        if est == "csv":
            try:
                righe, intest = L.leggi_csv(raw)
                for r in righe:
                    voci.append(_da_riga(r, ramo))
            except Exception as e:
                print("    %s: CSV non interpretabile (%s)" % (nome, str(e)[:50]))
        elif est == "json":
            try:
                j = json.loads(raw.decode("utf-8", "ignore"))
                elenco = j if isinstance(j, list) else (
                    j.get("results", {}).get("bindings")
                    or j.get("votazioni") or j.get("data") or [])
                for r in elenco:
                    if isinstance(r, dict):
                        piatto = {k: (v.get("value") if isinstance(v, dict) else v)
                                  for k, v in r.items()}
                        voci.append(_da_riga(piatto, ramo))
            except Exception as e:
                print("    %s: JSON non interpretabile (%s)" % (nome, str(e)[:50]))
        else:  # xml / rdf
            testo = raw.decode("utf-8", "ignore")
            for m in re.finditer(r"<rdfs:label[^>]*>(.*?)</rdfs:label>", testo, re.S):
                voci.append({"id": "", "titolo": re.sub(r"\s+", " ", m.group(1)).strip(),
                             "data": "", "esito": None, "ramo": ramo})

        voci = [v for v in voci if v and v.get("titolo")]
        print("    %s → %d voci (%s)" % (nome, len(voci), ramo))
        out.extend(voci)
    return out


def _da_riga(r, ramo):
    """Estrae titolo/data/esito da una riga qualsiasi, cercando i campi."""
    norm = {}
    for k, v in (r or {}).items():
        if not k:
            continue
        kk = re.sub(r"[^a-z0-9]+", "_", str(k).strip().lower()).strip("_")
        norm[kk] = v

    def cerca(nomi):
        for n in nomi:
            if n in norm and str(norm[n]).strip():
                return str(norm[n]).strip()
        for n in nomi:
            for k, v in norm.items():
                if n in k and str(v).strip():
                    return str(v).strip()
        return None

    titolo = cerca(["titolo", "label", "oggetto", "descrizione", "argomento",
                    "denominazione", "testo"])
    if not titolo:
        return None
    data = cerca(["data", "datavotazione", "data_votazione", "dataseduta",
                  "data_seduta", "giorno"]) or ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", data) or \
        re.search(r"(\d{2})/(\d{2})/(\d{4})", data)
    if m:
        g = m.groups()
        data = ("%s-%s-%s" % g) if len(g[0]) == 4 else ("%s-%s-%s" % (g[2], g[1], g[0]))
    else:
        data = data[:10]
    return {"id": cerca(["id", "uri", "votazione"]) or "",
            "titolo": titolo[:300], "data": data,
            "esito": cerca(["esito", "risultato"]), "ramo": ramo}


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="ETL assemblee — Camera e Senato")
    ap.add_argument("--out", default="../dati/assemblee.json")
    ap.add_argument("--locale", help="cartella con i file scaricati a mano")
    ap.add_argument("--cache", default=".cache_etl")
    ap.add_argument("--giorni", type=int, default=120,
                    help="tieni solo le sedute degli ultimi N giorni (0 = tutte)")
    args = ap.parse_args()
    cli = L.Client(args.cache)

    voci = []
    if args.locale:
        voci = da_cartella(args.locale)
    else:
        try:
            voci = da_camera(cli)
        except L.FonteBloccata as e:
            print("\n!! %s" % e)
            return 2

    if not voci:
        print("\nNessuna seduta raccolta.")
        if not args.locale:
            print("I portali istituzionali respingono spesso i client automatici.\n"
                  "Scarica i file dal browser e rilancia con:\n"
                  "  python3 etl_assemblee.py --locale CARTELLA\n"
                  "Indirizzi nella documentazione in testa a questo file.")
        return 1

    # classificazione e pulizia
    visti = set()
    puliti = []
    for v in voci:
        t = re.sub(r"\s+", " ", v.get("titolo") or "").strip()
        if len(t) < 8:
            continue
        chiave = (t[:120] + v.get("data", "")).lower()
        if chiave in visti:
            continue
        visti.add(chiave)
        v["titolo"] = t
        v["temi"] = classifica(t)
        puliti.append(v)

    # finestra temporale
    if args.giorni:
        limite = (datetime.now(timezone.utc).date().toordinal() - args.giorni)
        def recente(v):
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", v.get("data") or "")
            if not m:
                return True          # senza data non si scarta
            try:
                return datetime(int(m.group(1)), int(m.group(2)),
                                int(m.group(3))).date().toordinal() >= limite
            except Exception:
                return True
        puliti = [v for v in puliti if recente(v)]

    puliti.sort(key=lambda v: v.get("data") or "", reverse=True)

    conteggio = {}
    for v in puliti:
        for t in v["temi"]:
            conteggio[t] = conteggio.get(t, 0) + 1
    senza_tema = sum(1 for v in puliti if not v["temi"])

    L.scrivi_json(args.out, {
        "_generato": L.ora(),
        "aggiornato": datetime.now(timezone.utc).strftime("%d/%m/%Y"),
        "fonte": "Camera dei deputati e Senato della Repubblica · dati aperti (CC BY)",
        "_metodo": ("Ogni punto è etichettato per tema in base alle parole "
                    "presenti nel titolo. L'etichetta dice DI COSA si parla, "
                    "non cosa è stato deciso. Il titolo originale resta visibile."),
        "rilevanza": RILEVANZA,
        "temi": sorted(TEMI.keys()),
        "conteggio_temi": conteggio,
        "n": len(puliti),
        "n_senza_tema": senza_tema,
        "sedute": puliti,
    })

    print("\n=== assemblee aggiornate ===")
    print("  punti raccolti: %d" % len(puliti))
    print("  senza tema riconosciuto: %d" % senza_tema)
    for t, n in sorted(conteggio.items(), key=lambda x: -x[1])[:8]:
        print("    %-12s %d" % (t, n))
    print("  scritto: %s" % os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
