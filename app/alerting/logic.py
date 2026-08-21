"""
Astro Cortex - Alerting state machine.

Manages the lifecycle of alerts:
- Detect state transitions (No-Go → Go, Go → No-Go)
- Enforce cooldown between same-type alerts
- Escalate wind warnings (warning → danger)
- Track "session" state (currently in an observing session or not)

State lives in state.json (atomic file rewrite). This module is the single
source of truth for alerting decisions — the radar tick calls into it.

State machine (simplified):

    IDLE → (Go detected) → SESSION_ACTIVE → (No-Go detected) → COOLDOWN → IDLE
                                                                │
                                                                └─ (wind danger) → DANGER_ALERT

Cooldown prevents alert storms when conditions are oscillating near a threshold.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel

from app.config import settings
from app.engine.rating import GoNoGo, RatingResult

log = structlog.get_logger()


STATE_FILE = settings.state_dir / "alert_state.json"


class SessionState(str, Enum):
    IDLE = "idle"
    SESSION_ACTIVE = "session_active"
    COOLDOWN = "cooldown"
    DANGER_ALERT = "danger_alert"


class LocationState(BaseModel):
    session_state: SessionState = SessionState.IDLE
    last_go_nogo: GoNoGo | None = None
    last_alert_at: datetime | None = None
    last_alert_type: str | None = None
    wind_warning_sent: bool = False
    wind_danger_sent: bool = False


def load_state() -> dict[str, LocationState]:
    """Load alert state from disk. Returns empty dict if file missing."""
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text())
        return {k: LocationState(**v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError) as e:
        log.warning("alert_state_corrupt", error=str(e))
        return {}


def save_state(state: dict[str, LocationState]) -> None:
    """Atomically save alert state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(
            {k: v.model_dump(mode="json") for k, v in state.items()},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    tmp.rename(STATE_FILE)


def should_alert(
    state: LocationState,
    rating: RatingResult,
    wind_kmh: float | None = None,
) -> tuple[bool, str | None]:
    """Decide whether an alert should be sent based on current state and rating.

    Returns (should_send, alert_type). alert_type is one of:
    - 'go' — transition to Go
    - 'no_go' — transition to No-Go
    - 'wind_warning' — wind crossed 45 km/h
    - 'wind_danger' — wind crossed 60 km/h
    - None — no alert needed
    """
    now = datetime.now(timezone.utc)
    cooldown = timedelta(minutes=settings.cooldown_minutes)

    # In cooldown: suppress all alerts except wind danger
    if state.session_state == SessionState.COOLDOWN:
        if wind_kmh and wind_kmh >= settings.wind_danger_kmh and not state.wind_danger_sent:
            return True, "wind_danger"
        return False, None

    # State transition detection
    if state.last_go_nogo != rating.go_nogo:
        if state.last_alert_at and now - state.last_alert_at < cooldown:
            return False, None  # too soon after last alert
        return True, rating.go_nogo.value

    # Wind escalation (within an active session)
    if wind_kmh is not None:
        if wind_kmh >= settings.wind_danger_kmh and not state.wind_danger_sent:
            return True, "wind_danger"
        if wind_kmh >= settings.wind_warning_kmh and not state.wind_warning_sent:
            return True, "wind_warning"

    return False, None


def update_state(
    state: LocationState,
    rating: RatingResult,
    alert_sent: str | None,
    wind_kmh: float | None = None,
) -> None:
    """Update state after processing a rating. Mutates state in-place."""
    state.last_go_nogo = rating.go_nogo

    if alert_sent == "go":
        state.session_state = SessionState.SESSION_ACTIVE
        state.last_alert_at = datetime.now(timezone.utc)
        state.last_alert_type = "go"
    elif alert_sent == "no_go":
        state.session_state = SessionState.COOLDOWN
        state.last_alert_at = datetime.now(timezone.utc)
        state.last_alert_type = "no_go"
    elif alert_sent == "wind_warning":
        state.wind_warning_sent = True
        state.last_alert_at = datetime.now(timezone.utc)
    elif alert_sent == "wind_danger":
        state.wind_danger_sent = True
        state.session_state = SessionState.DANGER_ALERT
        state.last_alert_at = datetime.now(timezone.utc)

    # Reset wind flags when wind drops below warning threshold
    if wind_kmh is not None and wind_kmh < settings.wind_warning_kmh:
        state.wind_warning_sent = False
        state.wind_danger_sent = False
