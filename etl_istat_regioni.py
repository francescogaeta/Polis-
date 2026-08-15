#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_istat_regioni.py — indicatori regionali dai servizi SDMX di ISTAT.

LICENZA DELLA FONTE
-------------------
ISTAT, CC BY 3.0 IT: riuso e adattamento liberi, anche a fini commerciali,
citando la fonte. Verificato tramite fonti_regioni.py, che blocca l'esecuzione
se la riga non è marcata come commerciale.

LIMITE DELLA FONTE, RISPETTATO
------------------------------
ISTAT dichiara 5 query al minuto per indirizzo IP; oltre il limite scatta un
blocco dell'IP di 1-2 giorni. Qui si aspettano 13 secondi fra una richiesta e
l'altra (≈4,6 al minuto) e ci si ferma dichiarandolo su 403/429, senza
ritentare e senza cambiare indirizzo. Con `--max` si limita quante richieste
fare in una esecuzione, così l'aggiornamento può essere ripartito su più
giorni come già si fa per i comuni.

COME EVITA DI INVENTARE
-----------------------
Nessun codice viene presunto. Per ogni indicatore lo script:
  1. verifica che il dataflow esista davvero      → /dataflow/IT1/{id}
  2. legge la sua struttura (dimensioni)          → ?references=all
  3. legge quali codici hanno davvero dati        → /availableconstraint/{id}
  4. per le dimensioni diverse da territorio e tempo sceglie il codice la cui
     ETICHETTA UFFICIALE è un totale ("totale", "tutte le voci"…), oppure il
     valore fissato nella configurazione dell'indicatore;
  5. se una dimensione resta ambigua, l'indicatore viene SALTATO e il motivo
     viene scritto nel rapporto. Non si tira a indovinare.
Se dopo il filtro una regione ha più di una riga per lo stesso anno, il dato
è ambiguo e l'indicatore viene scartato: meglio nessun numero che uno sbagliato.

USO
---
  python3 etl_istat_regioni.py --out ../data/territorio          # normale
  python3 etl_istat_regioni.py --esplora 150_915                 # ispeziona
  python3 etl_istat_regioni.py --solo occupazione,disoccupazione # un pezzo
  python3 etl_istat_regioni.py --max 6                           # a lotti
