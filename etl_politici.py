#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETL POLITICI IN CARICA — Governo, Senato, Camera
=================================================
Popola le schede dei politici in Polis.

FONTI (scelte perché raggiungibili da un server, verificato sul campo):
  · Senatori  -> repository GitHub ufficiale del Senato della Repubblica
                 SenatoDellaRepubblica/OpenData, dump RDF della composizione.
                 Licenza CC BY 3.0.
  · Deputati  -> Wikidata (query.wikidata.org). Il portale dati.camera.it
                 blocca gli accessi automatici dai server, quindi non è usabile.
                 Licenza CC0.
  · Governo   -> Wikidata. Permette anche di collegare un ministro alla sua
                 scheda da parlamentare, perché è la stessa voce.

NESSUN DATO INVENTATO: tutto viene dalle fonti sopra. Dove un campo manca,
resta vuoto. Le fotografie NON vengono raccolte: le note legali di Camera e
Senato ne vietano l'uso commerciale, in conflitto con la licenza dei dati.

    python etl_politici.py           aggiorna tutto
    python etl_politici.py --test    prova solo la raggiungibilità delle fonti
"""
import os, re, io, sys, json, time, zipfile, argparse
import datetime as dt

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RADICE   = os.path.dirname(os.path.abspath(__file__))
DIR_DATI = os.path.join(RADICE, "dati")
F_OUT    = os.path.join(DIR_DATI, "politici.json")

UA = ("PolisApp/1.0 (applicazione civica italiana; "
      "contatto: polis.app.contatto@gmail.com)")

WDQS = "https://query.wikidata.org/sparql"
SENATO_ZIP = ("https://raw.githubusercontent.com/SenatoDellaRepubblica/"
              "OpenData/main/Leg19/dump-composizione-19.zip")

# soglie di guardia: sotto questi numeri i dati sono incompleti e non si pubblica
MIN_SENATORI = 190
MIN_DEPUTATI = 350
MIN_GOVERNO  = 30

Q_DEPUTATO = "wd:Q18558478"    # membro della Camera dei deputati
Q_TERM_XIX = "wd:Q114381503"   # XIX legislatura
Q_GOVERNO  = "wd:Q113723473"   # Governo Meloni


def sessione():
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=4, backoff_factor=3, status_forcelist=[429,500,502,503,504],
        allowed_methods=["GET"])))
    s.headers.update({"User-Agent": UA, "Accept-Language": "it,en;q=0.8"})
    return s


# ------------------------------------------------------------------ Wikidata
def wdqs(sess, query, timeout=120):
    for tentativo in range(4):
        r = sess.get(WDQS, params={"query": query, "format": "json"},
                     headers={"Accept": "application/sparql-results+json"},
                     timeout=timeout)
        if r.status_code == 200:
            return r.json()["results"]["bindings"]
        if r.status_code == 429:
            attesa = int(r.headers.get("Retry-After", "35"))
            print(f"  [attendo {attesa}s: limite di richieste]", flush=True)
            time.sleep(attesa); continue
        r.raise_for_status()
    raise RuntimeError("Wikidata: tentativi esauriti")


def v(b, k):
    return b.get(k, {}).get("value", "")


def scarica_deputati(sess):
    q = f"""
SELECT DISTINCT ?p ?pLabel ?gruppoLabel ?collegioLabel ?inizio ?fine WHERE {{
  ?p p:P39 ?st .
  ?st ps:P39 {Q_DEPUTATO} ; pq:P2937 {Q_TERM_XIX} .
  OPTIONAL {{ ?st pq:P4100 ?gruppo }}
  OPTIONAL {{ ?st pq:P768  ?collegio }}
  OPTIONAL {{ ?st pq:P580  ?inizio }}
  OPTIONAL {{ ?st pq:P582  ?fine }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "it,en" }}
}}"""
    out = {}
    for b in wdqs(sess, q):
        qid = v(b, "p").rsplit("/", 1)[-1]
        fine = v(b, "fine")[:10]
        if fine:                       # mandato concluso: non è più in carica
            continue
        out[qid] = {
            "id": qid, "r": "deputato",
            "n": v(b, "pLabel"),
            "g": v(b, "gruppoLabel"),
            "c": v(b, "collegioLabel"),
            "d": v(b, "inizio")[:10],
        }
    return list(out.values())


def scarica_governo(sess):
    q = f"""
