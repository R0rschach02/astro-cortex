# LESSONS - wiederkehrende Bug-Muster dieses Projekts

> **Gemeinsames Muster aller Faelle:** Beweis auf einer Ebene (Datei-Kopie,
> DB, JS-Zustand, Referenzregion, Fallback-Erfolg) wurde mit Beweis auf der
> eigentlich relevanten Ebene verwechselt. Bei jeder neuen Aenderung: Was
> genau wird hier bewiesen, und ist das wirklich die Ebene, die zaehlt?

Erweitert bei jedem neuen Vorfall dieser Art (analog changelog.json).

## 1. Workspace-Kopie vs. Live-Dienste
Workspace bearbeitet/getestet, waehrend Live-Dienste die separat deployte
Kopie ausfuehrten - Aenderungen wirkten, griffen aber nie. Ein Deploy
(astro_deploy.sh cp Workspace -> Live) in der falschen Richtung hat einmal
alle Live-Aenderungen mit dem alten Workspace-Stand ueberschrieben.
-> Crawler-Aenderungen IMMER in der Workspace-Kopie beginnen, Deploy macht
   den Rest; nach jedem Vorfall md5 Workspace vs. Live vergleichen.

## 2. Langlebiger Backend-Prozess (astro-app / uvicorn)
DB-Zeilen nach einem Standort-Update korrekt, aber der laufende
astro-app.service servierte weiter alte Werte aus dem Arbeitsspeicher -
kein Neustart, kein Import des neuen Stands.
-> Deploy-Skript restartet den Dienst seitdem immer mit; ExecStart-Pfad
   im Deploy-Beweis ausweisen.

## 3. JS-Zustand ist kein Beweis fuer gerenderte Pixel
RainViewer-Kachel-Animation: Timer, Opacity, Zeitstempel und
naturalWidth>0 waren komplett korrekt - die tatsaechlich gerenderten
Kacheln waren identische Fehlergrafiken ("Zoom Level Not Supported",
HTTP 200, ab z8). DOM-/Zustandspruefung validiert "geladen", nicht
"Inhalt".
-> "Visuell bewiesen" heisst immer Screenshot-Pixel-Diff mit Zahlen.

## 4. Referenzregion ist nicht der Zielort
Nach dem RainViewer-Fix wurde der Pixel-Beweis auf einer
Regen-Referenzregion (Alpen) erbracht - der eigentliche Zielort
(Mannheim) wurde nie separat nachgeprueft; der Nutzer fand die Luecke
selbst ueber Live-Beobachtung.
-> Beweis am konkreten Nutzungsort fahren, nicht nur am einfachen Ort.

## 5. Injizierte Testdaten koennen echte Fehler verdecken
Regen-Icon-Zeitregler: Fuer den Pixel-Beweis wurden Testdaten injiziert,
weil echte Daten gerade trocken waren - dabei wurde zunaechst eine echte,
unabhaengige JS-Laufzeitfehler-Luecke (undefinierte Variable) uebersehen.
-> Injizierte Daten nur nachweislich auf dem echten Codepfad testen und
   das Live-Verhalten zusaetzlich gegen die echte API beweisen.

## 6. Python-Gates sehen JS nicht
Frontend-JS-Laufzeitfehler (undefinierte Variable) legte das komplette
Icon-Rendering lahm - weder ruff noch pytest (beide Python-only) koennen
das sehen. Ein Deploy lief trotzdem durch.
-> Frontend-Aenderungen brauchen IMMER echte Browser-/Pixel-Verifikation,
   unabhaengig von den Gates. Nachtraeger-Crash-Falle: Handler-Funktionen
   werden von Gates UND Seiten-Load nicht erfasst, wenn sie erst bei
   Interaktion laufen (siehe Fall 9).

## 7. Funktionierender Fallback verschleiert den Ausfall
Ein funktionierender Fallback (ClearOutside -> Open-Meteo) verschleierte
36 Stunden einen kompletten Ausfall der Primaeerquelle (Regression:
Variable vor Zuweisung genutzt), weil der Fallback anstandslos lief und
keinen sichtbaren Fehler produzierte.
-> data_sanity-Pruefungen (Fehlerquote, Quellenabgleich) laufen seither
   in jedem Heavy-Tick; Quellen-Health aktiv monitoren, nicht nur
   Endergebnis.

