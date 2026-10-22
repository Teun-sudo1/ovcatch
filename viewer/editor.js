/* Haltebereik NL — lijn bewerken in de browser (origineel vs. variant).
 *
 * Isochronen: ongewijzigde haltes uit de vooraf berekende data (iso/), verplaatste en nieuwe
 * haltes en de busroute via de openbare Valhalla-server van FOSSGIS. Tellen gebeurt op
 * 100m-vierkanten (cells/): origineel en variant op dezelfde manier, dus eerlijk vergelijkbaar.
 */
(() => {
  "use strict";

  const VALHALLA = "https://valhalla1.openstreetmap.de";
  const MAX_PARALLEL = 2; // gratis server: niet te veel tegelijk
  const MAX_LOCATIONS = 10; // limiet per routeverzoek op de openbare server
  const SNAP_M = 40;
  const STORE_KEY = "ovcatch.scenarios";

  let O; // gedeelde helpers uit app.js
  const E = { base: null, variant: null, res: null, tool: "move", markers: [], busy: false, stale: true, msg: "", err: false };
  const isoMem = new Map();
  const cellMem = new Map();

  // ---------- geometrie ----------
  const clone = (o) => JSON.parse(JSON.stringify(o));
  function dist(a, b) { // meter, equirectangulair
    const k = Math.cos(((a[1] + b[1]) / 2) * Math.PI / 180);
    return Math.hypot((a[0] - b[0]) * 111320 * k, (a[1] - b[1]) * 110540);
  }
  const xy = (s) => [s.lon, s.lat];

  // WGS84 -> RD (benadering, ~1 m): alleen voor de tegelsleutels van iso/ en cells/
  function toRD(lon, lat) {
    const dp = 0.36 * (lat - 52.15517440), dl = 0.36 * (lon - 5.38720621);
    const x = 190094.945 * dl - 11832.228 * dp * dl - 114.221 * dp * dp * dl - 32.391 * dl ** 3 - 0.705 * dp
      - 2.340 * dp ** 3 * dl - 0.608 * dp * dl ** 3 - 0.008 * dl * dl + 0.148 * dp * dp * dl ** 3;
    const y = 309056.544 * dp + 3638.893 * dl * dl + 73.077 * dp * dp - 157.984 * dp * dl * dl + 59.788 * dp ** 3
      + 0.433 * dl - 6.439 * dp * dp * dl * dl - 0.032 * dp * dl + 0.092 * dl ** 4 - 0.054 * dp * dl ** 4;
    return [155000 + x, 463000 + y];
  }
  const tileKey = (lon, lat) => { const [x, y] = toRD(lon, lat); return `${Math.floor(x / 10000)}_${Math.floor(y / 10000)}`; };

  // polys: [[ring, ring...], ...] met ring = [[lon, lat], ...]; even-oneven over alle ringen
  function inPolys(polys, x, y) {
    for (const poly of polys) {
      let inside = false;
      for (const ring of poly) {
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
          const [xi, yi] = ring[i], [xj, yj] = ring[j];
          if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
        }
      }
      if (inside) return true;
    }
    return false;
  }
  function bboxOf(polys) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of polys) for (const [x, y] of p[0]) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    return [x0, y0, x1, y1];
  }

  // ---------- netwerk ----------
  let active = 0;
  const queue = [];
  function limited(fn) {
    return new Promise((resolve, reject) => {
      const run = () => { active++; fn().then(resolve, reject).finally(() => { active--; if (queue.length) queue.shift()(); }); };
      if (active < MAX_PARALLEL) run(); else queue.push(run);
    });
  }
  async function valhalla(path, body) {
    return limited(async () => {
      const r = await fetch(VALHALLA + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error(`routing ${r.status}`);
      return r.json();
    });
  }
  function decodePolyline6(s) {
    const out = []; let i = 0, lat = 0, lon = 0;
    while (i < s.length) {
      for (const w of [0, 1]) {
        let r = 0, sh = 0, b;
        do { b = s.charCodeAt(i++) - 63; r |= (b & 0x1f) << sh; sh += 5; } while (b >= 0x20);
        const d = r & 1 ? ~(r >> 1) : r >> 1;
        if (w === 0) lat += d; else lon += d;
      }
      out.push([lon / 1e6, lat / 1e6]);
    }
    return out;
  }

  // isochronen van één halte: {walk: [polys per band], bike: [...]}
  async function isochrones(stop) {
    if (stop.area != null && !stop.moved) {
      const s = O.S.stops[stop.area];
      const tile = await O.isoTile(s[5]);
      const e = tile[String(stop.area)];
      if (e) {
        const out = {};
        for (const m of O.S.meta.modi) out[m] = (e[m] || []).map((polys) => polys.map((rings) => rings.map(O.decodePolyline)));
        return out;
      }
    }
    const k = `${stop.lon.toFixed(6)},${stop.lat.toFixed(6)}`;
    if (!isoMem.has(k)) {
      const p = (async () => {
        const out = {};
        for (const m of O.S.meta.modi) {
          const ths = O.S.meta.drempels[m];
          const data = await valhalla("/isochrone", {
            locations: [{ lon: stop.lon, lat: stop.lat }], costing: m === "walk" ? "pedestrian" : "bicycle",
            contours: ths.map((t) => ({ distance: t / 1000 })), polygons: true, denoise: 0.3, generalize: 10,
          });
          const bands = ths.map(() => []);
          for (const f of data.features || []) {
            const b = ths.findIndex((t) => Math.abs(t / 1000 - f.properties.contour) < 1e-6);
            if (b < 0 || !f.geometry) continue;
            if (f.geometry.type === "Polygon") bands[b].push(f.geometry.coordinates);
            else if (f.geometry.type === "MultiPolygon") bands[b].push(...f.geometry.coordinates);
          }
          out[m] = bands;
        }
        return out;
      })();
      isoMem.set(k, p);
      p.catch(() => isoMem.delete(k));
    }
    return isoMem.get(k);
  }

  async function route(sc) {
    const pts = [];
    // De route volgt één richting: perrons die alleen de terugrichting bedienen (dir 1, bijv. aan de
    // overkant van een snelweg) slaan we over, tenzij je ze zelf hebt verplaatst. radius: alle wegen
    // binnen 25 m zijn kandidaat (haltes liggen vaak tussen twee rijbanen in).
    sc.stops.forEach((s, i) => {
      if (s.dir !== 1 || s.moved) pts.push({ lon: s.lon, lat: s.lat, type: "break", radius: 25 });
      (sc.vias || []).filter((v) => v.after === i).forEach((v) => pts.push({ lon: v.lon, lat: v.lat, type: "through", radius: 25 }));
    });
    if (pts.length < 2) return { geometry: pts.map((p) => [p.lon, p.lat]), length_m: 0, duration_s: 0 };
    // De openbare server accepteert max. MAX_LOCATIONS punten per verzoek: in stukken die elk op een
    // halte beginnen en eindigen (een via-punt mag geen begin/eind zijn), daarna aan elkaar.
    const chunks = [];
    for (let i = 0; i < pts.length - 1;) {
      let j = Math.min(i + MAX_LOCATIONS - 1, pts.length - 1);
      if (j < pts.length - 1) { let k = j; while (k > i + 1 && pts[k].type !== "break") k--; if (k > i + 1) j = k; }
      chunks.push(pts.slice(i, j + 1).map((p, n, a) => ({ ...p, type: n === 0 || n === a.length - 1 ? "break" : p.type })));
      i = j;
    }
    const parts = await Promise.all(chunks.map((locations) => valhalla("/route", { locations, costing: "bus", directions_type: "none" })));
    const geometry = [];
    let length_m = 0, duration_s = 0;
    for (const d of parts) {
      for (const leg of d.trip.legs) { const c = decodePolyline6(leg.shape); geometry.push(...(geometry.length ? c.slice(1) : c)); }
      length_m += d.trip.summary.length * 1000;
      duration_s += d.trip.summary.time;
    }
    return { geometry, length_m, duration_s };
  }

  // ---------- vierkanten ----------
  async function cellTile(key) {
    if (!cellMem.has(key)) {
      cellMem.set(key, fetch(`${O.DATA}cells/${key}.json`).then((r) => (r.ok ? r.json() : { lon: [], lat: [], v: [] })).then((t) => ({
        lon: t.lon.map((x) => x / 1e5), lat: t.lat.map((x) => x / 1e5), v: t.v,
      })));
    }
    return cellMem.get(key);
  }
  async function cellsIn(b) {
    const [x0, y0] = toRD(b[0], b[1]), [x1, y1] = toRD(b[2], b[3]);
    const [xa, ya] = toRD(b[0], b[3]), [xb, yb] = toRD(b[2], b[1]);
    const keys = [];
    for (let tx = Math.floor(Math.min(x0, xa) / 10000); tx <= Math.floor(Math.max(x1, xb) / 10000); tx++)
      for (let ty = Math.floor(Math.min(y0, yb) / 10000); ty <= Math.floor(Math.max(y1, ya) / 10000); ty++) keys.push(`${tx}_${ty}`);
    const tiles = await Promise.all(keys.map(cellTile));
    const lon = [], lat = [], v = [];
    for (const t of tiles) for (let i = 0; i < t.v.length; i++) {
      if (t.lon[i] >= b[0] && t.lon[i] <= b[2] && t.lat[i] >= b[1] && t.lat[i] <= b[3]) { lon.push(t.lon[i]); lat.push(t.lat[i]); v.push(t.v[i]); }
    }
    return { lon, lat, v };
  }

  // ---------- analyse ----------
  async function analyse(sc, progress) {
    const meta = O.S.meta, K = meta.kengetallen.length;
    const isos = [];
    let done = 0;
    await Promise.all(sc.stops.map(async (s, i) => { isos[i] = await isochrones(s); progress(++done, sc.stops.length); }));
    let rr = null, routeErr = null;
    try { rr = await route(sc); } catch (e) { routeErr = e.message; }
    const res = { modes: {}, route: rr, routeErr, n_stops: sc.stops.length };
    for (const m of meta.modi) {
      const nb = meta.drempels[m].length;
      const all = [];
      isos.forEach((iso) => (iso[m] || []).forEach((p) => all.push(...p)));
      const zero = () => Array.from({ length: nb }, () => new Array(K).fill(0));
      if (!all.length) { res.modes[m] = { line: zero(), perStop: sc.stops.map(zero) }; continue; }
      const cells = await cellsIn(bboxOf(all));
      const lineBand = new Int8Array(cells.v.length).fill(-1);
      const perStop = sc.stops.map((_, si) => {
        const bands = isos[si][m] || [];
        const out = zero();
        if (!bands.length || !bands[nb - 1].length) return out;
        const [bx0, by0, bx1, by1] = bboxOf(bands[nb - 1]);
        const bbs = bands.map((p) => (p.length ? bboxOf(p) : null));
        for (let c = 0; c < cells.v.length; c++) {
          const x = cells.lon[c], y = cells.lat[c];
          if (x < bx0 || x > bx1 || y < by0 || y > by1) continue;
          let band = -1;
          for (let b = 0; b < nb; b++) {
            const bb = bbs[b];
            if (bb && x >= bb[0] && x <= bb[2] && y >= bb[1] && y <= bb[3] && inPolys(bands[b], x, y)) { band = b; break; }
          }
          if (band < 0) continue;
          for (let b = band; b < nb; b++) for (let k = 0; k < K; k++) out[b][k] += cells.v[c][k];
          if (lineBand[c] < 0 || band < lineBand[c]) lineBand[c] = band;
        }
        return out;
      });
      const line = zero();
      for (let c = 0; c < cells.v.length; c++) {
        if (lineBand[c] < 0) continue;
        for (let b = lineBand[c]; b < nb; b++) for (let k = 0; k < K; k++) line[b][k] += cells.v[c][k];
      }
      res.modes[m] = { line, perStop, isos: isos.map((iso) => iso[m] || []) };
    }
    return res;
  }

  // ---------- scenario's ----------
  function fromLine(li) {
    const l = O.S.lines[li];
    return {
      name: `Lijn ${l[0]} (origineel)`, line: li,
      // dir: 0 = alleen heen, 1 = alleen terug, 2 = beide (oudere data zonder richting: beide)
      stops: l[3].map((a, n) => ({ id: `a${a}`, name: O.S.stops[a][0], lon: O.S.stops[a][1], lat: O.S.stops[a][2], area: a, dir: l[6] ? l[6][n] : 2 })),
      vias: [], geometry: O.decodePolyline(l[5]), modified: false,
    };
  }
  function nearestArea(p) {
    let best = null, bd = SNAP_M;
    O.S.stops.forEach((s, i) => {
      if (Math.abs(s[2] - p[1]) > 0.001 || Math.abs(s[1] - p[0]) > 0.0015) return;
      const d = dist(p, [s[1], s[2]]);
      if (d < bd) { bd = d; best = i; }
    });
    return best;
  }

  function start(li) {
    E.base = fromLine(li);
    E.variant = { ...clone(E.base), name: `Lijn ${O.S.lines[li][0]} (variant)` };
    E.res = null; E.stale = true; E.msg = ""; E.err = false;
    O.S.edit = true;
    O.setData("iso", null); O.setData("sel", null);
    O.map.setLayoutProperty("line-stops", "visibility", "none");
    O.map.setLayoutProperty("line-stops-lbl", "visibility", "none");
    O.map.setPaintProperty("stops", "circle-opacity", 0.35);
    O.map.setPaintProperty("stops", "circle-stroke-opacity", 0.35);
    renderMap();
    O.renderDetail();
  }

  function stop() {
    O.S.edit = false;
    E.markers.forEach((m) => m.remove()); E.markers = [];
    for (const id of ["edit-base-route", "edit-base-stops", "edit-var-route", "edit-iso"]) O.setData(id, null);
    O.map.setLayoutProperty("line-stops", "visibility", "visible");
    O.map.setLayoutProperty("line-stops-lbl", "visibility", "visible");
    O.map.setPaintProperty("stops", "circle-opacity", 1);
    O.map.setPaintProperty("stops", "circle-stroke-opacity", 1);
    document.body.classList.remove("tool-add", "tool-via", "tool-delete");
    O.renderDetail();
  }

  // ---------- kaart ----------
  function line(coords) {
    return { type: "FeatureCollection", features: coords && coords.length > 1 ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } }] : [] };
  }
  function routePath(sc) {
    const pts = [];
    sc.stops.forEach((s, i) => { pts.push(xy(s)); (sc.vias || []).filter((v) => v.after === i).forEach((v) => pts.push([v.lon, v.lat])); });
    return pts;
  }

  function renderMap() {
    const b = E.base, v = E.variant;
    O.setData("edit-base-route", line(b.geometry && b.geometry.length ? b.geometry : routePath(b)));
    O.setData("edit-base-stops", { type: "FeatureCollection", features: b.stops.map((s) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: xy(s) } })) });
    O.setData("edit-var-route", line(v.geometry && v.geometry.length ? v.geometry : routePath(v)));
    E.markers.forEach((m) => m.remove()); E.markers = [];
    v.stops.forEach((s, i) => {
      const el = document.createElement("div");
      el.className = "stop-mk" + (s.area == null ? " new" : s.moved ? " moved" : "");
      el.textContent = i + 1;
      el.title = `${s.name}${s.area == null ? " (nieuw)" : ""}\nDubbelklik = hernoemen`;
      const mk = new maplibregl.Marker({ element: el, draggable: E.tool === "move" }).setLngLat(xy(s)).addTo(O.map);
      mk.on("dragend", () => {
        const ll = mk.getLngLat();
        const a = nearestArea([ll.lng, ll.lat]);
        if (a != null) { s.lon = O.S.stops[a][1]; s.lat = O.S.stops[a][2]; s.area = a; s.moved = false; if (String(s.id).startsWith("nieuw")) s.name = O.S.stops[a][0]; }
        else { s.lon = ll.lng; s.lat = ll.lat; s.moved = true; }
        edited();
      });
      el.addEventListener("click", (ev) => { if (E.tool === "delete") { ev.stopPropagation(); deleteStop(i); } });
      el.addEventListener("dblclick", (ev) => {
        ev.stopPropagation();
        const n = prompt("Naam van de halte", s.name);
        if (n) { s.name = n; renderMap(); O.renderDetail(); }
      });
      E.markers.push(mk);
    });
    (v.vias || []).forEach((via, j) => {
      const el = document.createElement("div");
      el.className = "via-mk";
      el.title = "Via-punt (route loopt hierlangs)";
      const mk = new maplibregl.Marker({ element: el, draggable: E.tool === "move" }).setLngLat([via.lon, via.lat]).addTo(O.map);
      mk.on("dragend", () => { const ll = mk.getLngLat(); via.lon = ll.lng; via.lat = ll.lat; edited(); });
      el.addEventListener("click", (ev) => { if (E.tool === "delete") { ev.stopPropagation(); v.vias.splice(j, 1); edited(); } });
      E.markers.push(mk);
    });
    renderIso();
  }

  function renderIso() {
    const r = E.res && E.res.variant;
    const m = O.S.mode, th = O.S.th;
    if (!r || !$("edShowIso") || !$("edShowIso").checked) { O.setData("edit-iso", null); return; }
    const features = [];
    (r.modes[m].isos || []).forEach((bands, si) => {
      const p = bands[th];
      if (p && p.length) features.push({ type: "Feature", properties: { color: O.color(si) }, geometry: { type: "MultiPolygon", coordinates: p } });
    });
    O.setData("edit-iso", { type: "FeatureCollection", features });
  }

  // ---------- bewerken ----------
  let rerouteTimer = null;
  function edited() {
    E.variant.modified = true;
    E.stale = true;
    renderMap();
    O.renderDetail();
    clearTimeout(rerouteTimer);
    rerouteTimer = setTimeout(reroute, 300);
  }
  async function reroute() {
    const v = E.variant;
    if (v.stops.length < 2) { v.geometry = routePath(v); renderMap(); return; }
    try {
      const r = await route(v);
      if (v !== E.variant) return;
      v.geometry = r.geometry;
      O.setData("edit-var-route", line(v.geometry));
      status(`Route: ${O.nf1.format(r.length_m / 1000)} km, ~${O.nf.format(r.duration_s / 60)} min. Klik ‘Bereken’ voor het bereik.`);
    } catch (e) {
      v.geometry = routePath(v);
      O.setData("edit-var-route", line(v.geometry));
      status(`Busroute berekenen mislukt (${e.message}); rechte lijnen getoond.`, true);
    }
  }
  function addStop(p, area) {
    const stops = E.variant.stops;
    let best = stops.length, bestCost = Infinity;
    for (let i = 0; i <= stops.length; i++) {
      const prev = i > 0 ? xy(stops[i - 1]) : null, next = i < stops.length ? xy(stops[i]) : null;
      const c = prev && next ? dist(prev, p) + dist(p, next) - dist(prev, next) : dist(prev || next || p, p) * 1.0001;
      if (c < bestCost) { bestCost = c; best = i; }
    }
    let s;
    if (area != null) s = { id: `a${area}`, name: O.S.stops[area][0], lon: O.S.stops[area][1], lat: O.S.stops[area][2], area };
    else {
      const name = prompt("Naam van de nieuwe halte", `Nieuwe halte ${stops.filter((x) => x.area == null).length + 1}`);
      if (name === null) return;
      s = { id: `nieuw_${Date.now().toString(36)}`, name: name || "Nieuwe halte", lon: p[0], lat: p[1], area: null, moved: true };
    }
    stops.splice(best, 0, s);
    (E.variant.vias || []).forEach((v) => { if (v.after >= best) v.after += 1; });
    edited();
  }
  function addVia(p) {
    const v = E.variant;
    if (v.stops.length < 2) return;
    let best = 0, bestCost = Infinity;
    for (let i = 0; i < v.stops.length - 1; i++) {
      const a = xy(v.stops[i]), b = xy(v.stops[i + 1]);
      const c = dist(a, p) + dist(p, b) - dist(a, b);
      if (c < bestCost) { bestCost = c; best = i; }
    }
    v.vias.push({ after: best, lon: p[0], lat: p[1] });
    edited();
  }
  function deleteStop(i) {
    const v = E.variant;
    if (!confirm(`Halte "${v.stops[i].name}" verwijderen?`)) return;
    v.stops.splice(i, 1);
    v.vias = (v.vias || []).map((via) => {
      let a = via.after;
      if (a > i) a -= 1; else if (a === i) a = Math.max(0, i - 1);
      return { ...via, after: Math.min(a, Math.max(0, v.stops.length - 2)) };
    });
    edited();
  }
  function onMapClick(e) {
    if (!O.S.edit || (E.tool !== "add" && E.tool !== "via")) return false;
    const p = [e.lngLat.lng, e.lngLat.lat];
    if (E.tool === "via") { addVia(p); return true; }
    const hit = O.map.queryRenderedFeatures(e.point, { layers: ["stops"] })[0];
    addStop(hit ? [O.S.stops[hit.properties.i][1], O.S.stops[hit.properties.i][2]] : p, hit ? hit.properties.i : nearestArea(p));
    return true;
  }
  function setTool(t) {
    E.tool = t;
    document.body.classList.remove("tool-add", "tool-via", "tool-delete");
    if (t !== "move") document.body.classList.add(`tool-${t}`);
    renderMap();
    O.renderDetail();
  }

  // ---------- berekenen ----------
  const $ = (id) => document.getElementById(id);
  function status(msg, err) { E.msg = msg; E.err = !!err; const el = $("edStatus"); if (el) { el.textContent = msg; el.classList.toggle("err", !!err); } }

  async function run() {
    if (E.busy) return;
    E.busy = true; O.renderDetail();
    const t0 = performance.now();
    try {
      const prog = (label) => (d, n) => status(`${label}: isochronen ${d}/${n}…`);
      // momentopname: tabel en CSV horen bij wat berekend is, ook als er daarna bewerkt wordt
      const baseSc = clone(E.base), varSc = clone(E.variant);
      const base = await analyse(baseSc, prog("Origineel"));
      const variant = await analyse(varSc, prog("Variant"));
      E.res = { base, variant, baseSc, varSc };
      E.stale = false;
      if (variant.route && variant.route.geometry.length > 1) E.variant.geometry = variant.route.geometry;
      status(`Klaar in ${O.nf1.format((performance.now() - t0) / 1000)} s.${variant.routeErr ? ` Busroute niet beschikbaar (${variant.routeErr}).` : ""}`, !!variant.routeErr);
    } catch (e) {
      status(`Berekenen mislukt: ${e.message}. Is de routing-server bereikbaar? Probeer het later opnieuw.`, true);
    } finally {
      E.busy = false;
      renderMap();
      O.renderDetail();
    }
  }

  // ---------- paneel ----------
  function delta(b, v, digits) {
    const d = v - b, f = digits ? O.nf1 : O.nf;
    const cls = d > 0 ? "pos" : d < 0 ? "neg" : "";
    const pct = b ? ` (${d >= 0 ? "+" : ""}${O.nf1.format((100 * d) / b)}%)` : "";
    return `<span class="${cls}">${d >= 0 ? "+" : ""}${f.format(d)}${pct}</span>`;
  }

  function render(el) {
    const S = O.S, meta = S.meta, m = S.mode, th = S.th, K = meta.kengetallen;
    const t = meta.drempels[m][th];
    const v = E.variant, b = E.base;
    const tools = [["move", "Verslepen"], ["add", "+ Halte"], ["via", "+ Via-punt"], ["delete", "Verwijderen"]];
    const hints = {
      move: "Sleep haltes (genummerd) of via-punten (wit). Loslaten bij een bestaande halte = daarop vastklikken. Dubbelklik = hernoemen.",
      add: "Klik op de kaart (of op een bestaande halte): de halte komt op de logische plek in de volgorde.",
      via: "Klik op de kaart: de route tussen de twee dichtstbijzijnde haltes loopt via dit punt.",
      delete: "Klik op een halte of via-punt om het te verwijderen.",
    };
    const changes = (() => {
      const bi = new Set(b.stops.map((s) => s.id)), vi = new Set(v.stops.map((s) => s.id));
      return { removed: [...bi].filter((x) => !vi.has(x)).length, added: [...vi].filter((x) => !bi.has(x)).length, moved: v.stops.filter((s) => s.moved && bi.has(s.id)).length };
    })();
    let h = `<button class="back" id="edClose">× Bewerken sluiten</button>
      <h2>${O.esc(v.name)}</h2>
      <div class="muted">Origineel: ${O.esc(b.name)} · ${b.stops.length} haltes</div>
      <div class="tools">${tools.map(([k, l]) => `<button data-tool="${k}" class="tool${E.tool === k ? " active" : ""}">${l}</button>`).join("")}</div>
      <p class="hint">${hints[E.tool]}</p>
      <div class="row"><button id="edReset">Reset naar origineel</button><button id="edRun" class="primary"${E.busy ? " disabled" : ""}>${E.busy ? "Bezig…" : "Bereken"}</button></div>
      <p class="note">Variant: ${v.stops.length} haltes · ${changes.added} nieuw · ${changes.removed} vervallen · ${changes.moved} verplaatst · ${v.vias.length} via-punten</p>
      <div id="edStatus" class="status${E.err ? " err" : ""}">${O.esc(E.msg)}</div>`;

    if (E.res) {
      const rb = E.res.base.modes[m], rv = E.res.variant.modes[m];
      const kv = (arr) => Object.fromEntries(K.map((k, i) => [k, arr[i]]));
      const lb = kv(rb.line[th]), lv = kv(rv.line[th]);
      const within = `binnen ${O.fmtTh(t)} ${O.MODE_ADJ[m]}`;
      const kpi = (label, vv, bb, digits) => `<div class="kpi"><div class="lbl">${label}</div><div class="val">${(digits ? O.nf1 : O.nf).format(vv)}</div><div class="cmp">origineel ${(digits ? O.nf1 : O.nf).format(bb)} · ${delta(bb, vv, digits)}</div></div>`;
      const routeKpis = E.res.base.route && E.res.variant.route
        ? kpi("Routelengte (km)", E.res.variant.route.length_m / 1000, E.res.base.route.length_m / 1000, 1) + kpi("Rijtijd (min, geschat)", E.res.variant.route.duration_s / 60, E.res.base.route.duration_s / 60, 1)
        : "";
      h += `${E.stale ? '<p class="banner">Gewijzigd na de laatste berekening — klik ‘Bereken’.</p>' : ""}
        <div class="res-h">Lijntotaal ${within} (uniek)</div>
        <div class="kpis">${kpi("Inwoners (geschat)", lv.inwoners, lb.inwoners)}${kpi("Woningen", lv.woningen, lb.woningen)}${kpi("Arbeidsplaatsen*", lv.banen, lb.banen)}${kpi("Voorzieningen (OSM)", lv.voorzieningen, lb.voorzieningen)}${routeKpis}</div>
        <label class="check"><input type="checkbox" id="edShowIso" checked /> isochronen variant tonen</label>
        <div class="res-h">Per halte — variant ${within} (bruto)</div>
        <table><thead><tr><th>Halte</th><th>Inwoners</th><th>Woningen</th><th>Banen*</th><th>Voorz.</th></tr></thead><tbody>
        ${E.res.varSc.stops.map((s, i) => { const x = kv(rv.perStop[i][th]); return `<tr><td><span class="sw" style="background:${O.color(i)}"></span>${i + 1}. ${O.esc(s.name)}${s.area == null ? " <i>(nieuw)</i>" : s.moved ? " <i>(verplaatst)</i>" : ""}</td><td>${O.nf.format(x.inwoners)}</td><td>${O.nf.format(x.woningen)}</td><td>${O.nf.format(x.banen)}</td><td>${O.nf.format(x.voorzieningen)}</td></tr>`; }).join("")}
        </tbody></table>
        <details><summary>Alle afstanden (lijntotaal)</summary><table><thead><tr><th></th><th>Origineel</th><th>Variant</th><th>Verschil</th></tr></thead><tbody>
        ${meta.modi.map((mm) => meta.drempels[mm].map((tt, bi) => { const x = kv(E.res.base.modes[mm].line[bi]), y = kv(E.res.variant.modes[mm].line[bi]); return `<tr><td>Inwoners, ${O.MODE_LABEL[mm].toLowerCase()} ${O.fmtTh(tt)}</td><td>${O.nf.format(x.inwoners)}</td><td>${O.nf.format(y.inwoners)}</td><td>${delta(x.inwoners, y.inwoners)}</td></tr>`; }).join("")).join("")}
        </tbody></table></details>
        <div class="row"><button id="edCsv">Exporteer vergelijking (CSV)</button></div>
        <p class="note">Bewerkmodus telt per 100m-vierkant (middelpunt binnen bereik), origineel en variant op dezelfde manier; daardoor kan het origineel iets afwijken van de lijnweergave (die telt per adres). Routing voor nieuwe/verplaatste haltes en de busroute: Valhalla via FOSSGIS (openbare server).</p>`;
    } else if (!E.busy) {
      h += `<p class="note">Bewerk de variant en klik ‘Bereken’. Ongewijzigde haltes zijn direct beschikbaar; nieuwe of verplaatste haltes worden online berekend (enkele seconden per halte).</p>`;
    }
    h += `<div class="res-h">Scenario’s</div>
      <div class="row"><input id="edName" placeholder="Naam scenario" value="${O.esc(v.name)}" /><button id="edSave">Opslaan</button></div>
      <div class="row"><select id="edList"><option value="">Opgeslagen scenario laden…</option>${Object.keys(loadAll()).map((n) => `<option>${O.esc(n)}</option>`).join("")}</select><button id="edLoad">Laden</button><button id="edDel" title="Verwijderen">✕</button></div>
      <div class="row"><button id="edDownload">Download (JSON)</button><label class="filebtn">Open bestand…<input id="edUpload" type="file" accept=".json,application/json" hidden /></label></div>
      <p class="note">Opgeslagen in deze browser. Delen: download het JSON-bestand en laat de ander het openen.</p>`;
    el.innerHTML = h;

    $("edClose").onclick = stop;
    el.querySelectorAll("[data-tool]").forEach((x) => (x.onclick = () => setTool(x.dataset.tool)));
    $("edReset").onclick = () => { E.variant = { ...clone(E.base), name: E.variant.name }; E.stale = true; renderMap(); O.renderDetail(); };
    $("edRun").onclick = run;
    if ($("edShowIso")) $("edShowIso").onchange = renderIso;
    if ($("edCsv")) $("edCsv").onclick = exportCsv;
    $("edSave").onclick = () => {
      const n = $("edName").value.trim() || E.variant.name;
      E.variant.name = n;
      const all = loadAll(); all[n] = { base: E.base, variant: E.variant };
      try { localStorage.setItem(STORE_KEY, JSON.stringify(all)); status(`Scenario '${n}' opgeslagen in deze browser.`); } catch (e) { status("Opslaan in de browser lukt niet (privévenster?). Gebruik Download.", true); }
      O.renderDetail();
    };
    $("edLoad").onclick = () => { const n = $("edList").value; const sc = loadAll()[n]; if (sc) applyScenario(sc); };
    $("edDel").onclick = () => {
      const n = $("edList").value; if (!n) return;
      const all = loadAll(); delete all[n];
      try { localStorage.setItem(STORE_KEY, JSON.stringify(all)); } catch (e) { /* noop */ }
      O.renderDetail();
    };
    $("edDownload").onclick = () => download(`${(E.variant.name || "scenario").replace(/[^\w-]+/g, "_")}.json`, JSON.stringify({ ovcatch: 1, base: E.base, variant: E.variant }, null, 1), "application/json");
    $("edUpload").onchange = async (ev) => {
      const f = ev.target.files[0]; if (!f) return;
      try { const sc = JSON.parse(await f.text()); if (!sc.base || !sc.variant) throw new Error("geen ovcatch-scenario"); applyScenario(sc); }
      catch (e) { status(`Bestand openen mislukt: ${e.message}`, true); }
    };
  }

  function loadAll() { try { return JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); } catch (e) { return {}; } }
  function applyScenario(sc) {
    E.base = sc.base; E.variant = sc.variant; E.variant.vias = E.variant.vias || [];
    E.res = null; E.stale = true;
    status(`Scenario '${E.variant.name}' geladen.`);
    renderMap(); O.renderDetail();
    const pts = E.variant.stops.map(xy);
    if (pts.length) { const bb = new maplibregl.LngLatBounds(pts[0], pts[0]); pts.forEach((p) => bb.extend(p)); O.map.fitBounds(bb, { padding: 60 }); }
  }

  function download(name, text, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  function exportCsv() {
    const S = O.S, meta = S.meta, K = meta.kengetallen;
    const num = (x, d = 0) => (Math.round(x * 10 ** d) / 10 ** d).toString().replace(".", ",");
    const pct = (b, v) => (b ? num((100 * (v - b)) / b, 1) : "");
    const rows = ["# Lijntotalen (uniek, zonder dubbeltelling; geteld per 100m-vierkant)", "modus;drempel_m;kengetal;origineel;variant;verschil;verschil_pct"];
    for (const m of meta.modi) meta.drempels[m].forEach((t, b) => K.forEach((k, i) => {
      const x = E.res.base.modes[m].line[b][i], y = E.res.variant.modes[m].line[b][i];
      rows.push([m, t, k, num(x), num(y), num(y - x), pct(x, y)].join(";"));
    }));
    rows.push("", "# Route", "kengetal;origineel;variant");
    const rb = E.res.base.route, rv = E.res.variant.route;
    rows.push(`lengte_m;${rb ? num(rb.length_m) : ""};${rv ? num(rv.length_m) : ""}`, `rijtijd_s;${rb ? num(rb.duration_s) : ""};${rv ? num(rv.duration_s) : ""}`, `haltes;${E.res.baseSc.stops.length};${E.res.varSc.stops.length}`);
    rows.push("", "# Per halte (bruto: alles binnen bereik van die halte)", ["scenario", "halte", "status", "lon", "lat", "modus", "drempel_m", ...K].join(";"));
    for (const [label, sc, r] of [["origineel", E.res.baseSc, E.res.base], ["variant", E.res.varSc, E.res.variant]]) {
      sc.stops.forEach((s, si) => meta.modi.forEach((m) => meta.drempels[m].forEach((t, b) => {
        const st = s.area == null ? "nieuw" : s.moved ? "verplaatst" : "bestaand";
        rows.push([label, `"${s.name.replace(/"/g, '""')}"`, st, num(s.lon, 6), num(s.lat, 6), m, t, ...r.modes[m].perStop[si][b].map((x) => num(x))].join(";"));
      })));
    }
    rows.push("", "# Inwoners: CBS 100m-vierkanten verdeeld over BAG-woningen (schatting). Arbeidsplaatsen: ruwe schatting uit m² BAG.");
    download(`ov-catchment-${(E.variant.name || "variant").replace(/[^\w-]+/g, "_")}.csv`, "﻿" + rows.join("\r\n"), "text/csv;charset=utf-8");
  }

  // ---------- koppeling met app.js ----------
  window.OVCEditor = {
    init(shared) {
      O = shared;
      const map = O.map;
      for (const id of ["edit-iso", "edit-base-route", "edit-base-stops", "edit-var-route"]) map.addSource(id, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "edit-iso-fill", type: "fill", source: "edit-iso", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.1 } }, "stops");
      map.addLayer({ id: "edit-iso-line", type: "line", source: "edit-iso", paint: { "line-color": ["get", "color"], "line-width": 1.2, "line-opacity": 0.8 } }, "stops");
      map.addLayer({ id: "edit-base-route", type: "line", source: "edit-base-route", paint: { "line-color": "#7a7f87", "line-width": 4, "line-opacity": 0.7, "line-dasharray": [1.5, 1.2] } });
      map.addLayer({ id: "edit-var-route", type: "line", source: "edit-var-route", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#0b6e4f", "line-width": 4.5, "line-opacity": 0.9 } });
      map.addLayer({ id: "edit-base-stops", type: "circle", source: "edit-base-stops", paint: { "circle-radius": 5, "circle-color": "#fff", "circle-stroke-color": "#7a7f87", "circle-stroke-width": 2.5 } });
    },
    start, stop, render, renderIso, onMapClick, setTool,
  };
})();
