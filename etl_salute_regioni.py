#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_salute_regioni.py — posti letto ospedalieri per regione.

LICENZA DELLA FONTE
-------------------
Ministero della Salute, dati aperti, IODL 2.0: riuso e modifica liberi, anche
a fini commerciali, citando la fonte. Il controllo è fatto da fonti_regioni.py,
che interrompe l'esecuzione se la riga non è marcata come commerciale.

LIMITE E CORTESIA
-----------------
Il portale non pubblica un limite numerico: si tengono 3 secondi fra le
richieste, si scarica al massimo un file per esecuzione e ci si ferma
dichiarandolo su 403/429, senza ritentare.

COME EVITA DI INVENTARE
-----------------------
1. NON costruisce l'indirizzo del file. Parte dalla pagina del dataset e
   prende i collegamenti che la pagina stessa pubblica. Se non ne trova,
   lo dice e si ferma: un indirizzo indovinato è un dato inventato.
2. NON presume i nomi delle colonne. Le cerca per contenuto (una colonna che
   parla di regione, una che parla di posti letto, una di anno) e, se lo
   schema non è riconoscibile, si ferma dichiarando le colonne trovate.
3. NON stima il dato per abitante se manca la popolazione: lascia il campo
   vuoto e lo dichiara.

USO
---
  python3 etl_salute_regioni.py --out ../data/territorio
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L
import fonti_regioni as FR

# Pagina del dataset pubblicata dal Ministero. È l'unico indirizzo scritto
# qui dentro: tutti gli altri vengono letti da questa pagina.
PAGINA = "https://www.dati.salute.gov.it/dataset/posti_letto_per_regione_e_per_disciplina.jsp"

# Parole con cui riconoscere le colonne, senza presumerne il nome esatto.
COL_REGIONE = ("regione", "descrizione regione", "des_regione", "cod_regione")
COL_LETTI = ("posti letto", "postiletto", "pl_", "totale posti")
COL_ANNO = ("anno", "periodo")


def collegamenti(html, pagina):
    """Estrae dalla pagina i collegamenti a file tabellari, senza inventarli."""
    from urllib.parse import urljoin
    trovati = []
    for m in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', html, re.I):
        u = m.group(1).strip()
        if re.search(r"\.(csv|zip|xlsx?|json)(\?|$)", u, re.I):
            trovati.append(urljoin(pagina, u))
    # senza duplicati, nell'ordine in cui la pagina li presenta
    visti, out = set(), []
    for u in trovati:
        if u not in visti:
            visti.add(u)
            out.append(u)
    return out


def trova_colonna(intestazioni, parole):
    for c in intestazioni:
        n = (c or "").strip().lower()
        for p in parole:
            if p in n:
                return c
    return None


def aggrega(righe, intest):
    """Somma i posti letto per regione, sull'anno più recente disponibile."""
    col_reg = trova_colonna(intest, COL_REGIONE)
    col_pl = trova_colonna(intest, COL_LETTI)
    col_anno = trova_colonna(intest, COL_ANNO)
    if not col_reg or not col_pl:
        return None, None, ("schema non riconosciuto: manca la colonna della "
                            "regione o dei posti letto. Colonne trovate: %s"
                            % intest[:12])

    anni = set()
    if col_anno:
        for r in righe:
            a = str(r.get(col_anno) or "").strip()
            if a.isdigit():
                anni.add(a)
    anno = max(anni) if anni else None

    per_reg = {}
    for r in righe:
        if anno and col_anno and str(r.get(col_anno) or "").strip() != anno:
            continue
        cod = L.codice_regione(r.get(col_reg))
        if not cod:
            continue
        v = L.numero(r.get(col_pl))
        if v is None:
            continue
        per_reg[cod] = per_reg.get(cod, 0.0) + v
    if not per_reg:
        return None, None, "nessuna riga regionale riconosciuta nel file"
    return per_reg, anno, None


