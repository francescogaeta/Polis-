#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_inpa.py — concorsi e avvisi pubblici aperti, dal Portale del reclutamento.

LICENZA DELLA FONTE
-------------------
inPA (Dipartimento della Funzione Pubblica): contenuti in CC BY 3.0 salvo
diversa indicazione; i testi dei bandi sono atti ufficiali, esclusi dal
diritto d'autore (art. 5 L. 633/1941). Il controllo è in fonti_regioni.py:
se la riga «inpa» non è marcata come commerciale, lo script non parte.

COME SI ACCEDE, E COME NO
-------------------------
Il robots.txt di inPA esclude il percorso /wp-json/ (l'interfaccia di
programmazione del sito). Esistono servizi che estraggono i bandi proprio da
lì: noi no. Lo script:
  1. legge il robots.txt a ogni avvio e verifica OGNI indirizzo prima di
     chiederlo; se un indirizzo non è consentito, lo salta e lo dichiara;
  2. parte dalla sitemap pubblicata (sitemap-bandi.xml), che è il canale
     che il sito stesso offre ai motori di ricerca;
  3. apre solo le pagine pubbliche di dettaglio dei bandi.

LIMITI RISPETTATI
-----------------
Nessun limite numerico pubblicato: 3 secondi fra una richiesta e l'altra e al
massimo --max pagine per esecuzione (150 di default ≈ 8 minuti). Si scaricano
solo i bandi nuovi o cambiati dall'ultima volta (confronto sul lastmod della
sitemap): il primo giro richiede più esecuzioni, dopo bastano pochi minuti al
giorno. Su 403/429 ci si ferma, si salva quanto raccolto e si dichiara.

COSA SALVA, E COSA NO
---------------------
Solo i campi strutturati che la pagina espone con un'etichetta (area
geografica, ente, date di apertura e chiusura, numero di posti, modalità di
valutazione, stato) più il titolo e il collegamento al bando e agli allegati.
Il testo integrale NON viene copiato: l'app rimanda all'atto originale.
Le etichette vengono cercate, non presunte: se la pagina cambia struttura, il
bando viene scartato con il motivo, invece di salvare campi sbagliati.

Uso:
  python3 etl_inpa.py --out ../data/opportunita --max 150
