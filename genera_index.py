#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genera_index.py — ricostruisce `index.json` dai file dei comuni già scaricati.

PERCHÉ SERVE
------------
Sul web un'app non può elencare il contenuto di una cartella: può solo
chiedere un file di cui conosce già il nome. I file dei comuni si chiamano
col codice ISTAT (016024.json), ma l'utente cerca "Bergamo". Senza un indice
che leghi il nome al codice, l'app non sa quale file chiedere — e ogni
comune risulta assente anche se il file c'è.

Questo script apre i file presenti, ne legge nome e regione, e scrive
l'indice. Non tocca la rete: lavora solo su ciò che è già sul disco,
quindi si può lanciare quante volte si vuole senza limiti di alcun tipo.

Uso:
  python3 genera_index.py --dir ../data/comuni
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone


def main():
    ap = argparse.ArgumentParser(
        description="Ricostruisce index.json dai file dei comuni presenti")
    ap.add_argument("--dir", default="../data/comuni",
                    help="cartella con i file <istat>.json")
    args = ap.parse_args()

    if not os.path.isdir(args.dir):
        print("Cartella inesistente: %s" % os.path.abspath(args.dir))
        print("Indica la cartella dove stanno i file dei comuni, per esempio:")
        print("  python3 genera_index.py --dir ../data/comuni")
        return 1

    voci, saltati = [], 0
    for nome_file in sorted(os.listdir(args.dir)):
        if not nome_file.endswith(".json"):
            continue
        if nome_file.startswith("_") or nome_file in ("index.json",):
            continue
        percorso = os.path.join(args.dir, nome_file)
        try:
            with open(percorso, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception as e:
            print("  salto %s (%s)" % (nome_file, str(e)[:50]))
            saltati += 1
            continue
        if not isinstance(rec, dict):
            saltati += 1
            continue

        istat = rec.get("istat") or nome_file[:-5]
        nome = rec.get("nome")
        kpi = rec.get("kpi") or {}
        ana = kpi.get("anagrafica") or {}
        if not nome:
            nome = ana.get("nome")
        regione = rec.get("regione") or ana.get("regione")
        prov = rec.get("provincia") or ana.get("provincia")
        pop = (kpi.get("demografia") or {}).get("popolazione")

        if not nome:
            print("  salto %s: non contiene il nome del comune" % nome_file)
            saltati += 1
            continue

        voci.append({"i": str(istat), "n": nome, "r": regione,
                     "p": prov, "pop": pop})

    if not voci:
        print("\nNessun file di comune valido trovato in %s"
              % os.path.abspath(args.dir))
        print("Controlla di aver indicato la cartella giusta: deve contenere "
              "file come 016024.json")
        return 1

    voci.sort(key=lambda x: (x.get("n") or ""))
    destinazione = os.path.join(args.dir, "index.json")
    with open(destinazione, "w", encoding="utf-8") as f:
        json.dump(voci, f, ensure_ascii=False, separators=(",", ":"))

    # piccolo riepilogo utile a capire cosa c'è davvero in archivio
    per_regione = {}
    for v in voci:
        r = v.get("r") or "(regione non indicata)"
        per_regione[r] = per_regione.get(r, 0) + 1

    print("\n=== indice ricostruito ===")
    print("  comuni indicizzati: %d" % len(voci))
    if saltati:
        print("  file saltati:       %d" % saltati)
    print("  scritto: %s" % os.path.abspath(destinazione))
    print("\n  Per regione:")
    for r, n in sorted(per_regione.items(), key=lambda x: -x[1]):
        print("    %-26s %d" % (r, n))
    print("\n  Primi comuni: %s"
          % ", ".join(v["n"] for v in voci[:6]))
    print("\nOra carica su GitHub il file index.json insieme agli altri.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
