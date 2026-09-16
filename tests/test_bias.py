"""Bias-Korrektur: Berechnung aus forecast_verification (exakter
Mittelwert, Mindest-Stichprobe je Bucket) und Anwendung im Endpoint
(nur Anzeigewerte, Rohwerte bleiben im Response)."""
import json
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, "/home/enigma")


def _seed(ac, conn, rows):
    """rows: [(lead, err_clouds, err_seeing)] -> forecast_log + verification."""
    now = datetime.now()
    for lead, ec, es in rows:
        cur = conn.execute(
            "INSERT INTO forecast_log (created_at, target_ts, location_name,"
            " lead_hours, clouds_total, seeing) VALUES (?,?,?,?,?,?)",
            ((now - timedelta(hours=lead + 1)).isoformat(timespec="minutes"),
             (now - timedelta(hours=1)).isoformat(timespec="minutes"),
             "T", lead, 50 + ec if ec is not None else None,
             1.5 + es if es is not None else None))
        conn.execute(
            "INSERT INTO forecast_verification (forecast_log_id, verified_at,"
            " matched, actual_clouds, actual_seeing, err_clouds, err_seeing)"
            " VALUES (?,?,1,50,1.5,?,?)",
            (cur.lastrowid, now.isoformat(timespec="seconds"), ec, es))
    conn.commit()


def test_bias_exakter_mittelwert_und_schwelle(isolated):
    ac = isolated
    conn = sqlite3.connect(ac.DB_PATH)
    # Bucket <=24h: 60 Zeilen Wolken-Fehler, bekannter Mittelwert
    #   40x +10 und 20x -8  -> mean = (400 - 160)/60 = +4.0
    #   Seeing: nur 10 Zeilen (unter Schwelle) -> None
    # Bucket >24h: 55 Zeilen Seeing-Fehler 3x +0.4, 52x +0.1
    #   -> mean = (1.2 + 5.2)/55 = 0.116363... -> round 2 = 0.12
    rows = ([(12, 10, None)] * 40 + [(12, -8, None)] * 20
            + [(12, None, 0.3)] * 10
            + [(30, None, 0.4)] * 3 + [(30, None, 0.1)] * 52)
    _seed(ac, conn, rows)
    conn.close()
    ac.recompute_bias_corrections()
    b = json.load(open(ac.BIAS_PATH))
    assert b["clouds"]["le24"]["bias"] == 4.0, b["clouds"]["le24"]
    assert b["clouds"]["le24"]["n"] == 60
    assert b["seeing"]["le24"]["bias"] is None          # n=10 < 50
    assert b["seeing"]["le24"]["n"] == 10
    assert b["seeing"]["gt24"]["bias"] == 0.12          # gerundet auf 2
    assert b["clouds"].get("gt24") is None              # keine Wolken->24h


