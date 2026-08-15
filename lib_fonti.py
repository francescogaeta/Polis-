#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lib_fonti.py — infrastruttura condivisa degli ETL territoriali di Polis.

Contiene una sola cosa importante: il rispetto dei limiti che ogni fonte
pubblica impone. I limiti sono dichiarati qui, in un unico posto, con la
fonte della regola accanto. NON vanno alzati per andare più veloci.

  ISTAT SDMX  → 5 query al minuto per IP; oltre il limite scatta un blocco
                di 1-2 giorni. Qui teniamo 13 s fra le richieste (≈4,6/min).
  BDAP-MOP    → nessun limite numerico pubblicato, ma il portale può
                respingere client automatici: User-Agent esplicito e 2 s
                fra le richieste.
  CPT         → file statici; 2 s di cortesia.
  OpenCoesione / Italia Domani → file statici; 2 s di cortesia.

Cruscotto Italia NON è gestito qui: ha un ETL separato con i suoi limiti
(12 comuni ogni 600 s).

Fornisce inoltre:
  * download condizionale (ETag / Last-Modified) per non riscaricare
    file immutati e per capire quando i dati sono davvero cambiati;
  * lettura CSV robusta: separatore e codifica rilevati, non presunti;
  * scrittura JSON compatta.
"""

import csv
import io
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

UA = ("polis-etl/1.0 (app civica; contatto via repository del progetto) "
      "Mozilla/5.0 (compatible)")

# --- limiti per fonte: secondi minimi fra due richieste ---
LIMITI = {
    "istat": 13.0,          # 5 query/min dichiarate da ISTAT → stiamo sotto
    "bdap": 2.0,
    "cpt": 2.0,
    "opencoesione": 2.0,
    "italiadomani": 2.0,
    "default": 2.0,
}


class FonteBloccata(Exception):
    """La fonte ha risposto con un blocco (403/429). Ci si ferma."""


class Client:
    """Client HTTP che rispetta i limiti e ricorda cosa ha già scaricato."""

    def __init__(self, cache_dir, verbose=True):
        self.cache_dir = cache_dir
        self.verbose = verbose
        self.ultimo = {}                     # fonte -> timestamp
        os.makedirs(cache_dir, exist_ok=True)
        self.stato_path = os.path.join(cache_dir, "_stato_http.json")
        self.stato = leggi_json(self.stato_path) or {}

    # ---------- limiti ----------
    def _attendi(self, fonte):
        minimo = LIMITI.get(fonte, LIMITI["default"])
        ultimo = self.ultimo.get(fonte)
        if ultimo is not None:
            passato = time.monotonic() - ultimo
            if passato < minimo:
                time.sleep(minimo - passato)
        self.ultimo[fonte] = time.monotonic()

    # ---------- download ----------
    def scarica(self, url, fonte="default", accept=None, forza=False):
        """Scarica un URL rispettando i limiti della fonte.

        Ritorna (contenuto_bytes, cambiato_bool).
        Se il server risponde 304 (non modificato) ritorna (None, False).
        Solleva FonteBloccata su 403/429.
        """
        self._attendi(fonte)
        headers = {"User-Agent": UA}
        if accept:
            headers["Accept"] = accept
        memo = self.stato.get(url, {}) if not forza else {}
        if memo.get("etag"):
            headers["If-None-Match"] = memo["etag"]
        if memo.get("last_modified"):
            headers["If-Modified-Since"] = memo["last_modified"]

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180, context=_ctx()) as r:
                data = r.read()
                self.stato[url] = {
                    "etag": r.headers.get("ETag"),
                    "last_modified": r.headers.get("Last-Modified"),
                    "scaricato": datetime.now(timezone.utc).isoformat(),
                    "byte": len(data),
                }
                scrivi_json(self.stato_path, self.stato)
                if self.verbose:
                    print("    scaricati %s da %s" % (_umano(len(data)), _host(url)))
                return data, True
        except urllib.error.HTTPError as e:
            if e.code == 304:
                if self.verbose:
                    print("    non modificato (304): salto")
                return None, False
            if e.code in (403, 429):
                raise FonteBloccata(
                    "%s ha risposto %s su %s. Ci fermiamo: non ritentiamo e "
                    "non aggiriamo il blocco." % (fonte.upper(), e.code, _host(url)))
            raise
        return None, False

    def json(self, url, fonte="default"):
        data, _ = self.scarica(url, fonte, accept="application/json", forza=True)
        return json.loads(data.decode("utf-8")) if data else None


def _ctx():
    ctx = ssl.create_default_context()
    try:
        if ctx.cert_store_stats().get("x509_ca", 0) == 0:
            import certifi
            ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return ctx


def _host(url):
    try:
        return url.split("/")[2]
    except Exception:
        return url[:40]


def _umano(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.0f %s" % (n, u)
        n /= 1024.0
    return "%.1f TB" % n


# ------------------------------------------------------------------ CSV

def leggi_csv(data, attesi=None):
    """Legge un CSV rilevando codifica e separatore invece di presumerli.

    `attesi`: elenco di nomi di colonna che dovrebbero esserci. Se mancano
    tutti, significa che lo schema è cambiato: meglio saperlo subito.
    Ritorna (righe_come_dict, intestazioni).
    """
    testo = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            testo = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if testo is None:
        raise ValueError("codifica del CSV non riconosciuta")

    prima = testo.split("\n", 1)[0]
    sep = ";" if prima.count(";") > prima.count(",") else ","
    righe = list(csv.DictReader(io.StringIO(testo), delimiter=sep))
    intest = righe[0].keys() if righe else []
    intest = list(intest)

    if attesi:
        trovati = [c for c in attesi if c in intest]
        if not trovati:
            raise ValueError(
                "schema inatteso: nessuna delle colonne %s è presente. "
                "Trovate invece: %s" % (attesi, intest[:12]))
    return righe, intest


def estrai_zip(data, filtro=".csv"):
    """Estrae dai byte di uno zip i file che finiscono con `filtro`."""
    out = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for nome in z.namelist():
            if nome.lower().endswith(filtro):
                out[nome] = z.read(nome)
    return out


def numero(v, decimale_virgola=True):
    """Converte in float un valore che può usare la virgola decimale e i
    separatori di migliaia. Ritorna None se non è un numero."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in ("-", "..", "n.d.", "ND", "N.D."):
        return None
    if decimale_virgola:
        s = s.replace(".", "").replace(",", ".") if ("," in s) else s
    try:
        return float(s)
    except ValueError:
        return None


