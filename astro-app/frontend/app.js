/* Astro Command Center - Frontend-Logik (vanilla JS, keine Build-Tools).
 *
 * Backend-Adresse konfigurierbar (Capacitor-sicher): BASE_URL liegt im
 * localStorage, leer = gleiche Origin (Default beim via Tailscale Serve
 * ausgelieferten Betrieb). Alle Fetches laufen ausschliesslich darueber.
 * Offline: Service Worker cached die Shell; der letzte /api/spots-Stand
 * landet zusaetzlich im localStorage und wird mit "vor X Min" angezeigt.
 */
"use strict";

/* Cloudflare-Tunnel-Endpoint (Access-geschuetzt). Nativ ist er der
   Default; alte Tailscale-Konfigurationen wandern einmalig um. */
const CF_API = "https://api.teamigel.com";
/* Cloudflare-Access M2M-Service-Token (maschinelle Auth, kein OTP/
   Cookie noetig). Ueberschreibbar via localStorage fuer Rotation ohne
   APK-Rebuild (astro_cf_id / astro_cf_secret). */
const CF_ACCESS = {
  id: localStorage.getItem("astro_cf_id")
    || "2cf0ebe34375ac9e58cd1aef99cb31d6.access",
  secret: localStorage.getItem("astro_cf_secret")
    || "cfast_u1pe4fQKo8pOoQKgv0lhmy1gQvruihuAfUr8Vat65f85f9c0"
};
const isNativeApp = () => !!(window.Capacitor
  && window.Capacitor.isNativePlatform
  && window.Capacitor.isNativePlatform())
  || location.origin === "https://localhost"
  || location.origin === "http://localhost"
  || location.protocol === "capacitor:";
let BASE = (localStorage.getItem("astro_base") || "").replace(/\/$/, "");
if (BASE.includes("tailcc473e.ts.net")) BASE = CF_API;   // Migration
if (!BASE && isNativeApp()) BASE = CF_API;               // nativer Default
localStorage.setItem("astro_base", BASE);
const $ = (id) => document.getElementById(id);
const REFRESH_MS = 60_000;

let map, markersLayer, warnLayer, lpLayer, rainGridLayer;
let rgActive = false, rgDebounce = null, stormRings = [];
let rgHour = 0, rgLastData = null;   // Zeitregler 0-6 h fuer die Regen-Icons
let rgPlaying = false, rgTimer = null;

function lastSpatsCache(data) {
  return data.spots.map(s => [s.name, s.rating]);
}
let lastSpots = null;
let CURRENT_PROFILE = "dso";
let currentSpot = null;   // fuer Tab-Wechsel im Detail-Panel
let currentTab = "now";

/* ---------- Hilfen ---------- */
function fmt(v, unit) { return (v === null || v === undefined) ? "n/a" : v + (unit || ""); }
function ratingColor(rating) {
  return rating === "GO" ? "#35d07f" : rating === "MAYBE" ? "#f5c542"
       : rating === "NO-GO" ? "#ff5252" : "#93a1b3";
}
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

/* M2M-Auth: Jeder API-Call traegt die CF-Access-Service-Token-Header.
   Nativ laeuft fetch ueber CapacitorHttp (native HTTP-Schicht) - kein
   CORS, keine Preflights, kein Cookie-Handling. Der alte OTP-WebView-
   Flow ist entfernt. */
async function api(path, opts = {}) {
  opts.headers = Object.assign({
    "CF-Access-Client-Id": CF_ACCESS.id,
    "CF-Access-Client-Secret": CF_ACCESS.secret
  }, opts.headers || {});
  const res = await fetch(BASE + path, opts);
  if (!res.ok) throw new Error(path + " -> HTTP " + res.status);
  return res.json();
}

/* OTA-Live-Update (nativ): Kaltstart-Check gegen /updates/latest.json;
   neuere Version -> Bundle laden, entpacken, WebView umschalten
   (@capgo/capacitor-updater). Web/Browser identifiziert sich ueber
   die gleiche Origin und braucht kein OTA. */
async function checkOtaUpdate(force) {
  if (!isNativeApp() || !window.Capacitor?.Plugins?.CapacitorUpdater)
    return;
  if (!force) {
    const last = +(localStorage.getItem("astro_ota_last_check") || 0);
    if (Date.now() - last < 24 * 3600 * 1000) {
      commsLog("OTA: gedrosselt (max 1x/24h, letzter Check "
        + Math.round((Date.now() - last) / 3600000) + "h her)");
      return;
    }
  }
  localStorage.setItem("astro_ota_last_check", String(Date.now()));
  const currentVersion = localStorage.getItem("astro_bundle_version")
    || "APK-Basis (" + (document.querySelector("[title*=BUILD]")
      ? "unbekannt" : "v1.1.0") + ")";
  commsLog("OTA: Pruefe... (installiert: " + currentVersion + ")");
  try {
    const CU = window.Capacitor.Plugins.CapacitorUpdater;
    let m;
    try {
      m = await api("/updates/latest.json");
    } catch (manifestErr) {
      commsLog("OTA: Manifest nicht erreichbar ("
        + (manifestErr.message || "Netzwerk") + ") "
        + "- ueberspringe Update", "alert");
      return;
    }
    if (!m || !m.version) {
      commsLog("OTA: Manifest ungueltig (keine Version)", "alert");
      return;
    }
    if (m.version === currentVersion || m.version === localStorage
        .getItem("astro_bundle_version")) {
      commsLog("OTA: aktuell (" + m.version + ")");
      return;
    }
    commsLog("OTA: Lade Bundle " + m.version + "...");
    let done;
    try {
      done = await CU.download({
        url: BASE + m.url, version: m.version});
    } catch (dlErr) {
      commsLog("OTA-Download fehlgeschlagen: "
        + (dlErr.message || dlErr)
        + " | Installiert: " + currentVersion, "alert");
      return;
    }
    localStorage.setItem("astro_bundle_version", m.version);
    await CU.set(done);
    commsLog("OTA AKTIV: " + currentVersion + " \u2192 " + m.version
      + " \u2014 App startet neu");
  } catch (e) {
    commsLog("OTA-FEHLER: " + (e.message || e)
      + " | Installiert: " + currentVersion, "alert");
  }
}

/* ============================================================
   L3+L4: CLOUD-HUNTER (Umkreis-Scan) + DEPARTURE-OPTIMIZER
   ============================================================ */
let cloudHunterLayer = null;
let hunterActive = false;

async function toggleCloudHunter() {
  hunterActive = !hunterActive;
  if (hunterActive) {
    await fetchCloudHunter();
    commsLog("CLOUD-HUNTER: Scan aktiv");
  } else if (cloudHunterLayer) {
    map.removeLayer(cloudHunterLayer);
    cloudHunterLayer = null;
  }
}

async function fetchCloudHunter() {
  try {
    const d = await api("/api/cloud-hunter?radius_km=50&hours_ahead=3");
    if (!d || !d.grid || !d.grid.length) return;
    if (cloudHunterLayer) map.removeLayer(cloudHunterLayer);
    cloudHunterLayer = L.layerGroup();
    for (const g of d.grid) {
      const color = g.clouds_avg < 20 ? "#3aff7c" :
                    g.clouds_avg < 40 ? "#ffd24a" :
                    g.clouds_avg < 70 ? "#ff8a3d" : "#ff4b3e";
      L.circleMarker([g.lat, g.lon], {
        radius: 14, color: color, fillColor: color,
        fillOpacity: 0.15 + (g.clouds_avg / 100) * 0.35, weight: 1,
      }).addTo(cloudHunterLayer)
        .bindTooltip("Wolken " + g.clouds_avg + "% B" + g.bortle
          + " " + g.dist_km + "km");
    }
    if (d.best) {
      const bestIcon = L.divIcon({className: "hunter-best",
        html: "<div class='hb-ring'></div><div class='hb-label'>"
          + "\uD83C\uDF1F OPTIMUM<br>" + d.best.clouds_avg + "% B"
          + d.best.bortle + " " + d.best.dist_km + "km</div>",
        iconSize: [0, 0]});
      L.marker([d.best.lat, d.best.lon],
        {icon: bestIcon, interactive: false}).addTo(cloudHunterLayer);
      commsLog("CLOUD-HUNTER Optimum: " + d.best.clouds_avg
        + "% Wolken, " + d.best.dist_km + "km entfernt");
    }
    cloudHunterLayer.addTo(map);
    commsLog("[CLOUD-HUNTER] " + d.grid.length
      + " Punkte, Optimum: "
      + (d.best ? d.best.clouds_avg + "% bei "
        + d.best.dist_km + "km" : "kein"));
  } catch (e) {
    commsLog("CLOUD-HUNTER Fehler: " + (e.message || e), "alert");
  }
}

async function updateDepartureOptimizer() {
  try {
    const d = await api("/api/departure-optimizer?setup_minutes=45");
    if (!d || !d.results || !d.results.length) return;
    const el = document.getElementById("lh-departure");
    if (!el) return;
    el.innerHTML = d.results.slice(0, 4).map(r => {
      const icon = r.status === "GO" ? "\uD83D\uDFE9" :
        r.status === "DEPARTURE_IMMINENT" ? "\uD83D\uDFE5" : "\uD83D\uDD34";
      return "<div class='dep-line dep-" + r.status + "'>" + icon + " "
        + r.location.split(",")[0] + ": " + r.status_detail
        + " <small>(Abfahrt " + r.latest_departure + ", "
        + r.transit_minutes + "min)</small></div>";
    }).join("");
  } catch (e) {}
}

/* ============================================================
   LUECKE 1+2: Inversions-Badge + Wetter-Bewegungsanalyse
   ============================================================ */
function inversionBadgeHtml(s) {
  const inv = s.inversion;
  if (!inv || !inv.inversion_likely) return "";
  commsLog("[INVERSION DETECTED] " + s.name + " (" + inv.elevation_m
    + "m) Wolken " + s.clouds_total + "% -> "
    + inv.clouds_adjusted + "%");
  const adj = s.clouds_total_adjusted;
  return `<div class="inv-badge" title="Inversions-Erkennung: Talsohle "
    + "bewoelkt, aber ${inv.elevation_m}m Hoehe vermutlich ueber der "
    + "Wolkenschicht (${inv.adjustment_pp}pp Korrektur)">
    \u26F0 \u00DCBER INVERSION — ${adj !== undefined ? adj.toFixed(0) + "%" : "?"} statt ${s.clouds_total}%
  </div>`;
}

