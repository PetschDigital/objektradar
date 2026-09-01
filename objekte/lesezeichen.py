"""Das Lesezeichen-Skript. Eine Zeile JavaScript, die im Browser laeuft.

Eigenes Modul und ausdruecklich kein Textblock in der Vorlage: das Skript ist
Programmtext und gehoert nicht zwischen HTML, wo eine Einrueckung es
unbemerkt in zwei Zeilen bricht.

**Warum ueberhaupt ein Lesezeichen.** Ein Serverabruf der Inseratsseite
liefert bei Idealista HTTP 403 (DataDome), und die geladene Seite enthaelt
kein `application/ld+json`. Playwright ist verworfen - es muesste eine aktive
Schutzmassnahme aushebeln. Die Seite wird deshalb von dem Browser gelesen, in
dem sie ohnehin offen ist. Es gibt keine Sperre zu ueberwinden, weil kein
Automat abruft.

**Drei Festlegungen, die sich nicht von selbst verstehen:**

1. **Nur `window.open`.** Kein `fetch`, kein `XMLHttpRequest`, kein Nachladen
   eines Skripts von aussen: alle drei scheitern an der Content-Security-Policy
   der Portalseiten oder an CORS. `window.open` ist eine Navigation und von
   beidem nicht betroffen.

2. **Keine doppelten Anfuehrungszeichen.** Der Text steht im `href`-Attribut
   eines Links. Ein `"` darin spraenge das Attribut. Die Vorlage gibt ihn mit
   `{{ … }}` aus, damit Django escapet; im Skript selbst stehen nur einfache
   Anfuehrungszeichen. Aus demselben Grund werden die `og:`-Angaben ueber
   `getElementsByTagName` eingesammelt statt ueber einen Attributselektor -
   `meta[property='og:title']` braeuchte geschachtelte Anfuehrungszeichen.

3. **Eine Zeile.** Deshalb sind die Bausteine unten aneinandergesetzte
   Zeichenkettenliterale und keine mit `\\n` verbundenen Zeilen.

**Was NICHT gelesen wird**, und das ist Absicht: Grundstuecksgroesse (von der
Wohnflaeche im Fliesstext nicht sicher zu unterscheiden), Baujahr, Ort,
Region, Objekttyp und Zustand (ohne verlaessliche Auszeichnung im Markup, und
eine Fehlzuordnung bei einer Auswahl ist teuer). Und keine Kontaktdaten -
Maklername, Telefonnummer und Inserentendaten werden nicht gelesen, nicht
uebertragen und nicht gespeichert, auch dann nicht, wenn sie im Markup stehen.
"""

#: Wird durch die absolute Adresse des Uebernahme-Endpunkts ersetzt. Ein
#: Platzhalter und keine `%s`- oder `{}`-Formatierung: das Skript ist voller
#: geschweifter Klammern, und `str.format` verschluckte sich daran.
PLATZHALTER = "__ZIEL__"

#: Grenze fuer den zusammengebauten Query-String. Server und Proxys begrenzen
#: die Anfragezeile typischerweise auf 8 KB; ein abgeschnittener Aufruf waere
#: ein stiller Fehler. Wird die Grenze gerissen, fallen zuerst die Bild-URLs
#: weg, dann die Beschreibung.
QUERY_MAXLAENGE = 6000

#: Hoechstens so viele `og:image` gehen mit.
BILDER_MAX = 5

#: Die Beschreibung wird hier gekuerzt, nicht erst am Server.
BESCHREIBUNG_MAXLAENGE = 1000

