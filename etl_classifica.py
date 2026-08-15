#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
etl_classifica.py — costruisce la classifica fra regioni a partire dai dati
già scaricati dagli altri ETL. Non contatta nessuna fonte esterna.

COSA CAMBIA RISPETTO ALLA VERSIONE PRECEDENTE
---------------------------------------------
1. Legge anche i nuovi file regionali: `istat_regioni.json` (ISTAT SDMX) e
   `salute_regioni.json` (Ministero della Salute).
2. FILTRA PER LICENZA. Ogni fonte passa da fonti_regioni.py: se la licenza
   non è verificata per l'uso commerciale, l'indicatore NON entra nella
   classifica, e il motivo viene scritto nel file e stampato a schermo.
   Riguarda oggi BDAP-MOP e CPT, che erano usati dalla versione precedente:
   restano esclusi finché la licenza non è chiarita per iscritto.
3. Ogni indicatore porta il VERSO (meglio in alto, meglio in basso, nessun
   verso migliore) e la LICENZA della fonte, così l'app può dirlo all'utente.

Principio guida invariato: una classifica fra regioni è onesta solo se
dichiara come è costruita. Ogni indicatore porta fonte, anno, unità e limiti.

Regola sul PRO CAPITE: i valori assoluti misurano soprattutto la dimensione
della regione. Perciò la vista di riferimento è quella pro capite, e se manca
la popolazione il pro capite NON viene calcolato: si dichiara l'assenza.

Uso:
  python3 etl_classifica.py --dati ../data/territorio
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L
import fonti_regioni as FR


def _ordina(d, verso):
    """Ordina la classifica secondo il verso dichiarato: primo = migliore
    dove «migliore» ha un senso, altrimenti dal valore più alto al più basso."""
    crescente = (verso == "basso")
    return [{"reg": r, "nome": L.REGIONI.get(r, r), "valore": v}
            for r, v in sorted(d.items(), key=lambda kv: kv[1],
                               reverse=not crescente)]


def _indicatore(chiave, titolo, spiegazione, valori, pop, unita, per_mille,
                fonte, anno, limiti, impatto, verso="neutro", licenza=None,
                gia_normalizzato=False, procapite_pronto=None):
    """Costruisce un indicatore con classifica assoluta e pro capite."""
    valori = {r: v for r, v in (valori or {}).items()
              if r in L.REGIONI and v is not None}
    procapite = dict(procapite_pronto or {})
    mancanti = []
    if pop and not gia_normalizzato and not procapite:
        for r, v in valori.items():
            p = pop.get(r)
            if not p:
                mancanti.append(r)
                continue
            procapite[r] = round(v / p * (1000 if per_mille else 1), 4)

    limiti = [x for x in (limiti or []) if x]
    if mancanti:
        limiti.append("Popolazione non disponibile per %d regioni: per queste "
                      "il dato per abitante non è calcolato, e non viene "
                      "stimato." % len(mancanti))
    if gia_normalizzato:
        limiti.append("Indicatore già espresso in forma confrontabile "
                      "(percentuale, media o valore pro capite): non viene "
                      "normalizzato una seconda volta.")
    if verso == "neutro":
        limiti.append("Per questo indicatore non esiste un valore «migliore»: "
                      "l'ordine va dal più alto al più basso, non dal migliore "
                      "al peggiore.")
    if len(valori) < 20:
        limiti.append("Dato disponibile per %d regioni su 20: la classifica è "
                      "parziale e lo dichiara." % len(valori))

    return {
        "chiave": chiave, "titolo": titolo, "spiegazione": spiegazione,
        "impatto_cittadino": impatto,
        "unita": unita,
        "unita_procapite": (("%s per 1.000 abitanti" % unita) if per_mille
                            else ("%s per abitante" % unita)) if not gia_normalizzato
                           else unita,
        "fonte": fonte, "licenza": licenza, "anno": anno, "verso": verso,
        "limiti": limiti,
        "n_regioni": len(valori),
        "completo": len(valori) == 20,
        "assoluto": _ordina(valori, verso),
        "procapite": _ordina(procapite, verso) if procapite else [],
    }


