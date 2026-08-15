#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
importa_dati_camera_senato.py — trasforma i file scaricati a mano dai portali
di Camera e Senato nei JSON compatti che l'app legge.

PERCHÉ ESISTE
-------------
I portali istituzionali respingono spesso i client automatici, ma dal
browser il download funziona. Questi file però sono enormi (oltre 130 MB in
totale, con un singolo file di votazioni da 68 MB) e in formato N-Triples o
XML: inutilizzabili così come sono dentro un'app che deve restare leggera.

Questo script li legge in streaming — una riga per volta, senza caricarli
tutti in memoria — ne estrae solo ciò che serve e produce pochi JSON piccoli.

COSA PRODUCE
------------
  dati/assemblee.json   votazioni d'Aula con esito, numeri e tema
  dati/politica/senatori.json  composizione del Senato

COSA NON FA
-----------
Non interpreta il merito politico. Il tema serve a dire DI COSA si parla,
non se una decisione sia giusta. Il titolo ufficiale resta sempre integro.

Uso:
  python3 importa_dati_camera_senato.py --da CARTELLA_FILE --out ../dati
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import lib_fonti as L
except Exception:
    L = None

# riuso la classificazione per temi dell'ETL assemblee
try:
    from etl_assemblee import TEMI, RILEVANZA, classifica
except Exception:
    TEMI, RILEVANZA = {}, {}
    def classifica(t): return []


# ------------------------------------------------------------ N-Triples

RE_TRIPLA = re.compile(r'^<([^>]+)>\s+<([^>]+)>\s+(.+?)\s*\.\s*$')


def valore(grezzo):
    """Ripulisce l'oggetto di una tripla: stringa, numero o riferimento."""
    g = grezzo.strip()
    if g.startswith("<") and g.endswith(">"):
        return g[1:-1]
    m = re.match(r'^"(.*)"(?:\^\^<[^>]+>|@[\w-]+)?$', g, re.S)
    if m:
        v = m.group(1)
        # gli accenti arrivano come \u00EC
        try:
            v = v.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
        except Exception:
            v = re.sub(r'\\u([0-9A-Fa-f]{4})',
                       lambda x: chr(int(x.group(1), 16)), v)
        return v.replace('\\"', '"').replace("\\\\", "\\")
    return g


def leggi_ntriples(path, proprieta, filtro_soggetto=None):
    """Legge un file N-Triples in streaming e raccoglie, per ogni soggetto,
    solo le proprietà richieste. Non carica il file in memoria."""
    dati = {}
    letti = tenuti = 0
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for riga in f:
            letti += 1
            m = RE_TRIPLA.match(riga)
            if not m:
                continue
            sogg, pred, ogg = m.group(1), m.group(2), m.group(3)
            if filtro_soggetto and filtro_soggetto not in sogg:
                continue
            corta = pred.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
            if corta not in proprieta:
                continue
            dati.setdefault(sogg, {})[corta] = valore(ogg)
            tenuti += 1
    print("    righe lette: %s · valori tenuti: %s · soggetti: %s"
          % (f"{letti:,}".replace(",", "."), f"{tenuti:,}".replace(",", "."),
             f"{len(dati):,}".replace(",", ".")))
    return dati


# ------------------------------------------------------------ votazioni

PROP_VOT = {"label", "title", "description", "date", "identifier",
            "votanti", "favorevoli", "contrari", "astenuti", "presenti",
            "approvato", "votazioneSegreta", "votazioneFinale",
            "richiestaFiducia", "maggioranza", "rif_seduta",
            "rif_attoCamera", "rif_aic"}

# Il titolo della votazione è generico ("Votazione Articolo 4"): dice come si
# è votato, non su che cosa. L'argomento sta nell'ATTO collegato. Senza questa
# unione la classificazione per temi è impossibile e l'utente legge righe che
# non significano nulla.
PROP_ATTO = {"title", "label"}


def ripulisci_titolo(t):
    """I titoli degli atti contengono entità HTML e virgolette sfuggite."""
    t = str(t or "")
    for a, b in [("&rsquo;", "\u2019"), ("&lsquo;", "\u2018"),
                 ("&agrave;", "\u00e0"), ("&egrave;", "\u00e8"),
                 ("&eacute;", "\u00e9"), ("&igrave;", "\u00ec"),
                 ("&ograve;", "\u00f2"), ("&ugrave;", "\u00f9"),
                 ("&quot;", '"'), ("&amp;", "&"), ("&nbsp;", " ")]:
        t = t.replace(a, b)
    t = re.sub(r"&lt;/?em&gt;|<\/?em>", "", t)
    t = t.replace('\\"', '"')
    return re.sub(r"\s+", " ", t).strip(' "')


