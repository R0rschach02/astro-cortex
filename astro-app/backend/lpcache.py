#!/usr/bin/env python3
"""Lichtverschmutzungs-Tiles: Proxy mit permanentem Disk-Cache.

Primärquelle: David Lorenz' Light Pollution Atlas (NOAA-VIIRS, simulierte
Zenit-Helligkeit - dieselbe Datenklasse wie lightpollutionmap.info).
  https://djlorenz.github.io/astronomy/image_tiles/tiles{YYYY}/tile_{z}_{x}_{y}.png
Fallback: NASA GIBS VIIRS_Black_Marble (offiziell, key-frei, rohe Stadtlichter).

Jede Kachel wird genau EINMAL geladen und landet dauerhaft in
~/astro-app/tilecache/ - Lichtverschmutzung aendert sich jaerlich, nicht
taeglich. Im Feld muss das Tablet den externen Host nie erreichen.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

log = logging.getLogger("astro-app.lpcache")

CACHE_DIR = os.path.expanduser("~/astro-app/tilecache")

# --- Bortle-/Zonen-Palette (Fix 17.08.) ------------------------------------
# Die 16 Lorenz-Zonenfarben (0a..7b), direkt aus der Original-Legende
# (colorbar.png) abgetastet - Reihenfolge dunkel->hell. Anker laut Doku:
# Grenze Zone 3b/4a = 22.0 mag/arcsec² (+ natuerliche Himmelshelligkeit),
# jede Zone = Faktor 3, jede Subzone = Faktor sqrt(3).
LP_ZONES = [
    ((0, 0, 0), "0a"), ((34, 34, 34), "0b"), ((66, 66, 66), "1a"),
    ((20, 47, 114), "1b"), ((33, 84, 216), "2a"), ((15, 87, 20), "2b"),
    ((31, 161, 42), "3a"), ((110, 100, 30), "3b"), ((184, 166, 37), "4a"),
    ((191, 100, 30), "4b"), ((253, 150, 80), "5a"), ((251, 90, 73), "5b"),
    ((251, 153, 138), "6a"), ((160, 160, 160), "6b"), ((242, 242, 242), "7a"),
    ((255, 255, 255), "7b"),
]


def _zone_index_from_rgb(rgb):
    """Naechste Zonenfarbe nach euklidischer Distanz (0..15)."""
    r, g, b = rgb[:3]
    return min(range(16), key=lambda i:
               (LP_ZONES[i][0][0] - r) ** 2
               + (LP_ZONES[i][0][1] - g) ** 2
               + (LP_ZONES[i][0][2] - b) ** 2)


def _zone_mag(s: int) -> float:
    """Zenit-Helligkeit der Subzone s in mag/arcsec^2 (Naeherung ueber den
    dokumentierten Anker: 3b/4a-Grenze = 22.0, Faktor 3 je Zone)."""
    import math
    lpi = 3 ** ((s - 7) / 2.0)
    return round(22.0 - 2.5 * math.log10(1.0 + lpi), 1)


def bortle_at(lat: float, lon: float) -> dict:
    """Lichtverschmutzung am Standort: Pixel aus der z6-Lorenz-Kachel am
    Cache-Pfad lesen (ggf. einmalig herunterladen), Zone matchen und in
    mag/arcsec^2 + Bortle-Naeherung uebersetzen."""
    import math
    z = 6
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    la = math.radians(lat)
    y = int((1.0 - math.log(math.tan(la) + 1.0 / math.cos(la)) / math.pi)
            / 2.0 * n)
    if not (0 <= x < n and 0 <= y < n):
        raise HTTPException(400, "Koordinaten ausserhalb der Kachel")

    os.makedirs(CACHE_DIR, exist_ok=True)
    tile = os.path.join(CACHE_DIR, f"lp_{z}_{x}_{y}.png")
    if not (os.path.exists(tile) and os.path.getsize(tile) > 0):
        data = _fetch(LORENZ_TEMPLATE.format(year=LORENZ_YEAR, z=z, x=x, y=y))
        if data is None:
            data = _fetch(GIBS_TEMPLATE.format(z=z, x=x, y=y))
        if data is None:
            raise HTTPException(404, "LP-Kachel nicht verfuegbar")
        with open(tile, "wb") as f:
            f.write(data)

    from PIL import Image
    img = Image.open(tile).convert("RGB")
    fx = (lon + 180.0) / 360.0 * n - x
    fy = (1.0 - math.log(math.tan(la) + 1.0 / math.cos(la)) / math.pi) \
        / 2.0 * n - y
    px = min(img.size[0] - 1, int(fx * img.size[0]))
    py = min(img.size[1] - 1, int(fy * img.size[1]))
    rgb = img.getpixel((px, py))
    s = _zone_index_from_rgb(rgb)
    return {
        "zone": LP_ZONES[s][1], "zone_index": s, "rgb": list(rgb[:3]),
        "mag": _zone_mag(s),
        # Bortle-Naeherung (subjektive Skala, ohne Anspruch auf exakte
        # Entsprechung - Lorenz selbst betont: Zenit-Wert != Bortle)
        "bortle": max(1, min(9, 1 + round(s / 2))),
    }

# Lorenz-Atlas: maximal nativ verfuegbare Zoomstufe (Leaflet skaliert darueber
# hinaus hoch). tiles2025 = aktuellster Jahrgang; aeltere als Fallback.
LORENZ_YEAR = os.environ.get("LP_YEAR", "2025")
LORENZ_TEMPLATE = ("https://djlorenz.github.io/astronomy/image_tiles/"
                   "tiles{year}/tile_{z}_{x}_{y}.png")
LORENZ_MAX_ZOOM = 6  # verifiziert; hoeher zoomt Leaflet native-up

# NASA GIBS Black Marble als Zweitquelle (key-frei, WMTS im XYZ-Muster)
GIBS_TEMPLATE = ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
                 "VIIRS_Black_Marble/default/2016-01-01/"
                 "GoogleMapsCompatible_Level8/{z}/{y}/{x}.png")
GIBS_MAX_ZOOM = 8

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _fetch(url: str, timeout: int = 20) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return resp.read()
    except Exception:
        return None
    return None


def get_lp_tile(z: int, x: int, y: int) -> Response:
    """Tile aus Cache oder (einmalig) von der Quelle; 404 wenn beide versagen."""
    if not (0 <= z <= 12 and x >= 0 and y >= 0):
        raise HTTPException(400, "Kachel-Koordinaten ungueltig")

    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"lp_{z}_{x}_{y}.png")
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
        return FileResponse(cache_file, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=604800"})

    # Quelle 1: Lorenz-Atlas (nur bis zur nativen Zoomstufe)
    data = None
    if z <= LORENZ_MAX_ZOOM:
        data = _fetch(LORENZ_TEMPLATE.format(year=LORENZ_YEAR, z=z, x=x, y=y))
    # Quelle 2: GIBS Black Marble (oder Zoom-Up fuer Lorenz via Ueber-Kachel)
    if data is None and z <= GIBS_MAX_ZOOM:
        data = _fetch(GIBS_TEMPLATE.format(z=z, x=x, y=y))
    # Quelle 3: Lorenz-Kachel der Mutterstufe holen (Leaflet-Anteil reicht
    # fuer einen groben Overlay nicht - wir skalieren serverseitig nicht,
    # also lieber 404: Leaflet zeigt dann eben keine Kachel, kein Drama)
    if data is None:
        raise HTTPException(404, "Kachel bei keiner Quelle verfuegbar")

    with open(cache_file, "wb") as f:
        f.write(data)
    log.info("LP-Tile geladen & gecacht: z%s x%s y%s (%d B)", z, x, y, len(data))
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=604800"})
