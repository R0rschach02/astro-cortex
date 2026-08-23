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

let map, markersLayer, warnLayer, lpLayer;
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
  map = L.map("map", { zoomControl: true, tap: true })
        .setView([49.54, 8.63], 10);

  L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
    maxZoom: 18,
    attribution: '&copy; OpenStreetMap, &copy; CARTO',
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

  // RainViewer: Dummy-Overlays nur fuer die Control, Logik via Events
  const rvRadarDummy = L.layerGroup();
  const rvSatDummy = L.layerGroup();
  L.control.layers(null, {
    "Lichtverschmutzung": lpLayer,
    "Unwetterwarnungen (DWD)": warnLayer,
    "Regenradar (RainViewer)": rvRadarDummy,
    "Wolken (Satellit)": rvSatDummy,
  }, { position: "bottomright", collapsed: true }).addTo(map);
  map.on("overlayadd", (e) => {
    if (e.name.includes("Regenradar")) rvStart("radar");
    if (e.name.includes("Satellit")) rvStart("satellite");
  });
  map.on("overlayremove", (e) => {
    if (e.name.includes("Regenradar") || e.name.includes("Satellit")) rvStop();
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
  const d = new Date();
  const today = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const tom = new Date(d.getTime() + 86400000);
  const tomorrow = `${tom.getFullYear()}-${String(tom.getMonth()+1).padStart(2,'0')}-${String(tom.getDate()).padStart(2,'0')}`;
  if (night === today) return "Heute Nacht";
  if (night === tomorrow) return "Morgen Nacht (+1)";
  return `Nacht auf ${esc(night.slice(8,10))}.${esc(night.slice(5,7))}. (+2)`;
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
  rvState.frames = data.frames;
  rvState.layers = data.frames.map(f => L.tileLayer(
    `${data.host}${f.path}/256/{z}/{x}/{y}${opts}.png`,
    { opacity: 0, className: "rv-tile", zIndex: 350, maxNativeZoom: 12 }
  ).addTo(map));
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

function rvShow(i) {
  rvState.idx = i;                      // Loop-Position mitfuehren
  rvState.layers.forEach((l, j) => l.setOpacity(j === i ? rvOpacity() : 0));
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

/* ---------- Marker + Detail-Panel ---------- */
function markerIcon(spot) {
  const rating = spot.rating || "NA";
  const alertCls = (spot.radar_status || "").includes("Alert") ? " alert" : "";
  return L.divIcon({
    className: "",
    html: `<div class="spot-marker">
             <div class="spot-dot rating-${esc(rating)}${alertCls}"></div>
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
    <div class="sub" style="margin-top:10px">
      Wolkenquelle: ${esc(s.clouds_source || "n/a")} &middot; ${isPlanet
        ? "Planetarisch: Seeing/Jetstream hart, Mond & Beschlag irrelevant"
        : "DSO: Beschlag hart (keine Tauheizung), Mond-Ampel = DSO-Eignung"}
    </div>`;
}

function renderSpots(data) {
  markersLayer.clearLayers();
  for (const s of data.spots) {
    const mk = L.marker([s.lat, s.lon], { icon: markerIcon(s) });
    mk.on("click", () => {
      currentSpot = s;
      $("panel").classList.remove("hidden");
      showTab("now");
      fetchBortle(s);
    });
    markersLayer.addLayer(mk);
  }
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
    renderSpots(data);
    // Warnungen nachladen (Layer nur, wenn aktiviert)
    try {
      const warns = await api("/api/warnings");
      warnLayer.addData({ type: "FeatureCollection",
                          features: warns.features.filter(f => f.properties.kind !== "other") });
    } catch (e) { console.warn("warnings offline:", e); }
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
