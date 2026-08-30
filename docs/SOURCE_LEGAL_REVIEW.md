# SOURCE_LEGAL_REVIEW.md - UAP-Quellen, Primaerpruefung

Geprueft am: 2026-08-30. Methode: direkter Abruf der Seiten (curl,
Roh-HTML) bzw. Wayback-Machine-Volltext; Zitate wortgetreu aus den
abgerufenen Dokumenten, KEINE Uebernahme aus Sekundaerquellen oder
Spec-Revisionen. Suchreihenfolge je Quelle: Reuse-/Lizenz-/Open-Data-
Seite, dann erst Impressum/Disclaimer.

Bewertungsskala: NUR "freigegeben" (explizite Lizenz), "geklaert nach
Rueckfrage" oder "ungeklaert". Keine Einstufung als "wahrscheinlich okay".

---

## 1) GEIPAN (CNES) - cnes-geipan.fr - UNGEKLAERT

Gepruefte Orte:
- https://www.cnes-geipan.fr/fr (Startseite): einziger Rechts-Link im
  Footer ist `/fr/mentions_legales`. Kein Open-Data-, Lizenz- oder
  Reuse-Link vorhanden.
- https://www.cnes-geipan.fr/fr/mentions_legales - exakter Wortlaut:

  > "Toutes les données, et plus généralement le contenu du site Internet
  > sont la propriété du CNES, ou d'un tiers et pour lesquels il a obtenu
  > le droit d'en disposer. Ils sont rendus accessibles tels quel, tels
  > que disponibles, et sans garantie d'aucune sorte"

  (Eigentumsvorbehalt + Haftungsausschluss; KEINE Weiterverwendungs-
  freigabe, kein Lizenzvermerk.)
- https://www.cnes-geipan.fr/fr/web/deir (Fall-Datenbank): nur eine
  ~4 kB SPA-Shell; 0 Treffer fuer "licen", "réutilis", "csv", "export",
  "télécharg" im ausgelieferten HTML.
- Offizieller franzoesischer Open-Data-Katalog data.gouv.fr (API v2,
  Lizenzfeld je Datensatz): Suche "geipan" -> 8 Treffer, ALLE von
  anderen Organisationen (GEOPAL, DDT Var, OpenHealth, ...), kein
  GEIPAN-/CNES-Datensatz. Organisation "Centre National d'Etudes
  Spatiales" existiert (ID 563a2cdf88ee385a94531575), hat dort aber
  0 Datensaetze.
- https://www.cnes-geipan.fr/sitemap.xml -> liefert HTML-Fallback,
  keine Sitemap-Navigation auswertbar.

Fazit: Die in einer Spec-Revision behauptete Freigabe "Licence Ouverte /
Etalab" ist aus Primaerquellen NICHT belegbar - weder auf der Site noch
im offiziellen Open-Data-Katalog. (Dass Drittprojekte wie carteovni.fr
eine solche Lizenz BEHAUPTEN, ist Sekundaerquelle und zaehlt nicht.)
Status: UNGEKLAERT. Vor jedem Abruf: CNES/GEIPAN direkt anfragen
(Kontaktformular/Postmaster) und schriftliche Nutzungsfreigabe einholen.

## 2) Enigma Labs - enigmalabs.io - UNGEKLAERT

Gepruefte Orte:
- https://enigmalabs.io/terms (Live-Abruf: HTTP 403; Volltext via
  Wayback-Machine-Snapshot vom 2026-06-12:
  http://web.archive.org/web/20260612081939/https://enigmalabs.io/terms ,
  "Last updated: July 12, 2023"). Exakte Wortlaute:

  > "The Service and its original content (excluding Content provided by
  > You or other users), features and functionality are and will remain
  > the exclusive property of the Company and its licensors."

  > "By posting Content to the Service, You grant Us the royalty-free,
  > worldwide, perpetual right and license to use, modify, publicly
  > perform, publicly display, reproduce, create derivative works of and
  > distribute such Content [...] You agree that this license includes
  > the right for Us to make Your Content available to other users of
  > the Service, who may also use Your Content subject to these Terms."

- Volltext-Suche im ToS: "scrap" = 0 Treffer, "reuse" = 0 Treffer,
  API-Klausel = nicht enthalten (einziger "API"-Treffer ist die
  Definition von "Application"). Keine Rate-Limit-, keine Bulk-Daten-,
  keine Attribution-Regel.

Fazit: Keine explizite Freigabe fuer programmatischen Zugriff oder
Dritt-Weiterverwendung; die "exclusive property"-Klausel plus
Schweigen zu Automatisierung ergibt keine Nutzungsgrundlage.
Status: UNGEKLAERT. Nur nach offizieller API-/Zugangs-Zusage nutzbar.

## 3) ufo-hunters.com - UNGEKLAERT

Gepruefte Orte:
- https://www.ufo-hunters.com/terms -> HTTP 404; /terms-of-use -> 404;
  /disclaimer -> 404; /privacy-policy -> 404; /about -> 404.
  Es existiert KEINE aufrufbare Nutzungsbedingungen-Seite.
- https://www.ufo-hunters.com/sightings/about - exakter Wortlaut des
  einzigen Rechts-Hinweises (Disclaimer-Overlay):

  > "The information on this site that is not produced by
  > ufo-hunters.com is covered under the Copyright Disclaimer Under
  > Section 107 of the Copyright Act 1976"

  Footer: "UFO HUNTERS (c) 2026 UFO HUNTERS - HIGH ALTITUDE
  INTELLIGENCE DATASET". Volltext-Suche: "terms" = 0, "licen" = 0,
  "permission" = 0 Treffer auf der About-Seite.
- robots.txt: sperrt nur /sessions/*, /users/*, /password_resets/*,
  Fehlerseiten und /articles/myspace - die Sichtungsseiten sind NICHT
  gesperrt (robots.txt ist aber keine Lizenz, nur Zusatzinfo).

Fazit: Kein Lizenztext, keine Terms, kein Bulk-/API-Zugang dokumentiert;
Fair-Use-Hinweis betrifft nur FREMDE Inhalte und ist keine Einraumung
an Dritte. Betreiber sind auf der About-Seite namentlich genannt
(Rueckfrage moeglich). Status: UNGEKLAERT.

---

## Gesamtstatus

| Quelle        | Status     | Naechster Schritt                          |
|---------------|------------|--------------------------------------------|
| GEIPAN        | ungeklaert | Schriftliche Freigabe CNES/GEIPAN anfragen |
| Enigma Labs   | ungeklaert | Offiziellen API-Zugang erfragen            |
| ufo-hunters   | ungeklaert | Betreiber-Rueckfrage                       |
| NUFORC        | nicht geprueft (CTO-Anfrage laeuft) | abwarten   |

KEIN Source-Modul, KEIN Scraper fuer eine dieser Quellen, bis der
jeweilige Status "freigegeben"/"geklaert nach Rueckfrage" ist und
der Betreiber das dokumentiert hat.
