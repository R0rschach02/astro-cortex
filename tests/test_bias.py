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
