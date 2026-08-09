#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_normattiva_testi.py — scarica il TESTO degli atti già presenti
nell'archivio Normattiva di Polis, così il traduttore burocratico può
lavorare sul testo completo e non solo sul titolo.

PERCHÉ SERVE
------------
L'ETL Normattiva esistente salva titoli e metadati (dati/normattiva.json).
Con quelli lo strumento riconosce il tipo di atto e i rinvii citati nel
titolo, ma non può scomporre articoli e commi, che sono il cuore del testo.
Questo ETL colma quel vuoto.

LICENZA
-------
Normattiva pubblica i testi con licenza CC BY 4.0 (IPZS): la ripubblicazione
è consentita citando la fonte. Nell'app la citazione c'è già in ogni scheda.
Diverso dai programmi dei partiti, che restano opera loro e vanno solo
linkati: qui il testo è normativo e riutilizzabile.

RISPETTO DELLA FONTE
--------------------
Normattiva non pubblica limiti numerici, ma è un servizio pubblico gratuito:
  * 2 secondi fra una richiesta e l'altra (in lib_fonti);
  * un LOTTO limitato per esecuzione (default 25 atti), con cursore che
    riprende da dove si era fermato;
  * download condizionale: un atto già scaricato non si riscarica;
  * se il server risponde 403 o 429 ci si ferma e lo si dichiara.

ONESTÀ SUI LIMITI
-----------------
Il portale può respingere i client automatici. In quel caso lo script NON
inventa nulla: salta l'atto, lo registra fra i non riusciti e lo dichiara
nel riepilogo. L'app continua a mostrare il link al testo ufficiale.

Uso:
  python3 etl_normattiva_testi.py --archivio ../dati/normattiva.json \\
                                  --out ../dati/testi
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L

MAX_CARATTERI = 120000       # oltre, il testo si tronca (dichiarandolo)


def url_atto(a):
    """Costruisce il permalink ufficiale dell'atto.
    Usa l'URN se l'archivio lo contiene; altrimenti la forma ELI con il
    codice redazionale. Non inventa: se mancano entrambi, ritorna None."""
    u = a.get("urn")
    if u:
        u = str(u)
        # un URN non è un indirizzo scaricabile: va passato al risolutore
        # ufficiale di Normattiva, che lo traduce nella pagina dell'atto.
        if u.startswith("http"):
            return u
        if u.startswith("urn:"):
            return "https://www.normattiva.it/uri-res/N2Ls?" + u
    cod, anno = a.get("cod"), a.get("anno") or "2026"
    if cod:
        return "https://www.normattiva.it/eli/id/%s/%s/CONSOLIDATED" % (anno, cod)
    return None


def estrai_testo(html):
    """Ricava il testo dell'atto dalla pagina, scartando menu e contorno."""
    h = html
    # via script, stili e navigazione
    h = re.sub(r'(?is)<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>', ' ', h)
    # il corpo dell'atto sta di norma in un contenitore dedicato
    m = re.search(r'(?is)<div[^>]*(?:id|class)="[^"]*(?:testo|articolo|bodyTesto|'
                  r'wrapper_testo|contenuto)[^"]*"[^>]*>(.*?)</div>\s*(?:</div>|$)', h)
    corpo = m.group(1) if m else h
    corpo = re.sub(r'(?i)<br\s*/?>', '\n', corpo)
    corpo = re.sub(r'(?i)</(p|div|li|tr|h\d)>', '\n', corpo)
    testo = re.sub(r'(?s)<[^>]+>', ' ', corpo)
    # entità più comuni
    for a, b in [('&nbsp;', ' '), ('&agrave;', 'à'), ('&egrave;', 'è'),
                 ('&eacute;', 'é'), ('&igrave;', 'ì'), ('&ograve;', 'ò'),
                 ('&ugrave;', 'ù'), ('&amp;', '&'), ('&lt;', '<'),
                 ('&gt;', '>'), ('&quot;', '"'), ('&#39;', "'")]:
        testo = testo.replace(a, b)
    testo = re.sub(r'[ \t]+', ' ', testo)
    testo = re.sub(r'\n\s*\n\s*\n+', '\n\n', testo)
    return testo.strip()


def sembra_atto(testo):
    """Controllo di qualità: se non riconosco la struttura di un atto,
    è probabile che abbia catturato la pagina sbagliata. Meglio scartare
    che salvare spazzatura."""
    # soglia bassa: esistono atti brevissimi (una proroga di due righe).
    # Meglio farsi guidare dagli indizi strutturali che dalla lunghezza.
    if len(testo) < 200:
        return False
    indizi = 0
    if re.search(r'\bart(?:icolo)?\.?\s*\d', testo, re.I):
        indizi += 1
    if re.search(r'\b\d\.\s+[A-ZÀ-Ù]', testo):          # commi numerati
        indizi += 1
    if re.search(r"(?i)gazzetta ufficiale|entrata in vigore|è pubblicat|e' pubblicat", testo):
        indizi += 1
    if re.search(r'(?i)\b(decreto|legge|regolamento)\b', testo):
        indizi += 1
    return indizi >= 3


