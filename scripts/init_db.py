#!/usr/bin/env python3
"""
Astro Cortex - Database initialization script.

Creates the SQLite database and applies schema.sql. Safe to run multiple
times (idempotent — uses CREATE TABLE IF NOT EXISTS).

Usage:
    python scripts/init_db.py [--db-path PATH]

Default DB path comes from settings (env var DB_PATH or /var/lib/astro-cortex/cortex.db).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import click

from app.config import settings


SCHEMA_FILE = Path(__file__).parent.parent / "app" / "db" / "schema.sql"


@click.command()
@click.option("--db-path", default=None, help="Override DB path from settings")
def main(db_path: str | None) -> None:
    path = Path(db_path) if db_path else Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not SCHEMA_FILE.exists():
        click.echo(f"ERROR: schema file not found: {SCHEMA_FILE}", err=True)
        sys.exit(1)

    click.echo(f"Initializing database at: {path}")
    schema = SCHEMA_FILE.read_text(encoding="utf-8")

    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(schema)
        conn.commit()
        # Verify
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        click.echo(f"Created tables: {', '.join(tables)}")
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        click.echo(f"Schema version: {version[0] if version else 'unknown'}")
    finally:
        conn.close()

    click.echo("Done.")


if __name__ == "__main__":
    main()
