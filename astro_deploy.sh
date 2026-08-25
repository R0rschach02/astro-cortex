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
python3 -m py_compile "$WS/astro_crawler.py" "$WS/data_sanity.py"

# ruff-Gate: nur Korrektheits-Fehler (F821 undefined name, F823/F811
# unbenutzte/doppelte Definition - die Bug-Klasse des ClearOutside-Regressions-
# Feblers). Reine Stil-Warnungen (E/W) blockieren bewusst NICHT.
echo "== 1b/5 Statische Korrektheit (ruff F8xx) =="
ruff check --select F821,F823,F811 \
  "$WS/astro_crawler.py" "$WS/data_sanity.py" "$APP_DIR/backend/main.py" \
  || { echo "RUFF-FEHLER: Deploy abgebrochen"; exit 1; }

echo "== 2/5 Deploy nach $LIVE (+ data_sanity.py) =="
cp "$WS/astro_crawler.py" "$LIVE"
cp "$WS/data_sanity.py" "/home/enigma/data_sanity.py"

echo "== 3/5 Integrität (md5 Workspace == Live) =="
a=$(md5sum "$WS/astro_crawler.py" | cut -d' ' -f1)
b=$(md5sum "$LIVE" | cut -d' ' -f1)
[ "$a" = "$b" ] && echo "OK: $a" || { echo "MD5-MISMATCH!"; exit 1; }
c=$(md5sum "$WS/data_sanity.py" | cut -d' ' -f1)
d=$(md5sum "/home/enigma/data_sanity.py" | cut -d' ' -f1)
[ "$c" = "$d" ] && echo "OK: $c" || { echo "MD5-MISMATCH data_sanity!"; exit 1; }

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
  git -C /home/enigma add astro_crawler.py data_sanity.py astro_deploy.sh \
    locations.json.example messier.csv astro-app 2>/dev/null || true
  if git -C /home/enigma diff --cached --quiet; then
    echo "Keine Quellcode-Änderungen - kein Commit."
  else
    # Konventionelle Message als $1 uebergeben (feat:/fix:/...), sonst Fallback Datum
    git -C /home/enigma commit -q -m "${1:-deploy: $(date '+%Y-%m-%d %H:%M')}" \
      && echo "Commit: $(git -C /home/enigma log -1 --oneline)"
  fi
else
  echo "WARNUNG: kein Git-Repo in /home/enigma - Deploy nicht versioniert!"
fi
echo "== 7/7 GitHub-Push (mit Divergenz-Warnung, nie auto-merge/force) =="
export GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
if git -C /home/enigma fetch origin 2>/dev/null; then
  BEHIND=$(git -C /home/enigma rev-list --count HEAD..origin/main 2>/dev/null || echo 1)
  if [ "$BEHIND" -gt 0 ]; then
    echo "ABBRUCH: origin/main hat $BEHIND Commits, die lokal fehlen - KEIN Push, KEIN Merge:"
    git -C /home/enigma log --oneline HEAD..origin/main | head -5
    git -C /home/enigma diff --stat HEAD origin/main | tail -3
    python3 - << 'PYTELE' 2>/dev/null || true
import sys; sys.path.insert(0, "/home/enigma")
import astro_crawler as ac, datetime
state = ac.load_state()
today = f"{datetime.datetime.now():%Y-%m-%d}"
if state.get("push_alert_date") != today:  # max. 1 Hinweis/Tag
    state["push_alert_date"] = today
    ac.save_state(state)
    ac.send_telegram("Deploy pausiert - GitHub hat unbekannte Commits, bitte manuell pruefen (astro_deploy.sh Schritt 7).")
PYTELE
    exit 1
  fi
  if git -C /home/enigma push origin main 2>&1 | tail -2; then
    echo "Push OK: $(git -C /home/enigma rev-parse --short HEAD) -> origin/main"
  else
    echo "FEHLER: Push gescheitert (Netzwerk/Auth) - kein stiller Retry."
    exit 1
  fi
else
  echo "FEHLER: git fetch origin gescheitert (Netzwerk/Auth) - kein stiller Retry."
  exit 1
fi
echo "DEPLOY KOMPLETT."