# ------------------------------------------------------------------ file

def leggi_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def scrivi_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def ora():
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ regioni

# Codici ISTAT regione (2 cifre) → nome. Usati come chiave stabile ovunque.
REGIONI = {
    "01": "Piemonte", "02": "Valle d'Aosta", "03": "Lombardia",
    "04": "Trentino-Alto Adige", "05": "Veneto", "06": "Friuli-Venezia Giulia",
    "07": "Liguria", "08": "Emilia-Romagna", "09": "Toscana", "10": "Umbria",
    "11": "Marche", "12": "Lazio", "13": "Abruzzo", "14": "Molise",
    "15": "Campania", "16": "Puglia", "17": "Basilicata", "18": "Calabria",
    "19": "Sicilia", "20": "Sardegna",
}

# Alias per riconoscere la regione quando la fonte scrive il nome per esteso
_ALIAS = {
    "valle daosta": "02", "valle d aosta": "02", "valle d'aosta": "02",
    "vallee daoste": "02", "trentino alto adige": "04",
    "trentino-alto adige/sudtirol": "04", "friuli venezia giulia": "06",
    "friuli-venezia giulia": "06", "emilia romagna": "08",
    "emilia-romagna": "08", "provincia autonoma di trento": "04",
    "provincia autonoma di bolzano": "04", "bolzano": "04", "trento": "04",
}


def codice_regione(valore):
    """Ricava il codice regione a 2 cifre da forme diverse:
    '01', '1', '01 - Piemonte', 'Piemonte', 'PIEMONTE'."""
    if valore is None:
        return None
    s = str(valore).strip()
    if not s:
        return None
    # forma "01 - Piemonte" o "01"
    testa = s.split("-")[0].strip()
    if testa.isdigit():
        c = "%02d" % int(testa)
        if c in REGIONI:
            return c
    if s.isdigit():
        c = "%02d" % int(s)
        if c in REGIONI:
            return c
    # per nome
    n = (s.lower().replace("'", "").replace("’", "")
         .replace("_", " ").strip())
    for cod, nome in REGIONI.items():
        if nome.lower().replace("'", "") == n:
            return cod
    if n in _ALIAS:
        return _ALIAS[n]
    for chiave, cod in _ALIAS.items():
        if n.startswith(chiave):
            return cod
    return None


def regione_da_istat_comune(istat6):
    """La regione NON è deducibile dalle prime cifre del codice comune in
    modo affidabile (le province non seguono l'ordine regionale in tutti i
    casi). Va usata la tabella di raccordo ISTAT: questa funzione esiste
    solo per segnalare esplicitamente che la scorciatoia non va usata."""
    raise NotImplementedError(
        "usare la tabella di raccordo ISTAT (etl_istat.py), non le cifre del codice")
