/* Astro Command Center - Frontend-Logik (vanilla JS, keine Build-Tools).
 *
 * Backend-Adresse konfigurierbar (Capacitor-sicher): BASE_URL liegt im
 * localStorage, leer = gleiche Origin (Default beim via Tailscale Serve
 * ausgelieferten Betrieb). Alle Fetches laufen ausschliesslich darueber.
 * Offline: Service Worker cached die Shell; der letzte /api/spots-Stand
 * landet zusaetzlich im localStorage und wird mit "vor X Min" angezeigt.
 */
"use strict";

const BASE = (localStorage.getItem("astro_base") || "").replace(/\/$/, "");
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

async function api(path, opts) {
  const res = await fetch(BASE + path, opts);
  if (!res.ok) throw new Error(path + " -> HTTP " + res.status);
  return res.json();
}

/* ---------- Karte ---------- */
function initMap() {
  map = L.map("map", { zoomControl: false, tap: true })
        .setView([49.54, 8.63], 10);

  // Basiskarte: OSM + CSS-Invert-Filter = tiefschwarz taktisch (LESSONS
  // Fall 8: CARTODark lieferte in der Praxis doch "API KEY REQUIRED"-
  // Wasserzeichen obwohl Einzelkacheln 200 lieferten - Live-Beweis immer
  // gegen die GERENDERTE Karte fahren, nicht gegen Einzelrequests).
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, subdomains: "abc", className: "basemap-tile",
    attribution: '&copy; OpenStreetMap-Mitwirkende',
  }).addTo(map);

  // Lichtverschmutzung (Lorenz-Atlas via Backend-Proxy, Disk-Cache dort).
  // maxNativeZoom 6: darueber fragt Leaflet die 6er-Kacheln ab und skaliert.
  lpLayer = L.tileLayer(BASE + "/api/lp-tiles/{z}/{x}/{y}", {
    maxNativeZoom: 6, maxZoom: 18, opacity: 0.55,
    attribution: "Light Pollution: D. Lorenz (VIIRS)",
  });

  // DWD-Warnpolygone (GeoJSON vom Backend)
  warnLayer = L.geoJSON(null, {
    style: (f) => {
      const k = f.properties.kind;
      return {
        color: k === "storm" ? "#ff2a00" : k === "rain" ? "#3fa9f5" : "#9aa5b1",
        weight: 2, fillOpacity: k === "storm" ? 0.35 : 0.18,
        dashArray: k === "other" ? "4 6" : null,
      };
    },
    onEachFeature: (f, layer) => layer.bindPopup(
      `<b>${esc(f.properties.event)}</b><br>${esc(f.properties.description || "")}`),
  });

  markersLayer = L.layerGroup().addTo(map);

  // Regionales Regen-Icon-Raster (Open-Meteo via /api/rain-grid): die neue
  // Standard-Regenansicht - klare Wolke/Tropfen-Icons statt Farbflaechen.
  rainGridLayer = L.layerGroup().addTo(map);
  rgActive = true;

  // Vorhersage-Zuverlaessigkeit + Bot-Befehle: einklappbares Widget
  // top-left (data-cached, offline-faehig via localStorage).
  buildGauges();
  buildLunar();
  initFlightstick();
  initInfoWidget();
  initCockpitStatus();
  // Grid-Layout mit minmax kann die Map-Groesse nach dem ersten Render
  // aendern - Leaflet muss den Container neu vermessen
  setTimeout(() => map.invalidateSize(), 100);
  setTimeout(() => map.invalidateSize(), 500);
  // Kartenbox aendert nur ihre Pixelmasse bei echten Viewport-Events -
  // Overlay-Toggles (Mobile) beruehren sie bewusst NICHT.
  window.addEventListener("resize", () => map.invalidateSize());
  window.addEventListener("orientationchange", () => map.invalidateSize());
  window.visualViewport?.addEventListener("resize",
    () => map.invalidateSize());

  // Zeitregler sitzt statisch im unteren Cockpit-Panel (Timeline):
  // nur Listener, kein dynamisches Element mehr.
  $("rg-slider").addEventListener("input", (e) => {
    setRgHour(Number(e.target.value));
  });
  $("rg-play").addEventListener("click", rgTogglePlay);

  // Cockpit-Hardware-Buttons (linkes Panel) ersetzen die Layer-Dropdown-
  // Control: massiv, permanent sichtbar, LED-Status. Regen-Icons sind
  // default scharf (wie bisher Standard-Layer).
  const hwState = { regen: true, radar: false, satellite: false,
                    warn: false, lp: false };
  const hwBtn = document.querySelector('.hw-btn[data-layer="regen"]');
  if (hwBtn) hwBtn.classList.add("active");
  document.querySelectorAll("#layer-hw-stack .hw-btn").forEach(b => {
    b.addEventListener("click", () => {
      const key = b.dataset.layer;
      hwState[key] = !hwState[key];
      b.classList.toggle("active", hwState[key]);
      if (key === "regen") {
        rgActive = hwState[key];
        if (rgActive) { fetchRainGrid(); } else { rainGridLayer.clearLayers(); }
      } else if (key === "radar") {
        hwState[key] ? rvStart("radar") : rvStop();
      } else if (key === "satellite") {
        hwState[key] ? rvStart("satellite") : rvStop();
      } else if (key === "warn") {
        hwState[key] ? map.addLayer(warnLayer) : map.removeLayer(warnLayer);
      } else if (key === "lp") {
        hwState[key] ? map.addLayer(lpLayer) : map.removeLayer(lpLayer);
      }
      commsLog(`SENSOR ${key.toUpperCase()} ${hwState[key] ? "ON" : "OFF"}`);
    });
  });
  // Zoom + Panel-Pins
  $("zoom-in")?.addEventListener("click", () => map.zoomIn());
  $("zoom-out")?.addEventListener("click", () => map.zoomOut());

  /* Mobile: Panels sind exklusive Slide-in-HUD-Overlays ueber der
     Fullscreen-Map. Toggle aendert NUR die Transform-Klasse - die
     Karten-Box behaelt ihre Pixelmasse, invalidateSize bleibt an
     resize/orientationchange/visualViewport gebunden (nicht hier!).
     Desktop-Pfad (Grid-Spalte auf 0) unveraendert. */
  const isMobileUI = () => window.matchMedia("(max-width: 980px)").matches;
  const toggleHud = (side) => {
    const el = document.getElementById(side === "left"
      ? "left-panel" : "right-panel");
    const other = document.getElementById(side === "left"
      ? "right-panel" : "left-panel");
    const open = !el.classList.contains("hud-open");
    el.classList.toggle("hud-open", open);
    if (open) {   // Exklusivitaet - inkl. Persistenz-Sync
      other.classList.remove("hud-open");
      localStorage.setItem("astro_hud_" + (side === "left" ? "right" : "left"), "0");
    }
    localStorage.setItem("astro_hud_" + side, open ? "1" : "0");
  };
  // Beim Start hoechstens EIN Overlay wiederherstellen
  if (isMobileUI()) {
    if (localStorage.getItem("astro_hud_left") === "1")
      document.getElementById("left-panel")?.classList.add("hud-open");
    else if (localStorage.getItem("astro_hud_right") === "1")
      document.getElementById("right-panel")?.classList.add("hud-open");
  }
  $("left-panel-pin")?.addEventListener("click", () => {
    if (isMobileUI()) return toggleHud("left");
    document.getElementById("cockpit").classList.toggle("collapse-left");
    setTimeout(() => map.invalidateSize(), 200);
  });
  $("left-hud-btn")?.addEventListener("click", () => toggleHud("left"));
  $("right-panel-pin")?.addEventListener("click", () => {
    if (isMobileUI()) return toggleHud("right");
    document.getElementById("cockpit").classList.toggle("collapse-right");
    setTimeout(() => map.invalidateSize(), 200);
  });
  // Raster folgt dem Ausschnitt (debounced); Cache im Backend faengt Pan an
  map.on("moveend zoomend", () => {
    if (!rgActive) return;
    clearTimeout(rgDebounce);
    rgDebounce = setTimeout(fetchRainGrid, 600);
  });
}