"""

import argparse
import html as htmlmod
import os
import re
import sys
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, date
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L
import fonti_regioni as FR

BASE = "https://www.inpa.gov.it"
ROBOTS = BASE + "/robots.txt"
SITEMAP = BASE + "/sitemap-bandi.xml"
NS_SM = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}

MESI = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
    "novembre": 11, "dicembre": 12,
}

# Etichette esposte dalla pagina di dettaglio → nome del campo Polis.
# Si cercano così come la pagina le scrive; nessuna viene indovinata.
ETICHETTE = {
    "area geografica": "area",
    "valutazione": "valutazione",
    "stato": "stato",
    "data apertura candidature": "apertura",
    "data chiusura candidature": "chiusura",
    "numero di posti": "posti",
    "ente di riferimento": "ente",
}
OBBLIGATORI = ("ente", "chiusura")


# ---------------------------------------------------------------- robots
class Robots:
    """Regole del robots.txt lette dalla fonte, non scritte a mano."""

    def __init__(self, client):
        data, _ = client.scarica(ROBOTS, "inpa", forza=True)
        self.rp = urllib.robotparser.RobotFileParser()
        self.rp.parse((data or b"").decode("utf-8", "replace").splitlines())

    def consentito(self, url):
        return self.rp.can_fetch(L.UA, url) and self.rp.can_fetch("*", url)


# ---------------------------------------------------------------- sitemap
def leggi_sitemap(xml_bytes):
    """Ritorna [(id_concorso, url_canonico, lastmod)] dalla sitemap."""
    radice = ET.fromstring(xml_bytes)
    out = []
    for u in radice.findall("s:url", NS_SM):
        loc = (u.findtext("s:loc", default="", namespaces=NS_SM) or "").strip()
        mod = (u.findtext("s:lastmod", default="", namespaces=NS_SM) or "").strip()
        q = parse_qs(urlparse(loc).query)
        cid = (q.get("concorso_id") or [None])[0]
        if not cid or not re.fullmatch(r"[0-9a-f]{32}", cid):
            continue
        # la sitemap usa inpa.gov.it senza www, che reindirizza: si usa
        # direttamente l'indirizzo canonico dichiarato dalle pagine
        url = BASE + "/bandi-e-avvisi/dettaglio-bando-avviso/?concorso_id=" + cid
        out.append((cid, url, mod))
    return out


# ---------------------------------------------------------------- pagina
def _testo(frammento):
    t = re.sub(r"<[^>]+>", " ", frammento)
    t = htmlmod.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def data_it(s):
    """'04 Novembre 2026 23:59' → '2026-11-04T23:59'. None se non leggibile."""
    m = re.search(r"(\d{1,2})\s+([A-Za-zàèéìòù]+)\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", s or "")
    if not m:
        return None
    mese = MESI.get(m.group(2).lower())
    if not mese:
        return None
    try:
        d = datetime(int(m.group(3)), mese, int(m.group(1)),
                     int(m.group(4) or 0), int(m.group(5) or 0))
    except ValueError:
        return None
    return d.strftime("%Y-%m-%dT%H:%M")


def analizza_pagina(html_bytes, cid, url):
    """Estrae i campi etichettati. Ritorna (record, problema)."""
    h = html_bytes.decode("utf-8", "replace")

    titolo = None
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S | re.I)
    if m:
        titolo = _testo(m.group(1))
    if not titolo:
        m = re.search(r"<title>(.*?)</title>", h, re.S | re.I)
        titolo = _testo(m.group(1)).split(" – ")[0] if m else None
    if not titolo:
        return None, "titolo non trovato"

    # il blocco dei campi strutturati sta dopo il testo del bando: si legge
    # il testo piano e si cercano le etichette come la pagina le scrive
    piano = _testo(h)
    # il riquadro dei dati strutturati è in fondo alla pagina: si parte
    # dall'ultima «Area geografica:», così una parola come «Stato:» citata
    # nel testo del bando non viene scambiata per un'etichetta
    inizio = max(piano.lower().rfind("area geografica:"), 0)
    piano = piano[inizio:]
    campi = {}
    nomi = sorted(ETICHETTE.keys(), key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in nomi)
    for m in re.finditer(r"(?i)\b(" + alt + r")\s*:\s*", piano):
        chiave = ETICHETTE[m.group(1).lower()]
        resto = piano[m.end():]
        fine = re.search(r"(?i)\b(" + alt + r"|bando/avviso e allegati|invia la tua candidatura|contatti)\b", resto)
        valore = resto[:fine.start()] if fine else resto[:200]
        valore = valore.strip(" ;,")
        if valore and chiave not in campi:
            campi[chiave] = valore[:200]

    manca = [c for c in OBBLIGATORI if not campi.get(c)]
    if manca:
        return None, "etichette non trovate: %s (la pagina potrebbe aver cambiato struttura)" % ", ".join(manca)

    chiusura = data_it(campi.get("chiusura"))
    if not chiusura:
        return None, "data di chiusura non leggibile: %r" % campi.get("chiusura")

    posti = None
    if campi.get("posti"):
        mp = re.search(r"\d+", campi["posti"])
        posti = int(mp.group(0)) if mp else None

    # allegati: solo collegamenti al media server ufficiale del portale
    allegati = []
    for ma in re.finditer(r'<a[^>]+href="(https://portale\.inpa\.gov\.it/api/media/[0-9a-f-]{36})"[^>]*>(.*?)</a>', h, re.S | re.I):
        nome = _testo(ma.group(2))
        nome = re.sub(r"\s*\(Pubblicato il[^)]*\)\s*$", "", nome)
        allegati.append({"nome": nome[:140], "url": ma.group(1)})
        if len(allegati) >= 6:
            break

    area = campi.get("area", "")
    codici = sorted({c for c in (L.codice_regione(p.strip()) for p in re.split(r"[,;/]", area)) if c})
    regioni = [L.REGIONI[c] for c in codici]      # stessi nomi del catalogo incentivi

    return {
        "id": cid,
        "titolo": titolo[:300],
        "ente": campi["ente"],
        "area": area or None,
        "regioni": regioni,                       # [] = nazionale o non indicata
        "nazionale": bool(re.search(r"(?i)nazional|tutto il territorio|italia\b", area)),
        "apertura": data_it(campi.get("apertura")),
        "chiusura": chiusura,
        "posti": posti,
        "valutazione": campi.get("valutazione"),
        "stato": campi.get("stato"),
        "url": url,
        "allegati": allegati,
    }, None


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Concorsi e avvisi aperti da inPA")
    ap.add_argument("--out", default="../data/opportunita")
    ap.add_argument("--cache", default="../.cache_etl")
    ap.add_argument("--max", type=int, default=150,
                    help="pagine di dettaglio al massimo per esecuzione")
    args = ap.parse_args()

    scheda = FR.pretendi("inpa")
    L.LIMITI.update(FR.limiti_per_client())
    client = L.Client(args.cache)

    dest = os.path.join(args.out, "concorsi.json")
    stato_path = os.path.join(args.out, "_stato_inpa.json")
    archivio = (L.leggi_json(dest) or {}).get("concorsi") or []
    per_id = {c["id"]: c for c in archivio if c.get("id")}
    visti = L.leggi_json(stato_path) or {}          # id -> lastmod già elaborato

    print("inPA · %s (%s)" % (scheda["licenza"], scheda["attribuzione"]))
    print("Limite rispettato: %s\n" % scheda["limite_nota"])

    problemi, bloccato = [], None
    nuovi = aggiornati = scartati = 0
    try:
        robots = Robots(client)
        if not robots.consentito(SITEMAP):
            print("La sitemap non è consentita dal robots.txt: mi fermo.")
            return 1
        sm, _ = client.scarica(SITEMAP, "inpa", forza=True)
        voci = leggi_sitemap(sm or b"")
        print("Bandi elencati nella sitemap: %d" % len(voci))

        # solo nuovi o cambiati, dal più recente
        da_fare = [v for v in voci if visti.get(v[0]) != v[2]]
        da_fare.sort(key=lambda v: v[2], reverse=True)
        print("Nuovi o cambiati dall'ultima esecuzione: %d · ne elaboro al massimo %d\n"
              % (len(da_fare), args.max))

        for cid, url, mod in da_fare[:args.max]:
            if not robots.consentito(url):
                problemi.append("%s: escluso dal robots.txt, non richiesto" % cid)
                continue
            dati, _ = client.scarica(url, "inpa", forza=True)
            if not dati:
                continue
            rec, prob = analizza_pagina(dati, cid, url)
            visti[cid] = mod
            if prob:
                scartati += 1
                problemi.append("%s: %s" % (cid, prob))
                continue
            rec["aggiornato_fonte"] = mod
            if cid in per_id:
                aggiornati += 1
            else:
                nuovi += 1
            per_id[cid] = rec
    except L.FonteBloccata as e:
        bloccato = str(e)
        print("\nFERMO: %s\nSalvo quanto raccolto; si riprende alla prossima esecuzione." % e)

    # si tengono solo i bandi ancora aperti: chiusi = non più utili
    adesso = datetime.now().strftime("%Y-%m-%dT%H:%M")
    aperti = [c for c in per_id.values() if (c.get("chiusura") or "") >= adesso]
    rimossi = len(per_id) - len(aperti)
    aperti.sort(key=lambda c: c["chiusura"])

    L.scrivi_json(stato_path, visti)
    L.scrivi_json(dest, {
        "_generato": L.ora(),
        "_fonte": "inPA — Portale unico del reclutamento",
        "_licenza": scheda["licenza"],
        "_uso_commerciale": True,
        "_attribuzione": scheda["attribuzione"],
        "_limite_rispettato": scheda["limite_nota"],
        "_avvertenza": ("Polis riporta i dati strutturati pubblicati su inPA e "
                        "rimanda al bando originale, che è l'unico testo che "
                        "fa fede. Requisiti, scadenze e modalità vanno sempre "
                        "verificati sull'atto."),
        "_completo": not bloccato and len(da_fare) <= args.max,
        "n_concorsi": len(aperti),
        "concorsi": aperti,
        "problemi": problemi[-50:],
    })

    print("\n=== esito ===")
    print("  nuovi: %d · aggiornati: %d · scartati: %d · chiusi rimossi: %d"
          % (nuovi, aggiornati, scartati, rimossi))
    print("  bandi aperti in archivio: %d" % len(aperti))
    if bloccato:
        print("  interrotto: %s" % bloccato)
    print("  scritto: %s" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
