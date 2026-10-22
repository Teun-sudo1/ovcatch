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
  const METRIC_LABEL = { inwoners: "Inwoners (geschat)", woningen: "Woningen", banen: "Arbeidsplaatsen*", voorzieningen: "Voorzieningen (OSM)", adressen: "Adressen" };
  const RAMP = ["#e3f1ec", "#a8d8c2", "#62b393", "#2a8a68", "#0b5e44"];
  const ISO_COLORS = ["#0b6e4f", "#1d4ed8", "#a21caf"];
  const PALETTE = ["#0b6e4f", "#c2410c", "#1d4ed8", "#a21caf", "#b45309", "#0e7490", "#be123c", "#4d7c0f", "#6d28d9", "#0f766e", "#9a3412", "#334155"];

  const S = { meta: null, stops: [], lines: [], mode: "walk", th: 1, stop: null, line: null, isoCache: {}, breaks: {}, edit: false };

  const fmtTh = (t) => (t >= 1000 ? `${nf1.format(t / 1000)} km` : `${t} m`);

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
    S.meta.kengetallen.forEach((k, i) => { out[k] = row[o + i]; });
    return out;
  }
  const inwKey = (mode, band) => `${mode}${band}`;

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
    map.addLayer({ id: "line", type: "line", source: "line", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#c2410c", "line-width": 4, "line-opacity": 0.85 } });
    map.addLayer({ id: "stops", type: "circle", source: "stops", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 1.4, 11, 3.2, 15, 7],
      "circle-color": "#ccc", "circle-stroke-color": "#fff",
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9, 0, 12, 1],
    } });
    map.addLayer({ id: "line-stops", type: "circle", source: "line-stops", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 3, 14, 7],
      "circle-color": "#fff", "circle-stroke-color": "#c2410c", "circle-stroke-width": 2.5 } });
    map.addLayer({ id: "sel", type: "circle", source: "sel", paint: {
      "circle-radius": 9, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#1d232a", "circle-stroke-width": 3 } });
    map.addLayer({ id: "line-stops-lbl", type: "symbol", source: "line-stops", minzoom: 12.5,
      layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"], "text-size": 11, "text-offset": [0, 1.1], "text-anchor": "top", "text-optional": true },
      paint: { "text-color": "#333", "text-halo-color": "#fff", "text-halo-width": 1.5 } });

    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
    for (const layer of ["stops", "line-stops"]) {
      map.on("mouseenter", layer, (e) => {
        map.getCanvas().style.cursor = "pointer";
        const f = e.features[0];
        const s = S.stops[f.properties.i];
        const v = vals(s[3], S.mode, S.th);
        popup.setLngLat(f.geometry.coordinates).setHTML(`<b>${esc(s[0])}</b><br>${nf.format(v.inwoners)} inwoners binnen ${fmtTh(S.meta.drempels[S.mode][S.th])} ${MODE_ADJ[S.mode]}`).addTo(map);
      });
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; popup.remove(); });
      map.on("click", layer, (e) => { if (!S.edit) selectStop(e.features[0].properties.i, true); });
    }
    map.on("click", (e) => {
      if (S.edit) { window.OVCEditor.onMapClick(e); return; }
      // klik naast een halte = halte deselecteren
      if (S.stop != null && !map.queryRenderedFeatures(e.point, { layers: ["stops", "line-stops"] }).length) clearStop();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || S.edit || e.target.tagName === "INPUT") return;
      if (S.stop != null) clearStop(); else if (S.line != null) clearLine();
    });
    window.OVCEditor.init({
      S, map, setData, decodePolyline, isoTile, renderDetail, esc, nf, nf1, fmtTh, MODE_ADJ, MODE_LABEL, DATA,
      color: (i) => PALETTE[i % PALETTE.length],
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

  async function load() {
    const [meta, stops, lines] = await Promise.all([getJSON("meta.json"), getJSON("stops.json"), getJSON("lines.json")]);
    S.meta = meta; S.stops = stops.rows; S.lines = lines.rows;
    const d = meta.disaggregatie && meta.disaggregatie.pop_estimated_total;
    $("meta").textContent = `${nf.format(meta.n_haltes)} haltelocaties · ${nf.format(meta.n_lijnen)} buslijnen · gemaakt ${meta.gemaakt}${d ? ` · ${nf.format(d)} inwoners verdeeld` : ""}`;

    // kleurgrenzen per modus/drempel: kwantielen van haltes met inwoners
    for (const m of meta.modi) {
      meta.drempels[m].forEach((_, b) => {
        const xs = S.stops.map((s) => vals(s[3], m, b).inwoners).filter((x) => x > 0).sort((a, c) => a - c);
        S.breaks[inwKey(m, b)] = [0.2, 0.4, 0.6, 0.8].map((q) => xs[Math.floor(q * (xs.length - 1))] || 0);
      });
    }
    setData("stops", {
      type: "FeatureCollection",
      features: S.stops.map((s, i) => {
        const p = { i };
        for (const m of meta.modi) meta.drempels[m].forEach((_, b) => { p[inwKey(m, b)] = vals(s[3], m, b).inwoners; });
        return { type: "Feature", properties: p, geometry: { type: "Point", coordinates: [s[1], s[2]] } };
      }),
    });
    renderControls();
    $("loading").classList.add("hidden");
    $("loading").style.display = "none";
    $("search").oninput = search;
    window.addEventListener("hashchange", fromHash);
    fromHash();
  }

  // ---------- bediening ----------
  function renderControls() {
    $("modeSeg").innerHTML = S.meta.modi.map((m) => `<button data-mode="${m}" class="${m === S.mode ? "active" : ""}">${MODE_LABEL[m] || m}</button>`).join("");
    const ths = S.meta.drempels[S.mode];
    if (S.th >= ths.length) S.th = ths.length - 1;
    $("thSeg").innerHTML = ths.map((t, b) => `<button data-th="${b}" class="${b === S.th ? "active" : ""}">${fmtTh(t)}</button>`).join("");
    $("modeSeg").onclick = (e) => {
      const b = e.target.closest("button[data-mode]"); if (!b) return;
      S.mode = b.dataset.mode; S.th = Math.min(S.th, S.meta.drempels[S.mode].length - 1); refresh();
    };
    $("thSeg").onclick = (e) => {
      const b = e.target.closest("button[data-th]"); if (!b) return;
      S.th = Number(b.dataset.th); refresh();
    };
    const key = inwKey(S.mode, S.th), br = S.breaks[key];
    map.setPaintProperty("stops", "circle-color", ["step", ["get", key], RAMP[0], br[0], RAMP[1], br[1], RAMP[2], br[2], RAMP[3], br[3], RAMP[4]]);
    $("legend").innerHTML = `Inwoners binnen ${fmtTh(ths[S.th])} ${MODE_ADJ[S.mode]} (per halte)
      <div class="bar">${RAMP.map((c) => `<span style="background:${c}"></span>`).join("")}</div>
      <div class="ticks"><span>0</span>${br.map((x) => `<span>${nf.format(x)}</span>`).join("")}<span>meer</span></div>`;
  }

  function refresh() {
    renderControls();
    renderDetail();
    if (S.edit) window.OVCEditor.renderIso(); else drawIso();
  }

  function search() {
    const q = $("search").value.trim().toLowerCase();
    if (q.length < 1) { $("results").innerHTML = ""; return; }
    const words = q.split(/\s+/);
    const wordStart = (hay) => words.every((w) => new RegExp(`(^|[^\\p{L}\\p{N}])${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "u").test(hay));
    const lines = [];
    S.lines.forEach((l, i) => {
      const exact = l[0].toLowerCase() === q;
      if (exact || wordStart(`${l[0]} ${l[1]} ${l[2]}`.toLowerCase())) lines.push([exact ? 0 : 1, i]);
    });
    lines.sort((a, b) => a[0] - b[0] || S.lines[a[1]][0].localeCompare(S.lines[b[1]][0], "nl", { numeric: true }));
    const stops = [];
    if (q.length >= 3) S.stops.forEach((s, i) => { if (wordStart(s[0].toLowerCase())) stops.push(i); });
    $("results").innerHTML =
      lines.slice(0, 40).map(([, i]) => { const l = S.lines[i]; return `<li data-line="${i}"><span class="num">${esc(l[0] || "?")}</span><span class="nm" title="${esc(l[1])}">${esc(l[2])} · ${esc(l[1])}</span><span class="kind">lijn</span></li>`; }).join("") +
      stops.slice(0, 25).map((i) => `<li data-stop="${i}"><span class="nm" style="color:var(--ink)">${esc(S.stops[i][0])}</span><span class="kind">halte</span></li>`).join("");
    $("results").onclick = (e) => {
      const li = e.target.closest("li"); if (!li) return;
      if (li.dataset.line) selectLine(Number(li.dataset.line), true);
      else selectStop(Number(li.dataset.stop), true, true);
      $("results").innerHTML = "";
    };
  }

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

  function setHashState() {
    const parts = [];
    if (S.line != null) parts.push(`lijn=${S.line}`);
    if (S.stop != null) parts.push(`halte=${S.stop}`);
    history.replaceState(null, "", parts.length ? `#${parts.join("&")}` : location.pathname);
  }

  function fromHash() {
    const h = new URLSearchParams(location.hash.slice(1));
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
  function kpis(v, note) {
    return `<div class="kpis">${S.meta.kengetallen.filter((k) => k !== "adressen").map((k) =>
      `<div class="kpi"><div class="lbl">${METRIC_LABEL[k] || k}</div><div class="val">${nf.format(v[k])}</div><div class="cmp">${note || "&nbsp;"}</div></div>`).join("")}</div>`;
  }

  function thTable(row) {
    const ks = S.meta.kengetallen.filter((k) => k !== "adressen");
    let h = `<table><thead><tr><th></th>${ks.map((k) => `<th>${(METRIC_LABEL[k] || k).replace(" (geschat)", "").replace(" (OSM)", "")}</th>`).join("")}</tr></thead><tbody>`;
    for (const m of S.meta.modi) {
      S.meta.drempels[m].forEach((t, b) => {
        const v = vals(row, m, b);
        h += `<tr class="${m === S.mode && b === S.th ? "sel" : ""}"><td>${MODE_LABEL[m]} ${fmtTh(t)}</td>${ks.map((k) => `<td>${nf.format(v[k])}</td>`).join("")}</tr>`;
      });
    }
    return h + "</tbody></table>";
  }

  function lineChips(idx) {
    return `<div class="chips">${idx.slice(0, 60).map((li) => `<button class="chip" data-line="${li}" title="${esc(S.lines[li][2])} · ${esc(S.lines[li][1])}">${esc(S.lines[li][0] || "?")}</button>`).join("")}</div>`;
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
      h += `<button class="back" id="closeLine">× Lijn sluiten</button>
        <h2>Lijn ${esc(l[0])}</h2><div class="muted">${esc(l[2])} · ${esc(l[1])}</div>
        <div class="row"><button id="editLine" class="primary">Bewerken: variant maken</button></div>
        <div class="res-h">Lijntotaal ${within} (uniek)</div>
        ${kpis(tot)}
        <p class="note">${l[3].length} haltes. Som per halte (bruto, met overlap): ${nf.format(bruto)} inwoners; uniek: ${nf.format(tot.inwoners)}.</p>
        <div class="res-h">Per halte ${within} (bruto)</div>
        <table><thead><tr><th>Halte</th><th>Inwoners</th><th>Woningen</th><th>Banen*</th><th>Voorz.</th></tr></thead><tbody>
        ${l[3].map((a, n) => { const v = vals(S.stops[a][3], S.mode, S.th); return `<tr class="click${a === S.stop ? " sel" : ""}" data-stop="${a}"><td>${n + 1}. ${esc(S.stops[a][0])}</td><td>${nf.format(v.inwoners)}</td><td>${nf.format(v.woningen)}</td><td>${nf.format(v.banen)}</td><td>${nf.format(v.voorzieningen)}</td></tr>`; }).join("")}
        </tbody></table>`;
    }
    if (S.stop != null) {
      const s = S.stops[S.stop];
      h += `${S.line != null ? '<div class="res-h" style="margin-top:22px">Geselecteerde halte</div>' : ""}
        <button class="back" id="closeStop" title="Of klik naast een halte op de kaart, of druk Esc">× Halte sluiten</button>
        <h2>${esc(s[0])}</h2>
        ${kpis(vals(s[3], S.mode, S.th), within)}
        <div class="res-h">Alle afstanden</div>${thTable(s[3])}
        <div class="res-h">Buslijnen bij deze halte (${s[4].length})</div>${s[4].length ? lineChips(s[4]) : '<p class="note">Geen lijn met dit als vaste halte in het hoofdpatroon.</p>'}
        <p class="note">Op de kaart: bereik ${MODE_ADJ[S.mode]} over het netwerk; de gekozen afstand is ingekleurd.</p>`;
    }
    if (!h) h = `<p class="muted">Klik op een halte op de kaart of zoek een lijn. Haltes zijn gekleurd naar het aantal inwoners binnen de gekozen afstand.</p>`;
    h += `<p class="note">* Ruwe schatting: m² BAG-gebruiksoppervlak ÷ aanname m² per arbeidsplaats.</p>`;
    el.innerHTML = h;
    const cl = $("closeLine"); if (cl) cl.onclick = clearLine;
    const cs = $("closeStop"); if (cs) cs.onclick = clearStop;
    const ed = $("editLine"); if (ed) ed.onclick = () => window.OVCEditor.start(S.line);
    el.querySelectorAll("[data-line]").forEach((b) => (b.onclick = () => selectLine(Number(b.dataset.line), true)));
    el.querySelectorAll("tr[data-stop]").forEach((r) => (r.onclick = () => selectStop(Number(r.dataset.stop), true, true)));
  }
})();
