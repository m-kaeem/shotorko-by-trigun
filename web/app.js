/* Shotorko by TRIGUN — web client.
   Vanilla JS + deck.gl (vendored, no build step). All forecasts come from the
   local FastAPI model server; nothing here is precomputed or mocked. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const TIERS = ["Low", "Medium", "High", "Critical"];
  const HEX = { Low: "#22C55E", Medium: "#FACC15", High: "#FB923C", Critical: "#F43F5E" };
  const RGB = { Low: [34, 197, 94], Medium: [250, 204, 21], High: [251, 146, 60], Critical: [244, 63, 94] };
  const TEXT_ON = { Low: "#052E16", Medium: "#422006", High: "#431407", Critical: "#170309" };
  // the whole country in the map box; recomputed from the box size by fitCountry()
  let VIEW_DHAKA = { longitude: 90.35, latitude: 23.7, zoom: 6.2, pitch: 0, bearing: 0 };
  const BD_BOUNDS = [[88.0, 20.6], [92.7, 26.65]];
  // where each city's ward grid is drawn at country scale. Chattogram is at its real place; the four Dhaka-area
  // cities are only ~40 km apart, so they are spread out north-to-south (same order as in reality) to stay readable
  const ANCHOR = { "Gazipur": [90.42, 24.62], "Dhaka North": [90.40, 24.05], "Dhaka South": [90.41, 23.45],
    "Narayanganj": [90.50, 22.95], "Chattogram": [91.80, 22.36] };
  const CELL_PX = 8;   // one ward square every 8 screen pixels
  const VIEW_ALL = { longitude: 91.0, latitude: 23.12, zoom: 7.45, pitch: 0, bearing: 0 };
  const CITY_VIEW = { "Dhaka North": [90.392, 23.852, 10.9], "Dhaka South": [90.412, 23.70, 10.9], "Gazipur": [90.43, 24.02, 11.3],
    "Narayanganj": [90.535, 23.612, 11.3], "Chattogram": [91.80, 22.356, 10.8] };
  const BN_DIG = "০১২৩৪৫৬৭৮৯";
  const ELEV = 420; // metres per expected case at the reference zoom
  const MONTHS = { en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    bn: ["জানু", "ফেব্রু", "মার্চ", "এপ্রিল", "মে", "জুন", "জুলাই", "আগস্ট", "সেপ্টে", "অক্টো", "নভে", "ডিসে"] };

  const S = {
    meta: null, dates: [], date: "2025-07-07", h: 3, city: "All", sel: "DNCC_Ward_01",
    scen: { rain: 1, temp: 0, hum: 0 }, mode: "replay", lang: "en", view: "fc", hosp: null, showHosp: false, heat: {}, heatView: "pred",
    fc: null, ward: null, playing: false, vs: { ...VIEW_DHAKA }, elev: 0, trustH: 3, val: null,
  };
  let deckgl = null, region = null, wardRows = [], byId = new Map(), colLayer = null, staticLayers = [];
  let seqF = 0, seqW = 0, prevKpi = { Critical: 0, High: 0, Medium: 0, Low: 0 };

  // ------------------------------------------------------------------ helpers
  const t = (k) => window.I18N[S.lang][k];
  const loc = (s) => (S.lang === "bn" ? String(s).replace(/[0-9]/g, (d) => BN_DIG[d]) : String(s));
  const nf = (x, d = 1) => (x == null ? "–" : loc(Number(x).toFixed(d)));
  const pad = (n) => (n < 10 ? "0" + n : "" + n);
  const fmtDate = (iso, year) => {
    const [y, m, d] = iso.split("-").map(Number);
    const s = `${d} ${MONTHS[S.lang][m - 1]}` + (year ? ` ${y}` : "");
    return loc(s);
  };
  const cityName = (c) => t("cities")[c] || c;
  const tierName = (x) => t("tiers")[x] || x;
  const wardLabel = (id) => { const w = byId.get(id); return `${t("ward")} ${loc(pad(w.n))}`; };
  const ease = (x) => 1 - Math.pow(1 - x, 3);
  const debounce = (fn, ms) => { let k; return (...a) => { clearTimeout(k); k = setTimeout(() => fn(...a), ms); }; };
  function tween(from, to, ms, onStep) {
    const t0 = performance.now();
    const step = (now) => { const p = Math.min(1, (now - t0) / ms); onStep(from + (to - from) * ease(p)); if (p < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }
  function toast(msg) { const el = $("#toast"); el.textContent = msg; el.classList.add("on"); clearTimeout(toast.k); toast.k = setTimeout(() => el.classList.remove("on"), 2600); }
  async function api(path, params = {}) {
    const u = new URL(path, location.origin);
    Object.entries(params).forEach(([k, v]) => v != null && u.searchParams.set(k, v));
    const r = await fetch(u);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  }
  const scenParams = () => ({ mode: S.mode, date: S.mode === "live" ? undefined : S.date, rain: S.scen.rain, temp: S.scen.temp, hum: S.scen.hum });
  const cur = (w) => w.h[S.h - 2];
  const shownTier = (w) => (S.view === "act" ? cur(w).obs_tier || cur(w).tier : cur(w).tier);
  const shownValue = (w) => (S.view === "act" ? (cur(w).actual ?? 0) : cur(w).pred);
  const visible = () => (S.fc ? S.fc.wards.filter((w) => S.city === "All" || byId.get(w.id).city === S.city) : []);

  // ------------------------------------------------------------------ i18n
  function applyLang() {
    document.documentElement.lang = S.lang;
    document.body.classList.toggle("lang-bn", S.lang === "bn");
    $$("[data-i]").forEach((el) => { const v = t(el.dataset.i); if (typeof v === "string") el.textContent = v; });
    $("#search").placeholder = t("searchPh");
    $$("[data-i-title]").forEach((el) => { el.title = t(el.dataset.iTitle); el.setAttribute("aria-label", t(el.dataset.iTitle)); });
    $("#langEn").setAttribute("aria-pressed", S.lang === "en");
    $("#langBn").setAttribute("aria-pressed", S.lang === "bn");
    $("#legend").innerHTML = TIERS.map((x) => `<span><i style="background:${HEX[x]}"></i>${tierName(x)}</span>`).join("");
    $("#wardList").innerHTML = wardRows.map((w) => `<option value="${cityName(w.city)} · ${wardLabel(w.id)}"></option>`).join("");
    buildChips(); buildHorizons(); buildWhatIf(); buildTicks();
    if (S.fc) renderForecast(false);
    if (S.ward) renderPanel(false);
    if (S.val) renderTrust();
    buildDataTab();
    buildHow();
    try { localStorage.setItem("sk-lang", S.lang); } catch (e) { /* storage unavailable */ }
  }

  // ------------------------------------------------------------------ static controls
  // data freshness (Requirement 5): in live mode, say where the weather came from and how old it is
  function renderFreshness() {
    const el = $("#wxFresh"); if (!el) return;
    const w = S.fc && S.fc.weather;
    if (S.mode !== "live" || !w || !w.fetched_at) { el.hidden = true; return; }
    const d = new Date(w.fetched_at);
    const when = `${fmtDate(w.fetched_at.slice(0, 10))} ${loc(String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"))}`;
    const age = w.age_hours < 1 ? t("wxJustNow") : t("wxHoursAgo")(loc(w.age_hours < 10 ? w.age_hours.toFixed(1) : Math.round(w.age_hours)));
    el.className = "wx-fresh " + (w.source === "network" || w.source === "cache" ? "ok" : "stale");
    el.textContent = t("wxFresh")({ when, age, src: (t("wxSrc") || {})[w.source] || w.source });
    el.hidden = false;
  }

  // Phase 2 operations strip: one card per module, each opening its own page
  function renderOps(hiCount) {
    const el = $("#ops"); if (!el) return;
    const q = new URLSearchParams({ date: S.date, h: S.h, lang: S.lang }).toString();
    const ic = {
      alloc: '<path d="M3 7h18M3 12h18M3 17h10"/><circle cx="18" cy="17" r="3"/>',
      overflow: '<path d="M3 21V8l9-5 9 5v13"/><path d="M9 21v-6h6v6M12 7v4M10 9h4"/>',
      sim: '<path d="M12 3v3M5.6 5.6l2.1 2.1M3 12h3M18 12h3M16.3 7.7l2.1-2.1"/><circle cx="12" cy="14" r="5"/>',
      dispatch: '<path d="M4 4h16v12H5.2L4 17.5z"/><path d="M8 9h8M8 12h5"/>',
    };
    const card = (k, title, sub, href) => `<${href ? `a href="${href}"` : 'div aria-disabled="true"'} class="op${href ? " live" : ""}">
      <span class="op-ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ic[k]}</svg></span>
      <span class="op-txt"><b>${title}</b><small>${sub}</small></span>${href ? '<span class="op-go" aria-hidden="true">→</span>' : ""}</${href ? "a" : "div"}>`;
    const live = S.mode !== "live";
    // allocation lives in the Supplies tab, overflow in the Hospital beds tab, the simulator in the Crew simulator tab (teammates' modules)
    const hasTab = (name) => !!$(`nav.tabs button[data-tab=${name}]`);
    el.innerHTML = `<h2 class="ops-title" id="opsTitle">${t("opsTitle")}</h2><div class="ops-row">
      ${card("alloc", t("opsAlloc"), hasTab("ops") ? t("opsAllocSub") : t("comingSoon"), hasTab("ops") ? "#tab=ops" : null)}
      ${card("overflow", t("opsOverflow"), hasTab("beds") ? t("opsOverflowSub") : t("comingSoon"), hasTab("beds") ? "#tab=beds" : null)}
      ${card("sim", t("opsSim"), hasTab("sim") ? t("opsSimSub") : t("comingSoon"), hasTab("sim") ? "#tab=sim" : null)}
      ${card("dispatch", t("opsDispatch"), live ? t("opsReady")(loc(hiCount)) : t("liveNoDispatch"), live ? "dispatch.html?" + q : null)}</div>`;
    $$('#ops a[href^="#tab="]').forEach((a) => a.onclick = (e) => { e.preventDefault(); switchTab(a.getAttribute("href").slice(5)); });
  }
  function buildChips() {
    const opts = ["All", ...S.meta.cities.map((c) => c.id)];
    $("#cityChips").innerHTML = opts.map((c) => `<button class="chip" data-city="${c}" aria-pressed="${c === S.city}">${c === "All" ? t("allCities") : cityName(c)}</button>`).join("");
    $$("#cityChips .chip").forEach((b) => b.onclick = () => {
      S.city = b.dataset.city; buildChips(); renderForecast(false);
      // the whole country stays in view; other cities dim
    });
  }
  function buildHorizons() {
    const tg = S.fc ? S.fc.targets : [];
    $("#horizons").innerHTML = [2, 3, 4].map((h, i) => `<button class="pill" data-h="${h}" aria-pressed="${h === S.h}"><b>${loc(h)} ${t("weeks")}</b><small>${tg[i] ? t("wkOf") + " " + fmtDate(tg[i]) : "&nbsp;"}</small></button>`).join("");
    $$("#horizons .pill").forEach((b) => b.onclick = () => setH(+b.dataset.h));
  }
  function setH(h) { S.h = h; buildHorizons(); renderForecast(false); loadWard(); }
  function buildTicks() { updateCalLabel(); if (!$("#calPop").hidden) renderCal(); }
  function buildWhatIf() {
    const wrap = $("#whatIf");
    if (!wrap) return;
    const sc = S.scen;
    const lab = (k, v, txt) => `<label for="wi-${k}"><span>${txt}</span><b id="wi-${k}-v">${v}</b></label>`;
    wrap.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><span style="display:flex;align-items:center;gap:9px"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#67E8F9" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 15a4 4 0 0 1-.9-7.9A6 6 0 0 1 17.7 8 4 4 0 0 1 17 16"/><path class="sim-drop" d="M9 18v2M13 18v3M17 17v2"/></svg><span><b style="font-size:15px;display:block" class="h-font">${t("whatIf")}</b><small style="color:var(--muted);font-size:12px">${t("whatIfSub")}</small></span></span><button class="btn" id="wiReset" style="padding:6px 10px">${t("resetScn")}</button></div>
      ${lab("rain", rainTxt(sc.rain), t("rainMult"))}<input id="wi-rain" type="range" min="0" max="3" step="0.1" value="${sc.rain}">
      ${lab("temp", tempTxt(sc.temp), t("tempD"))}<input id="wi-temp" type="range" min="-4" max="4" step="0.5" value="${sc.temp}">
      ${lab("hum", humTxt(sc.hum), t("humD"))}<input id="wi-hum" type="range" min="-20" max="20" step="1" value="${sc.hum}">
      <div class="wi-effect" id="wiEffect" aria-live="polite"></div>
      <span style="font-size:12px;color:var(--ink-2)">${t("whatIfNote")}</span>`;
    renderWhatIfEffect();
    const go = debounce(refresh, 280);
    ["rain", "temp", "hum"].forEach((k) => {
      const el = $("#wi-" + k);
      el.oninput = () => { S.scen[k] = +el.value; $("#wi-" + k + "-v").textContent = { rain: rainTxt, temp: tempTxt, hum: humTxt }[k](S.scen[k]); updateRain(); go(); };
    });
    $("#wiReset").onclick = () => { S.scen = { rain: 1, temp: 0, hum: 0 }; buildWhatIf(); updateRain(); refresh(); };
  }
  // what the weather what-if changed, as numbers: the map colours only move when a ward crosses a risk level
  function renderWhatIfEffect() {
    const el = $("#wiEffect"); if (!el) return;
    const sc = S.scen, active = sc.rain !== 1 || sc.temp !== 0 || sc.hum !== 0;
    const key = `${S.mode}|${S.mode === "live" ? "" : S.date}`;
    if (!active || !S.fc || !S.base || S.baseKey !== key) { el.innerHTML = ""; el.hidden = true; return; }
    const ids = new Set(visible().map((w) => w.id)), i = S.h - 2;
    const sum = (fc) => fc.wards.filter((w) => ids.has(w.id)).reduce((a, w) => {
      const x = w.h[i]; a.cases += x.pred; if (x.tier === "High" || x.tier === "Critical") a.hot++; return a; }, { cases: 0, hot: 0 });
    const was = sum(S.base), now = sum(S.fc), pct = was.cases > 0.05 ? Math.round((now.cases / was.cases - 1) * 100) : null;
    const sign = (v) => (v > 0 ? "+" : "") + loc(v);
    el.hidden = false;
    el.innerHTML = `<span>${t("wiCases")(loc(S.h))}</span><b>${nf(was.cases, 0)} → ${nf(now.cases, 0)}${pct != null ? ` <small>(${sign(pct)}%)</small>` : ""}</b>
      <span>${t("wiHot")}</span><b>${loc(was.hot)} → ${loc(now.hot)}</b>
      ${now.hot === was.hot && was.cases < 3 * ids.size ? `<small class="wi-hint">${t("wiQuiet")}</small>` : ""}`;
  }
  const rainTxt = (v) => (v === 1 ? t("actualWx") : "×" + nf(v, 1));
  const tempTxt = (v) => (v === 0 ? t("actualWx") : (v > 0 ? "+" : "") + nf(v, 1) + "°C");
  const humTxt = (v) => (v === 0 ? t("actualWx") : (v > 0 ? "+" : "") + nf(v, 0) + "%");

  // ------------------------------------------------------------------ map
  // Bangladesh sits in the left part of the box as a locator; the right part holds the city lenses
  const LENSES = [
    { id: "dhaka", cities: ["Gazipur", "Dhaka North", "Dhaka South", "Narayanganj"] },
    { id: "ctg", cities: ["Chattogram"] },
  ];
  function fitCountry() {
    const el = $("#stage"); if (!el || !el.clientWidth) return;
    const W = el.clientWidth, H = el.clientHeight, B = lensBoxes(W, H).loc;
    try {
      const v1 = new deck.WebMercatorViewport({ width: B.w, height: B.h }).fitBounds(BD_BOUNDS, { padding: 14 });
      const v2 = new deck.WebMercatorViewport({ width: W, height: H, longitude: v1.longitude, latitude: v1.latitude, zoom: v1.zoom });
      const [lon, lat] = v2.unproject([W - (B.x + B.w / 2), H - (B.y + B.h / 2)]);
      VIEW_DHAKA = { longitude: lon, latitude: lat, zoom: v1.zoom, pitch: 0, bearing: 0 };
    } catch (e) { console.warn(e); }
    S.vs = { ...VIEW_DHAKA };
    $("#labels").dataset.built = "";
  }
  function initDeck() {
    fitCountry();
    // key light from the upper left makes the tops bright and the two visible sides two different shades,
    // like the isometric prisms of the design prototype
    const lighting = new deck.LightingEffect({
      ambient: new deck.AmbientLight({ color: [255, 255, 255], intensity: 0.85 }),
      key: new deck.DirectionalLight({ color: [255, 255, 255], intensity: 2.4, direction: [-1.2, -0.6, -2.4] }),
      rim: new deck.DirectionalLight({ color: [120, 200, 255], intensity: 0.45, direction: [2, 1.5, -0.4] }),
    });
    deckgl = new deck.Deck({
      parent: $("#deck"),
      initialViewState: { ...VIEW_DHAKA },   // open straight on the wards: no slow fly-in
      controller: false,
      effects: [lighting],
      getCursor: ({ isHovering }) => (isHovering ? "pointer" : "default"),
      onViewStateChange: ({ viewState }) => { S.vs = viewState; refreshLayers(); },
      onAfterRender: positionLabels,
      getTooltip: ({ object, layer }) => {
        if (object && layer && layer.id === "hosp") return { html: `<b>✚ ${object.name}</b><br><span style="color:#A9B6CF">${object.note}</span>`,
          style: { background: "rgba(7,11,22,.95)", border: "1px solid #F43F5E", borderRadius: "10px", color: "#E8EEF9", fontSize: "13px", padding: "8px 10px", maxWidth: "260px" } };
        if (!object || !layer || layer.id !== "wards") return null;
        const w = byId.get(object.id), f = S.fc && S.fc.wards.find((x) => x.id === object.id);
        if (!f) return null;
        const c = cur(f), tier = shownTier(f);
        const val = S.view === "act" ? `${loc(c.actual ?? "–")} ${S.lang === "bn" ? "জন রিপোর্ট" : "reported"}` : `${nf(c.pred)} ${t("expected")} (${nf(c.lo)}–${nf(c.hi)})`;
        return { html: `<b>${cityName(w.city)} · ${wardLabel(w.id)}</b><br><span style="color:${HEX[tier]}">● ${tierName(tier)}</span> · ${val}`,
          style: { background: "rgba(7,11,22,.95)", border: "1px solid #22304D", borderRadius: "10px", color: "#E8EEF9", fontSize: "13px", padding: "8px 10px", fontFamily: "Inter, Hind Siliguri, sans-serif" } };
      },
    });
    staticLayers = [
      new deck.GeoJsonLayer({
        id: "region", data: region, filled: true, stroked: true, pickable: false,
        getFillColor: (f) => (f.properties.name === "Bangladesh" ? [16, 32, 60, 255] : [10, 17, 31, 255]),
        getLineColor: (f) => (f.properties.name === "Bangladesh" ? [34, 211, 238, 170] : [45, 64, 98, 160]),
        lineWidthMinPixels: 1.2,
      }),
    ];
    refreshLayers();
    // cinematic intro: fly in, then grow the columns
    // (no cinematic fly-in: the map opens already framed on the wards)
    setTimeout(() => tween(0, 1, 1500, (v) => { S.elev = v; refreshLayers(); }), 1300);
    requestAnimationFrame(pulseLoop);
  }
  const zoomK = () => Math.min(6, Math.max(0.4, Math.pow(2, 9.45 - (S.vs.zoom || 9.45))));
  function columnLayer() {
    const key = S.fc ? `${S.fc.ref_date}|${S.h}|${S.view}|${JSON.stringify(S.fc.scenario)}|${S.fc.mode}` : "none";
    const radius = Math.max(4, Math.min(22, 9.5 * Math.pow(2, ((S.vs.zoom || 9.45) - 9.45) * 0.75)));
    return new deck.ColumnLayer({
      id: "wards", data: wardRows, diskResolution: 4, angle: 45, radiusUnits: "pixels", radius: CELL_PX * 0.5, extruded: false, pickable: true,
      flatShading: true, wireframe: true, coverage: 1,
      getLineColor: (d) => { const f = d.f; if (!f) return [120, 140, 170, 80]; const c = RGB[shownTier(f)]; const dim = S.city !== "All" && d.city !== S.city;
        return [...c.map((v) => Math.round(v + (255 - v) * 0.55)), dim ? 30 : 200]; },
      autoHighlight: true, highlightColor: [255, 255, 255, 70],
      elevationScale: ELEV * S.elev * zoomK(),
      getPosition: (d) => [d.dlon ?? d.lon, d.dlat ?? d.lat],
      getElevation: (d) => { const f = d.f; return f ? 0.25 + shownValue(f) : 0.25; },
      getFillColor: (d) => {
        const f = d.f; if (!f) return [60, 80, 110, 120];
        const c = RGB[shownTier(f)]; const dim = S.city !== "All" && d.city !== S.city;
        return [...c, dim ? 40 : 240];
      },
      material: { ambient: 0.45, diffuse: 0.75, shininess: 64, specularColor: [90, 110, 140] },
      transitions: { getElevation: { duration: 700, easing: ease }, getFillColor: { duration: 500 }, getLineColor: { duration: 500 } },
      updateTriggers: { getElevation: key, getFillColor: [key, S.city], getLineColor: [key, S.city] },
      onClick: ({ object }) => object && selectWard(object.id, true),
    });
  }
  let ringPhase = 0;
  function ringLayer() {
    const d = byId.get(S.sel); if (!d) return null;
    const f = d.f; const col = f ? RGB[shownTier(f)] : [34, 211, 238];
    const p = ringPhase;
    return new deck.ScatterplotLayer({
      id: "ring", data: [d], getPosition: (x) => [x.dlon ?? x.lon, x.dlat ?? x.lat], radiusUnits: "pixels", getRadius: 6 + 12 * p,
      filled: false, stroked: true, lineWidthUnits: "pixels", getLineWidth: 2.5, getLineColor: [...col, Math.round(230 * (1 - p))],
      updateTriggers: { getRadius: p, getLineColor: [p, col.join()] }, pickable: false,
    });
  }
  function hospLayer() {
    if (!S.hosp || !S.showHosp) return null;
    const pulse = 0.5 + 0.5 * Math.sin(ringPhase * Math.PI * 2);
    return new deck.ScatterplotLayer({
      id: "hosp", data: S.hosp.hospitals, getPosition: (d) => [d.lon, d.lat], radiusUnits: "pixels", getRadius: 6.5,
      filled: true, getFillColor: [255, 255, 255, 235], stroked: true, lineWidthUnits: "pixels", getLineWidth: 3 + 2 * pulse,
      getLineColor: [244, 63, 94, 200], pickable: true, updateTriggers: { getLineWidth: pulse },
      onClick: () => {},
    });
  }
  const allLayers = () => [...staticLayers, hospLayer()].filter(Boolean);
  function refreshLayers() {
    if (!deckgl) return;
    colLayer = null;
    deckgl.setProps({ layers: allLayers() });
    drawLenses();
  }
  function pulseLoop(now) {
    ringPhase = ((now || 0) % 1600) / 1600;
    if (deckgl && S.showHosp) deckgl.setProps({ layers: allLayers() });
    requestAnimationFrame(pulseLoop);
  }
  function flyTo(v, ms = 1300) {
    if (!deckgl) return;
    // the map is fixed (no pan or zoom), so just jump to the view and redraw the lenses
    S.vs = { ...S.vs, ...v }; deckgl.setProps({ initialViewState: { ...S.vs } }); drawLenses();
  }
  // ---- city lenses: each ward is an irregular cell (a Voronoi cell around its point, rounded off at the edge)
  const hash01 = (str, k) => { let h = 2166136261 ^ k; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); } return ((h >>> 0) % 10000) / 10000; };
  function clipHalf(poly, a, b) {   // keep the part of poly closer to point a than to point b
    const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, nx = b[0] - a[0], ny = b[1] - a[1];
    const side = (q) => (q[0] - mx) * nx + (q[1] - my) * ny;
    const out = [];
    for (let i = 0; i < poly.length; i++) {
      const p1 = poly[i], p2 = poly[(i + 1) % poly.length], s1 = side(p1), s2 = side(p2);
      if (s1 <= 0) out.push(p1);
      if ((s1 < 0) !== (s2 < 0) && s1 !== s2) { const t2 = s1 / (s1 - s2); out.push([p1[0] + (p2[0] - p1[0]) * t2, p1[1] + (p2[1] - p1[1]) * t2]); }
    }
    return out;
  }
  const pathOf = (poly) => "M" + poly.map((q) => q[0].toFixed(1) + "," + q[1].toFixed(1)).join("L") + "Z";
  const KM_PER_DEG = 111.32;
  function lensBoxes(W, H) {
    const split = Math.round(W * 0.44), hl = Math.round(H * 0.6);
    return {
      loc: { x: 4, y: 4, w: split - 8, h: hl - 8 },
      dhaka: { x: split + 4, y: 8, w: W - split - 12, h: H - 16 },
      ctg: { x: 8, y: hl + 4, w: Math.round(split * 0.62), h: H - hl - 12 },
      legend: { x: Math.round(split * 0.62) + 18, y: hl + 10 },
    };
  }
  const LABEL_AT = { "Narayanganj": "left" };
  function drawLenses() {
    if (!deckgl || !S.fc) return;
    const el = $("#labels"), stage = $("#stage");
    const W = stage.clientWidth, H = stage.clientHeight;
    const key = [W, H, VIEW_DHAKA.zoom.toFixed(3), VIEW_DHAKA.longitude.toFixed(3), S.lang, S.city, S.h, S.view, S.fc.ref_date, S.sel, JSON.stringify(S.fc.scenario)].join("|");
    if (el.dataset.built === "1" && el.dataset.key === key) return;
    if (!W || !H) return;
    const vp = new deck.WebMercatorViewport({ width: W, height: H, ...VIEW_DHAKA });
    const boxes = lensBoxes(W, H), counts = visibleCounts();
    let svg = "", locs = "", scCommon = Infinity;
    LENSES.forEach((L) => {
      const b = boxes[L.id], ws = wardRows.filter((w) => L.cities.includes(w.city));
      if (!ws.length) return;
      // fit the lens's wards (equal-area-ish: shrink longitudes by cos(lat))
      const lat0 = ws.reduce((a, w) => a + w.lat, 0) / ws.length, kx = Math.cos(lat0 * Math.PI / 180);
      const xs = ws.map((w) => w.lon * kx), ys = ws.map((w) => w.lat);
      const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
      const padT = 34, padB = 40, padX = 26;
      const sc = Math.min(scCommon, (b.w - 2 * padX) / Math.max(1e-6, maxX - minX), (b.h - padT - padB) / Math.max(1e-6, maxY - minY));
      scCommon = Math.min(scCommon, sc);
      const cx = b.x + b.w / 2 - ((minX + maxX) / 2) * sc, cy = b.y + padT + (b.h - padT - padB) / 2 + ((minY + maxY) / 2) * sc;
      const P = (lon, lat) => [cx + lon * kx * sc, cy - lat * sc];
      // seeds, nudged a little so cells look like real, uneven wards
      const pts = ws.map((w) => { const q = P(w.lon, w.lat); return { w, q }; });
      let cells = "";
      L.cities.forEach((city) => {
        const cp = pts.filter((x) => x.w.city === city); if (!cp.length) return;
        const nn = cp.map((a) => Math.min(...cp.filter((x) => x !== a).map((x) => Math.hypot(x.q[0] - a.q[0], x.q[1] - a.q[1])), 1e9));
        const d = nn.slice().sort((a, b2) => a - b2)[Math.floor(nn.length / 2)] || 20;
        cp.forEach((a) => { a.s = [a.q[0] + (hash01(a.w.id, 1) - 0.5) * d * 0.42, a.q[1] + (hash01(a.w.id, 2) - 0.5) * d * 0.42]; });
        const R = d * 1.02, dim = S.city !== "All" && S.city !== city;
        cp.forEach((a) => {
          let poly = [];
          for (let k = 0; k < 28; k++) { const t2 = (k / 28) * Math.PI * 2, rr = R * (0.9 + 0.2 * hash01(a.w.id, 10 + k)); poly.push([a.s[0] + rr * Math.cos(t2), a.s[1] + rr * Math.sin(t2)]); }
          cp.forEach((o) => { if (o !== a && poly.length) poly = clipHalf(poly, a.s, o.s); });
          if (poly.length < 3) return;
          const f = a.w.f, tier = f ? shownTier(f) : null, sel = a.w.id === S.sel;
          cells += `<path class="ward-cell${sel ? " sel" : ""}" data-w="${a.w.id}" d="${pathOf(poly)}" fill="${tier ? HEX[tier] : "#3C506E"}" fill-opacity="${dim ? 0.18 : 0.92}"/>`;
        });
        // city name above its wards
        const left = LABEL_AT[city] === "left";
        const top = left ? cp.reduce((acc, a) => acc + a.s[1], 0) / cp.length + 4 : Math.min(...cp.map((a) => a.s[1])) - R - 3;
        const mid = left ? Math.min(...cp.map((a) => a.s[0])) - R - 4 : cp.reduce((acc, a) => acc + a.s[0], 0) / cp.length;
        const n = counts[city] || { hi: 0, n: 0 };
        cells += `<text class="lens-city${dim ? " dim" : ""}" x="${mid.toFixed(1)}" y="${top.toFixed(1)}" text-anchor="${left ? "end" : "middle"}">${cityName(city)}<tspan class="lens-cnt" dx="5">${loc(n.hi)}/${loc(n.n)}</tspan></text>`;
      });
      // scale bar
      const km = (b.w - 2 * padX) / sc * KM_PER_DEG > 30 ? 5 : 2, px = km / KM_PER_DEG * sc;
      const sbx = b.x + b.w - 12 - px - 30, sby = b.y + b.h - 11;
      // leader line from the city's real spot on the country map to the lens
      const lon = ws.reduce((a, w) => a + w.lon, 0) / ws.length, lat = lat0;
      const [mx, my] = vp.project([lon, lat]);
      const ex = Math.max(b.x + 12, Math.min(b.x + b.w - 12, mx)), ey = Math.max(b.y + 12, Math.min(b.y + b.h - 12, my));
      const sx = mx < b.x ? b.x : mx > b.x + b.w ? b.x + b.w : ex, sy = my < b.y ? b.y : my > b.y + b.h ? b.y + b.h : ey;
      locs += `<path class="lens-lead" d="M${mx.toFixed(1)},${my.toFixed(1)}L${sx.toFixed(1)},${sy.toFixed(1)}"/>
        <circle class="lens-pin" cx="${mx.toFixed(1)}" cy="${my.toFixed(1)}" r="4"/>`;
      svg += `<g class="lens" data-lens="${L.id}">
        <rect class="lens-box" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="12"/>
        ${L.cities.length > 1 ? `<text class="lens-title" x="${b.x + 12}" y="${b.y + b.h - 11}">${t("lens")[L.id]}</text>` : ""}
        ${cells}
        <g class="lens-scale"><path d="M${sbx},${sby - 4}V${sby}H${(sbx + px).toFixed(1)}V${sby - 4}"/><text x="${(sbx + px + 5).toFixed(1)}" y="${sby}">${loc(km)} ${t("lens").km}</text></g>
      </g>`;
    });
    el.innerHTML = `<svg class="lens-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${locs}${svg}</svg><div class="lens-tip" hidden></div>`;
    el.dataset.built = "1"; el.dataset.key = key;
    const lg = $("#legend"); if (lg) { lg.classList.add("in-lens"); lg.style.left = boxes.legend.x + "px"; lg.style.top = boxes.legend.y + "px"; }
    const tip = $(".lens-tip", el);
    $$(".ward-cell", el).forEach((c) => {
      c.onclick = () => selectWard(c.dataset.w, true);
      c.onmousemove = (e) => {
        const html = wardTip(c.dataset.w); if (!html) return;
        const r = stage.getBoundingClientRect();
        tip.innerHTML = html; tip.hidden = false;
        tip.style.left = Math.min(W - 10, e.clientX - r.left + 12) + "px"; tip.style.top = (e.clientY - r.top + 12) + "px";
      };
      c.onmouseleave = () => { tip.hidden = true; };
    });
  }
  function wardTip(id) {
    const w = byId.get(id), f = S.fc && S.fc.wards.find((x) => x.id === id);
    if (!w || !f) return "";
    const c = cur(f), tier = shownTier(f);
    const val = S.view === "act" ? `${loc(c.actual ?? "–")} ${S.lang === "bn" ? "জন রিপোর্ট" : "reported"}` : `${nf(c.pred)} ${t("expected")} (${nf(c.lo)}–${nf(c.hi)})`;
    return `<b>${cityName(w.city)} · ${wardLabel(w.id)}</b><br><span style="color:${HEX[tier]}">● ${tierName(tier)}</span> · ${val}`;
  }
  const positionLabels = drawLenses;
  function visibleCounts() {
    const out = {};
    (S.fc ? S.fc.wards : []).forEach((w) => {
      const c = byId.get(w.id).city; out[c] = out[c] || { hi: 0, n: 0 }; out[c].n++;
      if (["High", "Critical"].includes(shownTier(w))) out[c].hi++;
    });
    return out;
  }
  function updateRain() {
    const n = S.scen.rain === 1 ? 0 : Math.round(Math.max(4, Math.min(60, 8 + 16 * (S.scen.rain - 0.5))));
    const el = $("#rain");
    if (+el.dataset.n === n) return;
    el.dataset.n = n;
    let html = "";
    for (let i = 0; i < n; i++) {
      const r1 = ((i * 73) % 97) / 97, r2 = ((i * 41) % 89) / 89;
      html += `<i class="drop" style="left:${(1 + r1 * 98).toFixed(1)}%;animation-duration:${(0.8 + r2 * 0.9).toFixed(2)}s;animation-delay:-${(r2 * 2).toFixed(2)}s;opacity:${(0.3 + r1 * 0.5).toFixed(2)}"></i>`;
    }
    el.innerHTML = html;
  }

  // ------------------------------------------------------------------ data loading
  async function loadForecast() {
    const my = ++seqF;
    $("#stage").style.cursor = "progress";
    try {
      const p = scenParams(), active = p.rain !== 1 || p.temp !== 0 || p.hum !== 0;
      const baseKey = `${p.mode}|${p.date || ""}`;
      const [fc, base] = await Promise.all([api("/api/forecast", p),
        active && S.baseKey !== baseKey ? api("/api/forecast", { ...p, rain: 1, temp: 0, hum: 0 }) : null]);
      if (my !== seqF) return;
      if (base) { S.base = base; S.baseKey = baseKey; }
      if (!active) { S.base = fc; S.baseKey = baseKey; }
      S.fc = fc;
      if (S.mode === "live") S.date = fc.ref_date;
      const map = new Map(fc.wards.map((w) => [w.id, w]));
      wardRows.forEach((r) => { r.f = map.get(r.id); });
      renderForecast(true);
      return true;
    } catch (e) { if (my === seqF) toast(S.mode === "live" ? e.message : t("error")); return false; }
    finally { $("#stage").style.cursor = ""; }
  }
  // forecast first, then the ward panel, so the panel always matches the map
  function refresh() { return loadForecast().then((ok) => ok && loadWard()); }
  async function loadWard() {
    const my = ++seqW;
    try {
      const w = await api("/api/ward/" + S.sel, { ...scenParams(), h: S.h });
      if (my !== seqW) return;
      S.ward = w; renderPanel(true);
    } catch (e) { if (my === seqW) toast(t("error")); }
  }

  // ------------------------------------------------------------------ renderers
  function renderForecast(animate) {
    buildHorizons();
    // timeline label + badge
    if (S.mode === "replay") updateCalLabel(); else $("#dateLabel").textContent = fmtDate(S.fc.ref_date, true);
    const whenSub = $("[data-i=stepWhenSub]"); if (whenSub) whenSub.textContent = t(S.mode === "live" ? "stepWhenSubLive" : "stepWhenSub");
    renderFreshness();
    renderWhatIfEffect();
    const b = $("#sampleBadge");
    if (S.mode === "live") { b.className = "badge live"; b.textContent = t("liveBadge"); }
    else if (S.fc.out_of_sample) { b.className = "badge oos"; b.textContent = "● " + t("oos"); }
    else { b.className = "badge ins"; b.textContent = "● " + t("ins"); }
    $("#mapNote").textContent = S.view === "act" ? t("actualNote") : t("mapNote");
    // KPIs with count-up
    const vis = visible();
    const counts = { Critical: 0, High: 0, Medium: 0, Low: 0 };
    vis.forEach((w) => counts[shownTier(w)]++);
    $("#kpis").innerHTML = ["Critical", "High", "Medium", "Low"].map((x, i) => `<div class="kpi reveal" style="--c:${HEX[x]};animation-delay:${0.18 + i * 0.07}s">
      <div class="lab"><span class="dot" style="background:${HEX[x]}"></span>${tierName(x)}</div>
      <div class="val"><span data-k="${x}">${loc(prevKpi[x])}</span><small>${t("wards")}</small></div>
      <div class="meter"><i data-m="${x}"></i></div></div>`).join("");
    ["Critical", "High", "Medium", "Low"].forEach((x) => {
      const from = prevKpi[x], to = counts[x];
      tween(from, to, animate ? 800 : 1, (v) => { const n = $(`[data-k="${x}"]`); if (n) n.textContent = loc(Math.round(v)); });
      requestAnimationFrame(() => { const m = $(`[data-m="${x}"]`); if (m) m.style.width = (vis.length ? (to / vis.length) * 100 : 0) + "%"; });
    });
    prevKpi = counts;
    renderOps(counts.Critical + counts.High);
    // top 5
    const ranked = vis.slice().sort((a, b) => (S.view === "act" ? shownValue(b) - shownValue(a) : cur(b).score - cur(a).score));
    $("#top5").innerHTML = ranked.slice(0, 5).map((w, i) => {
      const c = cur(w), tier = shownTier(w), d = byId.get(w.id);
      const val = S.view === "act" ? `${loc(c.actual ?? "–")} ${S.lang === "bn" ? "জন" : "cases"}` : `${loc(Math.round(c.score))}/${loc(100)}`;
      return `<button data-w="${w.id}" class="${w.id === S.sel ? "sel" : ""}"><small>#${loc(i + 1)} · ${cityName(d.city)}</small><b>${wardLabel(w.id)}</b>
        <span style="display:flex;align-items:center;gap:6px;font-size:13px"><span class="dot" style="background:${HEX[tier]}"></span>${tierName(tier)} · ${val}</span></button>`;
    }).join("");
    $$("#top5 button").forEach((b) => b.onclick = () => selectWard(b.dataset.w, true));
    // ticker
    const hi = vis.filter((w) => ["High", "Critical"].includes(cur(w).tier)).length;
    const items = [{ c: "#22D3EE", txt: t("tickTotal")({ n: loc(hi), total: loc(vis.length), h: loc(S.h) }) }]
      .concat(vis.slice().sort((a, b) => cur(b).score - cur(a).score).slice(0, 6).map((w) => ({
        c: HEX[cur(w).tier], txt: t("tickWard")({ city: cityName(byId.get(w.id).city), ward: wardLabel(w.id), tier: tierName(cur(w).tier), pred: nf(cur(w).pred), date: fmtDate(S.fc.targets[S.h - 2]) }) })));
    const one = items.map((x) => `<span class="item"><span class="dot" style="background:${x.c}"></span>${x.txt}</span>`).join("");
    $("#ticker").innerHTML = one + one;
    $("#labels").dataset.built = "";
    refreshLayers();
    updateRain();
  }

  function selectWard(id, fly) {
    S.sel = id;
    $("#labels").dataset.built = "";
    $$("#top5 button").forEach((b) => b.classList.toggle("sel", b.dataset.w === id));
    // selecting a ward no longer moves the camera; the ward is outlined on the map and opened in the panel
    refreshLayers();
    loadWard();
  }

  // "show more" sections in the ward panel: closed by default, remembered once opened
  const OPEN = new Set();
  const more = (key, title, html) => `<details class="more" data-more="${key}"${OPEN.has(key) ? " open" : ""}>
      <summary>${title}</summary><div class="more-body">${html}</div></details>`;
  function renderPanel(animate) {
    const W = S.ward, f = S.fc && S.fc.wards.find((x) => x.id === W.ward);
    if (!f) return;
    const d = byId.get(W.ward), c = cur(f), tier = c.tier, hx = HEX[tier];
    const circ = 2 * Math.PI * 40;
    const top = W.drivers[0] || { group: "Other", mult: 1 };
    const summary = t("summary")({ tier: tierName(tier), ward: wardLabel(d.id), city: cityName(d.city), date: fmtDate(S.fc.targets[S.h - 2]),
      pred: nf(c.pred), lo: nf(c.lo), hi: nf(c.hi), normal: nf(c.normal), rain4: loc(W.rain4), ratio: nf(W.rain_ratio, 1), driver: t("groups")[top.group] || top.group, mult: nf(top.mult, 2) });
    const alert = TIERS.indexOf(tier) >= 2;
    const action = alert ? t("actionAlert")({ ward: wardLabel(d.id), date: fmtDate(S.fc.targets[S.h - 2]) }) : t("actionOk")();
    const maxLog = Math.max(0.01, ...W.drivers.map((x) => Math.abs(Math.log(x.mult))));
    const rains = W.weather.slice(-8), rmax = Math.max(1, ...rains.map((x) => x.rain));
    const body = $("#wardBody");
    // ---- plain-language pieces
    const P = t("panel");
    const ratio = c.normal >= 0.5 ? c.pred / c.normal : (c.pred >= 1 ? 2 : 1);
    const cmp = ratio >= 1.5 ? P.cmpMuch(nf(ratio, 1)) : ratio >= 1.15 ? P.cmpHigher : ratio > 0.85 ? P.cmpSame : P.cmpLower;
    const trendArrow = (i) => {
      if (i === 0) return "";
      const a = f.h[i - 1].pred, b = f.h[i].pred;
      return b > a * 1.1 ? '<em class="up" title="' + P.rising + '">↑</em>' : b < a * 0.9 ? '<em class="down" title="' + P.falling + '">↓</em>' : '<em class="flat">→</em>';
    };
    const REASON = { "Rainfall (lagged)": () => P.rRain(loc(W.rain4), nf(W.rain_ratio, 1)), "Temperature": () => P.rTemp(nf(W.temp4)),
      "Humidity": () => P.rHum(nf(W.hum4, 0)), "Recent cases": () => P.rCases, "Ward vulnerability": () => P.rWard, "Seasonality": () => P.rSeason };
    const reasons = W.drivers.filter((x) => x.mult >= 1.15 && REASON[x.group]).slice(0, 3)
      .map((x) => `<li><span class="r-ic" aria-hidden="true">↑</span><span>${REASON[x.group]()}${x.mult >= 2 ? ` <b class="r-strong">${P.strong}</b>` : ""}</span></li>`).join("");
    const chips = [
      d.density >= 52000 ? P.chipCrowded : d.density < 35000 ? P.chipSparse : null,
      d.waterlogging >= 0.6 ? P.chipFloods : d.waterlogging <= 0.4 ? P.chipDrains : null,
      d.sanitation <= 0.45 ? P.chipSanPoor : d.sanitation >= 0.65 ? P.chipSanGood : null,
    ].filter(Boolean).map((x) => `<span class="w-chip">${x}</span>`).join("");
    const todoKey = `sk-todo:${d.id}:${S.date}:${S.h}`;
    let done = {}; try { done = JSON.parse(localStorage.getItem(todoKey) || "{}"); } catch (e) { /* ignore */ }
    const todos = (P.todo[tier] || []).map((txt, i) => `<label class="w-todo-item"><input type="checkbox" data-i="${i}" ${done[i] ? "checked" : ""}><span>${txt}</span></label>`).join("");
    const dispUrl = "dispatch.html?" + new URLSearchParams({ date: S.date, h: S.h, ward: d.id, lang: S.lang, tier: "all" }).toString();
    body.innerHTML = `
      <div class="w-head">
        <div><span class="w-city">${cityName(d.city)}</span><h2 class="w-name">${wardLabel(d.id)}</h2></div>
        <span class="tier-badge" style="background:${hx};color:${TEXT_ON[tier]}">${tierName(tier)} ${t("risk")}</span>
      </div>
      <div class="w-verdict" style="--c:${hx}">
        <b>${P.verdict[tier]}</b>
        <p>${P.line({ cases: `<strong>${nf(c.pred, 0)}</strong>`, date: fmtDate(S.fc.targets[S.h - 2]), cmp })}</p>
      </div>
      <div class="w-chance" role="img" aria-label="${P.chance} ${Math.round(c.score)}%">
        <div class="w-chance-top"><span>${P.chance}</span><b>${loc(Math.round(c.score))}%</b></div>
        <div class="w-chance-bar"><i data-w="${Math.max(2, Math.min(100, c.score)).toFixed(0)}" style="background:${hx}"></i></div>
        <small>${P.chanceNote}</small>
      </div>
      <div class="w-trend" role="group" aria-label="${P.trendLabel}">${f.h.map((x, i) => `<button data-h="${i + 2}" aria-pressed="${i + 2 === S.h}">
          <small>${P.inWeeks(loc(i + 2))}</small><b>~${nf(x.pred, 0)}</b>${trendArrow(i)}
          <span class="w-dot" style="background:${HEX[x.tier]}" title="${tierName(x.tier)}"></span></button>`).join("")}</div>
      <small class="w-range">${P.likely(nf(c.lo, 0), nf(c.hi, 0))}</small>
      <div class="w-block">
        <h3>${P.whyTitle}</h3>
        ${reasons ? `<ul class="w-reasons">${reasons}</ul>` : `<p class="w-muted">${P.noSigns}</p>`}
        ${chips ? `<div class="w-chips"><span class="w-muted">${P.thisWard}</span>${chips}</div>` : ""}
      </div>
      <div class="w-block w-todo" style="--c:${hx}">
        <h3>${P.todoTitle}</h3>
        ${todos}
        <div class="w-links">
          <a class="btn w-go" href="${dispUrl}">${P.sendNotice} →</a>
          <button class="btn w-go2" data-go="ops">${P.planSupplies} →</button>
        </div>
      </div>
      ${more("why", P.whyDetail, `<div style="display:flex;flex-direction:column;gap:9px">
        ${W.drivers.slice(0, 5).map((x) => `<div class="driver"><span style="color:var(--ink-2)">${t("groups")[x.group] || x.group}</span>
          <span class="track"><i data-w="${Math.max(4, (Math.abs(Math.log(x.mult)) / maxLog) * 100).toFixed(0)}" style="background:${x.mult >= 1 ? "linear-gradient(90deg,#F97316,#FB923C)" : "linear-gradient(90deg,#0EA5E9,#38BDF8)"}"></i></span>
          <span class="m">×${nf(x.mult, 2)}</span></div>`).join("")}
        <span style="font-size:12px;color:var(--muted)">${t("whyNote")}</span></div>`)}
      ${more("chart", t("casesChart"), `<div class="chart">${chartSVG(W, f)}
        <div class="legend-row"><span><i style="width:14px;height:3px;background:#38BDF8;border-radius:2px;display:inline-block"></i>${t("reported")}</span>
        <span><i style="width:14px;height:3px;background:#FB923C;border-radius:2px;display:inline-block"></i>${t("fcRange")}</span>
        <span><i style="width:14px;border-top:2px dashed #93A1BD;display:inline-block"></i>${t("normal")}</span>
        ${S.mode === "replay" ? `<span><i style="width:9px;height:9px;border:2px solid #38BDF8;border-radius:99px;display:inline-block"></i>${t("laterActual")}</span>` : ""}</div></div>`)}
      ${more("wx", t("wxTitle"), `<div class="wx">
        <div><span style="font-size:12px;color:var(--muted)">${t("rain8")}</span>
          <div class="rainbars">${rains.map((x, i) => `<i data-h="${Math.max(2, (x.rain / rmax) * 48).toFixed(0)}" title="${loc(Math.round(x.rain))} mm" style="background:${i >= rains.length - 4 ? "linear-gradient(180deg,#67E8F9,#0891B2)" : "#2A3A5E"}"></i>`).join("")}</div>
          <b style="font-size:13px">${loc(W.rain4)} ${t("rain4")}</b></div>
        <div><span style="font-size:12px;color:var(--muted)">${t("temp")}</span><span class="big">${nf(W.temp4)}°C</span><span style="font-size:12px;color:#A9B6CF">${t("tempNote")}</span></div>
        <div><span style="font-size:12px;color:var(--muted)">${t("hum")}</span><span class="big">${nf(W.hum4, 0)}%</span><span style="font-size:12px;color:#A9B6CF">${t("humNote")}</span></div>
      </div>`)}
      ${S.hosp ? more("hosp", t("hosp").title, hospBlock(d, alert)) : ""}`;
    $$("details.more", body).forEach((el) => el.addEventListener("toggle", () => { el.open ? OPEN.add(el.dataset.more) : OPEN.delete(el.dataset.more); }));
    if (animate) body.classList.remove("fade"), void body.offsetWidth, body.classList.add("fade");
    $$(".w-trend button", body).forEach((b) => b.onclick = () => setH(+b.dataset.h));
    $$(".w-todo-item input", body).forEach((cb) => cb.onchange = () => {
      let st = {}; try { st = JSON.parse(localStorage.getItem(todoKey) || "{}"); } catch (e) { /* ignore */ }
      st[cb.dataset.i] = cb.checked; try { localStorage.setItem(todoKey, JSON.stringify(st)); } catch (e) { /* ignore */ }
    });
    const go2 = $(".w-go2", body); if (go2) go2.onclick = () => switchTab("ops");
    // animate everything in
    requestAnimationFrame(() => requestAnimationFrame(() => {
      $$(".w-chance-bar i", body).forEach((i) => i.style.width = i.dataset.w + "%");
      $$(".driver .track i", body).forEach((i) => i.style.width = i.dataset.w + "%");
      $$(".rainbars i", body).forEach((i) => i.style.height = i.dataset.h + "px");
      $$(".draw", body).forEach((p) => p.style.strokeDashoffset = 0);
      $$(".hosp .gauge i", body).forEach((i) => i.style.width = i.dataset.w + "%");
    }));
    $$(".hosp li button", body).forEach((btn) => btn.onclick = () => {
      const hh = S.hosp.hospitals[+btn.dataset.i]; S.showHosp = true; $("#hospToggle").setAttribute("aria-pressed", true);
      $("#stage").scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  function hospBlock(d, wardAlert) {
    if (!S.hosp) return "";
    const H = t("hosp"), i = S.h - 2;
    const cw = S.fc.wards.filter((w) => byId.get(w.id).city === d.city);
    const exp = cw.reduce((a, w) => a + w.h[i].pred, 0), norm = cw.reduce((a, w) => a + (w.h[i].normal || 0), 0);
    const ratio = exp / Math.max(norm, 1);
    const level = ratio < 1.15 ? 0 : ratio < 1.5 ? 1 : 2;
    const col = ["#22C55E", "#FB923C", "#F43F5E"][level];
    const alert = wardAlert || level >= 1;
    const acts = alert ? H.actionsAlert({ extra: loc(Math.max(0, Math.round(exp - norm))), h: loc(S.h) }) : H.actionsOk();
    const list = S.hosp.hospitals.map((x, k) => [x, k]).filter(([x]) => x.city === d.city);
    return `<div class="hosp"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
        <b class="h-font" style="font-size:15px">${H.title}</b><span class="badge" style="border-color:${col};color:${col}">● ${H.levels[level]}</span></div>
      <div style="font-size:13px;color:var(--ink-2)">${H.load({ city: cityName(d.city), date: fmtDate(S.fc.targets[i]), exp: nf(exp, 0), norm: nf(norm, 0) })}</div>
      <div class="load"><span class="gauge"><i data-w="${Math.min(100, ratio * 50).toFixed(0)}" style="background:${col}"></i></span><b>${nf(ratio * 100, 0)}%</b></div>
      ${alert ? `<ul style="color:var(--ink-2)">${acts.map((a) => `<li><span style="color:${col}">▸</span>${a}</li>`).join("")}</ul>` : `<span style="font-size:13px;color:var(--ink-2)">${acts[0]}</span>`}
      <ul>${list.map(([x, k]) => `<li><span class="cross"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></span>
        <span style="flex:1"><b style="font-weight:600">${x.name}</b><br><small style="color:var(--muted)">${x.note}</small></span><button data-i="${k}">${H.show}</button></li>`).join("")}</ul>
      <div class="hotline"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.8.6 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.5 2.7.6a2 2 0 0 1 1.7 2z"/></svg>${H.hotline}: <b>${loc(S.hosp.hotline.number)}</b> <small>(${S.hosp.hotline.name})</small></div>
      <small style="color:var(--muted)">${H.note}</small></div>`;
  }

  function chartSVG(W, f) {
    const hist = W.history.filter((x) => !x.future), fut = W.history.filter((x) => x.future);
    const fcs = f.h.map((x, i) => ({ i: hist.length + 1 + i, pred: x.pred, lo: x.lo, hi: x.hi }));
    const n = hist.length + 4;
    const vals = [...hist.map((x) => x.cases || 0), ...hist.map((x) => x.normal || 0), ...fcs.map((x) => x.hi), ...fut.map((x) => x.cases || 0)];
    const ymax = Math.max(4, ...vals) * 1.12;
    const X = (i) => 10 + (i / (n - 1)) * 340, Y = (v) => 128 - (v / ymax) * 112;
    const pts = (arr) => arr.map((p) => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
    const len = (arr) => arr.reduce((s, p, i) => (i ? s + Math.hypot(p[0] - arr[i - 1][0], p[1] - arr[i - 1][1]) : 0), 0);
    const hArr = S.mode === "live" ? [] : hist.map((x, i) => [X(i), Y(x.cases || 0)]);
    const last = hist.length - 1;
    const startPt = S.mode === "live" ? [X(last), Y(hist[last].normal || 0)] : [X(last), Y(hist[last].cases || 0)];
    const fArr = [startPt, ...fcs.map((x) => [X(x.i), Y(x.pred)])];
    const band = [...fcs.map((x) => [X(x.i), Y(x.hi)]), ...fcs.slice().reverse().map((x) => [X(x.i), Y(x.lo)])];
    const norm = [...hist.map((x, i) => [X(i), Y(x.normal || 0)]), ...fcs.map((x) => [X(x.i), Y(f.h[x.i - hist.length - 1].normal || 0)])];
    const hl = len(hArr), fl = len(fArr);
    const later = S.mode === "replay" ? fut.slice(1).map((x, k) => `<circle cx="${X(hist.length + 1 + k).toFixed(1)}" cy="${Y(x.cases || 0).toFixed(1)}" r="4" fill="#0A1222" stroke="#38BDF8" stroke-width="2"><title>${loc(x.cases)}</title></circle>`).join("") : "";
    return `<svg viewBox="0 0 360 140" role="img" aria-label="${t("casesChart")}">
      <line x1="0" y1="128" x2="360" y2="128" stroke="#1B2742"/>
      <polygon points="${pts(band)}" fill="#FB923C" fill-opacity=".16"/>
      <polyline points="${pts(norm)}" fill="none" stroke="#93A1BD" stroke-width="1.5" stroke-dasharray="4 4"/>
      ${hl ? `<polyline class="draw" points="${pts(hArr)}" fill="none" stroke="#38BDF8" stroke-width="2.5" stroke-linejoin="round" stroke-dasharray="${hl}" style="stroke-dashoffset:${hl}"/>` : ""}
      <polyline class="draw" points="${pts(fArr)}" fill="none" stroke="#FB923C" stroke-width="2.5" stroke-dasharray="${fl}" style="stroke-dashoffset:${fl};transition-delay:.5s"/>
      ${fcs.map((x) => `<circle cx="${X(x.i).toFixed(1)}" cy="${Y(x.pred).toFixed(1)}" r="4.5" fill="${HEX[f.h[x.i - hist.length - 1].tier]}" stroke="#0A1222" stroke-width="1.5"/>`).join("")}
      ${later}
      <line x1="${X(last).toFixed(1)}" y1="6" x2="${X(last).toFixed(1)}" y2="128" stroke="#3B4B70"/>
      <text x="${(X(last) - 4).toFixed(1)}" y="14" font-size="10" fill="#93A1BD" text-anchor="end">${S.lang === "bn" ? "আজ" : "today"}</text>
    </svg>`;
  }

  // ------------------------------------------------------------------ timeline / play
  // calendar picker (replaces the old slider): year -> month grid -> week chips, plus week/month steppers
  const isoWeek = (iso) => {
    const d = new Date(iso + "T00:00:00Z"); const day = (d.getUTCDay() + 6) % 7;
    d.setUTCDate(d.getUTCDate() - day + 3);
    const first = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
    return 1 + Math.round(((d - first) / 864e5 - 3 + ((first.getUTCDay() + 6) % 7)) / 7);
  };
  const ym = (iso) => iso.slice(0, 7);
  function updateCalLabel() {
    const [y, m] = S.date.split("-").map(Number);
    $("#calLabel").textContent = `${t("monthsFull")[m - 1]} ${loc(y)} · ${t("calWeek")} ${loc(isoWeek(S.date))}`;
    $("#dateLabel").textContent = fmtDate(S.date, true);
    const i = S.dates.indexOf(S.date);
    $("#prevWeek").disabled = i <= 0; $("#nextWeek").disabled = i >= S.dates.length - 1;
    $("#prevMonth").disabled = ym(S.date) <= ym(S.dates[0]); $("#nextMonth").disabled = ym(S.date) >= ym(S.dates[S.dates.length - 1]);
  }
  const goRefresh = debounce(refresh, 120);
  function setDate(d) {
    if (!d || d === S.date) return;
    S.date = d; S.calYear = +d.slice(0, 4); S.calMonth = +d.slice(5, 7);
    updateCalLabel(); if (!$("#calPop").hidden) renderCal(); goRefresh();
  }
  function stepWeek(k) { const i = S.dates.indexOf(S.date) + k; if (i >= 0 && i < S.dates.length) setDate(S.dates[i]); }
  function stepMonth(k) {
    let [y, m] = S.date.split("-").map(Number); m += k; if (m < 1) { m = 12; y--; } if (m > 12) { m = 1; y++; }
    const target = `${y}-${String(m).padStart(2, "0")}`;
    const hit = k > 0 ? S.dates.find((d) => ym(d) >= target) : [...S.dates].reverse().find((d) => ym(d) <= target && ym(d) < ym(S.date));
    // land on the same position in the month when possible
    const sameMonth = S.dates.filter((d) => hit && ym(d) === ym(hit));
    const pos = S.dates.filter((d) => ym(d) === ym(S.date)).indexOf(S.date);
    setDate(sameMonth[Math.min(pos, sameMonth.length - 1)] || hit);
  }
  function renderCal() {
    const pop = $("#calPop"), years = [...new Set(S.dates.map((d) => +d.slice(0, 4)))];
    const Y = S.calYear, M = S.calMonth, W = S.meta.weekly_cases || {};
    const monthTot = {}; S.dates.forEach((d) => { monthTot[ym(d)] = (monthTot[ym(d)] || 0) + (W[d] || 0); });
    const maxM = Math.max(1, ...Object.values(monthTot));
    const testYm = ym(S.meta.test_start);
    const months = t("monthsFull").map((name, i) => {
      const key = `${Y}-${String(i + 1).padStart(2, "0")}`, has = key in monthTot, tot = monthTot[key] || 0;
      return `<button class="month" data-m="${i + 1}" aria-pressed="${i + 1 === M}" ${has ? "" : "disabled"}>
        <span class="heat-fill" style="height:${((tot / maxM) * 100).toFixed(0)}%"></span>${key >= testYm && has ? '<span class="tst"></span>' : ""}
        <b>${name}</b><small>${has ? loc(tot.toLocaleString("en")) + " " + (tot === 1 && S.lang === "en" ? "case" : t("calCases")) : "—"}</small></button>`;
    }).join("");
    const wk = S.dates.filter((d) => ym(d) === `${Y}-${String(M).padStart(2, "0")}`);
    const maxW = Math.max(1, ...wk.map((d) => W[d] || 0));
    const weeks = wk.map((d) => `<button class="wk" data-d="${d}" aria-pressed="${d === S.date}"><b>${t("calWeek")} ${loc(isoWeek(d))}</b><small>${fmtDate(d)}</small>
        <i style="width:${(((W[d] || 0) / maxW) * 72 + 6).toFixed(0)}px"></i></button>`).join("");
    pop.innerHTML = `<div class="cal-head"><button class="step" data-y="-1" ${Y <= years[0] ? "disabled" : ""} aria-label="Previous year"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg></button>
      <b>${loc(Y)}</b><button class="step" data-y="1" ${Y >= years[years.length - 1] ? "disabled" : ""} aria-label="Next year"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg></button></div>
      <div class="months">${months}</div>
      <div class="eyebrow" style="margin-top:14px">${t("calWeeksIn")(t("monthsFull")[M - 1] + " " + loc(Y))}</div>
      <div class="weeks" style="border-top:0;padding-top:6px;margin-top:6px">${weeks || `<small style="color:var(--muted)">${t("calPick")}</small>`}</div>
      <div class="cal-legend"><span><i style="display:inline-block;width:10px;height:10px;border-radius:3px;background:linear-gradient(180deg,#FB923C,#F43F5E)"></i> ${t("calLegendHeat")}</span><span><i style="display:inline-block;width:8px;height:8px;border-radius:99px;background:#22C55E"></i> ${t("calLegendTest")}</span></div>`;
    $$("[data-y]", pop).forEach((b) => b.onclick = () => {
      S.calYear += +b.dataset.y;
      const firstM = S.dates.find((d) => +d.slice(0, 4) === S.calYear);
      if (!S.dates.some((d) => ym(d) === `${S.calYear}-${String(S.calMonth).padStart(2, "0")}`) && firstM) S.calMonth = +firstM.slice(5, 7);
      renderCal();
    });
    $$(".month", pop).forEach((b) => b.onclick = () => { S.calMonth = +b.dataset.m; renderCal(); });
    $$(".wk", pop).forEach((b) => b.onclick = () => { setDate(b.dataset.d); closeCal(); });
  }
  function openCal() { S.calYear = +S.date.slice(0, 4); S.calMonth = +S.date.slice(5, 7); renderCal(); $("#calPop").hidden = false; $("#calBtn").setAttribute("aria-expanded", true); }
  function closeCal() { $("#calPop").hidden = true; $("#calBtn").setAttribute("aria-expanded", false); }
  function initTimeline() {
    updateCalLabel();
    $("#calBtn").onclick = (e) => { e.stopPropagation(); $("#calPop").hidden ? openCal() : closeCal(); };
    $("#calPop").addEventListener("click", (e) => e.stopPropagation());
    document.addEventListener("click", () => { if (!$("#calPop").hidden) closeCal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#calPop").hidden) { closeCal(); $("#calBtn").focus(); } });
    $("#prevWeek").onclick = () => stepWeek(-1); $("#nextWeek").onclick = () => stepWeek(1);
    $("#prevMonth").onclick = () => stepMonth(-1); $("#nextMonth").onclick = () => stepMonth(1);
    $("#playBtn").onclick = () => (S.playing ? stopPlay() : startPlay());
  }
  async function startPlay() {
    S.playing = true;
    $("#playBtn").classList.add("on"); $("#playBtn").setAttribute("aria-label", t("pause"));
    $("#playIcon").innerHTML = '<path d="M6 4h4v16H6zM14 4h4v16h-4z"/>';
    let i = S.dates.indexOf(S.date);
    if (i >= S.dates.length - 1 || i < S.dates.indexOf(S.meta.test_start)) i = S.dates.indexOf(S.meta.test_start) - 1;
    while (S.playing && i < S.dates.length - 1) {
      i++; S.date = S.dates[i]; updateCalLabel(); if (!$("#calPop").hidden) renderCal();
      const t0 = performance.now();
      await loadForecast();
      if (i % 3 === 0) loadWard();
      await new Promise((r) => setTimeout(r, Math.max(0, 1500 - (performance.now() - t0))));
    }
    stopPlay(); loadWard();
  }
  function stopPlay() {
    S.playing = false;
    $("#playBtn").classList.remove("on"); $("#playBtn").setAttribute("aria-label", t("play"));
    $("#playIcon").innerHTML = '<path d="M7 4v16l13-8z"/>';
  }

  // ------------------------------------------------------------------ trust tab
  async function loadTrust() {
    if (!S.val) { try { S.val = await api("/api/validation"); } catch (e) { toast(t("error")); return; } }
    renderTrust();
  }
  async function renderTrust() {
    const M = S.val.metrics.horizons, h = String(S.trustH), m = M[h];
    $("#trustH").innerHTML = [2, 3, 4].map((x) => `<button class="pill" data-h="${x}" aria-pressed="${x === S.trustH}"><b>${loc(x)} ${t("weeks")}</b></button>`).join("");
    $$("#trustH .pill").forEach((b) => b.onclick = () => { S.trustH = +b.dataset.h; renderTrust(); });
    const cut = 1 - m["LightGBM (deployed)"].MAE / m["Seasonal naive (same week last year)"].MAE;
    const cards = [[cut, t("maeCut")], [m.PI80_coverage, t("coverage")], [m.alert_sensitivity, t("sens")], [m.alert_precision, t("prec")]];
    $("#trustCards").innerHTML = cards.map(([v, l], i) => `<div class="stat reveal" style="animation-delay:${i * 0.07}s"><span class="v" data-v="${v}">${loc(0)}%</span><small>${l}</small></div>`).join("");
    $$("#trustCards .v").forEach((n) => tween(0, +n.dataset.v * 100, 900, (v) => n.textContent = loc(Math.round(v)) + "%"));
    // grouped MAE bars
    const names = Object.keys(t("models"));
    const cols = ["#22D3EE", "#8B7CF6", "#1BAF7A", "#2A78D6", "#7A8599"];
    const maxv = Math.max(...["2", "3", "4"].flatMap((k) => names.map((n) => M[k][n].MAE)));
    const W = 560, H = 230, gw = (W - 40) / 3, bw = (gw - 30) / names.length;
    let bars = "";
    ["2", "3", "4"].forEach((k, gi) => {
      names.forEach((n, ni) => {
        const v = M[k][n].MAE, bh = (v / maxv) * (H - 50), x = 30 + gi * gw + 10 + ni * bw, y = H - 30 - bh;
        bars += `<rect x="${x.toFixed(1)}" y="${(H - 30).toFixed(1)}" width="${(bw - 3).toFixed(1)}" height="0" rx="3" fill="${cols[ni]}" data-y="${y.toFixed(1)}" data-h="${bh.toFixed(1)}"><title>${t("models")[n]}: ${nf(v, 2)}</title></rect>`;
        if (ni === 0) bars += `<text x="${(x + bw / 2).toFixed(1)}" y="${(y - 6).toFixed(1)}" font-size="11" fill="#E8EEF9" text-anchor="middle">${nf(v, 2)}</text>`;
      });
      bars += `<text x="${(30 + gi * gw + gw / 2).toFixed(1)}" y="${H - 10}" font-size="12" fill="#93A1BD" text-anchor="middle">${loc(k)} ${t("weeks")}</text>`;
    });
    $("#maeChart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${bars}</svg>
      <div class="legend-row" style="margin-top:6px">${names.map((n, i) => `<span><i style="width:10px;height:10px;border-radius:3px;background:${cols[i]};display:inline-block"></i>${t("models")[n]}</span>`).join("")}</div>`;
    requestAnimationFrame(() => $$("#maeChart rect").forEach((r, i) => setTimeout(() => {
      r.style.transition = "all .7s cubic-bezier(.2,.8,.2,1)"; r.setAttribute("y", r.dataset.y); r.setAttribute("height", r.dataset.h);
    }, i * 25)));
    $("#splitTable").innerHTML = `<tr><th></th><th>${S.lang === "bn" ? "সময়" : "Weeks"}</th><th>${S.lang === "bn" ? "কাজ" : "Used for"}</th></tr>` +
      t("split").map((r, i) => `<tr style="${i === 2 ? "color:#86EFAC;font-weight:600" : ""}"><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td></tr>`).join("");
    // confusion matrix
    const cm = m.tier_confusion, mx = Math.max(...cm.flat());
    $("#cmGrid").innerHTML = `<div></div>${TIERS.map((x) => `<div style="color:var(--muted)">${t("pred")}<br>${tierName(x)}</div>`).join("")}` +
      cm.map((row, i) => `<div style="color:var(--muted);text-align:right">${t("obs")} ${tierName(TIERS[i])}</div>` +
        row.map((v, j) => `<div style="background:rgba(34,211,238,${(0.06 + 0.8 * v / mx).toFixed(2)});color:${v / mx > 0.5 ? "#070B16" : "#E8EEF9"};${i === j ? "outline:1px solid #22D3EE" : ""}">${loc(v)}</div>`).join("")).join("");
    // month errors
    const bm = S.val.by_month[h], mmax = Math.max(...bm.map((x) => x.rmse));
    $("#monthChart").innerHTML = `<svg viewBox="0 0 520 200" style="width:100%;height:auto">${bm.map((x, i) => {
      const xx = 20 + i * 41, h1 = (x.mae / mmax) * 150, h2 = (x.rmse / mmax) * 150;
      return `<rect x="${xx}" y="${170 - h1}" width="16" height="${h1}" rx="3" fill="#2A78D6"><title>MAE ${nf(x.mae, 2)}</title></rect>
        <rect x="${xx + 18}" y="${170 - h2}" width="16" height="${h2}" rx="3" fill="#FB923C"><title>RMSE ${nf(x.rmse, 2)}</title></rect>
        <text x="${xx + 17}" y="188" font-size="11" fill="#93A1BD" text-anchor="middle">${MONTHS[S.lang][x.month - 1]}</text>`;
    }).join("")}</svg><div class="legend-row"><span><i style="width:10px;height:10px;background:#2A78D6;border-radius:3px;display:inline-block"></i>MAE</span><span><i style="width:10px;height:10px;background:#FB923C;border-radius:3px;display:inline-block"></i>RMSE</span></div>`;
    // backtest for the selected ward
    $("#btTitle").textContent = `${t("backtestTitle")} · ${cityName(byId.get(S.sel).city)} ${wardLabel(S.sel)}`;
    try {
      const bt = await api("/api/backtest/" + S.sel, { h: S.trustH });
      const P = bt.points, n = P.length, ym = Math.max(4, ...P.map((p) => Math.max(p.hi, p.actual))) * 1.08;
      const X = (i) => 30 + (i / (n - 1)) * 1060, Y = (v) => 200 - (v / ym) * 180;
      const band = [...P.map((p, i) => `${X(i).toFixed(1)},${Y(p.hi).toFixed(1)}`), ...P.slice().reverse().map((p, k) => `${X(n - 1 - k).toFixed(1)},${Y(p.lo).toFixed(1)}`)].join(" ");
      const pl = (key) => P.map((p, i) => `${X(i).toFixed(1)},${Y(p[key]).toFixed(1)}`).join(" ");
      const yr = P.findIndex((p) => p.date >= "2025-01-01");
      $("#btChart").innerHTML = `<svg viewBox="0 0 1100 220" style="width:100%;height:auto">
        <polygon points="${band}" fill="#FB923C" fill-opacity=".15"/>
        <polyline class="draw" points="${pl("pred")}" fill="none" stroke="#FB923C" stroke-width="2" stroke-dasharray="3000" style="stroke-dashoffset:3000"/>
        <polyline class="draw" points="${pl("actual")}" fill="none" stroke="#38BDF8" stroke-width="2" stroke-dasharray="3000" style="stroke-dashoffset:3000;transition-delay:.3s"/>
        <line x1="${X(yr)}" y1="10" x2="${X(yr)}" y2="200" stroke="#3B4B70" stroke-dasharray="3 3"/>
        <text x="40" y="18" font-size="12" fill="#93A1BD">${loc(2024)}</text><text x="${X(yr) + 8}" y="18" font-size="12" fill="#93A1BD">${loc(2025)}</text>
      </svg><div class="legend-row"><span><i style="width:14px;height:3px;background:#38BDF8;display:inline-block"></i>${t("reported")}</span><span><i style="width:14px;height:3px;background:#FB923C;display:inline-block"></i>${t("fcRange")}</span></div>`;
      requestAnimationFrame(() => requestAnimationFrame(() => $$("#btChart .draw").forEach((p) => p.style.strokeDashoffset = 0)));
    } catch (e) { /* leave empty */ }
    renderSplits(); renderRobust(); renderHeatmap();
  }

  // ----- season heatmap (ward x week)
  async function renderHeatmap() {
    const h = S.trustH;
    if (!S.heat[h]) { try { S.heat[h] = await api("/api/heatmap", { h }); } catch (e) { return; } }
    const H = S.heat[h], M = S.heatView === "pred" ? H.pred_tier : H.obs_tier;
    const nW = H.wards.length, nD = H.dates.length, lw = 112, cw = (1100 - lw) / nD, ch = 8.5;
    const shade = ["#173828", "#FACC15", "#FB923C", "#F43F5E"];
    let cells = "", labels = "", prevCity = null;
    H.wards.forEach((w, r) => {
      const y = 18 + r * ch;
      if (H.cities[r] !== prevCity) {
        prevCity = H.cities[r];
        labels += `<text x="${lw - 8}" y="${y + 8}" font-size="11" fill="#C7D2E6" text-anchor="end" font-weight="600">${cityName(prevCity)}</text>`;
        if (r) labels += `<line x1="${lw}" x2="1100" y1="${y}" y2="${y}" stroke="#070B16" stroke-width="2"/>`;
      }
      let row = "";
      for (let c = 0; c < nD; c++) row += `<rect x="${(lw + c * cw).toFixed(1)}" y="${y.toFixed(1)}" width="${(cw + 0.2).toFixed(1)}" height="${ch - 1}" fill="${shade[M[r][c]]}"/>`;
      cells += `<g class="hrow" data-w="${w}" style="cursor:pointer"><title>${cityName(H.cities[r])} ${wardLabel(w)}</title>${row}</g>`;
    });
    let xt = "";
    H.dates.forEach((d, c) => { if (d.slice(8) <= "07" && ["01", "04", "07", "10"].includes(d.slice(5, 7))) xt += `<text x="${(lw + c * cw).toFixed(1)}" y="12" font-size="10" fill="#93A1BD">${MONTHS[S.lang][+d.slice(5, 7) - 1]} ${loc(d.slice(2, 4))}</text>`; });
    $("#heatmap").innerHTML = `<div class="heat" style="overflow-x:auto"><svg viewBox="0 0 1100 ${22 + nW * ch}" style="width:100%;min-width:720px;height:auto">${xt}${cells}${labels}</svg></div>
      <div class="legend-row" style="margin-top:8px">${TIERS.map((x, i) => `<span><i style="width:10px;height:10px;border-radius:3px;background:${shade[i]};display:inline-block"></i>${tierName(x)}</span>`).join("")}</div>`;
    $$("#heatmap .hrow").forEach((g) => g.onclick = () => { switchTab("map"); selectWard(g.dataset.w, true); });
  }

  // ----- data distribution per split
  function renderSplits() {
    const P = S.val.metrics.split_profile; if (!P) return;
    const st = t("st"), names = t("splitNames"), cols = { train: "#2A78D6", validation: "#8B7CF6", test: "#22D3EE" };
    const pct = (x) => nf(x * 100, 1) + "%";
    $("#splitCards").innerHTML = Object.entries(P).map(([k, v]) => {
      const total = Object.values(v.cases_per_year).reduce((a, b) => a + b, 0);
      return `<div class="stat split-card" style="border-top:3px solid ${cols[k]}"><h4>${names[k]}<small style="font-size:12px;color:var(--muted);font-family:Inter">${fmtDate(v.from, true)} – ${fmtDate(v.to, true)}</small></h4>
        <dl><dt>${st.weeks} · ${st.rows}</dt><dd>${loc(v.weeks)} · ${loc(v.rows.toLocaleString("en"))}</dd>
        <dt>${st.total}</dt><dd>${loc(total.toLocaleString("en"))}</dd>
        <dt>${st.mean}</dt><dd>${nf(v.cases_mean, 2)}</dd>
        <dt>${st.median} · ${st.sd} · ${st.max}</dt><dd>${nf(v.cases_median, 0)} · ${nf(v.cases_std, 2)} · ${loc(v.cases_max)}</dd>
        <dt>${st.zero}</dt><dd>${pct(v.zero_share)}</dd><dt>${st.hi}</dt><dd>${pct(v.high_plus_share)}</dd>
        <dt>${st.rain}</dt><dd>${nf(v.rain_mean, 1)} mm</dd><dt>${st.temp} · ${st.hum}</dt><dd>${nf(v.temp_mean, 1)}°C · ${nf(v.hum_mean, 0)}%</dd></dl></div>`;
    }).join("");
    // histogram (share of ward-weeks per bin)
    const bins = Object.keys(P.train.hist), keys = Object.keys(P);
    const share = (k, b) => P[k].hist[b] / P[k].rows;
    const mx = Math.max(...keys.flatMap((k) => bins.map((b) => share(k, b))));
    const W = 560, Hh = 220, gw = (W - 30) / bins.length, bw = (gw - 14) / 3;
    let svg = "";
    bins.forEach((b, i) => {
      keys.forEach((k, j) => {
        const v = share(k, b), bh = (v / mx) * 160, x = 20 + i * gw + 7 + j * bw;
        svg += `<rect x="${x.toFixed(1)}" y="${(185 - bh).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${bh.toFixed(1)}" rx="2" fill="${cols[k]}"><title>${names[k]} · ${b}: ${pct(v)}</title></rect>`;
      });
      svg += `<text x="${(20 + i * gw + gw / 2).toFixed(1)}" y="203" font-size="11" fill="#93A1BD" text-anchor="middle">${loc(b)}</text>`;
    });
    const legend = `<div class="legend-row">${keys.map((k) => `<span><i style="width:10px;height:10px;border-radius:3px;background:${cols[k]};display:inline-block"></i>${names[k]}</span>`).join("")}</div>`;
    $("#histChart").innerHTML = `<svg viewBox="0 0 ${W} ${Hh}" style="width:100%;height:auto">${svg}</svg>${legend}`;
    // seasonal lines
    const smx = Math.max(...keys.flatMap((k) => P[k].monthly_mean_cases));
    let lines = "";
    keys.forEach((k) => {
      const pts = P[k].monthly_mean_cases.map((v, m) => `${(30 + m * 45).toFixed(1)},${(180 - (v / smx) * 160).toFixed(1)}`).join(" ");
      lines += `<polyline class="draw" points="${pts}" fill="none" stroke="${cols[k]}" stroke-width="2.5" stroke-dasharray="900" style="stroke-dashoffset:900"/>`;
    });
    lines += MONTHS[S.lang].map((m, i) => `<text x="${30 + i * 45}" y="200" font-size="11" fill="#93A1BD" text-anchor="middle">${m}</text>`).join("");
    $("#seasonChart").innerHTML = `<svg viewBox="0 0 560 210" style="width:100%;height:auto">${lines}</svg>${legend}`;
    requestAnimationFrame(() => requestAnimationFrame(() => $$("#seasonChart .draw").forEach((x) => x.style.strokeDashoffset = 0)));
    // accuracy per split
    const bs = S.val.metrics.horizons[String(S.trustH)].by_split, rows = t("accRows"), mods = t("accModels");
    $("#accTable").innerHTML = `<table class="acc"><tr><th></th><th>MAE</th><th>RMSE</th><th>WAPE</th><th>R²</th><th>n</th></tr>` +
      Object.entries(bs).map(([mk, splits]) => `<tr><td colspan="6" style="color:var(--cyan-2);font-weight:600;text-align:left">${mods[mk]}</td></tr>` +
        Object.entries(splits).map(([sk, m]) => `<tr class="${sk === "test" && mk === "deployed_model" ? "hl" : ""}"><td>${rows[sk] || sk}</td><td>${nf(m.MAE, 3)}</td><td>${nf(m.RMSE, 3)}</td><td>${nf(m.WAPE * 100, 1)}%</td><td>${nf(m.R2, 3)}</td><td>${loc(m.n.toLocaleString("en"))}</td></tr>`).join("")).join("") + `</table>`;
    $("#accTitle") && 0;
  }

  // ----- robustness to missing data
  function renderRobust() {
    const C = S.val.metrics.horizons[String(S.trustH)].completeness; if (!C) return;
    const sc = Object.entries(C.scenarios), clim = C.climatology_baseline.MAE;
    const mx = Math.max(clim, ...sc.flatMap(([, v]) => [v.deployed.MAE, v.robust.MAE])) * 1.08;
    const lw = 300, W = 1100, rh = 34, x = (v) => lw + (v / mx) * (W - lw - 70);
    let svg = "";
    sc.forEach(([k, v], i) => {
      const y = 10 + i * rh;
      svg += `<text x="${lw - 10}" y="${y + 18}" font-size="12.5" fill="#C7D2E6" text-anchor="end">${t("scen")[k] || v.label}</text>
        <rect x="${lw}" y="${y + 3}" width="0" data-w="${(x(v.deployed.MAE) - lw).toFixed(1)}" height="11" rx="3" fill="#7A8599"><title>${nf(v.deployed.MAE, 3)}</title></rect>
        <rect x="${lw}" y="${y + 16}" width="0" data-w="${(x(v.robust.MAE) - lw).toFixed(1)}" height="11" rx="3" fill="#22D3EE"><title>${nf(v.robust.MAE, 3)}</title></rect>
        <text x="${(x(v.deployed.MAE) + 6).toFixed(1)}" y="${y + 13}" font-size="11" fill="#93A1BD">${nf(v.deployed.MAE, 2)}</text>
        <text x="${(x(v.robust.MAE) + 6).toFixed(1)}" y="${y + 26}" font-size="11" fill="#E8EEF9" font-weight="600">${nf(v.robust.MAE, 2)}</text>`;
    });
    const H = 14 + sc.length * rh;
    svg += `<line x1="${x(clim).toFixed(1)}" x2="${x(clim).toFixed(1)}" y1="4" y2="${H}" stroke="#F43F5E" stroke-dasharray="5 4"/><text x="${(x(clim) + 4).toFixed(1)}" y="${H}" font-size="11" fill="#FDA4AF">${nf(clim, 2)}</text>`;
    const L = t("robLegend");
    $("#robChart").innerHTML = `<div style="overflow-x:auto"><svg viewBox="0 0 ${W} ${H + 6}" style="width:100%;min-width:720px;height:auto">${svg}</svg></div>
      <div class="legend-row"><span><i style="width:12px;height:10px;border-radius:3px;background:#7A8599;display:inline-block"></i>${L[0]}</span><span><i style="width:12px;height:10px;border-radius:3px;background:#22D3EE;display:inline-block"></i>${L[1]}</span><span><i style="width:14px;border-top:2px dashed #F43F5E;display:inline-block"></i>${L[2]}</span><span style="color:var(--muted)">MAE · ${loc(S.trustH)} ${t("weeks")}</span></div>`;
    requestAnimationFrame(() => $$("#robChart rect").forEach((r, i) => setTimeout(() => { r.style.transition = "width .7s cubic-bezier(.2,.8,.2,1)"; r.setAttribute("width", r.dataset.w); }, i * 30)));
  }

  // ------------------------------------------------------------------ your data (upload)
  function buildDataTab() {
    $("#formatList").innerHTML = "<ul style='margin:0;padding-left:18px'>" + t("formatList").map((x) => `<li>${x}</li>`).join("") + "</ul>";
    if (S.upload) renderUpload(S.upload);
  }
  async function sendCsv(text) {
    $("#uploadOut").innerHTML = `<div class="card" style="margin-top:12px">${t("uploading")}<div class="skeleton" style="height:90px;margin-top:10px"></div></div>`;
    try {
      const r = await fetch("/api/upload", { method: "POST", headers: { "Content-Type": "text/csv" }, body: text });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      S.upload = j; renderUpload(j);
    } catch (e) { $("#uploadOut").innerHTML = `<div class="action alert" style="margin-top:12px">${e.message}</div>`; }
  }
  function renderUpload(j) {
    const U = t("up"), I = j.info;
    const g = Object.entries(I.groups).map(([k, v]) => `<span class="gbadge ${v ? "ok" : "no"}">${v ? "✓" : "✕"} ${U.groups[k]}</span>`).join("");
    const byWard = {};
    j.forecasts.forEach((f) => (byWard[f.ward] = byWard[f.ward] || []).push(f));
    const cards = Object.entries(byWard).map(([w, fs]) => {
      fs.sort((a, b) => a.horizon - b.horizon);
      const top = fs.find((f) => f.horizon === S.h) || fs[0], col = HEX[top.tier];
      return `<div class="up-card" style="--c:${col}">
        <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start"><div><span class="eyebrow">${top.city ? cityName(top.city) : "—"}</span><br><b class="h-font" style="font-size:18px">${w.replace(/_/g, " ")}</b></div>
          <span class="tier-badge" style="background:${col};color:${TEXT_ON[top.tier]};margin:0">${tierName(top.tier)} · ${loc(top.score)}</span></div>
        <div class="hs">${fs.map((f) => `<div>${loc(f.horizon)} ${t("weeks")} · ${fmtDate(f.target_date)}<b>${nf(f.pred)}</b>${t("range80")}: ${nf(f.lo)}–${nf(f.hi)}<br><span style="color:${HEX[f.tier]}">● ${tierName(f.tier)}</span>${f.actual_in_dataset != null ? `<br><small style="color:var(--muted)">${U.actual(loc(f.actual_in_dataset))}</small>` : ""}</div>`).join("")}</div>
        <small style="color:var(--ink-2)">${U.model[top.model]} · ${nf(top.features_available * 100, 0)}% ${U.feats} · ${loc(top.weeks_of_history)} ${U.weeks} · ${U.from} ${fmtDate(top.ref_date, true)}</small>
        <small style="color:var(--cyan-2)">${U.expErr(nf(top.expected_mae, 2))} — ${t("scen")[top.scenario] || top.scenario_label}</small></div>`;
    }).join("");
    $("#uploadOut").innerHTML = `<div class="card fade" style="margin-top:12px;display:flex;flex-direction:column;gap:6px">
      <div>${g}</div>
      <small style="color:var(--muted)">${U.found}: ${I.columns_recognised.join(", ")}${I.filled_from_ward_registry.length ? " · " + U.filled + ": " + I.filled_from_ward_registry.join(", ") : ""}${I.aggregated_from_daily ? " · " + U.daily : ""}</small></div>
      <div class="up-grid">${cards}</div>`;
  }
  function switchTab(name) { const b = $(`nav.tabs button[data-tab=${name}]`); if (b) b.click(); }

  // ------------------------------------------------------------------ how tab
  let clockTimer = null;
  async function buildHow() {
    const icons = [
      '<path d="M7 16a4 4 0 0 1-.9-7.9A6 6 0 0 1 17.7 9 4 4 0 0 1 17 17"/><path d="M8 19v2M12 19v2M16 19v2"/>',
      '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
      '<path d="M12 8c-2 0-3 1.5-3 3.5S10 16 12 16s3-2.5 3-4.5S14 8 12 8z"/><path d="M12 8V5M9 10 5 7M15 10l4-3M9 13l-5 2M15 13l5 2M10.5 16 8 20M13.5 16l2.5 4"/>',
      '<path d="M3 21h18M5 21V7l7-4 7 4v14"/><path d="M10 12h4M12 10v4"/>',
    ];
    $("#clock").innerHTML = t("steps").map((s, i) => `<div class="stage-step" data-i="${i}"><div class="ic"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[i]}</svg></div>
      <span class="eyebrow">${s[1]}</span><b class="h-font" style="font-size:18px">${s[0]}</b><span style="font-size:14px;color:var(--ink-2);line-height:1.5">${s[2]}</span></div>`).join("");
    let k = 0;
    clearInterval(clockTimer);
    const tick = () => {
      $$(".stage-step").forEach((el, i) => el.classList.toggle("lit", i === k));
      const fill = $("#clockFill"); fill.style.transition = k === 0 ? "none" : "width 1.3s linear"; fill.style.width = ((k + 1) / 4 * 100) + "%";
      k = (k + 1) % 4;
    };
    tick(); clockTimer = setInterval(tick, 1500);
    let u = { model_calls: 0, last_inference_ms: 0, weather_api_today: { network_calls: 0 }, n_models: 9 };
    try { u = await api("/api/usage"); } catch (e) { /* offline */ }
    $("#hood").innerHTML = t("hood")({ models: loc(u.n_models), ms: loc(Math.round(u.last_inference_ms || 0)), calls: loc(u.weather_api_today.network_calls) })
      .map(([v, l]) => `<div class="stat"><span class="v">${v}</span><small>${l}</small></div>`).join("");
  }

  // ------------------------------------------------------------------ tour
  function tour(start = 0) {
    const steps = [["#stage", 0], ["#wardBody", 1], ["#whatIf", 2], ["#timeline", 3]];
    const el = $("#tour"); let i = start;
    const show = () => {
      if (i >= steps.length) { el.classList.remove("on"); try { localStorage.setItem("sk-toured", "1"); } catch (e) { /* ignore */ } return; }
      const target = $(steps[i][0]); target.scrollIntoView({ behavior: "auto", block: "center" });
      setTimeout(() => {
        const r = target.getBoundingClientRect(), p = 8;
        const hole = $(".hole", el);
        Object.assign(hole.style, { left: r.left - p + "px", top: r.top - p + "px", width: r.width + 2 * p + "px", height: Math.min(r.height, innerHeight - 40) + 2 * p + "px" });
        const [title, text] = t("tour")[steps[i][1]];
        const tip = $(".tip", el);
        tip.innerHTML = `<h4>${title}</h4><p>${text}</p><div class="row"><span style="font-size:12px;color:var(--muted)">${t("stepOf")(loc(i + 1), loc(steps.length))}</span>
          <span style="display:flex;gap:6px"><button class="btn" data-a="skip">${t("skip")}</button><button class="btn" data-a="next" style="background:var(--cyan);color:var(--night);border-color:var(--cyan)">${i === steps.length - 1 ? t("done") : t("next")}</button></span></div>`;
        const tw = 340, left = Math.min(innerWidth - tw - 16, Math.max(16, r.left));
        const below = r.bottom + 190 < innerHeight;
        Object.assign(tip.style, { left: left + "px", top: (below ? Math.min(r.bottom + 16, innerHeight - 200) : Math.max(16, r.top - 190)) + "px" });
        $("[data-a=next]", tip).onclick = () => { i++; show(); };
        $("[data-a=skip]", tip).onclick = () => { i = steps.length; show(); };
      }, 120);
    };
    el.classList.add("on"); show();
  }

  // ------------------------------------------------------------------ misc wiring
  function wire() {
    // the Dispatch notices tab opens its own page for the week, horizon and language on screen
    const dl = $("#dispatchTab");
    if (dl) dl.onclick = dl.onmouseenter = dl.onfocus = () => {
      dl.href = "dispatch.html?" + new URLSearchParams(S.mode === "live" ? { lang: S.lang } : { date: S.date, h: S.h, lang: S.lang }).toString();
    };
    $$("nav.tabs button").forEach((b) => b.onclick = () => {
      $$("nav.tabs button").forEach((x) => x.setAttribute("aria-selected", x === b));
      $$(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + b.dataset.tab));
      if (b.dataset.tab === "trust") loadTrust();
      if (b.dataset.tab === "data") buildDataTab();
      if (b.dataset.tab === "how") buildHow();
      if (b.dataset.tab === "map" && deckgl) setTimeout(() => deckgl.redraw(true), 50);
      scrollTo({ top: 0, behavior: "smooth" });
    });
    $("#langEn").onclick = () => { S.lang = "en"; applyLang(); };
    $("#langBn").onclick = () => { S.lang = "bn"; applyLang(); };
    $("#modeReplay").onclick = () => setMode("replay");
    $("#modeLive").onclick = () => setMode("live");
    $("#viewFc").onclick = () => setView("fc");
    $("#viewAct").onclick = () => setView("act");
    $("#tourBtn").onclick = () => tour(0);
    $("#hospToggle").onclick = () => { S.showHosp = !S.showHosp; $("#hospToggle").setAttribute("aria-pressed", S.showHosp); refreshLayers(); };
    $("#heatPred").onclick = () => { S.heatView = "pred"; $("#heatPred").setAttribute("aria-pressed", true); $("#heatObs").setAttribute("aria-pressed", false); renderHeatmap(); };
    $("#heatObs").onclick = () => { S.heatView = "obs"; $("#heatObs").setAttribute("aria-pressed", true); $("#heatPred").setAttribute("aria-pressed", false); renderHeatmap(); };
    $("#fileIn").onchange = (e) => { const f = e.target.files[0]; if (f) f.text().then(sendCsv); e.target.value = ""; };
    const dz = $("#dropZone");
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
    dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) f.text().then(sendCsv); });
    $("#trySample").onclick = () => fetch("/api/sample.csv").then((r) => r.text()).then(sendCsv);
    $("#resetView").onclick = () => flyTo(VIEW_DHAKA, 500);
    $("#search").addEventListener("change", (e) => {
      const v = e.target.value.trim().toLowerCase();
      const hit = wardRows.find((w) => `${cityName(w.city)} · ${wardLabel(w.id)}`.toLowerCase() === v)
        || wardRows.find((w) => w.id.toLowerCase().includes(v.replace(/\s+/g, "_")))
        || wardRows.find((w) => `${cityName(w.city)} ${wardLabel(w.id)}`.toLowerCase().includes(v));
      if (hit) { selectWard(hit.id, true); e.target.value = ""; e.target.blur(); }
    });
    $("#csvBtn").onclick = () => {
      const rows = visible().slice().sort((a, b) => cur(b).score - cur(a).score).map((w) => {
        const c = cur(w), d = byId.get(w.id);
        return [d.id, d.city, S.fc.targets[S.h - 2], c.tier, Math.round(c.score), c.pred, c.lo, c.hi, c.normal].join(",");
      });
      const csv = ["ward_id,city,forecast_week,tier,risk_score,expected_cases,low80,high80,seasonal_normal", ...rows].join("\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      a.download = `shotorko_field_plan_${S.fc.targets[S.h - 2]}_${S.h}wk.csv`; a.click();
      toast(t("csvDone"));
    };
    addEventListener("resize", () => { if ($("#tour").classList.contains("on")) $("#tour").classList.remove("on"); });
    addEventListener("resize", debounce(() => { if (!deckgl) return; fitCountry(); flyTo(VIEW_DHAKA, 0); $("#labels").dataset.built = ""; refreshLayers(); }, 200));
  }
  function setMode(m) {
    if (S.playing) stopPlay();
    // live mode moves S.date to this week's date; keep the replay week so switching back returns to it
    if (m === "live" && S.mode !== "live") S.replayDate = S.date;
    if (m === "replay" && S.mode === "live" && S.replayDate) S.date = S.replayDate;
    S.mode = m;
    $("#modeReplay").setAttribute("aria-pressed", m === "replay"); $("#modeLive").setAttribute("aria-pressed", m === "live");
    // live mode has no week to pick, but the horizon and the Replay / Live switch must stay reachable
    $("#timeline").classList.toggle("is-live", m === "live");
    $("#liveInfo").style.display = m === "live" ? "flex" : "none";
    if (m === "live") setView("fc", true);
    buildWhatIf();
    loadForecast().then((ok) => {
      if (!ok && m === "live") { setMode("replay"); return; }   // fall back gracefully when the weather API is unreachable
      loadWard();
    });
  }
  function setView(v, quiet) {
    S.view = v;
    $("#viewFc").setAttribute("aria-pressed", v === "fc"); $("#viewAct").setAttribute("aria-pressed", v === "act");
    if (!quiet && S.fc) renderForecast(true);
  }


  // ------------------------------------------------------------------ offline badge (Phase 2, Requirement 5)
  async function checkNet() {
    let offline = !navigator.onLine;
    try { const h = await fetch("/api/health", { cache: "no-store" }).then((r) => r.json()); offline = offline || !h.internet; checkNet.forced = h.offline_forced; } catch (e) { /* server is local; ignore */ }
    const L = window.I18N[document.documentElement.lang === "bn" ? "bn" : "en"];
    const b = document.getElementById("netBadge"); if (!b) return;
    b.hidden = !offline; b.title = L.netOfflineTip;
    const sw = document.getElementById("netSwitch");
    if (sw && checkNet.forced !== undefined) {
      sw.setAttribute("aria-pressed", checkNet.forced); sw.title = L.simOfflineTip;
      sw.querySelector("span").textContent = checkNet.forced ? L.simOnline : L.simOffline;
    }
  }
  // the in-app offline switch: go offline (and back) live, e.g. during judging, without unplugging anything
  async function toggleOffline() {
    const sw = document.getElementById("netSwitch"); if (!sw) return;
    sw.disabled = true;
    try {
      const r = await fetch("/api/offline", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ on: sw.getAttribute("aria-pressed") !== "true" }) }).then((x) => x.json());
      checkNet.forced = r.offline_forced;
      await checkNet();
      document.dispatchEvent(new CustomEvent("sk-net-changed", { detail: r }));
    } finally { sw.disabled = false; }
  }
  document.addEventListener("click", (e) => { if (e.target.closest && e.target.closest("#netSwitch")) toggleOffline(); });
  document.addEventListener("sk-net-changed", () => { if (S.fc) refresh(); });
  addEventListener("online", checkNet); addEventListener("offline", checkNet);
  setInterval(checkNet, 30000); checkNet();

  // shared with web/ops.js (Phase 2 operations tab)
  window.SK = { S, api, t, loc, nf, fmtDate, cityName, tierName, toast, HEX, TEXT_ON, debounce, tween,
    setDate: (d) => setDate(d), setH: (h) => setH(h), wardLabel: (id) => wardLabel(id), ward: (id) => byId.get(id) };

  // ------------------------------------------------------------------ intro ("what is Shotorko?")
  function setIntro(show) {
    $("#intro").hidden = !show; $("#introShow").hidden = show;
    try { localStorage.setItem("sk-intro", show ? "1" : "0"); } catch (e) { /* ignore */ }
  }
  function initIntro() {
    let show = true; try { show = localStorage.getItem("sk-intro") !== "0"; } catch (e) { /* ignore */ }
    setIntro(show);
    $("#introClose").onclick = () => setIntro(false);
    $("#introShow").onclick = () => setIntro(true);
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    try {
      const [meta, geo, hosp] = await Promise.all([api("/api/meta"), fetch("data/region.geojson").then((r) => r.json()), api("/api/hospitals").catch(() => null)]);
      S.meta = meta; S.dates = meta.dates; S.date = meta.default_date; region = geo; S.hosp = hosp;
      wardRows = meta.wards.map((w) => ({ ...w, f: null }));
      byId = new Map(wardRows.map((w) => [w.id, w]));
    } catch (e) { toast(t("error")); return; }
    try { const l = localStorage.getItem("sk-lang"); if (l === "bn" || l === "en") S.lang = l; } catch (e) { /* ignore */ }
    const wi = document.createElement("div"); wi.className = "whatif"; wi.id = "whatIf";
    const body = document.createElement("div"); body.id = "wardBody"; body.style.cssText = "display:flex;flex-direction:column;gap:16px";
    body.innerHTML = '<div class="skeleton" style="height:120px"></div><div class="skeleton" style="height:80px"></div><div class="skeleton" style="height:160px"></div>';
    const wiWrap = document.createElement("details"); wiWrap.className = "more wi-more"; wiWrap.id = "whatIfMore";
    wiWrap.innerHTML = '<summary data-i="whatIf"></summary>'; wiWrap.append(wi);
    $("#panel").append(body, wiWrap);
    $("#stage").after($("#mapNote"));   // the explanation sits under the map, not on top of it
    wire(); initIntro(); applyLang(); initTimeline(); updateRain(); initDeck();
    await loadForecast(); await loadWard();
    let toured = false; try { toured = localStorage.getItem("sk-toured") === "1"; } catch (e) { /* ignore */ }
    if (!toured && !/notour/.test(location.search)) setTimeout(() => tour(0), 3600);
  }
  boot();
})();
