/* OV-catchment — web-UI (vanilla JS + MapLibre) */
(() => {
  "use strict";

  const PALETTE = ["#0b6e4f", "#c2410c", "#1d4ed8", "#a21caf", "#b45309", "#0e7490", "#be123c", "#4d7c0f", "#6d28d9", "#0f766e", "#9a3412", "#334155"];
  const color = (i) => PALETTE[i % PALETTE.length];
  const $ = (id) => document.getElementById(id);
  const nf = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 1 });
  const clone = (o) => JSON.parse(JSON.stringify(o));

  const S = {
    cfg: null, baseline: null, variant: null, res: null,
    mode: "walk", th: null, tool: "move",
    markers: [], newCounter: 1, stale: false, busy: false,
  };

  // ---------- API ----------
  async function api(path, opts = {}) {
    const r = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (_) { /* noop */ }
      throw new Error(msg);
    }
    const ct = r.headers.get("content-type") || "";
    return ct.includes("application/json") ? r.json() : r.text();
  }

  function status(msg, isErr = false) {
    const el = $("status");
    el.textContent = msg || "";
    el.classList.toggle("err", !!isErr);
  }

  // ---------- Map ----------
  const map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {
        brt: {
          type: "raster",
          tiles: ["https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/grijs/EPSG:3857/{z}/{x}/{y}.png"],
          tileSize: 256, maxzoom: 19,
          attribution: "Kaartgegevens © Kadaster (PDOK BRT)",
        },
      },
      layers: [{ id: "brt", type: "raster", source: "brt" }],
    },
    center: [5.24, 52.3], zoom: 11.5,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");

  const EMPTY = { type: "FeatureCollection", features: [] };
  function setData(id, data) { const s = map.getSource(id); if (s) s.setData(data || EMPTY); }

  map.on("load", () => {
    for (const id of ["base-iso", "iso", "pts", "base-route", "base-stops", "var-route"]) {
      map.addSource(id, { type: "geojson", data: EMPTY });
    }
    map.addLayer({ id: "base-iso-line", type: "line", source: "base-iso",
      paint: { "line-color": "#7a7f87", "line-width": 1.5, "line-dasharray": [2, 2] } });
    map.addLayer({ id: "iso-fill", type: "fill", source: "iso",
      paint: { "fill-color": ["get", "color"], "fill-opacity": 0.12 } });
    map.addLayer({ id: "iso-line", type: "line", source: "iso",
      paint: { "line-color": ["get", "color"], "line-width": 1.2, "line-opacity": 0.8 } });
    map.addLayer({ id: "pts", type: "circle", source: "pts",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 1.2, 16, 4],
        "circle-color": ["get", "color"],
        "circle-opacity": ["case", ["==", ["get", "woon"], 1], 0.75, 0.35],
      } });
    map.addLayer({ id: "base-route", type: "line", source: "base-route",
      paint: { "line-color": "#7a7f87", "line-width": 4, "line-opacity": 0.7, "line-dasharray": [1.5, 1.2] } });
    map.addLayer({ id: "var-route", type: "line", source: "var-route",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#0b6e4f", "line-width": 4.5, "line-opacity": 0.9 } });
    map.addLayer({ id: "base-stops", type: "circle", source: "base-stops",
      paint: { "circle-radius": 5, "circle-color": "#fff", "circle-stroke-color": "#7a7f87", "circle-stroke-width": 2.5 } });
    map.addLayer({ id: "base-stops-lbl", type: "symbol", source: "base-stops",
      layout: { "text-field": ["get", "name"], "text-size": 11, "text-offset": [0, 1.2], "text-anchor": "top", "text-optional": true },
      paint: { "text-color": "#555", "text-halo-color": "#fff", "text-halo-width": 1.5 }, minzoom: 13 });
    init();
  });

  map.on("click", (e) => {
    if (!S.variant) return;
    const p = [e.lngLat.lng, e.lngLat.lat];
    if (S.tool === "add") addStop(p);
    else if (S.tool === "via") addVia(p);
  });

  // ---------- geometry helpers ----------
  function dist(a, b) { // meters, equirectangular
    const k = Math.cos(((a[1] + b[1]) / 2) * Math.PI / 180);
    const dx = (a[0] - b[0]) * 111320 * k, dy = (a[1] - b[1]) * 110540;
    return Math.hypot(dx, dy);
  }
  const xy = (s) => [s.lon, s.lat];

  function routePath(sc) {
    const pts = [];
    sc.stops.forEach((s, i) => {
      pts.push(xy(s));
      (sc.vias || []).filter((v) => v.after === i).forEach((v) => pts.push([v.lon, v.lat]));
    });
    return pts;
  }

  function line(coords) {
    return { type: "FeatureCollection", features: coords && coords.length > 1 ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } }] : [] };
  }

  function fitTo(sc) {
    const pts = (sc.geometry && sc.geometry.length ? sc.geometry : routePath(sc));
    if (!pts.length) return;
    const b = new maplibregl.LngLatBounds(pts[0], pts[0]);
    pts.forEach((p) => b.extend(p));
    map.fitBounds(b, { padding: 60, duration: 600 });
  }

  // ---------- rendering ----------
  function renderBaseline() {
    const b = S.baseline;
    $("baseName").textContent = b ? `${b.name} · ${b.feed || "scenario"}` : "—";
    setData("base-route", b ? line(b.geometry && b.geometry.length ? b.geometry : routePath(b)) : null);
    setData("base-stops", b ? { type: "FeatureCollection", features: b.stops.map((s) => ({ type: "Feature", properties: { name: s.name }, geometry: { type: "Point", coordinates: xy(s) } })) } : null);
  }

  function renderVariant() {
    S.markers.forEach((m) => m.remove());
    S.markers = [];
    const v = S.variant;
    $("varName").textContent = v ? `${v.name}${v.modified ? " (bewerkt)" : ""}` : "—";
    setData("var-route", v ? line(v.geometry && v.geometry.length ? v.geometry : routePath(v)) : null);
    if (!v) return;

    v.stops.forEach((s, i) => {
      const el = document.createElement("div");
      el.className = "stop-mk" + (String(s.id).startsWith("nieuw") ? " new" : "");
      el.style.background = color(i);
      el.textContent = i + 1;
      el.title = `${s.name}\n(dubbelklik = hernoemen)`;
      const mk = new maplibregl.Marker({ element: el, draggable: S.tool === "move" }).setLngLat(xy(s)).addTo(map);
      mk.on("dragend", () => { const ll = mk.getLngLat(); s.lon = ll.lng; s.lat = ll.lat; edited(); });
      el.addEventListener("click", (ev) => {
        if (S.tool === "delete") { ev.stopPropagation(); deleteStop(i); }
      });
      el.addEventListener("dblclick", (ev) => {
        ev.stopPropagation();
        const n = prompt("Naam van de halte", s.name);
        if (n) { s.name = n; renderVariant(); markStale(); }
      });
      S.markers.push(mk);
    });
    (v.vias || []).forEach((via, j) => {
      const el = document.createElement("div");
      el.className = "via-mk";
      el.title = "Via-punt (route loopt hierlangs)";
      const mk = new maplibregl.Marker({ element: el, draggable: S.tool === "move" }).setLngLat([via.lon, via.lat]).addTo(map);
      mk.on("dragend", () => { const ll = mk.getLngLat(); via.lon = ll.lng; via.lat = ll.lat; edited(); });
      el.addEventListener("click", (ev) => {
        if (S.tool === "delete") { ev.stopPropagation(); v.vias.splice(j, 1); edited(); }
      });
      S.markers.push(mk);
    });
  }

  // ---------- editing ----------
  let rerouteTimer = null;
  function edited() {
    S.variant.modified = true;
    renderVariant();
    markStale();
    clearTimeout(rerouteTimer);
    rerouteTimer = setTimeout(reroute, 250);
  }

  async function reroute() {
    const v = S.variant;
    if (!v || v.stops.length < 2) { if (v) { v.geometry = routePath(v); renderVariant(); } return; }
    try {
      const r = await api("/api/route", { method: "POST", body: { stops: v.stops, vias: v.vias || [] } });
      v.geometry = r.geometry;
      v.route_length_m = r.length_m;
      setData("var-route", line(v.geometry));
      status(`Route: ${nf1.format(r.length_m / 1000)} km, ~${nf.format(r.duration_s / 60)} min`);
      if ($("autoRun").checked) scheduleRun();
    } catch (e) {
      v.geometry = routePath(v);
      setData("var-route", line(v.geometry));
      status(`Route berekenen mislukt: ${e.message}`, true);
    }
  }

  function addStop(p) {
    const v = S.variant;
    const stops = v.stops;
    let best = stops.length, bestCost = Infinity;
    for (let i = 0; i <= stops.length; i++) {
      const prev = i > 0 ? xy(stops[i - 1]) : null;
      const next = i < stops.length ? xy(stops[i]) : null;
      let c;
      if (prev && next) c = dist(prev, p) + dist(p, next) - dist(prev, next);
      else c = dist(prev || next || p, p) * 1.0001;
      if (c < bestCost) { bestCost = c; best = i; }
    }
    const name = prompt("Naam van de nieuwe halte", `Nieuwe halte ${S.newCounter}`);
    if (name === null) return;
    stops.splice(best, 0, { id: `nieuw_${Date.now().toString(36)}_${S.newCounter++}`, name: name || "Nieuwe halte", lon: p[0], lat: p[1], members: [] });
    (v.vias || []).forEach((via) => { if (via.after >= best) via.after += 1; });
    edited();
  }

  function addVia(p) {
    const v = S.variant;
    if (v.stops.length < 2) return;
    let best = 0, bestCost = Infinity;
    for (let i = 0; i < v.stops.length - 1; i++) {
      const a = xy(v.stops[i]), b = xy(v.stops[i + 1]);
      const c = dist(a, p) + dist(p, b) - dist(a, b);
      if (c < bestCost) { bestCost = c; best = i; }
    }
    v.vias = v.vias || [];
    v.vias.push({ after: best, lon: p[0], lat: p[1] });
    edited();
  }

  function deleteStop(i) {
    const v = S.variant;
    if (!confirm(`Halte "${v.stops[i].name}" verwijderen?`)) return;
    v.stops.splice(i, 1);
    v.vias = (v.vias || []).map((via) => {
      let a = via.after;
      if (a > i) a -= 1; else if (a === i) a = Math.max(0, i - 1);
      return { ...via, after: Math.min(a, Math.max(0, v.stops.length - 2)) };
    });
    edited();
  }

  function setTool(t) {
    S.tool = t;
    document.querySelectorAll(".tool").forEach((b) => b.classList.toggle("active", b.dataset.tool === t));
    document.body.className = `tool-${t}`;
    const hints = {
      move: "Sleep haltes (genummerd) of via-punten (wit). Dubbelklik op een halte om te hernoemen.",
      add: "Klik op de kaart: de halte wordt op de logische plek in de volgorde ingevoegd.",
      via: "Klik op de kaart: de route tussen de dichtstbijzijnde haltes loopt via dit punt.",
      delete: "Klik op een halte of via-punt om het te verwijderen.",
    };
    $("toolHint").textContent = hints[t];
    renderVariant();
  }

  // ---------- loading ----------
  async function loadFeeds() {
    const feeds = await api("/api/feeds");
    const sel = $("feedSelect");
    sel.innerHTML = feeds.map((f) => `<option>${f}</option>`).join("") || "<option value=''>(geen feeds — draai gtfs-index)</option>";
  }

  let searchTimer = null;
  async function searchRoutes() {
    const feed = $("feedSelect").value;
    if (!feed) return;
    const q = $("routeSearch").value.trim();
    try {
      const list = await api(`/api/routes?feed=${encodeURIComponent(feed)}&q=${encodeURIComponent(q)}&limit=40`);
      $("routeList").innerHTML = list.map((r) => `
        <li>
          <span class="num">${esc(r.short_name || "?")}</span>
          <span class="nm" title="${esc(r.long_name)}">${esc(r.agency)} · ${esc(r.long_name)}</span>
          <button data-rid="${esc(r.route_id)}" data-as="base" class="ghost">Origineel</button>
          <button data-rid="${esc(r.route_id)}" data-as="var">Variant</button>
        </li>`).join("");
    } catch (e) { status(e.message, true); }
  }

  async function loadLine(routeId, as) {
    const feed = $("feedSelect").value;
    status("Lijn laden…");
    try {
      const ln = await api(`/api/line?feed=${encodeURIComponent(feed)}&route_id=${encodeURIComponent(routeId)}`);
      if (as === "base") {
        S.baseline = ln;
        if (!S.variant) S.variant = { ...clone(ln), name: "Variant" };
      } else {
        S.variant = { ...clone(ln), name: `${ln.route_name} (${ln.feed})` };
        if (!S.baseline) S.baseline = ln;
      }
      S.res = null;
      renderBaseline(); renderVariant(); renderResults(); fitTo(ln);
      status(`${ln.stops.length} haltes geladen.`);
      if ($("autoRun").checked) scheduleRun();
    } catch (e) { status(e.message, true); }
  }

  async function refreshScenarios() {
    const names = await api("/api/scenarios");
    $("scenarioSelect").innerHTML = `<option value="">Scenario laden…</option>` + names.map((n) => `<option>${esc(n)}</option>`).join("");
  }

  // ---------- analysis ----------
  function markStale() { S.stale = true; if (S.res) status("Wijzigingen — klik ‘Bereken catchment’ om bij te werken."); }

  let runTimer = null;
  function scheduleRun() { clearTimeout(runTimer); runTimer = setTimeout(run, 600); }

  async function run() {
    if (!S.variant && !S.baseline) { status("Laad eerst een lijn.", true); return; }
    if (S.busy) { scheduleRun(); return; }
    S.busy = true;
    $("runBtn").disabled = true;
    status("Bezig met berekenen (isochronen + toewijzing)…");
    const t0 = performance.now();
    try {
      if (S.baseline && S.variant) {
        S.res = await api("/api/compare", { method: "POST", body: { baseline: S.baseline, variant: S.variant } });
      } else {
        const one = await api("/api/analyse", { method: "POST", body: S.variant || S.baseline });
        S.res = { baseline: null, variant: one, comparison: null };
      }
      S.stale = false;
      status(`Klaar in ${nf1.format((performance.now() - t0) / 1000)} s.`);
      renderResults();
    } catch (e) {
      status(`Analyse mislukt: ${e.message}`, true);
    } finally {
      S.busy = false;
      $("runBtn").disabled = false;
    }
  }

  function thresholds() { return (S.cfg && S.cfg.thresholds[S.mode]) || []; }

  function renderThSeg() {
    const ths = thresholds();
    if (!ths.includes(S.th)) S.th = ths[Math.min(1, ths.length - 1)];
    $("thSeg").innerHTML = ths.map((t) => `<button data-th="${t}" class="${t === S.th ? "active" : ""}">${t >= 1000 ? nf1.format(t / 1000) + " km" : t + " m"}</button>`).join("");
  }

  function renderMapResults() {
    const r = S.res;
    const showIso = $("showIso").checked, showBase = $("showBaseIso").checked, showPts = $("showPts").checked;
    const v = r && r.variant;
    if (v && showIso) {
      setData("iso", { type: "FeatureCollection", features: v.isochrones.features
        .filter((f) => f.properties.mode === S.mode && f.properties.threshold === S.th)
        .map((f) => ({ ...f, properties: { ...f.properties, color: color(f.properties.stop_index) } })) });
    } else setData("iso", null);
    const b = r && r.baseline;
    if (b && showBase) {
      setData("base-iso", { type: "FeatureCollection", features: b.isochrones.features.filter((f) => f.properties.mode === S.mode && f.properties.threshold === S.th) });
    } else setData("base-iso", null);
    if (v && showPts && v.points[S.mode]) {
      const ti = thresholds().indexOf(S.th);
      const rows = v.points[S.mode].rows.filter((p) => p[3] <= ti);
      setData("pts", { type: "FeatureCollection", features: rows.map((p) => ({ type: "Feature", properties: { color: color(p[2]), woon: p[5] }, geometry: { type: "Point", coordinates: [p[0], p[1]] } })) });
    } else setData("pts", null);
  }

  function amenitiesTotal(m) {
    return Object.entries(m).filter(([k]) => k.startsWith("voorz_")).reduce((a, [, x]) => a + x, 0);
  }

  function delta(b, v, digits = 0) {
    if (b == null) return "";
    const d = v - b;
    const cls = d > 0 ? "pos" : d < 0 ? "neg" : "";
    const pct = b ? ` (${d >= 0 ? "+" : ""}${nf1.format((100 * d) / b)}%)` : "";
    const f = digits ? nf1 : nf;
    return `<span class="${cls}">${d >= 0 ? "+" : ""}${f.format(d)}${pct}</span>`;
  }

  function renderResults() {
    renderMapResults();
    const out = $("results");
    const r = S.res;
    if (!r) { out.innerHTML = ""; return; }
    const v = r.variant, b = r.baseline;
    const key = String(S.th);
    const vm = v.modes[S.mode].line[key], bm = b ? b.modes[S.mode].line[key] : null;
    const L = v.labels;
    const approx = v.router.approximate
      ? `<div class="banner">Benadering: routing-engine is <b>${v.router.engine}</b> (cirkels, geen netwerk). Start Valhalla voor echte loop-/fietsafstanden.</div>` : "";
    const kpi = (label, vv, bb, digits = 0) => `
      <div class="kpi"><div class="lbl">${label}</div>
        <div class="val">${(digits ? nf1 : nf).format(vv)}</div>
        <div class="cmp">${bb != null ? `origineel ${(digits ? nf1 : nf).format(bb)} · ${delta(bb, vv, digits)}` : "&nbsp;"}</div></div>`;
    const modeTxt = S.mode === "walk" ? "lopend" : "fietsend";
    let html = approx + `<div class="res-h">Lijntotaal — ${modeTxt} binnen ${S.th >= 1000 ? nf1.format(S.th / 1000) + " km" : S.th + " m"} (uniek, zonder dubbeltelling)</div>
      <div class="kpis">
        ${kpi("Inwoners (geschat)", vm.inwoners, bm && bm.inwoners)}
        ${kpi("Woningen", vm.n_woningen, bm && bm.n_woningen)}
        ${kpi("Arbeidsplaatsen*", vm.banen_schatting, bm && bm.banen_schatting)}
        ${kpi("Voorzieningen (OSM)", amenitiesTotal(vm), bm && amenitiesTotal(bm))}
        ${kpi("Routelengte (km)", v.route.length_m / 1000, b && b.route.length_m / 1000, 1)}
        ${kpi("Rijtijd (min, geschat)", v.route.duration_s / 60, b && b.route.duration_s / 60, 1)}
      </div>`;

    // per halte variant
    const stopRows = (res) => res.modes[S.mode].per_stop.map((s, i) => {
      const a = s.by_threshold[key].assigned, w = s.by_threshold[key].raw;
      return `<tr><td><span class="sw" style="background:${color(i)}"></span>${i + 1}. ${esc(s.name)}</td>
        <td>${nf.format(a.inwoners)}</td><td>${nf.format(w.inwoners)}</td><td>${nf.format(a.banen_schatting)}</td><td>${nf.format(amenitiesTotal(a))}</td></tr>`;
    }).join("");
    const stopTable = (res) => `<table><thead><tr><th>Halte</th><th title="Toegewezen: elk adres telt bij één halte">Inw.</th><th title="Alles binnen bereik, overlap telt dubbel">Inw. bruto</th><th>Banen*</th><th>Voorz.</th></tr></thead><tbody>${stopRows(res)}</tbody></table>`;
    html += `<div class="res-h">Per halte — variant</div>${stopTable(v)}`;
    if (b) html += `<details><summary>Per halte — origineel</summary>${stopTable(b)}</details>`;

    // alle metrics
    const rows = v.metrics.map((k) => `<tr><td>${esc(L[k] || k)}</td><td>${bm ? nf.format(bm[k]) : ""}</td><td>${nf.format(vm[k])}</td><td>${bm ? delta(bm[k], vm[k]) : ""}</td></tr>`).join("");
    html += `<details><summary>Alle kengetallen (lijntotaal)</summary><table><thead><tr><th></th><th>Origineel</th><th>Variant</th><th>Verschil</th></tr></thead><tbody>${rows}</tbody></table></details>`;
    if (r.comparison) {
      const c = r.comparison.stop_changes;
      html += `<p class="note">Haltes: ${c.kept.length} behouden, ${c.added.length} nieuw, ${c.removed.length} vervallen. Rijtijd via de routing-engine (bus); GTFS-dienstregeling origineel: ${b && b.route.gtfs_duration_s ? nf.format(b.route.gtfs_duration_s / 60) + " min" : "n.v.t."}.</p>`;
    }
    html += `<p class="note">* Arbeidsplaatsen = m² BAG-gebruiksoppervlak ÷ aanname m²/baan (config.yaml). Inwoners = CBS 100m-vierkanten verdeeld over BAG-woningen.</p>`;
    if (b) html += `<div class="row"><button id="csvBtn" class="ghost">Exporteer vergelijking (CSV)</button></div>`;
    out.innerHTML = html;
    const csvBtn = $("csvBtn");
    if (csvBtn) csvBtn.onclick = exportCsv;
  }

  async function exportCsv() {
    status("CSV maken…");
    try {
      const r = await fetch("/api/export.csv", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ baseline: S.baseline, variant: S.variant }) });
      if (!r.ok) throw new Error(r.statusText);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `ov-catchment-${(S.variant.name || "variant").replace(/[^\w-]+/g, "_")}.csv`;
      a.click();
      URL.revokeObjectURL(a.href);
      status("CSV gedownload.");
    } catch (e) { status(`Export mislukt: ${e.message}`, true); }
  }

  function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  // ---------- init ----------
  async function init() {
    try {
      S.cfg = await api("/api/config");
    } catch (e) { status(`Backend niet bereikbaar: ${e.message}`, true); return; }
    const rb = $("routerBadge");
    const rt = S.cfg.router;
    rb.textContent = rt.approximate ? "Benadering (geen netwerk)" : rt.ok ? "Valhalla ✓" : "Valhalla offline";
    rb.className = "badge" + (rt.approximate ? " warn" : rt.ok ? "" : " err");
    const msgs = [];
    if (S.cfg.demo) msgs.push("<b>DEMO</b> — synthetische data, niet echt.");
    if (!S.cfg.data_available) msgs.push("Geen verwerkte adresdata gevonden. Draai <code>make data</code>.");
    if (!rt.approximate && !rt.ok) msgs.push(`Valhalla niet bereikbaar op ${rt.url}. Draait <code>docker compose up valhalla</code>?`);
    if (msgs.length) { $("banner").innerHTML = msgs.join("<br>"); $("banner").classList.remove("hidden"); }
    if (S.cfg.bbox) map.fitBounds([[S.cfg.bbox[0], S.cfg.bbox[1]], [S.cfg.bbox[2], S.cfg.bbox[3]]], { duration: 0 });

    renderThSeg();
    await loadFeeds();
    await refreshScenarios();
    searchRoutes();

    $("feedSelect").onchange = searchRoutes;
    $("routeSearch").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(searchRoutes, 250); };
    $("routeList").onclick = (e) => { const b = e.target.closest("button[data-rid]"); if (b) loadLine(b.dataset.rid, b.dataset.as); };
    document.querySelectorAll(".tool").forEach((b) => (b.onclick = () => setTool(b.dataset.tool)));
    $("modeSeg").onclick = (e) => {
      const b = e.target.closest("button[data-mode]"); if (!b) return;
      S.mode = b.dataset.mode;
      document.querySelectorAll("#modeSeg button").forEach((x) => x.classList.toggle("active", x === b));
      renderThSeg(); renderResults();
    };
    $("thSeg").onclick = (e) => {
      const b = e.target.closest("button[data-th]"); if (!b) return;
      S.th = Number(b.dataset.th); renderThSeg(); renderResults();
    };
    ["showIso", "showBaseIso", "showPts"].forEach((id) => ($(id).onchange = renderMapResults));
    $("runBtn").onclick = run;
    $("resetVariant").onclick = () => {
      if (!S.baseline) return;
      S.variant = { ...clone(S.baseline), name: "Variant" };
      renderVariant(); markStale();
    };
    $("swapBtn").onclick = () => {
      if (!S.baseline || !S.variant) return;
      [S.baseline, S.variant] = [S.variant, S.baseline];
      renderBaseline(); renderVariant(); markStale();
      if (S.res) { [S.res.baseline, S.res.variant] = [S.res.variant, S.res.baseline]; S.res.comparison = null; renderResults(); }
    };
    $("saveScenario").onclick = async () => {
      if (!S.variant) return;
      const name = $("scenarioName").value.trim() || S.variant.name;
      try {
        S.variant.name = name;
        await api(`/api/scenarios/${encodeURIComponent(name)}`, { method: "PUT", body: S.variant });
        await refreshScenarios(); renderVariant(); status(`Scenario '${name}' opgeslagen.`);
      } catch (e) { status(e.message, true); }
    };
    const loadScenario = async (as) => {
      const n = $("scenarioSelect").value; if (!n) return;
      try {
        const sc = await api(`/api/scenarios/${encodeURIComponent(n)}`);
        if (as === "base") S.baseline = sc; else S.variant = sc;
        renderBaseline(); renderVariant(); fitTo(sc); markStale();
      } catch (e) { status(e.message, true); }
    };
    $("loadAsVariant").onclick = () => loadScenario("var");
    $("loadAsBase").onclick = () => loadScenario("base");
    setTool("move");
  }
})();
