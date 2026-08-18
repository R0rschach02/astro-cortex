#!/usr/bin/env bash
# Deploy-Skript Astro Command Center (Git-Tracking seit 17.08.; urspruenglich Fix vom 16.08.:
# astro_crawler.py-Änderungen erreichten die Timer-Dienste sofort, aber der
# Dauerdienst astro-app.service importiert das Modul beim START - ohne
# Restart blieb die PWA-API auf altem Stand, während die DB schon neue
# Daten hatte. Dieses Skript macht jeden Deploy atomar + sichtbar.)
set -euo pipefail

WS="/home/enigma/.zcode/workspace/default"
LIVE="/home/enigma/astro_crawler.py"
APP_DIR="/home/enigma/astro-app"

echo "== 1/5 Syntax-Check (Workspace-Kopie) =="
python3 -m py_compile "$WS/astro_crawler.py"

echo "== 2/5 Deploy nach $LIVE =="
cp "$WS/astro_crawler.py" "$LIVE"

echo "== 3/5 Integrität (md5 Workspace == Live) =="
a=$(md5sum "$WS/astro_crawler.py" | cut -d' ' -f1)
b=$(md5sum "$LIVE" | cut -d' ' -f1)
[ "$a" = "$b" ] && echo "OK: $a" || { echo "MD5-MISMATCH!"; exit 1; }

echo "== 4/5 Dienste: Timer sind oneshot (laden Datei je Tick frisch),"
echo "         aber astro-app importiert beim Start -> IMMER neu starten =="
systemctl --user restart astro-app.service
sleep 3
systemctl --user is-active astro-app.service

echo "== 5/5 Live-Beweis gegen die laufende API =="
curl -sf --max-time 15 http://127.0.0.1:8000/api/spots \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("Spots:", len(d["spots"]), "|", ", ".join(s["name"] for s in d["spots"]))'

echo "ExecStart-Pfade zur Referenz:"
for s in astro-crawler astro-radar astro-app; do
  systemctl --user show "$s.service" -p ExecStart | sed 's/ ; .*//;s/ExecStart={ path=/  /' 
done

echo "== 6/6 Git: Deploy-Commit (nur wenn Quellcode-Änderungen) =="
if git -C /home/enigma rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C /home/enigma add astro_crawler.py astro_deploy.sh messier.csv astro-app 2>/dev/null || true
  if git -C /home/enigma diff --cached --quiet; then
    echo "Keine Quellcode-Änderungen - kein Commit."
  else
    git -C /home/enigma commit -q -m "deploy: $(date '+%Y-%m-%d %H:%M')" \
      && echo "Commit: $(git -C /home/enigma log -1 --oneline)"
  fi
else
  echo "WARNUNG: kein Git-Repo in /home/enigma - Deploy nicht versioniert!"
fi
echo "DEPLOY KOMPLETT."