/* ---------- Ampel-Schwellen (bestätigt 2026-08-15) ----------
   grün/gelb/rot je Parameter; Rating-Schwellen (Wolken 20/40, Seeing 2.0)
   stammen 1:1 aus der Crawler-Logik. Rückgabe: 'g' | 'y' | 'r' | null(n/a). */
const TH = {
  seeing:    (v) => v == null ? null : v <= 1.0 ? "g" : v <= 2.0 ? "y" : "r",
  jet:       (v) => v == null ? null : v <= 15  ? "g" : v <= 30  ? "y" : "r",
  clouds:    (v) => v == null ? null : v <= 20  ? "g" : v <= 40  ? "y" : "r",
  rain:      (v) => v == null ? null : v <= 10  ? "g" : v <= 30  ? "y" : "r",
  precip:    (v) => v == null ? null : v <= 0.1 ? "g" : v <= 1.0 ? "y" : "r",
  wind:      (v) => v == null ? null : v <= 15  ? "g" : v <= 30  ? "y" : "r",
  gusts:     (v) => v == null ? null : v <= 25  ? "g" : v <= 40  ? "y" : "r",
  tau:       (v) => v == null ? null : v >= 6   ? "g" : v >= 3   ? "y" : "r",
  temp:      (v) => v == null ? null : v <= 18  ? "g" : v <= 25  ? "y" : "r",  // 600D ungekuehlt
  rh:        (v) => v == null ? null : v <= 80  ? "g" : v <= 90  ? "y" : "r",
  moonIll:   (v) => v == null ? null : v <= 25  ? "g" : v <= 60  ? "y" : "r",
  moonAlt:   (v) => v == null ? null : v > 30   ? "g" : "r",
  planetAlt: (v) => v == null ? null : v > 30   ? "g" : "r",
};
function dot(cls) {
  return cls ? `<span class="dot d-${cls}"></span>` : `<span class="dot d-na"></span>`;
}
function row(k, v, cls) {
  return `<div class="k">${k}</div><div class="v ${cls || ""}">${v}</div>`;
}

