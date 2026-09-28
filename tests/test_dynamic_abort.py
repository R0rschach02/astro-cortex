"""Backend-Autonomie: serverseitiger DYNAMIC-ABORT-Telegram-Alarm."""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/home/enigma")

import astro_crawler as ac


class _AbortCase:
    """Fenster laeuft (Start vor 30 min, Ende in 3 h), Stunde in 1 h
    kippt auf NO-GO (Wolken 95%)."""
    now = datetime.now()

    def fake_fc(self, path, *a, **k):
        class _R:
            def __enter__(self):
                return self
            def __exit__(self, *x):
                return False
            def read(self):
                import json
                now = datetime.now()
                night = (now - timedelta(hours=2)).date().isoformat()
                flip = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:00")
                return json.dumps({"Testort": {
                    "golden_windows": [{"night": night, "start":
                        (datetime.now() - timedelta(minutes=30)).strftime("%H:%M"),
                        "hours": 4}],
                    "series": [{"ts": flip, "clouds": 95, "dark": True}]}})
        return _R()

    def fake_state(self):
        return {"observation_mode": True}

    def fake_locations(self, *a):
        return [{"name": "Testort", "lat": 49.5, "lon": 8.6}]


def self_night(now):
    # Abenddatum des laufenden Fensters (Start vor 30 min)
    return (now - timedelta(hours=2)).date().isoformat()


def test_abort_alarm_feuert_einmal_und_dedup(monkeypatch):
    case = _AbortCase()
    sent = []
    saved = {}
    monkeypatch.setattr("builtins.open", lambda p, *a, **k:
                        case.fake_fc(p) if "forecast" in str(p)
                        else _NullFile())
    monkeypatch.setattr(ac, "load_state", case.fake_state)
    monkeypatch.setattr(ac, "save_state", lambda s: saved.update(s))
    monkeypatch.setattr(ac, "active_locations", lambda *a:
                        case.fake_locations())
    monkeypatch.setattr(ac, "load_watchlist", lambda: [])
    monkeypatch.setattr(ac, "weather_pushes_allowed", lambda s: True)
    monkeypatch.setattr(ac, "send_telegram", lambda msg: sent.append(msg))

    ac.check_dynamic_abort_alerts()
    assert len(sent) == 1, sent
    assert "DYNAMIC ABORT" in sent[0] and "Testort" in sent[0]
    assert "abort_alerted" in saved

    # Zweiter Aufruf (naechster Radar-Tick): Dedup greift
    sent.clear()
    monkeypatch.setattr(ac, "load_state",
                        lambda: {"observation_mode": True,
                                 "abort_alerted": saved["abort_alerted"]})
    ac.check_dynamic_abort_alerts()
    assert sent == [], "Alarm darf pro Standort|Nacht nur einmal feuern"


def test_abort_kein_alarm_ohne_laufendes_fenster(monkeypatch):
    case = _AbortCase()
    sent = []
    monkeypatch.setattr(ac, "load_state", lambda: {"observation_mode": True})
    monkeypatch.setattr(ac, "save_state", lambda s: None)
    monkeypatch.setattr(ac, "active_locations",
                        lambda *a: [{"name": "Anderer Ort", "lat": 0, "lon": 0}])
    monkeypatch.setattr(ac, "load_watchlist", lambda: [])
    monkeypatch.setattr(ac, "weather_pushes_allowed", lambda s: True)
    monkeypatch.setattr(ac, "send_telegram", lambda msg: sent.append(msg))
    # forecast.json leer -> kein Fenster, kein Alarm, kein Crash
    monkeypatch.setattr("builtins.open",
                        lambda p, *a, **k: (_ for _ in ()).throw(OSError()))
    ac.check_dynamic_abort_alerts()
    assert sent == []


class _NullFile:
    def __enter__(self):
        return self
    def __exit__(self, *x):
        return False
    def read(self):
        return "{}"
