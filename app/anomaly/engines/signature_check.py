"""Visuelle Signaturen: typische Beobachtungsmerkmale der haeufigsten
natuerlichen/technischen Verwechslungskandidaten als Konstanten.
Vergleichsfunktionen sind bewusst grob (Spannen statt Punkte) - die
Zeugenangaben sind selbst unsicher; die Signatur schliesst Kandidaten AUS,
sie beweist sie nicht.
"""
from __future__ import annotations

# Jede Signatur: Dauer [s], Helligkeit (skala: 'dim','hell','sehr_hell',
# 'blendend'), Bewegung ('still','langsam','satellitengleich','schnell',
# 'plotzlich'), Farben (Menge), Form-Merkmale.
SIGNATURES = {
    "iss": {
        "duration_s": (120, 420), "brightness": "sehr_hell",
        "movement": "satellitengleich", "colors": {"weiss", "gelblich"},
        "features": ("gleichmaessig_hell", "kein_flackern", "langsamer_bogen"),
    },
    "iridium_flare": {
        "duration_s": (5, 30), "brightness": "blendend",
        "movement": "langsam", "colors": {"weiss"},
        "features": ("aufblitzen", "abklingen", "punktfoermig"),
    },
    "starlink_train": {
        "duration_s": (60, 600), "brightness": "hell",
        "movement": "satellitengleich", "colors": {"weiss"},
        "features": ("perlenkette", "gleichmaessige_reihe", "gleiche_helligkeit"),
    },
    "meteor": {
        "duration_s": (1, 10), "brightness": "hell",
        "movement": "plotzlich", "colors": {"weiss", "gruenlich", "gelblich"},
        "features": ("bogen", "schweif", "eine_richtung"),
    },
    "feuerball": {
        "duration_s": (3, 30), "brightness": "blendend",
        "movement": "plotzlich", "colors": {"orange", "rot", "gruenlich"},
        "features": ("bogen", "schweif", "fragmentierung"),
    },
    "radiosonde": {
        "duration_s": (600, 3600), "brightness": "dim",
        "movement": "langsam", "colors": {"weiss", "silber"},
        "features": ("kleiner_punkt", "treibt_mit_wind", "kein_regelmaessiger_bogen"),
    },
    "venus": {
        "duration_s": (600, 14400), "brightness": "blendend",
        "movement": "still", "colors": {"weiss", "gelblich"},
        "features": ("punktfoermig", "position_stabil", "daemmerung"),
    },
    "mond": {
        "duration_s": (600, 43200), "brightness": "blendend",
        "movement": "still", "colors": {"weiss", "gelblich", "orange"},
        "features": ("scheibe", "position_stabil"),
    },
    "laterne": {
        "duration_s": (300, 3600), "brightness": "hell",
        "movement": "langsam", "colors": {"orange", "rot"},
        "features": ("flammend", "treibt_mit_wind", "flackert"),
    },
}

_BRIGHT_ORDER = ["dim", "hell", "sehr_hell", "blendend"]


def _brightness_matches(observed: str, expected: str, tol: int = 1) -> bool:
    if observed not in _BRIGHT_ORDER or expected not in _BRIGHT_ORDER:
        return True   # unbekannte Angabe -> nicht ausschliessen
    return abs(_BRIGHT_ORDER.index(observed) - _BRIGHT_ORDER.index(expected)) <= tol


def _duration_matches(observed_s, expected: tuple) -> bool:
    if observed_s is None:
        return True
    lo, hi = expected
    return lo * 0.3 <= observed_s <= hi * 3.0


def signature_compatible(observed: dict, name: str) -> bool:
    """Schliesst der Beobachtungs-Textur die Signatur aus?
    observed: {'duration_s':..., 'brightness':..., 'movement':...,
               'color':..., 'features': [...] } - fehlende Angaben
    schliessen nie aus."""
    sig = SIGNATURES[name]
    if not _duration_matches(observed.get("duration_s"), sig["duration_s"]):
        return False
    if not _brightness_matches(observed.get("brightness", ""), sig["brightness"]):
        return False
    mv = observed.get("movement")
    if mv and sig["movement"] != mv and mv != "unbekannt":
        return False
    col = observed.get("color")
    if col and sig["colors"] and col not in sig["colors"] and col != "unbekannt":
        return False
    return True


def compatible_signatures(observed: dict) -> list[str]:
    return [n for n in SIGNATURES if signature_compatible(observed, n)]
