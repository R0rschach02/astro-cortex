"""
Astro Cortex - Database operations layer.

All SQLite access goes through this module. fcntl-based file locking
guarantees safe concurrent writes from radar (5min) and crawler (30min)
processes.

Design rules:
- Append-only tables (crawls, forecast_log, forecast_verification, alerts)
  are never UPDATEd.
- State mutations (cooldown, session state) live in state.json, not in DB.
- Every public function takes a sqlite3.Connection; connection lifecycle
  is managed by the caller via contextmanager `db_session()`.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import settings


LOCK_FILE = Path(settings.state_dir) / "cortex.db.lock"


@contextlib.contextmanager
def db_session(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with WAL mode and foreign keys enabled.

    Yields a Connection; commits on clean exit, rolls back on exception.
    """
    path = Path(db_path) if db_path else Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextlib.contextmanager
def file_lock() -> Iterator[None]:
    """Cross-process lock for write coordination.

    Use this around multi-statement write transactions that must be atomic
    across processes (radar + crawler + app might write concurrently).
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Crawls
# ---------------------------------------------------------------------------

def insert_crawl(
    conn: sqlite3.Connection,
    location_id: str,
    crawled_at: datetime,
    values: dict[str, float | None],
    sources: dict[str, str],
    raw_payload: dict | None = None,
) -> int:
    """Insert a normalized crawl snapshot. Returns the new row id.

    Raises sqlite3.IntegrityError if (location_id, crawled_at) already exists.
    """
    conn.execute(
        """
        INSERT INTO crawls (
            location_id, crawled_at,
            cloud_cover_pct, wind_kmh, wind_gust_kmh,
            seeing_arcsec, jetstream_ms,
            dew_point_c, ambient_c, humidity_pct, precipitation_mm,
            sources_json, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            location_id,
            crawled_at.astimezone(timezone.utc).isoformat(),
            values.get("cloud_cover_pct"),
            values.get("wind_kmh"),
            values.get("wind_gust_kmh"),
            values.get("seeing_arcsec"),
            values.get("jetstream_ms"),
            values.get("dew_point_c"),
            values.get("ambient_c"),
            values.get("humidity_pct"),
            values.get("precipitation_mm"),
            json.dumps(sources),
            json.dumps(raw_payload) if raw_payload else None,
        ),
    )
    row = conn.execute(
        "SELECT id FROM crawls WHERE location_id=? AND crawled_at=?",
        (location_id, crawled_at.astimezone(timezone.utc).isoformat()),
    ).fetchone()
    return row["id"]


def find_nearest_crawl(
    conn: sqlite3.Connection,
    location_id: str,
    target_ts: datetime,
    tolerance_minutes: int = 20,
) -> sqlite3.Row | None:
    """Find the crawl row closest to target_ts within ±tolerance_minutes.

    Used by forecast_verifier to match predictions to actuals.
    Returns None if no crawl falls within the tolerance window.
    """
    target_iso = target_ts.astimezone(timezone.utc).isoformat()
    rows = conn.execute(
        """
        SELECT *,
               ABS(strftime('%s', crawled_at) - strftime('%s', ?)) AS delta_s
        FROM crawls
        WHERE location_id = ?
          AND ABS(strftime('%s', crawled_at) - strftime('%s', ?)) <= ?
        ORDER BY delta_s ASC
        LIMIT 1
        """,
        (target_iso, location_id, target_iso, tolerance_minutes * 60),
    ).fetchall()
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Forecast log + verification
# ---------------------------------------------------------------------------