/* ---------- Vorausschau-Tab ---------- */
function cell(v, fmtFn) {
  const cls = fmtFn(v);
  return `<td class="${cls ? "c-" + cls : "c-na"}">${v ?? "–"}</td>`;
}

function nightLabel(night) {
  // Absolute Datumsangabe statt relativem "Heute/+1/+2": das night-Feld
  // (YYYY-MM-DD) ist im Forecast-Datensatz vorhanden - reine Anzeige-Sache.
  const WD = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
  const y = +night.slice(0, 4), m = +night.slice(5, 7), d = +night.slice(8, 10);
  const dt = new Date(y, m - 1, d);
  if (isNaN(dt)) return esc(night);
  return `${WD[dt.getDay()]}, ${String(d).padStart(2, "0")}.${String(m).padStart(2, "0")}.${y}`;
}

function forecastHtml(fc) {
  const g = fc.golden;
  const others = (fc.golden_windows || []).filter(w => w !== g);
  const goldenCard = g
    ? `<div class="golden">
         <div class="g-title">✨ Golden Window ${g.night === new Date().toISOString().slice(0,10) ? "heute Nacht" : ""}</div>
         <div class="g-time">${esc(g.start)} – ${esc(g.end)} Uhr</div>
         <div class="g-why">${g.reasons.map(esc).join(" · ")}</div>
         ${others.length ? `<div class="g-why" style="margin-top:4px">Weitere: ${others.map(w =>
            `${w.night.slice(5)} ${esc(w.start)}-${esc(w.end)} (${w.hours}h)`).join(" · ")}</div>` : ""}
       </div>`
    : `<div class="golden none">
         <div class="g-title">Kein brauchbares Fenster in den nächsten Nächten</div>
         <div class="g-why">${esc(CURRENT_PROFILE === "planet"
              ? "Seeing/Jetstream/Wolken erfüllen nie gleichzeitig die Kriterien"
              : "Es fehlt vermutlich an Dunkelheit, Wolken oder Seeing")}</div>
       </div>`;
  // Stunden nach Nacht gruppieren (Segmente), innerhalb chronologisch
  const segs = [];
  for (const h of fc.series) {
    if (!segs.length || segs[segs.length-1].night !== h.night)
      segs.push({night: h.night, rows: []});
    segs[segs.length-1].rows.push(h);
  }
  const rows = segs.map(seg => `
    <tr class="night-sep"><td colspan="8">${nightLabel(seg.night)}</td></tr>` +
    seg.rows.map(h => `
    <tr class="${h.ok ? "row-ok" : ""}">
      <td class="c-h">${esc(h.hhmm)}</td>
      ${cell(h.clouds, TH.clouds)}
      ${h.beyond_seeing ? '<td class="c-na" title="Meteoblue-Horizont überschritten">–</td>'
                        : cell(h.seeing, TH.seeing)}
      ${cell(h.wind, TH.wind)}
      ${cell(h.tau, TH.tau)}
      ${cell(h.rain, TH.rain)}
      <td class="${h.dark ? "c-g" : "c-na"}">${h.dark ? "🌙" : "☀"}</td>
      <td class="${h.moon_up ? "c-y" : "c-na"}">${h.moon_up ? "🌕" : ""}</td>
    </tr>`).join("")).join("");
  return `
    <div class="sub mono">Prognose ${esc(fc.ts || "")} · Profil ${esc(fc.profile)} ·
      Seeing bis ${esc((fc.seeing_horizon || "").slice(11,16) || "Horizont")} ·
      Wolken: ${esc(fc.sources && fc.sources.clouds || "?")}/OM ·
      Dunkel heute ${esc(fc.dark_window || "n/a")}</div>
    ${goldenCard}
    <table class="fc-table mono">
      <thead><tr><th>Std</th><th>Wolk%</th><th>See"</th><th>Wind</th>
        <th>Tau</th><th>Reg%</th><th>🌌</th><th>🌕</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="sub" style="margin-top:8px">
      Mond: ${fc.moon_illum != null ? fc.moon_illum + "% " : "n/a "}
      ${esc(fc.moon_window || "")}
    </div>`;
}

