/* Haltebereik NL — statische viewer op vooraf berekende data (zie ovcatch/webexport.py) */
(() => {
  "use strict";

  const DATA = "data/";
  const $ = (id) => document.getElementById(id);
  const nf = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 1 });
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MODE_LABEL = { walk: "Lopen", bike: "Fietsen" };
  const MODE_ADJ = { walk: "lopend", bike: "fietsend" };
  const METRIC_LABEL = {
    inwoners: "Inwoners (geschat)", woningen: "Woningen", banen: "Arbeidsplaatsen*", voorzieningen: "Voorzieningen (OSM)", adressen: "Adressen",
    inw_0_15: "0–15 jaar", inw_15_25: "15–25 jaar", inw_65plus: "65+",
  };
  const MAIN = ["inwoners", "woningen", "banen", "voorzieningen"];
  const ISO_COLORS = ["#0b6e4f", "#1d4ed8", "#a21caf"];
  const PALETTE = ["#0b6e4f", "#c2410c", "#1d4ed8", "#a21caf", "#b45309", "#0e7490", "#be123c", "#4d7c0f", "#6d28d9", "#0f766e", "#9a3412", "#334155"];
  // kleurschalen: licht = licht→donker, donker = donker→licht (hoge waarden springen eruit)
  const RAMPS = {
    light: { inw: ["#e3f1ec", "#a8d8c2", "#62b393", "#2a8a68", "#0b5e44"], dep: ["#e0ecfb", "#a9c9f2", "#6a9fe0", "#3471c6", "#1b4a8f"] },
    dark: { inw: ["#1b3a31", "#22604b", "#2f8a67", "#4fb88a", "#a2e6c4"], dep: ["#1b2a44", "#22457a", "#2f64ad", "#5b90dd", "#b3d0ff"] },
  };
  const MAP_COLORS = {
    light: { panel: "#ffffff", ink: "#1b2128", line: "#c2410c", grey: "#7a7f87", accent: "#0b6e4f" },
    dark: { panel: "#161b21", ink: "#e6e9ed", line: "#f08a4b", grey: "#8a929c", accent: "#3fb68b" },
  };

  const S = {
    meta: null, stops: [], lines: [], mode: "walk", th: 1, colorBy: "inw", stop: null, line: null,
    isoCache: {}, breaks: {}, edit: false, theme: document.documentElement.getAttribute("data-theme") || "light",
  };

  const fmtTh = (t) => (t >= 1000 ? `${nf1.format(t / 1000)} km` : `${t} m`);
  const color = (i) => PALETTE[i % PALETTE.length];

  // ---------- data ----------
  // waarden per halte/lijn: per modus, per drempel, per kengetal (plat)
  function offset(mode, band) {
    let o = 0;
    for (const m of S.meta.modi) {
      if (m === mode) return (o + band) * S.meta.kengetallen.length;
      o += S.meta.drempels[m].length;
    }
    return 0;
  }
  function vals(row, mode, band) {
    const o = offset(mode, band);
    const out = {};
    S.meta.kengetallen.forEach((k, i) => { out[k] = row[o + i] || 0; });
    return out;
  }
  const inwKey = (mode, band) => `${mode}${band}`;
  const dataVersion = () => `${S.meta.gemaakt}/${S.meta.n_haltes}/${S.meta.n_lijnen}`;

  function decodePolyline(str) {
    const coords = [];
    let i = 0, lat = 0, lon = 0;
    while (i < str.length) {
      for (const which of [0, 1]) {
        let result = 0, shift = 0, b;
        do { b = str.charCodeAt(i++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
        const d = result & 1 ? ~(result >> 1) : result >> 1;
        if (which === 0) lat += d; else lon += d;
      }
      coords.push([lon / 1e5, lat / 1e5]);
    }
    return coords;
  }

  // ---------- thema ----------
  function setTheme(t) {
    S.theme = t;
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("ovcatch.theme", t); } catch (e) { /* privévenster: alleen deze sessie */ }
    applyMapTheme();
    if (S.meta) renderControls();
    if (window.OVCEditor && map.getSource("edit-iso")) window.OVCEditor.applyTheme();
  }
  const themeColors = () => MAP_COLORS[S.theme];

  function applyMapTheme() {
    if (!map.getLayer("brt")) return;
    const dark = S.theme === "dark";
    // PDOK heeft geen donkere achtergrondkaart: helderheid omkeren (min > max) en contrast dempen
    map.setPaintProperty("brt", "raster-brightness-min", dark ? 0.88 : 0);
    map.setPaintProperty("brt", "raster-brightness-max", dark ? 0.1 : 1);
    map.setPaintProperty("brt", "raster-contrast", dark ? -0.15 : 0);
    map.setPaintProperty("brt", "raster-saturation", dark ? -0.6 : 0);
    const c = themeColors();
    map.setPaintProperty("stops", "circle-stroke-color", c.panel);
    map.setPaintProperty("line", "line-color", c.line);
    map.setPaintProperty("line-stops", "circle-color", c.panel);
    map.setPaintProperty("line-stops", "circle-stroke-color", c.line);
    map.setPaintProperty("sel", "circle-stroke-color", c.ink);
    map.setPaintProperty("line-stops-lbl", "text-color", c.ink);
    map.setPaintProperty("line-stops-lbl", "text-halo-color", c.panel);
  }

  // ---------- kaart ----------
  const map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
      sources: {
        brt: {
          type: "raster",
          tiles: ["https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/grijs/EPSG:3857/{z}/{x}/{y}.png"],
          tileSize: 256, maxzoom: 19,
          attribution: "Kaart © Kadaster (PDOK BRT) · Data: BAG, CBS, OSM, OVapi",
        },
      },
      layers: [{ id: "brt", type: "raster", source: "brt" }],
    },
    bounds: [[3.3, 50.75], [7.25, 53.55]],
    fitBoundsOptions: { padding: 20 },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  const EMPTY = { type: "FeatureCollection", features: [] };
  const setData = (id, d) => { const s = map.getSource(id); if (s) s.setData(d || EMPTY); };

  map.on("load", async () => {
    for (const id of ["stops", "iso", "line", "line-stops", "sel"]) map.addSource(id, { type: "geojson", data: EMPTY });
    map.addLayer({ id: "iso-fill", type: "fill", source: "iso", paint: { "fill-color": ["get", "color"], "fill-opacity": ["case", ["get", "active"], 0.16, 0.0] } });
    map.addLayer({ id: "iso-line", type: "line", source: "iso", paint: { "line-color": ["get", "color"], "line-width": ["case", ["get", "active"], 2, 1], "line-opacity": 0.85, "line-dasharray": [3, 1.5] } });
    map.addLayer({ id: "line", type: "line", source: "line", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-width": 4, "line-opacity": 0.85 } });
    map.addLayer({ id: "stops", type: "circle", source: "stops", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 1.4, 11, 3.2, 15, 7],
      "circle-color": "#ccc",
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9, 0, 12, 1],
    } });
    map.addLayer({ id: "line-stops", type: "circle", source: "line-stops", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 3, 14, 7], "circle-stroke-width": 2.5 } });
    map.addLayer({ id: "sel", type: "circle", source: "sel", paint: { "circle-radius": 9, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 3 } });
    map.addLayer({ id: "line-stops-lbl", type: "symbol", source: "line-stops", minzoom: 12.5,
      layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"], "text-size": 11, "text-offset": [0, 1.1], "text-anchor": "top", "text-optional": true },
      paint: { "text-halo-width": 1.5 } });
    applyMapTheme();

    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
    for (const layer of ["stops", "line-stops"]) {
      map.on("mouseenter", layer, (e) => {
        map.getCanvas().style.cursor = "pointer";
        const f = e.features[0];
        const s = S.stops[f.properties.i];
        const v = vals(s[3], S.mode, S.th);
        const dep = s[6] ? ` · ${nf.format(s[6][0])} vertrekken/werkdag` : "";
        popup.setLngLat(f.geometry.coordinates).setHTML(`<b>${esc(s[0])}</b><br>${nf.format(v.inwoners)} inwoners binnen ${fmtTh(S.meta.drempels[S.mode][S.th])}${dep}`).addTo(map);
      });
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; popup.remove(); });
      map.on("click", layer, (e) => { if (!S.edit) selectStop(e.features[0].properties.i, true); });
    }
    map.on("click", (e) => {
      if (S.edit) { window.OVCEditor.onMapClick(e); return; }
      // klik naast een halte = halte deselecteren
      if (S.stop != null && !map.queryRenderedFeatures(e.point, { layers: ["stops", "line-stops"] }).length) clearStop();
    });
    window.OVCEditor.init({
      S, map, setData, decodePolyline, isoTile, renderDetail, esc, nf, nf1, fmtTh, MODE_ADJ, MODE_LABEL, METRIC_LABEL, DATA,
      color, vals, agesHtml, themeColors, dataVersion, selectLine, setHashState,
    });

    try {
      await load();
    } catch (e) {
      $("loading").textContent = `Data laden mislukt: ${e.message}`;
      $("loading").classList.add("err");
    }
  });

  async function getJSON(path) {
    const r = await fetch(DATA + path);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }

  function quantiles(xs) {
    xs = xs.filter((x) => x > 0).sort((a, c) => a - c);
    return [0.2, 0.4, 0.6, 0.8].map((q) => xs[Math.floor(q * (xs.length - 1))] || 0);
  }

  async function load() {
    const [meta, stops, lines] = await Promise.all([getJSON("meta.json"), getJSON("stops.json"), getJSON("lines.json")]);
    S.meta = meta; S.stops = stops.rows; S.lines = lines.rows;
    const ref = meta.referentiedag ? ` · dienstregeling ${new Date(meta.referentiedag).toLocaleDateString("nl-NL", { weekday: "short", day: "numeric", month: "short" })}` : "";
    $("meta").textContent = `${nf.format(meta.n_haltes)} haltes · ${nf.format(meta.n_lijnen)} lijnen${ref}`;

    for (const m of meta.modi) meta.drempels[m].forEach((_, b) => { S.breaks[inwKey(m, b)] = quantiles(S.stops.map((s) => vals(s[3], m, b).inwoners)); });
    S.breaks.dep = quantiles(S.stops.map((s) => (s[6] ? s[6][0] : 0)));
    if (!S.stops.some((s) => s[6])) $("colorSeg").closest(".controls").classList.add("hidden");
    setData("stops", {
      type: "FeatureCollection",
      features: S.stops.map((s, i) => {
        const p = { i, dep: s[6] ? s[6][0] : 0 };
        for (const m of meta.modi) meta.drempels[m].forEach((_, b) => { p[inwKey(m, b)] = vals(s[3], m, b).inwoners; });
        return { type: "Feature", properties: p, geometry: { type: "Point", coordinates: [s[1], s[2]] } };
      }),
    });
    renderControls();
    renderDetail();
    $("loading").style.display = "none";
    $("search").oninput = search;
    $("search").onkeydown = searchKeys;
    window.addEventListener("hashchange", fromHash);
    await fromHash();
  }

  // ---------- bediening ----------
  function renderControls() {
    $("modeSeg").innerHTML = S.meta.modi.map((m) => `<button data-mode="${m}" class="${m === S.mode ? "active" : ""}">${MODE_LABEL[m] || m}</button>`).join("");
    const ths = S.meta.drempels[S.mode];
    if (S.th >= ths.length) S.th = ths.length - 1;
    $("thSeg").innerHTML = ths.map((t, b) => `<button data-th="${b}" class="${b === S.th ? "active" : ""}">${fmtTh(t)}</button>`).join("");
    document.querySelectorAll("#colorSeg button").forEach((b) => b.classList.toggle("active", b.dataset.color === S.colorBy));
    const ramp = RAMPS[S.theme][S.colorBy];
    const key = S.colorBy === "dep" ? "dep" : inwKey(S.mode, S.th);
    const br = S.colorBy === "dep" ? S.breaks.dep : S.breaks[key];
    map.setPaintProperty("stops", "circle-color", ["step", ["get", key], ramp[0], br[0], ramp[1], br[1], ramp[2], br[2], ramp[3], br[3], ramp[4]]);
    const title = S.colorBy === "dep" ? "Busvertrekken per werkdag (per halte)" : `Inwoners binnen ${fmtTh(ths[S.th])} ${MODE_ADJ[S.mode]} (per halte)`;
    $("legend").innerHTML = `${title}
      <div class="bar">${ramp.map((c) => `<span style="background:${c}"></span>`).join("")}</div>
      <div class="ticks"><span>0</span>${br.map((x) => `<span>${nf.format(x)}</span>`).join("")}<span>meer</span></div>`;
  }

  function refresh() {
    renderControls();
    renderDetail();
    if (S.edit) window.OVCEditor.renderIso(); else drawIso();
  }

  $("modeSeg").onclick = (e) => {
    const b = e.target.closest("button[data-mode]"); if (!b || !S.meta) return;
    S.mode = b.dataset.mode; S.th = Math.min(S.th, S.meta.drempels[S.mode].length - 1); refresh();
  };
  $("thSeg").onclick = (e) => {
    const b = e.target.closest("button[data-th]"); if (!b || !S.meta) return;
    S.th = Number(b.dataset.th); refresh();
  };
  $("colorSeg").onclick = (e) => {
    const b = e.target.closest("button[data-color]"); if (!b || !S.meta) return;
    S.colorBy = b.dataset.color; renderControls();
  };
  $("themeBtn").onclick = () => setTheme(S.theme === "dark" ? "light" : "dark");
  $("aboutBtn").onclick = () => $("about").classList.remove("hidden");
  $("aboutClose").onclick = () => $("about").classList.add("hidden");
  $("about").onclick = (e) => { if (e.target.id === "about") $("about").classList.add("hidden"); };
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("about").classList.contains("hidden")) { $("about").classList.add("hidden"); return; }
    if (S.edit || e.target.tagName === "INPUT") return;
    if (S.stop != null) clearStop(); else if (S.line != null) clearLine();
  });

  // ---------- zoeken ----------
  let focusIdx = -1;
  function search() {
    const q = $("search").value.trim().toLowerCase();
    focusIdx = -1;
    if (q.length < 1) { $("results").innerHTML = ""; return; }
    const words = q.split(/\s+/);
    const res = words.map((w) => new RegExp(`(^|[^\\p{L}\\p{N}])${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "u"));
    const wordStart = (hay) => res.every((r) => r.test(hay));
    const lines = [];
    S.lines.forEach((l, i) => {
      const exact = l[0].toLowerCase() === q;
      if (exact || wordStart(`${l[0]} ${l[1]} ${l[2]}`.toLowerCase())) lines.push([exact ? 0 : 1, i]);
    });
    lines.sort((a, b) => a[0] - b[0] || S.lines[a[1]][0].localeCompare(S.lines[b[1]][0], "nl", { numeric: true }));
    const stops = [];
    if (q.length >= 3) S.stops.forEach((s, i) => { if (wordStart(s[0].toLowerCase())) stops.push(i); });
    $("results").innerHTML =
      lines.slice(0, 30).map(([, i]) => { const l = S.lines[i]; return `<li data-line="${i}"><span class="num">${esc(l[0] || "?")}</span><span class="nm" title="${esc(l[1])}">${esc(l[2])} · ${esc(l[1])}</span><span class="kind">lijn</span></li>`; }).join("") +
      stops.slice(0, 20).map((i) => `<li data-stop="${i}"><span class="nm" style="color:var(--ink)">${esc(S.stops[i][0])}</span><span class="kind">halte</span></li>`).join("");
  }
  function pick(li) {
    if (!li) return;
    if (li.dataset.line) selectLine(Number(li.dataset.line), true);
    else selectStop(Number(li.dataset.stop), true, true);
    $("results").innerHTML = "";
    $("search").blur();
  }
  $("results").onclick = (e) => pick(e.target.closest("li"));
  function searchKeys(e) {
    const items = [...$("results").querySelectorAll("li")];
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!items.length) return;
      focusIdx = (focusIdx + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach((li, i) => li.classList.toggle("focus", i === focusIdx));
      items[focusIdx].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      pick(items[Math.max(0, focusIdx)]);
    } else if (e.key === "Escape") {
      $("search").value = ""; $("results").innerHTML = "";
    }
  }
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) $("results").innerHTML = ""; });

  // ---------- selectie ----------
  function selectStop(i, setHash, fly) {
    if (S.edit) window.OVCEditor.stop();
    S.stop = i;
    const s = S.stops[i];
    setData("sel", { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [s[1], s[2]] } }] });
    if (fly) map.flyTo({ center: [s[1], s[2]], zoom: Math.max(map.getZoom(), 14) });
    if (setHash) setHashState();
    renderDetail();
    drawIso();
  }

  function selectLine(i, setHash) {
    if (S.edit) window.OVCEditor.stop();
    S.line = i; S.stop = null;
    const l = S.lines[i];
    const coords = decodePolyline(l[5]);
    setData("line", { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } }] });
    setData("line-stops", { type: "FeatureCollection", features: l[3].map((a) => ({ type: "Feature", properties: { i: a, name: S.stops[a][0] }, geometry: { type: "Point", coordinates: [S.stops[a][1], S.stops[a][2]] } })) });
    setData("sel", null);
    const b = new maplibregl.LngLatBounds(coords[0], coords[0]);
    coords.forEach((c) => b.extend(c));
    map.fitBounds(b, { padding: 50, duration: 700 });
    if (setHash) setHashState();
    renderDetail();
    drawIso();
  }

  function clearStop() {
    S.stop = null;
    setData("sel", null); setData("iso", null);
    setHashState(); renderDetail();
  }

  function clearLine() {
    S.line = null;
    setData("line", null); setData("line-stops", null);
    setHashState(); renderDetail();
  }

  function setHashState(extra) {
    const parts = [];
    if (S.line != null && !extra) parts.push(`lijn=${S.line}`);
    if (S.stop != null && !extra) parts.push(`halte=${S.stop}`);
    if (extra) parts.push(extra);
    history.replaceState(null, "", parts.length ? `#${parts.join("&")}` : location.pathname + location.search);
  }

  async function fromHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    const v = h.get("v");
    if (v) { await window.OVCEditor.fromLink(v); return; }
    const l = h.get("lijn"), s = h.get("halte");
    if (l != null && S.lines[+l]) selectLine(+l, false);
    if (s != null && S.stops[+s]) selectStop(+s, false, l == null);
  }

  // ---------- isochronen ----------
  function isoTile(key) {
    if (!S.isoCache[key]) {
      S.isoCache[key] = getJSON(`iso/${key}.json`);
      S.isoCache[key].catch(() => delete S.isoCache[key]);
    }
    return S.isoCache[key];
  }

  async function drawIso() {
    if (S.stop == null || S.edit) { setData("iso", null); return; }
    const i = S.stop, key = S.stops[i][5];
    try {
      const tile = await isoTile(key);
      if (S.stop !== i) return;
      const entry = (tile[String(i)] || {})[S.mode] || [];
      const features = entry.map((polys, b) => ({
        type: "Feature",
        properties: { color: ISO_COLORS[b % ISO_COLORS.length], active: b === S.th },
        geometry: { type: "MultiPolygon", coordinates: polys.map((rings) => rings.map(decodePolyline)) },
      })).reverse();
      setData("iso", { type: "FeatureCollection", features });
    } catch (e) {
      setData("iso", null);
    }
  }

  // ---------- paneel ----------
  function kpis(v, note, keys = MAIN) {
    return `<div class="kpis">${keys.map((k) =>
      `<div class="kpi"><div class="lbl">${METRIC_LABEL[k] || k}</div><div class="val">${nf.format(v[k])}</div>${note ? `<div class="cmp">${note}</div>` : ""}</div>`).join("")}</div>`;
  }

  // leeftijdsopbouw: 0–15, 15–25, 25–65 (rest), 65+
  function agesHtml(v) {
    if (!("inw_65plus" in v) || !v.inwoners) return "";
    const a = v.inw_0_15, b = v.inw_15_25, d = v.inw_65plus, c = Math.max(0, v.inwoners - a - b - d);
    const parts = [["0–15", a, "--age-1"], ["15–25", b, "--age-2"], ["25–65", c, "--age-3"], ["65+", d, "--age-4"]];
    const tot = a + b + c + d || 1;
    return `<div class="ages"><div class="bar">${parts.map(([, x, col]) => `<span style="width:${(100 * x) / tot}%;background:var(${col})"></span>`).join("")}</div>
      <div class="keys">${parts.map(([l, x, col]) => `<span><i style="background:var(${col})"></i>${l}<b>${nf.format(x)}</b></span>`).join("")}</div></div>`;
  }

  function thTable(row) {
    const ks = MAIN;
    let h = `<table><thead><tr><th></th>${ks.map((k) => `<th>${(METRIC_LABEL[k] || k).replace(" (geschat)", "").replace(" (OSM)", "").replace("Arbeidsplaatsen*", "Banen*")}</th>`).join("")}<th>65+</th></tr></thead><tbody>`;
    for (const m of S.meta.modi) {
      S.meta.drempels[m].forEach((t, b) => {
        const v = vals(row, m, b);
        h += `<tr class="${m === S.mode && b === S.th ? "sel" : ""}"><td>${MODE_LABEL[m]} ${fmtTh(t)}</td>${ks.map((k) => `<td>${nf.format(v[k])}</td>`).join("")}<td>${nf.format(v.inw_65plus)}</td></tr>`;
      });
    }
    return h + "</tbody></table>";
  }

  function lineChips(idx) {
    return `<div class="chips">${idx.slice(0, 60).map((li) => `<button class="chip" data-line="${li}" title="${esc(S.lines[li][2])} · ${esc(S.lines[li][1])}">${esc(S.lines[li][0] || "?")}</button>`).join("")}</div>`;
  }

  function freqStop(s) {
    if (!s[6]) return "";
    return `<div class="sec">Frequentie (werkdag)</div><div class="freq">
      <div class="kpi"><div class="lbl">Busvertrekken per dag</div><div class="val">${nf.format(s[6][0])}</div></div>
      <div class="kpi"><div class="lbl">In de spits (07–09)</div><div class="val">${nf1.format(s[6][1])}<span class="muted" style="font-size:12px;font-weight:400"> /uur</span></div></div></div>`;
  }
  function freqLine(l) {
    if (!l[7]) return "";
    if (!l[7][0]) return `<p class="note">Geen vaste ritten op de referentiedag (bijv. alleen op afroep of in het weekend).</p>`;
    return `<div class="sec">Frequentie (werkdag)</div><div class="freq">
      <div class="kpi"><div class="lbl">Ritten per dag (beide richtingen)</div><div class="val">${nf.format(l[7][0])}</div></div>
      <div class="kpi"><div class="lbl">Spits (07–09), per richting</div><div class="val">${nf1.format(l[7][1])}<span class="muted" style="font-size:12px;font-weight:400"> /uur</span></div></div></div>`;
  }

  function renderDetail() {
    const el = $("detail");
    if (!S.meta) return;
    if (S.edit) { window.OVCEditor.render(el); return; }
    const t = S.meta.drempels[S.mode][S.th];
    const within = `binnen ${fmtTh(t)} ${MODE_ADJ[S.mode]}`;
    let h = "";
    if (S.line != null) {
      const l = S.lines[S.line];
      const tot = vals(l[4], S.mode, S.th);
      const bruto = l[3].reduce((a, s) => a + vals(S.stops[s][3], S.mode, S.th).inwoners, 0);
      h += `<div class="card">
        <div class="card-h"><div><h2>Lijn ${esc(l[0])}</h2><div class="meta">${esc(l[2])} · ${esc(l[1])}</div></div>
          <button class="close" id="closeLine" title="Lijn sluiten (Esc)" aria-label="Lijn sluiten">×</button></div>
        <div class="row"><button id="editLine" class="primary" style="flex:1">Bewerken: variant maken</button></div>
        <div class="sec">Bereik ${within} · uniek</div>
        ${kpis(tot)}
        <div class="sec">Leeftijd</div>${agesHtml(tot)}
        ${freqLine(l)}
        <p class="note">${l[3].length} haltes · som per halte (met overlap) ${nf.format(bruto)} inwoners, uniek ${nf.format(tot.inwoners)}.</p>
        <details><summary>Per halte (${within}, bruto)</summary>
        <table><thead><tr><th>Halte</th><th>Inwoners</th><th>65+</th><th>Banen*</th>${S.stops[0][6] ? "<th>Vertr./dag</th>" : ""}</tr></thead><tbody>
        ${l[3].map((a, n) => { const s = S.stops[a], v = vals(s[3], S.mode, S.th); return `<tr class="click${a === S.stop ? " sel" : ""}" data-stop="${a}"><td>${n + 1}. ${esc(s[0])}</td><td>${nf.format(v.inwoners)}</td><td>${nf.format(v.inw_65plus)}</td><td>${nf.format(v.banen)}</td>${s[6] ? `<td>${nf.format(s[6][0])}</td>` : ""}</tr>`; }).join("")}
        </tbody></table></details>
        <details><summary>Alle afstanden (lijn, uniek)</summary>${thTable(l[4])}</details>
      </div>`;
    }
    if (S.stop != null) {
      const s = S.stops[S.stop];
      const v = vals(s[3], S.mode, S.th);
      h += `<div class="card">
        <div class="card-h"><div><h2>${esc(s[0])}</h2><div class="meta">Halte · ${s[4].length} buslijn${s[4].length === 1 ? "" : "en"}</div></div>
          <button class="close" id="closeStop" title="Halte sluiten (Esc of klik naast een halte)" aria-label="Halte sluiten">×</button></div>
        <div class="sec">Bereik ${within}</div>
        ${kpis(v)}
        <div class="sec">Leeftijd</div>${agesHtml(v)}
        ${freqStop(s)}
        <div class="sec">Buslijnen</div>${s[4].length ? lineChips(s[4]) : '<p class="note">Alleen bediend door een nevenpatroon van een lijn.</p>'}
        <details><summary>Alle afstanden</summary>${thTable(s[3])}</details>
        <p class="note">Op de kaart: bereik ${MODE_ADJ[S.mode]} over het netwerk; de gekozen afstand is ingekleurd.</p>
      </div>`;
    }
    if (!h) {
      h = `<div class="empty">
        <p><b>Zo werkt het</b></p>
        <ol>
          <li>Klik op een halte, of zoek een lijn of halte hierboven.</li>
          <li>Kies lopen of fietsen en een afstand: alle cijfers passen zich aan.</li>
          <li>Bij een lijn: <b>Bewerken</b> om haltes te verplaatsen en het effect te zien, en je variant te delen.</li>
        </ol>
        <p class="note">Haltes zijn gekleurd naar inwoners binnen bereik of naar het aantal busvertrekken. Uitleg en bronnen: ⓘ rechtsboven.</p>
      </div>`;
    }
    h += `<p class="note">* Arbeidsplaatsen: ruwe schatting uit m² BAG-gebruiksoppervlak.</p>`;
    el.innerHTML = h;
    const cl = $("closeLine"); if (cl) cl.onclick = clearLine;
    const cs = $("closeStop"); if (cs) cs.onclick = clearStop;
    const ed = $("editLine"); if (ed) ed.onclick = () => window.OVCEditor.start(S.line);
    el.querySelectorAll("[data-line]").forEach((b) => (b.onclick = () => selectLine(Number(b.dataset.line), true)));
    el.querySelectorAll("tr[data-stop]").forEach((r) => (r.onclick = () => selectStop(Number(r.dataset.stop), true, true)));
  }
})();
