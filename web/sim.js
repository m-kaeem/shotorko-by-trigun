/* Shotorko — Vector-control simulator tab (Phase 2, Requirement 3).
   Officers send larvicide or indoor-spraying crews to wards and see the model-driven
   2-4 week reduction in cases, beds and saline, with wards ranked by ROI (cases prevented per crew).
   All numbers come from POST /api/simulate (local LightGBM counterfactual, works offline). */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const TYPE_COL = { larvicide: "#22D3EE", indoor: "#A78BFA" };

  const STR = {
    en: {
      tabSim: "Crew simulator",
      title: "Vector-control what-if simulator",
      sub: "Send spraying crews to wards and see how many cases, hospital beds and saline bags the forecast model says they would prevent over the next 2–4 weeks. Wards are ranked by return: cases prevented per crew.",
      offline: "Runs fully offline",
      week: "Forecast week", crews: "Crews available", crewsHelp: "larvicide + indoor-spraying teams",
      auto: "Auto-plan best deployment", clear: "Clear", autoHelp: "Each crew goes where the model says it prevents the most extra cases, and the better crew type for that ward is picked.",
      used: (u, n) => `${u} of ${n} crews deployed`,
      types: { larvicide: "Larvicide", indoor: "Indoor spray" },
      typeHelp: {
        larvicide: "Removes standing water (larviciding + source reduction). Slow to start, lasts. Best when rain drives the forecast. Evidence: ~30% fewer cases (Camino Verde trial).",
        indoor: "Sprays houses around recent cases with a non-pyrethroid insecticide. Works within a week, fades. Best when a ward has a few recent cases to chase. Evidence: 86–96% less transmission at treated homes (Cairns).",
      },
      k: { prevented: "Cases prevented", beds: "Hospital beds saved", saline: "Saline bags saved", roi: "Cases prevented per crew", wait: "Cost of waiting 1 week" },
      range: (lo, hi) => `range ${lo}–${hi}`, of: (n) => `of ${n} forecast cases`,
      waitFoot: (n) => `${n} fewer cases prevented if the same crews go next week`,
      chartTitle: "Forecast cases, all wards, with and without crews", without: "Without crews", with: "With crews", wk: (h) => `+${h} wk`,
      rankTitle: "Wards ranked by intervention ROI", rankSub: "ROI = cases prevented per crew if one crew is sent (whichever type works better there). Use + / − to deploy.",
      cols: ["#", "Ward", "Best crew", "ROI larvicide", "ROI indoor", "Recent cases", "Larvicide", "Indoor", "Prevented", "Beds", "Saline"],
      csv: "Download simulation CSV", csvDone: "Downloaded",
      method: "How this is calculated", methodText: "The same LightGBM model that makes the forecast is run twice for weeks +2, +3 and +4: once as is, once with the inputs the crews change (larvicide: breeding-water features; indoor spray: recent-case features plus part of the vector proxy). The difference is the cases prevented, so it changes by ward, week and weather. Bands show low/high effect sizes. Fogging is not offered: trials show no proven case reduction and Dhaka Aedes are pyrethroid-resistant.",
      tooMany: "Not enough crews left", none: "No crews deployed yet. Use Auto-plan or the + buttons.",
      money: "Money view (BDT)", moneySub: "Treatment cost avoided vs what the crews cost. Only hospitalised cases are counted, so savings are a floor.",
      cHosp: "Cost per hospitalised case", cLarv: "Larvicide crew (2–4 wk)", cInd: "Indoor-spray crew (2–4 wk)",
      saved: "Treatment cost avoided", spent: "Crew cost", bcr: "Benefit / cost", perCase: "Crew cost per case prevented",
      costSrc: "Hospital cost: BDT 39,893 per episode, Dhaka (PLOS NTD 2023). Crew costs are estimates; edit them.",
      check: "Is the simulator's model trustworthy?", checkSub: "Out-of-sample check on 2024–25 test weeks, plus what the raw data says.",
      dose: (k, c, m) => `Keep ${k}% of breeding water → forecast ${c}% (${m}% of ward-weeks never go up)`,
      dataLine: (r, w) => `Data: 4-week rain vs cases 3 weeks later, Spearman ${r}; ward water-logging vs mean cases, ${w}.`,
      season: "Season backtest: would this plan have beaten the usual rule?",
      seasonSub: (n, c) => `Every week of the 2024 and 2025 seasons (${n} weeks, never seen in training), ${c} crews a week, planned four ways and scored with the same model.`,
      pol: { shotorko: "Shotorko auto-plan", shotorko_larvicide: "Shotorko, larvicide only", tier_rule: "Tier rule (1/2/3 crews by risk)", top_cases: "Most forecast cases first" },
      seasonHead: (x, y) => `Same crews, ${x}% more cases prevented than the tier rule. Choosing where alone (larvicide only) gives +${y}%.`,
      seasonWeeks: (a, n) => `Shotorko's plan was best in ${a} of ${n} weeks.`,
      seasonNote: "Model-based comparison of plans, each week scored on its own 2–4 week window; not a measured field result.",
      prevented: "cases prevented",
      checkNote: "The model learned the direction the evidence expects: less standing water, fewer cases. The few ward-weeks that tick up are tree-model noise and are floored at zero in the simulator.",
    },
    bn: {
      tabSim: "স্প্রে দল সিমুলেটর",
      title: "মশা নিয়ন্ত্রণ “যদি” সিমুলেটর",
      sub: "ওয়ার্ডে স্প্রে দল পাঠান, আর দেখুন আগামী ২–৪ সপ্তাহে মডেল অনুযায়ী কত রোগী, হাসপাতাল বেড ও স্যালাইন বাঁচে। ওয়ার্ডগুলো সাজানো আছে প্রতি দলে কত রোগী কমে সেই অনুযায়ী।",
      offline: "সম্পূর্ণ অফলাইনে চলে",
      week: "পূর্বাভাসের সপ্তাহ", crews: "মোট দল", crewsHelp: "লার্ভিসাইড + ঘরের ভেতরে স্প্রে দল",
      auto: "সেরা পরিকল্পনা স্বয়ংক্রিয়", clear: "মুছুন", autoHelp: "প্রতিটি দল যায় যেখানে মডেল অনুযায়ী সবচেয়ে বেশি বাড়তি রোগী কমে, আর সেই ওয়ার্ডের জন্য ভালো দলের ধরন বেছে নেওয়া হয়।",
      used: (u, n) => `${n}টির মধ্যে ${u}টি দল পাঠানো`,
      types: { larvicide: "লার্ভিসাইড", indoor: "ঘরে স্প্রে" },
      typeHelp: {
        larvicide: "জমা পানি সরায় (লার্ভিসাইড + উৎস ধ্বংস)। ধীরে শুরু, দীর্ঘস্থায়ী। বৃষ্টির কারণে ঝুঁকি বাড়লে সবচেয়ে কাজের। প্রমাণ: ~৩০% কম রোগী (Camino Verde)।",
        indoor: "সাম্প্রতিক রোগীর আশেপাশের ঘরে স্প্রে। এক সপ্তাহের মধ্যে কাজ করে, পরে কমে। কয়েকজন সাম্প্রতিক রোগী থাকলে সবচেয়ে কাজের। প্রমাণ: ৮৬–৯৬% কম সংক্রমণ (Cairns)।",
      },
      k: { prevented: "রোগী কমবে", beds: "বেড বাঁচবে", saline: "স্যালাইন বাঁচবে", roi: "প্রতি দলে রোগী কমে", wait: "১ সপ্তাহ দেরির ক্ষতি" },
      range: (lo, hi) => `পরিসর ${lo}–${hi}`, of: (n) => `${n} পূর্বাভাসিত রোগীর মধ্যে`,
      waitFoot: (n) => `একই দল পরের সপ্তাহে গেলে ${n} জন কম রোগী ঠেকানো যাবে`,
      chartTitle: "পূর্বাভাসিত রোগী (সব ওয়ার্ড), দলসহ ও দল ছাড়া", without: "দল ছাড়া", with: "দলসহ", wk: (h) => `+${h} সপ্তাহ`,
      rankTitle: "ফলাফল অনুযায়ী ওয়ার্ড তালিকা", rankSub: "ROI = একটি দল পাঠালে কত রোগী কমে (যে ধরন সেখানে ভালো)। + / − দিয়ে দল পাঠান।",
      cols: ["#", "ওয়ার্ড", "ভালো দল", "ROI লার্ভি", "ROI ঘরে", "সাম্প্রতিক রোগী", "লার্ভিসাইড", "ঘরে স্প্রে", "রোগী কমে", "বেড", "স্যালাইন"],
      csv: "সিমুলেশন CSV ডাউনলোড", csvDone: "ডাউনলোড হয়েছে",
      method: "কীভাবে হিসাব হয়", methodText: "পূর্বাভাসের একই LightGBM মডেল +২, +৩, +৪ সপ্তাহের জন্য দুবার চালানো হয়: একবার যেমন আছে, একবার দলের কাজে যে তথ্য বদলায় তা বদলে। পার্থক্যই রোগী কমা, তাই ওয়ার্ড, সপ্তাহ ও আবহাওয়া অনুযায়ী বদলায়। ফগিং রাখা হয়নি: প্রমাণিত সুফল নেই এবং ঢাকার এডিস পাইরেথ্রয়েড প্রতিরোধী।",
      tooMany: "আর দল বাকি নেই", none: "এখনও কোনো দল পাঠানো হয়নি। স্বয়ংক্রিয় পরিকল্পনা বা + বোতাম ব্যবহার করুন।",
      money: "টাকার হিসাব (BDT)", moneySub: "চিকিৎসা খরচ বাঁচে বনাম দলের খরচ। শুধু হাসপাতালে ভর্তি রোগী ধরা হয়েছে, তাই আসল সাশ্রয় এর চেয়ে বেশি।",
      cHosp: "ভর্তি রোগী প্রতি খরচ", cLarv: "লার্ভিসাইড দল (২–৪ সপ্তাহ)", cInd: "ঘরে স্প্রে দল (২–৪ সপ্তাহ)",
      saved: "চিকিৎসা খরচ বাঁচে", spent: "দলের খরচ", bcr: "লাভ / খরচ", perCase: "প্রতি রোগী ঠেকাতে খরচ",
      costSrc: "হাসপাতাল খরচ: প্রতি রোগী ৩৯,৮৯৩ টাকা, ঢাকা (PLOS NTD 2023)। দলের খরচ আনুমানিক; বদলাতে পারেন।",
      check: "সিমুলেটরের মডেল কি বিশ্বাসযোগ্য?", checkSub: "২০২৪–২৫ এর পরীক্ষার সপ্তাহে যাচাই, আর মূল ডেটা কী বলে।",
      dose: (k, c, m) => `জমা পানি ${k}% রাখলে → পূর্বাভাস ${c}% (${m}% ওয়ার্ড-সপ্তাহে বাড়ে না)`,
      dataLine: (r, w) => `ডেটা: ৪ সপ্তাহের বৃষ্টি বনাম ৩ সপ্তাহ পরের রোগী, Spearman ${r}; জলাবদ্ধতা বনাম গড় রোগী, ${w}।`,
      season: "মৌসুম যাচাই: এই পরিকল্পনা কি প্রচলিত নিয়মের চেয়ে ভালো হতো?",
      seasonSub: (n, c) => `২০২৪ ও ২০২৫ মৌসুমের প্রতিটি সপ্তাহ (${n} সপ্তাহ, প্রশিক্ষণে দেখা হয়নি), প্রতি সপ্তাহে ${c}টি দল, চারভাবে পরিকল্পনা করে একই মডেলে যাচাই।`,
      pol: { shotorko: "শতর্ক স্বয়ংক্রিয় পরিকল্পনা", shotorko_larvicide: "শতর্ক, শুধু লার্ভিসাইড", tier_rule: "ঝুঁকি নিয়ম (১/২/৩ দল)", top_cases: "বেশি রোগী আগে" },
      seasonHead: (x, y) => `একই দলে প্রচলিত নিয়মের চেয়ে ${x}% বেশি রোগী ঠেকানো যায়। শুধু কোথায় পাঠাবেন বেছে নিলেই (শুধু লার্ভিসাইড) +${y}%।`,
      seasonWeeks: (a, n) => `${n} সপ্তাহের মধ্যে ${a} সপ্তাহে শতর্কের পরিকল্পনা সেরা ছিল।`,
      seasonNote: "মডেল-ভিত্তিক তুলনা, প্রতিটি সপ্তাহ নিজের ২–৪ সপ্তাহে যাচাই; মাঠের মাপা ফল নয়।",
      prevented: "রোগী ঠেকানো",
      checkNote: "মডেল প্রমাণের সাথে মেলে এমন দিক শিখেছে: জমা পানি কম, রোগী কম। যে অল্প কিছু ওয়ার্ড-সপ্তাহ সামান্য বাড়ে তা ট্রি-মডেলের নয়েজ, সিমুলেটরে শূন্যে আটকানো।",
    },
  };
  Object.entries(STR).forEach(([l, o]) => { if (window.I18N && window.I18N[l]) window.I18N[l].tabSim = o.tabSim; });

  const V = { crews: 20, dep: {}, res: null, built: false, seq: 0, first: true, check: null,
    costs: { hospital_case_bdt: 39893, larvicide_crew_bdt: 100000, indoor_crew_bdt: 120000 } };
  const SK = () => window.SK;
  const L = () => STR[SK().S.lang] || STR.en;
  const loc = (x) => SK().loc(x);
  const n1 = (x) => loc((Math.round(x * 10) / 10).toLocaleString());
  const n0 = (x) => loc(Math.round(x).toLocaleString());
  const wname = (id) => { const w = SK().ward(id); return w ? `${SK().cityName(w.city)} · ${SK().wardLabel(id)}` : id; };
  const used = () => Object.values(V.dep).reduce((s, d) => s + d.larvicide + d.indoor, 0);

  // ------------------------------------------------------------------ layout
  function build() {
    const T = L();
    $("#tab-sim").innerHTML = `
      <div class="hero"><div><h1>${T.title}</h1><p>${T.sub}</p></div><span class="ops-offline"><i></i>${T.offline}</span></div>
      <div class="card ops-deck">
        <div class="ops-row">
          <div class="ops-field"><span class="eyebrow">${T.week}</span>
            <div class="ops-week"><button class="step" data-wk="-1" aria-label="Previous week">‹</button><b id="simWeek">–</b><button class="step" data-wk="1" aria-label="Next week">›</button></div></div>
          <div class="ops-field"><span class="eyebrow">&nbsp;</span>
            <div class="sim-actions"><button class="btn sim-auto" id="simAuto">${T.auto}</button><button class="btn" id="simClear">${T.clear}</button></div>
            <small>${T.autoHelp}</small></div>
          <div class="ops-field"><span class="eyebrow" id="simUsed"></span>
            <div class="sim-types">${["larvicide", "indoor"].map((t) => `<div><i style="background:${TYPE_COL[t]}"></i><b>${T.types[t]}</b><small>${T.typeHelp[t]}</small></div>`).join("")}</div></div>
        </div>
        <div class="ops-sliders sim-one">
          <label class="ops-slider" for="simCrews"><span class="ops-sl-top"><span class="eyebrow">${T.crews}</span><b id="simCrewsV">${loc(V.crews)}</b></span>
            <input type="range" id="simCrews" min="0" max="120" step="1" value="${V.crews}" style="--p:${V.crews / 120 * 100}%"><small>${T.crewsHelp}</small></label>
        </div>
      </div>
      <div class="cards sim-cards" id="simKpis"></div>
      <div class="two">
        <div class="card"><h3 class="sec-title">${T.chartTitle}</h3><div id="simChart"></div></div>
        <div class="card"><h3 class="sec-title">${T.method}</h3><p class="ops-muted sim-method">${T.methodText}</p></div>
      </div>
      <div class="two">
        <div class="card"><h3 class="sec-title">${T.money}</h3><small class="ops-muted">${T.moneySub}</small>
          <div class="sim-costs">${[["hospital_case_bdt", T.cHosp], ["larvicide_crew_bdt", T.cLarv], ["indoor_crew_bdt", T.cInd]].map(([k, l]) =>
            `<label><span>${l}</span><input type="number" min="0" step="1000" data-cost="${k}" value="${V.costs[k]}"></label>`).join("")}</div>
          <div class="sim-money" id="simMoney"></div><small class="ops-muted">${T.costSrc}</small></div>
        <div class="card"><h3 class="sec-title">${T.check}</h3><small class="ops-muted">${T.checkSub}</small><div id="simCheck" class="sim-check"></div></div>
      </div>
      <div class="card" id="simSeasonCard"><h3 class="sec-title">${T.season}</h3><small class="ops-muted" id="simSeasonSub"></small><div id="simSeason" class="sim-season"></div></div>
      <div class="card"><div class="toolbar"><div><h3 class="sec-title">${T.rankTitle}</h3><small class="ops-muted">${T.rankSub}</small></div>
        <button class="btn" id="simCsv"><span>${T.csv}</span></button></div>
        <div class="ops-table-wrap"><table class="ops-table" id="simTable"></table></div></div>`;
    wire();
    V.built = true;
  }

  function wire() {
    const S = SK().S, go = SK().debounce(load, 160);
    const inp = $("#simCrews");
    inp.addEventListener("input", () => {
      V.crews = +inp.value; $("#simCrewsV").textContent = loc(V.crews);
      inp.style.setProperty("--p", (V.crews / 120 * 100) + "%");
      trimToCrews(); go();
    });
    $("#simAuto").onclick = () => load("auto");
    $("#simClear").onclick = () => { V.dep = {}; load(); };
    $$("#tab-sim .ops-week .step").forEach((b) => b.onclick = () => {
      const i = S.dates.indexOf(S.date) + +b.dataset.wk;
      if (S.mode === "replay" && i >= 0 && i < S.dates.length) { SK().setDate(S.dates[i]); load(V.first ? "auto" : undefined); }
    });
    $("#simCsv").onclick = csv;
    $$("#tab-sim input[data-cost]").forEach((i) => i.addEventListener("input", () => { V.costs[i.dataset.cost] = Math.max(0, +i.value || 0); go(); }));
    $("#simTable").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-w]"); if (!b) return;
      const w = b.dataset.w, t = b.dataset.t, d = V.dep[w] || { larvicide: 0, indoor: 0 };
      if (+b.dataset.d > 0 && used() >= V.crews) { SK().toast(L().tooMany); return; }
      d[t] = Math.max(0, Math.min(6, d[t] + +b.dataset.d));
      if (d.larvicide + d.indoor) V.dep[w] = d; else delete V.dep[w];
      go();
    });
  }
  function trimToCrews() {   // slider lowered below what is deployed: take crews back from the lowest-ROI wards
    let over = used() - V.crews;
    if (over <= 0 || !V.res) return;
    const order = V.res.roi_ranking.map((r) => r.ward_id).reverse();
    for (const w of order) {
      const d = V.dep[w]; if (!d) continue;
      while (over > 0 && (d.larvicide + d.indoor) > 0) { if (d.indoor) d.indoor--; else d.larvicide--; over--; }
      if (!(d.larvicide + d.indoor)) delete V.dep[w];
      if (over <= 0) break;
    }
  }

  // ------------------------------------------------------------------ data
  async function load(mode) {
    const my = ++V.seq, S = SK().S;
    $("#tab-sim").classList.add("busy");
    const body = { date: S.mode === "live" ? undefined : S.date, total_crews: V.crews,
      rain: S.scen.rain, temp: S.scen.temp, hum: S.scen.hum, deployments: mode === "auto" ? "auto" : V.dep, costs: V.costs };
    try {
      const r = await fetch("/api/simulate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      if (my !== V.seq) return;
      V.res = j; V.dep = j.deployments; V.first = false;
      render();
    } catch (e) { if (my === V.seq) SK().toast(e.message); }
    finally { if (my === V.seq) $("#tab-sim").classList.remove("busy"); }
  }

  // ------------------------------------------------------------------ render
  function render() {
    const T = L(), r = V.res, s = r.summary;
    $("#simWeek").textContent = SK().fmtDate(r.date, true);
    $("#simUsed").textContent = T.used(loc(s.crews_deployed), loc(V.crews));
    const kpi = (label, v, foot, col) => `<div class="stat"><small>${label}</small><span class="v">${v}</span><small style="${col ? `color:${col}` : ""}">${foot}</small></div>`;
    $("#simKpis").innerHTML = s.crews_deployed ? [
      kpi(T.k.prevented, n1(s.cases_prevented), `${T.range(n1(s.cases_prevented_low), n1(s.cases_prevented_high))} · ${T.of(n0(s.cases_without))}`),
      kpi(T.k.beds, n1(s.beds_saved), `${T.k.saline}: ${n0(s.saline_saved)}`),
      kpi(T.k.roi, n1(s.roi_cases_per_crew), T.range(n1(s.roi_low), n1(s.roi_high))),
      kpi(T.k.wait, n1(s.cost_of_waiting_1w), T.waitFoot(n1(s.cost_of_waiting_1w)), s.cost_of_waiting_1w > 1 ? "#FDA4AF" : ""),
    ].join("") : `<div class="stat" style="grid-column:1/-1"><small>${T.none}</small></div>`;
    renderChart();
    renderMoney();
    renderCheck();
    renderSeason();
    const sims = r.wards;
    $("#simTable").innerHTML = `<thead><tr>${T.cols.map((x) => `<th>${x}</th>`).join("")}</tr></thead><tbody>${r.roi_ranking.map((x) => {
      const d = V.dep[x.ward_id] || { larvicide: 0, indoor: 0 }, sm = sims[x.ward_id];
      const step = (t) => `<span class="sim-step"><button data-w="${x.ward_id}" data-t="${t}" data-d="-1" aria-label="remove">−</button><b style="color:${d[t] ? TYPE_COL[t] : "inherit"}">${loc(d[t])}</b><button data-w="${x.ward_id}" data-t="${t}" data-d="1" aria-label="add">+</button></span>`;
      return `<tr class="${d.larvicide + d.indoor ? "sim-on" : ""}"><td class="ops-rank">${loc(x.roi_rank)}</td><td><b>${wname(x.ward_id)}</b></td>
        <td><span class="sim-badge" style="border-color:${TYPE_COL[x.best_type]};color:${TYPE_COL[x.best_type]}">${T.types[x.best_type]}</span></td>
        <td>${n1(x.roi_larvicide)}</td><td>${n1(x.roi_indoor)}</td><td>${n0(x.recent_cases)}</td>
        <td>${step("larvicide")}</td><td>${step("indoor")}</td>
        <td class="${sm.cases_prevented > 0 ? "green" : ""}">${sm.cases_prevented > 0 ? n1(sm.cases_prevented) : "–"}</td>
        <td>${sm.beds_saved > 0 ? n1(sm.beds_saved) : "–"}</td><td>${sm.saline_saved > 0 ? n0(sm.saline_saved) : "–"}</td></tr>`;
    }).join("")}</tbody>`;
  }

  function renderChart() {
    const T = L(), sims = Object.values(V.res.wards), hs = [2, 3, 4];
    const agg = hs.map((h) => sims.reduce((a, s) => { const w = s.by_week.find((b) => b.h === h); return w ? [a[0] + w.without, a[1] + w.with] : a; }, [0, 0]));
    const max = Math.max(1, ...agg.flat()), W = 520, H = 220, pad = 34, bw = 46, gap = (W - pad * 2) / hs.length;
    const y = (v) => H - 28 - (v / max) * (H - 60);
    const bars = agg.map(([a, b], i) => {
      const x0 = pad + i * gap + gap / 2 - bw - 3;
      return `<rect x="${x0}" y="${y(a)}" width="${bw}" height="${H - 28 - y(a)}" rx="6" fill="#334155"/>
        <rect x="${x0 + bw + 6}" y="${y(b)}" width="${bw}" height="${H - 28 - y(b)}" rx="6" fill="url(#simG)"/>
        <text x="${x0 + bw / 2}" y="${y(a) - 6}" text-anchor="middle" class="sim-tl">${n0(a)}</text>
        <text x="${x0 + bw * 1.5 + 6}" y="${y(b) - 6}" text-anchor="middle" class="sim-tl">${n0(b)}</text>
        <text x="${x0 + bw + 3}" y="${H - 8}" text-anchor="middle" class="sim-tx">${T.wk(loc(hs[i]))}</text>`;
    }).join("");
    $("#simChart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="sim-svg" role="img" aria-label="${T.chartTitle}">
      <defs><linearGradient id="simG" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#22D3EE"/><stop offset="1" stop-color="#8B7CF6"/></linearGradient></defs>${bars}</svg>
      <div class="ops-legend"><span><i style="background:#334155"></i>${T.without}</span><span><i style="background:#22D3EE"></i>${T.with}</span></div>`;
  }

  const tk = (x) => "৳" + loc(Math.round(x).toLocaleString());
  function renderMoney() {
    const T = L(), s = V.res.summary;
    const box = (l, v, col) => `<div><small>${l}</small><b style="${col ? `color:${col}` : ""}">${v}</b></div>`;
    $("#simMoney").innerHTML = s.crews_deployed ? [
      box(T.saved, tk(s.money_saved_bdt)), box(T.spent, tk(s.crew_cost_bdt)),
      box(T.bcr, loc(s.benefit_cost_ratio.toFixed(2)) + "×", s.benefit_cost_ratio >= 1 ? "#86EFAC" : "#FDE047"),
      box(T.perCase, s.cost_per_case_prevented_bdt != null ? tk(s.cost_per_case_prevented_bdt) : "–"),
    ].join("") : "";
  }
  async function renderSeason() {
    const T = L(), POL = ["shotorko", "shotorko_larvicide", "tier_rule", "top_cases"];
    const COL = { shotorko: "url(#simSG)", shotorko_larvicide: "#22D3EE", tier_rule: "#64748B", top_cases: "#475569" };
    try {
      if (V.season === undefined) V.season = await SK().api("/api/simulate/season").catch(() => null);
      const r = V.season;
      if (!r) { $("#simSeasonCard").style.display = "none"; return; }
      $("#simSeasonSub").textContent = T.seasonSub(loc(r.n_weeks), loc(r.crews_per_week));
      const max = Math.max(...POL.map((p) => r.all[p])), W = 560, rowH = 34;
      const bars = POL.map((p, i) => {
        const w = (r.all[p] / max) * (W - 250), y = 8 + i * rowH;
        return `<text x="0" y="${y + 17}" class="sim-tx">${T.pol[p]}</text>
          <rect x="190" y="${y + 4}" width="${w}" height="20" rx="6" fill="${COL[p]}"/>
          <text x="${196 + w}" y="${y + 18}" class="sim-tl">${n0(r.all[p])}</text>`;
      }).join("");
      // weekly lines: shotorko vs tier rule
      const wk = r.weeks, LW = 560, LH = 150, mx = Math.max(1, ...wk.map((x) => Math.max(x.shotorko, x.tier_rule)));
      const pts = (k) => wk.map((x, i) => `${(i / Math.max(1, wk.length - 1)) * (LW - 20) + 10},${LH - 18 - (x[k] / mx) * (LH - 34)}`).join(" ");
      const split = wk.findIndex((x) => x.season !== wk[0].season), sx = split > 0 ? (split / Math.max(1, wk.length - 1)) * (LW - 20) + 10 : null;
      $("#simSeason").innerHTML = `<p class="sim-head">${T.seasonHead(loc(r.shotorko_vs_tier_rule_pct), loc(r.larvicide_only_vs_tier_rule_pct))} ${T.seasonWeeks(loc(r.shotorko_weeks_best), loc(r.n_weeks))}</p>
        <div class="sim-season-grid">
          <svg viewBox="0 0 ${W} ${8 + POL.length * rowH}" class="sim-svg" role="img" aria-label="${T.season}">
            <defs><linearGradient id="simSG" x1="0" x2="1"><stop offset="0" stop-color="#22D3EE"/><stop offset="1" stop-color="#8B7CF6"/></linearGradient></defs>${bars}</svg>
          <div><svg viewBox="0 0 ${LW} ${LH}" class="sim-svg" role="img" aria-label="weekly">
            ${sx ? `<line x1="${sx}" x2="${sx}" y1="4" y2="${LH - 16}" stroke="#334155" stroke-dasharray="4 4"/>` : ""}
            <text x="10" y="${LH - 2}" class="sim-tx">${wk[0].season}</text>${sx ? `<text x="${sx + 4}" y="${LH - 2}" class="sim-tx">${wk[split].season}</text>` : ""}
            <polyline points="${pts("tier_rule")}" fill="none" stroke="#64748B" stroke-width="2"/>
            <polyline points="${pts("shotorko")}" fill="none" stroke="#22D3EE" stroke-width="2.5"/></svg>
            <div class="ops-legend"><span><i style="background:#22D3EE"></i>${T.pol.shotorko}</span><span><i style="background:#64748B"></i>${T.pol.tier_rule}</span><span>· ${T.prevented} / week</span></div></div>
        </div><small class="ops-muted">${T.seasonNote}</small>`;
    } catch (e) { $("#simSeason").textContent = e.message; }
  }
  async function renderCheck() {
    const T = L();
    try {
      if (!V.check) V.check = await SK().api("/api/simulate/check");
      const c = V.check;
      $("#simCheck").innerHTML = `<ul>${c.breeding_water_dose_response.map((d) =>
        `<li>${T.dose(loc(d.breeding_water_kept_pct), loc(d.forecast_change_pct), loc(d.rows_not_increased_pct))}</li>`).join("")}</ul>
        <p>${T.dataLine(loc(c.data_spearman.rain_4w_vs_cases_3w_later), loc(c.data_spearman.ward_waterlogging_vs_mean_cases))}</p>
        <small class="ops-muted">${T.checkNote}</small>`;
    } catch (e) { $("#simCheck").textContent = e.message; }
  }

  // ------------------------------------------------------------------ export
  function csv() {
    const r = V.res; if (!r) return;
    const rows = [["roi_rank", "ward_id", "city", "best_type", "roi_larvicide", "roi_indoor", "recent_cases", "larvicide_crews", "indoor_crews",
      "cases_without", "cases_with", "cases_prevented", "cases_prevented_low", "cases_prevented_high", "beds_saved", "saline_saved", "cost_of_waiting_1w",
      "money_saved_bdt", "crew_cost_bdt", "benefit_cost_ratio", "forecast_week"]];
    r.roi_ranking.forEach((x) => {
      const s = r.wards[x.ward_id], m = s.crew_mix;
      rows.push([x.roi_rank, x.ward_id, x.city, x.best_type, x.roi_larvicide, x.roi_indoor, x.recent_cases, m.larvicide, m.indoor,
        s.cases_without, s.cases_with, s.cases_prevented, s.cases_prevented_low, s.cases_prevented_high, s.beds_saved, s.saline_saved, s.cost_of_waiting_1w ?? "",
        s.money_saved_bdt, s.crew_cost_bdt, s.benefit_cost_ratio, r.date]);
    });
    const text = rows.map((x) => x.join(",")).join("\n"), a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" })); a.download = `shotorko_crew_simulation_${r.date}.csv`; a.click();
    SK().toast(L().csvDone);
  }

  // ------------------------------------------------------------------ hooks
  function open() { build(); load(V.first ? "auto" : undefined); }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("nav.tabs button");
    if (b && b.dataset.tab === "sim") setTimeout(open, 0);
    if (e.target.closest("#langEn, #langBn") && V.built && $("#tab-sim").classList.contains("active")) setTimeout(() => { build(); if (V.res) render(); }, 0);
  });
})();