SELECT DISTINCT ?p ?pLabel ?incaricoLabel ?partitoLabel ?inizio ?fine WHERE {{
  ?p p:P39 ?st .
  ?st ps:P39 ?incarico .
  ?st pq:P5054 {Q_GOVERNO} .
  OPTIONAL {{ ?st pq:P580 ?inizio }}
  OPTIONAL {{ ?st pq:P582 ?fine }}
  OPTIONAL {{ ?p wdt:P102 ?partito }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "it,en" }}
}}"""
    out = {}
    for b in wdqs(sess, q):
        if v(b, "fine")[:10]:
            continue
        qid = v(b, "p").rsplit("/", 1)[-1]
        voce = out.setdefault(qid, {
            "id": qid, "r": "governo", "n": v(b, "pLabel"),
            "i": [], "g": v(b, "partitoLabel"), "d": v(b, "inizio")[:10],
        })
        inc = v(b, "incaricoLabel")
        if inc and inc not in voce["i"]:
            voce["i"].append(inc)
    return list(out.values())


# ------------------------------------------------------------------ Senato
def scarica_senatori(sess):
    import rdflib
    r = sess.get(SENATO_ZIP, timeout=180)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    nome = [n for n in z.namelist() if n.endswith(".rdf")][0]
    raw = z.read(nome)

    # Il file pubblicato dal Senato usa identificatori numerici (nodeID="0")
    # che non sono validi per lo standard XML: senza questa correzione il
    # parser si ferma alla riga 46.
    raw = re.sub(rb'(rdf:nodeID=")(\d)', rb'\1n\2', raw)

    g = rdflib.Graph()
    g.parse(io.BytesIO(raw), format="xml")

    P = """
    PREFIX osr:<http://dati.senato.it/osr/>
    PREFIX ocd:<http://dati.camera.it/ocd/>
    PREFIX foaf:<http://xmlns.com/foaf/0.1/>
    PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
    """

    # nome del gruppo: gruppo -> denominazione -> titolo/titoloBreve
    gruppi = {}
    for row in g.query(P + """SELECT ?g ?titolo ?breve ?fine WHERE {
        ?g a ocd:gruppoParlamentare ; osr:denominazione ?d .
        ?d osr:titolo ?titolo . OPTIONAL{ ?d osr:titoloBreve ?breve }
        OPTIONAL{ ?d osr:fine ?fine }
    }"""):
        if str(row.fine or ""):        # denominazione superata
            continue
        gruppi[str(row.g)] = {"nome": str(row.titolo),
                              "sigla": str(row.breve or "")}

    # adesione al gruppo: parte DAL senatore (proprietà "aderisce")
    adesione = {}
    for row in g.query(P + """SELECT ?s ?gruppo ?fine WHERE {
        ?s a osr:Senatore ; ocd:aderisce ?ad .
        ?ad osr:gruppo ?gruppo .
        OPTIONAL{ ?ad osr:fine ?fine }
    }"""):
        if str(row.fine or ""):        # adesione conclusa: cambio di gruppo
            continue
        adesione[str(row.s)] = str(row.gruppo)

    # mandato in corso: da qui la regione di elezione
    regione = {}
    for row in g.query(P + """SELECT ?s ?reg ?fine WHERE {
        ?s a osr:Senatore ; osr:mandato ?m .
        OPTIONAL{ ?m osr:regioneElezione ?reg }
        OPTIONAL{ ?m osr:fine ?fine }
    }"""):
        if str(row.fine or ""):
            continue
        if row.reg:
            regione[str(row.s)] = str(row.reg)

    out = []
    for row in g.query(P + """SELECT ?s ?nome ?cognome ?label WHERE {
        ?s a osr:Senatore .
        OPTIONAL{ ?s foaf:firstName ?nome }
        OPTIONAL{ ?s foaf:lastName  ?cognome }
        OPTIONAL{ ?s rdfs:label ?label }
    }"""):
        uri = str(row.s)
        nome = str(row.label or "").strip() or \
               f"{row.nome or ''} {row.cognome or ''}".strip()
        gr = gruppi.get(adesione.get(uri, ""), {})
        out.append({
            "id": "sen" + uri.rsplit("/", 1)[-1],
            "r": "senatore",
            "n": nome,
            "g": gr.get("sigla") or gr.get("nome", ""),
            "c": regione.get(uri, ""),
            "d": "",
        })
    return out


# ------------------------------------------------------------------ test
def test(sess):
    print("=" * 56)
    print("RAGGIUNGIBILITÀ DELLE FONTI")
    print("=" * 56, flush=True)
    esiti = []
    try:
        r = wdqs(sess, "SELECT ?x WHERE { BIND(1 AS ?x) }", timeout=60)
        print("  Wikidata          -> OK")
        esiti.append(True)
    except Exception as e:
        print(f"  Wikidata          -> FALLITO: {str(e)[:90]}")
        esiti.append(False)
    try:
        r = sess.get(SENATO_ZIP, timeout=120, stream=True)
        ok = r.status_code == 200
        print(f"  Dump del Senato   -> {'OK' if ok else 'risposta '+str(r.status_code)}")
        esiti.append(ok)
    except Exception as e:
        print(f"  Dump del Senato   -> FALLITO: {str(e)[:90]}")
        esiti.append(False)
    print("=" * 56)
    print("Entrambe le fonti funzionano." if all(esiti)
          else "Almeno una fonte non risponde: vedi sopra.")
    return 0 if all(esiti) else 1


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    os.makedirs(DIR_DATI, exist_ok=True)
    sess = sessione()

    if a.test:
        return test(sess)

    politici, problemi = [], []

    for etichetta, funzione, minimo in (
        ("senatori", scarica_senatori, MIN_SENATORI),
        ("deputati", scarica_deputati, MIN_DEPUTATI),
        ("governo",  scarica_governo,  MIN_GOVERNO),
    ):
        try:
            print(f"[{etichetta}] scarico…", flush=True)
            dati = funzione(sess)
            print(f"[{etichetta}] {len(dati)} trovati", flush=True)
            if len(dati) < minimo:
                problemi.append(f"{etichetta}: solo {len(dati)}, attesi almeno {minimo}")
            politici += dati
        except Exception as e:
            problemi.append(f"{etichetta}: {type(e).__name__} {str(e)[:120]}")
            print(f"[{etichetta}] ERRORE: {e}", flush=True)
        time.sleep(2)

    if problemi:
        print("\n[ATTENZIONE] dati incompleti:")
        for p in problemi: print("   -", p)
        print("[ETL] non sovrascrivo i dati esistenti.")
        return 1

    politici.sort(key=lambda x: (x["r"], x["n"]))
    out = {
        "aggiornato": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "legislatura": 19,
        "totali": {r: sum(1 for p in politici if p["r"] == r)
                   for r in ("governo", "senatore", "deputato")},
        "fonti": ("Senato della Repubblica (CC BY 3.0) · "
                  "Wikidata (CC0) per deputati e Governo"),
        "nota": ("Le fotografie non sono incluse: le note legali di Camera e "
                 "Senato ne vietano l'uso commerciale."),
        "politici": politici,
    }
    json.dump(out, open(F_OUT, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"\n[ETL] {len(politici)} politici · "
          f"{os.path.getsize(F_OUT)//1024} KB -> {F_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
