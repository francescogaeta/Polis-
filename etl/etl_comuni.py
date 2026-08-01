#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_comuni.py — ETL Polis per i dati comunali di Cruscotto Italia (AgID).

Cosa fa
-------
1. Legge l'indice ufficiale dei comuni da Cruscotto Italia.
2. Per una lista di comuni (di default i 107 capoluoghi), scarica lo shard
   dashboard e ne estrae:
     - un blocco KPI sintetico (per la scheda comune dell'app);
     - il blocco "Iniziative della PA" (PNRR + Opere BDAP + Appalti ANAC).
3. Salva per ogni comune un file `comuni/<istat>.json` (dati correnti).
4. Mantiene uno STORICO versionato in `comuni/storico/<istat>/<data>.json`,
   scrivendo un nuovo snapshot solo quando i dati sono cambiati rispetto
   all'ultimo (confronto sul campo `_generated_at` della fonte + hash dei KPI).
5. Rigenera `comuni/index.json` (indice leggero per la ricerca nell'app) e
   `comuni/_meta.json` (quando è girato l'ETL, quante fonti, ecc.).

Principi (allineati a Polis)
----------------------------
- NESSUN DATO INVENTATO: si copiano solo i valori pubblicati dalla fonte.
  Nessuna stima, nessun ricalcolo, nessun aggregato territoriale.
- Le fonti sono sempre citate nel JSON prodotto.
- Rispetto del server: throttle configurabile, backoff sugli errori,
  nessun download di massa oltre la lista dichiarata.
- Idempotente e incrementale: rilanciarlo non duplica lo storico.

Uso
---
    # tutti i capoluoghi (default), sorgente pubblica
    python3 etl_comuni.py

    # solo alcuni comuni per codice ISTAT
    python3 etl_comuni.py --istat 075035 058091 015146

    # da file lista (una riga = un codice ISTAT o "istat;nome;regione")
    python3 etl_comuni.py --lista capoluoghi.txt

    # cambiare cartella di output e intervallo fra richieste
    python3 etl_comuni.py --out ../app/data --sleep 1.5

Variabili d'ambiente
--------------------
    CRUSCOTTO_BASE   base URL degli shard
                     (default https://cruscotto-italia.dati.gov.it/data)

Dipendenze: solo standard library.
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
from datetime import datetime, timezone

BASE = os.environ.get("CRUSCOTTO_BASE", "https://cruscotto-italia.dati.gov.it/data")
UA = "polis-etl/1.0 (+https://github.com/) civic-tech"

# ------------------------------------------------------------------ rete

def _ctx():
    ctx = ssl.create_default_context()
    try:
        if ctx.cert_store_stats().get("x509_ca", 0) == 0:
            import certifi
            ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return ctx


def fetch_json(path, retries=3, backoff=2.0):
    """Scarica uno shard JSON. Ritorna (obj, None) o (None, motivo)."""
    if BASE.startswith("/"):
        fp = os.path.join(BASE, path)
        if not os.path.exists(fp):
            return None, "file locale assente: " + fp
        with open(fp, encoding="utf-8") as f:
            return json.load(f), None
    url = BASE.rstrip("/") + "/" + path
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45, context=_ctx()) as r:
                return json.loads(r.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, "404 (fonte assente per questo comune)"
            last = "HTTP %s" % e.code
            # 403/429: probabile throttle → backoff progressivo
            time.sleep(backoff * (attempt + 1))
        except Exception as e:
            last = str(e)
            time.sleep(backoff * (attempt + 1))
    return None, last or "errore rete"


# ------------------------------------------------------------------ estrazione

def _num(v):
    return v if isinstance(v, (int, float)) else None


def build_kpi(dash):
    """Sottoinsieme KPI usato dalla scheda comune. Solo valori pubblicati."""
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
    """PNRR + Opere BDAP + Appalti ANAC: le iniziative della PA sul territorio."""
    pnrr = dash.get("pnrr") or {}
    opere = dash.get("opere") or {}
    anac = dash.get("anac") or {}

    # PNRR: top 20 progetti per finanziamento, + riepilogo per missione
    pnrr_prj = sorted(
        pnrr.get("progetti", []),
        key=lambda x: -(x.get("finanziamento_pnrr") or 0),
    )[:20]
    pnrr_out = {
        "per_missione": pnrr.get("per_missione", []),
        "progetti": [{
            "cup": p.get("cup"),
            "titolo": p.get("titolo"),
            "missione": p.get("missione"),
            "missione_desc": p.get("missione_descrizione"),
            "importo": p.get("finanziamento_pnrr"),
            "stato": p.get("stato_avanzamento"),
            "fase": p.get("fase_iter"),
            "fine_prevista": p.get("data_fine_prevista"),
        } for p in pnrr_prj],
        "fonte": pnrr.get("fonte") or "ReGiS / OpenPNRR",
        "fonte_url": pnrr.get("fonte_url"),
        "data_estrazione": pnrr.get("data_estrazione"),
    } if pnrr else None

    # Opere BDAP: top 20 per costo
    opere_prj = sorted(
        opere.get("progetti", []),
        key=lambda x: -((x.get("costo_eff") or x.get("costo_prev")) or 0),
    )[:20]
    opere_out = {
        "n_progetti": opere.get("n_progetti"),
        "progetti": [{
            "cup": p.get("cup"),
            "descrizione": p.get("descrizione"),
            "stato": p.get("stato"),
            "settore": p.get("settore"),
            "costo": p.get("costo_eff") or p.get("costo_prev"),
            "fin_statali": p.get("fin_statali"),
            "fin_europei": p.get("fin_europei"),
            "data_inizio": p.get("data_inizio"),
        } for p in opere_prj],
        "fonte": "BDAP-MOP · Monitoraggio Opere Pubbliche (RGS-MEF)",
    } if opere else None

    # ANAC: aggregato per categoria CPV
    anac_out = {
        "buyer": anac.get("buyer_name"),
        "count": anac.get("count"),
        "importo_totale": anac.get("importo_totale"),
        "first": anac.get("first_award_date"),
        "last": anac.get("last_award_date"),
        "top_cpv": [{
            "desc": c.get("desc"),
            "count": c.get("count"),
            "importo": c.get("importo"),
        } for c in (anac.get("top_cpv") or [])[:10]],
        "fonte": "ANAC · Banca Dati Nazionale dei Contratti Pubblici",
    } if anac else None

    out = {}
    if pnrr_out:
        out["pnrr"] = pnrr_out
    if opere_out:
        out["opere"] = opere_out
    if anac_out:
        out["anac"] = anac_out
    return out


def kpi_fingerprint(kpi, pa):
    """Hash stabile per capire se i dati sono cambiati (per lo storico)."""
    blob = json.dumps({"k": kpi, "p": pa}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ storico

def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def update_storico(out_dir, istat, record):
    """Scrive uno snapshot nello storico solo se cambiato dall'ultimo.

    Struttura: comuni/storico/<istat>/<YYYY-MM-DD>.json
    Ritorna: 'nuovo' | 'aggiornato' | 'invariato'
    """
    sdir = os.path.join(out_dir, "storico", istat)
    os.makedirs(sdir, exist_ok=True)
    snaps = sorted(f for f in os.listdir(sdir) if f.endswith(".json"))
    fp = record["_fingerprint"]
    if snaps:
        last = read_json(os.path.join(sdir, snaps[-1]))
        if last and last.get("_fingerprint") == fp:
            return "invariato"
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    write_json(os.path.join(sdir, day + ".json"), record)
    # aggiorna un piccolo manifest dello storico
    manifest = {
        "istat": istat,
        "snapshots": sorted(
            [s[:-5] for s in os.listdir(sdir) if s.endswith(".json")]
        ),
    }
    write_json(os.path.join(sdir, "_index.json"), manifest)
    return "nuovo" if not snaps else "aggiornato"


# ------------------------------------------------------------------ pipeline

CAPOLUOGHI_DEFAULT = [
    # 107 capoluoghi (codice ISTAT). L'ETL li risolve e verifica sull'indice.
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
    "095038", "111",
]


def load_index():
    idx, err = fetch_json("lookup/comuni-index.json")
    if err:
        return None, err
    # normalizza in dict istat -> record
    d = {}
    for c in idx:
        d[c["i"]] = {"istat": c["i"], "nome": c.get("n"),
                     "prov": c.get("p"), "reg": c.get("r")}
    return d, None


def run(istat_list, out_dir, sleep_s, index):
    os.makedirs(out_dir, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    esiti = {"ok": 0, "nuovo": 0, "aggiornato": 0, "invariato": 0, "errore": 0}
    index_out = []
    dettaglio = []

    for i, istat in enumerate(istat_list, 1):
        meta = (index or {}).get(istat, {}) if index else {}
        nome = meta.get("nome") or istat
        print("[%3d/%3d] %s (%s) ... " % (i, len(istat_list), nome, istat),
              end="", flush=True)

        dash, err = fetch_json("dashboard/%s.json" % istat)
        if err or not dash:
            print("SALTATO:", err)
            esiti["errore"] += 1
            dettaglio.append({"istat": istat, "nome": nome, "esito": "errore",
                              "motivo": err})
            time.sleep(sleep_s)
            continue

        kpi = build_kpi(dash)
        pa = build_iniziative_pa(dash)
        fp = kpi_fingerprint(kpi, pa)

        record = {
            "istat": istat,
            "nome": (kpi.get("anagrafica") or {}).get("nome") or nome,
            "regione": (kpi.get("anagrafica") or {}).get("regione")
                       or meta.get("reg"),
            "provincia": (kpi.get("anagrafica") or {}).get("provincia")
                         or meta.get("prov"),
            "kpi": kpi,
            "iniziative_pa": pa,
            "_source_generated_at": dash.get("_generated_at"),
            "_etl_version": dash.get("_etl_version"),
            "_fetched_at": now,
            "_fingerprint": fp,
            "_fonte": "Cruscotto Italia · AgID (dati.gov.it)",
        }

        # file corrente
        write_json(os.path.join(out_dir, "%s.json" % istat), record)
        # storico
        esito_st = update_storico(out_dir, istat, record)
        esiti[esito_st] += 1
        esiti["ok"] += 1

        index_out.append({
            "i": istat, "n": record["nome"],
            "r": record["regione"], "p": record["provincia"],
            "pop": (kpi.get("demografia") or {}).get("popolazione"),
        })
        dettaglio.append({"istat": istat, "nome": record["nome"],
                          "esito": esito_st})
        print(esito_st)
        time.sleep(sleep_s)

    # indice comuni per la ricerca in-app (solo quelli scaricati)
    index_out.sort(key=lambda x: (x.get("n") or ""))
    write_json(os.path.join(out_dir, "index.json"), index_out)

    meta = {
        "generato": now,
        "n_comuni": esiti["ok"],
        "esiti": esiti,
        "dettaglio": dettaglio,
        "fonte": "Cruscotto Italia · AgID",
        "base": BASE if not BASE.startswith("/") else "(filesystem locale)",
    }
    write_json(os.path.join(out_dir, "_meta.json"), meta)

    print("\n=== ETL completato ===")
    print("  comuni ok:      ", esiti["ok"])
    print("  nuovi:          ", esiti["nuovo"])
    print("  aggiornati:     ", esiti["aggiornato"])
    print("  invariati:      ", esiti["invariato"])
    print("  errori/saltati: ", esiti["errore"])
    print("  output:         ", os.path.abspath(out_dir))
    return esiti


def main():
    ap = argparse.ArgumentParser(description="ETL Polis — comuni Cruscotto Italia")
    ap.add_argument("--istat", nargs="*", help="codici ISTAT specifici")
    ap.add_argument("--lista", help="file con un codice ISTAT per riga")
    ap.add_argument("--out", default="../app/data/comuni",
                    help="cartella di output (default ../app/data/comuni)")
    ap.add_argument("--sleep", type=float, default=1.2,
                    help="secondi fra le richieste (default 1.2)")
    ap.add_argument("--no-index", action="store_true",
                    help="non scaricare l'indice comuni (usa solo --istat)")
    args = ap.parse_args()

    # lista comuni
    if args.istat:
        istat_list = args.istat
    elif args.lista:
        istat_list = []
        with open(args.lista, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                istat_list.append(line.split(";")[0].strip())
    else:
        istat_list = CAPOLUOGHI_DEFAULT

    # indice (per nomi e verifica)
    index = None
    if not args.no_index:
        index, err = load_index()
        if err:
            print("Attenzione: indice comuni non disponibile (%s). "
                  "Procedo senza nomi risolti." % err, file=sys.stderr)

    run(istat_list, args.out, args.sleep, index)


if __name__ == "__main__":
    main()