async function updateWeatherMovement() {
  try {
    const d = await api("/api/weather/movement");
    if (!d || !d.available) return;
    const el = document.getElementById("wmove");
    if (!el) return;
    const icon = d.trend === "clearing" ? "\u2600" :
                 d.trend === "clouding" ? "\u2601" : "\u2014";
    const arrow = d.movement_direction_deg != null
      ? dirToArrow(d.movement_direction_deg) : "";
    const speed = d.wind_speed_kmh ? d.wind_speed_kmh.toFixed(0) + " km/h" : "";
    let loc_hint = "";
    const clearing = Object.entries(d.locations || {})
      .filter(([n, v]) => v.in_clearing_path)
      .map(([n, v]) => `${n.split(",")[0]} (~${v.eta_hours}h)`);
    if (clearing.length && d.trend === "clearing") {
      loc_hint = ` \u00b7 Klart auf: ${clearing.slice(0, 2).join(", ")}`;
    }
    if (el.dataset.trend !== d.trend) {
      commsLog("[MOVEMENT] " + d.trend_text + " | "
        + d.movement_direction_deg + "deg | "
        + (d.wind_speed_kmh || "?") + " km/h");
      el.dataset.trend = d.trend;
    }
    el.innerHTML = `<span class="wm-icon">${icon}</span> `
      + `<span class="wm-trend wm-${d.trend}">${d.trend_text}</span> `
      + `${arrow} ${speed}${loc_hint}`;
    el.className = "wmove mono wm-" + d.trend;
  } catch (e) { /* Hintergrund-Feature, still failen */ }
}

function dirToArrow(deg) {
  const dirs = ["\u2191", "\u2197", "\u2192", "\u2198",
                "\u2193", "\u2199", "\u2190", "\u2196"];
  return dirs[Math.round(deg / 45) % 8];
}

/* ============================================================
   ERINNERUNGEN: Taegliche Pop-up-Meldungen (Datenwirt)
   ============================================================ */
async function scheduleDailyReminders() {
  if (!isNativeApp() || !window.Capacitor?.Plugins
      ?.LocalNotifications) return;
  try {
    const LN = window.Capacitor.Plugins.LocalNotifications;
    const perm = await LN.requestPermissions();
    if (!perm || perm.display !== "granted") return;
    const today = new Date().toISOString().slice(0, 10);
    const rk = "datenwirt_" + today;
    if (localStorage.getItem("astro_reminder_" + rk)) return;
    const now = new Date();
    const rt = new Date(now);
    rt.setHours(19, 30, 0, 0);
    if (rt <= now) return;
    await LN.schedule({
      notifications: [{
        id: 900001,
        title: "\uD83D\uDD25 DATENWIRT-ERINNERUNG",
        body: "Bitte Wolkenlage bewerten \u2014 Blick nach oben!\n"
          + "Astro CC \u2192 Standort \u2192 GROUND TRUTH\n"
          + "Jede Bewertung verbessert die Vorhersage.",
        schedule: {
          year: rt.getFullYear(), month: rt.getMonth() + 1,
          day: rt.getDate(), hour: 19, minute: 30, second: 0},
        extra: {type: "datenwirt_reminder"},
      }]
    });
    localStorage.setItem("astro_reminder_" + rk, "1");
    commsLog("ERINNERUNG: Datenwirt 19:30 Uhr geplant");
  } catch (e) {
    commsLog("Erinnerung-Fehler: " + (e.message || e));
  }
}

/* ============================================================
   PRIME-WINDOW-BENACHTIGUNGEN: Proaktive lokale Push-Meldungen
   mit Teleskop-Icon, 1-2 Tage im Voraus.
   ============================================================ */
let notifiedWindows = new Set(
  (localStorage.getItem("astro_notified_windows") || "").split(",")
    .filter(Boolean));

function _notifSchedule(d) {
  return {year: d.getFullYear(), month: d.getMonth() + 1,
          day: d.getDate(), hour: d.getHours(),
          minute: d.getMinutes(), second: d.getSeconds()};
}

async function checkPrimeWindowNotifications() {
  if (!isNativeApp() || !window.Capacitor?.Plugins
      ?.LocalNotifications) return;
  try {
    const data = await api("/api/notifications/upcoming?hours_ahead=48");
    if (!data || !data.events || !data.events.length) return;
    const LN = window.Capacitor.Plugins.LocalNotifications;
    const perm = await LN.requestPermissions();
    if (!perm || perm.display !== "granted") return;

    const toSchedule = [];
    for (const ev of data.events) {
      const key = ev.location + "|" + ev.night + "|" + ev.start;
      if (notifiedWindows.has(key)) continue;
      const scheduleAt = new Date(Date.now() + 1000);
      const dateStr = new Date(ev.night + "T" + ev.start).toLocaleDateString(
        "de-DE", {weekday: "short", day: "2-digit", month: "2-digit"});
      const body = ev.location + "\n" + dateStr + ", "
        + ev.start + "-" + ev.end + " Uhr\n"
        + ev.conditions.join(" \u00b7 ");
      toSchedule.push({
        id: ((Date.now() / 1000) | 0) % 2147483647,
        title: "\uD83C\uDF0C PRIME WINDOW",
        body: body,
        schedule: {at: _notifSchedule(scheduleAt)},
        extra: {type: "prime_window", location: ev.location},
      });
      notifiedWindows.add(key);
      commsLog("NOTIF: PRIME WINDOW " + ev.location + " " + ev.night
        + " " + ev.start + " geplant");
    }
    if (toSchedule.length) {
      await LN.schedule({notifications: toSchedule});
      const arr = [...notifiedWindows].slice(-50);
      localStorage.setItem("astro_notified_windows", arr.join(","));
    }
  } catch (e) {
    commsLog("NOTIF-Check: " + (e.message || e));
  }
}

/* ============================================================
   GROUND TRUTH (Phase 2: Datenhoheit): Menschliche Bodenwahrheit
   bricht die zirkulaere DWD-Verifikation. Vier Quick-Buttons im
   Standort-Panel senden cloud_cover-Schaetzwerte ans Backend.
   ============================================================ */
const GT_BUTTONS = [
  {icon: "\uD83C\uDF0C", label: "KLAR", clouds: 5,
   note: null, cls: "gt-klar"},
  {icon: "\u26C5", label: "L\u00DCCKEN", clouds: 40,
   note: null, cls: "gt-luecken"},
  {icon: "\u2601\uFE0F", label: "DICHT", clouds: 95,
   note: null, cls: "gt-dicht"},
  {icon: "\uD83C\uDF2B\uFE0F", label: "NEBEL", clouds: 100,
   note: "Inversions-Anomalie", cls: "gt-nebel"},
];

async function sendGroundTruth(gt) {
  commsLog("GROUND TRUTH: " + gt.label + " (" + gt.clouds + "%)");
  try {
    const payload = {
      timestamp: new Date().toISOString(),
      reporter: "balkon",
      actual_clouds: gt.clouds,
      note: gt.note || null,
    };
    if (currentSpot) payload.location_name = currentSpot.name;
    const r = await api("/api/telemetry/ground_truth", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)});
    if (r.delta != null) {
      commsLog("GT #" + r.id + ": " + gt.label + " " + gt.clouds + "%"
        + " \u2192 Delta: " + (r.delta > 0 ? "+" : "")
        + r.delta.toFixed(0) + " pp"
        + (r.delta > 15 ? " \u26A0 Modell deutlich daneben!"
           : r.delta < -15 ? " \u26A0 Modell zu optimistisch!" : " \u2713"),
        r.delta > 15 || r.delta < -15 ? "alert" : undefined);
    } else {
      commsLog("GT #" + r.id + ": " + gt.label + " gespeichert");
    }
  } catch (e) {
    commsLog("GT-FEHLER: " + (e.message || e), "alert");
  }
}

function groundTruthHtml() {
  const btns = GT_BUTTONS.map(gt =>
    `<button class="gt-btn ${gt.cls}" onclick='sendGroundTruth(${
      JSON.stringify({clouds: gt.clouds, label: gt.label, note: gt.note})
    })' title="${gt.label} (${gt.clouds}%)${
      gt.note ? " \u2014 " + gt.note : ""}">${gt.icon}<br>${gt.label}</button>`
  ).join("");
  return `<div class="gt-panel">
    <div class="gt-hdr mono">UPLINK: GROUND TRUTH</div>
    <div class="gt-hint">Blick nach oben \u2014 wie ist es wirklich?</div>
    <div class="gt-row">${btns}</div>
  </div>`;
}