## 8. Externe Abhaengigkeiten aendern ihre Bedingungen (CARTO-API-Key)
Die CARTO-Basiskacheln verlangten ploetzlich einen API-Key (extern,
unangekuendigt) - Kartenhintergrund away, ohne dass irgendetwas im
eigenen Code geaendert hatte. (Behoben ausserhalb der ZCode-Sessions;
Details siehe changelog.)
-> Externe Kachel-/API-Anbieter in data_sanity/Stichprogen im Blick
   behalten; Basiskarten-Fallback pruefen (OSM direkt verfuegbar halten).

## 9. Halb fertige Aenderungsreihe erreichte Produktion (rgTogglePlay)
Eine abgebrochene Aenderungsreihe hinterliess FunktionsAUFRUFE ohne
Definition (rgTogglePlay/setRgHour) im Workspace; der Stand wurde
deployt (Commit-Text der Hotfix-Kette: e871434) und brachte initMap()
zum Absturz - die Karte war komplett tot. Der nachgelieferte Hotfix
ergaenzte rgTogglePlay/rgAdvance, LIESS ABER setRgHour weiterhin
undefiniert: der Crash wanderte nur vom Seiten-Laden in die erste
Interaktion (Slider/Play-Klick).
-> Vor jedem Deploy: grep auf jede im Diff neu referenzierte Funktion;
   nach Abbruch/Neustart einer Session zuerst diff Workspace vs. Live
   sichten, nie blind weiterbauen. Interaktions-Pfade (Klicks!) im
   Browser-Test mit abdecken, nicht nur Seiten-Load.

## 10. Uncommittete Whitelist-Zeile machte Suite-Commit leer
Die .gitignore-Zeile `!/tests/` blieb uncommittet - der Commit
"feat: pytest suite ... as deploy gate" enthielt nur das Skript, die
Test-Dateien selbst blieben unversioniert im HOME (Repo-Verlust haette
das Deploy-Gate gebrochen).
-> Bei Whitelist-.gitignore: Whitelist-Zeile und die Dateien, die sie
   freigeben soll, im SELBEN Commit pruefen (git ls-files gegen
   Dateibauf abgleichen).

## 11. Sekundaerbehauptung statt Primaerlizenz
 Fuer die GEIPAN-Datenuebernahme stand "Licence Ouverte/Etalab" im Raum
 (Spec-Revision + Drittseiten wie carteovni.fr behaupten das). Die
 Primaerpruefung (cnes-geipan.fr selbst + offizieller Katalog
 data.gouv.fr) ergab: keine Reuse-Freigabe auf der Site, kein GEIPAN-
 Datensatz im Katalog - die Freigabe war nicht belegbar.
 -> Lizenzen nur aus der Primaerquelle zitieren (docs/SOURCE_LEGAL_REVIEW.md);
    fehlende Freigabe heisst "ungeklaert", nie "wahrscheinlich okay".

## 12. Wiederkehrendes 0-Byte-Artefakt = Symptom eines fremden Prozesses
 Zweimal tauchte eine leere "astro-app/astro_crawler.py" auf (zweites Mal
 in astro-app/astro-app/ verschachtelt). Forensik: Birth-Timestamp
 03:03:54 nachts korrelierte exakt mit dem Start eines aider-Prozesses
 (--model ollama/qwen3-coder), der mit dem FALSCHEN Datei-Argument
 "astro-app/astro_crawler.py" gestartet wurde (Datei liegt in ~, nicht in
 ~/astro-app). aider legt fehlende Argument-Dateien als LEERE Dateien an
 ("> Creating empty file ..." in ~/.aider.chat.history.md); nach "/cd
 ~/astro-app" in der Session verschachtelte sich der Pfad nochmals.
 -> Root Cause war ein Werkzeug-Aufruffehler, kein Deploy/Hotfix-Rest.
    Fixes: Shell-Alias aider-astro (korrekte Pfade, Workspace-Kopie) in
    ~/.bashrc; Deploy-Guard bricht bei fremden astro_crawler.py-Kopien
    hart ab. Merksatz: wiederkehrende Artefakte erst der Birth-Time nach
    correlationsprozessen zuordnen (journalctl, ps, Tool-Histories),
    bevor Hotfixes/Deploy-Logik verdächtigt werden.
    Meta-Lesson: selbst der Guard hatte einen Bug - find liefert bei
    Permission-Denied Exit 1 und set -euo pipefail bricht die stille
    Zuweisung ab; immer "|| true" bei diagnostischen find-Aufrufen in
    strict-mode-Skripten.