def main():
    ap = argparse.ArgumentParser(
        description="Posti letto ospedalieri per regione (Ministero della Salute)")
    ap.add_argument("--out", default="../data/territorio")
    ap.add_argument("--cache", default="../.cache_etl")
    args = ap.parse_args()

    scheda = FR.pretendi("salute")          # cancello delle licenze
    L.LIMITI.update(FR.limiti_per_client())
    client = L.Client(args.cache)

    print("Ministero della Salute · %s (%s)" % (scheda["licenza"], scheda["attribuzione"]))
    print("Limite rispettato: %s\n" % scheda["limite_nota"])

    problemi = []
    valori, anno, file_usato = None, None, None
    try:
        pagina, _ = client.scarica(PAGINA, "salute", forza=True)
        if not pagina:
            problemi.append("la pagina del dataset non ha restituito contenuto")
        else:
            html = pagina.decode("utf-8", "replace")
            link = collegamenti(html, PAGINA)
            print("Collegamenti a file trovati nella pagina: %d" % len(link))
            if not link:
                problemi.append(
                    "nessun collegamento a file tabellari nella pagina del "
                    "dataset: l'indirizzo del file non viene indovinato. "
                    "Controllare %s" % PAGINA)
            for u in link[:3]:               # al massimo tre tentativi, in ordine
                print("  provo %s" % u.split("/")[-1])
                try:
                    dati, _ = client.scarica(u, "salute", forza=True)
                except L.FonteBloccata as e:
                    problemi.append(str(e))
                    break
                if not dati:
                    continue
                pezzi = {u: dati}
                if u.lower().endswith(".zip"):
                    pezzi = L.estrai_zip(dati, ".csv")
                for nome, contenuto in pezzi.items():
                    try:
                        righe, intest = L.leggi_csv(contenuto)
                    except Exception as e:
                        problemi.append("%s: %s" % (nome.split("/")[-1], e))
                        continue
                    v, a, prob = aggrega(righe, intest)
                    if prob:
                        problemi.append("%s: %s" % (nome.split("/")[-1], prob))
                        continue
                    valori, anno, file_usato = v, a, u
                    break
                if valori:
                    break
    except L.FonteBloccata as e:
        problemi.append(str(e))
        print("FERMO: %s" % e)

    # dato per abitante: solo se la popolazione c'è davvero
    pop = (L.leggi_json(os.path.join(args.out, "popolazione.json")) or {})
    pop_reg = {k: v for k, v in (pop.get("regioni") or {}).items() if v}
    per_mille = {}
    if valori and pop_reg:
        for r, v in valori.items():
            p = pop_reg.get(r)
            if p:
                per_mille[r] = round(v / p * 1000, 3)
    if valori and not per_mille:
        problemi.append("popolazione regionale assente: il dato ogni 1.000 "
                        "abitanti non è calcolato (non viene stimato)")

    dest = os.path.join(args.out, "salute_regioni.json")
    L.scrivi_json(dest, {
        "_generato": L.ora(),
        "_fonte": "Ministero della Salute — dati aperti",
        "_licenza": scheda["licenza"],
        "_uso_commerciale": True,
        "_attribuzione": scheda["attribuzione"],
        "_pagina": PAGINA,
        "_file_usato": file_usato,
        "indicatori": ({
            "posti_letto": {
                "titolo": "Posti letto ospedalieri",
                "spiegazione": "Posti letto negli istituti di cura della regione.",
                "impatto_cittadino": "Quanti posti letto ci sono, se hai bisogno "
                                     "di un ricovero.",
                "unita": "posti letto",
                "verso": "alto",
                "gia_normalizzato": False,
                "anno": anno,
                "valori": valori,
                "per_mille": True,
                "per_mille_valori": per_mille,
                "n_regioni": len(valori),
            }} if valori else {}),
        "problemi": problemi,
    })

    print("\n=== esito ===")
    if valori:
        print("  posti letto: %d regioni · anno %s" % (len(valori), anno))
        if per_mille:
            print("  calcolato anche ogni 1.000 abitanti per %d regioni" % len(per_mille))
    else:
        print("  nessun dato raccolto (dichiarato nel file, non stimato)")
    for p in problemi:
        print("  · %s" % p)
    print("  scritto: %s" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