function panelHtml(s) {
  const m = s.moon || {};
  const isPlanet = CURRENT_PROFILE === "planet";
  const badge = `<span class="badge ${esc(s.rating || "NO DATA")}">${esc(s.rating || "NO DATA")}</span>`
    + (s.radar_status ? `<span class="badge">${esc(s.radar_status)}</span>` : "")
    + (s.is_live ? `<span class="badge">LIVE</span>` : "")
    + (s.dew_risk ? `<span class="badge dew-${esc(s.dew_risk)}">${
        s.dew_risk === "hoch" ? "⚠ Beschlag: Fangspiegel!" :
        s.dew_risk === "mittel" ? "Beschlag: mittel" : "Beschlag: gering"}</span>` : "");

  const heavyAge = s.age_min != null ? `Heavy vor ${s.age_min} Min` : "Heavy: n/a";
  const radarAge = s.radar_age_min != null ? `Radar vor ${s.radar_age_min} Min` : "Radar: n/a";
  const lmh = s.clouds_lmh || [null, null, null];
  const temp = (s.night_temp_min != null && s.night_temp_max != null)
    ? `${s.night_temp_min.toFixed(0)} – ${s.night_temp_max.toFixed(0)} °C` : "n/a";
  const planets = s.planets || {};
  const PLABEL = { jupiter: "Jupiter", saturn: "Saturn", mars: "Mars" };
  const planetRows = Object.keys(PLABEL)
    .filter(k => planets[k])
    .map(k => row(dot(TH.planetAlt(planets[k].max_alt)) + " " + PLABEL[k],
                  `${planets[k].culm} (${fmt(planets[k].max_alt, "°")})` +
                  (planets[k].window ? ` · >30° ${esc(planets[k].window)}` : " · nie >30°")))
    .join("");

  return `
    <h2>${esc(s.name)}</h2>
    <div class="sub mono">Stand: ${esc(s.ts || "unbekannt")} · Profil: ${isPlanet ? "PLANETARISCH" : "DSO"}</div>
    <div>${badge}</div>
    <div class="grp mono">${dot(TH.clouds(s.clouds_total))}${dot(TH.seeing(s.seeing))} <b>Wolken · Seeing · Jetstream</b><span class="age">${esc(heavyAge)}</span></div>
    <div class="kv mono">
      ${row(dot(TH.seeing(s.seeing)) + " Seeing", fmt(s.seeing, "&quot;") + ` (Idx ${s.seeing_index ?? "-"} /5)`)}
      ${row(dot(TH.jet(s.jetstream)) + " Jetstream", fmt(s.jetstream, " m/s"))}
      ${row(dot(TH.clouds(s.clouds_total)) + " Wolken total", fmt(s.clouds_total, " %"))}
      ${row(dot(TH.clouds(lmh[0])) + " Wolken L / M / H", `${fmt(lmh[0], "")} / ${fmt(lmh[1], "")} / ${fmt(lmh[2], "")} %`)}
    </div>
    <div class="grp mono">${dot(TH.precip(s.precip_2h))}${dot(TH.wind(s.wind_speed))} <b>Radar · Regen · Wind · Tau</b><span class="age">${esc(radarAge)}</span></div>
    <div class="kv mono">
      ${row(dot(TH.rain(s.rain_prob)) + " Regen (4 h)", fmt(s.rain_prob, " %"))}
      ${row(dot(TH.precip(s.precip_2h)) + " Niederschlag (2 h)", fmt(s.precip_2h, " mm"))}
      ${row(dot(TH.wind(s.wind_speed)) + " Wind (max 2 h)", fmt(s.wind_speed, " km/h"))}
      ${row(dot(TH.tau(s.dewpoint_spread)) + " Tau-Spread (min 2 h)", fmt(s.dewpoint_spread, " K"))}
    </div>
    <div class="grp mono">${dot(TH.temp(s.night_temp_max))}${dot(TH.gusts(s.wind_gusts))} <b>Nacht · Boden</b><span class="age">Nachtverlauf (30-Min)</span></div>
    <div class="kv mono">
      ${row(dot(TH.temp(s.night_temp_max)) + " Temp (Nacht min–max)", temp)}
      ${row(dot(TH.rh(s.night_rh_max)) + " Feuchte (max)", fmt(s.night_rh_max, " %"))}
      ${row(dot(TH.gusts(s.wind_gusts)) + " Böen (Nacht max)", fmt(s.wind_gusts, " km/h"))}
    </div>
    <div class="grp mono">${dot(TH.moonAlt(m.max_alt))} <b>Mond · Dunkelheit</b><span class="age">Tages-Stand (skyfield)</span></div>
    <div class="kv mono">
      ${row(dot(TH.moonIll(m.illum)) + " Mond-Illumination", m.illum !== undefined ? fmt(m.illum, " %") + " <span class='dim'>(DSO)</span>" : "n/a")}
      ${row(dot(TH.moonAlt(m.max_alt)) + " Mond-Kulmination", m.culm ? `${esc(m.culm)} (${fmt(m.max_alt, "°")})` : "n/a")}
      ${row("Mond &gt; 30°", m.window ? esc(m.window) : "nie in dieser Nacht")}
      ${row("Astron. Dunkelheit", s.dark_window ? esc(s.dark_window) : "n/a")}
      ${m.libration ? row("Libration l/b",
        fmtSigned(m.libration.lib_l) + "° / " + fmtSigned(m.libration.lib_b) + "°")
        : ""}
      ${m.libration ? row("Kolongitude",
        m.libration.colong.toFixed(2) + "° <span class='age'>(LROC-Ref.)</span>")
        : ""}
      ${moonNightsRows(m.nights)}
      ${lpLine(s)}
    </div>
    <div class="grp mono"><b>Planeten &gt; 30°</b><span class="age">de421 · lokal</span></div>
    <div class="kv mono">${planetRows || row("Planeten", "keine Daten")}</div>
    ${inversionBadgeHtml(s)}
    ${groundTruthHtml()}
    <button class="transit-btn" onclick="fetchTransitRoute('${esc(s.id || s.name)}', ${s.lat}, ${s.lon})" title="OePNV-Einsatzweg vom HQ (Ilvesheim) zu diesem Standort">&#128646; TRANSIT ROUTE</button>
    <div id="transit-result" class="transit-result"></div>
    <div class="sub" style="margin-top:10px">
      Wolkenquelle: ${esc(s.clouds_source || "n/a")} &middot; ${isPlanet
        ? "Planetarisch: Seeing/Jetstream hart, Mond & Beschlag irrelevant"
        : "DSO: Beschlag hart (keine Tauheizung), Mond-Ampel = DSO-Eignung"}
    </div>`;
}

function renderSpots(data) {
  markersLayer.clearLayers();
  for (const s of data.spots) {
    const mm = s.precip_2h || 0;
    const rainNew = mm > 0 && (lastRainMm[s.name] ?? 0) <= 0;   // trocken -> nass
    const mk = L.marker([s.lat, s.lon], { icon: markerIcon(s, rainNew) });
    mk.on("click", () => {
      currentSpot = s;
      setTelemetry(s.name);
      $("panel").classList.remove("hidden");
      setStickHidden(true);   // Panel offen = Stick weicht dem Sheet
      showTab("now");
      fetchBortle(s);
    });
    mk.__spot = s;
    markersLayer.addLayer(mk);
    lastRainMm[s.name] = mm;
  }
  updateTargetLock();
}

/* ---------- Daten + Aktualitaet ---------- */
async function refresh() {
  try {
    const data = await api("/api/spots");
    localStorage.setItem("astro_last_refresh_ts", String(Date.now()));
    updateDataAge();
    lastSpots = data;
    localStorage.setItem("astro_last_spots", JSON.stringify(data));
    if (data.profile) {
      CURRENT_PROFILE = data.profile;
      updateModeButton();
    }
    const prevRatings = (lastSpots && lastSpots.spots)
      ? Object.fromEntries(lastSpatsCache(lastSpots)) : {};
    renderSpots(data);
    // Instrumente: einzeln try/catch - ein Crash darf die Map nicht toeten
    try { updateAstroInstruments(data); } catch (e) {
      console.error("UI Render Error (instruments):", e); }
    try { updateLunarHorizon(data); } catch (e) {
      console.error("UI Render Error (lunar):", e); }
    // Comms-Feed: Rating-Wechsel melden (nur wenn vorher bekannt)
    for (const s of data.spots) {
      const before = prevRatings[s.name];
      if (before && before !== s.rating) {
        const toAlert = String(s.rating).includes("NO-GO");
        commsLog(`${s.name.toUpperCase()} ${before} -> ${s.rating}`,
                 toAlert ? "alert" : undefined);
      }
    }
    // Warnungen nachladen (Layer nur, wenn aktiviert); Gewitter-Ringe
    // speichern wir zusaetzlich fuer die Blitz-Icons im Regen-Raster
    try {
      const warns = await api("/api/warnings");
      const nWarn = warns.features.length;
      if (nWarn !== (window._lastWarnN ?? -1)) {
        if (nWarn) {
          const storms = warns.features.filter(f => f.properties.kind === "storm");
          commsLog(`WARNING: DWD STORM CELL DETECTED (${storms.length} Gewitter, ` +
                   `${nWarn} gesamt)`, "alert");
        } else {
          commsLog("DWD: keine Warnungen");
        }
        window._lastWarnN = nWarn;
      }
      warnLayer.addData({ type: "FeatureCollection",
                          features: warns.features.filter(f => f.properties.kind !== "other") });
      stormRings = [];
      for (const f of warns.features) {
        if (f.properties.kind !== "storm" || !f.geometry) continue;
        if (f.geometry.type === "Polygon") stormRings.push(f.geometry.coordinates[0]);
        else if (f.geometry.type === "MultiPolygon")
          f.geometry.coordinates.forEach(p => stormRings.push(p[0]));
      }
    } catch (e) { console.warn("warnings offline:", e); }
    if (rgActive) fetchRainGrid();
    setFreshness(data.ts, false);
  } catch (e) {
    console.warn("refresh fehlgeschlagen:", e);
    const cached = localStorage.getItem("astro_last_spots");
    if (cached) { renderSpots(JSON.parse(cached)); setFreshness(JSON.parse(cached).ts, true); }
    else { setFreshness(null, true); }
  }
}

function setFreshness(ts, stale) {
  const el = $("freshness");
  const st = document.getElementById("tp-status");
  const stText = document.getElementById("tp-status-text");
  if (st) st.classList.toggle("stale", !!stale);
  if (stText) stText.textContent = stale ? "LINK LOST" : "SYSTEM SECURE";
  if (!ts) { el.textContent = stale ? "offline - kein Cache" : "lade…"; return; }
  const age = Math.max(0, Math.round((Date.now() - new Date(ts).getTime()) / 60000));
  el.textContent = (stale ? "OFFLINE - " : "") + `vor ${age} Min`;
}

/* ---------- Aktionen ---------- */
function updateModeButton() {
  const b = $("btn-mode");
  b.textContent = CURRENT_PROFILE === "planet" ? "🪐" : "🌌";
  b.title = CURRENT_PROFILE === "planet"
    ? "Profil: PLANETARISCH (klicken für DSO)"
    : "Profil: DSO (klicken für Planetarisch)";
}

async function toggleMode() {
  const next = CURRENT_PROFILE === "planet" ? "dso" : "planet";
  $("btn-mode").textContent = "…";
  try {
    await api("/api/profile", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: next }),
    });
    await refresh();
  } catch (e) {
    alert("Profilwechsel fehlgeschlagen: " + e.message);
    updateModeButton();
  }
}

function toggleNight() {
  document.body.classList.toggle("night");
  localStorage.setItem("astro_night", document.body.classList.contains("night") ? "1" : "0");
}



/* ============================================================
   DATALINK (Backend-Autonomie): Die App ist Display - der Igel
   crawlt und warnt allein. AUTO = 60s-Polling (Netzbetrieb),
   MAN = stumm + UPLINK-Button (einmalig holen + GPS-Ping).
   Standard: MAN auf schmalen Viewports (Batterie), AUTO am Desktop.
   ============================================================ */
let refreshTimer = null;

function datalinkMode() {
  const m = localStorage.getItem("astro_datalink")
    || (window.matchMedia("(max-width: 980px)").matches ? "man" : "auto");
  localStorage.setItem("astro_datalink", m);   // Default einmalig festhalten
  return m;
}

function applyDatalinkMode() {
  const mode = datalinkMode();
  const sw = document.getElementById("datalink-sw");
  if (sw) sw.checked = mode === "auto";
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
  if (mode === "auto") {
    refreshTimer = setInterval(refresh, REFRESH_MS);
    commsLog("DATALINK AUTO - 60s TICK");
  } else {
    commsLog("DATALINK MANUAL - UPLINK fuer Daten", "alert");
  }
  updateDataAge();
}

function setDatalinkMode(mode) {
  localStorage.setItem("astro_datalink", mode);
  applyDatalinkMode();
}

async function uplinkNow() {
  localStorage.removeItem("astro_auth_pending");   // Auth-Retry erlauben
  checkOtaUpdate(true);   // OTA-Check erzwingen (24h-Drossel umgehen)
  const btn = document.getElementById("uplink-btn");
  if (btn) btn.classList.add("uplink-busy");
  commsLog("UPLINK: hole Daten + GPS-Ping...");
  try { await refresh(); } catch (e) { /* refresh loggt selbst */ }
  try { if (!(pingBusy)) await gpsWatch(); } catch (e) { /* LED zeigt Fehler */ }
  if (btn) btn.classList.remove("uplink-busy");
}

/* Data Freshness: Alter der ANGEZEIGTEN Daten (Letztabruf),
   Amber ab 30 min, Rot ab 2 h - aktualisiert ohne Netz-Traffic */
