"""DUAL-PROFILE-Fruehwarnung (v1.1.11): Der Crawler konsumiert
/api/notifications/upcoming (localhost) - dieselben LUNAR/DEEP-SKY-
Profile wie die App. Ein Push pro Event (Dedup type|location|night|
start), parallel Telegram + FCM, Backend-down = still."""
import sys
import urllib.request

sys.path.insert(0, "/home/enigma")

import astro_crawler as ac


class _FakeResp:
    def __init__(self, payload):
        import json as _json
        self._b = _json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _event(etype="DEEP_SKY_WINDOW", **over):
    ev = {"location": "Katzenbuckel (Odenwald)", "night": "2026-10-13",
          "start": "21:00", "end": "04:00", "hours_until_start": 30.0,
          "type": etype, "clouds_avg": 8, "seeing_avg": 1.4,
          "wind_max": 6, "moon_alt": -22.5, "moon_illum": 3,
          "bortle": 4, "moon_status": "Set",
          "conditions": ["Moon: Set", "Wolken \u00d8 8%", "Bortle 4",
                         "Seeing 1.4\u2033"]}
    ev.update(over)
    return ev


def _run(monkeypatch, events, state=None):
    teles, fcms, saved = [], [], {}
    monkeypatch.setattr(ac, "load_state",
                        lambda: state or {})
    monkeypatch.setattr(ac, "save_state", lambda s: saved.update(s))
    monkeypatch.setattr(ac, "send_telegram", lambda msg: teles.append(msg))
    monkeypatch.setattr(ac, "send_fcm_push",
                        lambda title, body, data=None: fcms.append(title))
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=10:
                        _FakeResp({"events": events}))
    ac.check_prime_window_push()
    return teles, fcms, saved


def test_deep_sky_push_feuert_formatiert(monkeypatch):
    teles, fcms, saved = _run(monkeypatch, [_event()])
    assert len(teles) == 1, teles
    msg = teles[0]
    assert "DEEP SKY WINDOW \u2014 Katzenbuckel (Odenwald)" in msg
    assert "21:00-04:00 Uhr" in msg and "13.10.2026" in msg
    assert "Start in ~30 h" in msg
    assert "Bortle 4" in msg
    # Moon-Doppler-Filter: eigene Moon-Zeile ja, conditions-Moon nein
    assert msg.count("Moon: Set") == 1, msg
    assert len(fcms) == 1 and "DEEP SKY WINDOW" in fcms[0]
    key = "DEEP_SKY_WINDOW|Katzenbuckel (Odenwald)|2026-10-13|21:00"
    assert key in saved.get("prime_pushed", {})


def test_lunar_push_format(monkeypatch):
    teles, fcms, _ = _run(monkeypatch, [_event(
        "LUNAR_WINDOW", moon_alt=35.2, moon_illum=78, hours_until_start=47.5,
        conditions=["Mond Alt 35\u00b0", "Illum 78%", "Wolken \u00d8 12%",
                    "Seeing 1.4\u2033"])])
    assert len(teles) == 1
    assert "LUNAR WINDOW \u2014 Katzenbuckel (Odenwald)" in teles[0]
    assert "Mond Alt 35\u00b0" in teles[0] and "Illum 78%" in teles[0]
    assert "Start in ~48 h" in teles[0]
    assert "LUNAR WINDOW" in fcms[0]


def test_dedup_ein_push_pro_window(monkeypatch):
    _, _, saved = _run(monkeypatch, [_event()])
    key = list(saved["prime_pushed"])[0]
    teles2, fcms2, _ = _run(
        monkeypatch, [_event()],
        state={"prime_pushed": {key: "2099-01-01T00:00"}})
    assert teles2 == [] and fcms2 == [], \
        "zweiter Lauf fuer dasselbe Fenster: kein Push"


def test_zwei_events_zwei_pushes(monkeypatch):
    teles, fcms, _ = _run(monkeypatch, [
        _event(), _event("LUNAR_WINDOW", moon_alt=35, moon_illum=78,
                         start="23:00")])
    assert len(teles) == 2 and len(fcms) == 2


def test_keine_events_still(monkeypatch):
    teles, fcms, _ = _run(monkeypatch, [])
    assert teles == [] and fcms == []


def test_backend_down_kein_crash(monkeypatch):
    def _boom(url, timeout=10):
        raise OSError("connection refused")
    monkeypatch.setattr(ac, "load_state", lambda: {})
    monkeypatch.setattr(ac, "save_state", lambda s: None)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    ac.check_prime_window_push()   # darf nicht werfen


def test_kurze_restzeit_banner(monkeypatch):
    teles, _, _ = _run(monkeypatch, [_event(hours_until_start=4.2)])
    assert "JETZT DRAN" in teles[0]
