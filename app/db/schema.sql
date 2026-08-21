-- =====================================================================
-- Astro Cortex - Database Schema
-- Run via: python scripts/init_db.py
-- =====================================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Locations: observing sites (fixed + dynamic watchlist)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS locations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    elevation_m REAL,
    is_fixed    INTEGER NOT NULL DEFAULT 0,   -- 1 = permanent site, 0 = watchlist
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    notes       TEXT
);

-- ---------------------------------------------------------------------
-- crawls: every heavy-crawl snapshot per location (append-only)
-- One row per (location_id, crawled_at) pair.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crawls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id     TEXT NOT NULL REFERENCES locations(id),
    crawled_at      TEXT NOT NULL,             -- ISO8601 UTC
    -- Normalized weather values (see app/engine/normalizer.py for units)
    cloud_cover_pct REAL,
    wind_kmh        REAL,
    wind_gust_kmh   REAL,
    seeing_arcsec   REAL,
    jetstream_ms    REAL,
    dew_point_c     REAL,
    ambient_c       REAL,
    humidity_pct    REAL,
    precipitation_mm REAL,
    -- Source provenance (which source provided which value)
    sources_json    TEXT NOT NULL,            -- {"cloud": "dwd", "seeing": "meteoblue", ...}
    raw_json        TEXT,                     -- full raw payload for debugging (optional)
    UNIQUE(location_id, crawled_at)
);
CREATE INDEX IF NOT EXISTS idx_crawls_loc_time ON crawls(location_id, crawled_at DESC);

-- ---------------------------------------------------------------------
-- ratings: Go/No-Go decision per crawl
-- One rating per crawl row (1:1).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ratings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_id        INTEGER NOT NULL REFERENCES crawls(id),
    mode            TEXT NOT NULL,             -- 'dso' or 'planetary'
    go_nogo         TEXT NOT NULL,             -- 'go', 'no_go', 'marginal'
    score           REAL NOT NULL,            -- 0.0 .. 1.0 (composite)
    -- Per-component breakdown (for transparency/debugging)
    score_cloud     REAL,
    score_seeing    REAL,
    score_dew       REAL,
    score_wind      REAL,
    score_jetstream REAL,
    -- Thresholds applied (snapshot, so historical ratings stay interpretable)
    thresholds_json TEXT NOT NULL,
    -- Golden windows identified within this forecast window
    golden_windows_json TEXT,                  -- [{"start": ..., "end": ..., "score": ...}]
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(crawl_id)
);

-- ---------------------------------------------------------------------
-- forecast_log: every forecast prediction (append-only, lead <= 48h)
-- Populated by heavy crawl, one row per (location, target_ts, lead_time)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forecast_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id         TEXT NOT NULL REFERENCES locations(id),
    created_at          TEXT NOT NULL,         -- when forecast was made (UTC)
    target_ts           TEXT NOT NULL,         -- when the forecast is FOR (UTC)
    lead_time_hours     REAL NOT NULL,         -- (target_ts - created_at) in hours
    -- Predicted values (same schema as crawls)
    cloud_cover_pct     REAL,
    wind_kmh            REAL,
    seeing_arcsec       REAL,
    dew_point_c         REAL,
    -- Source provenance
    sources_json        TEXT NOT NULL,
    -- Verification state: NULL = pending, 'verified', 'unverifiable'
    verification_status TEXT,
    verified_at         TEXT,
    UNIQUE(location_id, created_at, target_ts)
);
CREATE INDEX IF NOT EXISTS idx_forecast_target ON forecast_log(target_ts);
CREATE INDEX IF NOT EXISTS idx_forecast_verify_pending
    ON forecast_log(verification_status) WHERE verification_status IS NULL;

-- ---------------------------------------------------------------------
-- forecast_verification: auto-computed errors (separate from forecast_log)
-- Populated by daily milestone check, never modified after insert.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forecast_verification (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_log_id     INTEGER NOT NULL REFERENCES forecast_log(id),
    actual_crawl_id     INTEGER REFERENCES crawls(id),  -- NULL if unverifiable
    -- Actual values (from crawls row nearest to target_ts)
    actual_cloud_cover_pct REAL,
    actual_wind_kmh        REAL,
    actual_seeing_arcsec   REAL,
    actual_dew_point_c     REAL,
    -- Errors (predicted - actual)
    error_cloud_cover_pct REAL,
    error_wind_kmh        REAL,
    error_seeing_arcsec   REAL,
    error_dew_point_c     REAL,
    -- Tolerance window: |actual.crawled_at - target_ts| in minutes
    match_tolerance_min  INTEGER NOT NULL,
    verified_at           TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(forecast_log_id)
);

-- ---------------------------------------------------------------------
-- alerts: state machine log for Telegram notifications
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id     TEXT NOT NULL REFERENCES locations(id),
    triggered_at    TEXT NOT NULL DEFAULT (datetime('now')),
    alert_type      TEXT NOT NULL,             -- 'go', 'no_go', 'wind_warning', 'wind_danger'
    severity         TEXT NOT NULL,             -- 'info', 'warning', 'danger'
    message          TEXT NOT NULL,
    telegram_message_id INTEGER,               -- NULL if not sent (e.g., cooldown)
    delivered        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_loc_time ON alerts(location_id, triggered_at DESC);

-- ---------------------------------------------------------------------
-- Schema version (for future migrations)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '1');