function showTab(tab) {
  currentTab = tab;
  $("tab-now").classList.toggle("active", tab === "now");
  $("tab-fc").classList.toggle("active", tab === "fc");
  if (!currentSpot) return;
  if (tab === "now") {
    $("panel-content").innerHTML = panelHtml(currentSpot);
  } else {
    $("panel-content").innerHTML =
      '<div class="sub">lade Vorausschau…</div>';
    api(`/api/forecast?name=${encodeURIComponent(currentSpot.name)}`)
      .then(fc => {
        if (currentTab === "fc")
          $("panel-content").innerHTML = forecastHtml(fc);
      })
      .catch(e => {
        $("panel-content").innerHTML =
          `<div class="sub">Vorausschau nicht verfügbar: ${esc(e.message)}</div>`;
      });
  }
}

function lpLine(s) {
  const key = `astro_lp_${s.lat.toFixed(3)},${s.lon.toFixed(3)}`;
  let b = null;
  try { b = JSON.parse(localStorage.getItem(key)); } catch (e) { /* noch nicht geladen */ }
  if (!b) return row("Zenit-Lichtverschm.", "lade…");
  const cls = b.zone_index <= 4 ? "g" : b.zone_index <= 9 ? "y" : "r";
  const tip = CURRENT_PROFILE === "planet"
    ? "Planetarisch: Filter irrelevant"
    : b.zone_index >= 10 ? "Triband Pflicht · UHC schwach"
    : b.zone_index >= 8 ? "Antlia Triband empfohlen"
    : "Himmel dunkel genug · Triband optional";
  return row(dot(cls) + " Zenit-Lichtverschm.",
              `Zone ${esc(b.zone)} · ${b.mag} mag/arcsec² <span class='dim'>≈Bortle ${b.bortle}</span>`)
       + row("Filter-Tipp", tip);
}

function fetchBortle(s) {
  const key = `astro_lp_${s.lat.toFixed(3)},${s.lon.toFixed(3)}`;
  if (localStorage.getItem(key)) return;
  api(`/api/bortle?lat=${s.lat}&lon=${s.lon}`)
    .then(b => {
      localStorage.setItem(key, JSON.stringify(b));
      if (currentSpot === s && currentTab === "now") showTab("now");
    }).catch(() => {});
}

/* ---------- RainViewer: Regenradar + Satellit (animiert, direkt, kein Proxy) */
const RV_API = "https://api.rainviewer.com/public/weather-maps.json";
let rvState = { frames: [], layers: [], idx: 0, playing: false,
                timer: null, refetch: null, kind: null, active: false };

