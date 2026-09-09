"""Equipment-Inventar: Laedt ~/equipment.json (echte Ausruestung,
gitignored; Vorlage equipment.json.example im Repo - gleiches Muster wie
locations.json). Reine Lese-Logik, validiert Struktur.

Rollen-Konzept: Kameras mit role='guiding' (ASI120MC-S) sind reine
Guiding-Kameras - sie spielen in observable_objects() KEINE Rolle fuer
Grenzgroesse/Sichtbarkeit, sondern stehen nur fuer Vollstaendigkeit im
Inventar (spueter z.B. fuer eine Gear-Checklist).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

EQUIPMENT_PATH = os.path.expanduser("~/equipment.json")


@dataclass
class Equipment:
    aperture_mm: float
    telescope_key: str
    telescope_name: str
    camera_key: Optional[str] = None
    camera_name: Optional[str] = None
    camera_role: Optional[str] = None


def load_equipment(path: str = EQUIPMENT_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SystemExit(
            f"[Config] {path} fehlt - aus equipment.json.example anlegen")
    except (ValueError, OSError) as e:
        raise SystemExit(f"[Config] {path} unlesbar: {e}")
    for section in ("telescopes", "cameras", "eyepieces",
                    "barlow_lenses", "filters"):
        if not isinstance(data.get(section), dict):
            raise SystemExit(
                f"[Config] {path}: Sektion '{section}' fehlt oder ist "
                f"kein Objekt")
    return data


def build_equipment(inventory: dict, telescope_key: str,
                    camera_key: Optional[str] = None) -> Equipment:
    tel = (inventory["telescopes"] or {}).get(telescope_key)
    if not tel:
        raise KeyError(f"Teleskop '{telescope_key}' nicht im Inventar "
                       f"(vorhanden: {sorted(inventory['telescopes'])})")
    cam = (inventory["cameras"] or {}).get(camera_key) if camera_key else None
    return Equipment(
        aperture_mm=float(tel["aperture_mm"]),
        telescope_key=telescope_key,
        telescope_name=tel.get("name", telescope_key),
        camera_key=camera_key if cam else None,
        camera_name=cam.get("name") if cam else None,
        camera_role=cam.get("role") if cam else None,
    )