def _int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


def _data(v):
    """Le date arrivano come 20231020 oppure 2023-10-20."""
    s = str(v or "").strip()
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", s)
    return "%s-%s-%s" % m.groups() if m else ""


def costruisci_votazioni(dati, atti=None):
    atti = atti or {}
    out = []
    senza_atto = 0
    for uri, d in dati.items():
        # 1) titolo dell'atto votato: è quello che dice DI COSA si tratta
        rif = d.get("rif_attoCamera") or d.get("rif_aic")
        oggetto = ""
        if rif and rif in atti:
            oggetto = ripulisci_titolo(atti[rif].get("title")
                                       or atti[rif].get("label") or "")
        # 2) come si è votato (finale, articolo 4, ecc.)
        fase = ripulisci_titolo(d.get("title") or d.get("label")
                                or d.get("description") or "")
        titolo = oggetto or fase
        if not oggetto:
            senza_atto += 1
        if len(titolo) < 8:
            continue
        appr = _int(d.get("approvato"))
        fav, con = _int(d.get("favorevoli")), _int(d.get("contrari"))
        v = {
            "id": uri.rsplit("/", 1)[-1],
            "titolo": titolo[:300],
            "fase": fase[:120] if oggetto else "",
            "data": _data(d.get("date")),
            "ramo": "Camera",
            "esito": ("Approvato" if appr == 1 else "Respinto") if appr is not None else None,
            "fav": fav, "con": con, "ast": _int(d.get("astenuti")),
            "votanti": _int(d.get("votanti")),
            "fiducia": _int(d.get("richiestaFiducia")) == 1,
            "finale": _int(d.get("votazioneFinale")) == 1,
            "segreta": _int(d.get("votazioneSegreta")) == 1,
            "temi": classifica(titolo),
        }
        out.append(v)
    out.sort(key=lambda x: (x.get("data") or "", x.get("id") or ""), reverse=True)
    if senza_atto:
        print("    votazioni senza atto collegato: %d (resta il titolo di fase)"
              % senza_atto)
    return out


# ------------------------------------------------------------ senatori

def leggi_sparql_xml(path):
    """Legge un export del Senato in formato risultati SPARQL e ne ricava
    un elenco di dizionari campo→valore."""
    import xml.etree.ElementTree as ET
    try:
        radice = ET.parse(path).getroot()
    except Exception as e:
        print("    XML non interpretabile (%s)" % str(e)[:60])
        return []
    righe = []
    for nodo in radice.iter():
        if nodo.tag.rsplit("}", 1)[-1].lower() != "result":
            continue
        campi = {}
        for b in nodo:
            if b.tag.rsplit("}", 1)[-1].lower() != "binding":
                continue
            nome = (b.get("name") or "").lower()
            testo = ""
            for v in b:
                testo = (v.text or "").strip()
                if testo:
                    break
            if nome and testo:
                campi[nome] = testo
        if campi:
            righe.append(campi)
    return righe


def votazioni_senato(path):
    """Dalle votazioni per senatore ricava le votazioni d'Aula del Senato,
    con l'oggetto e il conteggio dei voti. Ogni riga del file è il voto di
    UN senatore: qui vengono raggruppate per votazione."""
    print("[Senato] %s" % os.path.basename(path))
    righe = leggi_sparql_xml(path)
    print("    voti individuali letti: %s" % f"{len(righe):,}".replace(",", "."))
    per_vot = {}
    for r in righe:
        chiave = r.get("votazione") or (
            (r.get("numeroseduta") or "") + "_" + (r.get("numerovotazione") or ""))
        if not chiave:
            continue
        v = per_vot.setdefault(chiave, {
            "oggetto": "", "data": "", "fav": 0, "con": 0, "ast": 0, "altro": 0})
        if not v["oggetto"]:
            v["oggetto"] = ripulisci_titolo(r.get("oggetto") or "")
        if not v["data"]:
            v["data"] = _data(r.get("dataseduta"))
        voto = (r.get("voto") or "").lower()
        if "favor" in voto:
            v["fav"] += 1
        elif "contr" in voto:
            v["con"] += 1
        elif "asten" in voto:
            v["ast"] += 1
        else:
            v["altro"] += 1
    out = []
    for chiave, v in per_vot.items():
        if len(v["oggetto"]) < 8:
            continue
        out.append({
            "id": str(chiave).rsplit("/", 1)[-1],
            "titolo": v["oggetto"][:300], "fase": "",
            "data": v["data"], "ramo": "Senato",
            "esito": ("Approvato" if v["fav"] > v["con"] else "Respinto")
                     if (v["fav"] or v["con"]) else None,
            "fav": v["fav"] or None, "con": v["con"] or None,
            "ast": v["ast"] or None,
            "votanti": (v["fav"] + v["con"] + v["ast"]) or None,
            "fiducia": False, "finale": False, "segreta": False,
            "temi": classifica(v["oggetto"]),
        })
    print("    votazioni d'Aula ricostruite: %d" % len(out))
    return out


