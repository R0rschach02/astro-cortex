"""Limiting Magnitude fuer visuelle Beobachtung - pure Funktion,
deterministisch, keine I/O.

Kalibrierung: Schaefer-inspirierte Basisformel
    m_lim = 2.7 + 5*log10(aperture_mm) + B(bortle) - extinction
mit B so kalibriert, dass die etablierten Richtwerte fuer 150 mm
zutreffen (Bortle 1 ~14.0, Bortle 5 ~12.3, Bortle 8 ~10.3) -
B(k) = 5.73*sqrt((9-k)/8) - 5.11. Seeing- und Vergroesserungs-
korrektur kommen additiv dazu.

Bewusst NICHT abgebildet (V1, keine Empfehlungs-Logik): Filter-
Einfluesse - ein Schmalband-/UHC-Filter macht breitbandige Objekte
dunkler, verbessert aber den Kontrast von Emissionsnebeln gegen
Lichtverschmutzung; das ist kein linearer magnitude-Shift.
"""
from __future__ import annotations

import math

_EXTINCTION = 0.2        # typische Gesamtextinktion im Zenit (mag)
_B_AMPLITUDE = 5.73
_B_OFFSET = -5.11


def _bortle_term(bortle_class: int) -> float:
    """Bortle-Beitrag: 1.0-faktor bei Klasse 1, abnehmend bis 0 bei 9."""
    k = min(9, max(1, int(bortle_class)))
    return _B_AMPLITUDE * math.sqrt((9 - k) / 8.0) + _B_OFFSET


def _seeing_penalty(seeing_arcsec: float | None) -> float:
    """seeing > 3'' kostet ~0.5 mag, > 5'' ~1.0 mag (fuer Grenzgroesse)."""
    if seeing_arcsec is None or seeing_arcsec <= 3.0:
        return 0.0
    return 1.0 if seeing_arcsec > 5.0 else 0.5


def _magnification_penalty(magnification: float | None,
                           aperture_mm: float) -> float:
    """Optimale Grenzgroessen-Vergroesserung ~ 2x Oeffnungsdurchmesser
    (in mm); relative Abweichung kostet bis 0.8 mag."""
    if magnification is None:
        return 0.0
    optimum = 2.0 * aperture_mm
    rel = abs(magnification - optimum) / optimum
    return min(0.8, rel * 0.8)


def limiting_magnitude(aperture_mm: float, bortle_class: int,
                       seeing_arcsec: float | None,
                       magnification: float | None = None) -> float:
    """Grenzgroesse fuer visuelle Beobachtung (Schaefers TLM, vereinfacht
    und an Bortle-Richtwerten fuer 150 mm kalibriert)."""
    if aperture_mm is None or aperture_mm <= 0:
        raise ValueError("aperture_mm muss positiv sein")
    m = (2.7 + 5.0 * math.log10(aperture_mm)
         + _bortle_term(bortle_class) - _EXTINCTION
         - _seeing_penalty(seeing_arcsec)
         - _magnification_penalty(magnification, aperture_mm))
    return round(m, 2)
