#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
valida_spiegazioni.py — controlla che ogni argomento complesso di Polis
abbia la sua scheda completa.

La regola è: nessun argomento complesso senza spiegazione, problema,
previsioni ufficiali e provvedimenti adottati. Una regola scritta solo
nelle intenzioni si perde alla terza sessione di lavoro; qui è un
controllo che fallisce e blocca la pubblicazione.

Cosa verifica:
  · che ogni argomento registrato abbia una scheda;
  · che ogni scheda abbia tutti e quattro i blocchi più le fonti;
  · che ogni previsione porti il nome dell'istituzione, il documento e
    la data: una previsione senza autore sarebbe una previsione nostra;
  · che ogni fonte abbia ente e data;
  · che i testi destinati al modulo dibattito stiano nei limiti dei
    campi (300 caratteri), altrimenti verrebbero troncati a metà frase;
  · elenca gli argomenti ancora senza scheda, senza fallire: è la lista
    di lavoro, non un errore.

Uso:
  python3 valida_spiegazioni.py --file ../data/paese/spiegazioni.json
"""

import argparse
import json
import sys

# Argomenti che DEVONO avere la scheda: sono le schermate dei conti
# dello Stato e della Ragioneria già presenti nell'app.
OBBLIGATORI = [
    "quadro", "saldo", "entrate", "uscite", "interessi", "pil",
    "redditi", "sanita", "nero", "opere", "personale",
]

# Argomenti complessi delle altre sezioni, da coprire man mano.
# Restano una lista di lavoro finché non hanno contenuto verificato.
DA_COPRIRE = [
    ("impatto_bes", "Politica · Impatto sul Paese (domini BES)"),
    ("leggi", "Politica · Leggi e decreti"),
    ("regioni", "Confronto fra regioni"),
    ("comuni", "Confronto fra comuni"),
    ("classe", "La mia classe · misure applicabili"),
]

BLOCCHI = ["semplice", "problema", "futuro", "manovre", "fonti"]
LIMITE_CAMPO = 300      # quanto accetta il modulo di creazione dibattito


def valida(dati):
    errori, avvisi = [], []
    schede = dati.get("schede") or {}

    for chiave in OBBLIGATORI:
        s = schede.get(chiave)
        if not s:
            errori.append("«%s»: scheda assente. L'argomento è visibile "
                          "nell'app senza spiegazione." % chiave)
            continue

        for b in BLOCCHI:
            if not s.get(b):
                errori.append("«%s»: manca il blocco «%s»." % (chiave, b))

        for i, p in enumerate(s.get("futuro") or [], 1):
            if not p.get("chi"):
                errori.append("«%s» previsione %d: manca l'istituzione. "
                              "Senza autore diventerebbe una previsione di "
                              "Polis." % (chiave, i))
            if not p.get("documento") or not p.get("data"):
                errori.append("«%s» previsione %d (%s): manca il documento o "
                              "la data." % (chiave, i, p.get("chi", "?")))
            if not p.get("dice"):
                errori.append("«%s» previsione %d: manca il contenuto."
                              % (chiave, i))

        for i, m in enumerate(s.get("manovre") or [], 1):
            if not m.get("atto"):
                errori.append("«%s» manovra %d: manca il riferimento "
                              "normativo." % (chiave, i))
            if not m.get("cosa"):
                errori.append("«%s» manovra %d: manca la descrizione."
                              % (chiave, i))

        for i, f in enumerate(s.get("fonti") or [], 1):
            if not f.get("ente") or not f.get("data"):
                errori.append("«%s» fonte %d: manca l'ente o la data."
                              % (chiave, i))

        for campo in ("semplice", "problema"):
            testo = s.get(campo) or ""
            if len(testo) > LIMITE_CAMPO:
                avvisi.append("«%s»: il testo «%s» è lungo %d caratteri; nel "
                              "modulo dibattito viene accorciato all'ultima "
                              "frase intera entro %d."
                              % (chiave, campo, len(testo), LIMITE_CAMPO))

        verifiche = []
        for p in (s.get("futuro") or []):
            if p.get("verifica"):
                verifiche.append(p.get("chi", "?"))
        for m in (s.get("manovre") or []):
            if m.get("verifica"):
                verifiche.append(m.get("atto", "?"))
        if verifiche:
            avvisi.append("«%s»: %d riferimenti attendono il riscontro sul "
                          "documento depositato (%s). Sono mostrati con "
                          "l'avvertenza." % (chiave, len(verifiche),
                                             ", ".join(verifiche[:2])))

    scoperti = [(k, d) for k, d in DA_COPRIRE if k not in schede]
    return errori, avvisi, scoperti


def main():
    ap = argparse.ArgumentParser(
        description="Verifica le schede di spiegazione degli argomenti complessi")
    ap.add_argument("--file", default="../data/paese/spiegazioni.json")
    args = ap.parse_args()

    try:
        with open(args.file, encoding="utf-8") as f:
            dati = json.load(f)
    except FileNotFoundError:
        print("File non trovato: %s" % args.file)
        return 1
    except json.JSONDecodeError as e:
        print("JSON non valido: %s" % e)
        return 1

    errori, avvisi, scoperti = valida(dati)
    schede = dati.get("schede") or {}

    print("Schede presenti: %d · argomenti obbligatori: %d"
          % (len(schede), len(OBBLIGATORI)))
    print("Contenuto aggiornato al: %s\n" % dati.get("_aggiornato", "non dichiarato"))

    if avvisi:
        print("AVVISI (non bloccano)")
        for a in avvisi:
            print("  · %s" % a)
        print()

    if scoperti:
        print("ARGOMENTI ANCORA SENZA SCHEDA (lista di lavoro)")
        for k, d in scoperti:
            print("  · %-14s %s" % (k, d))
        print("  Finché non hanno contenuto verificato, l'app non mostra "
              "nulla per questi argomenti: non si inventa una spiegazione.\n")

    if errori:
        print("ERRORI (%d) — vanno risolti prima di pubblicare" % len(errori))
        for e in errori:
            print("  ✗ %s" % e)
        return 1

    print("Tutte le schede obbligatorie sono complete.")
    if dati.get("_prossimi_aggiornamenti"):
        print("\nProssimi documenti che renderanno vecchi questi dati:")
        for p in dati["_prossimi_aggiornamenti"]:
            if isinstance(p, dict):
                print("  · %-46s %s" % (p.get("cosa", "?"), p.get("quando", "?")))
            else:
                print("  · %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
