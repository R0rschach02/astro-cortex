# Astro Equipment Inventory

> Single Source of Truth für alle Astro-Hardware.
> Diese Datei ist die autoritative Quelle — wenn ein Modul (z.B. `equipment.json`)
> oder ein Prompt Equipment-Daten braucht, wird hieraus referenziert, nicht
> aus Chat-Verläufen oder Gedächtnis.

**Letzte Aktualisierung:** 2026-09-08
**Verifiziert durch:** Igor (Nutzer)
**Storage-Ort des Equipment:** Bei Marcel (Garten + Keller), nicht beim Nutzer

---

## Teleskope

| ID | Name | Typ | Öffnung (mm) | Brennweite (mm) | f/Verhältnis |
|----|------|-----|--------------|------------------|--------------|
| `newton_150` | Skywatcher Quattro 150P | Newton | 150 | 600 | f/4 |

**Mount:** EQ5 Pro SynScan (deutsche Montierung, SynScan-Handcontroller, GoTo-fähig)

---

## Kameras

| ID | Name | Pixelgröße (µm) | Sensor (mm) | Rolle | Anmerkung |
|----|------|------------------|-------------|-------|-----------|
| `asi662mc` | ZWO ASI662MC | 2.9 | 8.47 | Imaging | Hauptkamera, 130fps max, color |
| `canon_600d` | Canon EOS 600D | 4.3 | 22.3 (APS-C) | Imaging | DSLR, modifiziert? TBD |
| `asi120mc_s` | ZWO ASI120MC-S | — | — | Guiding | Nur für Guiding, nicht für Beobachtung/Imaging |

**Steuerung:** ASIAIR Mini (Mobile App, steuert Mount + Kameras)

---

## Okulare

| ID | Name | Brennweite (mm) | Barrel (Zoll) | Gesichtsfeld (°) |
|----|------|-----------------|---------------|-------------------|
| `swa_26mm` | Omegon SWA 26mm 70° | 26 | 2 | 70 |

---

## Barlow-Linsen

| ID | Name | Faktor visuell | Faktor fotografisch | Barrel (Zoll) |
|----|------|----------------|---------------------|---------------|
| `omegon_ed` | Omegon ED Barlow | 2.0 | 1.5 | 2 |

**Hinweis:** Eine Barlow ändert die effektive Brennweite und damit die Vergrößerung.
Für `limiting_magnitude()` relevant — Magnification-Korrektur nutzt effektive
Brennweite (Teleskop × Barlow-Faktor).

---

## Filter

| ID | Name | Typ | Barrel (Zoll) | Anwendung |
|----|------|-----|---------------|----------|
| `antlia_triband` | Antlia 2" Triband RGB Ultra | Narrowband (Triband) | 2 | DSO unter Lichtverschmutzung, zentraler DSO-Filter |
| `omegon_uhc` | Omegon Pro UHC 2" | Lichtverschmutzungsfilter | 2 | Stadtlicht-Beobachtung |
| `omegon_moon` | Omegon variable Polarising Mondfilter 2" | Mondfilter (polarisierend) | 2 | Mondbeobachtung, Helligkeitsreduktion |
| `svbony_uvir` | SVBONY 1.25" UV/IR Sperrfilter | UV/IR-Cut | 1.25 | Fotografie, schneidet UV/IR ab |

**Hinweis zu `limiting_magnitude()` für V1:**
Filter beeinflussen die Grenzgröße nicht linear wie eine Barlow. Ein Schmalband-
oder UHC-Filter macht breitspektrale Objekte tendenziell dunkler, verbessert aber
den Kontrast auf Emissionsnebel gegen Lichtverschmutzung. Die V1 des Hardware-
Filters bildet das **nicht** ab — das ist bewusst ausgeklammert, kein Bug.

---

## Beobachtungsstandorte (Bortle-Klassen, geschätzt)

