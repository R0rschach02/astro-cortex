"""PRIME-WINDOW-Fruehwarnung: 2 Tage im Voraus, ueberdurchschnittlich,
immer (ungated), dedupliziert, mit Location/Datum/Uhrzeit."""
import sys
from datetime import date, timedelta

sys.path.insert(0, "/home/enigma")

import astro_crawler as ac


def _fc(premium):
    """Zwei Standorte: einer mit Premium-Fenster in 2 Naechten (Wolken
    8% / Seeing 1.4), der andere Durchschnitt (Wolken 40% / 2.5")."""
    night = (date.today() + timedelta(days=2)).isoformat()

    def series(cl, se):
        return [{"ts": f"{night}T22:00", "clouds": cl, "seeing": se,
                 "wind": 8, "tau": 6},
                {"ts": f"{night}T23:00", "clouds": cl, "seeing": se,
                 "wind": 8, "tau": 6}]

    return {
        "Premiumort": {"golden_windows": [{"night": night, "start": "22:00",
                                           "hours": 2}],
                       "series": series(8, 1.4) if premium else series(40, 2.5)},
        "Normalort": {"golden_windows": [{"night": night, "start": "23:00",
                                          "hours": 2}],
                      "series": series(55, 2.8)},
    }


def _run(monkeypatch, fc, state=None):
    sent = []
    saved = {}
    monkeypatch.setattr(ac, "load_state",
                        lambda: state or {"observation_mode": False})
    monkeypatch.setattr(ac, "save_state", lambda s: saved.update(s))
    monkeypatch.setattr(ac, "send_telegram", lambda msg: sent.append(msg))
    monkeypatch.setattr(ac, "FORECAST_PATH", "/tmp/fc_prime_test.json")
    import json as _json
    with open("/tmp/fc_prime_test.json", "w") as f:
        _json.dump(fc, f)
    ac.check_prime_window_push()
    return sent, saved


def test_prime_push_feuert_mit_ortsdatum_uhrzeit(monkeypatch):
    sent, saved = _run(monkeypatch, _fc(premium=True))
    assert len(sent) == 1, sent
    msg = sent[0]
    assert "Premiumort" in msg and "22:00-00:00 Uhr" in msg
    assert (date.today() + timedelta(days=2)).strftime("%d.%m.%Y") in msg
    assert "2 TAGE IM VORAUS" in msg
    assert "prime_pushed" in saved


def test_prime_push_dedup(monkeypatch):
    sent, saved = _run(monkeypatch, _fc(premium=True))
    key = list(saved["prime_pushed"])[0]
    sent2, _ = _run(monkeypatch, _fc(premium=True),
                    state={"prime_pushed": {key: "2099-01-01T00:00"}})
    assert sent2 == [], "nur ein Push pro Standort|Nacht"


def test_kein_push_ohne_ueberdurchschnitt(monkeypatch):
    sent, _ = _run(monkeypatch, _fc(premium=False))
    assert sent == [], "durchschnittliches Fenster -> kein Push"


def test_immer_ohne_observation_mode(monkeypatch):
    # Bewusst observation_mode=False (weather_pushes_allowed waere False):
    # der Prime-Push ist NICHT gegated
    sent, _ = _run(monkeypatch, _fc(premium=True),
                   state={"observation_mode": False})
    assert len(sent) == 1
