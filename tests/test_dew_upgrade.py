"""v1.1.14 DEW-Entschaerkung: Optik ist geschuetzt (Omegon Dew Shield +
ZWO Heizmanschette) - Beschlag allein darf das Rating nicht mehr ruinieren.
Harte Grenze ist nur noch Bodennebel/Dunst (Spread < 0.5 K, Transparenz)."""
import sys

sys.path.insert(0, "/home/enigma")

import astro_crawler as ac


def _rep(spread, clouds=10, wind=5, seeing=1.5):
    r = ac.SiteReport(name="Test", lat=49.0, lon=8.0)
    r.clouds_total = clouds
    r.dewpoint_spread = spread
    r.wind_speed = wind
    r.seeing = seeing
    r.night_cloud_min = clouds
    r.compute_dew_risk()
    return r


def test_alter_fall_1_2k_jetzt_nur_mittel():
    # Vor v1.1.14: klar+windstill+<4K = "hoch" (hartes NO-GO). Jetzt:
    # Optik geschuetzt -> nur noch Vorlauf-Warnung.
    assert _rep(1.2).dew_risk == "mittel"


def test_frueher_hoch_nur_unter_0_5k():
    # Echte Bodennebel-Grenze: Luft kondensiert am Boden -> Transparenz tot
    assert _rep(0.3).dew_risk == "hoch"
    assert _rep(0.49).dew_risk == "hoch"
    assert _rep(0.5).dew_risk == "mittel"    # Grenze exakt bei 0.5 K


def test_mittlerer_bereich_gering():
    assert _rep(1.6).dew_risk == "gering"
    assert _rep(3.5).dew_risk == "gering"    # war vor v1.1.14 "mittel"
    assert _rep(5.9).dew_risk == "gering"    # war vor v1.1.14 "mittel"


def test_bodennebel_nogo_bleibt_hart():
    r = _rep(0.3)
    rating, _ = r.rate("dso")
    assert rating == "NO-GO"


def test_taunego_stunde_entschaerft():
    # _hour_score: tau 1.5 K war K.o. (tau_nogo 3.0) - jetzt durchgaengig
    ok, reasons = ac._hour_score(
        {"dark": True, "clouds": 10, "seeing": 1.2, "tau": 1.5,
         "jet": 10, "wind": 8, "rain": 5, "precip": 0.0}, "dso")
    assert ok, reasons
    ok2, _ = ac._hour_score(
        {"dark": True, "clouds": 10, "seeing": 1.2, "tau": 0.3,
         "jet": 10, "wind": 8, "rain": 5, "precip": 0.0}, "dso")
    assert not ok2, "Bodennebel <0.5 K muss Stunden-K.o. bleiben"


def test_tau_good_grund_ab_2k():
    ok, reasons = ac._hour_score(
        {"dark": True, "clouds": 10, "seeing": 1.2, "tau": 2.5,
         "jet": 10, "wind": 8, "rain": 5, "precip": 0.0}, "dso")
    assert ok and any(r.startswith("Tau") for r in reasons), reasons


def test_profilregeln_aktualisiert():
    assert ac.PROFILE_RULES["dso"]["tau_nogo"] == 0.5
    assert ac.PROFILE_RULES["dso"]["tau_good"] == 2.0
    assert ac.PROFILE_RULES["planet"]["tau_nogo"] == 0.5