def insert_forecast_log(
    conn: sqlite3.Connection,
    location_id: str,
    created_at: datetime,
    target_ts: datetime,
    lead_time_hours: float,
    values: dict[str, float | None],
    sources: dict[str, str],
) -> int:
    """Insert a forecast prediction row. Append-only."""
    conn.execute(
        """
        INSERT INTO forecast_log (
            location_id, created_at, target_ts, lead_time_hours,
            cloud_cover_pct, wind_kmh, seeing_arcsec, dew_point_c,
            sources_json, verification_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            location_id,
            created_at.astimezone(timezone.utc).isoformat(),
            target_ts.astimezone(timezone.utc).isoformat(),
            lead_time_hours,
            values.get("cloud_cover_pct"),
            values.get("wind_kmh"),
            values.get("seeing_arcsec"),
            values.get("dew_point_c"),
            json.dumps(sources),
        ),
    )
    row = conn.execute(
        "SELECT id FROM forecast_log WHERE location_id=? AND created_at=? AND target_ts=?",
        (location_id, created_at.astimezone(timezone.utc).isoformat(),
         target_ts.astimezone(timezone.utc).isoformat()),
    ).fetchone()
    return row["id"]


def pending_verifications(
    conn: sqlite3.Connection,
    cutoff_lead_minutes: int = 45,
    max_age_hours: int = 24,
) -> list[sqlite3.Row]:
    """Return forecast_log rows awaiting verification.

    Criteria:
    - target_ts is at least cutoff_lead_minutes in the past (ensures the
      corresponding heavy crawl has run).
    - verification_status IS NULL.
    - target_ts is not older than max_age_hours (gives up after grace period;
      gets marked 'unverifiable' by the verifier job).
    """
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """
        SELECT * FROM forecast_log
        WHERE verification_status IS NULL
          AND strftime('%s', target_ts) <= strftime('%s', ?) - ?
          AND strftime('%s', target_ts) >= strftime('%s', ?) - ?
        ORDER BY target_ts ASC
        """,
        (now, cutoff_lead_minutes * 60, now, max_age_hours * 3600),
    ).fetchall()
    return list(rows)


def insert_verification(
    conn: sqlite3.Connection,
    forecast_log_id: int,
    actual_crawl_id: int | None,
    actuals: dict[str, float | None],
    errors: dict[str, float | None],
    tolerance_minutes: int,
) -> int:
    """Insert a verification result (success or unverifiable)."""
    conn.execute(
        """
        INSERT INTO forecast_verification (
            forecast_log_id, actual_crawl_id,
            actual_cloud_cover_pct, actual_wind_kmh, actual_seeing_arcsec, actual_dew_point_c,
            error_cloud_cover_pct, error_wind_kmh, error_seeing_arcsec, error_dew_point_c,
            match_tolerance_min
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            forecast_log_id, actual_crawl_id,
            actuals.get("cloud_cover_pct"), actuals.get("wind_kmh"),
            actuals.get("seeing_arcsec"), actuals.get("dew_point_c"),
            errors.get("cloud_cover_pct"), errors.get("wind_kmh"),
            errors.get("seeing_arcsec"), errors.get("dew_point_c"),
            tolerance_minutes,
        ),
    )
    conn.execute(
        "UPDATE forecast_log SET verification_status = ?, verified_at = ? WHERE id = ?",
        ("verified" if actual_crawl_id is not None else "unverifiable",
         datetime.now(timezone.utc).isoformat(), forecast_log_id),
    )
    row = conn.execute(
        "SELECT id FROM forecast_verification WHERE forecast_log_id = ?",
        (forecast_log_id,),
    ).fetchone()
    return row["id"]


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def upsert_location(
    conn: sqlite3.Connection,
    location_id: str,
    name: str,
    latitude: float,
    longitude: float,
    elevation_m: float | None = None,
    is_fixed: bool = False,
    notes: str | None = None,
) -> None:
    """Insert or update a location."""
    conn.execute(
        """
        INSERT INTO locations (id, name, latitude, longitude, elevation_m, is_fixed, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            latitude = excluded.latitude,
            longitude = excluded.longitude,
            elevation_m = excluded.elevation_m,
            notes = excluded.notes
        """,
        (location_id, name, latitude, longitude, elevation_m, int(is_fixed), notes),
    )


def get_active_locations(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM locations WHERE is_active = 1 ORDER BY id"))