"""

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L
import fonti_regioni as FR

BASE = "https://esploradati.istat.it/SDMXWS/rest"
AGENZIA = "IT1"

# Codici NUTS2 delle regioni → codice ISTAT a due cifre usato in tutta Polis.
# Trentino-Alto Adige è pubblicato da ISTAT sia come regione sia come due
# province autonome: la mappatura tiene entrambe le forme e lo dichiara.
NUTS2 = {
    "ITC1": "01", "ITC2": "02", "ITC4": "03", "ITH1": "04", "ITH2": "04",
    "ITH10": "04", "ITH20": "04", "ITD1": "04", "ITD2": "04", "ITH3": "05",
    "ITH4": "06", "ITC3": "07", "ITH5": "08", "ITI1": "09", "ITI2": "10",
    "ITI3": "11", "ITI4": "12", "ITF1": "13", "ITF2": "14", "ITF3": "15",
    "ITF4": "16", "ITF5": "17", "ITF6": "18", "ITG1": "19", "ITG2": "20",
    "ITH": None, "ITC": None, "ITI": None, "ITF": None, "ITG": None,  # ripartizioni
    "IT": None,                                                        # Italia
}
# Codici che ISTAT usa nella forma numerica (ITTER107 può contenerli)
NUMERICI = {"%02d" % i: "%02d" % i for i in range(1, 21)}

# Etichette che indicano un totale. Non è un'euristica sui dati: è la lettura
# dell'etichetta che ISTAT stessa pubblica nella sua codelist.
ETICHETTE_TOTALE = (
    "totale", "total", "tutte le voci", "tutti", "tutte", "complesso",
    "totale complessivo", "all items", "all",
)

# Dimensioni che non vanno mai filtrate come "totale"
DIM_TEMPO = ("TIME_PERIOD", "TIME")
DIM_TERRITORIO = ("ITTER107", "REF_AREA", "TERRITORIO", "GEO")


# ---------------------------------------------------------------- indicatori
# dataflow: identificativo del flusso SDMX di ISTAT.
# fissi:    filtri da imporre su specifiche dimensioni, quando il totale non è
#           il dato che serve (per esempio la fascia d'età 15-64 anni).
# verso:    'alto' meglio in alto · 'basso' meglio in basso · 'neutro' nessuno.
INDICATORI = [
    {"chiave": "occupazione", "dataflow": "150_915",
     "titolo": "Tasso di occupazione",
     "spiegazione": "Quota di persone occupate fra quelle in età da lavoro.",
     "impatto": "Quante persone, dove vivi, hanno un lavoro.",
     "unita": "%", "verso": "alto", "percentuale": True,
     "fissi": {}},
    {"chiave": "disoccupazione", "dataflow": "151_914",
     "titolo": "Tasso di disoccupazione",
     "spiegazione": "Quota di persone che cercano lavoro senza trovarlo.",
     "impatto": "Quanto è difficile trovare un impiego nella tua regione.",
     "unita": "%", "verso": "basso", "percentuale": True,
     "fissi": {}},
    {"chiave": "neet", "dataflow": "172_931",
     "titolo": "Giovani che non studiano e non lavorano",
     "spiegazione": "Quota di giovani fuori sia dal lavoro sia dalla formazione.",
     "impatto": "Quanti ragazzi restano fermi dopo la scuola.",
     "unita": "%", "verso": "basso", "percentuale": True,
     "fissi": {}},
    {"chiave": "abbandono_scolastico", "dataflow": "52_607",
     "titolo": "Uscita precoce dalla scuola",
     "spiegazione": "Quota di 18-24enni che ha lasciato gli studi troppo presto.",
     "impatto": "Quanti ragazzi lasciano la scuola prima del diploma.",
     "unita": "%", "verso": "basso", "percentuale": True,
     "fissi": {}},
    {"chiave": "speranza_vita", "dataflow": "26_295",
     "titolo": "Speranza di vita alla nascita",
     "spiegazione": "Anni che una persona nata oggi può attendersi di vivere.",
     "impatto": "Quanto a lungo si vive, in media, dove sei nato.",
     "unita": "anni", "verso": "alto", "percentuale": True,
     "fissi": {}},
    {"chiave": "reddito_famiglie", "dataflow": "175_634",
     "titolo": "Reddito disponibile delle famiglie",
     "spiegazione": "Reddito che resta alle famiglie della regione dopo imposte "
                    "e contributi.",
     "impatto": "Quante risorse restano davvero nelle case della tua regione.",
     "unita": "euro", "verso": "alto", "percentuale": False,
     "fissi": {}},
    {"chiave": "valore_procapite", "dataflow": "270_255",
     "titolo": "Valori pro capite dei conti territoriali",
     "spiegazione": "Aggregati economici regionali già rapportati agli abitanti.",
     "impatto": "Quanta ricchezza si produce per ogni abitante.",
     "unita": "euro", "verso": "alto", "percentuale": True,
     "fissi": {}},
    {"chiave": "poverta", "dataflow": "34_727",
     "titolo": "Povertà",
     "spiegazione": "Diffusione della povertà fra le famiglie residenti.",
     "impatto": "Quante famiglie non arrivano a fine mese.",
     "unita": "%", "verso": "basso", "percentuale": True,
     "fissi": {}},
    {"chiave": "rischio_poverta", "dataflow": "498_1104",
     "titolo": "Rischio di povertà o esclusione sociale",
     "spiegazione": "Quota di persone a rischio di povertà o esclusione.",
     "impatto": "Quante persone rischiano di restare indietro.",
     "unita": "%", "verso": "basso", "percentuale": True,
     "fissi": {}},
    {"chiave": "delitti", "dataflow": "73_67",
     "titolo": "Delitti denunciati",
     "spiegazione": "Delitti denunciati dalle forze di polizia all'autorità "
                    "giudiziaria.",
     "impatto": "Quanti reati vengono denunciati dove vivi.",
     "unita": "delitti", "verso": "basso", "percentuale": False,
     "per_mille": True, "fissi": {}},
    {"chiave": "popolazione", "dataflow": "22_289",
     "titolo": "Popolazione residente",
     "spiegazione": "Persone residenti nella regione.",
     "impatto": "Quante persone vivono nella tua regione.",
     "unita": "abitanti", "verso": "neutro", "percentuale": True,
     "fissi": {}},
    {"chiave": "indicatori_demografici", "dataflow": "22_293",
     "titolo": "Indicatori demografici",
     "spiegazione": "Natalità, fecondità e saldo migratorio della regione.",
     "impatto": "Se la tua regione cresce, invecchia o si svuota.",
     "unita": "valore", "verso": "neutro", "percentuale": True,
     "fissi": {}},
]


# ---------------------------------------------------------------- SDMX
NS = {
    "m": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "s": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "c": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
}


def _testo_nome(nodo, lingua="it"):
    for n in nodo.findall("c:Name", NS):
        if n.get("{http://www.w3.org/XML/1998/namespace}lang") == lingua:
            return (n.text or "").strip()
    n = nodo.find("c:Name", NS)
    return (n.text or "").strip() if n is not None else ""


class Struttura:
    """Dimensioni di un dataflow e codici disponibili, letti dalla fonte."""

    def __init__(self, dimensioni, codelist, etichette):
        self.dimensioni = dimensioni      # elenco ordinato di nomi dimensione
        self.codelist = codelist          # dimensione -> id codelist
        self.etichette = etichette        # dimensione -> {codice: etichetta}


def leggi_struttura(client, dataflow):
    """Scarica dataflow + struttura e ne ricava dimensioni ed etichette."""
    url = "%s/dataflow/%s/%s?references=all&detail=full" % (BASE, AGENZIA, dataflow)
    data, _ = client.scarica(url, "istat", accept="application/xml", forza=True)
    if not data:
        return None, "il servizio non ha restituito la struttura"
    try:
        radice = ET.fromstring(data)
    except ET.ParseError as e:
        return None, "struttura non leggibile (%s)" % e

    if radice.find(".//s:Dataflow", NS) is None:
        return None, "il dataflow non esiste su ISTAT"

    dims, cl = [], {}
    for d in radice.findall(".//s:DimensionList/s:Dimension", NS):
        nome = d.get("id")
        if not nome:
            continue
        dims.append(nome)
        rif = d.find(".//s:Enumeration/Ref", NS)
        if rif is not None and rif.get("id"):
            cl[nome] = rif.get("id")
    tempo = radice.find(".//s:DimensionList/s:TimeDimension", NS)
    if tempo is not None and tempo.get("id"):
        dims.append(tempo.get("id"))

    if not dims:
        return None, "nessuna dimensione dichiarata nella struttura"

    etichette = {}
    for lista in radice.findall(".//s:Codelist", NS):
        idl = lista.get("id")
        voci = {}
        for code in lista.findall("s:Code", NS):
            voci[code.get("id")] = _testo_nome(code)
        if idl:
            etichette[idl] = voci

    per_dim = {d: etichette.get(cl.get(d), {}) for d in dims}
    return Struttura(dims, cl, per_dim), None


def leggi_disponibili(client, dataflow):
    """Codici che hanno davvero osservazioni, dimensione per dimensione."""
    url = "%s/availableconstraint/%s" % (BASE, dataflow)
    try:
        data, _ = client.scarica(url, "istat", accept="application/xml", forza=True)
    except Exception:
        return {}
    if not data:
        return {}
    try:
        radice = ET.fromstring(data)
    except ET.ParseError:
        return {}
    out = {}
    for kv in radice.findall(".//c:KeyValue", NS):
        dim = kv.get("id")
        valori = [v.text.strip() for v in kv.findall("c:Value", NS) if v.text]
        if dim and valori:
            out[dim] = valori
    return out


def _e_totale(etichetta):
    e = (etichetta or "").strip().lower()
    return e in ETICHETTE_TOTALE


def scegli_filtri(struttura, disponibili, fissi):
    """Per ogni dimensione decide il codice da usare.
    Ritorna (filtri, dimensione_territorio, problema)."""
    filtri, territorio = {}, None
    for dim in struttura.dimensioni:
        if dim in DIM_TEMPO:
            continue
        if dim in DIM_TERRITORIO:
            territorio = dim
            continue
        if dim in fissi:
            filtri[dim] = fissi[dim]
            continue
        codici = disponibili.get(dim) or list(struttura.etichette.get(dim, {}).keys())
        if not codici:
            continue                                  # dimensione libera
        if len(codici) == 1:
            filtri[dim] = codici[0]
            continue
        etich = struttura.etichette.get(dim, {})
        totali = [c for c in codici if _e_totale(etich.get(c))]
        if len(totali) == 1:
            filtri[dim] = totali[0]
            continue
        return None, territorio, (
            "dimensione «%s» ambigua: %d codici disponibili e %d etichettati "
            "come totale. Fissa il valore in INDICATORI['fissi'] dopo aver "
            "guardato l'esito di --esplora." % (dim, len(codici), len(totali)))
    if not territorio:
        return None, None, "nessuna dimensione territoriale riconosciuta"
    return filtri, territorio, None


def codice_regione_sdmx(valore):
    """Da codice SDMX a codice regione Polis. None per Italia e ripartizioni."""
    if valore is None:
        return None
    v = str(valore).strip().upper()
    if v in NUTS2:
        return NUTS2[v]
    if v in NUMERICI:
        return NUMERICI[v]
    return L.codice_regione(v)


def scarica_dati(client, dataflow, filtri, struttura, anni=6):
    """Scarica i dati in CSV SDMX filtrando solo per le dimensioni fissate."""
    chiave = []
    for dim in struttura.dimensioni:
        if dim in DIM_TEMPO:
            continue
        chiave.append(filtri.get(dim, ""))
    key = ".".join(chiave)
    from datetime import datetime
    inizio = datetime.now().year - anni
    url = "%s/data/%s/%s?startPeriod=%d&format=csv" % (BASE, dataflow, key, inizio)
    data, _ = client.scarica(
        url, "istat",
        accept="application/vnd.sdmx.data+csv;version=1.0.0", forza=True)
    return data


def estrai_regioni(csv_bytes, territorio):
    """Ultimo anno disponibile per regione. Se una regione ha più righe nello
    stesso anno il dato è ambiguo e l'indicatore va scartato."""
    righe, intest = L.leggi_csv(csv_bytes)
    if territorio not in intest:
        cand = [c for c in intest if c.upper() in DIM_TERRITORIO]
        if not cand:
            return None, None, ("colonna territoriale assente nel CSV "
                                "(trovate: %s)" % intest[:10])
        territorio = cand[0]
    col_tempo = next((c for c in intest if c.upper() in DIM_TEMPO), None)
    col_val = next((c for c in intest if c.upper() in ("OBS_VALUE", "VALUE")), None)
    if not col_tempo or not col_val:
        return None, None, "colonne di tempo o valore assenti nel CSV"

    per_reg = {}
    for r in righe:
        cod = codice_regione_sdmx(r.get(territorio))
        if not cod:
            continue                                   # Italia e ripartizioni
        anno = str(r.get(col_tempo) or "").strip()
        val = L.numero(r.get(col_val), decimale_virgola=False)
        if val is None or not anno:
            continue
        per_reg.setdefault(cod, {}).setdefault(anno, []).append(val)

    valori, anni_usati, ambigue = {}, [], []
    for cod, per_anno in per_reg.items():
        ultimo = max(per_anno.keys())
        v = per_anno[ultimo]
        if len(v) > 1:
            ambigue.append(cod)
            continue
        valori[cod] = v[0]
        anni_usati.append(ultimo)
    if ambigue:
        return None, None, ("dato ambiguo: %d regioni hanno più valori per lo "
                            "stesso anno dopo il filtro" % len(ambigue))
    anno = max(anni_usati) if anni_usati else None
    return valori, anno, None


