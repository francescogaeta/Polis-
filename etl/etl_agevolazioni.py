#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_agevolazioni.py — catalogo di bandi e agevolazioni per l'assistente
civico di Polis, dal portale pubblico Incentivi.gov.it (MIMIT/Invitalia).

LICENZA: IODL 2.0, che consente il riuso anche commerciale e le opere
derivate con il solo obbligo di citare la fonte. È la ragione per cui
questa fonte è utilizzabile in Polis, mentre altre no.

COSA COPRE E COSA NO — va detto all'utente, non nascosto
--------------------------------------------------------
Il catalogo è alimentato dal Registro Nazionale degli Aiuti e copre misure
nazionali, regionali e camerali, MA è orientato agli incentivi al sistema
produttivo (imprese e professionisti). Le prestazioni sociali per la persona
(assegni, sussidi INPS) NON stanno qui: i loro requisiti vivono nelle
circolari, in forma testuale, e nell'app sono gestiti a parte con la
citazione della circolare.

RISPETTO DELLA FONTE
--------------------
Un file per esecuzione, download condizionale (se non è cambiato non si
riscarica), 2 secondi fra le richieste, stop immediato su 403/429.

NIENTE DATI INVENTATI
---------------------
Lo schema del CSV non è documentato in modo stabile: qui le colonne vengono
CERCATE, non presunte. Se non si riconosce nulla, lo script lo dichiara e si
ferma invece di produrre un catalogo sbagliato.

MODALITÀ MANUALE (quando il portale blocca il server)
-----------------------------------------------------
Incentivi.gov.it respinge gli accessi dai runner di GitHub: la richiesta va
in timeout. Dal browser invece funziona. Quindi:

  1. apri https://www.incentivi.gov.it/it/open-data
  2. scarica il file di export (CSV o ZIP)
  3. mettilo in una cartella e lancia:

       python3 etl_agevolazioni.py --locale ~/Downloads/incentivi

Il risultato è identico alla modalità automatica.

Uso:
  python3 etl_agevolazioni.py --out ../dati/agevolazioni.json
  python3 etl_agevolazioni.py --out ../dati/agevolazioni.json --locale CARTELLA
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L

PAGINA = "https://www.incentivi.gov.it/it/open-data"

# nomi possibili delle colonne: la fonte li ha cambiati nel tempo
C_TITOLO = ["titolo", "title", "nome", "denominazione"]
C_DESCR = ["descrizione", "cos_e", "cose", "abstract", "sintesi"]
C_CHI = ["a_chi_si_rivolge", "beneficiari", "destinatari", "soggetti_beneficiari"]
C_SOGG = ["soggetto_concedente", "amministrazione", "ente", "titolare"]
C_REG = ["regioni", "regione", "territorio", "ambito_territoriale"]
C_FORMA = ["forma_agevolazione", "forma", "tipo_agevolazione"]
C_APERT = ["data_apertura", "apertura", "data_inizio"]
C_CHIUS = ["data_chiusura", "chiusura", "data_fine", "scadenza"]
C_SETT = ["settore_attivita", "settore", "settori"]
C_URL = ["link_istituzionale", "url", "link", "sito"]
C_STATO = ["stato", "stato_misura", "status"]


def _col(riga, nomi):
    """Cerca una colonna per nome normalizzato, senza presumerne la forma."""
    norm = {}
    for k, v in riga.items():
        if not k:
            continue
        kk = re.sub(r"[^a-z0-9]+", "_", k.strip().lower()).strip("_")
        norm[kk] = v
    for n in nomi:
        if n in norm and str(norm[n]).strip():
            return str(norm[n]).strip()
    for n in nomi:                      # tentativo per contenimento
        for k, v in norm.items():
            if n in k and str(v).strip():
                return str(v).strip()
    return None


