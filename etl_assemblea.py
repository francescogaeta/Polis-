#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETL SEDUTE D'ASSEMBLEA — Camera dei deputati  (versione 2)
===========================================================
L'endpoint SPARQL dati.camera.it blocca gli accessi automatici da GitHub
(risponde con la pagina anti-robot invece che con i dati). Questa versione
non lo usa: legge direttamente i resoconti pubblicati su documenti.camera.it,
che sono file con indirizzi prevedibili.

Come trova le sedute nuove: parte dall'ultimo numero conosciuto e prova
i successivi finché non ne trova più. Nessun elenco da interrogare.

NESSUN DATO INVENTATO: argomenti e date sono estratti dal resoconto
ufficiale. Se un dato non c'è, resta vuoto.

    python etl_assemblea.py            aggiornamento incrementale
    python etl_assemblea.py --test     quali indirizzi sono raggiungibili
    python etl_assemblea.py --full     riparte dalla seduta 1
"""
import os, re, sys, json, time, argparse, datetime as dt
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LEGISLATURA = 19
UA = ("PolisETL/2.0 (app civica; contatto: assistenza-dati@camera.it "
      "per segnalazioni)")

RADICE   = os.path.dirname(os.path.abspath(__file__))
DIR_DATI = os.path.join(RADICE, "dati")
F_OUT    = os.path.join(DIR_DATI, "assemblea.json")
F_STATO  = os.path.join(RADICE, "state_assemblea.json")

PAUSA          = 1.2   # cortesia verso il server
MAX_PER_RUN    = 25    # sedute nuove per esecuzione
STOP_DOPO_VUOTI = 4    # quante mancanti di fila prima di fermarsi

MESI = {m: i+1 for i, m in enumerate(
    ["gennaio","febbraio","marzo","aprile","maggio","giugno",
     "luglio","agosto","settembre","ottobre","novembre","dicembre"])}


# ------------------------------------------------------------------ rete
def sessione():
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=4, backoff_factor=2, status_forcelist=[429,500,502,503,504],
        allowed_methods=["GET"])))
    s.mount("http://", HTTPAdapter(max_retries=Retry(total=4, backoff_factor=2)))
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/xml,text/xml,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9",
    })
    return s


def scarica(sess, url, timeout=45):
    """Ritorna (stato, contenuto). Non solleva eccezioni."""
    try:
        r = sess.get(url, timeout=timeout)
        return r.status_code, r.content
    except Exception as e:
        return 0, str(e).encode()


def anti_robot(contenuto):
    t = contenuto[:800].decode("utf-8", "ignore").lower()
    return ("checking your browser" in t or "just a moment" in t
            or "cf-browser-verification" in t)


# ------------------------------------------------------------------ url
def url_seduta(num):
    s = f"sed{int(num):04d}"
    b = f"https://documenti.camera.it/leg{LEGISLATURA}/resoconti/assemblea"
    return {
        "sommario": f"{b}/xml/repository/{s}/sommario.xml",
        "steno":    f"{b}/xml/repository/{s}/stenografico.xml",
        "html":     f"{b}/html/{s}/stenografico.htm",
        "pdf":      f"{b}/html/{s}/stenografico.pdf",
        "scheda":   f"https://www.camera.it/leg{LEGISLATURA}/410?idSeduta={int(num):04d}&tipo=stenografico",
    }


# ------------------------------------------------------------------ test
def test_raggiungibilita(sess):
    print("=" * 56)
    print("QUALI INDIRIZZI DELLA CAMERA SONO RAGGIUNGIBILI")
    print("=" * 56, flush=True)
    prove = [
        ("Resoconto XML di una seduta (sommario)",
         url_seduta(100)["sommario"]),
        ("Resoconto XML di una seduta (stenografico)",
         url_seduta(100)["steno"]),
        ("Resoconto in pagina web",
         url_seduta(100)["html"]),
        ("Elenco delle sedute sul sito",
         f"https://www.camera.it/leg{LEGISLATURA}/207"),
        ("Endpoint dati (quello che risultava bloccato)",
         "https://dati.camera.it/sparql?query=SELECT%20?x%20WHERE%20%7BBIND(1%20AS%20?x)%7D&format=application/sparql-results%2Bjson"),
    ]
    buoni = []
    for nome, u in prove:
        st, cont = scarica(sess, u, timeout=40)
        testa = cont[:300].decode("utf-8", "ignore").replace("\n", " ").strip()
        # capisco DAVVERO cosa è arrivato, non mi fido della dimensione
        if st == 0:
            esito, buono = f"NON RAGGIUNGIBILE ({testa[:70]})", False
        elif anti_robot(cont):
            esito, buono = "BLOCCATO dal sistema anti-robot", False
        elif st != 200:
            esito, buono = f"risposta {st}", False
        elif cont.lstrip()[:5] == b"<?xml" or cont.lstrip()[:1] == b"<" and b"<html" not in cont[:400].lower():
            esito, buono = f"OK — documento XML, {len(cont)} byte", True
        elif b"<html" in cont[:400].lower():
            titolo = re.search(rb"<title[^>]*>(.{0,90}?)</title>", cont[:4000], re.I|re.S)
            t = titolo.group(1).decode("utf-8","ignore").strip() if titolo else "senza titolo"
            atteso_html = u.endswith(".htm") or "/207" in u
            esito = f"pagina web \u00ab{t}\u00bb, {len(cont)} byte"
            buono = atteso_html and len(cont) > 8000
            if not buono:
                esito += "  <-- NON e' il dato che serve"
        else:
            esito, buono = f"contenuto non riconosciuto, {len(cont)} byte", False
        print(f"\n  {nome}\n    -> {esito}")
        print(f"    inizio: {testa[:150]}", flush=True)
        if buono:
            buoni.append(nome)
        time.sleep(PAUSA)

    print("\n" + "=" * 56)
    print("RIEPILOGO")
    print("=" * 56)
    if buoni:
        print(f"{len(buoni)} indirizzi funzionano:")
        for b in buoni:
            print("   OK  ", b)
        print("\nCONCLUSIONE: si possono scaricare i resoconti da qui.")
        print("Lancia il workflow in modalità 'completo' per il primo caricamento.")
    else:
        print("Nessun indirizzo raggiungibile dagli indirizzi di GitHub.")
        print("\nCONCLUSIONE: la Camera blocca tutto il traffico automatico.")
        print("Restano due strade: eseguire lo scarico da un altro computer,")
        print("oppure scrivere ad assistenza-dati@camera.it chiedendo l'accesso.")
    print("=" * 56, flush=True)
    return 0 if buoni else 1


# ------------------------------------------------------------------ parsing
def testo_tag(el):
    return " ".join("".join(el.itertext()).split())


def leggi_sommario(contenuto):
    """Estrae data e argomenti dal resoconto ufficiale. Tollerante:
    i nomi dei tag cambiano tra legislature."""
    out = {"data": "", "argomenti": []}
    try:
        root = ET.fromstring(contenuto)
    except Exception:
        return out

    testo_intero = " ".join(root.itertext())

    # data: prima in forma numerica, poi "12 marzo 2026"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", testo_intero)
    if m:
        out["data"] = m.group(0)
    else:
        m = re.search(r"\b(\d{1,2})\s+(" + "|".join(MESI) + r")\s+(\d{4})", testo_intero, re.I)
        if m:
            g, mese, a = int(m.group(1)), MESI[m.group(2).lower()], int(m.group(3))
            out["data"] = f"{a:04d}-{mese:02d}-{g:02d}"

    # argomenti: titoli dell'indice
    visti, voci = set(), []
    for el in root.iter():
        nome = el.tag.lower().rsplit("}", 1)[-1]
        if nome in ("titolo", "tit", "argomento", "oggetto", "titoloatto"):
            t = testo_tag(el)
            # l'intestazione ("Seduta di martedì 12 marzo 2026") non è un argomento
            if re.match(r"^seduta\s+(di|del)\b", t, re.I):
                continue
            if 12 <= len(t) <= 180:
                k = t.lower()[:60]
                if k not in visti:
                    visti.add(k); voci.append(t)
        if len(voci) >= 10:
            break
    out["argomenti"] = voci[:8]
    return out


# ------------------------------------------------------------------ stato
def leggi_stato():
    if os.path.exists(F_STATO):
        try: return json.load(open(F_STATO, encoding="utf-8"))
        except Exception: pass
    return {"ultimo_numero": 0}

def scrivi_stato(s):
    json.dump(s, open(F_STATO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def leggi_esistenti():
    if os.path.exists(F_OUT):
        try: return json.load(open(F_OUT, encoding="utf-8")).get("sedute", [])
        except Exception: pass
    return []


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--introspect", action="store_true", help="alias di --test")
    a = ap.parse_args()

    os.makedirs(DIR_DATI, exist_ok=True)
    sess = sessione()

    if a.test or a.introspect:
        return test_raggiungibilita(sess)

    stato = leggi_stato()
    esistenti = leggi_esistenti()
    per_num = {s["numero"]: s for s in esistenti}
    inizio = 1 if a.full else max(1, stato.get("ultimo_numero", 0) + 1)

    print(f"[ETL] cerco le sedute dalla numero {inizio} in avanti", flush=True)

    n, vuoti, nuove, ultimo_ok = inizio, 0, 0, stato.get("ultimo_numero", 0)
    while nuove < MAX_PER_RUN and vuoti < STOP_DOPO_VUOTI:
        link = url_seduta(n)
        st, cont = scarica(sess, link["sommario"])

        if st == 200 and not anti_robot(cont) and len(cont) > 300:
            d = leggi_sommario(cont)
            per_num[n] = {
                "id": f"s{LEGISLATURA}_{n}", "leg": LEGISLATURA, "numero": n,
                "data": d["data"], "titolo": "",
                "argomenti": d["argomenti"], "votazioni": [],
                "link": link,
            }
            nuove += 1; vuoti = 0; ultimo_ok = n
            print(f"  + seduta {n} — {d['data'] or 'data non trovata'} — "
                  f"{len(d['argomenti'])} argomenti", flush=True)
        elif anti_robot(cont):
            print(f"[ERRORE] anche documenti.camera.it blocca gli accessi automatici.")
            print("[ETL] nessuna modifica ai dati esistenti.")
            return 1
        else:
            vuoti += 1
        n += 1
        time.sleep(PAUSA)

    if nuove == 0:
        print("[ETL] nessuna seduta nuova trovata.")
        if ultimo_ok == 0:
            print("[ETL] non è stata trovata NESSUNA seduta: controlla con --test")
            return 1
        return 0

    sedute = sorted(per_num.values(),
                    key=lambda x: (x["data"] or "", x["numero"]), reverse=True)
    out = {
        "aggiornato": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "legislatura": LEGISLATURA, "totale": len(sedute),
        "fonte": "Camera dei deputati — resoconti ufficiali su documenti.camera.it",
        "licenza": "CC-BY 4.0 — Camera dei deputati",
        "sedute": sedute[:200],
    }
    json.dump(out, open(F_OUT, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))

    stato["ultimo_numero"] = max(ultimo_ok, stato.get("ultimo_numero", 0))
    scrivi_stato(stato)
    print(f"[ETL] {nuove} sedute nuove · {len(sedute)} totali · "
          f"{os.path.getsize(F_OUT)//1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
