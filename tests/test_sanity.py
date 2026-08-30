"""data_sanity: alle fuenf Pruefklassen mit tmp-DB, 1x/Tag-Suppression."""
import json
import sqlite3
from datetime import datetime, timedelta


def _seed_error_ratio(ac, conn, now, bad=8, good=2):
    for i in range(bad + good):
        err = None if i < good else "ClearOutside: UnboundLocalError"
        conn.execute(
            "INSERT INTO crawls (ts, mode, location_name, clouds_total, errors)"
            " VALUES (?,?,?,?,?)",
            ((now - timedelta(hours=1, minutes=5 * i)).isoformat(timespec="minutes"),
             "heavy", "ErrOrt", 50, err))


def test_wertebereiche(ds, rep):
    r = rep(clouds_total=130, seeing=0.03)
    out = ds.run_sanity([r], "/nonexistent.db")   # DB-Teil faellt sanft flach
    assert any("clouds_total=130" in f for f in out["ranges"])
    assert any("seeing=0.03" in f for f in out["ranges"])


def test_stale_crawls(ds, isolated, tmp_path):
    ac = isolated
    ds.STATE_PATH = str(tmp_path / "sanity.json")
    conn = sqlite3.connect(ac.DB_PATH)
    now = datetime.now()
    for i in range(6):
        conn.execute("INSERT INTO crawls (ts, mode, location_name, "
                     "clouds_total, clouds_source) VALUES (?,?,?,?,?)",
                     ((now - timedelta(minutes=30 * i)).isoformat(timespec="minutes"),
                      "heavy", "Frost", 73, "open_meteo"))
    conn.commit()
    out = ds.check_stale_crawls(conn)
    assert len(out) == 1 and "Frost" in out[0] and "73" in out[0]


def test_error_ratio(ds, isolated, tmp_path):
    ac = isolated
    ds.STATE_PATH = str(tmp_path / "sanity.json")
    conn = sqlite3.connect(ac.DB_PATH)
    _seed_error_ratio(ac, conn, datetime.now(), bad=8, good=2)
    out = ds.check_error_ratio(conn)
    assert len(out) == 1 and "8/10" in out[0]


def test_cross_source_clouds(ds, isolated, tmp_path):
    ac = isolated
    ds.STATE_PATH = str(tmp_path / "sanity.json")
    conn = sqlite3.connect(ac.DB_PATH)
    now = datetime.now()
    ts = (now + timedelta(hours=3)).isoformat(timespec="minutes")
    for src, val in (("clearoutside", 10), ("open_meteo", 90)):
        conn.execute(
            "INSERT INTO forecast_log (created_at, target_ts, location_name,"
            " lead_hours, clouds_total, source_clouds) VALUES (?,?,?,?,?,?)",
            (now.isoformat(timespec="minutes"), ts, "Diff", 24, val, src))
    conn.commit()
    out = ds.check_cross_source_clouds(conn)
    assert len(out) == 1 and out[0][2] == 10 and out[0][3] == 90


def test_suppression_einmal_pro_tag(ds, isolated, tmp_path, caplog):
    ac = isolated
    ds.STATE_PATH = str(tmp_path / "sanity.json")
    conn = sqlite3.connect(ac.DB_PATH)
    _seed_error_ratio(ac, conn, datetime.now())
    conn.commit()
    conn.close()
    first = ds.check_error_ratio(sqlite3.connect(ac.DB_PATH))
    second = ds.check_error_ratio(sqlite3.connect(ac.DB_PATH))  # gleicher Tag
    assert len(first) == 1 and len(second) == 1   # Findings bleiben
    st = json.load(open(ds.STATE_PATH))
    assert st.get("error_ratio") == f"{datetime.now():%Y-%m-%d}"   # aber 1x geloggt