def test_bias_endpoint_wendet_korrektur_an(forecast_env):
    import sys as _s
    be = _s.modules["backend_main"]
    # Bias injizieren: Wolken <=24h immer +8pp Uberschaetzung
    bias = {"computed_at": "now", "min_n": 50,
            "clouds": {"le24": {"bias": 8.0, "n": 900}},
            "seeing": {}}
    with open(be.ac.BIAS_PATH, "w") as f:
        json.dump(bias, f)
    # Serie: 1 nahe Stunde (lead<24, clouds 20 -> 12) + 1 ferne (bleibt)
    now = datetime.now()
    near = {"ts": (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:00"),
            "clouds": 20, "seeing": 1.5}
    far = {"ts": (now + timedelta(hours=40)).strftime("%Y-%m-%dT%H:00"),
           "clouds": 95, "seeing": 2.0}
    data = json.load(open(be.ac.FORECAST_PATH))
    data["Ellerstadt Ost"]["series"] = [near, far]
    json.dump(data, open(be.ac.FORECAST_PATH, "w"))

    r = forecast_env.get("/api/forecast", params={"id": "ellerstadt_east"})
    body = r.json()
    s = body["series"]
    assert s[0]["clouds"] == 12 and s[0]["clouds_raw"] == 20
    assert "seeing_raw" not in s[0]                      # kein Seeing-Bias
    assert s[1]["clouds"] == 95 and "clouds_raw" not in s[1]  # >24h: keine
    assert body["bias_applied"]["clouds_le24"] == {"bias": 8.0, "n": 900}


def test_bias_clamping_an_den_grenzen(forecast_env):
    import sys as _s
    be = _s.modules["backend_main"]
    bias = {"clouds": {"le24": {"bias": 30.0, "n": 100}}, "seeing": {}}
    with open(be.ac.BIAS_PATH, "w") as f:
        json.dump(bias, f)
    now = datetime.now()
    data = json.load(open(be.ac.FORECAST_PATH))
    data["Ellerstadt Ost"]["series"] = [
        {"ts": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:00"),
         "clouds": 10, "seeing": 1.0},   # 10-30 -> klemmt bei 0
        {"ts": (now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:00"),
         "clouds": 98, "seeing": 1.0}]   # 98-30 -> 68 (normal)
    json.dump(data, open(be.ac.FORECAST_PATH, "w"))
    body = forecast_env.get("/api/forecast",
                            params={"id": "ellerstadt_east"}).json()
    assert body["series"][0]["clouds"] == 0
    assert body["series"][1]["clouds"] == 68


# ---------- V1: /api/bias-stats ----------
def test_bias_stats_returns_current_values(isolated):
    import importlib.util as ilu, sys as _s, json as _j
    _s.path.insert(0, "/home/enigma/astro-app/backend")
    ac = isolated
    # Echte Struktur: aktiver + inaktiver Bucket
    _j.dump({"computed_at": "2026-09-14T22:00:00", "min_n": 50,
             "clouds": {"le24": {"bias": -8.9, "n": 880}},
             "seeing": {"le24": {"bias": None, "n": 10}}},
            open(ac.BIAS_PATH, "w"))
    spec = ilu.spec_from_file_location("be_bias", "/home/enigma/astro-app/backend/main.py")
    be = ilu.module_from_spec(spec); _s.modules["be_bias"] = be
    spec.loader.exec_module(be)
    import pytest as _pt
    from unittest.mock import patch as _patch
    # Backend-ac ist die LIVE-Instanz: BIAS_PATH dorthin spiegeln (nur Lesen)
    with _patch.object(be.ac, "BIAS_PATH", ac.BIAS_PATH):
        payload = be._bias_stats_payload()
    assert payload["computed_at"] == "2026-09-14T22:00:00"
    c = payload["buckets"]["clouds_le24h"]
    assert c["bias"] == -8.9 and c["sample_n"] == 880 and c["applied"] is True
    assert c["min_n_threshold"] == 50
    assert "display layer only" in payload["applied_to"]


def test_bias_stats_low_sample_size_no_correction(isolated):
    import importlib.util as ilu, sys as _s, json as _j
    from unittest.mock import patch as _patch
    ac = isolated
    _j.dump({"computed_at": "t", "min_n": 50,
             "clouds": {"le24": {"bias": None, "n": 10}},
             "seeing": {}}, open(ac.BIAS_PATH, "w"))
    spec = ilu.spec_from_file_location("be_bias2", "/home/enigma/astro-app/backend/main.py")
    be = ilu.module_from_spec(spec); _s.modules["be_bias2"] = be
    spec.loader.exec_module(be)
    with _patch.object(be.ac, "BIAS_PATH", ac.BIAS_PATH):
        payload = be._bias_stats_payload()
    assert payload["buckets"]["clouds_le24h"]["applied"] is False
    assert payload["buckets"]["clouds_le24h"]["bias"] is None


# ---------- V2: bias_history ----------
def test_bias_history_append_daily(isolated):
    """Zwei recomputes (verschiedene computed_at) -> zwei Zeilen je Bucket;
    nur AKTIVE Buckets werden historisiert."""
    import json as _j
    ac = isolated
    conn = __import__("sqlite3").connect(ac.DB_PATH)
    # 60 Zeilen clouds le24 (aktiv) + 10 seeing (inaktiv -> keine Zeile)
    rows = [(12, 10, None)] * 60 + [(12, None, 0.3)] * 10
    _seed(ac, conn, rows)
    conn.close()
    ac.recompute_bias_corrections()
    # gestriger Lauf simulieren: Zeile von heute auf gestern zurueckdatieren
    import sqlite3 as _sq0
    c0 = _sq0.connect(ac.DB_PATH)
    from datetime import datetime as _dt0, timedelta as _td0
    gestern = (_dt0.now() - _td0(days=1)).isoformat(timespec="seconds")
    c0.execute("UPDATE bias_history SET computed_at=? WHERE bucket='clouds_le24h'", (gestern,))
    c0.commit(); c0.close()
    st = _j.load(open(ac.STATE_PATH)); st["bias_recompute_date"] = ""
    _j.dump(st, open(ac.STATE_PATH, "w"))
    ac.recompute_bias_corrections()
    import sqlite3 as _sq
    conn = _sq.connect(ac.DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM bias_history").fetchone()[0]
    buckets = {r[0] for r in conn.execute("SELECT DISTINCT bucket FROM bias_history")}
    ver = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
    conn.close()
    assert n == 2          # 2 Laeufe x 1 aktiver Bucket (clouds_le24h)
    assert buckets == {"clouds_le24h"}
    assert ver == "3"      # v3: rolling bias_7d/n_7d-Spalten
    # Rolling-Fenster: alle 60 Zeilen liegen innerhalb 7 Tagen -> bias_7d
    # muss dem kumulativen Mittel (+4.0) entsprechen, n_7d=60
    conn = _sq.connect(ac.DB_PATH)
    row = conn.execute("SELECT bias, bias_7d, n_7d FROM bias_history "
                       "WHERE bucket='clouds_le24h' ORDER BY computed_at DESC").fetchone()
    assert row[0] == 10.0 and row[1] == 10.0 and row[2] == 60, row


def test_bias_history_endpoint_returns_last_n_days(backend_env, tmp_path):
    """Endpoint: nur Zeilen >= cutoff, absteigend sortiert."""
    import sqlite3 as _sq
    from datetime import datetime as _dt, timedelta as _td
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    conn = _sq.connect(backend_env.ac.DB_PATH)
    now = _dt.now()
    rows = [(now - _td(days=1)).isoformat(timespec="seconds"), "clouds_le24h", -9.0, 800],
    for d, b, bi, n in [
        ((now - _td(days=1)).isoformat(timespec="seconds"), "clouds_le24h", -9.0, 800),
        ((now - _td(days=10)).isoformat(timespec="seconds"), "clouds_le24h", -10.0, 700),
        ((now - _td(days=60)).isoformat(timespec="seconds"), "clouds_le24h", -12.0, 600)]:
        conn.execute("INSERT OR IGNORE INTO bias_history (computed_at, bucket,"
                     " bias, sample_n) VALUES (?,?,?,?)", (d, b, bi, n))
    conn.commit(); conn.close()
    with _patch.object(backend_env.ac, "DB_PATH", backend_env.ac.DB_PATH):
        client = TestClient(backend_env.app)
        r = client.get("/api/bias-history", params={"days": 30})
        body = r.json()
        assert r.status_code == 200 and len(body) == 2   # 60-Tage-Zeile raus
        assert body[0]["computed_at"] >= body[1]["computed_at"]


def test_bias_history_no_data_returns_empty_list(backend_env):
    from unittest.mock import patch as _patch
    from fastapi.testclient import TestClient
    with _patch.object(backend_env.ac, "DB_PATH", backend_env.ac.DB_PATH):
        client = TestClient(backend_env.app)
        r = client.get("/api/bias-history", params={"days": 30})
        assert r.status_code == 200 and r.json() == []


# ---------- /api/telegram-commands ----------
def test_api_telegram_commands(backend_env):
    from fastapi.testclient import TestClient
    client = TestClient(backend_env.app)
    r = client.get("/api/telegram-commands")
    assert r.status_code == 200
    body = r.json()
    assert body["bot_name"] == "@AstroCrawler007bot"
    assert len(body["commands"]) >= 12
    first = body["commands"][0]
    assert first["command"].startswith("/") and first["description"]


def test_telegram_commands_complete():
    """Sync: jeder im Dispatch behandelte Befehl hat einen Eintrag in
    TELEGRAM_COMMANDS (und umgekehrt) - UI zeigt nie Phantom-Befehle."""
    import importlib.util as ilu, sys as _s, re as _re
    spec = ilu.spec_from_file_location(
        "ac_cmds", "/home/enigma/.zcode/workspace/default/astro_crawler.py")
    m = ilu.module_from_spec(spec); _s.modules["ac_cmds"] = m
    spec.loader.exec_module(m)
    src = open("/home/enigma/.zcode/workspace/default/astro_crawler.py",
               encoding="utf-8").read()
    dispatched = set(_re.findall(r'cmd == "(/[a-z]+)"', src))
    documented = {c["command"] for c in m.TELEGRAM_COMMANDS}
    assert dispatched - {"/start"} == documented, \
        f"Dispatch ohne Doku: {dispatched - documented} | " \
        f"Doku ohne Dispatch: {documented - dispatched}"


# ---------- Zeitzonen-Regression: BrightSky-Einzelstunde (Lesson 17-Fall) ----------
def test_brightsky_hour_clouds_utc_timestamps(isolated):
    """BrightSky antwortet UTC-Timestamps; target ist lokal. Vor dem Fix
    warf der aware-naive-Vergleich still TypeError (seit 23.08. Wolken-
    Verify tot). Hier laeuft die ECHTE Funktion, nur der HTTP-Layer ist
    injiziert."""
    from datetime import datetime as _dt
    ac = isolated
    calls = []
    def fake_http(url, timeout=10, retries=2):
        calls.append(url)
        return {"weather": [
            {"timestamp": "2026-09-15T18:00:00+00:00", "cloud_cover": 20},  # 20:00 MESZ
            {"timestamp": "2026-09-15T17:00:00+00:00", "cloud_cover": 80}]}  # 19:00 MESZ
    ac.http_get_json = fake_http
    val = ac._brightsky_hour_clouds(49.46, 8.26,
                                    _dt(2026, 9, 15, 20, 0))
    assert val == 20, f"20:00 MESZ muss cloud_cover=20 treffen, got {val}"
    assert "date=2026-09-15T17" in calls[0] or "17%3A30" in calls[0], calls[0]
