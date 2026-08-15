#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test offline di etl_istat_regioni.py.

Non tocca la rete: sostituisce il client HTTP con uno finto che restituisce
risposte SDMX realistiche. Verifica ciò che conta davvero: che i codici non
vengano indovinati, che l'ambiguità porti a saltare l'indicatore invece che a
scrivere un numero sbagliato, e che il blocco della fonte fermi tutto.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L
import fonti_regioni as FR
import etl_istat_regioni as E

falliti = 0


def ok(c, m):
    global falliti
    print(("  OK   " if c else "  FALLITO ") + m)
    if not c:
        falliti += 1


STRUTTURA = """<?xml version="1.0" encoding="UTF-8"?>
<m:Structure xmlns:m="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
 xmlns:s="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
 xmlns:c="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">
 <m:Structures>
  <s:Dataflows><s:Dataflow id="{df}" agencyID="IT1"><c:Name xml:lang="it">Prova</c:Name></s:Dataflow></s:Dataflows>
  <s:Codelists>
   <s:Codelist id="CL_ITTER107">
     <s:Code id="IT"><c:Name xml:lang="it">Italia</c:Name></s:Code>
     <s:Code id="ITC1"><c:Name xml:lang="it">Piemonte</c:Name></s:Code>
     <s:Code id="ITF4"><c:Name xml:lang="it">Puglia</c:Name></s:Code>
   </s:Codelist>
   <s:Codelist id="CL_SEXISTAT1">
     <s:Code id="1"><c:Name xml:lang="it">maschi</c:Name></s:Code>
     <s:Code id="2"><c:Name xml:lang="it">femmine</c:Name></s:Code>
     <s:Code id="9"><c:Name xml:lang="it">totale</c:Name></s:Code>
   </s:Codelist>
   <s:Codelist id="CL_ETA1">
     <s:Code id="Y15-64"><c:Name xml:lang="it">15-64 anni</c:Name></s:Code>
     <s:Code id="Y_GE15"><c:Name xml:lang="it">15 anni e più</c:Name></s:Code>
   </s:Codelist>
  </s:Codelists>
  <s:DataStructures><s:DataStructure id="DCCV_PROVA">
   <s:DataStructureComponents><s:DimensionList>
     <s:Dimension id="FREQ" position="1"/>
     <s:Dimension id="ITTER107" position="2">
       <s:LocalRepresentation><s:Enumeration><Ref id="CL_ITTER107"/></s:Enumeration></s:LocalRepresentation>
     </s:Dimension>
     <s:Dimension id="SEXISTAT1" position="3">
       <s:LocalRepresentation><s:Enumeration><Ref id="CL_SEXISTAT1"/></s:Enumeration></s:LocalRepresentation>
     </s:Dimension>
     {extra}
     <s:TimeDimension id="TIME_PERIOD"/>
   </s:DimensionList></s:DataStructureComponents>
  </s:DataStructure></s:DataStructures>
 </m:Structures></m:Structure>"""

DIM_ETA = """<s:Dimension id="ETA1" position="4">
       <s:LocalRepresentation><s:Enumeration><Ref id="CL_ETA1"/></s:Enumeration></s:LocalRepresentation>
     </s:Dimension>"""

VINCOLI = """<?xml version="1.0" encoding="UTF-8"?>
<m:Structure xmlns:m="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
 xmlns:s="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
 xmlns:c="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">
 <m:Structures><s:Constraints><s:ContentConstraint id="X"><s:CubeRegion>
   <c:KeyValue id="SEXISTAT1"><c:Value>1</c:Value><c:Value>2</c:Value><c:Value>9</c:Value></c:KeyValue>
   {eta}
 </s:CubeRegion></s:ContentConstraint></s:Constraints></m:Structures></m:Structure>"""

CSV_OK = ("DATAFLOW,FREQ,ITTER107,SEXISTAT1,TIME_PERIOD,OBS_VALUE\n"
          "IT1:X(1.0),A,IT,9,2024,61.5\n"
          "IT1:X(1.0),A,ITC1,9,2023,66.0\n"
          "IT1:X(1.0),A,ITC1,9,2024,67.2\n"
          "IT1:X(1.0),A,ITF4,9,2024,49.8\n"
          "IT1:X(1.0),A,ITH,9,2024,70.1\n")

CSV_AMBIGUO = ("DATAFLOW,FREQ,ITTER107,SEXISTAT1,TIME_PERIOD,OBS_VALUE\n"
               "IT1:X(1.0),A,ITC1,9,2024,67.2\n"
               "IT1:X(1.0),A,ITC1,9,2024,51.0\n")


class ClientFinto:
    """Sostituisce il client HTTP: nessuna rete, risposte decise dal test."""

    def __init__(self, csv=CSV_OK, con_eta=False, blocca_dopo=None):
        self.csv = csv
        self.con_eta = con_eta
        self.blocca_dopo = blocca_dopo
        self.chiamate = []

    def scarica(self, url, fonte="default", accept=None, forza=False):
        self.chiamate.append(url)
        if self.blocca_dopo is not None and len(self.chiamate) > self.blocca_dopo:
            raise L.FonteBloccata("ISTAT ha risposto 429: ci fermiamo.")
        if "/dataflow/" in url:
            df = url.split("/dataflow/IT1/")[1].split("?")[0]
            if df == "999_inesistente":
                return (b"<?xml version='1.0'?><m:Structure xmlns:m='http://www.sdmx.org/"
                        b"resources/sdmxml/schemas/v2_1/message'><m:Structures/></m:Structure>"), True
            return STRUTTURA.format(
                df=df, extra=DIM_ETA if self.con_eta else "").encode(), True
        if "/availableconstraint/" in url:
            eta = ('<c:KeyValue id="ETA1"><c:Value>Y15-64</c:Value>'
                   '<c:Value>Y_GE15</c:Value></c:KeyValue>') if self.con_eta else ""
            return VINCOLI.format(eta=eta).encode(), True
        if "/data/" in url:
            return self.csv.encode(), True
        return None, False


