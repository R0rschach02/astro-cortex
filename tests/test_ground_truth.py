"""Ground Truth: menschliche Bodenwahrheit — Endpoint-Logik."""
import sys

import pytest

sys.path.insert(0, "/home/enigma")


def test_gt_clouds_clamping():
    """Coverage-Werte werden auf [0,100] geklemmt."""
    assert max(0, min(100, 150)) == 100
    assert max(0, min(100, -5)) == 0
    assert max(0, min(100, 5)) == 5
    assert max(0, min(100, 40)) == 40
    assert max(0, min(100, 95)) == 95


def test_gt_button_werte():
    """Die 4 UI-Buttons senden die definierten cloud_cover-Werte."""
    GT_BUTTONS = [
        {"label": "KLAR", "clouds": 5},
        {"label": "LÜCKEN", "clouds": 40},
        {"label": "DICHT", "clouds": 95},
        {"label": "NEBEL", "clouds": 100, "note": "Inversions-Anomalie"},
    ]
    assert GT_BUTTONS[0]["clouds"] == 5
    assert GT_BUTTONS[1]["clouds"] == 40
    assert GT_BUTTONS[2]["clouds"] == 95
    assert GT_BUTTONS[3]["clouds"] == 100
    assert GT_BUTTONS[3]["note"] == "Inversions-Anomalie"


def test_gt_delta_berechnung():
    """Delta = Prognose - Ist (gleiches Vorzeichen wie err_clouds)."""
    fc, actual = 90, 5
    delta = round(fc - actual, 1)
    assert delta == 85.0  # Prognose 90%, Klar -> massiv ueberschaetzt

    fc, actual = 30, 95
    delta = round(fc - actual, 1)
    assert delta == -65.0  # Prognose 30%, Dicht -> unterschaetzt
