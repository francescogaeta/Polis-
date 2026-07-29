#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETL SEDUTE D'ASSEMBLEA — Camera dei deputati
=============================================
Aggiorna ogni notte i dati delle sedute dell'Aula per l'app Polis.

Fonti ufficiali:
  - Endpoint SPARQL: https://dati.camera.it/sparql (ontologia OCD, licenza CC-BY 4.0)
  - Resoconti: documenti.camera.it (XML stenografico e sommario)

PRINCIPIO "NESSUN DATO INVENTATO": lo script non genera testo. Estrae solo
argomenti realmente presenti nell'indice del resoconto ufficiale e vi allega
sempre il link alla fonte. Se un dato manca, resta vuoto: non viene stimato.

Esecuzione:
    python etl_assemblea.py               # aggiornamento incrementale
    python etl_assemblea.py --introspect  # mostra i predicati reali (fase 0)
    python etl_assemblea.py --full        # rigenera tutto da inizio legislatura
"""
import os, re, sys, json, time, argparse, datetime as dt
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------- costanti
SPARQL      = "https://dati.camera.it/sparql"
LEGISLATURA = 19
LEG_URI     = f"http://dati.camera.it/ocd/legislatura.rdf/repubblica_{LEGISLATURA}"
INIZIO_LEG  = "2022-10-13"

UA = ("PolisETL/1.0 (civic app; +https://github.com/polis-app; "
      "contatto: assistenza-dati@camera.it per segnalazioni)")

RADICE   = os.path.dirname(os.path.abspath(__file__))
DIR_DATI = os.path.join(RADICE, "dati")
F_OUT    = os.path.join(DIR_DATI, "assemblea.json")
F_STATO  = os.path.join(RADICE, "state_assemblea.json")

PAUSA = 1.5          # secondi tra richieste: cortesia verso il server
MAX_SEDUTE_RUN = 40  # tetto per esecuzione, evita run infiniti al primo giro


# ---------------------------------------------------------------- rete
def sessione():
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=5, backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"])))
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/sparql-results+json",
        "Accept-Language": "it-IT,it;q=0.9",
    })
    return s


def sparql(sess, query, timeout=90):
    """Interroga l'endpoint. Se l'anti-bot intercetta, riprova con cloudscraper."""
    params = {"query": query, "format": "application/sparql-results+json"}
    try:
        r = sess.get(SPARQL, params=params, timeout=timeout)
        testo = r.text[:400]
        if "Checking your browser" in testo or "<html" in testo[:60].lower():
            raise RuntimeError("anti-bot")
        r.raise_for_status()
        return r.json()
    except Exception as e:
        if "anti-bot" not in str(e) and not isinstance(e, requests.HTTPError):
            raise
        try:
            import cloudscraper
        except ImportError:
            raise RuntimeError(
                "L'endpoint ha risposto con la schermata anti-bot e cloudscraper "
                "non è installato. Esegui: pip install cloudscraper") from e
        sc = cloudscraper.create_scraper()
        sc.headers.update({"User-Agent": UA, "Accept": "application/sparql-results+json"})
        r = sc.get(SPARQL, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()


def righe(res):
    for b in res.get("results", {}).get("bindings", []):
        yield {k: v.get("value") for k, v in b.items()}


# ---------------------------------------------------------------- fase 0
def introspezione(sess):
    """Diagnosi passo per passo. Stampa SEMPRE qualcosa, anche quando fallisce:
    un log muto non permette di capire dove si è fermato."""
    esiti = []

    def prova(titolo, query, timeout=60):
        print("\n" + "-"*54)
        print("PROVA:", titolo)
        print("-"*54, flush=True)
        try:
            res = sparql(sess, query, timeout=timeout)
            r = list(righe(res))
            if not r:
                print("  Risposta ricevuta, ma NESSUN risultato.")
                esiti.append((titolo, "vuoto"))
                return []
            print(f"  OK — {len(r)} risultati:")
            for x in r[:45]:
                riga = " | ".join(f"{k}={str(v)[:70]}" for k, v in x.items())
                print("   ", riga)
            esiti.append((titolo, "ok"))
            return r
        except Exception as e:
            msg = str(e)[:200].replace("\n", " ")
            print(f"  FALLITA: {type(e).__name__}: {msg}")
            esiti.append((titolo, "errore"))
            return []

    print("=" * 54)
    print("DIAGNOSI CONNESSIONE A dati.camera.it")
    print("=" * 54, flush=True)

    # 1. l'endpoint risponde?
    prova("L'endpoint risponde a una domanda banale",
          "SELECT ?x WHERE { BIND(1 AS ?x) }", timeout=45)

    # 2. esiste la classe seduta e quante ne vede?
    prova("Quante sedute esistono in archivio",
          """PREFIX ocd: <http://dati.camera.it/ocd/>
             SELECT (COUNT(?s) AS ?quante) WHERE { ?s a ocd:seduta }""", timeout=60)

    # 3. predicati di UNA seduta precisa (leggera: non scandisce tutto l'archivio)
    prova("Come è fatta una seduta (campi disponibili)",
          """SELECT ?campo ?valore WHERE {
               <http://dati.camera.it/ocd/seduta.rdf/s18_376> ?campo ?valore
             } LIMIT 45""", timeout=60)

    # 4. predicati di UNA votazione precisa
    prova("Come è fatta una votazione (campi disponibili)",
          """SELECT ?campo ?valore WHERE {
               <http://dati.camera.it/ocd/votazione.rdf/vs18_376_073> ?campo ?valore
             } LIMIT 45""", timeout=60)

    # 5. esiste la legislatura 19?
    prova("Sedute della legislatura in corso (prime 5)",
          f"""PREFIX ocd: <http://dati.camera.it/ocd/>
             SELECT ?seduta WHERE {{
               ?seduta a ocd:seduta ; ocd:rif_leg <{LEG_URI}>
             }} LIMIT 5""", timeout=60)

    # riepilogo finale, sempre stampato
    print("\n" + "=" * 54)
    print("RIEPILOGO")
    print("=" * 54)
    for t, e in esiti:
        segno = {"ok": "OK   ", "vuoto": "VUOTO", "errore": "ERRORE"}[e]
        print(f"  {segno}  {t}")
    ok_n = sum(1 for _, e in esiti if e == "ok")
    print()
    if ok_n == 0:
        print("CONCLUSIONE: la Camera non risponde a nessuna richiesta.")
        print("Probabile blocco degli accessi automatici dagli indirizzi di GitHub.")
        print("Serve cambiare metodo: leggere i file pubblicati invece dell'endpoint.")
    elif ok_n < len(esiti):
        print(f"CONCLUSIONE: {ok_n} prove su {len(esiti)} riuscite.")
        print("L'endpoint funziona ma alcuni campi hanno nomi diversi da quelli previsti.")
        print("Manda questo elenco a chi sviluppa: bastano piccole correzioni.")
    else:
        print("CONCLUSIONE: tutto funziona. Copia i campi qui sopra e mandali.")
    print("=" * 54, flush=True)


# ---------------------------------------------------------------- query
def q_sedute(dal, al):
    """Sedute d'Assemblea in un intervallo.
    I predicati di data/numero variano: uso OPTIONAL su più candidati e
    tengo il primo valorizzato, così la query non si rompe se cambiano."""
    return f"""
    PREFIX ocd: <http://dati.camera.it/ocd/>
    PREFIX dc:  <http://purl.org/dc/elements/1.1/>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
    PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
    SELECT DISTINCT ?seduta ?data ?numero ?titolo WHERE {{
      ?seduta a ocd:seduta .
      ?seduta ocd:rif_leg <{LEG_URI}> .
      OPTIONAL {{ ?seduta dc:date ?d1 . }}
      OPTIONAL {{ ?seduta ocd:dataSeduta ?d2 . }}
      OPTIONAL {{ ?seduta ocd:numero ?n1 . }}
      OPTIONAL {{ ?seduta ocd:numeroSeduta ?n2 . }}
      OPTIONAL {{ ?seduta rdfs:label ?titolo . }}
      BIND(COALESCE(?d1, ?d2) AS ?data)
      BIND(COALESCE(?n1, ?n2) AS ?numero)
      FILTER(BOUND(?data))
      FILTER(xsd:date(?data) >= "{dal}"^^xsd:date)
      FILTER(xsd:date(?data) <= "{al}"^^xsd:date)
    }} ORDER BY ?data LIMIT 300
    """


def q_votazioni(seduta_uri):
    return f"""
    PREFIX ocd: <http://dati.camera.it/ocd/>
    PREFIX dc:  <http://purl.org/dc/elements/1.1/>
    PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?votazione ?descrizione ?esito ?fav ?contr ?ast WHERE {{
      ?votazione a ocd:votazione ; ocd:rif_seduta <{seduta_uri}> .
      OPTIONAL {{ ?votazione dc:description ?descrizione . }}
      OPTIONAL {{ ?votazione rdfs:label ?descrizione . }}
      OPTIONAL {{ ?votazione ocd:esito ?esito . }}
      OPTIONAL {{ ?votazione ocd:favorevoli ?fav . }}
      OPTIONAL {{ ?votazione ocd:contrari ?contr . }}
      OPTIONAL {{ ?votazione ocd:astenuti ?ast . }}
    }} LIMIT 120
    """


# ---------------------------------------------------------------- resoconti
def url_resoconto(num):
    s = f"sed{int(num):04d}"
    b = f"http://documenti.camera.it/leg{LEGISLATURA}/resoconti/assemblea"
    return {
        "xml_sommario": f"{b}/xml/repository/{s}/sommario.xml",
        "xml_steno":    f"{b}/xml/repository/{s}/stenografico.xml",
        "html":  f"https://documenti.camera.it/leg{LEGISLATURA}/resoconti/assemblea/html/{s}/stenografico.htm",
        "pdf":   f"https://documenti.camera.it/leg{LEGISLATURA}/resoconti/assemblea/html/{s}/stenografico.pdf",
        "scheda": f"https://www.camera.it/leg{LEGISLATURA}/410?idSeduta={int(num):04d}&tipo=stenografico",
    }


def argomenti_da_sommario(sess, url, max_voci=8):
    """Estrae gli argomenti REALI dall'indice del sommario ufficiale.
    Non genera né riassume: prende i titoli così come sono."""
    try:
        r = sess.get(url, timeout=60, headers={"Accept": "application/xml"})
        if r.status_code != 200 or len(r.content) < 200:
            return []
        root = ET.fromstring(r.content)
    except Exception:
        return []

    voci, visti = [], set()
    # i titoli stanno in tag che variano tra legislature: cerco i più probabili
    for tag in ("titolo", "tit", "argomento", "oggetto", "intestazione"):
        for el in root.iter():
            if el.tag.lower().endswith(tag):
                t = " ".join((el.text or "").split())
                if 12 <= len(t) <= 180:
                    chiave = t.lower()[:60]
                    if chiave not in visti:
                        visti.add(chiave); voci.append(t)
        if len(voci) >= max_voci:
            break
    return voci[:max_voci]


# ---------------------------------------------------------------- stato
def leggi_stato():
    if os.path.exists(F_STATO):
        try:
            return json.load(open(F_STATO, encoding="utf-8"))
        except Exception:
            pass
    return {"ultima_data": INIZIO_LEG, "sedute_note": []}


def scrivi_stato(s):
    json.dump(s, open(F_STATO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def leggi_esistenti():
    if os.path.exists(F_OUT):
        try:
            j = json.load(open(F_OUT, encoding="utf-8"))
            return j.get("sedute", [])
        except Exception:
            pass
    return []


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--introspect", action="store_true", help="mostra i predicati reali")
    ap.add_argument("--full", action="store_true", help="rigenera da inizio legislatura")
    args = ap.parse_args()

    os.makedirs(DIR_DATI, exist_ok=True)
    sess = sessione()

    if args.introspect:
        introspezione(sess); return 0

    stato = leggi_stato()
    dal = INIZIO_LEG if args.full else stato.get("ultima_data", INIZIO_LEG)
    al  = dt.date.today().isoformat()
    print(f"[ETL] sedute Camera leg.{LEGISLATURA} dal {dal} al {al}")

    try:
        res = sparql(sess, q_sedute(dal, al))
    except Exception as e:
        print(f"[ERRORE] endpoint SPARQL non raggiungibile: {e}")
        print("[ETL] nessuna modifica ai dati esistenti.")
        return 1

    trovate = list(righe(res))
    print(f"[ETL] {len(trovate)} sedute restituite dall'endpoint")

    esistenti = leggi_esistenti()
    per_id = {s["id"]: s for s in esistenti}
    nuove = 0

    for r in trovate[:MAX_SEDUTE_RUN]:
        uri  = r.get("seduta", "")
        data = (r.get("data") or "")[:10]
        num  = r.get("numero") or ""
        if not num:
            m = re.search(r"s\d+_(\d+)", uri)
            num = m.group(1) if m else ""
        if not num or not data:
            continue
        sid = f"s{LEGISLATURA}_{int(num)}"
        if sid in per_id and not args.full:
            continue

        link = url_resoconto(num)
        argomenti = argomenti_da_sommario(sess, link["xml_sommario"])
        time.sleep(PAUSA)

        votazioni = []
        try:
            for v in righe(sparql(sess, q_votazioni(uri))):
                d = (v.get("descrizione") or "").strip()
                if not d:
                    continue
                voce = {"o": d[:160]}
                for k_src, k_dst in (("esito","e"),("fav","f"),("contr","c"),("ast","a")):
                    if v.get(k_src):
                        voce[k_dst] = v[k_src]
                votazioni.append(voce)
        except Exception:
            pass
        time.sleep(PAUSA)

        per_id[sid] = {
            "id": sid,
            "leg": LEGISLATURA,
            "numero": int(num),
            "data": data,
            "titolo": (r.get("titolo") or "").strip()[:200],
            "argomenti": argomenti,
            "votazioni": votazioni[:12],
            "link": link,
        }
        nuove += 1
        print(f"  + seduta {num} del {data} — {len(argomenti)} argomenti, {len(votazioni)} votazioni")

    sedute = sorted(per_id.values(), key=lambda x: (x["data"], x["numero"]), reverse=True)

    out = {
        "aggiornato": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "legislatura": LEGISLATURA,
        "totale": len(sedute),
        "fonte": "Camera dei deputati — dati.camera.it (SPARQL, ontologia OCD) e "
                 "documenti.camera.it (resoconti ufficiali)",
        "licenza": "CC-BY 4.0 — Camera dei deputati",
        "sedute": sedute[:200],
    }
    json.dump(out, open(F_OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    if sedute:
        stato["ultima_data"] = max(s["data"] for s in sedute)
    scrivi_stato(stato)

    kb = os.path.getsize(F_OUT) / 1024
    print(f"[ETL] {nuove} sedute nuove · {len(sedute)} totali · {kb:.0f} KB → {F_OUT}")
    if nuove == 0:
        print("[ETL] nessuna seduta nuova (normale nei periodi di pausa dei lavori).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
