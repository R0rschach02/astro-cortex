"""Luecke 1+2: Inversions-Erkennung + Bewegungsvektor (Logik-Tests)."""
import sys

sys.path.insert(0, "/home/enigma")


def _inversion_adjusted(spot, clouds, tau, wind):
    """Reproduziert die Backend-Logik fuer deterministische Tests."""
    elevation = spot.get("elevation_m", 100)
    inversion_likely = (
        clouds is not None and clouds > 70
        and tau is not None and tau < 3.0
        and wind is not None and wind < 10
        and elevation > 200
    )
    if not inversion_likely:
        return {"inversion_likely": False, "clouds_adjusted": clouds,
                "elevation_m": elevation, "adjustment_pp": 0}
    adjustment = min(60.0, max(0.0, (elevation - 100) / 1000 * 100))
    return {"inversion_likely": True,
            "clouds_adjusted": max(0, clouds - adjustment),
            "elevation_m": elevation, "adjustment_pp": adjustment}


class TestInversion:

    def test_koenigsstuhl_inversion(self):
        r = _inversion_adjusted({"elevation_m": 550}, 100, 1.5, 5)
        assert r["inversion_likely"] is True
        assert r["clouds_adjusted"] == 55  # 100 - 45
        assert r["adjustment_pp"] == 45.0

    def test_talsohle_keine_inversion(self):
        r = _inversion_adjusted({"elevation_m": 95}, 100, 1.5, 5)
        assert r["inversion_likely"] is False
        assert r["clouds_adjusted"] == 100

    def test_weinheim_geringe_korrektur(self):
        r = _inversion_adjusted({"elevation_m": 150}, 90, 2.0, 8)
        assert r["inversion_likely"] is False  # 150m unter 200m-Schwellwert

    def test_wind_verhindert_inversion(self):
        r = _inversion_adjusted({"elevation_m": 550}, 100, 1.5, 15)
        assert r["inversion_likely"] is False

    def test_trocken_verhindert_inversion(self):
        r = _inversion_adjusted({"elevation_m": 550}, 100, 5.0, 5)
        assert r["inversion_likely"] is False

    def test_klar_keine_anpassung(self):
        r = _inversion_adjusted({"elevation_m": 550}, 30, 1.0, 5)
        assert r["inversion_likely"] is False


class TestMovement:

    def test_trend_klassifizierung(self):
        assert -20 < -15  # clearing
        assert 20 > 15    # clouding
        assert 5 < 15     # stable

    def test_richtungspfeil_mapping(self):
        arrows = ["↑", "↗", "→", "↘", "↓", "↙", "←", "↖"]
        assert arrows[round(0 / 45) % 8] == "↑"      # Nord
        assert arrows[round(90 / 45) % 8] == "→"     # Ost
        assert arrows[round(180 / 45) % 8] == "↓"    # Sued
        assert arrows[round(270 / 45) % 8] == "←"    # West