function updateDataAge() {
  const el = document.getElementById("data-age");
  if (!el) return;
  const ts = +(localStorage.getItem("astro_last_refresh_ts") || 0);
  if (!ts) {
    el.textContent = "DATA: --";
    el.className = "data-age mono age-red";
    return;
  }
  const min = Math.floor((Date.now() - ts) / 60000);
  const label = min < 60 ? min + " MIN" : (min / 60).toFixed(1) + " H";
  el.textContent = "DATA: " + label;
  el.className = "data-age mono "
    + (min < 30 ? "age-fresh" : min < 120 ? "age-amber" : "age-red");
}

/* ============================================================
   GPS-PING (Phase 2): ein Tap = eine Messung. Native nutzt das vom
   Capacitor-Runtime injizierte window.Capacitor.Plugins.Geolocation
   (kein Bundler noetig), Browser bleibt bei navigator.geolocation.
   Beide Pfade muenden in handlePingResult: localStorage -> POST
   /api/watch (2 h Watchpoint) -> Freshness-LED/Text.
   ============================================================ */
let pingBusy = false;

function pingLedState() {
  /* Ampel gekoppelt ans 2h-Verfallsfenster von /api/watch:
     nie -> off (dim), Fehler -> blink (ping-error),
     <30min -> gruen, 30min-2h -> amber, >2h -> rot */
  const led = document.getElementById("led-ping");
  const txt = document.getElementById("ping-link");
  if (!led) return;
  let cls = "sq-led", label = "PING: --", title = "GPS-Ping: noch nie";
  const err = JSON.parse(localStorage.getItem("astro_ping_error") || "null");
  const last = JSON.parse(localStorage.getItem("astro_last_ping") || "null");
  if (err && (!last || err.ts > last.ts)) {
    cls += " ping-error";
    label = "PING: FEHLER";
    title = "GPS-Ping fehlgeschlagen: " + (err.msg || "?");
  } else if (last) {
    const min = Math.floor((Date.now() - last.ts) / 60000);
    if (min < 30) cls += " on";
    else if (min < 120) cls += " on amber";
    else cls += " on red";
    label = "PING: " + (min < 120 ? min + " MIN" : Math.floor(min / 60) + " H");
    title = "GPS-Ping vor " + min + " min";
  }
  led.className = cls;
  led.setAttribute("title", title);
  if (txt) { txt.textContent = label; txt.title = title; }
}

async function handlePingResult(lat, lon) {
  const ts = Date.now();
  localStorage.setItem("astro_last_ping", JSON.stringify({lat, lon, ts}));
  localStorage.removeItem("astro_ping_error");
  pingLedState();
  commsLog("GPS-PING: " + lat.toFixed(4) + " / " + lon.toFixed(4));
  try {
    // Cloudflare Access blockt CORS-Preflights (POST+JSON wuerde einen
    // ausloesen) -> SIMPLE REQUEST: text/plain ohne Custom-Header.
    // Der Body bleibt JSON, das Backend parst ihn tolerant; ein Token
    // geht per Query statt Header (Query loest keinen Preflight aus).
    const tok = localStorage.getItem("astro_api_token");
    const r = await api("/api/watch" + (tok ? "?token=" +
      encodeURIComponent(tok) : ""), {method: "POST",
      headers: {"Content-Type": "text/plain"},
      body: JSON.stringify({lat, lon, hours: 2})});
    commsLog("WATCHPOINT 2H AKTIV: " + (r.name || "Live"));
    refresh();
  } catch (e) {
    commsLog("WATCH-POST FEHLGESCHLAGEN: " + e.message, "alert");
  }
}

function pingFailure(msg) {
  localStorage.setItem("astro_ping_error",
    JSON.stringify({ts: Date.now(), msg: String(msg).slice(0, 120)}));
  pingLedState();
  commsLog("GPS-PING FEHLGESCHLAGEN: " + msg, "alert");
}

async function gpsWatch() {
  if (pingBusy) return;
  pingBusy = true;
  $("btn-gps").textContent = "\u2026";
  const done = () => { $("btn-gps").textContent = "\u25CE"; pingBusy = false; };
  const native = window.Capacitor && window.Capacitor.isNativePlatform
    && window.Capacitor.isNativePlatform();
  try {
    let lat, lon;
    if (native) {
      // Capacitor-Plugin (vom Runtime injiziert - kein Bundler noetig)
      const r = await window.Capacitor.Plugins.Geolocation.getCurrentPosition(
        { enableHighAccuracy: true, timeout: 15000 });
      lat = r.coords.latitude; lon = r.coords.longitude;
    } else {
      if (!navigator.geolocation)
        throw new Error("Geolocation nicht verfuegbar (HTTPS noetig)");
      const pos = await new Promise((res, rej) =>
        navigator.geolocation.getCurrentPosition(res, rej,
          { enableHighAccuracy: true, timeout: 15000 }));
      lat = pos.coords.latitude; lon = pos.coords.longitude;
    }
    map.setView([lat, lon], 12);
    await handlePingResult(lat, lon);
  } catch (e) {
    pingFailure(e.message || e);
    alert("GPS-Ping fehlgeschlagen: " + (e.message || e));
  }
  done();
}

function configure() {
  const cur = localStorage.getItem("astro_base") || "(gleiche Adresse wie diese Seite)";
  const v = prompt("Server-Adresse (leer = automatisch).\nSpäter z.B. die Tailscale-HTTPS-URL:", cur === "(gleiche Adresse wie diese Seite)" ? "" : cur);
  if (v === null) return;
  localStorage.setItem("astro_base", v.trim());
  location.reload();
}

/* ---------- Zahnrad-Menü + Changelog ---------- */
function toggleCfgMenu(force) {
  const m = $("cfg-menu");
  const show = force !== undefined ? force : m.classList.contains("hidden");
  m.classList.toggle("hidden", !show);
}