| ID | Name | Bortle | Anmerkung |
|----|------|--------|-----------|
| `mannheim_neckarplatten` | Mannheim Neckarplatten | 7 | Vorstadt, hell |
| `hemsbach_sulzbach` | Hemsbach (Sulzbach) | 6 | Ländlich, moderat |
| `weinheim` | Weinheim | 6 | Ländlich, moderat |
| `spinelli_park` | Spinelli Park Mannheim | 7 | Innenstadtnah |
| `viernheim_heddesheimer` | Viernheim (Heddesheimer Str.) | 6 | Ländlich, moderat |
| `ellerstadt_east` | Ellerstadt Ost (Pfalz) | 4-5 | Pfalz, dunkel |
| `fussgoennheim_border` | Fussgoennheim Rand (Pfalz) | 4-5 | Pfalz, dunkel |
| `koenigsstuhl_heidelberg` | Königsstuhl Heidelberg | 3-4 | Höhenlage, dunkel |

**Verifizierung:** Bortle-Klassen sind Schätzungen. Verfeinerung via
https://www.lightpollutionmap.info möglich.

---

## Storage & Logistik

- **Haupt-Storage:** Bei Marcel (Garten + Keller)
- **Begründung:** Marcel hat einen Garten (Aufbau möglich) und einen Keller
  (sichere Verstauung). Der Nutzer hat keinen Führerschein, Marcel ist der
  Logistik-Knotenpunkt.
- **Transport:** Bollerwagen + Auto (Marcel oder Dritter)
- **Standorte mit Übernachtung:** Gönnheim (Bernd), eventuell Königsstuhl (TBD)

---

## Equipment-JSON-Struktur (für `equipment.json.example`)

Die folgende Struktur ist die V1-Schema-Definition für `equipment.json`
(gitignored, Beispiel committed als `equipment.json.example`):

```json
{
    "telescopes": {
        "newton_150": {
            "name": "Skywatcher Quattro 150P",
            "aperture_mm": 150,
            "focal_length_mm": 600,
            "type": "newton"
        }
    },
    "cameras": {
        "asi662mc": {
            "name": "ZWO ASI662MC",
            "pixel_size_um": 2.9,
            "sensor_width_mm": 8.47,
            "role": "imaging"
        },
        "canon_600d": {
            "name": "Canon 600D",
            "pixel_size_um": 4.3,
            "sensor_width_mm": 22.3,
            "role": "imaging"
        },
        "asi120mc_s": {
            "name": "ZWO ASI120MC-S",
            "role": "guiding"
        }
    },
    "eyepieces": {
        "swa_26mm": {
            "name": "Omegon SWA 26mm 70 Grad",
            "focal_length_mm": 26,
            "barrel_inch": 2
        }
    },
    "barlow_lenses": {
        "omegon_ed": {
            "name": "Omegon ED Barlow",
            "factor_visual": 2.0,
            "factor_photographic": 1.5,
            "barrel_inch": 2
        }
    },
    "filters": {
        "antlia_triband": {
            "name": "Antlia 2\" Triband RGB Ultra",
            "type": "narrowband",
            "barrel_inch": 2
        },
        "omegon_uhc": {
            "name": "Omegon Pro UHC 2\"",
            "type": "light_pollution",
            "barrel_inch": 2
        },
        "omegon_moon": {
            "name": "Omegon variable Polarising Mondfilter 2\"",
            "type": "moon",
            "barrel_inch": 2
        },
        "svbony_uvir": {
            "name": "SVBONY UV/IR Sperrfilter 1.25\"",
            "type": "uv_ir_cut",
            "barrel_inch": 1.25
        }
    }
}
```

---

## V1-Implementierungs-Notiz (für `limiting_magnitude()`)

In V1 berücksichtigt:
- Aperture (Öffnung)
- Bortle-Klasse (Lichtverschmutzung)
- Seeing (Atmosphäre)
- Magnification (via Teleskop-Brennweite + Barlow)

In V1 bewusst NICHT berücksichtigt:
- Filter-Effekte (nicht-linear, komplex)
- ASI120MC-S Guiding-Kamera (nur für Guiding, nicht für Beobachtung)
- ASIAIR Mini als Steuerungsgerät (kein optisches Element)

---

## Zukünftige Erweiterungen (nicht V1)

- Filter-Effekt-Modellierung (Kontraststeigerung vs. Helligkeitsverlust)
- Guiding-Setup (ASI120MC-S + OAG oder Guidescope)
- ASIAIR Mini Integration in PTF-Controller (falls relevant)
- Polar-Alignment-Status als Beobachtungsqualitäts-Indikator