def leggi_senatori(path):
    """Legge l'XML dei senatori. Il tracciato del Senato usa elementi
    diversi a seconda del file: cerchiamo i campi, non li presumiamo."""
    print("  senatori: %s" % os.path.basename(path))
    try:
        import xml.etree.ElementTree as ET
    except Exception:
        return []
    try:
        albero = ET.parse(path)
    except Exception as e:
        print("    XML non interpretabile (%s)" % str(e)[:60])
        return []
    radice = albero.getroot()
    persone = {}
    # Il Senato esporta in formato "risultati SPARQL": ogni riga è un
    # <result> con dentro <binding name="..."><literal>valore</literal>.
    # Non sono elementi diretti, per questo un parser ingenuo trova zero.
    for nodo in radice.iter():
        if nodo.tag.rsplit("}", 1)[-1].lower() != "result":
            continue
        campi = {}
        for b in nodo:
            if b.tag.rsplit("}", 1)[-1].lower() != "binding":
                continue
            nome_campo = (b.get("name") or "").lower()
            testo = ""
            for v in b:
                testo = (v.text or "").strip()
                if testo:
                    break
            if nome_campo and testo:
                campi[nome_campo] = testo
        if not campi:
            continue
        nome = (campi.get("nome") or "").strip()
        cognome = (campi.get("cognome") or "").strip()
        completo = campi.get("senatore") or campi.get("nomecompleto") or ""
        if not (nome or cognome or completo):
            continue
        etichetta = (("%s %s" % (nome, cognome)).strip() or completo).strip()
        if len(etichetta) < 3:
            continue
        gruppo = (campi.get("gruppo") or campi.get("nomegruppo")
                  or campi.get("descrizionegruppo") or "")
        chiave = etichetta.lower()
        if chiave in persone and not gruppo:
            continue
        persone[chiave] = {
            "nome": etichetta[:80],
            "gruppo": gruppo[:120],
            "regione": (campi.get("regione") or "")[:40],
            "tipo": (campi.get("tipomandato") or campi.get("carica") or "")[:60],
        }
    print("    senatori trovati: %d" % len(persone))
    return sorted(persone.values(), key=lambda x: x["nome"])