async function rvFetchFrames(kind) {  // kind: 'radar' | 'satellite'
  const d = await (await fetch(RV_API)).json();
  if (kind === "radar") {
    return { host: d.host, frames: [...(d.radar.past || []),
                                     ...(d.radar.nowcast || [])] };
  }
  return { host: d.host, frames: d.satellite?.infrared || [] };
}

function rvTimestampEl() {
  let el = document.getElementById("rv-ts");
  if (!el) {
    el = document.createElement("div");
    el.id = "rv-ts";
    el.innerHTML = '<button id="rv-play" class="hbtn rv-btn">&#9654;</button>' +
                   '<span id="rv-time" class="mono"></span>';
    document.getElementById("map").appendChild(el);
    document.getElementById("rv-play").onclick = rvTogglePlay;
  }
  return el;
}

async function rvStart(kind) {
  rvState.kind = kind; rvState.active = true;
  let data;
  try { data = await rvFetchFrames(kind); }
  catch (e) { rvBail("Radar-API nicht erreichbar"); return; }
  if (!data.frames.length) {
    rvBail(kind === "satellite" ? "Satellit: keine Bilder verfuegbar"
                                : "Radar: keine Frames");
    return;
  }
  const opts = kind === "radar" ? "/2/1_1" : "/0/0_0";  // color/smooth bzw. 0/0
  rvState.host = data.host;
  rvState.opts = opts;
  rvState.frames = data.frames;
  // LAZY: nur den NEUESTEN Frame sofort laden. Alle 13 Frames vorzuladen
  // feuert ~300 Kachel-Requests als Burst - RainViewer antwortet mit 429
  // (Too Many Requests) und gerade der sichtbare Frame bleibt leer.
  // Aeltere Frames entstehen erst bei ihrem ersten Loop-Auftritt und liegen
  // danach im Browser-Cache.
  rvState.layers = data.frames.map(() => null);
  rvState.idx = rvState.frames.length - 1;
  rvShow(rvState.idx);
  rvTimestampEl().style.display = "flex";
  rvTogglePlay(true);
  // Frames alle 5 min auffrischen, solange aktiv
  rvState.refetch = setInterval(async () => {
    if (!rvState.active) return;
    try {
      const nd = await rvFetchFrames(kind);
      if (nd.frames.length && nd.frames.length !== rvState.frames.length) {
        rvStop(false); rvStart(kind);
      }
    } catch (e) { /* naechster Versuch kommt */ }
  }, 300000);
}

function rvLayerFor(i) {
  if (!rvState.layers[i]) {
    const f = rvState.frames[i];
    rvState.layers[i] = L.tileLayer(
      `${rvState.host}${f.path}/256/{z}/{x}/{y}${rvState.opts}.png`,
      // RainViewer Free liefert ab z8 nur "Zoom Level Not Supported"-Kacheln;
      // z7 ist die hoechste Stufe mit echten Daten, darueber skaliert Leaflet hoch.
      { opacity: 0, className: "rv-tile", zIndex: 350, maxNativeZoom: 7, maxZoom: 18 }
    ).addTo(map);
  }
  return rvState.layers[i];
}

function rvShow(i) {
  rvState.idx = i;                      // Loop-Position mitfuehren
  rvState.layers.forEach((l, j) => {
    if (l) l.setOpacity(j === i ? rvOpacity() : 0);
  });
  rvLayerFor(i).setOpacity(rvOpacity());   // lazy: Layer ggf. erst jetzt erzeugen
  const f = rvState.frames[i];
  const t = new Date(f.time * 1000);
  rvTimestampEl().style.display = "flex";   // Existenz sicherstellen
  const span = document.getElementById("rv-time");
  span.textContent =
    (f.time * 1000 > Date.now() ? "Nowcast " : "") +
    t.toLocaleTimeString("de-DE", {hour: "2-digit", minute: "2-digit"});
}

function rvOpacity() {
  return document.body.classList.contains("night") ? 0.45 : 0.75;
}