# ---------------------------------------------------------------- esecuzione
def esplora(client, dataflow):
    print("Esploro il dataflow %s su ISTAT\n" % dataflow)
    st, err = leggi_struttura(client, dataflow)
    if err:
        print("  NON UTILIZZABILE: %s" % err)
        return 1
    disp = leggi_disponibili(client, dataflow)
    for dim in st.dimensioni:
        codici = disp.get(dim) or list(st.etichette.get(dim, {}).keys())
        etich = st.etichette.get(dim, {})
        print("  %-16s %d codici" % (dim, len(codici)))
        for c in codici[:12]:
            marca = "  ← totale" if _e_totale(etich.get(c)) else ""
            print("      %-12s %s%s" % (c, (etich.get(c) or "")[:52], marca))
        if len(codici) > 12:
            print("      … altri %d" % (len(codici) - 12))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Indicatori regionali ISTAT via SDMX")
    ap.add_argument("--out", default="../data/territorio")
    ap.add_argument("--cache", default="../.cache_etl")
    ap.add_argument("--solo", help="chiavi separate da virgola")
    ap.add_argument("--max", type=int, default=0,
                    help="quanti indicatori al massimo in questa esecuzione")
    ap.add_argument("--esplora", help="ispeziona un dataflow e non scarica dati")
    args = ap.parse_args()

    # cancello delle licenze: se ISTAT non fosse ammessa, ci si ferma qui
    scheda = FR.pretendi("istat")
    L.LIMITI.update(FR.limiti_per_client())

    client = L.Client(args.cache)

    if args.esplora:
        return esplora(client, args.esplora)

    voluti = INDICATORI
    if args.solo:
        chiavi = {s.strip() for s in args.solo.split(",")}
        voluti = [i for i in INDICATORI if i["chiave"] in chiavi]
    if args.max:
        voluti = voluti[:args.max]

    print("ISTAT · %s (%s)" % (scheda["licenza"], scheda["attribuzione"]))
    print("Limite rispettato: %s" % scheda["limite_nota"])
    print("Indicatori da provare: %d\n" % len(voluti))

    risultati, saltati = {}, []
    for ind in voluti:
        print("· %s (dataflow %s)" % (ind["titolo"], ind["dataflow"]))
        try:
            st, err = leggi_struttura(client, ind["dataflow"])
            if err:
                saltati.append((ind["chiave"], err))
                print("    saltato: %s" % err)
                continue
            disp = leggi_disponibili(client, ind["dataflow"])
            filtri, territorio, problema = scegli_filtri(st, disp, ind.get("fissi") or {})
            if problema:
                saltati.append((ind["chiave"], problema))
                print("    saltato: %s" % problema)
                continue
            csv_bytes = scarica_dati(client, ind["dataflow"], filtri, st)
            if not csv_bytes:
                saltati.append((ind["chiave"], "nessun dato restituito"))
                print("    saltato: nessun dato restituito")
                continue
            valori, anno, problema = estrai_regioni(csv_bytes, territorio)
            if problema:
                saltati.append((ind["chiave"], problema))
                print("    saltato: %s" % problema)
                continue
            if len(valori) < 15:
                saltati.append((ind["chiave"],
                                "solo %d regioni su 20: copertura insufficiente"
                                % len(valori)))
                print("    saltato: solo %d regioni su 20" % len(valori))
                continue
            risultati[ind["chiave"]] = {
                "titolo": ind["titolo"],
                "spiegazione": ind["spiegazione"],
                "impatto_cittadino": ind["impatto"],
                "unita": ind["unita"],
                "verso": ind["verso"],
                "gia_normalizzato": bool(ind.get("percentuale")),
                "per_mille": bool(ind.get("per_mille")),
                "anno": anno,
                "dataflow": ind["dataflow"],
                "filtri": filtri,
                "valori": valori,
                "n_regioni": len(valori),
            }
            print("    %d regioni · anno %s" % (len(valori), anno))
        except L.FonteBloccata as e:
            print("\n  FERMO: %s" % e)
            print("  I dati già raccolti vengono salvati; riprovare domani.")
            break
        except Exception as e:
            saltati.append((ind["chiave"], "errore imprevisto: %s" % e))
            print("    saltato: errore imprevisto: %s" % e)

    dest = os.path.join(args.out, "istat_regioni.json")
    L.scrivi_json(dest, {
        "_generato": L.ora(),
        "_fonte": "ISTAT — servizi SDMX (esploradati.istat.it)",
        "_licenza": scheda["licenza"],
        "_uso_commerciale": True,
        "_attribuzione": scheda["attribuzione"],
        "_limite_rispettato": scheda["limite_nota"],
        "indicatori": risultati,
        "saltati": [{"chiave": k, "motivo": m} for k, m in saltati],
    })

    print("\n=== esito ===")
    print("  indicatori raccolti: %d" % len(risultati))
    for k, v in risultati.items():
        print("    %-24s %2d regioni · %s" % (k, v["n_regioni"], v["anno"]))
    if saltati:
        print("  saltati: %d (motivo scritto nel file, non stimati)" % len(saltati))
        for k, m in saltati:
            print("    %-24s %s" % (k, m))
    print("  scritto: %s" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
