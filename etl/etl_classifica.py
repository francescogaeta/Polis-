#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_classifica.py — costruisce la classifica fra regioni a partire dai dati
già scaricati dagli altri ETL. Non contatta nessuna fonte esterna.

Principio guida: una classifica fra regioni è onesta solo se dichiara come
è costruita. Qui ogni indicatore porta con sé la fonte, l'anno, l'unità e i
limiti del confronto, e l'app li mostra.

Regola sul PRO CAPITE: i valori assoluti misurano soprattutto la dimensione
della regione. La Lombardia spende più del Molise perché ha dieci milioni di
abitanti. Perciò la classifica di riferimento è quella PRO CAPITE, e il
valore assoluto resta visibile accanto, non al posto suo. Se manca la
popolazione, l'indicatore pro capite NON viene calcolato: si dichiara
l'assenza invece di inventare un denominatore.

Uso:
  python3 etl_classifica.py --dati ../data/territorio
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L


def costruisci(dati_dir):
    pop = (L.leggi_json(os.path.join(dati_dir, "popolazione.json")) or {})
    pop_reg = {k: v for k, v in (pop.get("regioni") or {}).items() if v}
    opere = (L.leggi_json(os.path.join(dati_dir, "bdap_opere.json")) or {})
    cpt = (L.leggi_json(os.path.join(dati_dir, "cpt_spesa.json")) or {})

    op_reg = opere.get("regioni") or {}
    cpt_ultimo = (cpt.get("ultimo") or {})

    indicatori = []

    # --- 1. opere pubbliche ---
    if op_reg:
        indicatori.append(_indicatore(
            chiave="opere_importo",
            titolo="Opere pubbliche · importo",
            spiegazione=("Valore complessivo delle opere pubbliche monitorate "
                         "sul territorio della regione."),
            valori={r: v.get("importo") for r, v in op_reg.items()},
            pop=pop_reg, unita="euro", per_mille=False,
            fonte=opere.get("_fonte", "BDAP-MOP"),
            anno=None,
            limiti=[opere.get("_nota_metodo", "")],
            impatto=("Sono i cantieri finanziati con denaro pubblico nel posto "
                     "dove vivi: scuole, strade, ospedali, reti."),
        ))
        indicatori.append(_indicatore(
            chiave="opere_numero",
            titolo="Opere pubbliche · quante sono",
            spiegazione="Numero di opere pubbliche monitorate nella regione.",
            valori={r: v.get("n_opere") for r, v in op_reg.items()},
            pop=pop_reg, unita="opere", per_mille=True,
            fonte=opere.get("_fonte", "BDAP-MOP"), anno=None,
            limiti=["Un'opera grande e una piccola contano uno: guardare anche "
                    "l'importo."],
            impatto="Quanti interventi pubblici sono attivi vicino a te.",
        ))
        # quota conclusa: indicatore di capacità di portare a termine
        concl = {}
        for r, v in op_reg.items():
            tot = (v.get("n_concluse") or 0) + (v.get("n_in_corso") or 0)
            if tot:
                concl[r] = round((v.get("n_concluse") or 0) / tot * 100, 1)
        if concl:
            indicatori.append(_indicatore(
                chiave="opere_concluse_pct",
                titolo="Opere concluse",
                spiegazione="Quota di opere risultate concluse sul totale di "
                            "quelle monitorate.",
                valori=concl, pop=None, unita="%", per_mille=False,
                fonte=opere.get("_fonte", "BDAP-MOP"), anno=None,
                limiti=["Dipende da quando sono partite le opere: una regione "
                        "con molti cantieri nuovi risulta più bassa."],
                impatto="Quanto spesso i cantieri finiscono davvero.",
            ))

    # --- 2. spesa pubblica consolidata ---
    for chiave, titolo, spiega, impatto in [
        ("spesa_totale", "Spesa pubblica totale",
         "Spesa consolidata della Pubblica Amministrazione sul territorio.",
         "È il denaro pubblico che passa dal tuo territorio in un anno."),
        ("spesa_capitale", "Spesa per investimenti",
         "Spesa in conto capitale: la parte che crea opere e beni durevoli.",
         "È la quota che costruisce cose nuove, non quella che paga la gestione."),
    ]:
        blocco = cpt_ultimo.get(chiave)
        if not blocco or not blocco.get("regioni"):
            continue
        indicatori.append(_indicatore(
            chiave=chiave,
            titolo=titolo, spiegazione=spiega,
            valori=blocco["regioni"], pop=pop_reg, unita="euro", per_mille=False,
            fonte=cpt.get("_fonte", "CPT"), anno=blocco.get("anno"),
            limiti=[cpt.get("_nota_metodo", ""),
                    "Universo PA consolidato: non confrontabile con dati SPA.",
                    "La sanità pesa moltissimo sui bilanci regionali e domina "
                    "il confronto se non viene isolata."],
            impatto=impatto,
        ))

    return {
        "_generato": L.ora(),
        "_popolazione_anno": pop.get("_anno"),
        "_popolazione_completa": bool(pop.get("_completo")),
        "_avvertenza": (
            "Le regioni si confrontano correttamente solo a parità di abitanti: "
            "la classifica principale è quella pro capite. I valori assoluti "
            "misurano soprattutto quanto è grande una regione."),
        "regioni": L.REGIONI,
        "indicatori": indicatori,
    }


