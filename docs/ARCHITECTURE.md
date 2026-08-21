# Astro Cortex - Architecture

## Core principles

### 1. Determinism over LLMs at runtime

Every runtime decision in Astro Cortex is a fixed arithmetic comparison.
No LLM is consulted when a Go/No-Go rating is computed. LLMs are only used:

- **During development** (one-time cost) — ZCode GLM 5.3 generates new modules
- **For optional NL summaries** — Telegram message formatting, not the decision itself
- **For future anomaly analysis** — and even then, only as an advisor, not a decision maker

The rating engine (`app/engine/rating.py`) is a pure function. Same input →
same output, always. This makes it:
- Testable (see `tests/test_rating.py`)
- Auditable (every decision is fully explained by thresholds + inputs)
- Reproducible (a past rating can be re-computed and compared)

### 2. Append-only where it matters

Three tables are append-only and never modified after insert:
- `crawls` — observation snapshots
- `forecast_log` — predictions made at a point in time
- `forecast_verification` — auto-computed errors

State mutations (cooldown, session state) live in `state.json`, not in DB.
This separation keeps the DB queryable for historical analysis without
worrying about UPDATE races.

### 3. Source isolation with cascade fallback

Each data source is a self-contained module implementing the `Source` protocol.
Sources are unaware of each other. The `Cascade` orchestrator tries sources
in priority order per parameter and records which source provided each value
(provenance tracking in `sources_json`).

Adding a new source:
1. Create `app/sources/<name>.py` implementing `Source`
2. Add the source name to `PRIORITY` in `cascade.py` for relevant parameters
3. Wire it up in `crawl/heavy.py:build_cascade()`

No other code changes needed.

### 4. Secret hygiene

- `.env` is gitignored (verified in `.gitignore`)
- `.env.example` is the template (committed)
- SSH keys live in `~/.ssh/`, never in the repo
- Telegram token is passed via env var, never hardcoded
- No production secrets appear in tests

If a secret is ever accidentally committed:
1. `git log -p --all | grep <secret-fragment>` to verify it's in history
2. Use `git filter-repo` to purge the history
3. Rotate the secret (BotFather `/revoke`, generate new SSH key, etc.)
4. Commit a clean version and force-push

### 5. Service composition

Three systemd units:
- `astro-radar.timer` (5 min) → `astro-radar.service` (oneshot)
  - Reads latest crawl, computes current rating, sends alerts on transitions
  - Calls `milestone.run()` to trigger daily checks
- `astro-crawler.timer` (30 min) → `astro-crawler.service` (oneshot)
  - Fetches all sources, writes new crawl + rating + forecast_log rows
- `astro-app.service` (long-running)
  - FastAPI server, hosts PWA and REST API

Why oneshot for radar and crawler?
- Process crashes don't leave a half-running service
- systemd handles retries via `OnFailure=` if needed
- No state held between runs (forces clean design)

## Data flow

```
[5min radar tick]
    │
    ├─ Read latest crawl row from DB
    ├─ Compute current rating (pure function)
    ├─ Compare to state.json
    ├─ If transition: send Telegram alert
    ├─ Check wind escalation thresholds
    ├─ If milestone hour: run daily checks
    └─ Update state.json
         │
         └─ Daily milestone:
             ├─ Verify pending forecasts (forecast_log ↔ crawls)
             └─ Mark unverifiable entries after grace period

[30min crawler tick]
    │
    ├─ For each active location:
    │   ├─ cascade.fetch_all(location, now) [async, all sources concurrent]
    │   ├─ Normalize to canonical schema
    │   ├─ rate(observation, mode)
    │   ├─ Insert crawl row + rating row
    │   └─ Insert forecast_log rows for next 48h
    └─ Update PWA cache (latest-wins JSON)

[Long-running app]
    │
    ├─ GET /health → liveness probe
    ├─ GET /api/locations → list sites
    ├─ GET /api/locations/{id}/current → latest rating
    ├─ GET /api/locations/{id}/forecast → multi-day forecast
    └─ GET / → PWA shell (Leaflet map, Service Worker)
```

## Future extensions

### Multi-day forecast horizon

Current schema supports multi-day forecasts via `forecast_log.target_ts`.
The `Cascade.fetch_forecast()` method already accepts a target time. To
extend from 48h to 72h+:

1. Verify which sources support longer horizons (per the prompt:
   Open-Meteo: 7d, BrightSky: ~24h, ClearOutside: ~12h, Meteoblue seeing: ~3d)
2. Mark seeing/jetstream as None for days beyond Meteoblue's horizon
3. Surface `seeing_horizon` flag in API response so the PWA can render "n/a"

### ML calibration of forecasts

Once `forecast_verification` accumulates enough rows (weeks/months),
a calibration layer can:
- Compute error distributions per (source, parameter, lead_time)
- Bias-correct forecasts: `corrected = predicted - mean_error(lead_time)`
- Re-evaluate the rating engine against corrected values

This is explicitly future work — the data foundation must be solid first.

### Anomaly detection (extensibility)

The repo name and architecture are deliberately chosen to support future
anomaly detection work without restructuring. The rating engine, source
layer, and DB schema all carry forward. A future anomaly detector would:
- Subscribe to new crawl rows (DB trigger or polling)
- Run a model (VAE, isolation forest, etc.) on the observation
- Emit its own events to a new `anomaly_events` table
- Surface via a new API endpoint

No changes to existing modules needed.
