#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Polis — ETL notturno: scarica gli atti normativi aggiornati pubblicati in
Gazzetta dal 1° gennaio 2026 (API Open Data dati.normattiva.it, CC BY 4.0)
e scrive dati/normattiva.json nel formato che l'app legge direttamente.
Eseguito ogni notte da GitHub Actions (vedi .github/workflows/aggiorna-dati.yml)."""
import json, re, time, urllib.request, datetime, html as H

API='https://api.normattiva.it/t/normattiva.api/bff-opendata/v1/api/v1/ricerca/avanzata'

def decode(t):
    return H.unescape(str(t or ''))

def pulisci(t):
    t=decode(t)
    t=re.sub(r'^\s*\[','',t); t=re.sub(r'\]\s*$','',t)
    t=re.sub(r'\(\d{2}[A-Z]\d{5}\)\s*$','',t)
    return t.strip()

def main():
    oggi=datetime.date.today().isoformat()
    body=json.dumps({
        'orderType':'recente','classeProvvedimento':'2',
        'dataInizioPubProvvedimento':'2026-01-01','dataFinePubProvvedimento':oggi,
        'paginazione':{'paginaCorrente':1,'numeroElementiPerPagina':100}
    }).encode()
    req=urllib.request.Request(API,data=body,headers={
        'Content-Type':'application/json','Accept':'application/json',
        'User-Agent':'Polis-civic-app (ETL notturno, dati CC BY 4.0)'})
    with urllib.request.urlopen(req,timeout=60) as r:
        j=json.load(r)
    atti=[{
        'titolo':pulisci(a.get('titoloAtto')),
        'tipo':a.get('denominazioneAtto',''),
        'numero':a.get('numeroProvvedimento') or a.get('numeroAtto') or '',
        'anno':a.get('annoProvvedimento',''),
        'gu':a.get('dataGUStr') or a.get('dataGU') or '',
        'numeroGU':a.get('numeroGU',''),
        'cod':a.get('codiceRedazionale',''),
        'descr':decode(a.get('descrizioneAtto',''))
    } for a in j.get('listaAtti',[])]
    out={'aggiornato':int(time.time()*1000),
         'totale':j.get('numeroAttiTrovati',len(atti)),
         'fonte':'dati.normattiva.it (IPZS) — CC BY 4.0',
         'atti':atti}
    with open('dati/normattiva.json','w',encoding='utf-8') as f:
        json.dump(out,f,ensure_ascii=False)
    print(f'salvati {len(atti)} atti (totale banca dati: {out["totale"]})')

if __name__=='__main__':
    main()
