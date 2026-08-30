"""Rating-Logik: rate() fuer DSO/Planetarisch ueber die bekannten Grenzen."""


def test_storm_forciert_nogo_beide_profile(rep):
    for profile in ("dso", "planet"):
        r = rep(radar_status="Storm Alert", clouds_total=0, seeing=0.5)
        assert r.rate(profile)[0] == "NO-GO"


def test_rain_alert_forciert_nogo(rep):
    r = rep(radar_status="Rain Alert", clouds_total=0)
    assert r.rate("dso")[0] == "NO-GO"


def test_planet_seeing_grenze(rep):
    r = rep(radar_status="Clear", seeing=2.5, jetstream=10,
            clouds_total=10)
    assert r.rate("planet")[0] == "NO-GO"
    r2 = rep(radar_status="Clear", seeing=1.5, jetstream=10,
             clouds_total=10,
             planets={"saturn": {"max_alt": 40, "culm": "01:00",
                                 "window": "01:00-04:00"}})
    assert r2.rate("planet")[0] in ("GO", "MAYBE")


def test_planet_jetstream_grenze(rep):
    r = rep(radar_status="Clear", seeing=1.0, jetstream=35,
            clouds_total=10)
    assert r.rate("planet")[0] == "NO-GO"


def test_dso_top_werte_kein_nogo(rep):
    r = rep(radar_status="Clear", clouds_total=10, seeing=1.0,
            rain_prob=5, dew_risk="gering", moon_illum=10,
            wind_speed=5, dewpoint_spread=8)
    assert r.rate("dso")[0] in ("GO", "MAYBE")


def test_dso_wolken_ueber_schwellwert(rep):
    r = rep(radar_status="Clear", clouds_total=60, seeing=1.0)
    assert r.rate("dso")[0] == "NO-GO"


def test_radar_none_crashfrei(rep):
    # Regressionstest: frischer Live-Watch ohne DB-Zeile hatte None
    r = rep(clouds_total=10, seeing=1.0)
    assert r.rate("dso")[0] in ("GO", "MAYBE", "NO-GO")  # Hauptsache: kein Crash