SKRIPT = (
    "javascript:(function(){"
    "var d=document,ziel='" + PLATZHALTER + "';"
    # --- die generischen Quellen ---------------------------------------
    # Stabiler als jede Klassenauswahl: Portale pflegen sie fuer
    # Suchmaschinen und Social-Media.
    "var og={};"
    "[].forEach.call(d.getElementsByTagName('meta'),function(m){"
    "var p=(m.getAttribute('property')||'').toLowerCase();"
    "if(p.indexOf('og:')===0){var n=p.slice(3);"
    "(og[n]=og[n]||[]).push((m.getAttribute('content')||'').trim())}});"
    "var erst=function(n){return (og[n]&&og[n][0])||''};"
    # Ohne Query, ohne Fragment: Tracking-Parameter gehoeren nicht in den
    # Datenbestand, und derselbe Link mit und ohne sie ist dasselbe Inserat.
    "var u=location.origin+location.pathname;"
    "var titel=erst('title')||d.title||'';"
    "var beschreibung=erst('description').slice(0," + str(BESCHREIBUNG_MAXLAENGE) + ");"
    "var bilder=(og['image']||[]).filter(function(b){return b}).slice(0," + str(BILDER_MAX) + ");"
    # --- die Zahlen, per Texterkennung ---------------------------------
    # Portalspezifische CSS-Auswahlen sind hier bewusst NICHT vorgegeben:
    # das Markup ist nicht dokumentiert, und geratene Auswahlen brechen
    # unbemerkt.
    "var t=(d.body&&d.body.innerText)||'';"
    # Alle Treffer sammeln, den GROESSTEN nehmen: Nebenzahlen auf
    # Inseratsseiten - Nebenkosten, Monatsrate, Preis pro Quadratmeter -
    # sind kleiner als der Kaufpreis.
    "var preis='',groesster=0;"
    "(t.match(/\\d{1,3}(?:[.\\s]\\d{3})+\\s*€/g)||[]).forEach(function(s){"
    "var z=parseInt(s.replace(/[^0-9]/g,''),10);if(z>groesster){groesster=z}});"
    "if(groesster>0){preis=String(groesster)}"
    "var flaeche='',f=t.match(/(\\d{2,5})\\s*m²/);"
    "if(f){var fz=parseInt(f[1],10);if(fz>=10&&fz<=10000){flaeche=String(fz)}}"
    "var zimmer='',r=t.match(/(\\d{1,2})\\s*(?:bed|hab|Zimmer|Schlafzimmer)/);"
    "if(r){var rz=parseInt(r[1],10);if(rz>=1&&rz<=20){zimmer=String(rz)}}"
    # --- den Query-String bauen ----------------------------------------
    # Leere Felder fallen weg. Was die Muster nicht sicher treffen, bleibt
    # leer und wird im Vorschauformular von Hand ergaenzt - kein Feld wird
    # geraten.
    "var bauen=function(mitBildern,mitText){"
    "var teile=[],setze=function(n,v){if(v){teile.push(n+'='+encodeURIComponent(v))}};"
    "setze('url',u);setze('titel',titel);"
    "if(mitText){setze('beschreibung',beschreibung)}"
    "setze('preis',preis);setze('wohnflaeche',flaeche);setze('zimmer',zimmer);"
    "if(mitBildern){bilder.forEach(function(b){setze('bilder',b)})}"
    "return teile.join('&')};"
    "var q=bauen(true,true);"
    "if(q.length>" + str(QUERY_MAXLAENGE) + "){q=bauen(false,true)}"
    "if(q.length>" + str(QUERY_MAXLAENGE) + "){q=bauen(false,false)}"
    # Eine Navigation - und damit weder von CORS noch von der
    # Content-Security-Policy der Portalseite betroffen.
    "window.open(ziel+'?'+q,'_blank')"
    "})();"
)


def skript_fuer(ziel):
    """Das Skript mit eingesetzter Zieladresse.

    Die Adresse wird gerendert und nicht hartkodiert: damit stimmt das
    Lesezeichen lokal auf Port 8347 genauso wie spaeter auf dem VPS. Aendert
    sich die Adresse, zieht man das Lesezeichen neu.
    """
    return SKRIPT.replace(PLATZHALTER, ziel)