def main():
    ap = argparse.ArgumentParser(
        description="ETL testi Normattiva per il traduttore burocratico")
    ap.add_argument("--archivio", default="../dati/normattiva.json")
    ap.add_argument("--out", default="../dati/testi")
    ap.add_argument("--cache", default=".cache_etl")
    ap.add_argument("--lotto", type=int, default=25,
                    help="atti da scaricare per esecuzione (default 25)")
    args = ap.parse_args()

    arch = L.leggi_json(args.archivio)
    if not arch or not isinstance(arch.get("atti"), list):
        print("Archivio non trovato o vuoto: %s" % args.archivio)
        print("Esegui prima l'ETL Normattiva che produce dati/normattiva.json.")
        return 1
    atti = arch["atti"]
    os.makedirs(args.out, exist_ok=True)
    cli = L.Client(args.cache)

    indice_path = os.path.join(args.out, "_indice.json")
    indice = L.leggi_json(indice_path) or {"testi": {}, "falliti": {}, "cursore": 0}
    testi = indice.get("testi", {})
    falliti = indice.get("falliti", {})

    # riparto dal cursore, saltando quelli già presi
    pos = int(indice.get("cursore", 0)) % max(1, len(atti))
    da_fare, i = [], 0
    while len(da_fare) < args.lotto and i < len(atti):
        a = atti[(pos + i) % len(atti)]
        chiave = a.get("cod") or a.get("urn")
        if chiave and chiave not in testi:
            da_fare.append(a)
        i += 1
    nuovo_cursore = (pos + i) % max(1, len(atti))

    print("Archivio: %d atti · già con testo: %d · in questo lotto: %d\n"
          % (len(atti), len(testi), len(da_fare)))
    if not da_fare:
        print("Tutti gli atti dell'archivio hanno già il testo.")
        return 0

    ok = salt = 0
    for n, a in enumerate(da_fare, 1):
        chiave = a.get("cod") or a.get("urn")
        titolo = (a.get("titolo") or "")[:52]
        url = url_atto(a)
        print("[%2d/%2d] %s ... " % (n, len(da_fare), titolo), end="", flush=True)
        if not url:
            print("senza permalink: salto")
            falliti[chiave] = "permalink assente"
            salt += 1
            continue
        try:
            raw, _ = cli.scarica(url, "default", forza=True)
        except L.FonteBloccata as e:
            print("STOP")
            print("\n!! %s" % e)
            break
        except Exception as e:
            print("non scaricato (%s)" % str(e)[:50])
            falliti[chiave] = str(e)[:120]
            salt += 1
            continue
        if not raw:
            print("vuoto")
            salt += 1
            continue

        testo = estrai_testo(raw.decode("utf-8", "ignore"))
        if not sembra_atto(testo):
            print("testo non riconosciuto: salto")
            falliti[chiave] = "struttura non riconosciuta (portale anti-bot?)"
            salt += 1
            continue
        troncato = False
        if len(testo) > MAX_CARATTERI:
            testo = testo[:MAX_CARATTERI]
            troncato = True

        L.scrivi_json(os.path.join(args.out, "%s.json" % chiave), {
            "cod": chiave,
            "tipo": a.get("tipo"), "numero": a.get("numero"),
            "titolo": a.get("titolo"), "gu": a.get("gu"),
            "url": url,
            "testo": testo,
            "troncato": troncato,
            "_scaricato": L.ora(),
            "_fonte": "Normattiva · IPZS (CC BY 4.0)",
        })
        testi[chiave] = {"file": "%s.json" % chiave, "car": len(testo)}
        falliti.pop(chiave, None)
        ok += 1
        print("ok (%d caratteri%s)" % (len(testo), ", troncato" if troncato else ""))

    L.scrivi_json(indice_path, {
        "_generato": L.ora(),
        "_fonte": "Normattiva · IPZS (CC BY 4.0)",
        "cursore": nuovo_cursore,
        "testi": testi, "falliti": falliti,
    })

    print("\n=== lotto completato ===")
    print("  testi scaricati:  %d" % ok)
    print("  saltati:          %d" % salt)
    print("  totale in archivio: %d di %d atti" % (len(testi), len(atti)))
    if falliti:
        print("\n  Atti non riusciti: %d. Se sono molti, il portale sta"
              " respingendo i client automatici:" % len(falliti))
        for k, v in list(falliti.items())[:3]:
            print("    · %s → %s" % (k, v))
        print("  In quel caso l'app continua a mostrare il link al testo"
              " ufficiale: nessun dato inventato.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