async function showChangelog() {
  toggleCfgMenu(false);
  const ov = $("changelog");
  ov.classList.remove("hidden");
  $("changelog-list").innerHTML = '<div class="sub">lade…</div>';
  try {
    const d = await api("/api/changelog");
    $("changelog-list").innerHTML = d.entries.map(e => `
      <article class="cl-entry">
        <div class="cl-head">
          <span class="cl-date">${esc(e.date)}</span>
          <span class="cl-title">${esc(e.title)}</span>
          ${e.tag ? `<span class="cl-tag">${esc(e.tag)}</span>` : ""}
        </div>
        <div class="cl-desc">${esc(e.desc || "")}</div>
        ${e.usage ? `<div class="cl-usage">&#9656; ${esc(e.usage)}</div>` : ""}
      </article>`).join("");
  } catch (err) {
    $("changelog-list").innerHTML =
      `<div class="sub">Changelog nicht verfügbar: ${esc(err.message)}</div>`;
  }
}

/* ---------- Start ---------- */
window.addEventListener("DOMContentLoaded", () => {
  if (localStorage.getItem("astro_night") === "1") document.body.classList.add("night");
  initMap();
  $("btn-refresh").onclick = refresh;
  $("btn-mode").onclick = toggleMode;
  $("btn-night").onclick = toggleNight;
  $("btn-gps").onclick = gpsWatch;
  $("btn-config").onclick = () => toggleCfgMenu();
  $("menu-config").onclick = () => { toggleCfgMenu(false); configure(); };
  $("menu-changelog").onclick = showChangelog;
  $("changelog-close").onclick = () => $("changelog").classList.add("hidden");
  document.addEventListener("click", (ev) => {
    const m = $("cfg-menu");
    if (!m.classList.contains("hidden")
        && !m.contains(ev.target) && ev.target.id !== "btn-config") {
      toggleCfgMenu(false);
    }
  });
  $("panel-close").onclick = () => {
    $("panel").classList.add("hidden");
    setStickHidden(false);    // Panel zu = Stick kehrt zurueck
  };
  $("tab-now").onclick = () => showTab("now");
  $("tab-fc").onclick = () => showTab("fc");
  refresh();
  applyDatalinkMode();
  setInterval(updateDataAge, 60_000);   // nur Anzeige, kein Traffic
  if ("serviceWorker" in navigator) {
    // Native (Capacitor): KEIN Service Worker - Assets kommen gebuendelt
    // aus der APK; ein SW-Cache wuerde bei App-Updates veraltete
    // Zustaende aus der Erstinstallation servieren (gleiche Origin).
    const isNative = window.Capacitor
      && window.Capacitor.isNativePlatform
      && window.Capacitor.isNativePlatform();
    if (!isNative) {
      navigator.serviceWorker.register("sw.js")
        .catch(e => console.warn("SW:", e));
    }
  }
});


/* ---------- Info-Widget: Vorhersage-Zuverlaessigkeit + Bot-Befehle ---------- */
const BIAS_LABELS = {
  clouds_le24h: ["Wolken \u226424 h", "pp", "kurzfristig"],
  clouds_gt24h: ["Wolken >24 h", "pp", "langfristig"],
  seeing_le24h: ["Seeing \u226424 h", "\u2033", "kurzfristig"],
  seeing_gt24h: ["Seeing >24 h", "\u2033", "langfristig"],
};
const BIAS_COLORS = {
  clouds_le24h: "#4db2ff", clouds_gt24h: "#0a4aa8",
  seeing_le24h: "#ffd24a", seeing_gt24h: "#ff8c42",
};

function biasTendenz(bucket, bias) {
  if (bias == null) return "zu wenig Daten \u2013 keine Korrektur aktiv";
  const [name, unit, wo] = BIAS_LABELS[bucket] || [bucket, ""];
  const b = Math.abs(bias);
  if (b < 0.3) return `${name}: sehr akkurat`;
  const richtung = bias < 0 ? "zu optimistisch" : "zu vorsichtig";
  const staerke = b > 10 ? "deutlich" : b > 3 ? "merklich" : "leicht";
  return `System ${richtung} (${wo}), ${staerke}: real ~${b.toFixed(1)} ${unit} ${bias < 0 ? "mehr" : "weniger"} als angesagt`;
}

function initInfoWidget() {
  // Eingebettet im rechten Cockpit-Panel (INTEL): Tabs + Body liegen statisch
  // in index.html (#iw-tabs / #iw-body im #bias-slot). Kein Floating-Panel
  // mehr, kein Oeffnen/Schliessen - immer sichtbar, Daten beim Start laden.
  document.querySelectorAll("#iw-tabs .iw-tab").forEach(t =>
    t.addEventListener("click", () => {
      document.querySelectorAll("#iw-tabs .iw-tab").forEach(x =>
        x.classList.toggle("active", x === t));
      renderInfoWidget(currentInfoData, t.dataset.tab);
    }));
  // Master-Schalter ASTRO OBSERVATION (unten im Panel, guard-cover Stil)
  const sw = document.getElementById("obs-mode-sw");
  if (sw) {
    api("/api/observation-mode").then(d => {
      sw.checked = !!d.observation_mode;
      commsLog(`OBS MODE ${d.observation_mode ? "ARMED" : "SAFE"}`);
    }).catch(() => {});
    sw.addEventListener("change", async () => {
      sw.disabled = true;
      try {
        const d = await api("/api/observation-mode", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ active: sw.checked }) });
        sw.checked = !!d.observation_mode;
        commsLog(`OBS MODE -> ${d.observation_mode ? "ARMED" : "SAFE"}`);
      } catch (e) { console.warn("observation-mode:", e); sw.checked = !sw.checked; }
      sw.disabled = false;
    });
  }
  loadInfoWidget();
}

// Comms-Feed: Matrix-Terminal im rechten Panel. Neue Zeilen erscheinen
// unten (Text laeuft nach oben), autoscroll, max 40 Zeilen.
/* Flightstick ausblenden, waehrend das Standort-Bottom-Sheet offen ist
   (alle Plattformen) - reine Sichtbarkeit, keine Transform-Kollision. */
function setStickHidden(hide) {
  document.getElementById("flightstick")
    ?.classList.toggle("fs-hidden", hide);
}

function commsLog(text, severity) {
  const t = document.getElementById("comms-terminal");
  if (!t) return;
  // Lokale Geraetezeit (Fix: toISOString lieferte UTC, 2h hinter Berlin)
  const ts = new Date().toLocaleTimeString("de-DE",
    {hour: "2-digit", minute: "2-digit", second: "2-digit"});
  const div = document.createElement("div");
  div.className = "ct-line" + (severity === "alert" ? " comms-alert" : "");
  div.innerHTML = `<span class="ct-ts">${ts}</span> ${esc(String(text))}`;
  t.appendChild(div);
  while (t.children.length > 40) t.removeChild(t.firstChild);
  t.scrollTop = t.scrollHeight;
}

/* ============================================================
   GLARESHIELD-INSTRUMENTE: Jet-Aesthetik per SVG-Generator.
   Zentrum (50,50) im viewBox 0 0 100 100 - ALLE Nadel-Rotationen
   laufen als SVG-Attribut rotate(WINKEL 50 50), niemals CSS-transform
   (Lesson aus der dezentrierten Nadel).
   ============================================================ */

function _gaugeTicks(min, max, majorEvery) {
  /* Major-Ticks alle majorEvery Einheiten (weiss, dick + Zahl),
     dazwischen ein feiner grauer Minor-Strich. -90deg = min. */
  let out = "";
  const span = max - min;
  const majors = Math.round(span / majorEvery);
  for (let i = 0; i <= majors; i++) {
    const v = min + i * majorEvery;
    const a = -90 + (v - min) / span * 180;
    const rad = a * Math.PI / 180;
    const tick = (r1, r2, w, col) =>
      `<line x1="${(50 + r1 * Math.sin(rad)).toFixed(1)}"
             y1="${(50 - r1 * Math.cos(rad)).toFixed(1)}"
             x2="${(50 + r2 * Math.sin(rad)).toFixed(1)}"
             y2="${(50 - r2 * Math.cos(rad)).toFixed(1)}"
             stroke="${col}" stroke-width="${w}"/>`;
    out += tick(37.5, 31.5, 1.9, "#e6edf4");
    if (i < majors) out += tick(37.5, 34.5, 0.7, "#5d6a78");
    out += `<text x="${(50 + 26.5 * Math.sin(rad)).toFixed(1)}"
              y="${(50 - 26.5 * Math.cos(rad) + 2.2).toFixed(1)}"
              text-anchor="middle" class="gauge-ticknum">${v}</text>`;
  }
  return out;
}

function _gaugeScrews() {
  /* Schlitzschrauben auf 45/135/225/315 Grad auf dem Bezel */
  const slots = [28, 75, 118, 163];   // deterministische Schlitzwinkel
  let out = "";
  [45, 135, 225, 315].forEach((deg, i) => {
    const rad = deg * Math.PI / 180;
    const cx = +(50 + 45.5 * Math.sin(rad)).toFixed(1);
    const cy = +(50 - 45.5 * Math.cos(rad)).toFixed(1);
    out += `<g><circle cx="${cx}" cy="${cy}" r="3.1" fill="#454c54"
              stroke="#14181c" stroke-width="0.9"/>
      <line x1="${cx - 2}" y1="${cy}" x2="${cx + 2}" y2="${cy}"
        stroke="#14181c" stroke-width="0.9"
        transform="rotate(${slots[i]} ${cx} ${cy})"/></g>`;
  });
  return out;
}

function gaugeSvg(o) {
  /* o = {id, label, valId, needleId, needleCls, min, max, majorEvery,
          dual: {needleId, needleCls}, digital: {boxId}} */
  return `<svg viewBox="0 0 100 100" class="gauge" id="${o.id}">
    <circle cx="50" cy="50" r="46" fill="none" stroke="#2c3136"
      stroke-width="7" class="gauge-bezel"/>
    <circle cx="50" cy="50" r="42" fill="#0b0f14" stroke="#1e252d"
      stroke-width="1"/>
    ${_gaugeScrews()}
    ${_gaugeTicks(o.min, o.max, o.majorEvery)}
    ${o.dual ? `<line x1="50" y1="52" x2="50" y2="13"
      class="gauge-needle ${o.dual.needleCls} gauge-needle-fine"
      id="${o.dual.needleId}" transform="rotate(-90 50 50)"/>` : ""}
    <line x1="50" y1="56" x2="50" y2="${o.dual ? 24 : 15}"
      class="gauge-needle ${o.needleCls}" id="${o.needleId}"
      transform="rotate(-90 50 50)"/>
    <circle cx="50" cy="50" r="4.6" class="gauge-hub"/>
    ${o.digital ? `<rect x="29" y="55" width="42" height="14" rx="1"
        class="gauge-digital-box"/>
      <text x="50" y="65.5" text-anchor="middle" class="gauge-digital"
        id="${o.digital.boxId}">B?</text>` : ""}
    <text x="50" y="80" text-anchor="middle" class="gauge-label">${o.label}</text>
    <text x="50" y="93" text-anchor="middle" class="gauge-val" id="${o.valId}">--</text>
  </svg>`;
}

/* Kuenstlicher LUNAR-HORIZONT als Rundinstrument: gleicher Bezel und
   dieselben 4 Schrauben wie die Glareshield-Gauges. Innen Himmel/Erde,
   die Horizontlinie verschiebt sich mit der Mond-Elevation (Pitch),
   der Mond sitzt fix auf der Achse wie das Flugzeug-Symbol. */
function buildLunar() {
  const mount = document.getElementById("lh-mount");
  if (!mount) return;
  mount.innerHTML = `
  <svg viewBox="0 0 100 100" id="lh-svg">
    <defs>
      <clipPath id="lh-clip"><circle cx="50" cy="50" r="39"/></clipPath>
      <radialGradient id="lh-moon-grad" cx=".38" cy=".34" r="1">
        <stop offset="0" stop-color="#fffdf2"/>
        <stop offset=".55" stop-color="#ffefc0"/>
        <stop offset="1" stop-color="#e3c98a"/>
      </radialGradient>
    </defs>
    <circle cx="50" cy="50" r="46" fill="none" stroke="#2c3136"
      stroke-width="7" class="gauge-bezel"/>
    <circle cx="50" cy="50" r="42" fill="#0b0f14" stroke="#1e252d"
      stroke-width="1"/>
    ${_gaugeScrews()}
    <g clip-path="url(#lh-clip)">
      <g id="lh-horizong" transform="translate(0 0)">
        <rect x="8" y="-40" width="84" height="90" fill="#0e2236"/>
        <rect x="8" y="50" width="84" height="100" fill="#191008"/>
        <line x1="8" y1="50" x2="92" y2="50" stroke="#f2f6fa"
          stroke-width="2.2"/>
        <line x1="8" y1="50" x2="92" y2="50" stroke="#ffd24a"
          stroke-width="0.8" opacity=".7"/>
        <line x1="30" y1="38" x2="70" y2="38" stroke="#5d7a99"
          stroke-width="1.1" stroke-dasharray="4 3"/>
        <line x1="34" y1="62" x2="66" y2="62" stroke="#6a5236"
          stroke-width="1.1" stroke-dasharray="4 3"/>
      </g>
    </g>
    <circle cx="50" cy="50" r="39" fill="none" stroke="#2c3642"
      stroke-width="1.5"/>
    <!-- Mond fix auf der Pitch-Achse -->
    <g id="lh-moonicon-wrap">
      <circle cx="50" cy="50" r="14" fill="rgba(255,240,190,.10)"/>
      <circle cx="50" cy="50" r="10.5" fill="rgba(255,240,190,.14)"/>
      <circle id="lh-moonicon" cx="50" cy="50" r="7" fill="url(#lh-moon-grad)"
        stroke="#8f8260" stroke-width="0.7"/>
      <circle cx="47.8" cy="48" r="1.4" fill="#c9b98c" opacity=".8"/>
      <circle cx="52" cy="52.4" r="1.05" fill="#c9b98c" opacity=".65"/>
      <circle cx="51.8" cy="47.6" r="0.7" fill="#c9b98c" opacity=".5"/>
    </g>
  </svg>`;
}

function buildGauges() {
  const row = document.getElementById("gauge-row");
  if (!row) return;
  const mount = (svg, title) =>
    `<div class="gauge-wrap" title="${title}">${svg}</div>`;
  row.innerHTML =
    mount(gaugeSvg({id: "gauge-seeing", label: "SEEING", valId: "seeing-val",
      needleId: "seeing-needle", needleCls: "needle-amber",
      min: 0, max: 5, majorEvery: 1}),
      "Seeing 0-5\u2033") +
    mount(gaugeSvg({id: "gauge-shear", label: "SHEAR", valId: "wind-val",
      needleId: "wind-needle", needleCls: "needle-cyan",
      dual: {needleId: "jet-needle", needleCls: "needle-cyan"},
      min: 0, max: 40, majorEvery: 10}),
      "Bodenwind 0-40 km/h (dicke Nadel) \u00b7 Jetstream 0-60 m/s (feine Nadel)") +
    mount(gaugeSvg({id: "gauge-tau", label: "TAU DP", valId: "tau-val",
      needleId: "tau-needle", needleCls: "needle-cyan",
      min: 0, max: 15, majorEvery: 5}),
      "Taupunkt-Spread 0-15 K") +
    mount(gaugeSvg({id: "gauge-dew", label: "DEW", valId: "dew-val",
      needleId: "dew-needle", needleCls: "needle-cyan",
      min: 0, max: 10, majorEvery: 5}),
      "Beschlags-Reserve: 10K sicher bis 0K Frost (invers)") +
    mount(gaugeSvg({id: "gauge-sqm", label: "SQM", valId: "sqm-val",
      needleId: "sqm-needle", needleCls: "needle-amber",
      min: 15, max: 22, majorEvery: 2,
      digital: {boxId: "sqm-bortle"}}),
      "Zenith-SQM 15-22 mag/arcsec\u00b2 \u00b7 Box: Bortle-Klasse");
}

/* ============================================================
   TELEMETRY-LINK: Glareshield-Instrumente zeigen ausschliesslich
   den gelockten Standort (Flightstick oder Marker-Klick setzt ihn).
   ============================================================ */
let telemetryTarget = localStorage.getItem("astro_telemetry") || null;

function telemetrySpot(data) {
  if (!data || !data.spots || !data.spots.length) return null;
  return data.spots.find(s => s.name === telemetryTarget)
    || data.spots[0];
}

/* HUD-Target-Brackets: der Marker des aktiven LINK-Ziels bekommt
   eckige Waffencomputer-Klammern (CSS ::before/::after). */
function updateTargetLock() {
  if (typeof markersLayer === "undefined" || !markersLayer) return;
  markersLayer.eachLayer(mk => {
    const el = mk._icon;
    if (!el) return;
    el.classList.remove("target-locked");
    const spot = mk.__spot;
    if (spot && spot.name === telemetryTarget) el.classList.add("target-locked");
  });
}

function setTelemetry(name, silent) {
  telemetryTarget = name;
  localStorage.setItem("astro_telemetry", name);
  const el = document.getElementById("telemetry-link");
  if (el) el.textContent = "[LINK: " + String(name || "?").toUpperCase() + "]";
  if (!silent && lastSpots) updateAstroInstruments(lastSpots);
  updateTargetLock();
}

/* Bortle-Klasse -> approx. Zenith-SQM (mag/arcsec^2) */
const SQM_FROM_BORTLE = {1: 22.0, 2: 21.7, 3: 21.3, 4: 20.9, 5: 20.3,
  6: 19.5, 7: 18.5, 8: 17.5, 9: 16.0};

function setNeedle(id, angle, cls) {
  const n = document.getElementById(id);
  if (!n) return;
  n.setAttribute("transform", `rotate(${angle.toFixed(1)} 50 50)`);
  if (cls) n.setAttribute("class", "gauge-needle " + cls
    + (id === "jet-needle" ? " gauge-needle-fine" : ""));
}

function setGaugeVal(id, txt, cls) {
  const v = document.getElementById(id);
  if (!v) return;
  v.textContent = txt;
  v.setAttribute("class", "gauge-val" + (cls ? " " + cls : ""));
}

function updateAstroInstruments(data) {
  pingLedState();   // Freshness-Ampel im bestaehenden 60s-Takt
  updateWeatherMovement();   // Luecke 2: Bewegungstrend
  updateDepartureOptimizer(); // Luecke 4: Countdown
  const s = telemetrySpot(data);
  if (!s) return;
  setTelemetry(s.name, true);   // Link-Label synchron halten
  // SEEING 0-5"
  const seeing = s.seeing ?? 2;
  setGaugeVal("seeing-val", seeing ? seeing.toFixed(1) + "\u2033" : "--");
  setNeedle("seeing-needle",
    -90 + (Math.min(seeing, 5) / 5) * 180, "needle-amber");
  // SHEAR: Bodenwind dick (0-40 km/h), Jetstream fein (0-60 m/s)
  const gusts = s.wind_gusts || s.wind_speed || 0;
  const jet = s.jetstream;
  setGaugeVal("wind-val", gusts.toFixed(0) + "km/h"
    + (jet != null ? " \u00b7J" + jet.toFixed(0) : ""),
    gusts > 30 ? "gv-danger" : gusts > 20 ? "gv-warn" : "");
  setNeedle("wind-needle", -90 + (Math.min(gusts, 40) / 40) * 180,
    gusts > 30 ? "needle-red" : gusts > 20 ? "needle-amber" : "needle-cyan");
  if (jet != null) setNeedle("jet-needle",
    -90 + (Math.min(jet, 60) / 60) * 180,
    jet > 45 ? "needle-red" : jet > 35 ? "needle-amber" : "needle-cyan");
  // TAU DP 0-15 K
  const tau = s.dewpoint_spread;
  setGaugeVal("tau-val", tau != null ? tau.toFixed(1) + "K" : "--");
  setNeedle("tau-needle",
    -90 + (Math.min(Math.max(tau ?? 7.5, 0), 15) / 15) * 180, "needle-cyan");
  // DEW: invers, 10K sicher (rechts) bis 0K Frost (links)
  if (tau != null) {
    const clamped = Math.min(Math.max(tau, 0), 10);
    setGaugeVal("dew-val", tau.toFixed(1) + " K" + (tau < 1.5 ? " !" : ""),
      tau < 1.5 ? "gv-danger" : tau < 3 ? "gv-warn" : "");
    setNeedle("dew-needle", -90 + (clamped / 10) * 180,
      tau < 1.5 ? "needle-red" : tau < 3 ? "needle-amber" : "needle-cyan");
  }
  // ZENITH SQM: Nadel 15-22 mag/arcsec^2, Box = Bortle-Klasse
  const bortle = s.bortle_class;
  const sqm = SQM_FROM_BORTLE[bortle] ?? null;
  const box = document.getElementById("sqm-bortle");
  if (box) box.textContent = bortle ? "B" + bortle : "B?";
  setGaugeVal("sqm-val", sqm != null ? sqm.toFixed(1) : "--");
  if (sqm != null) setNeedle("sqm-needle",
    -90 + ((sqm - 15) / 7) * 180, "needle-amber");
  // LEDs
  const ledVrn = document.getElementById("led-vrn");
  if (ledVrn) ledVrn.classList.add("on");
  const ledBias = document.getElementById("led-bias");
  if (ledBias) ledBias.classList.add("on", "amber");
  const ledObs = document.getElementById("led-obs");
  if (ledObs) ledObs.classList.add("off");
  const ledUap = document.getElementById("led-uap");
  if (ledUap) ledUap.classList.add("off");
}

function updateLunarHorizon(data) {
  if (!data || !data.spots || !data.spots.length) return;
  const best = data.spots.find(s => s.rating === "GO")
    || data.spots.find(s => s.rating === "MAYBE") || data.spots[0];
  const bestEl = document.getElementById("lh-best");
  if (bestEl) bestEl.textContent = (best.name || "?").toUpperCase();
  const alt = (best.moon || {}).max_alt || 0;
  const illum = (best.moon || {}).illum || 0;
  const altText = document.getElementById("lh-alt");
  const illumText = document.getElementById("lh-illum");
  if (altText) altText.textContent = "ALT " + alt.toFixed(0) + "\u00b0";
  if (illumText) illumText.textContent = "ILLUM " + illum.toFixed(0) + "%";
  /* Attitude-Logik: Mond steigt = Pitch hoch = Horizontlinie sinkt.
     Mond-Symbol bleibt fix im Zentrum (wie das Flugzeug-Symbol). */
  const hg = document.getElementById("lh-horizong");
  if (hg) hg.setAttribute("transform",
    `translate(0 ${((alt / 90) * 24).toFixed(1)})`);
  const mi = document.getElementById("lh-moonicon");
  if (mi) {
    mi.style.opacity = alt > 0 ? 1 : 0.22;
    mi.setAttribute("r", (5.5 + (illum / 100) * 2.5).toFixed(1));
  }

  // === LUNAR TARGET LOCK ===
  const tl = document.getElementById("tl-moon");
  if (tl) {
    if (alt > 30) {
      tl.textContent = "[LOCKED] MOON | ILLUM: " + illum.toFixed(0)
        + "% | ALT: " + alt.toFixed(0) + "\u00b0";
      tl.className = "tl-line tl-locked";
    } else if (alt > 0) {
      tl.textContent = "[OUT OF RANGE] MOON | ALT: " + alt.toFixed(0)
        + "\u00b0 (<30\u00b0 Atmosph\u00e4re)";
      tl.className = "tl-line tl-out-of-range";
    } else {
      tl.textContent = "[OUT OF RANGE] MOON | UNTER HORIZONT";
      tl.className = "tl-line tl-out-of-range";
    }
  }
  // SECONDARY: hellster Planet mit Fenster
  const sec = document.getElementById("tl-secondary");
  if (sec) {
    const planets = best.planets || {};
    const entries = Object.entries(planets)
      .filter(([, p]) => p && p.window && p.max_alt > 30)
      .sort((a, b) => b[1].max_alt - a[1].max_alt);
    if (entries.length) {
      const [name, p] = entries[0];
      sec.textContent = "[SECONDARY] " + name.toUpperCase()
        + " | ALT: " + (p.max_alt || 0).toFixed(0) + "\u00b0"
        + " | " + (p.window || "n/a");
    } else {
      sec.textContent = "";
    }
  }
}

/* ============================================================
   FLIGHTSTICK: physisches Targeting unten links auf der Karte.
   Ziehen des Knuppels definiert eine Peilung (0deg = Nord);
   der Standort, der von der Kartenmitte aus am besten in dieser
   Richtung liegt, wird Telemetry-Ziel (Live-Umschaltung der
   Glareshield-Instrumente + Marker-Highlight).
   ============================================================ */
function _bearingDeg(lat1, lon1, lat2, lon2) {
  const toR = d => d * Math.PI / 180;
  const y = Math.sin(toR(lon2 - lon1)) * Math.cos(toR(lat2));
  const x = Math.cos(toR(lat1)) * Math.sin(toR(lat2))
    - Math.sin(toR(lat1)) * Math.cos(toR(lat2)) * Math.cos(toR(lon2 - lon1));
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

/* HQ: Equipment-Depot Ilvesheim (Teleskop & Crawler) - fester Startpunkt
   aller Transit-Einsatzwege. Kein dynamisches Nutzer-GPS mehr. */
const HQ = { name: "ILVESHEIM HQ", lat: 49.4783726, lon: 8.5662896 };
let routeLayer = null;   // aktuelle Einsatzweg-Darstellung (HQ-Pin + Linie)

function drawRouteLine(destLat, destLon, destName) {
  // vorherige Route entfernen, bevor eine neue gezeichnet wird
  if (routeLayer) { map.removeLayer(routeLayer); routeLayer = null; }
  // Doppel-Linien-Trick: solide schwarze Kontur fuer Kontrast gegen
  // die dunkle Kachelkarte, darueber die taktisch rote gestrichelte
  // Trajektorie. Beide im selben LayerGroup -> Cleanup entfernt beide.
  const casing = L.polyline(
    [[HQ.lat, HQ.lon], [destLat, destLon]],
    { color: "#000000", weight: 7, opacity: 1 });
  const line = L.polyline(
    [[HQ.lat, HQ.lon], [destLat, destLon]],
    { color: "#ff3b30", weight: 4, opacity: 0.95, dashArray: "10, 15" });
  const hqIcon = L.divIcon({
    className: "hq-pin",
    html: "<div class='hq-dot'></div><div class='hq-tag'>HQ</div>",
    iconSize: [0, 0] });
  routeLayer = L.layerGroup([
    L.marker([HQ.lat, HQ.lon], { icon: hqIcon, interactive: false }),
    casing,
    line,
  ]).addTo(map);
  map.fitBounds(line.getBounds(), { padding: [50, 50] });
  commsLog("EINSATZWEG EINGEZEICHNET: " + HQ.name + " \u2192 "
    + String(destName || "?").toUpperCase());
}

/* Visueller Fahrplan: Verbindung als Pill-Kette rendern.
   Walk = Fussgaenger-Icon + Minuten, Tram/Stadtbahn (RNV) = amber,
   Bus/Regional = blau. Tooltip zeigt die Haltestellen. */
function transitPills(steps) {
  if (!steps || !steps.length) return "";
  return "<div class='tp-row'>" + steps.map(s => {
    if (s.kind === "walk")
      return "<span class='tp-pill tp-walk'>\uD83D\uDEB6 " + s.min
        + "\u2032</span>";
    const tram = /^RNV/i.test(s.line || "");
    return "<span class='tp-pill " + (tram ? "tp-tram" : "tp-bus")
      + "' title='" + esc(s.from) + " \u2192 " + esc(s.to) + "'>"
      + "<b>" + esc(s.line) + "</b> " + esc(s.dep) + "\u2013" + esc(s.arr)
      + "</span>";
  }).join("<span class='tp-arrow'>\u2799</span>") + "</div>";
}

async function fetchTransitRoute(name, lat, lon) {
  const resultEl = document.getElementById("transit-result");
  if (!resultEl) return;
  resultEl.innerHTML =
    "<div class='transit-loading'>\u23F3 VRN-Fahrplan ab HQ...</div>";
  try {
    const d = await api("/api/deployment?id=" + encodeURIComponent(name)
      + "&home_lat=" + HQ.lat + "&home_lon=" + HQ.lon
      + "&setup_minutes=30");
    // HTTP 200: Einsatzweg auf dem Radar zeichnen (HQ -> Ziel)
    drawRouteLine(lat, lon, name);
    let html = "<div class='transit-head'>[ROUTE: " + HQ.name + " -> "
      + esc(String(name).toUpperCase()) + "]</div>";
    if (d.dynamic_abort) {
      const ret = d.extraction ? d.extraction.earliest_return : "BERECHNET";
      html += "<div class='transit-abort'>\u26A0 DYNAMIC ABORT: "
        + "WETTERUMSCHLAG - N\u00C4CHSTE R\u00DCCKFAHRT " + esc(ret)
        + " \u00b7 Umschlag " + esc(d.dynamic_abort.time)
        + " (" + esc(d.dynamic_abort.reason || "?") + ")</div>";
      commsLog("DYNAMIC ABORT: WETTERUMSCHLAG \u2192 R\u00DCCKFAHRT "
        + ret + " AB " + d.dynamic_abort.time, "alert");
    }
    if (d.latest_departure) {
      const ld = d.latest_departure;
      html += "<div class='transit-conn'><b>\u27A1 HIN " + esc(ld.time)
        + " \u2192 " + esc(ld.arrival) + " AN</b>"
        + (ld.steps && ld.steps.length
          ? transitPills(ld.steps)
          : "<div class='tp-row'><span class='tp-pill tp-bus'><b>"
            + esc(ld.lines.join(", ")) + "</b></span></div>")
        + "</div>";
    } else if (d.note) {
      html += "<div class='transit-none'>\u26A0 " + esc(d.note) + "</div>";
    }
    if (d.extraction) {
      const ex = d.extraction;
      html += "<div class='transit-conn'><b>\u2B05 R\u00DCCK "
        + esc(ex.earliest_return) + " \u2192 " + esc(ex.arrival_home)
        + " AN</b> \u00b7 Abbau ab " + esc(d.extraction_warning_ts || "?")
        + (ex.steps && ex.steps.length ? transitPills(ex.steps) : "")
        + "</div>";
    } else if (!d.latest_departure) {
      html += "<div class='transit-none'>Keine Rueckverbindung.</div>";
    }
    html += "<div class='transit-src'>" + esc(d.transit_source)
      + " | GW " + esc(d.golden_window) + "</div>";
    resultEl.innerHTML = html;
  } catch (e) {
    resultEl.innerHTML = "<div class='transit-none'>\u274C "
      + esc(e.message || "Fehler") + "</div>";
  }
}

function initFlightstick() {
  const fs = document.getElementById("flightstick");
  if (!fs) return;
  /* 3D-HOTAS: Bezel + Schrauben wie bei den Gauges, Faltenbalg-Basis,
     ergonomischer Griff mit Hartlicht-Kanten und rotem Feuerknopf.
     #fs-grip neigt sich beim Drag um den Pivot (50,80). */
  fs.insertAdjacentHTML("afterbegin", `
  <svg viewBox="0 0 100 100" id="fs-svg">
    <defs>
      <linearGradient id="fs-grip-grad" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#454d55"/>
        <stop offset=".45" stop-color="#2b3238"/>
        <stop offset="1" stop-color="#171c21"/>
      </linearGradient>
      <radialGradient id="fs-fire-grad" cx=".35" cy=".3" r="1">
        <stop offset="0" stop-color="#ff8a72"/>
        <stop offset=".55" stop-color="#e8352a"/>
        <stop offset="1" stop-color="#7a0e08"/>
      </radialGradient>
    </defs>
    <circle cx="50" cy="50" r="46" fill="none" stroke="#2c3136"
      stroke-width="7" class="gauge-bezel"/>
    <circle cx="50" cy="50" r="42" fill="#0b0f14" stroke="#1e252d"
      stroke-width="1"/>
    ${_gaugeScrews()}
    <circle cx="50" cy="50" r="30" fill="none" stroke="#2c3642"
      stroke-width="1" stroke-dasharray="3 4"/>
    <line x1="50" y1="12" x2="50" y2="24" stroke="#4a5a6f" stroke-width="1.6"/>
    <line x1="50" y1="76" x2="50" y2="88" stroke="#4a5a6f" stroke-width="1.6"/>
    <line x1="12" y1="50" x2="24" y2="50" stroke="#4a5a6f" stroke-width="1.6"/>
    <line x1="76" y1="50" x2="88" y2="50" stroke="#4a5a6f" stroke-width="1.6"/>
    <!-- Faltenbalg-Basis (gerippte Gummi, harte Kanten fuer 3D) -->
    <g>
      <rect x="35" y="84" width="30" height="5.5" rx="2.7" fill="#262c33"
        stroke="#0d1013" stroke-width="0.9"/>
      <rect x="37" y="78.5" width="26" height="5.5" rx="2.7" fill="#303841"
        stroke="#0d1013" stroke-width="0.9"/>
      <rect x="39" y="73" width="22" height="5.5" rx="2.7" fill="#262c33"
        stroke="#0d1013" stroke-width="0.9"/>
      <line x1="37" y1="81.2" x2="63" y2="81.2" stroke="#55606c"
        stroke-width="0.9" opacity=".8"/>
      <line x1="39" y1="75.7" x2="61" y2="75.7" stroke="#55606c"
        stroke-width="0.9" opacity=".8"/>
    </g>
    <!-- Griff: ergonomisch gebogen, Pivot am Faltenbalg (50,80) -->
    <g id="fs-grip" transform="translate(0 0)">
      <path d="M45 76 C42.5 62 43 50 46.5 37
               C47.2 34.4 50.8 33.4 52.6 35.6
               C56.4 40.2 57.4 48 56.6 56
               C56 62 56.2 69 57 76 Z"
        fill="url(#fs-grip-grad)" stroke="#0d1013" stroke-width="1"/>
      <path d="M46.8 74 C45 61 45.4 49 48 38.4" fill="none"
        stroke="#9fb2c4" stroke-width="1.1" opacity=".6"/>
      <path d="M53.8 41 C55.6 46 56.2 52 55.9 58" fill="none"
        stroke="#6c7d8e" stroke-width="0.8" opacity=".45"/>
      <ellipse cx="51.4" cy="45.5" rx="2.4" ry="4.2" fill="#14181c"
        stroke="#3a444e" stroke-width="0.7"/>
      <circle cx="51.4" cy="45.5" r="1.1" fill="#5d6d7d"/>
      <!-- Feuer-/Trim-Knopf an der Griffspitze -->
      <circle cx="51" cy="34.5" r="4.6" fill="url(#fs-fire-grad)"
        stroke="#4a0a05" stroke-width="1"/>
      <circle cx="49.8" cy="33.2" r="1.3" fill="#ffc2ae" opacity=".85"/>
      <circle cx="51" cy="34.5" r="7" fill="none"
        stroke="rgba(255,80,60,.35)" stroke-width="1.5"/>
    </g>
  </svg>`);
  const grip = () => document.getElementById("fs-grip");
  const degEl = document.getElementById("fs-deg");
  let dragging = false;

  const pick = (deg) => {
    if (!lastSpots || !lastSpots.spots) return;
    const c = map.getCenter();
    let bestS = null, bestDiff = 361;
    for (const s of lastSpots.spots) {
      const b = _bearingDeg(c.lat, c.lng, s.lat, s.lon);
      const diff = Math.abs(((b - deg + 540) % 360) - 180);
      if (diff < bestDiff) { bestDiff = diff; bestS = s; }
    }
    if (bestS) setTelemetry(bestS.name);
  };

  const move = (e) => {
    const r = fs.getBoundingClientRect();
    let dx = e.clientX - (r.left + r.width / 2);
    let dy = e.clientY - (r.top + r.height / 2);
    const max = r.width / 2 - 16;
    const len = Math.min(Math.hypot(dx, dy), max);
    const deg = (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360;
    const ux = len * Math.sin(deg * Math.PI / 180);
    const uy = -len * Math.cos(deg * Math.PI / 180);
    const g = grip();
    if (g) g.setAttribute("transform",
      `translate(${(ux * 0.38).toFixed(1)} ${(uy * 0.38).toFixed(1)}) `
      + `rotate(${(deg * 0.22 - (deg > 180 ? 79.2 : 0)).toFixed(1)} 50 80)`);
    if (degEl) degEl.textContent = deg.toFixed(0).padStart(3, "0") + "\u00b0";
    fs.classList.add("fs-active");
    pick(deg);
  };
  const release = () => {
    dragging = false;
    const g = grip();
    if (g) g.setAttribute("transform", "translate(0 0) rotate(0 50 80)");
    fs.classList.remove("fs-active");
  };
  fs.addEventListener("pointerdown", e => {
    dragging = true;
    fs.setPointerCapture(e.pointerId);
    move(e);
  });
  fs.addEventListener("pointermove", e => { if (dragging) move(e); });
  fs.addEventListener("pointerup", release);
  fs.addEventListener("pointercancel", release);
}

function initCockpitStatus() {
  commsLog("ASTRO CC ONLINE — SYSTEM SECURE");
}

let currentInfoData = null;   // Cache: einmal laden pro Oeffnen

async function loadInfoWidget() {
  const body = $("iw-body");
  const cached = localStorage.getItem("astro_info_cache");
  let data = currentInfoData;
  if (!data) {
    try {
      const [stats, hist, cmds] = await Promise.all([
        api("/api/bias-stats"), api("/api/bias-history?days=365"),
        api("/api/telegram-commands")]);
      data = { stats, hist, cmds };
      currentInfoData = data;
      localStorage.setItem("astro_info_cache", JSON.stringify(data));
    } catch (e) {
      console.warn("info-widget offline:", e);
      data = cached ? JSON.parse(cached) : null;
      if (data) data._offline = true;
    }
  }
  renderInfoWidget(data, "bias");
}

function renderInfoWidget(data, tab) {
  const body = $("iw-body");
  if (!data) {
    body.innerHTML = "<div class='iw-empty'>Daten noch nicht verf\u00fcgbar "
      + "(offline und kein gespeicherter Stand).</div>";
    return;
  }
  if (tab === "bot") { body.innerHTML = iwBotHtml(data.cmds); return; }
  body.innerHTML = iwBiasHtml(data.stats, data.hist, !!data._offline);
}

function iwBiasHtml(stats, hist, offline) {
  if (!stats || !stats.buckets) return "<div class='iw-empty'>Noch keine "
    + "Bias-Daten berechnet.</div>";
  const cards = Object.entries(BIAS_LABELS).map(([k, [name, unit]]) => {
    const b = stats.buckets[k] || {};
    const bias = b.bias == null ? "\u2013" : `${b.bias > 0 ? "+" : ""}${b.bias.toFixed(2)} ${unit}`;
    const n = b.sample_n != null ? `n=${b.sample_n}` : "";
    const b7 = b.bias_7d != null
      ? `${b.bias_7d > 0 ? "+" : ""}${b.bias_7d.toFixed(k.startsWith("clouds") ? 1 : 2)} ${unit} <small>(7&nbsp;Tage)</small>`
      : "<small>7-Tage-Wert: zu wenig Daten</small>";
    return `<div class="iw-card ${b.applied ? "" : "iw-inactive"}">
      <div class="iw-card-name">${name}</div>
      <div class="iw-card-val">${bias} <small>insgesamt</small></div>
      <div class="iw-card-val iw-card-7d">${b7}</div>
      <div class="iw-card-n">${n}${b.applied === false ? " \u00b7 zu wenig Daten" : ""}</div>
      <div class="iw-card-tendenz">${biasTendenz(k, b.bias_7d != null ? b.bias_7d : b.bias)}</div>
    </div>`;
  }).join("");
  return `
    <div class="iw-sub">Wie genau stimmen die Prognosen?
      ${offline ? "<span class='iw-offline'>(offline: letzter Stand)</span>"
                : ""} \u00b7 Stand: ${esc(stats.computed_at || "?")}</div>
    <div class="iw-cards">${cards}</div>
    <div class="iw-chart-title">Gesamter Zeitverlauf \u2014 Abweichung = Prognose vs. Realit\u00e4t</div>
    <div class="iw-chart-hint">Kr\u00e4ftige Linie: letzte 7 Tage (Treffsicherheit jetzt) \u00b7 d\u00fcnn gestrichelt: Gesamtdurchschnitt (Referenz)</div>
    ${iwChartSvg(hist || [])}
    <div class="iw-note">Das System sagt tendenziell ${Math.abs((stats.buckets.clouds_le24h || {}).bias || 0) > 3 ? "zu optimistische Wolkenprognosen" : "gute Wolkenprognosen"}. Seeing ist sehr akkurat. Die Korrektur wird t\u00e4glich berechnet und in der Anzeige angewendet.</div>
    <div class="iw-foot">Korrektur auf Anzeige angewendet, nicht auf Rating. Rating bleibt bewusst unkorrigiert, bis sich die Korrektur bew\u00e4hrt hat.</div>`;
}

function iwChartSvg(hist) {
  // Liniendiagramm als Inline-SVG (keine externe Lib). Wichtig: Pfade
  // starten MIT M und fuehren mit L weiter - ein fuehrendes L waere
  // ungueltiges SVG und wuerde gar nicht gezeichnet (ursprung des
  // "nur unverbundene Punkte"-Bugs).
  const W = 320, H = 150, PL = 34, PR = 8, PT = 8, PB = 18;
  const buckets = Object.keys(BIAS_COLORS);
  // pro Bucket+Tag den LETZTEN Wert (Bias wird ggfs. mehrfach/Tag gerechnet)
  const byDay = {};
  (hist || []).forEach(h => {
    if (!buckets.includes(h.bucket)) return;
    const d = (h.computed_at || "").slice(0, 10);
    if (!d) return;
    byDay[d] = byDay[d] || {};
    const prev = byDay[d][h.bucket];
    if (!prev || String(prev.computed_at) <= String(h.computed_at))
      byDay[d][h.bucket] = h;
  });
  const days = Object.keys(byDay).sort();
  if (days.length < 2)
    return "<div class='iw-empty'>Zeitreihe entsteht \u2013 ab dem zweiten "
      + "Tag sehen Sie hier die Entwicklung.</div>";
  // X-Achse = echte Zeit: Position nach Tagesdistanz, dynamisch ueber
  // die gesamte Historie (Luecken im Zeitraum stauchen nicht)
  const DAY = 86400000;
  const d0 = new Date(days[0] + "T00:00:00").getTime();
  const d1 = new Date(days[days.length - 1] + "T00:00:00").getTime();
  const spanD = Math.max(1, (d1 - d0) / DAY);
  const x = ts => PL + (W - PL - PR)
    * ((new Date(ts.slice(0, 10) + "T00:00:00").getTime() - d0) / DAY) / spanD;
  // Y-Achse aus allen Serien (7 Tage + gesamt)
  const allVals = [];
  days.forEach(d => buckets.forEach(b => {
    const h = byDay[d][b];
    if (h) [h.bias, h.bias_7d].forEach(v => { if (v != null) allVals.push(v); });
  }));
  let lo = Math.min(...allVals), hi = Math.max(...allVals);
  if (hi - lo < 1) { hi += 0.5; lo -= 0.5; }
  const y = v => PT + (H - PT - PB) * (1 - (v - lo) / (hi - lo));
  const X0 = PL, X1 = W - PR;
  // Nulllinie + Y-Labels
  const axis = [];
  if (lo < 0 && hi > 0) axis.push(
    `<line x1="${X0}" x2="${X1}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" stroke="#667" stroke-dasharray="2 3" stroke-width="0.7"/>`,
    `<text x="${X0 - 3}" y="${y(0).toFixed(1)}" class="iw-ax" text-anchor="end" dominant-baseline="middle">0</text>`);
  [hi, lo].forEach((v, k) => axis.push(
    `<text x="${X0 - 3}" y="${y(v) + (k ? -3 : 3)}" class="iw-ax" text-anchor="end">${v > 0 ? "+" : ""}${v.toFixed(1)}</text>`));
  // X-Ticks: 5 gleichmaessig verteilte Datumsmarken
  const fmtD = t => new Date(t).toLocaleDateString("de-DE",
    { day: "2-digit", month: "2-digit" });
  for (let k = 0; k <= 4; k++) {
    const t = d0 + (d1 - d0) * k / 4;
    const px = PL + (X1 - PL) * k / 4;
    axis.push(`<line x1="${px.toFixed(1)}" x2="${px.toFixed(1)}" y1="${H - PB}" y2="${H - PB + 3}" stroke="#556"/>`,
      `<text x="${px.toFixed(1)}" y="${H - 5}" class="iw-ax" text-anchor="middle">${fmtD(t)}</text>`);
  }
  // Serien: gesamt (duenn, gestrichelt) + 7-Tage (kraeftig, durchgehend)
  const series = buckets.map(b => {
    const pts = days.map(d => byDay[d][b]).filter(Boolean);
    if (pts.length < 2 && !pts.length) return "";
    const mk = key => pts.filter(p => p[key] != null).map((p, i) =>
      `${i ? "L" : "M"}${x(p.computed_at).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
    const dots = pts.map(p =>
      `<circle cx="${x(p.computed_at).toFixed(1)}" cy="${y(p.bias_7d != null ? p.bias_7d : p.bias).toFixed(1)}" r="2.3" fill="${BIAS_COLORS[b]}"><title>${b}: 7T ${p.bias_7d ?? "\u2013"} (n=${p.n_7d ?? "?"}) | gesamt ${p.bias} (n=${p.sample_n})</title></circle>`).join("");
    return `<path d="${mk("bias")}" fill="none" stroke="${BIAS_COLORS[b]}" stroke-width="0.9" stroke-dasharray="3 3" opacity=".55"/>`
      + `<path d="${mk("bias_7d")}" fill="none" stroke="${BIAS_COLORS[b]}" stroke-width="2"/>`
      + dots;
  }).join("");
  // Legende: letzter Stand je Bucket
  const last = {};
  days.slice().reverse().some(d => buckets.some(b => {
    if (byDay[d][b] && !last[b]) last[b] = byDay[d][b];
    return false;
  }));
  buckets.forEach(b => { if (!last[b]) days.some(d => {
    if (byDay[d][b]) { last[b] = byDay[d][b]; return true; } return false; }); });
  const fmtV = (b, v) => v == null ? "\u2013"
    : `${v > 0 ? "+" : ""}${v.toFixed(b.startsWith("clouds") ? 1 : 2)}`;
  const legend = buckets.map(b => {
    if (!last[b]) return `<span class="iw-legend-empty">
       <i style="background:${BIAS_COLORS[b]}"></i>${BIAS_LABELS[b][0]} (keine Daten)</span>`;
    const v7 = last[b].bias_7d != null ? fmtV(b, last[b].bias_7d) : "\u2013";
    return `<span><i style="background:${BIAS_COLORS[b]}"></i>${BIAS_LABELS[b][0]}
       <b>${v7}</b> / gesamt ${fmtV(b, last[b].bias)}</span>`;
  }).join("");
  const spanLab = spanD >= 60 ? `${(spanD / 30.4).toFixed(1)} Monate`
    : `${Math.round(spanD)} Tage`;
  const span = `<div class="iw-chart-span">${days[0].slice(5)} \u2014 ${days[days.length - 1].slice(5)} (${spanLab})</div>`;
  return `<div class="iw-chart"><svg viewBox="0 0 ${W} ${H}" role="img">${axis.join("")}${series}</svg>
    <div class="iw-legend">${legend}</div>${span}</div>`;
}

function iwBotHtml(cmds) {
  const list = (cmds && cmds.commands) || [];
  return `
    <div class="iw-sub">Was der Bot kann</div>
    <table class="iw-cmds">${list.map(c => `
      <tr><td class="iw-cmd">${esc(c.command)}${c.usage ? "<br><small>" + esc(c.usage) + "</small>" : ""}</td>
          <td>${esc(c.description)}</td></tr>`).join("")}
    </table>
    <div class="iw-note">Alle Befehle funktionieren direkt im Chat mit
      ${esc((cmds && cmds.bot_name) || "@AstroCrawler007bot")}.
      Schreibe einfach /help, dann bekommst du diese Liste.</div>`;
}
