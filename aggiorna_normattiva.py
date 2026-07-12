#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Polis — ETL notturno v3: elenco atti + TESTI articolo per articolo.
1) ricerca/avanzata: atti normativi aggiornati pubblicati in GU dal 1/1/2026
2) per i 12 atti più recenti: atto/dettaglio-atto-urn → articoli 1..8
   (l'API restituisce un articolo per chiamata; parametri tentati in più forme
   per robustezza — al primo giro il log dice quale forma accetta il server)
Output: dati/normattiva.json con atti[].articoli=[{n,testo}].
Riuso testi: consentito citando fonte e carattere non ufficiale/gratuito
(dati.normattiva.it — IPZS); il testo ufficiale resta la Gazzetta Ufficiale."""
import json, re, time, urllib.request, urllib.error, datetime, html as H

BASE='https://api.normattiva.it/t/normattiva.api/bff-opendata/v1/api/v1'
MESI={'gennaio':'01','febbraio':'02','marzo':'03','aprile':'04','maggio':'05','giugno':'06',
      'luglio':'07','agosto':'08','settembre':'09','ottobre':'10','novembre':'11','dicembre':'12'}
URN_TIPO={'DECRETO-LEGGE':'decreto.legge','LEGGE':'legge','DECRETO LEGISLATIVO':'decreto.legislativo',
  'DECRETO DEL PRESIDENTE DELLA REPUBBLICA':'decreto.del.presidente.della.repubblica',
  'DECRETO DEL PRESIDENTE DEL CONSIGLIO DEI MINISTRI':'decreto.del.presidente.del.consiglio.dei.ministri'}

def post(path, body, timeout=45):
    req=urllib.request.Request(BASE+path, data=json.dumps(body).encode(),
        headers={'Content-Type':'application/json','Accept':'application/json',
                 'User-Agent':'Polis-civic-app (ETL, riuso con citazione fonte)'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def decode(t): return H.unescape(str(t or ''))
def pulisci(t):
    t=decode(t); t=re.sub(r'^\s*\[','',t); t=re.sub(r'\]\s*$','',t)
    return re.sub(r'\(\d{2}[A-Z]\d{5}\)\s*$','',t).strip()

def urn_di(a):
    tipo=URN_TIPO.get((a.get('denominazioneAtto') or '').upper().strip())
    num=a.get('numeroProvvedimento') or a.get('numeroAtto')
    m=re.search(r'(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\s+(\d{4})',
                decode(a.get('descrizioneAtto','')), re.I)
    if not (tipo and num and m): return None
    return f"urn:nir:stato:{tipo}:{m.group(3)}-{MESI[m.group(2).lower()]}-{int(m.group(1)):02d};{num}"

def spoglia_html(s):
    s=re.sub(r'<(script|style)[^>]*>.*?</\1>','',s,flags=re.S|re.I)
    s=re.sub(r'<br\s*/?>','\n',s,flags=re.I)
    s=re.sub(r'</p>','\n',s,flags=re.I)
    s=re.sub(r'<[^>]+>',' ',s)
    s=H.unescape(s)
    return re.sub(r'[ \t]+',' ',re.sub(r'\n{3,}','\n\n',s)).strip()

def testo_da_risposta(j):
    """estrae il testo utile da risposte di forma ignota, in profondità"""
    migliore=''
    def visita(x):
        nonlocal migliore
        if isinstance(x,str):
            if len(x)>len(migliore) and ('Art' in x or len(x)>300): migliore=x
        elif isinstance(x,dict):
            for v in x.values(): visita(v)
        elif isinstance(x,list):
            for v in x: visita(v)
    visita(j)
    return spoglia_html(migliore) if migliore else ''

def articolo(urn, n):
    """prova le forme di payload documentate/plausibili; log della prima che risponde"""
    forme=[{'urn':urn,'articolo':str(n)},{'urn':urn,'numeroArticolo':str(n)},
           {'urn':f'{urn}~art{n}'},{'urn':urn} if n==1 else None]
    for body in forme:
        if body is None: continue
        try:
            j=post('/atto/dettaglio-atto-urn', body)
            t=testo_da_risposta(j)
            if t and len(t)>60:
                articolo.forma=articolo.forma or json.dumps(list(body.keys()))
                return t[:1800]
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
            continue
    return None
articolo.forma=None

def main():
    oggi=datetime.date.today().isoformat()
    j=post('/ricerca/avanzata',{'orderType':'recente','classeProvvedimento':'2',
        'dataInizioPubProvvedimento':'2026-01-01','dataFinePubProvvedimento':oggi,
        'paginazione':{'paginaCorrente':1,'numeroElementiPerPagina':100}})
    atti=[{'titolo':pulisci(a.get('titoloAtto')),'tipo':a.get('denominazioneAtto',''),
           'numero':a.get('numeroProvvedimento') or a.get('numeroAtto') or '',
           'anno':a.get('annoProvvedimento',''),'gu':a.get('dataGUStr') or a.get('dataGU') or '',
           'numeroGU':a.get('numeroGU',''),'cod':a.get('codiceRedazionale',''),
           'descr':decode(a.get('descrizioneAtto','')),'urn':urn_di(a)}
          for a in j.get('listaAtti',[])]
    # testi: 12 atti più recenti, fino a 8 articoli ciascuno
    con_testo=0
    for a in atti[:12]:
        if not a['urn']: continue
        arts=[]
        for n in range(1,9):
            t=articolo(a['urn'],n)
            if not t: break
            arts.append({'n':str(n),'testo':t})
            time.sleep(0.6)  # gentilezza verso il server
        if arts:
            a['articoli']=arts; con_testo+=1
    out={'aggiornato':int(time.time()*1000),'totale':j.get('numeroAttiTrovati',len(atti)),
         'fonte':'dati.normattiva.it (IPZS) — testi non ufficiali a scopo informativo, riproduzione gratuita con menzione della fonte; testo ufficiale: Gazzetta Ufficiale',
         'atti':atti}
    with open('dati/normattiva.json','w',encoding='utf-8') as f:
        json.dump(out,f,ensure_ascii=False)
    print(f'atti: {len(atti)} · con testo articoli: {con_testo} · forma payload accettata: {articolo.forma or "nessuna (verificare endpoint nel log)"}')

if __name__=='__main__':
    main()
