#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_comuni.py — ETL Polis per i dati comunali di Cruscotto Italia (AgID).

=====================  USO RESPONSABILE DELLA FONTE  =====================
Il server di Cruscotto Italia è pubblico ma ha limiti espliciti, e superarli
fa bloccare l'indirizzo IP per un'ora. Questo script li rispetta:

  * almeno 0,5 s fra una richiesta e l'altra          (MIN_INTERVAL)
  * al massimo 12 comuni distinti ogni 600 s          (MAX_COMUNI / WINDOW)
  * se il server risponde 403 o 429, si FERMA subito e lo dichiara:
    non ritenta, non aggira, non cambia indirizzo.

Questi valori NON vanno alzati per andare più veloci. Se servono più comuni,
si fanno più esecuzioni distanziate nel tempo: lo script tiene un segnalibro
(cursore) e riprende da dove era rimasto.

Non ricostruisce aggregati territoriali (medie regionali, classifiche) con
scaricamenti di massa: la fonte non li pubblica e non vanno stimati.
==========================================================================

Cosa fa
-------
1. Legge l'indice ufficiale dei comuni.
2. Per un LOTTO di comuni (default 12, cioè una finestra) scarica lo shard ed
   estrae KPI + Iniziative della PA (PNRR, opere BDAP, appalti ANAC).
3. Salva `comuni/<istat>.json` e aggiorna lo STORICO versionato
   `comuni/storico/<istat>/<data>.json`, solo se i dati sono cambiati.
4. Aggiorna `comuni/index.json`, `comuni/_meta.json` e il cursore.

Uso
---
    # un lotto conforme (12 comuni) e poi si ferma: adatto al run notturno
    python3 etl_comuni.py --out ../app/data/comuni

    # comuni specifici (max 12 per esecuzione)
    python3 etl_comuni.py --out ../app/data/comuni --istat 048017 058091

    # tutti i capoluoghi in una sola esecuzione: LENTO PER SCELTA
    # (si mette in pausa da solo, ~50 s per comune, circa 90 minuti)
    python3 etl_comuni.py --out ../app/data/comuni --tutti

