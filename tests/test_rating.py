"""
Astro Cortex - Tests for the rating engine.

The rating engine is a pure function, so tests are deterministic and fast.
Every threshold value here corresponds to a constant in app.config — if you
change a threshold, update both the constant and the test assertion.

Run: pytest tests/test_rating.py -v
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.engine.rating import (
    GoNoGo,
    NormalizedObservation,
    ObservingMode,
    rate,
    thresholds_for,
)


class TestThresholds:
    def test_dso_thresholds_match_config(self):
        t = thresholds_for(ObservingMode.DSO)
        assert t.cloud_max == settings.dso_cloud_max
        assert t.seeing_max == settings.dso_seeing_max
        assert t.dew_delta_min == settings.dso_dew_delta_min
        assert t.wind_max == settings.dso_wind_max
        assert t.jetstream_max == settings.dso_jetstream_max

    def test_planetary_thresholds_more_permissive(self):
        t_dso = thresholds_for(ObservingMode.DSO)
        t_plan = thresholds_for(ObservingMode.PLANETARY)
        assert t_plan.cloud_max > t_dso.cloud_max
        assert t_plan.seeing_max > t_dso.seeing_max
        assert t_plan.wind_max > t_dso.wind_max


class TestPerfectConditions:
    def test_perfect_dso_yields_go(self):
        obs = NormalizedObservation(
            cloud_cover_pct=0.0,
            wind_kmh=2.0,
            seeing_arcsec=0.8,
            jetstream_ms=5.0,
            dew_point_c=0.0,
            ambient_c=15.0,
        )
        result = rate(obs, ObservingMode.DSO)
        assert result.go_nogo == GoNoGo.GO
        assert result.score > 0.9
        assert result.score_cloud == 1.0
        assert result.score_seeing == 1.0


class TestHardFails:
    def test_cloud_over_threshold_yields_no_go(self):
        obs = NormalizedObservation(
            cloud_cover_pct=settings.dso_cloud_max + 10,
            wind_kmh=2.0,
            seeing_arcsec=0.8,
            jetstream_ms=5.0,
            dew_point_c=0.0,
            ambient_c=15.0,
        )
        result = rate(obs, ObservingMode.DSO)
        assert result.score_cloud == 0.0
        assert result.go_nogo == GoNoGo.NO_GO

    def test_wind_over_threshold_yields_no_go(self):
        obs = NormalizedObservation(
            cloud_cover_pct=0.0,
            wind_kmh=settings.dso_wind_max + 5,
            seeing_arcsec=0.8,
            jetstream_ms=5.0,
            dew_point_c=0.0,
            ambient_c=15.0,
        )
        result = rate(obs, ObservingMode.DSO)
        assert result.score_wind == 0.0
        assert result.go_nogo == GoNoGo.NO_GO

    def test_seeing_over_threshold_yields_no_go(self):
        obs = NormalizedObservation(
            cloud_cover_pct=0.0,
            wind_kmh=2.0,
            seeing_arcsec=settings.dso_seeing_max + 1.0,
            jetstream_ms=5.0,
            dew_point_c=0.0,
            ambient_c=15.0,
        )
        result = rate(obs, ObservingMode.DSO)
        assert result.score_seeing == 0.0
        assert result.go_nogo == GoNoGo.NO_GO


class TestMarginalConditions:
    def test_moderate_cloud_yields_marginal(self):
        obs = NormalizedObservation(
            cloud_cover_pct=settings.dso_cloud_max * 0.5,  # halfway to threshold
            wind_kmh=settings.dso_wind_max * 0.3,
            seeing_arcsec=settings.dso_seeing_max * 0.5,
            jetstream_ms=10.0,
            dew_point_c=0.0,
            ambient_c=8.0,
        )
        result = rate(obs, ObservingMode.DSO)
        assert 0.0 < result.score < 1.0
        # Should be marginal, not full go
        assert result.go_nogo in (GoNoGo.MARGINAL, GoNoGo.GO)


class TestPlanetaryMode:
    def test_higher_cloud_tolerance(self):
        """Planetary mode tolerates more cloud than DSO mode."""
        obs = NormalizedObservation(
            cloud_cover_pct=settings.dso_cloud_max + 5,
            wind_kmh=15.0,
            seeing_arcsec=3.0,
            jetstream_ms=40.0,
            dew_point_c=0.0,
            ambient_c=10.0,
        )
        dso_result = rate(obs, ObservingMode.DSO)
        plan_result = rate(obs, ObservingMode.PLANETARY)
        # DSO should fail on cloud; planetary should not
        assert dso_result.go_nogo == GoNoGo.NO_GO
        assert plan_result.go_nogo != GoNoGo.NO_GO

    def test_planetary_ignores_jetstream(self):
        obs = NormalizedObservation(
            cloud_cover_pct=0.0,
            wind_kmh=10.0,
            seeing_arcsec=2.0,
            jetstream_ms=999.0,  # absurd, should not matter
            dew_point_c=0.0,
            ambient_c=15.0,
        )
        result = rate(obs, ObservingMode.PLANETARY)
        assert result.score_jetstream == 1.0
        assert result.go_nogo == GoNoGo.GO


class TestMissingValues:
    def test_none_values_yield_neutral_penalty(self):
        """Missing values should not hard-fail the rating, but should penalize."""
        obs = NormalizedObservation(
            cloud_cover_pct=None,
            wind_kmh=None,
            seeing_arcsec=None,
            jetstream_ms=None,
            dew_point_c=None,
            ambient_c=None,
        )
        result = rate(obs, ObservingMode.DSO)
        # All components get 0.5 → composite is 0.5 → marginal
        assert result.score_cloud == 0.5
        assert result.score == pytest.approx(0.5, abs=0.01)
        assert result.go_nogo == GoNoGo.MARGINAL


class TestDewDelta:
    def test_dew_delta_computation(self):
        obs = NormalizedObservation(ambient_c=10.0, dew_point_c=3.0)
        assert obs.dew_delta() == 7.0

    def test_negative_dew_delta(self):
        """If ambient < dew point, dew is forming — rating should heavily penalize."""
        obs = NormalizedObservation(
            ambient_c=5.0,
            dew_point_c=8.0,
            cloud_cover_pct=0.0,
            wind_kmh=5.0,
            seeing_arcsec=1.0,
            jetstream_ms=10.0,
        )
        result = rate(obs, ObservingMode.DSO)
        assert result.score_dew == 0.0
        assert result.go_nogo == GoNoGo.NO_GO
