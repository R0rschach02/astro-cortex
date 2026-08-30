# app/anomaly - Anomalie-Klassifikations-Engine (Phase 1, Parallel-Track)

## Herkunft und Annahmen
Die strukturgebende Spec (mit Stub-Beschreibungen) existiert ausserhalb
dieses Repos und war hier nie eingecheckt (Bestandsaufnahme 2026-08-30:
kein app/-Verzeichnis, keine Stubs). Dieser Code wurde neu aufgebaut nach
der Freigabeliste "Was Claude/ZCode jetzt bauen duerfen"; wo die Spec
Details nicht nennt, gelten folgende dokumentierte Annahmen:

- **Drei Tabellen** = anomaly_sightings (Sichtung, zeugenseitig, inkl.
  geipan_classification als quellenneutrales oeffentliches Schema
  A/B/C/D), anomaly_candidates (Erklaerungs-Kandidaten je Sichtung mit
  Engine-Score), anomaly_sources (Quellen-Registry MIT Compliance-Status;
  Ingest nur bei enabled=1, das setzt dokumentierte Freigabe voraus).
- **Skyfield-Source** statt Stub: Sonne/Mond/Venus(/Mars/Jupiter/Saturn)
  ueber lokales de421.bsp, ISS ueber injizierbares TLE (kein stiller
  Netz-Fallback). Ground-Truth: JPL Horizons (Fixtures, Abweichung
  < 0.001 Grad) + physikalische ISS-Invarianten.

## Module
- db/schema_anomaly.sql - Schema (SQLite-Dialekt wie Haupt-DB)
- engines/satellite_eras.py - Existenzfenster von Sat-Typen (z.B. Iridium-
  Flares nur bis 2019, Starlink erst ab 2019)
- engines/meteor_showers.py - harte Peaks/Fenster + Radianten
- engines/radiosonde_schedule.py - DWD-Aufstiegsfenster 00/12 UTC
  (Lindenberg zusaetzlich 06/18), Stationen als Daten
- engines/signature_check.py - visuelle Signaturen als Konstanten,
  Ausschluss-Logik (grob, Zeugendaten-tolerant)
- engines/classifier_rules.py - Kandidaten-Ranking (Position doppelt
  gewichtet) + GEIPAN-Klassifikations-VORSCHLAG (A/B/C/D)
- sources/skyfield_source.py - echte Ephemeriden-/TLE-Berechnung

## Blockiert bis Compliance (siehe docs/SOURCE_LEGAL_REVIEW.md)
geipan.py, opensky.py, enigma.py, nuforc.py, ufo_hunters.py,
cross_reference.py, classifier-Lauflogik gegen echte Sichtungdaten,
ingest_history.py, Live-Alert-Integration. KEIN Modul dieser Liste
anfangen, bevor die jeweilige Quelle den dokumentierten Status
"freigegeben"/"geklaert_nach_rueckfrage" hat.

## Tests
tests/test_anomaly_engines.py (deterministische Regeln),
tests/test_anomaly_skyfield.py (Horizons-Ground-Truth, ISS-Invarianten),
tests/test_anomaly_properties.py (Hypothesis, optional per
importorskip). Die Fixtures liegen in tests/fixtures/ - das ISS-TLE hat
die im Dateinamen/Dokument vermerkte Epoche und dient Struktur- und
Invariantentests, nicht Bahnpraediktion ueber Wochen.
