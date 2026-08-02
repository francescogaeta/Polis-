#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_cpt.py — spesa pubblica consolidata per regione (Conti Pubblici
Territoriali, Dipartimento per le politiche di coesione - PCM).

Perché CPT e non la somma dei comuni: CPT pubblica il dato GIÀ consolidato
per regione, cioè con i trasferimenti fra enti già eliminati. Sommare i
bilanci dei singoli comuni produrrebbe invece doppi conteggi (i soldi che lo
Stato gira alla Regione e la Regione al Comune verrebbero contati tre volte).
È anche il motivo per cui non serve — e non si deve — scaricare in massa i
dati comunali di altre fonti per costruire un aggregato regionale.

TRE REGOLE ANTI-DOPPIO-CONTEGGIO, applicate qui:
  1. un solo universo per volta: PA *oppure* SPA, mai sommati (SPA include PA);
  2. si usa la riga di TOTALE della categoria, senza sommarla alle sue voci;
  3. si scartano le righe di macroarea (Nord/Centro/Sud), che aggregano già
     le regioni: sommarle alle regioni raddoppierebbe tutto.

Importi: i file CPT sono in MILIONI DI EURO. Qui vengono convertiti in euro
una sola volta, e il fatto è dichiarato nell'output.

Uso:
  python3 etl_cpt.py --out ../data/territorio
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L

CATALOGO = ("https://politichecoesione.governo.it/it/politica-di-coesione/"
            "misurazione-valutazione-e-trasparenza/la-misurazione-delle-"
            "politiche-di-coesione/conti-pubblici-territoriali-cpt/i-dati/"
            "catalogo-open-cpt/")

# I path CPT contengono un hash che cambia a ogni ripubblicazione
# (es. /media/lh0nt3px/sp_pa_se_cetotcor.csv): NON vanno scritti come
# costanti eterne. Qui teniamo gli ultimi noti come ripiego, ma la strada
# maestra è ricavarli dal catalogo HTML.
NOTI = {
    "spesa_totale": "https://politichecoesione.governo.it/media/rnrpzckd/sp_pa_se_cetot.csv",
    "spesa_capitale": "https://politichecoesione.governo.it/media/sonn2z1j/sp_pa_se_cetotcap.csv",
    "spesa_corrente": "https://politichecoesione.governo.it/media/lh0nt3px/sp_pa_se_cetotcor.csv",
    "entrate": "https://politichecoesione.governo.it/media/yhqdfy5d/en_pa_cemacro.csv",
}
# nome del file (senza hash) → chiave logica
ATTESI = {
    "sp_pa_se_cetot": "spesa_totale",
    "sp_pa_se_cetotcap": "spesa_capitale",
    "sp_pa_se_cetotcor": "spesa_corrente",
    "en_pa_cemacro": "entrate",
}


def trova_url(cli):
    """Ricava dal catalogo gli URL correnti dei CSV, hash compreso.
    Se il catalogo non è raggiungibile, ripiega sugli ultimi URL noti."""
    print("[CPT] cerco gli URL correnti nel catalogo")
    urls = dict(NOTI)
    try:
        for pagina in ("", "?page=2", "?page=3"):
            data, _ = cli.scarica(CATALOGO + pagina, "cpt", forza=True)
            if not data:
                continue
            html = data.decode("utf-8", "ignore")
            for m in re.finditer(r'href="(/media/[^"]+?/([a-z0-9_]+)\.csv)"', html):
                path, nome = m.group(1), m.group(2)
                if nome in ATTESI:
                    urls[ATTESI[nome]] = "https://politichecoesione.governo.it" + path
        print("    trovati %d dataset nel catalogo" % len(urls))
    except Exception as e:
        print("    catalogo non leggibile (%s): uso gli URL noti" % e)
    return urls


def _campo(riga, contiene):
    """Trova il valore della prima colonna il cui nome contiene `contiene`."""
    for k, v in riga.items():
        if k and contiene.lower() in k.lower():
            return v
    return None


