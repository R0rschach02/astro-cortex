"""
Astro Cortex - Rating Engine.

This is the heart of the system. It is a PURE FUNCTION — given a normalized
observation dict and a mode, it returns a deterministic Go/No-Go decision.

No I/O, no side effects, no LLMs, no randomness. Every threshold is a named
constant from app.config. Every test in tests/test_rating.py asserts specific
input/output pairs.

Why pure functions matter:
- Testable in isolation (no fixtures, no mocking)
- Reproducible (same input → same output, always)
- Auditable (the decision is fully explained by thresholds + inputs)
- Cheap to run (microseconds; called once per location per heavy crawl)

The rating algorithm:
1. For each component (cloud, seeing, dew, wind, jetstream), compute a
   per-component score in [0, 1] where 1 = perfect, 0 = threshold exceeded.
2. Composite score = weighted product (not sum) — a single hard fail
   zeroes the whole rating.
3. Go/No-Go decision:
   - score >= 0.7 → go
   - 0.4 <= score < 0.7 → marginal
   - score < 0.4 → no_go
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.config import settings


class ObservingMode(str, Enum):
    DSO = "dso"
    PLANETARY = "planetary"


class GoNoGo(str, Enum):
    GO = "go"
    NO_GO = "no_go"
    MARGINAL = "marginal"


class NormalizedObservation(BaseModel):
    """A normalized weather observation, ready for rating.

    All fields use canonical units:
    - cloud_cover_pct: percent (0-100)
    - wind_kmh / wind_gust_kmh: km/h
    - seeing_arcsec: arcseconds
    - jetstream_ms: m/s at 300 hPa
    - dew_point_c / ambient_c: °C
    - humidity_pct: percent
    - precipitation_mm: mm/h
    """

    cloud_cover_pct: float | None = None
    wind_kmh: float | None = None
    wind_gust_kmh: float | None = None
    seeing_arcsec: float | None = None
    jetstream_ms: float | None = None
    dew_point_c: float | None = None
    ambient_c: float | None = None
    humidity_pct: float | None = None
    precipitation_mm: float | None = None

    def dew_delta(self) -> float | None:
        """Temperature margin above dew point (higher = safer against dew)."""
        if self.ambient_c is None or self.dew_point_c is None:
            return None
        return self.ambient_c - self.dew_point_c


class Thresholds(BaseModel):
    """Threshold set for one observing mode. Snapshotted into ratings table."""

    cloud_max: float
    seeing_max: float
    dew_delta_min: float
    wind_max: float
    jetstream_max: float | None = None


@dataclass
class RatingResult:
    mode: ObservingMode
    go_nogo: GoNoGo
    score: float
    score_cloud: float
    score_seeing: float
    score_dew: float
    score_wind: float
    score_jetstream: float
    thresholds: Thresholds
    golden_windows: list[dict] = field(default_factory=list)


def thresholds_for(mode: ObservingMode) -> Thresholds:
    """Return the threshold set for a given mode."""
    if mode == ObservingMode.DSO:
        return Thresholds(
            cloud_max=settings.dso_cloud_max,
            seeing_max=settings.dso_seeing_max,
            dew_delta_min=settings.dso_dew_delta_min,
            wind_max=settings.dso_wind_max,
            jetstream_max=settings.dso_jetstream_max,
        )
    if mode == ObservingMode.PLANETARY:
        return Thresholds(
            cloud_max=settings.planetary_cloud_max,
            seeing_max=settings.planetary_seeing_max,
            dew_delta_min=settings.planetary_dew_delta_min,
            wind_max=settings.planetary_wind_max,
            jetstream_max=None,  # planetary mode tolerates jetstream
        )
    raise ValueError(f"Unknown mode: {mode}")


def _component_score(value: float | None, threshold: float, direction: Literal["below", "above"]) -> float:
    """Compute a per-component score in [0, 1].

    - direction='below': score = 1 - (value / threshold) clipped to [0, 1]
        value=0 → score=1 (perfect)
        value=threshold → score=0 (hard fail)
    - direction='above': score = (value - threshold) / threshold + 1 clipped to [0, 1]
        value=threshold → score=1 (perfect)
        value=0 → score=0 (hard fail)
    - None → 0.5 (unknown, neutral penalty)
    """
    if value is None:
        return 0.5
    if direction == "below":
        if threshold <= 0:
            return 1.0 if value == 0 else 0.0
        return max(0.0, min(1.0, 1.0 - (value / threshold)))
    elif direction == "above":
        if threshold <= 0:
            return 1.0
        # value=threshold → 1.0; value=0 → ~0; values above threshold capped at 1
        return max(0.0, min(1.0, value / threshold))
    raise ValueError(f"Unknown direction: {direction}")


def rate(observation: NormalizedObservation, mode: ObservingMode) -> RatingResult:
    """Compute the Go/No-Go rating for an observation under a given mode.

    Pure function. No side effects.
    """
    thresholds = thresholds_for(mode)

    score_cloud = _component_score(observation.cloud_cover_pct, thresholds.cloud_max, "below")
    score_seeing = _component_score(observation.seeing_arcsec, thresholds.seeing_max, "below")
    score_dew = _component_score(observation.dew_delta(), thresholds.dew_delta_min, "above")
    score_wind = _component_score(observation.wind_kmh, thresholds.wind_max, "below")

    if thresholds.jetstream_max is not None:
        score_jetstream = _component_score(observation.jetstream_ms, thresholds.jetstream_max, "below")
    else:
        score_jetstream = 1.0  # not applicable for this mode

    # Composite score via weighted product.
    # Weights reflect relative importance for the decision:
    # cloud and seeing are hard gates, dew and wind are softer, jetstream is
    # least important (mostly affects high-magnification planetary only).
    weights = {
        "cloud": 0.35,
        "seeing": 0.25,
        "dew": 0.15,
        "wind": 0.15,
        "jetstream": 0.10,
    }
    composite = (
        score_cloud ** weights["cloud"]
        * score_seeing ** weights["seeing"]
        * score_dew ** weights["dew"]
        * score_wind ** weights["wind"]
        * score_jetstream ** weights["jetstream"]
    )

    if composite >= 0.7:
        go_nogo = GoNoGo.GO
    elif composite >= 0.4:
        go_nogo = GoNoGo.MARGINAL
    else:
        go_nogo = GoNoGo.NO_GO

    return RatingResult(
        mode=mode,
        go_nogo=go_nogo,
        score=composite,
        score_cloud=score_cloud,
        score_seeing=score_seeing,
        score_dew=score_dew,
        score_wind=score_wind,
        score_jetstream=score_jetstream,
        thresholds=thresholds,
    )