Dipendenze: solo standard library (certifi se disponibile).
"""

import argparse
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.error
from collections import deque
from datetime import datetime, timezone

BASE = os.environ.get("CRUSCOTTO_BASE", "https://cruscotto-italia.dati.gov.it/data")
UA = "polis-etl/2.0 (civic-tech; rispetta i limiti della fonte)"

# --- limiti della fonte: non modificare per aggirare un blocco ---
MIN_INTERVAL = 0.5      # secondi minimi fra due richieste
MAX_COMUNI = 12         # comuni distinti per finestra
WINDOW = 600            # ampiezza della finestra, in secondi


class LimiteFonte(Exception):
    """Il server ha segnalato un blocco (403/429): ci fermiamo."""


class Throttle:
    """Fa rispettare i due limiti: intervallo minimo e comuni per finestra."""

    def __init__(self, min_interval=MIN_INTERVAL, max_comuni=MAX_COMUNI,
                 window=WINDOW, verbose=True):
        self.min_interval = min_interval
        self.max_comuni = max_comuni
        self.window = window
        self.verbose = verbose
        self.ultimo = None      # None, non 0.0: monotonic() può valere 0
        self.finestra = deque()
        self.visti = set()

    def attendi(self, istat):
        ora = time.monotonic()
        delta = ora - (self.ultimo if self.ultimo is not None else -1e9)
        if self.ultimo is not None and delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        if istat not in self.visti:
            while True:
                ora = time.monotonic()
                while self.finestra and (ora - self.finestra[0]) >= self.window:
                    self.finestra.popleft()
                if len(self.finestra) < self.max_comuni:
                    break
                pausa = self.window - (ora - self.finestra[0]) + 0.5
                if self.verbose:
                    print("    [limite fonte] %d comuni in %ds: attendo %.0f s"
                          % (self.max_comuni, self.window, pausa), flush=True)
                time.sleep(max(1.0, pausa))
            self.finestra.append(time.monotonic())
            self.visti.add(istat)
        self.ultimo = time.monotonic()


def _ctx():
    ctx = ssl.create_default_context()
    try:
        if ctx.cert_store_stats().get("x509_ca", 0) == 0:
            import certifi
            ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return ctx


def fetch_json(path, throttle=None, istat=None, retries=2):
    """Scarica uno shard. Ritorna (obj, None) o (None, motivo).
    Solleva LimiteFonte su 403/429: in quel caso ci si ferma."""
    if BASE.startswith("/"):
        fp = os.path.join(BASE, path)
        if not os.path.exists(fp):
            return None, "file locale assente"
        with open(fp, encoding="utf-8") as f:
            return json.load(f), None

    url = BASE.rstrip("/") + "/" + path
    last = None
    for tentativo in range(retries):
        if throttle and istat:
            throttle.attendi(istat)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45, context=_ctx()) as r:
                return json.loads(r.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise LimiteFonte(
                    "il server ha risposto %s: probabile blocco per eccesso di "
                    "richieste. Lo script si ferma. Riprovare più tardi (il "
                    "blocco dura circa un'ora). NON alzare i limiti." % e.code)
            if e.code == 404:
                return None, "404 (nessun dato per questo comune)"
            last = "HTTP %s" % e.code
            time.sleep(2.0 * (tentativo + 1))
        except Exception as e:
            last = str(e)
            time.sleep(2.0 * (tentativo + 1))
    return None, last or "errore di rete"


def build_kpi(dash):
    g = dash.get("kpi_summary") or {}
    ana = g.get("anagrafica") or dash.get("anagrafica") or {}
    out = {
        "anagrafica": {
            "istat": ana.get("istat") or ana.get("istat_code"),
            "nome": ana.get("nome") or ana.get("denominazione"),
            "provincia": ana.get("provincia_sigla") or ana.get("provincia"),
            "regione": ana.get("regione"),
        },
        "demografia": g.get("demografia"),
        "istruzione": g.get("istruzione_profilo"),
        "lavoro": g.get("lavoro_profilo"),
        "redditi": g.get("redditi_mef"),
        "scuole": g.get("scuole_miur"),
        "siope": g.get("siope"),
        "ambiente": g.get("ambiente"),
        "aria": g.get("aria_ispra"),
        "turismo": g.get("turismo"),
        "veicoli": g.get("veicoli_aci"),
        "banda_larga": g.get("banda_larga_agcom"),
        "ricarica_ev": g.get("ricarica_ev_pun"),
        "sanita": g.get("sanita_mds"),
        "terzo_settore": g.get("terzo_settore_runts"),
        "imprese": g.get("imprese_asia"),
        "beni_culturali": g.get("beni_culturali_mic"),
        "pendolarismo": g.get("pendolarismo"),
    }
    return {k: v for k, v in out.items() if v is not None}


def build_iniziative_pa(dash):
    pnrr = dash.get("pnrr") or {}
    opere = dash.get("opere") or {}
    anac = dash.get("anac") or {}
    out = {}

    if pnrr.get("progetti") is not None:
        prj = sorted(pnrr.get("progetti", []),
                     key=lambda x: -(x.get("finanziamento_pnrr") or 0))[:20]
        out["pnrr"] = {
            "per_missione": pnrr.get("per_missione", []),
            "progetti": [{
                "cup": p.get("cup"), "titolo": p.get("titolo"),
                "missione": p.get("missione"),
                "missione_desc": p.get("missione_descrizione"),
                "importo": p.get("finanziamento_pnrr"),
                "stato": p.get("stato_avanzamento"),
                "fase": p.get("fase_iter"),
                "fine_prevista": p.get("data_fine_prevista"),
            } for p in prj],
            "fonte": pnrr.get("fonte") or "ReGiS / OpenPNRR",
            "fonte_url": pnrr.get("fonte_url"),
            "data_estrazione": pnrr.get("data_estrazione"),
        }

    if opere.get("progetti") is not None:
        prj = sorted(opere.get("progetti", []),
                     key=lambda x: -((x.get("costo_eff") or x.get("costo_prev")) or 0))[:20]
        out["opere"] = {
            "n_progetti": opere.get("n_progetti"),
            "progetti": [{
                "cup": p.get("cup"), "descrizione": p.get("descrizione"),
                "stato": p.get("stato"), "settore": p.get("settore"),
                "costo": p.get("costo_eff") or p.get("costo_prev"),
                "fin_statali": p.get("fin_statali"),
                "fin_europei": p.get("fin_europei"),
                "data_inizio": p.get("data_inizio"),
            } for p in prj],
            "fonte": "BDAP-MOP · Monitoraggio Opere Pubbliche (RGS-MEF)",
        }

    if anac.get("count") is not None:
        out["anac"] = {
            "buyer": anac.get("buyer_name"),
            "count": anac.get("count"),
            "importo_totale": anac.get("importo_totale"),
            "first": anac.get("first_award_date"),
            "last": anac.get("last_award_date"),
            "top_cpv": [{"desc": c.get("desc"), "count": c.get("count"),
                         "importo": c.get("importo")}
                        for c in (anac.get("top_cpv") or [])[:10]],
            "fonte": "ANAC · Banca Dati Nazionale dei Contratti Pubblici",
        }
    return out


def fingerprint(kpi, pa):
    blob = json.dumps({"k": kpi, "p": pa}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def update_storico(out_dir, istat, record):
    sdir = os.path.join(out_dir, "storico", istat)
    os.makedirs(sdir, exist_ok=True)
    snaps = sorted(f for f in os.listdir(sdir)
                   if f.endswith(".json") and not f.startswith("_"))
    if snaps:
        last = read_json(os.path.join(sdir, snaps[-1]))
        if last and last.get("_fingerprint") == record["_fingerprint"]:
            return "invariato"
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    write_json(os.path.join(sdir, day + ".json"), record)
    write_json(os.path.join(sdir, "_index.json"), {
        "istat": istat,
        "snapshots": sorted(s[:-5] for s in os.listdir(sdir)
                            if s.endswith(".json") and not s.startswith("_")),
    })
    return "nuovo" if not snaps else "aggiornato"


CAPOLUOGHI = [
    "001272", "002004", "005002", "096024", "004078", "003106", "103077",
    "002158", "007003", "015146", "016024", "017029", "013075", "019036",
    "097042", "098031", "020030", "108033", "018110", "014061", "012133",
    "022205", "021008", "027042", "023091", "024116", "026086", "028060",
    "029044", "025034", "032006", "030129", "093033", "031007", "010025",
    "008031", "011015", "009056", "037006", "038008", "034027", "033032",
    "035033", "036023", "039014", "040012", "099014", "048017", "049009",
    "050026", "047014", "045012", "046017", "051002", "052032", "053011",
    "100005", "054039", "055032", "042002", "041044", "043023", "044007",
    "109006", "058091", "060038", "059011", "057059", "056037", "066049",
    "069022", "068028", "067041", "070006", "094023", "063049", "061022",
    "062008", "064008", "065116", "072006", "110002", "074001", "071024",
    "075035", "073027", "076063", "077014", "080063", "078045", "079023",
    "101010", "102047", "082053", "083048", "087015", "084001", "085004",
    "086009", "088009", "089017", "081021", "092009", "090064", "091051",
    "095038",
]


def carica_indice(throttle):
    idx, err = fetch_json("lookup/comuni-index.json", throttle, "__indice__")
    if err or not idx:
        return None, err or "indice vuoto"
    return {c["i"]: {"nome": c.get("n"), "prov": c.get("p"), "reg": c.get("r")}
            for c in idx}, None


def run(istat_list, out_dir, throttle, index):
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    esiti = {"ok": 0, "nuovo": 0, "aggiornato": 0, "invariato": 0, "errore": 0}
    fermato = None

    for i, istat in enumerate(istat_list, 1):
        meta = (index or {}).get(istat, {})
        nome = meta.get("nome") or istat
        print("[%3d/%3d] %-24s (%s) ... " % (i, len(istat_list), nome, istat),
              end="", flush=True)
        try:
            dash, err = fetch_json("dashboard/%s.json" % istat, throttle, istat)
        except LimiteFonte as e:
            print("STOP")
            fermato = str(e)
            break
        if err or not dash:
            print("saltato:", err)
            esiti["errore"] += 1
            continue

        kpi = build_kpi(dash)
        pa = build_iniziative_pa(dash)
        ana = kpi.get("anagrafica") or {}
        record = {
            "istat": istat,
            "nome": ana.get("nome") or nome,
            "regione": ana.get("regione") or meta.get("reg"),
            "provincia": ana.get("provincia") or meta.get("prov"),
            "kpi": kpi,
            "iniziative_pa": pa or None,
            "_source_generated_at": dash.get("_generated_at"),
            "_etl_version": dash.get("_etl_version"),
            "_fetched_at": now,
            "_fingerprint": fingerprint(kpi, pa),
            "_fonte": "Cruscotto Italia · AgID (dati.gov.it)",
        }
        write_json(os.path.join(out_dir, "%s.json" % istat), record)
        esito = update_storico(out_dir, istat, record)
        esiti[esito] += 1
        esiti["ok"] += 1
        print(esito)

    # indice ricostruito da TUTTI i file presenti, non solo da questo lotto
    index_out = []
    for f in sorted(os.listdir(out_dir)):
        if not f.endswith(".json") or f.startswith("_") or f == "index.json":
            continue
        rec = read_json(os.path.join(out_dir, f))
        if not rec or not rec.get("kpi"):
            continue
        index_out.append({
            "i": rec.get("istat"), "n": rec.get("nome"),
            "r": rec.get("regione"), "p": rec.get("provincia"),
            "pop": (rec["kpi"].get("demografia") or {}).get("popolazione"),
        })
    index_out.sort(key=lambda x: (x.get("n") or ""))
    write_json(os.path.join(out_dir, "index.json"), index_out)
    write_json(os.path.join(out_dir, "_meta.json"), {
        "generato": now,
        "comuni_in_archivio": len(index_out),
        "esiti_ultimo_lotto": esiti,
        "fermato_dal_limite": fermato,
        "fonte": "Cruscotto Italia · AgID",
        "limiti_rispettati": {
            "intervallo_minimo_s": MIN_INTERVAL,
            "comuni_per_finestra": MAX_COMUNI,
            "finestra_s": WINDOW,
        },
    })

    print("\n=== lotto completato ===")
    for k in ("ok", "nuovo", "aggiornato", "invariato", "errore"):
        print("  %-12s %d" % (k + ":", esiti[k]))
    print("  in archivio: %d comuni" % len(index_out))
    if fermato:
        print("\n!! FERMATO DAL LIMITE DELLA FONTE\n   %s" % fermato)
        return 2
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="ETL Polis — comuni Cruscotto Italia (rispetta i limiti della fonte)")
    ap.add_argument("--istat", nargs="*", help="codici ISTAT specifici")
    ap.add_argument("--out", default="../app/data/comuni")
    ap.add_argument("--lotto", type=int, default=MAX_COMUNI,
                    help="comuni per esecuzione (default %d = una finestra)" % MAX_COMUNI)
    ap.add_argument("--tutti", action="store_true",
                    help="tutti i capoluoghi in una sola esecuzione: si mette "
                         "in pausa da solo, circa 50 s per comune")
    args = ap.parse_args()

    throttle = Throttle()

    if args.istat:
        lista = args.istat
        if not args.tutti and len(lista) > args.lotto:
            print("Richiesti %d comuni: ne faccio %d per rispettare la finestra. "
                  "Usa --tutti per l'intera lista (più lento)."
                  % (len(lista), args.lotto))
            lista = lista[:args.lotto]
    else:
        cur_path = os.path.join(args.out, "_cursore.json")
        cur = read_json(cur_path) or {"pos": 0}
        pos = int(cur.get("pos", 0)) % len(CAPOLUOGHI)
        n = len(CAPOLUOGHI) if args.tutti else max(1, args.lotto)
        lista = [CAPOLUOGHI[(pos + k) % len(CAPOLUOGHI)] for k in range(n)]
        os.makedirs(args.out, exist_ok=True)
        write_json(cur_path, {"pos": (pos + n) % len(CAPOLUOGHI),
                              "aggiornato": datetime.now(timezone.utc).isoformat()})
        print("Cursore: parto dalla posizione %d di %d capoluoghi."
              % (pos, len(CAPOLUOGHI)))

    print("Limiti rispettati: %.1fs fra richieste, max %d comuni ogni %ds.\n"
          % (MIN_INTERVAL, MAX_COMUNI, WINDOW))

    try:
        index, err = carica_indice(throttle)
    except LimiteFonte as e:
        print("!! %s" % e)
        return 2
    if err:
        print("Attenzione: indice non disponibile (%s). Procedo senza nomi."
              % err, file=sys.stderr)
        index = None

    return run(lista, args.out, throttle, index)


if __name__ == "__main__":
    sys.exit(main())
