# SOURCE_LEGAL_REVIEW - Template je Datenquelle

Eine Kopie dieses Blocks je Quelle ausfuellen und in
docs/SOURCE_LEGAL_REVIEW.md verlinken. Erst wenn Status
"freigegeben" oder "geklaert_nach_rueckfrage" DOKUMENTIERT ist,
darf ein Ingest-Modul (enabled=1 in anomaly_sources) aktiv werden.

---

## Quelle: <NAME>

- **Status:** ungeklaert | geklaert_nach_rueckfrage | freigegeben
- **Geprueft am:** <YYYY-MM-DD>
- **Pruefmethode:** <z.B. "direkter Abruf Roh-HTML per curl",
  "Wayback-Volltext-Snapshot vom <Datum>", "E-Mail-Antwort vom <Datum>">
- **Gepruefte URLs:** <alle besuchten Seiten auflisten, inkl. 404s>
- **Exaktes Zitat (woertlich, mit URL):**
  > "<Zitat aus der Primaerquelle>"
- **Volltext-Suche nach:** <z.B. "scrap", "reuse", "API", "licence">
  -> <Trefferzahlen>
- **Fazit:** <ein Satz, keine Interpretation ueber das Zitat hinaus>
- **Naechster Schritt:** <wer fragt wen worauf an>

Regeln (siehe LESSONS.md Fall 11):
- Lizenzen nur aus der Primaerquelle zitieren; Sekundaerbehauptungen
  (Drittseiten, Spec-Revisionen) sind KEIN Beleg.
- Ohne explizite Freigabe: Status "ungeklaert" - nie "wahrscheinlich okay".
- robots.txt dokumentieren, aber nie als Lizenz werten.
