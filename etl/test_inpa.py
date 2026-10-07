import sys, os, json, tempfile
sys.path.insert(0, sys.argv[1] if len(sys.argv)>1 else os.path.dirname(os.path.abspath(__file__)))
import lib_fonti as L, etl_inpa as E, fonti_regioni as FR
f=0
def ok(c,m):
    global f; print(("  OK   " if c else "  FALLITO ")+m); f+= 0 if c else 1

ROBOTS=b"""User-agent: *
Disallow: /wp-admin/
Disallow: /wp-json/
Allow: /wp-admin/admin-ajax.php
Sitemap: https://www.inpa.gov.it/sitemap.xml
"""
SM=b"""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://inpa.gov.it/bandi-e-avvisi/dettaglio-bando-avviso/?concorso_id=d9720f55a2374799b9c066bea6ef70b5</loc><lastmod>2026-10-04T22:01:00Z</lastmod></url>
<url><loc>https://inpa.gov.it/bandi-e-avvisi/dettaglio-bando-avviso/?concorso_id=530f447cdf16499e88284dc0d889e007</loc><lastmod>2026-10-03T08:00:00Z</lastmod></url>
<url><loc>https://inpa.gov.it/bandi-e-avvisi/dettaglio-bando-avviso/?concorso_id=NONVALIDO</loc><lastmod>2026-10-03T08:00:00Z</lastmod></url>
</urlset>"""
def pagina(titolo, area, chiusura, ente="Comune di Umbertide", posti="1"):
    return ("""<html><head><title>%s</title></head><body><h1>%s</h1>
<p>Descrizione: In esecuzione della Deliberazione n. 3 ... Stato giuridico &egrave; citato nel testo.</p>
<p>Area geografica: %s</p><p>Valutazione: Per titoli e colloquio</p><p>Stato: <strong>Aperto</strong></p>
<p>Data apertura candidature: 05 Ottobre 2026 00:01</p><p>Data chiusura candidature: %s</p>
<p>Numero di posti: %s</p><p>Ente di riferimento: %s</p><p>Bando/Avviso e Allegati:</p>
<a href="https://portale.inpa.gov.it/api/media/4d99e4b1-1ece-43fa-bd26-943fdb5439fe">Avviso mobilit&agrave;.pdf (Pubblicato il 05 Ottobre 2026 00:01)</a>
<a href="https://portale.inpa.gov.it/ui/public-area/login">Invia la tua candidatura</a><h2>Contatti</h2></body></html>"""
      %(titolo,titolo,area,chiusura,posti,ente)).encode()

class Finto:
    def __init__(s, pagine, blocca=None):
        s.pagine=pagine; s.chieste=[]; s.blocca=blocca
    def scarica(s,url,fonte="default",accept=None,forza=False):
        s.chieste.append(url)
        if s.blocca and len(s.chieste)>s.blocca: raise L.FonteBloccata("INPA ha risposto 429")
        if url.endswith("robots.txt"): return ROBOTS,True
        if url.endswith("sitemap-bandi.xml"): return SM,True
        return s.pagine.get(url),True

print("[1] Licenza e limiti")
ok(FR.consentita("inpa"),"inPA è fra le fonti ammesse (CC BY 3.0)")
L.LIMITI.update(FR.limiti_per_client())
ok(L.LIMITI["inpa"]>=3,"3 s fra le richieste")

print("\n[2] robots.txt letto dalla fonte")
r=E.Robots(Finto({}))
ok(not r.consentito("https://www.inpa.gov.it/wp-json/wp/v2/posts"),"/wp-json/ è escluso: non lo chiediamo")
ok(r.consentito("https://www.inpa.gov.it/bandi-e-avvisi/dettaglio-bando-avviso/?concorso_id=x"),"le pagine dei bandi sono consentite")
ok(r.consentito(E.SITEMAP),"la sitemap è consentita")

print("\n[3] Sitemap")
v=E.leggi_sitemap(SM)
ok(len(v)==2,"due bandi validi, l'identificativo non valido è scartato")
ok(v[0][1].startswith("https://www.inpa.gov.it/"),"si usa l'indirizzo canonico con www, senza passare dal reindirizzamento")

print("\n[4] Pagina del bando")
rec,p=E.analizza_pagina(pagina("AVVISO DI MOBILIT&Agrave; ISTRUTTORE","Umbria","04 Novembre 2026 23:59"),"d9720f55a2374799b9c066bea6ef70b5",v[0][1])
ok(p is None,"pagina riconosciuta (%s)"%p)
ok(rec["ente"]=="Comune di Umbertide","ente letto dall'etichetta")
ok(rec["chiusura"]=="2026-11-04T23:59","data di chiusura convertita")
ok(rec["apertura"]=="2026-10-05T00:01","data di apertura convertita")
ok(rec["posti"]==1 and rec["regioni"]==["Umbria"],"posti e regione riconosciuta")
ok(rec["stato"]=="Aperto" and rec["valutazione"]=="Per titoli e colloquio","stato e valutazione")
ok(rec["titolo"]=="AVVISO DI MOBILITÀ ISTRUTTORE","entità HTML del titolo decodificate")
ok(len(rec["allegati"])==1 and rec["allegati"][0]["nome"]=="Avviso mobilità.pdf","allegato dal media server ufficiale, link di login escluso")
ok("Deliberazione" not in json.dumps(rec),"il testo integrale del bando non viene copiato")

rec2,p2=E.analizza_pagina(b"<html><h1>Titolo</h1><p>niente</p></html>","x"*32,"u")
ok(rec2 is None and "etichette non trovate" in p2,"pagina cambiata: scartata con il motivo, nessun campo inventato")
rec3,p3=E.analizza_pagina(pagina("T","Nazionale","quando capita"),"y"*32,"u")
ok(rec3 is None and "non leggibile" in p3,"data illeggibile: scartato")
rec4,_=E.analizza_pagina(pagina("T","Nazionale","30 Dicembre 2026 12:00"),"z"*32,"u")
ok(rec4["nazionale"] and rec4["regioni"]==[],"bando nazionale riconosciuto")

print("\n[5] Esecuzione completa con client finto")
out=tempfile.mkdtemp()
pag={v[0][1]:pagina("A","Umbria","04 Novembre 2026 23:59"), v[1][1]:pagina("B","Lazio","01 Gennaio 2020 10:00")}
fin=Finto(pag)
L.Client=lambda *a,**k: fin
sys.argv=["x","--out",out,"--cache",out]
E.main()
d=json.load(open(os.path.join(out,"concorsi.json")))
ok(d["n_concorsi"]==1,"il bando già scaduto non entra nell'archivio")
ok(not any("/wp-json/" in u for u in fin.chieste),"nessuna richiesta a /wp-json/")
ok(d["_licenza"]=="CC BY 3.0 IT" and "fa fede" in d["_avvertenza"],"licenza e avvertenza nel file")
n=len(fin.chieste)
fin.chieste=[]; E.main()
ok(len(fin.chieste)==2,"seconda esecuzione: solo robots e sitemap, nessuna pagina riscaricata (%d richieste)"%len(fin.chieste))

print("\n[6] Blocco della fonte")
out2=tempfile.mkdtemp(); fb=Finto(pag,blocca=2)
L.Client=lambda *a,**k: fb
sys.argv=["x","--out",out2,"--cache",out2]
E.main()
d2=json.load(open(os.path.join(out2,"concorsi.json")))
ok(d2["_completo"] is False,"su 429 si ferma e lo dichiara nel file")
print("\n"+("%d TEST FALLITI"%f if f else "TUTTI I TEST SUPERATI")); sys.exit(1 if f else 0)
