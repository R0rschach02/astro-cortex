"""
Astro Cortex - Configuration.

Settings are loaded from environment variables (via .env file or shell env).
All thresholds live here as named constants — never hardcode numbers in
rating.py or other modules.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Telegram ---
    telegram_bot_token: str = Field(default="", description="Bot token from @BotFather")
    telegram_chat_id: str = Field(default="", description="Target chat for alerts")

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    tailscale_interface: str = "tailscale0"

    # --- Database ---
    db_path: Path = Path("/var/lib/astro-cortex/cortex.db")
    state_dir: Path = Path("/var/lib/astro-cortex/state")

    # --- Ephemeris ---
    skyfield_data_dir: Path = Path("/var/lib/astro-cortex/skyfield-data")

    # --- Crawling ---
    user_agent: str = "astro-cortex/0.1"
    heavy_crawl_interval_min: int = 30
    radar_interval_min: int = 5
    milestone_check_hour: int = 12

    # --- Locations ---
    active_locations: str = "home,bischofswiesen,wendelstein,arber"

    # --- Rating thresholds (DSO mode) ---
    dso_cloud_max: float = 25.0       # %
    dso_seeing_max: float = 2.5       # arcsec
    dso_dew_delta_min: float = 3.0    # °C
    dso_wind_max: float = 20.0        # km/h
    dso_jetstream_max: float = 30.0   # m/s

    # --- Rating thresholds (Planetary mode) ---
    planetary_cloud_max: float = 40.0
    planetary_seeing_max: float = 4.0
    planetary_dew_delta_min: float = 2.0
    planetary_wind_max: float = 30.0

    # --- Alert escalation ---
    wind_warning_kmh: float = 45.0
    wind_danger_kmh: float = 60.0
    cooldown_minutes: int = 15

    # --- Logging ---
    log_level: str = "INFO"

    # --- Verification ---
    forecast_lead_max_hours: float = 48.0
    forecast_tolerance_minutes: int = 20
    forecast_grace_period_hours: int = 24


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Convenience singleton
settings = get_settings()