def trova_export(cli):
    """Ricava dalla pagina open data l'indirizzo dell'ultimo export.
    Non costruisce URL a mano: prende i link realmente presenti."""
    print("[Agevolazioni] cerco l'ultimo export sulla pagina open data")
    try:
        raw, _ = cli.scarica(PAGINA, "default", forza=True)
    except Exception as e:
        print("    pagina non raggiungibile (%s)" % e)
        return []
    if not raw:
        return []
    html = raw.decode("utf-8", "ignore")
    link = []
    for m in re.finditer(r'href="([^"]+\.(?:csv|zip|json))"', html, re.I):
        u = m.group(1)
        if not u.startswith("http"):
            u = "https://www.incentivi.gov.it" + (u if u.startswith("/") else "/" + u)
        if u not in link:
            link.append(u)
    link.sort(key=lambda u: re.findall(r"\d{4}-\d{1,2}-\d{1,2}", u), reverse=True)
    print("    trovati %d file scaricabili" % len(link))
    return link


def carica_righe(cli, url):
    """Scarica un export e ne ricava le righe, gestendo anche gli zip."""
    print("    scarico %s" % url.split("/")[-1])
    raw, cambiato = cli.scarica(url, "default")
    if raw is None:
        return None                     # invariato
    if url.lower().endswith(".zip"):
        dentro = L.estrai_zip(raw, ".csv")
        if not dentro:
            return []
        raw = dentro[max(dentro, key=lambda k: len(dentro[k]))]
    righe, intest = L.leggi_csv(raw)
    print("    righe: %d | colonne: %d" % (len(righe), len(intest)))
    return righe


def da_cartella(percorso):
    """Legge i file scaricati a mano dal portale. Accetta CSV, ZIP e JSON,
    e prende il file che contiene più righe utilizzabili."""
    print("[Manuale] leggo i file in %s" % percorso)
    if not os.path.isdir(percorso):
        print("    cartella inesistente")
        return []
    migliori = []
    for nome in sorted(os.listdir(percorso)):
        fp = os.path.join(percorso, nome)
        if not os.path.isfile(fp):
            continue
        est = nome.lower().rsplit(".", 1)[-1]
        if est not in ("csv", "zip", "json"):
            continue
        try:
            with open(fp, "rb") as f:
                raw = f.read()
        except Exception as e:
            print("    %s: non leggibile (%s)" % (nome, str(e)[:40]))
            continue
        righe = []
        try:
            if est == "zip":
                dentro = L.estrai_zip(raw, ".csv")
                if dentro:
                    raw = dentro[max(dentro, key=lambda k: len(dentro[k]))]
                    righe, _ = L.leggi_csv(raw)
            elif est == "csv":
                righe, _ = L.leggi_csv(raw)
            else:
                j = json.loads(raw.decode("utf-8", "ignore"))
                righe = j if isinstance(j, list) else (
                    j.get("data") or j.get("risultati") or j.get("items") or [])
                righe = [r for r in righe if isinstance(r, dict)]
        except Exception as e:
            print("    %s: non interpretabile (%s)" % (nome, str(e)[:50]))
            continue
        print("    %s → %d righe" % (nome, len(righe)))
        if len(righe) > len(migliori):
            migliori = righe
    return migliori


def normalizza(righe):
    """Da righe grezze a voci pulite. Scarta ciò che non ha un titolo:
    una voce senza titolo non è mostrabile."""
    voci, scartate = [], 0
    for r in righe:
        titolo = _col(r, C_TITOLO)
        if not titolo or len(titolo) < 4:
            scartate += 1
            continue
        reg_raw = _col(r, C_REG) or ""
        regioni = []
        for pezzo in re.split(r"[;,|/]", reg_raw):
            cod = L.codice_regione(pezzo.strip())
            if cod and L.REGIONI[cod] not in regioni:
                regioni.append(L.REGIONI[cod])
        voci.append({
            "titolo": titolo[:180],
            "descrizione": (_col(r, C_DESCR) or "")[:600],
            "chi": (_col(r, C_CHI) or "")[:300],
            "soggetto": (_col(r, C_SOGG) or "")[:120],
            "regioni": regioni,
            "forma": (_col(r, C_FORMA) or "")[:120],
            "apertura": _col(r, C_APERT),
            "chiusura": _col(r, C_CHIUS),
            "settore": (_col(r, C_SETT) or "")[:120],
            "stato": (_col(r, C_STATO) or ""),
            "url": _col(r, C_URL),
        })
    print("    voci valide: %d (scartate %d senza titolo)" % (len(voci), scartate))
    return voci


