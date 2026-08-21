"""
Astro Cortex - Telegram bot interface.

Sends alerts on rating transitions, wind escalation, and daily summaries.
Bot token comes from env var TELEGRAM_BOT_TOKEN; never hardcoded.

This module is a STUB. Implementation TODOs:
- Implement send_message() to call Telegram sendMessage API
- Implement alert formatting (rating → human-readable text)
- Consider long-running listener for /rate, /forecast commands from user
"""

from __future__ import annotations

import structlog

from app.config import settings
from app.engine.rating import RatingResult

log = structlog.get_logger()


async def send_alert(rating: RatingResult, location_name: str) -> None:
    """Send a Go/No-Go alert to the configured Telegram chat.

    Respects cooldown (settings.cooldown_minutes): if an alert of the same
    type was sent for the same location within the cooldown window, skip.
    """
    if not settings.telegram_bot_token:
        log.warning("telegram_no_token_configured")
        return

    # TODO: implement
    # 1. Check cooldown state.json — skip if within cooldown window
    # 2. Format message (see format_alert below)
    # 3. POST to https://api.telegram.org/bot{token}/sendMessage
    # 4. On success: update state.json with this alert's timestamp
    log.warning("telegram_send_not_implemented", location=location_name)


def format_alert(rating: RatingResult, location_name: str) -> str:
    """Format a rating as a human-readable Telegram message."""
    emoji = {"go": "🟢", "marginal": "🟡", "no_go": "🔴"}[rating.go_nogo.value]
    return (
        f"{emoji} {location_name}\n"
        f"Mode: {rating.mode.value.upper()}\n"
        f"Rating: {rating.go_nogo.value.upper()} (score {rating.score:.2f})\n"
        f"  Cloud:    {rating.score_cloud:.2f}\n"
        f"  Seeing:   {rating.score_seeing:.2f}\n"
        f"  Dew:      {rating.score_dew:.2f}\n"
        f"  Wind:     {rating.score_wind:.2f}\n"
        f"  Jetstream: {rating.score_jetstream:.2f}\n"
    )


async def send_wind_warning(location_name: str, wind_kmh: float) -> None:
    """Send a wind escalation warning."""
    # TODO: implement
    log.warning("telegram_wind_warning_not_implemented", location=location_name, wind=wind_kmh)


async def send_wind_danger(location_name: str, wind_kmh: float) -> None:
    """Send a wind danger alert (60+ km/h)."""
    # TODO: implement
    log.warning("telegram_wind_danger_not_implemented", location=location_name, wind=wind_kmh)
