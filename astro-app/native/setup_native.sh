#!/usr/bin/env bash
# Phase-2-Setup: Capacitor + Android einmalig auf einer Node-Maschine.
# Voraussetzung: node >= 18, npm, Android Studio (SDK 34+, JAVA 17).
# Ausfuehren IN diesem Verzeichnis (astro-app/native/).
set -euo pipefail
# Umgebungs-Defaults (nvm-Java/SDK liegen im Home, kein sudo noetig)
export JAVA_HOME="${JAVA_HOME:-$HOME/jdk}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/android-sdk}"
export PATH="$HOME/.nvm/versions/node/$(ls $HOME/.nvm/versions/node | tail -1)/bin:$PATH"

[ -f package.json ] || npm init -y >/dev/null
npm install @capacitor/core @capacitor/cli
# capacitor.config.ts liegt bereits bei (webDir ../frontend) - nicht
# von cap init ueberschreiben, nur erzeugen falls entfernt wurde
[ -f capacitor.config.ts ] || npx cap init "Astro Cortex" "de.astrocortex.app" --web-dir=../frontend
npm install @capacitor/android
npx cap add android
npm install @capacitor/geolocation
npx cap sync android

# Manifest-Patch ALS LETZTER SCHRITT (cap sync ueberschreibt ihn sonst)
MANIFEST="android/app/src/main/AndroidManifest.xml"
if [ -f "$MANIFEST" ]; then
  # Minimal-Injection: nur Foreground-GPS, KEIN Background/Wake-Lock.
  python3 - "$MANIFEST" << 'PY'
import sys, re
p = sys.argv[1]
xml = open(p).read()
block = ('    <uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION" />\n'
         '    <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" />\n'
         '    <uses-feature android:name="android.hardware.location.gps" android:required="false" />\n')
if "ACCESS_FINE_LOCATION" not in xml:
    # <application> steht oft nackt am Zeilenende - am <manifest>-Tag
    # verankern, das ist immer einduetig in Zeile 1-2
    xml = re.sub(r'(<manifest[^>]*>\n)', r'\1' + block, xml, count=1)
    open(p, "w").write(xml)
    print("Manifest: Foreground-GPS-Rechte injiziert (required=false)")
else:
    print("Manifest: Rechte bereits vorhanden")
assert "ACCESS_BACKGROUND_LOCATION" not in xml, "Background-Location darf NICHT drin sein"
assert "WAKE_LOCK" not in xml.upper() or "wake" not in xml, "kein Wake-Lock erlaubt"
PY
else
  echo "HINWEIS: $MANIFEST nicht gefunden - Rechte manuell ergaenzen (siehe README)"
fi
echo "FERTIG. Android Studio: npx cap open android"
