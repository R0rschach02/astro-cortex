-- schema_anomaly.sql - Anomalie-/UAP-Sichtungskorrelation (Phase 1)
-- Quellenunabhaengig: kein Feld verweist auf eine konkrete externe Quelle;
-- Compliance je Quelle regelt docs/SOURCE_LEGAL_REVIEW.md (+ Template).
-- GEIPAN-Klassifikation (Feld geipan_classification) ist ein Oeffentlich
-- dokumentiertes, quellenneutrales Schema: A=erklaert, B=wahrscheinlich
-- erklaert, C=ungenuegende Daten, D=nicht identifiziert bei guten Daten.

CREATE TABLE IF NOT EXISTS anomaly_sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,             -- FK anomaly_sources
    external_key TEXT,                      -- ID im Quellsystem (dedupe)
    observed_at_utc TEXT NOT NULL,          -- ISO 8601 UTC
    lat REAL NOT NULL, lon REAL NOT NULL,
    azimuth REAL, altitude REAL,            -- Blickrichtung des Zeugen (Grad)
    duration_s REAL,
    shape TEXT, color TEXT, movement TEXT,  -- Zeugenbeschreibung, roh
    brightness TEXT,                        -- z.B. 'heller_als_venus'
    witness_notes TEXT,
    geipan_classification TEXT              -- 'A'|'B'|'C'|'D'|NULL(vorerst offen)
        CHECK (geipan_classification IN ('A','B','C','D') OR
               geipan_classification IS NULL),
    ingested_at TEXT NOT NULL,
    UNIQUE (source_id, external_key)
);
CREATE INDEX IF NOT EXISTS idx_asight_time ON anomaly_sightings(observed_at_utc);
CREATE INDEX IF NOT EXISTS idx_asight_geo ON anomaly_sightings(lat, lon);

CREATE TABLE IF NOT EXISTS anomaly_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sighting_id INTEGER NOT NULL REFERENCES anomaly_sightings(id),
    engine TEXT NOT NULL,                   -- 'satellite'|'meteor'|'radiosonde'|
                                            -- 'planet'|'moon'|'sun'|'signature'
    candidate TEXT NOT NULL,                -- z.B. 'ISS', 'perseiden', 'DWD-00Z'
    score REAL NOT NULL,                    -- 0..1, Regelsystem classifier_rules
    details_json TEXT,                      -- Engine-Ausgabedetails
    computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_acand_sighting ON anomaly_candidates(sighting_id);

CREATE TABLE IF NOT EXISTS anomaly_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,              -- 'geipan'|'enigma'|'nuforc'|...
    legal_status TEXT NOT NULL DEFAULT 'ungeklaert',
                                            -- 'freigegeben'|'geklaert_nach_rueckfrage'|'ungeklaert'
    legal_review_url TEXT,                  -- Verweis ins SOURCE_LEGAL_REVIEW
    last_ingest_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 0      -- Ingest nur bei dokumentierter Freigabe
);