function rvTogglePlay(force) {
  const want = force === true ? true : !rvState.playing;
  rvState.playing = want;
  document.getElementById("rv-play").innerHTML = want ? "&#10074;&#10074;" : "&#9654;";
  clearTimeout(rvState.timer);
  clearInterval(rvState.timer);
  if (want) {
    // Self-rescheduling Timeout statt setInterval: robust gegen
    // Timer-Throttling (Frame-Wechsel erst nach Rendering des vorigen)
    const step = () => {
      rvShow((rvState.idx + 1) % rvState.frames.length);
      rvState.timer = setTimeout(step, 700);
    };
    rvState.timer = setTimeout(step, 700);
  }
}

function rvStop(hideTs = true) {
  rvState.active = false; rvState.playing = false;
  clearTimeout(rvState.timer); clearInterval(rvState.timer); clearInterval(rvState.refetch);
  rvState.layers.forEach(l => map.removeLayer(l));
  rvState.layers = []; rvState.frames = [];
  if (hideTs) rvTimestampEl().style.display = "none";
}

function rvBail(text) {
  rvStop();
  alert(text);
  // Control-Checkbox zuruecksetzen
  document.querySelectorAll(".leaflet-control-layers-selector").forEach(cb => {
    if (cb.checked && (cb.closest("label").textContent.includes("Radar") ||
                       cb.closest("label").textContent.includes("Satellit")))
      cb.checked = false;
  });
}

/* ---------- Regen-Icon-Raster (Open-Meteo, Overworld-Bildsprache) ----------
   Keine Farbflaechen: pro Gitterpunkt eine Wolke mit 0-3 Tropfen,
   Groesse/Fuellung nach mm; hohle Wolke = Regen absehbar; Blitz-Symbol,
   wo der Punkt in einer aktiven DWD-Gewitterwarnung liegt. */

function setRgHour(h) {
  rgHour = h;
  $("rg-slider").value = h;
  $("rg-label").textContent = h === 0 ? "Jetzt" : `+${h} h`;
  if (rgLastData) renderRainGrid(rgLastData);
}

 function rgTogglePlay() { 
  rgPlaying = !rgPlaying; 
  const btn = $("rg-play");
   if (rgPlaying) { 
    btn.textContent = "\u23f8";
     btn.title = "Regen-Verlauf pausieren";
      rgAdvance();
     } else { 
      btn.textContent = "\u25b6"; 
      btn.title = "Regen-Verlauf 0-6 h automatisch abspielen"; 
      clearTimeout(rgTimer);
       rgTimer = null;
       } 
      } 
      
      
      function rgAdvance() { 
        if (!rgPlaying) 
          return; 
        const next = (rgHour + 1) % 7;
         rgHour = next; 
         setRgHour(next); 
         $("rg-slider").value = next; 
         rgTimer = setTimeout(rgAdvance, 1500); 
        } 


function rgIconHtml(p, storm) {
  const mm = p.mm ?? 0, prob = p.prob ?? 0;
  let cls = "", drops = 0;
  if (mm > 5)        { cls = "rg-4"; drops = 3; }
  else if (mm > 2)   { cls = "rg-3"; drops = 3; }
  else if (mm > 0.5) { cls = "rg-2"; drops = 2; }
  else if (mm > 0)   { cls = "rg-1"; drops = 1; }
  else if (prob >= 30) cls = "rg-forecast";
  if (!cls) return null;
  const when = rgHour === 0 ? "jetzt" : `in +${rgHour} h`;
  let html = `<div class="rg-icon ${cls}" title="${when}: ${mm.toFixed(1)} mm \u00b7 `
    + `${prob ?? "?"}% Regenwahrscheinlichkeit">`;
  html += `<span class="rg-cloud"></span>`;
  for (let i = 1; i <= drops; i++) html += `<span class="rg-drop rg-d${i}"></span>`;
  if (storm) html += `<span class="rg-bolt">\u26a1</span>`;
  return html + "</div>";
}