def segna_scadute(voci):
    """Segnala le misure già chiuse: l'utente deve saperlo."""
    oggi = datetime.now(timezone.utc).date()
    n = 0
    for v in voci:
        c = v.get("chiusura") or ""
        m1 = re.search(r"(\d{4})-(\d{2})-(\d{2})", c)
        m2 = re.search(r"(\d{2})/(\d{2})/(\d{4})", c)
        try:
            if m1:
                d = datetime(int(m1.group(1)), int(m1.group(2)),
                             int(m1.group(3))).date()
            elif m2:
                d = datetime(int(m2.group(3)), int(m2.group(2)),
                             int(m2.group(1))).date()
            else:
                continue
        except Exception:
            continue
        v["scaduta"] = d < oggi
        if v["scaduta"]:
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description="ETL agevolazioni — Incentivi.gov.it")
    ap.add_argument("--out", default="../dati/agevolazioni.json")
    ap.add_argument("--cache", default=".cache_etl")
    ap.add_argument("--locale",
                    help="cartella con l'export scaricato a mano dal browser")
    args = ap.parse_args()
    cli = L.Client(args.cache)

    print("Fonte: Incentivi.gov.it (MIMIT) · licenza IODL 2.0, uso commerciale "
          "ammesso citando la fonte.\n")

    # --- modalità manuale: nessuna richiesta di rete ---
    if args.locale:
        righe = da_cartella(args.locale)
        if not righe:
            print("\nNessun file utilizzabile trovato in %s" % args.locale)
            print("Scarica l'export da https://www.incentivi.gov.it/it/open-data "
                  "e mettilo in quella cartella.")
            return 1
        voci = normalizza(righe)
        if not voci:
            print("\nSchema non riconosciuto: mi fermo invece di produrre un "
                  "catalogo sbagliato.")
            return 1
        return scrivi_catalogo(voci, args.out)

    link = trova_export(cli)
    if not link:
        print("\nNessun export trovato: il portale respinge i client automatici "
              "oppure ha cambiato pagina.\nNon invento nulla: l'app continuerà a "
              "mostrare il catalogo come 'in costruzione'.")
        return 1

    voci = None
    for u in link[:3]:
        try:
            righe = carica_righe(cli, u)
        except L.FonteBloccata as e:
            print("\n!! %s" % e)
            return 2
        except Exception as e:
            print("    non leggibile (%s)" % str(e)[:70])
            continue
        if righe is None:
            print("    invariato dall'ultima volta")
            print("\nNessun aggiornamento disponibile.")
            return 0
        if righe:
            voci = normalizza(righe)
            if voci:
                break

    if not voci:
        print("\nSchema non riconosciuto in nessun file scaricato: mi fermo "
              "invece di produrre un catalogo sbagliato.")
        return 1

    return scrivi_catalogo(voci, args.out)


def scrivi_catalogo(voci, destinazione):
    n_scadute = segna_scadute(voci)
    attive = [v for v in voci if not v.get("scaduta")]

    per_regione = {}
    for v in voci:
        for r in (v["regioni"] or ["(nazionale)"]):
            per_regione[r] = per_regione.get(r, 0) + 1

    L.scrivi_json(destinazione, {
        "_generato": L.ora(),
        "aggiornato": datetime.now(timezone.utc).strftime("%d/%m/%Y"),
        "fonte": "Incentivi.gov.it · MIMIT (IODL 2.0)",
        "licenza": "IODL 2.0 — riuso consentito citando la fonte",
        "_avvertenza": ("Catalogo orientato agli incentivi al sistema produttivo. "
                        "Le prestazioni sociali per la persona non sono incluse. "
                        "Requisiti e scadenze possono cambiare e i fondi possono "
                        "esaurirsi: verificare sempre sulla pagina ufficiale."),
        "n_totale": len(voci),
        "n_attive": len(attive),
        "n_scadute": n_scadute,
        "copertura_regionale": per_regione,
        "voci": attive,
    })

    print("\n=== catalogo aggiornato ===")
    print("  voci totali:   %d" % len(voci))
    print("  ancora aperte: %d" % len(attive))
    print("  già scadute:   %d (escluse dal file)" % n_scadute)
    print("  regioni coperte: %d"
          % len([k for k in per_regione if k != "(nazionale)"]))
    print("  scritto: %s" % os.path.abspath(destinazione))
    return 0


if __name__ == "__main__":
    sys.exit(main())
