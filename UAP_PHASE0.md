# UAP-Feature: Phase 0 - Quellenklaerung (Stand 2026-09)

Vorbedingung: KEIN Quellcode fuer UAP-Datenquellen existiert (Bestandsaufnahme
2026-09: null Treffer fuer nuforc/uap/ufo im Repo inkl. kompletter Historie).
Dieses Dokument ist die Entscheidungsgrundlage, bevor irgendein Source-Modul
gebaut wird.

## NUFORC - NICHT im Code-Pfad
Status: bewusst nicht implementiert. Optional falls die laufende
CTO-Anfrage positiv beantwortet wird (formale Nutzungsgenehmigung/API-
Zugang). Kein Crawling der Website bis dahin.

## Enigma Labs (enigmalabs.io)
ToS gelesen (Stand "Last updated: July 12, 2023"):
- Enthaelt KEINE Klausel zu API-Zugang, Scraping oder Rate-Limits;
  ebenso keine Einraumung fuer Dritt-Reuse.
- "The Service and its original content [...] are and will remain the
  exclusive property of the Company" - abweisend gegenueber ungeklaertem
  automatisiertem Zugriff.
- Positiv: Nutzer-Content bleibt im Eigentum der Nutzer; der eigene
  Blog ("data transparency") kuendigt an, Sichtungsdaten frei und oeffentlich
  zu halten und historische Daten nicht zu monetarisieren.
Bewertung: GRAUZONE - nicht nutzbar ohne geklaerten Zugang. Empfehlung:
Offiziellen API-Zugang erfragen; bis dahin kein Abruf, kein Scraping.

## GEIPAN (CNES, Frankreich)
- Veroeffentlichte Fall-Daten (anonymisierte Zeugnisse, Klassifikation
  A/B/C/D) stehen unter Licence Ouverte / Etalab: kostenlose Weiter-
  verwendung inkl. kommerziell, bei Quellenangabe.
- KEINE formelle oeffentliche API; Zugang ueber Websuche/Downloads
  (cnes-geipan.fr, teils data.gouv.fr); Drittprojekte (CarteOvni u.a.)
  nutzen genau diesen Weg.
Bewertung: NUTZBAR - rechtlich sauber (Etalab: Attribution
"GEIPAN/CNES"), technisch Download/Export statt API.

## ufo-hunters.com
- Keine auffindbaren Nutzungsbedingungen (keine Terms-Seite indexiert);
  Disclaimer beruft sich fuer fremde Inhalte auf Fair Use (17 U.S.C. 107),
  Privacy Policy lehnt Verantwortung fuer Republishing ab - das ist
  Duldsamkeits-Andeutung, KEINE Lizenz.
- Keine API, kein dokumentierter Bulk-Zugriff.
Bewertung: NICHT NUTZBAR ohne Ruecksprache - automatisierter Abruf
unterbleibt, bis der Betreiber (Kontakt-E-Mail vorhanden) zustimmt.

## Konsequenz fuer Phase 1
Einzige sofort baubare Quelle: GEIPAN (Etalab). Enigma Labs nach
Zugangs-Klaerung, NUFORC nach CTO-Antwort, ufo-hunters.com nach
Betreiber-Ruecksprache.
