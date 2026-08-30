"""Klassifikationsregeln: kombiniert die Engine-Ergebnisse (Satelliten-
Epochen, Meteorstroeme, Sonden-Zeitfenster, Signaturen, Himmelskoerper-
Positionen) zu einem Kandidaten-Ranking und einem GEIPAN-Klassifikations-
VORSCHLAG. Deterministisch, regelbasiert, ohne ML.

Regeln (bewusst simpel und nachvollziehbar):
- Ein Kandidat zaehlt nur, wenn ALLE verfuegbaren Engines ihn stuetzen
  (Epoche aktiv, Zeitfenster trifft, Signatur kompatibel, Objekt nach
 weisbarem Stand am Himmel).
- Score = Anteil der anwendbaren Engines, die den Kandidaten stuetzen;
  Positions-Engine (skyfield) wiegt doppelt.
- GEIPAN-Vorschlag:
    A  = ein Kandidat mit Score >= 0.9 und Positions-Treffer
    B  = bester Kandidat 0.6 <= Score < 0.9
    C  = Zeugendaten zu duenn (keine Richtung/Zeit unbestimmt)
    D  = gute Daten, aber kein Kandidat >= 0.6
"""
from __future__ import annotations

POS_WEIGHT = 2.0


def rank_candidates(candidates: dict) -> list[tuple[str, float]]:
    """candidates: {name: {'era': bool|None, 'time': bool|None,
    'signature': bool|None, 'position': bool|None}} -> Ranking."""
    ranked = []
    for name, checks in candidates.items():
        total, supported = 0.0, 0.0
        for kind, ok in checks.items():
            if ok is None:
                continue          # Engine nicht anwendbar -> neutral
            w = POS_WEIGHT if kind == "position" else 1.0
            total += w
            if ok:
                supported += w
        score = supported / total if total else 0.0
        ranked.append((name, round(score, 3)))
    ranked.sort(key=lambda x: (-x[1], x[0]))
    return ranked


def suggest_geipan(ranked: list[tuple[str, float]], data_quality: str
                   ) -> str:
    """data_quality: 'gut' (Zeit+Ort+Richtung belastbar) oder 'duenn'."""
    if data_quality != "gut":
        return "C"
    best_score = ranked[0][1] if ranked else 0.0
    if best_score >= 0.9:
        return "A"
    if best_score >= 0.6:
        return "B"
    return "D"