def _colonna_valore(intest):
    """La colonna dell'importo in CPT si chiama tipo 'S - Consolidato PA'."""
    for c in intest:
        if c and "consolidat" in c.lower():
            return c
    # ripiego: l'ultima colonna numerica
    return intest[-1] if intest else None


def elabora(nome_logico, data):
    """Da un CSV CPT a {codice_regione: {anno: euro}} per il totale.

    Applica le tre regole anti-doppio-conteggio descritte in testa al file.
    """
    righe, intest = L.leggi_csv(data, attesi=["Anno"])
    col_val = _colonna_valore(intest)
    print("    righe %d | colonna valore: %s" % (len(righe), col_val))

    per_reg = {}
    tenute = scartate_macro = scartate_nontot = 0
    settori = set()
    for r in righe:
        regione = _campo(r, "Regione")
        cod = L.codice_regione(regione)
        if not cod:
            # riga di macroarea o territorio non regionale: si scarta
            scartate_macro += 1
            continue
        categoria = str(_campo(r, "Categoria") or "")
        # REGOLA 2: solo le righe di TOTALE, mai sommate alle singole voci
        if "TOTALE" not in categoria.upper():
            scartate_nontot += 1
            continue
        settore = str(_campo(r, "Settore") or "")
        settori.add(settore)
        anno = str(r.get("Anno") or "").strip()
        val = L.numero(r.get(col_val))
        if not anno or val is None:
            continue
        # milioni di euro → euro
        euro = val * 1_000_000
        per_reg.setdefault(cod, {}).setdefault(anno, 0.0)
        per_reg[cod][anno] += euro
        tenute += 1

    print("    tenute %d righe | scartate: %d non regionali, %d non totali"
          % (tenute, scartate_macro, scartate_nontot))
    print("    settori sommati: %d" % len(settori))
    return per_reg


def main():
    ap = argparse.ArgumentParser(description="ETL CPT — spesa consolidata regionale")
    ap.add_argument("--out", default="../data/territorio")
    ap.add_argument("--cache", default=".cache_etl")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cli = L.Client(args.cache)

    urls = trova_url(cli)
    risultato = {}
    for chiave, url in urls.items():
        print("[CPT] %s" % chiave)
        try:
            data, cambiato = cli.scarica(url, "cpt")
        except L.FonteBloccata as e:
            print("\n!! %s" % e)
            return 2
        except Exception as e:
            print("    non scaricato (%s)" % e)
            continue
        if data is None:
            print("    invariato dall'ultima volta")
            continue
        try:
            risultato[chiave] = elabora(chiave, data)
        except Exception as e:
            print("    schema non riconosciuto (%s): dataset saltato" % e)

    if not risultato:
        print("\nNessun dato CPT aggiornato in questa esecuzione.")
        return 0

    # ultimo anno disponibile per ciascuna serie
    ultimo = {}
    for chiave, per_reg in risultato.items():
        anni = sorted({a for reg in per_reg.values() for a in reg})
        if not anni:
            continue
        aa = anni[-1]
        ultimo[chiave] = {"anno": aa,
                          "regioni": {r: round(v.get(aa, 0.0))
                                      for r, v in per_reg.items() if aa in v}}

    L.scrivi_json(os.path.join(args.out, "cpt_spesa.json"), {
        "_generato": L.ora(),
        "_fonte": "Conti Pubblici Territoriali · Dipartimento per le politiche di coesione (PCM)",
        "_universo": "PA (Pubblica Amministrazione), consolidato",
        "_unita": "euro (convertiti dai milioni di euro della fonte)",
        "_nota_metodo": ("Dati già consolidati a livello regionale dalla fonte: "
                         "i trasferimenti fra enti sono eliminati. Sono state usate "
                         "solo le righe di totale di categoria, escluse le macroaree."),
        "serie": {k: {r: {a: round(x) for a, x in v.items()}
                      for r, v in reg.items()} for k, reg in risultato.items()},
        "ultimo": ultimo,
    })
    print("\nScritto cpt_spesa.json — serie: %s" % ", ".join(risultato))
    return 0


if __name__ == "__main__":
    sys.exit(main())
