# Astro Cortex

> Autonomous Go/No-Go decision system for astrophotography deployments.
> Multi-source weather aggregation, real-time rating engine, PWA + Telegram bot interface.

**GitHub Description (≤ 350 chars, paste into repo "About" field):**

```
Autonomous Go/No-Go decision system for astrophotography. Multi-source weather aggregation (DWD, BrightSky, Open-Meteo), seeing models, Skyfield ephemeris. Real-time rating engine with DSO/planetary modes. PWA + Telegram bot, multi-location, automated forecast verification. Designed for extensibility toward anomaly detection.
```

---

## What this is

Astro Cortex is a 24/7 decision engine that tells you, for any given observing site and time, whether conditions are good enough to deploy telescope equipment. It aggregates data from multiple meteorological and astronomical sources, applies a deterministic rating engine, and surfaces the result via a PWA and a Telegram bot.

The system runs on a Linux desktop and is composed of three systemd-managed services:
- **astro-radar** — 5-minute tick: lightweight status pulse, location rotation, alert escalation
- **astro-crawler** — 30-minute heavy crawl: full source aggregation, rating computation, forecast logging
- **astro-app** — long-running FastAPI server hosting the PWA and API endpoints

## Why this exists

A typical astrophotography session involves 30+ minutes of setup and teardown. Deploying under bad conditions wastes that effort and risks equipment. Astro Cortex answers one question — *"Should I go out tonight, and to which location?"* — by combining forecasts across sources instead of trusting any single one.

## Architecture

```
            ┌─────────────────────────────────────────┐
            │            Source Layer                 │
            │  DWD  BrightSky  ClearOutside          │
            │  Meteoblue  Open-Meteo  Skyfield        │
            └──────────────────┬──────────────────────┘
                               │ normalized
            ┌──────────────────▼──────────────────────┐
            │         Normalizer & Cascade            │
            │  (unit conversion, fallback priority)   │
            └──────────────────┬──────────────────────┘
                               │ unified schema
            ┌──────────────────▼──────────────────────┐
            │           Rating Engine                  │
            │  DSO mode / Planetary mode              │
            │  dew / cloud / seeing / wind / jetstream │
            └──────────────────┬──────────────────────┘
                               │ Go/No-Go + score
            ┌──────────────────▼──────────────────────┐
            │     Alerting + Persistence              │
            │  Telegram bot, SQLite, forecast_log     │
            └──────────────────┬──────────────────────┘
                               │
            ┌──────────────────▼──────────────────────┐
            │      PWA + REST API (FastAPI)           │
            │  Leaflet map, Service Worker, Tailscale  │
            └─────────────────────────────────────────┘
```

## Tech stack

- **Python 3.11+** — primary language
- **FastAPI** — REST API + PWA static hosting
- **Playwright / Chromium** — stealth crawling for JS-heavy sources
- **Skyfield** — local ephemeris computation (de421.bsp, no API calls)
- **SQLite + WAL** — single-file database, fcntl-locked writes
- **Leaflet** — PWA map with offline tile cache
- **python-telegram-bot** — bot interface
- **systemd** — service orchestration (timers + services)
- **Tailscale** — secure remote access to PWA

## Project layout

```
astro-cortex/
├── app/
│   ├── main.py                  # FastAPI entry point
│   ├── config.py                # Settings (env-driven)
│   ├── db/
│   │   ├── schema.sql           # All table definitions
│   │   └── operations.py        # CRUD layer (fcntl-locked)
│   ├── sources/                 # One module per data source
│   │   ├── base.py              # Abstract Source interface
│   │   ├── dwd.py
│   │   ├── brightsky.py
│   │   ├── clearoutside.py
│   │   ├── meteoblue.py
│   │   ├── open_meteo.py
│   │   ├── skyfield_local.py    # Local ephemeris (no network)
│   │   └── cascade.py           # Fallback priority per parameter
│   ├── engine/
│   │   ├── rating.py            # Go/No-Go scoring (deterministic)
│   │   ├── normalizer.py        # Unit & schema normalization
│   │   ├── time_calc.py         # Sunset/twilight/golden windows
│   │   ├── forecast.py           # Multi-day forecast logic
│   │   └── verifier.py          # Auto verification (forecast vs actuals)
│   ├── alerting/
│   │   ├── telegram_bot.py
│   │   └── logic.py              # State machine, cooldown, escalation
│   ├── crawl/
│   │   ├── radar.py             # 5-min tick
│   │   ├── heavy.py             # 30-min heavy crawl
│   │   └── milestone.py         # Daily checks (forecast verification, etc.)
│   └── pwa/
│       └── static/              # PWA assets (HTML, JS, Service Worker)
├── systemd/                     # systemd unit files
├── scripts/
│   ├── init_db.py               # Initialize SQLite schema
│   └── setup_playwright.py      # Install Chromium for stealth crawling
├── tests/
│   └── test_rating.py           # Deterministic tests for rating engine
├── docs/
│   └── ARCHITECTURE.md
├── .gitignore                   # Protects secrets (DO NOT bypass)
├── .env.example                 # Template for environment config
├── pyproject.toml
└── requirements.txt
```

## Quickstart

```bash
# 1. Clone
git clone git@github.com:<your-username>/astro-cortex.git
cd astro-cortex

# 2. Create virtualenv
python -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 4. Configure environment
cp .env.example .env
# Edit .env — fill in Telegram token, default locations, etc.

# 5. Initialize database
python scripts/init_db.py

# 6. Install systemd services
sudo cp systemd/astro-*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now astro-radar.timer astro-crawler.timer astro-app.service

# 7. Verify
curl http://localhost:8000/health
```

## Design principles

1. **Determinism over LLMs at runtime.** Every rating decision is a fixed arithmetic comparison against thresholds. No LLM is consulted at runtime — they are only used during code generation (one-time cost) and for natural-language summaries (optional, non-critical path).
2. **Append-only where it matters.** `forecast_log`, `crawls`, and `forecast_verification` are append-only. State changes live in `state.json` with explicit transitions.
3. **Graceful degradation.** Every parameter has a fallback cascade — if Meteoblue seeing is unreachable, Open-Meteo's model is used with a confidence penalty.
4. **Secret hygiene.** `.env` is gitignored. The repo contains `.env.example` as a template. SSH keys live outside the repo. Telegram tokens are passed via env, never hardcoded.
5. **Testable in isolation.** The rating engine accepts a normalized observation dict and returns a rating — pure function, no I/O. Every threshold is a named constant in `config.py`.

## License

MIT — see [LICENSE](LICENSE).

## Status

Active development. Architectural decisions are documented in `docs/`. The system is operational on a single Ubuntu host; multi-host deployment is not yet a goal.