def _da_blocco(blocco, chiave_fonte, pop, indicatori, esclusi):
    """Trasforma il file di un ETL in indicatori, se la licenza lo consente."""
    if not blocco:
        return
    scheda = FR.scheda(chiave_fonte)
    if not FR.consentita(chiave_fonte):
        esclusi.append({
            "fonte": scheda["nome"], "licenza": scheda["licenza"],
            "motivo": scheda["nota"], "verifica_su": scheda["verificata_su"],
        })
        return
    fonte_txt = "%s · %s" % (blocco.get("_fonte", scheda["nome"]), scheda["licenza"])
    for chiave, ind in (blocco.get("indicatori") or {}).items():
        valori = ind.get("valori") or {}
        if not valori:
            continue
        indicatori.append(_indicatore(
            chiave=chiave,
            titolo=ind.get("titolo", chiave),
            spiegazione=ind.get("spiegazione", ""),
            valori=valori,
            pop=pop,
            unita=ind.get("unita", "valore"),
            per_mille=bool(ind.get("per_mille")),
            fonte=fonte_txt,
            anno=ind.get("anno"),
            limiti=[ind.get("nota")],
            impatto=ind.get("impatto_cittadino", ""),
            verso=ind.get("verso", "neutro"),
            licenza=scheda["licenza"],
            gia_normalizzato=bool(ind.get("gia_normalizzato")),
            procapite_pronto=ind.get("per_mille_valori"),
        ))


def costruisci(dati_dir):
    pop = (L.leggi_json(os.path.join(dati_dir, "popolazione.json")) or {})
    pop_reg = {k: v for k, v in (pop.get("regioni") or {}).items() if v}

    indicatori, esclusi = [], []

    # --- fonti regionali con licenza verificata ---
    _da_blocco(L.leggi_json(os.path.join(dati_dir, "istat_regioni.json")),
               "istat", pop_reg, indicatori, esclusi)
    _da_blocco(L.leggi_json(os.path.join(dati_dir, "salute_regioni.json")),
               "salute", pop_reg, indicatori, esclusi)

    # --- fonti presenti in archivio ma con licenza non verificata ---
    # Restano fuori finché fonti_regioni.py non le marca come commerciali.
    for nome_file, chiave in (("bdap_opere.json", "bdap"),
                              ("cpt_spesa.json", "cpt")):
        blocco = L.leggi_json(os.path.join(dati_dir, nome_file))
        if blocco:
            _da_blocco(blocco, chiave, pop_reg, indicatori, esclusi)

    indicatori.sort(key=lambda i: (-i["n_regioni"], i["titolo"]))

    return {
        "_generato": L.ora(),
        "_soggetto": "regioni",
        "_popolazione_anno": pop.get("anno"),
        "_avvertenza": ("Confronto fra le 20 regioni su dati ufficiali. Ogni "
                        "indicatore dichiara fonte, anno, licenza e in che "
                        "verso va letto. Dove il dato manca, la classifica lo "
                        "dice invece di stimarlo."),
        "_licenze": sorted({i["licenza"] for i in indicatori if i.get("licenza")}),
        "_fonti_escluse": esclusi,
        "indicatori": indicatori,
    }


def main():
    ap = argparse.ArgumentParser(description="Costruisce la classifica regionale")
    ap.add_argument("--dati", default="../data/territorio")
    args = ap.parse_args()

    out = costruisci(args.dati)
    dest = os.path.join(args.dati, "classifica.json")
    L.scrivi_json(dest, out)

    print("Classifica costruita: %d indicatori" % len(out["indicatori"]))
    for i in out["indicatori"]:
        verso = {"alto": "↑", "basso": "↓", "neutro": "•"}[i["verso"]]
        print("  %s %-34s %2d/20 regioni · %s"
              % (verso, i["titolo"][:34], i["n_regioni"], i["licenza"] or "—"))
    if out["_fonti_escluse"]:
        print("\nFonti ESCLUSE per licenza non verificata:")
        for e in out["_fonti_escluse"]:
            print("  · %s (%s)" % (e["fonte"], e["licenza"]))
            print("    da verificare su %s" % e["verifica_su"])
    if not out["indicatori"]:
        print("  (nessun dato ancora scaricato: esegui prima gli ETL regionali)")
    print("\nScritto %s" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