def _indicatore(chiave, titolo, spiegazione, valori, pop, unita, per_mille,
                fonte, anno, limiti, impatto):
    """Costruisce un indicatore con classifica assoluta e pro capite."""
    valori = {r: v for r, v in (valori or {}).items()
              if r in L.REGIONI and v is not None}
    procapite = {}
    mancanti = []
    if pop:
        for r, v in valori.items():
            p = pop.get(r)
            if not p:
                mancanti.append(r)
                continue
            procapite[r] = round(v / p * (1000 if per_mille else 1), 4)

    limiti = [x for x in (limiti or []) if x]
    if pop and mancanti:
        limiti.append("Popolazione non disponibile per %d regioni: per queste "
                      "il dato pro capite non è calcolato." % len(mancanti))
    if not pop:
        limiti.append("Indicatore già in percentuale: non si normalizza per "
                      "abitante.")

    return {
        "chiave": chiave, "titolo": titolo, "spiegazione": spiegazione,
        "impatto_cittadino": impatto,
        "unita": unita,
        "unita_procapite": ("%s per 1.000 abitanti" % unita) if per_mille
                           else ("%s per abitante" % unita),
        "fonte": fonte, "anno": anno,
        "limiti": limiti,
        "n_regioni": len(valori),
        "completo": len(valori) == 20,
        "assoluto": _ordina(valori),
        "procapite": _ordina(procapite) if procapite else [],
    }


def _ordina(d):
    return [{"reg": r, "nome": L.REGIONI.get(r, r), "valore": v}
            for r, v in sorted(d.items(), key=lambda kv: -kv[1])]


def main():
    ap = argparse.ArgumentParser(description="Costruisce la classifica regionale")
    ap.add_argument("--dati", default="../data/territorio")
    args = ap.parse_args()

    out = costruisci(args.dati)
    dest = os.path.join(args.dati, "classifica.json")
    L.scrivi_json(dest, out)

    print("Classifica costruita: %d indicatori" % len(out["indicatori"]))
    for i in out["indicatori"]:
        print("  · %-32s %2d/20 regioni %s"
              % (i["titolo"], i["n_regioni"], "" if i["completo"] else "(parziale)"))
        if i["procapite"]:
            p = i["procapite"][0]
            print("      prima pro capite: %s" % p["nome"])
    if not out["indicatori"]:
        print("  (nessun dato ancora scaricato: esegui prima gli altri ETL)")
    print("\nScritto %s" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