function pointInRing(lat, lon, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    if ((yi > lat) !== (yj > lat) &&
        lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function renderRainGrid(data) {
  rainGridLayer.clearLayers();
  let shown = 0;
  for (const p of data.points) {
    // Zeitregler: Werte der gewaehlten Prognosestunde (Fallback: current)
    const src = (p.hours && p.hours[rgHour]) || p;
    const mm = src.mm ?? 0, prob = src.prob ?? 0;
    const storm = stormRings.some(r => pointInRing(p.lat, p.lon, r));
    const html = rgIconHtml({ ...p, mm, prob }, storm);
    if (!html) continue;
    shown++;
    L.marker([p.lat, p.lon], {
      icon: L.divIcon({ className: "", html, iconSize: [38, 34], iconAnchor: [19, 30] }),
      keyboard: false, zIndexOffset: -500,
    }).addTo(rainGridLayer);
  }
  console.debug(`[RegenIcons] ${shown}/${data.points.length} Gitterpunkte mit Icon (+${rgHour}h)`);
}

async function fetchRainGrid() {
  try {
    const b = map.getBounds();
    const bbox = [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]
      .map(v => v.toFixed(4)).join(",");
    const data = await api(`/api/rain-grid?bbox=${encodeURIComponent(bbox)}&zoom=${map.getZoom()}`);
    rgLastData = data;
    renderRainGrid(data);
  } catch (e) { console.warn("rain-grid offline:", e); }
}

/* ---------- Marker + Detail-Panel ---------- */
// Tropfen-Stufen ankoppelt an die TH.precip-Ampelgrenzen (0.1 / 1.0 mm) + Starkstufe
const RAIN_STEPS = [
  { max: 0.1, cls: "rain-1", label: "Nieselregen" },
  { max: 1.0, cls: "rain-2", label: "leichter Regen" },
  { max: 2.5, cls: "rain-3", label: "kräftiger Regen" },
  { max: Infinity, cls: "rain-4", label: "Starkregen" },
];
let lastRainMm = {};   // name -> precip_2h des vorherigen Refresh (fuer Regen-Puls)

function rainBadgeHtml(spot, isNew) {
  const mm = spot.precip_2h, prob = spot.rain_prob || 0;
  if (mm == null || mm <= 0) {
    if (prob >= 50)
      return `<div class="rain-badge rain-forecast" title="Regen absehbar (${prob}% innerhalb 4 h)"></div>`;
    return "";
  }
  const st = RAIN_STEPS.find(x => mm <= x.max);
  return `<div class="rain-badge ${st.cls}${isNew ? " rain-new" : ""}" title="${st.label}: ${mm.toFixed(1)} mm/2 h"></div>`;
}

function markerIcon(spot, rainNew) {
  const rating = spot.rating || "NA";
  const alertCls = (spot.radar_status || "").includes("Alert") ? " alert" : "";
  return L.divIcon({
    className: "",
    html: `<div class="spot-marker">
             <div class="spot-dot rating-${esc(rating)}${alertCls}"></div>
             ${rainBadgeHtml(spot, rainNew)}
             <div class="spot-label">${esc(spot.name)}</div>
           </div>`,
    iconSize: [46, 46], iconAnchor: [23, 23],
  });
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
      ${lpLine(s)}
    </div>
    <div class="grp mono"><b>Planeten &gt; 30°</b><span class="age">de421 · lokal</span></div>
    <div class="kv mono">${planetRows || row("Planeten", "keine Daten")}</div>
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

function gpsWatch() {
  if (!navigator.geolocation) { alert("Geolocation hier nicht verfügbar (HTTPS nötig)."); return; }
  $("btn-gps").textContent = "…";
  navigator.geolocation.getCurrentPosition(async (pos) => {
    try {
      const r = await api("/api/watch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: pos.coords.latitude, lon: pos.coords.longitude, hours: 2 }),
      });
      map.setView([pos.coords.latitude, pos.coords.longitude], 12);
      alert(`Live-Standort aktiv für 2 h:\n${r.name}\nRadar: ${r.radar_status}`);
      refresh();
    } catch (e) { alert("Watch fehlgeschlagen: " + e.message); }
    $("btn-gps").textContent = "\u25CE";
  }, (err) => {
    alert("GPS-Fehler: " + err.message);
    $("btn-gps").textContent = "\u25CE";
  }, { enableHighAccuracy: true, timeout: 15000 });
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
  $("panel-close").onclick = () => $("panel").classList.add("hidden");
  $("tab-now").onclick = () => showTab("now");
  $("tab-fc").onclick = () => showTab("fc");
  refresh();
  setInterval(refresh, REFRESH_MS);
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(e => console.warn("SW:", e));
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
function commsLog(text, severity) {
  const t = document.getElementById("comms-terminal");
  if (!t) return;
  const ts = new Date().toISOString().slice(11, 19);
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
