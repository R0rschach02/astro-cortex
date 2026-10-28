"""v1.1.19-ops: ping_healthchecks /fail-Pfad + http_get_json-Haertung.
Der Fail-Ping muss die URL um /fail erweitern, die Meldung als POST-Body
mitschicken und NIE selbst werfen (Ueberwachungstoerung darf den Tick
nicht rot machen)."""
import sys
import urllib.request

sys.path.insert(0, "/home/enigma")

import astro_crawler as ac


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        class _R:
            def read(self): return b"ok"
        return _R()


def _install(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(urllib.request, "urlopen", rec)
    return rec


def test_success_ping_ohne_fail_suffix(monkeypatch):
    rec = _install(monkeypatch)
    ac.ping_healthchecks("https://hc-ping.com/uuid-1", "radar")
    assert len(rec.calls) == 1
    assert rec.calls[0].full_url == "https://hc-ping.com/uuid-1"
    assert rec.calls[0].data is None          # GET-artig ohne Body


def test_fail_ping_url_und_body(monkeypatch):
    rec = _install(monkeypatch)
    ac.ping_healthchecks("https://hc-ping.com/uuid-1", "radar",
                         msg="ValueError: kaputt", fail=True)
    assert rec.calls[0].full_url == "https://hc-ping.com/uuid-1/fail"
    assert rec.calls[0].data == b"ValueError: kaputt"   # Body -> Dashboard


def test_fail_ping_body_begrenzt(monkeypatch):
    rec = _install(monkeypatch)
    ac.ping_healthchecks("https://hc-ping.com/uuid-1", "radar",
                         msg="x" * 50000, fail=True)
    assert len(rec.calls[0].data) == 10000    # gekappt auf 10 kB


def test_ping_fehler_wird_geschluckt(monkeypatch):
    def _boom(req, timeout=None):
        raise OSError("netz weg")
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    ac.ping_healthchecks("https://hc-ping.com/uuid-1", "radar",
                         msg="whatever", fail=True)   # darf nicht werfen


def test_leere_url_inaktiv(monkeypatch):
    rec = _install(monkeypatch)
    ac.ping_healthchecks("", "radar")
    assert rec.calls == []


def test_http_get_json_loggt_endgueltigen_fehler(monkeypatch, caplog):
    def _boom(req, timeout=None):
        raise TimeoutError("read timeout")
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    try:
        ac.http_get_json("https://api.test/x", timeout=1, retries=2)
        raised = False
    except TimeoutError:
        raised = True
    assert raised, "Erschoepfung muss werfen"
    assert any("endgueltig fehlgeschlagen" in r.getMessage()
               for r in caplog.records
               if r.levelname == "ERROR")