# ------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(
        description="Importa i file di Camera e Senato scaricati a mano")
    ap.add_argument("--da", required=True, help="cartella con i file scaricati")
    ap.add_argument("--out", default="../dati", help="cartella di destinazione")
    ap.add_argument("--max", type=int, default=1500,
                    help="quante votazioni tenere (le più recenti)")
    args = ap.parse_args()

    if not os.path.isdir(args.da):
        print("Cartella inesistente: %s" % args.da)
        return 1
    os.makedirs(args.out, exist_ok=True)
    file_presenti = os.listdir(args.da)

    # ---------- votazioni ----------
    vot = []
    for nome in file_presenti:
        if not re.search(r"votazion.*\.rdf$", nome, re.I):
            continue
        # prima gli atti: servono i loro titoli per sapere di cosa si votava
        atti = {}
        for na in file_presenti:
            if re.search(r"^atto.*\.rdf$", na, re.I):
                print("[Atti] %s" % na)
                atti = leggi_ntriples(os.path.join(args.da, na), PROP_ATTO)
                break
        print("[Votazioni] %s" % nome)
        dati = leggi_ntriples(os.path.join(args.da, nome), PROP_VOT)
        vot = costruisci_votazioni(dati, atti)
        print("    votazioni utilizzabili: %d" % len(vot))
        break

    # Senato: votazioni con l'oggetto, dalle votazioni per senatore
    for nome in file_presenti:
        if re.search(r"votazioni_senatore.*\.xml$", nome, re.I):
            vot.extend(votazioni_senato(os.path.join(args.da, nome)))
            break

    if vot:
        # Priorità a ciò che ha un oggetto vero: una riga "Votazione
        # Articolo 4" non dice nulla al cittadino, e riempirebbe l'elenco
        # scacciando le votazioni che invece si capiscono.
        # Uno stesso atto viene votato articolo per articolo: nell'elenco
        # comparirebbe decine di volte identico. Tengo una riga per atto e
        # per giorno, preferendo la votazione finale (quella che conta).
        migliori = {}
        for v in vot:
            k = (v["titolo"][:110].lower(), v.get("data") or "", v["ramo"])
            p = (2 if v.get("finale") else 0) + (1 if v.get("votanti") else 0)
            if k not in migliori or p > migliori[k][0]:
                migliori[k] = (p, v)
        prima = len(vot)
        vot = [x[1] for x in migliori.values()]
        print("  righe unite: da %d a %d (stesso atto, stesso giorno)"
              % (prima, len(vot)))

        con_oggetto = [v for v in vot if v.get("fase") or v["ramo"] == "Senato"]
        generiche = [v for v in vot if v not in con_oggetto]
        con_oggetto.sort(key=lambda x: x.get("data") or "", reverse=True)
        generiche.sort(key=lambda x: x.get("data") or "", reverse=True)
        tenute = (con_oggetto + generiche)[:args.max]
        tenute.sort(key=lambda x: x.get("data") or "", reverse=True)
        print("  con oggetto identificato: %d · procedurali: %d"
              % (len(con_oggetto), len(generiche)))
        conteggio = {}
        for v in tenute:
            for t in v["temi"]:
                conteggio[t] = conteggio.get(t, 0) + 1
        senza = sum(1 for v in tenute if not v["temi"])
        scrivi(os.path.join(args.out, "assemblee.json"), {
            "_generato": adesso(),
            "aggiornato": datetime.now(timezone.utc).strftime("%d/%m/%Y"),
            "fonte": "Camera dei deputati · dati.camera.it (CC BY)",
            "_metodo": ("Votazioni d'Aula della legislatura in corso. Il tema è "
                        "dedotto dalle parole del titolo e dice di cosa si "
                        "parla, non cosa è stato deciso."),
            "rilevanza": RILEVANZA,
            "temi": sorted(TEMI.keys()),
            "conteggio_temi": conteggio,
            "n": len(tenute),
            "n_totale_disponibili": len(vot),
            "n_senza_tema": senza,
            "sedute": tenute,
        })
        print("  → assemblee.json: %d votazioni (su %d disponibili)"
              % (len(tenute), len(vot)))
        print("    con tema riconosciuto: %d" % (len(tenute) - senza))
        for t, n in sorted(conteggio.items(), key=lambda x: -x[1])[:8]:
            print("      %-11s %d" % (t, n))

    # ---------- senatori ----------
    sen = []
    for nome in file_presenti:
        if re.search(r"senatori.*\.xml$", nome, re.I):
            trovati = leggi_senatori(os.path.join(args.da, nome))
            if len(trovati) > len(sen):
                sen = trovati
    if sen:
        gruppi = {}
        for s in sen:
            if s["gruppo"]:
                gruppi[s["gruppo"]] = gruppi.get(s["gruppo"], 0) + 1
        scrivi(os.path.join(args.out, "politica", "senatori.json"), {
            "_generato": adesso(),
            "fonte": "Senato della Repubblica · dati.senato.it (CC BY 3.0)",
            "n": len(sen),
            "gruppi": [{"nome": k, "componenti": v}
                       for k, v in sorted(gruppi.items(), key=lambda x: -x[1])],
            "senatori": sen,
        })
        print("\n  → politica/senatori.json: %d senatori, %d gruppi"
              % (len(sen), len(gruppi)))

    if not vot and not sen:
        print("\nNessun dato riconosciuto nella cartella indicata.")
        return 1
    print("\nFatto. Carica su GitHub i file creati in %s" % os.path.abspath(args.out))
    return 0


def adesso():
    return datetime.now(timezone.utc).isoformat()


def scrivi(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    sys.exit(main())