print("[1] Il cancello delle licenze")
ok(FR.consentita("istat") is True, "ISTAT è ammessa (CC BY 3.0 IT, uso commerciale)")
ok(FR.consentita("openpolis") is False, "Openpolis è respinta (CC BY-NC-SA)")
ok(FR.consentita("bdap") is False, "BDAP è respinta finché la licenza non è verificata")
try:
    FR.pretendi("cpt")
    ok(False, "pretendi() blocca una fonte non verificata")
except FR.FonteNonAmmessa as e:
    ok("non verificata" in str(e), "pretendi() blocca CPT spiegando il motivo")

print("\n[2] Limite di chiamata rispettato")
L.LIMITI.update(FR.limiti_per_client())
ok(L.LIMITI["istat"] >= 12.0,
   "fra due richieste a ISTAT si attendono almeno 12 s (sono %.0f)" % L.LIMITI["istat"])
ok(60.0 / L.LIMITI["istat"] < 5.0,
   "il ritmo resta sotto le 5 query al minuto dichiarate da ISTAT (%.1f/min)"
   % (60.0 / L.LIMITI["istat"]))

print("\n[3] Struttura letta dalla fonte, non presunta")
c = ClientFinto()
st, err = E.leggi_struttura(c, "150_915")
ok(err is None and st is not None, "la struttura viene letta")
ok(st.dimensioni == ["FREQ", "ITTER107", "SEXISTAT1", "TIME_PERIOD"],
   "dimensioni riconosciute nell'ordine dichiarato")
ok(st.etichette["SEXISTAT1"]["9"] == "totale", "le etichette ufficiali sono lette")

st2, err2 = E.leggi_struttura(ClientFinto(), "999_inesistente")
ok(st2 is None and "non esiste" in (err2 or ""),
   "un dataflow inesistente viene dichiarato, non inventato")

print("\n[4] Scelta dei filtri sulle etichette ufficiali")
disp = E.leggi_disponibili(c, "150_915")
filtri, terr, problema = E.scegli_filtri(st, disp, {})
ok(problema is None, "nessuna ambiguità con una sola etichetta «totale»")
ok(filtri.get("SEXISTAT1") == "9", "sceglie il codice etichettato «totale»")
ok(terr == "ITTER107", "riconosce la dimensione territoriale")

c2 = ClientFinto(con_eta=True)
st3, _ = E.leggi_struttura(c2, "150_915")
disp3 = E.leggi_disponibili(c2, "150_915")
f3, t3, p3 = E.scegli_filtri(st3, disp3, {})
ok(p3 is not None and "ambigua" in p3,
   "una dimensione senza totale evidente viene dichiarata ambigua, non indovinata")
f4, t4, p4 = E.scegli_filtri(st3, disp3, {"ETA1": "Y15-64"})
ok(p4 is None and f4.get("ETA1") == "Y15-64",
   "fissando il valore in configurazione l'ambiguità si risolve")

print("\n[5] Estrazione dei valori regionali")
val, anno, prob = E.estrai_regioni(CSV_OK.encode(), "ITTER107")
ok(prob is None, "CSV letto senza problemi")
ok(val == {"01": 67.2, "16": 49.8},
   "solo le regioni, con l'ultimo anno disponibile (%s)" % val)
ok("IT" not in val and len(val) == 2, "Italia e ripartizioni escluse dal confronto")
ok(anno == "2024", "anno dichiarato correttamente")

val2, anno2, prob2 = E.estrai_regioni(CSV_AMBIGUO.encode(), "ITTER107")
ok(val2 is None and "ambiguo" in (prob2 or ""),
   "due valori per la stessa regione e anno → indicatore scartato")

print("\n[6] Codici regione")
ok(E.codice_regione_sdmx("ITC1") == "01", "NUTS2 riconosciuto")
ok(E.codice_regione_sdmx("16") == "16", "codice numerico riconosciuto")
ok(E.codice_regione_sdmx("ITH1") == "04" and E.codice_regione_sdmx("ITH2") == "04",
   "le due province autonome confluiscono nel Trentino-Alto Adige")
ok(E.codice_regione_sdmx("IT") is None, "Italia non è una regione")
ok(E.codice_regione_sdmx("ITH") is None, "le ripartizioni non sono regioni")

print("\n[7] Blocco della fonte")
cb = ClientFinto(blocca_dopo=1)
bloccato = False
try:
    E.leggi_struttura(cb, "150_915")
    E.leggi_struttura(cb, "151_914")
except L.FonteBloccata:
    bloccato = True
ok(bloccato, "un 429 solleva FonteBloccata e interrompe: nessun tentativo di aggirarlo")

print("\n[8] Configurazione degli indicatori")
ok(len(E.INDICATORI) >= 12, "%d indicatori configurati" % len(E.INDICATORI))
ok(all(i["verso"] in ("alto", "basso", "neutro") for i in E.INDICATORI),
   "ogni indicatore dichiara il verso")
ok(len({i["chiave"] for i in E.INDICATORI}) == len(E.INDICATORI),
   "nessuna chiave duplicata")

print("\n" + (str(falliti) + " TEST FALLITI" if falliti else "TUTTI I TEST SUPERATI"))
sys.exit(1 if falliti else 0)
