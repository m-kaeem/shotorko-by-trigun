/* Shotorko — Operations tab (Phase 2).
   Req 1: constrained supply + crew allocation with scarcity controls and deficit alerts.
   Req 2: hospital surge detection and intra-city overflow routing.
   All numbers come from /api/ops/* (local model + deterministic math, no network needed). */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const ITEMS = ["saline_bags_needed", "diagnostic_kits_needed", "hospital_beds_required"];
  const CITIES = ["Dhaka North", "Dhaka South", "Chattogram", "Gazipur", "Narayanganj"];
  const ST_COL = { ok: "#22C55E", deficit: "#FACC15", critical: "#F43F5E", surge: "#FB923C", receiving: "#22D3EE", floating: "#A78BFA", unresolved: "#F43F5E" };

  // ------------------------------------------------------------------ strings
  const STR = {
    en: {
      tabOps: "Operations",
      title: "Operations command", sub: "Turn the forecast into supplies, spraying crews and hospital beds. Move the sliders to see who gets served first when stock runs short.",
      week: "Forecast week", horizon: "Plan for", weeks: "weeks ahead", basis: "Plan on", expected: "Expected cases", upper: "Worst case (80% upper)",
      policy: "When stock is short", weighted: "Share by priority", strict: "Strict triage",
      policyHelp: { weighted: "Every ward gets a share; higher-priority wards get a bigger share and are filled first.", strict: "Wards are served in full, one by one, in priority order until stock runs out." },
      stock: "Available regional stock", stockHelp: "share of total projected need", crews: "Spraying crews available", crewsHelp: "larvicide / fogging teams · need: Medium 1, High 2, Critical 3, +1 if density ≥ 60,000/km²",
      capScale: "Hospital bed capacity", capHelp: "of each ward's usual dengue beds",
      life: "Minimum for every ward", lifeHelp: "guaranteed share of each supply before priority sharing (0% = off)",
      real: "Or type what's in the warehouse", realNote: "Real counts replace the stock slider for that item.", clear: "Clear",
      reset: "Reset", resetAll: "Reset all", resetTip: "Back to the default setting", clearReserve: "Clear reserve beds",
      realItems: { saline: "Saline bags", kits: "Diagnostic kits", beds: "Hospital beds" }, overridden: "using real counts",
      fixTitle: "What it takes to clear every critical deficit",
      fixMore: (s, k, b) => `Get more stock: +${loc(s)} saline bags, +${loc(k)} kits, +${loc(b)} beds`,
      fixTry: (p) => `Try ${loc(p)}% stock`, fixLife: "Or share differently: guarantee every ward 50% first",
      fixLifeBtn: "Turn on 50% minimum", fixLifeNote: "Same stock; the top wards give up a little so no ward falls under half.",
      fixNoStock: "Even a 50% minimum can't fit in this stock. More stock is the only fix.",
      items: { saline_bags_needed: "Saline bags", diagnostic_kits_needed: "Diagnostic kits", hospital_beds_required: "Hospital beds" },
      crewsK: "Spraying crews", allocated: "allocated", required: "needed", unmet: "short", idle: "idle",
      alertN: (n, c) => `${loc(n)} ward${n === 1 ? "" : "s"} can't get 100% of what they need — ${loc(c)} critical.`,
      alertOk: "Every ward gets 100% of its projected supplies this week.",
      critWhy: "Critical = any item under 50% of need.",
      tblTitle: "Allocation by priority", tblSub: "Priority = risk tier weight (Low 1 · Medium 2 · High 3 · Critical 4) × ward density ÷ average density.",
      cols: ["#", "Ward", "Risk", "Density /km²", "Cases", "Saline", "Kits", "Beds", "Crews", "Status"],
      status: { ok: "Fully met", deficit: "Deficit", critical: "Critical deficit" },
      csv: "Download allocation CSV", csvT: "Download transfer plan CSV", csvDone: "Downloaded",
      surgeTitle: "Hospital surge & overflow routing",
      surgeSub: "Wards needing more beds than they have send extra patients to the least-loaded ward in the same city. Patients never cross city lines. If the whole city is full, its reserve (floating) beds open where the patients are.",
      capRule: "Local capacity = each ward's 95th-percentile weekly bed need in 2016–2023 (min 2), × the capacity slider.",
      util: "Bed use", before: "before", after: "after routing", headroom: "spare beds left", moved: "patients moved", unresolvedN: "can't be placed",
      cityStatus: { ok: "OK", strained: "Strained", escalate: "Escalate" },
      escalate: (c, n) => `${c}: ${loc(n)} patient${n === 1 ? "" : "s"} can't be placed inside the city — open reserve beds or refer out.`,
      reserve: "Reserve beds", reserveUsed: (u, p) => `${u} of ${p} used`, reserveOpened: "reserve beds opened",
      openReserve: (n) => `Open ${loc(n)} reserve bed${n === 1 ? "" : "s"}`, reserveHelp: "Floating beds that belong to no ward (field cots, a mobile team). Used only after transfers, where the patients are.",
      flowTitle: "Transfer map", noTransfers: "No transfers needed in this city this week.",
      tCols: ["From", "To", "Patients", "Distance"],
      wCols: ["Ward", "Beds needed", "Capacity", "Net", "Sent", "Received", "Reserve", "Use after"],
      wStatus: { ok: "OK", surge: "Surge", receiving: "Receiving", floating: "Reserve bed opened", unresolved: "Over city capacity" },
      legend: { surge: "Sending overflow", receiving: "Receiving", floating: "Reserve bed opened", ok: "Within capacity", unresolved: "Unplaced patients" },
      allCities: "All cities", target: "Target week", offline: "Runs fully offline", km: "km",
    },
    bn: {
      tabOps: "অপারেশন",
      title: "অপারেশন কমান্ড", sub: "পূর্বাভাসকে স্যালাইন, কিট, স্প্রে দল আর হাসপাতাল বেডে রূপান্তর করুন। স্টক কম হলে কে আগে পাবে, স্লাইডার নাড়িয়ে দেখুন।",
      week: "পূর্বাভাসের সপ্তাহ", horizon: "পরিকল্পনা", weeks: "সপ্তাহ আগে", basis: "ভিত্তি", expected: "প্রত্যাশিত রোগী", upper: "সবচেয়ে খারাপ (৮০% উচ্চসীমা)",
      policy: "স্টক কম হলে", weighted: "অগ্রাধিকার অনুযায়ী ভাগ", strict: "কড়া ট্রায়াজ",
      policyHelp: { weighted: "সব ওয়ার্ড কিছু পাবে; বেশি অগ্রাধিকারের ওয়ার্ড বড় ভাগ পাবে, আগে পূর্ণ হবে।", strict: "অগ্রাধিকারের ক্রমে একে একে পুরো চাহিদা মেটানো হবে, স্টক শেষ না হওয়া পর্যন্ত।" },
      stock: "আঞ্চলিক স্টক", stockHelp: "মোট চাহিদার অংশ", crews: "স্প্রে দল", crewsHelp: "লার্ভিসাইড / ফগিং দল · চাহিদা: মাঝারি ১, উচ্চ ২, সংকট ৩, ঘনত্ব ≥ ৬০,০০০/কিমি² হলে +১",
      capScale: "হাসপাতাল বেড ক্ষমতা", capHelp: "প্রতিটি ওয়ার্ডের স্বাভাবিক ডেঙ্গু বেডের",
      life: "প্রতিটি ওয়ার্ডের ন্যূনতম", lifeHelp: "অগ্রাধিকার অনুযায়ী ভাগের আগে প্রতিটি জিনিসের নিশ্চিত অংশ (০% = বন্ধ)",
      real: "অথবা গুদামে আসলে কত আছে লিখুন", realNote: "আসল সংখ্যা দিলে ওই জিনিসের জন্য স্টক স্লাইডার আর লাগবে না।", clear: "মুছুন",
      reset: "রিসেট", resetAll: "সব রিসেট", resetTip: "ডিফল্ট সেটিংয়ে ফিরুন", clearReserve: "সংরক্ষিত বেড মুছুন",
      realItems: { saline: "স্যালাইন ব্যাগ", kits: "পরীক্ষার কিট", beds: "হাসপাতাল বেড" }, overridden: "আসল সংখ্যা ব্যবহার হচ্ছে",
      fixTitle: "সব জরুরি ঘাটতি দূর করতে যা লাগবে",
      fixMore: (s, k, b) => `আরও স্টক আনুন: +${loc(s)} স্যালাইন, +${loc(k)} কিট, +${loc(b)} বেড`,
      fixTry: (p) => `${loc(p)}% স্টক দিয়ে দেখুন`, fixLife: "অথবা অন্যভাবে ভাগ করুন: আগে প্রতিটি ওয়ার্ডকে ৫০% নিশ্চিত করুন",
      fixLifeBtn: "৫০% ন্যূনতম চালু করুন", fixLifeNote: "একই স্টক; উপরের ওয়ার্ডগুলো একটু কম পায়, তাই কোনো ওয়ার্ড অর্ধেকের নিচে নামে না।",
      fixNoStock: "এই স্টকে ৫০% ন্যূনতমও সম্ভব না। আরও স্টকই একমাত্র উপায়।",
      items: { saline_bags_needed: "স্যালাইন ব্যাগ", diagnostic_kits_needed: "পরীক্ষার কিট", hospital_beds_required: "হাসপাতাল বেড" },
      crewsK: "স্প্রে দল", allocated: "বরাদ্দ", required: "দরকার", unmet: "ঘাটতি", idle: "অলস",
      alertN: (n, c) => `${loc(n)}টি ওয়ার্ড পুরো চাহিদা পাচ্ছে না — ${loc(c)}টি জরুরি।`,
      alertOk: "এই সপ্তাহে সব ওয়ার্ড পুরো চাহিদা পাচ্ছে।",
      critWhy: "জরুরি = কোনো একটি জিনিস চাহিদার ৫০%-এর কম।",
      tblTitle: "অগ্রাধিকার অনুযায়ী বরাদ্দ", tblSub: "অগ্রাধিকার = ঝুঁকির ওজন (কম ১ · মাঝারি ২ · উচ্চ ৩ · সংকট ৪) × ওয়ার্ডের ঘনত্ব ÷ গড় ঘনত্ব।",
      cols: ["#", "ওয়ার্ড", "ঝুঁকি", "ঘনত্ব /কিমি²", "রোগী", "স্যালাইন", "কিট", "বেড", "দল", "অবস্থা"],
      status: { ok: "পুরো পূরণ", deficit: "ঘাটতি", critical: "জরুরি ঘাটতি" },
      csv: "বরাদ্দ CSV ডাউনলোড", csvT: "স্থানান্তর পরিকল্পনা CSV", csvDone: "ডাউনলোড হয়েছে",
      surgeTitle: "হাসপাতাল চাপ ও রোগী স্থানান্তর",
      surgeSub: "যে ওয়ার্ডে বেডের চেয়ে রোগী বেশি, বাড়তি রোগী একই শহরের সবচেয়ে কম চাপের ওয়ার্ডে যায়। শহরের বাইরে কখনো নয়। পুরো শহর ভরে গেলে সংরক্ষিত (ভাসমান) বেড রোগীর ওয়ার্ডেই খোলে।",
      capRule: "স্থানীয় ক্ষমতা = ২০১৬–২০২৩ সালে ওয়ার্ডের সাপ্তাহিক বেড চাহিদার ৯৫তম পার্সেন্টাইল (কমপক্ষে ২), × স্লাইডার।",
      util: "বেড ব্যবহার", before: "আগে", after: "স্থানান্তরের পরে", headroom: "খালি বেড বাকি", moved: "রোগী স্থানান্তর", unresolvedN: "জায়গা নেই",
      cityStatus: { ok: "ঠিক আছে", strained: "চাপে", escalate: "জরুরি ব্যবস্থা" },
      escalate: (c, n) => `${c}: ${loc(n)} জন রোগীর শহরের ভেতরে জায়গা নেই — সংরক্ষিত বেড খুলুন বা অন্যত্র পাঠান।`,
      reserve: "সংরক্ষিত বেড", reserveUsed: (u, p) => `${p}টির মধ্যে ${u}টি ব্যবহৃত`, reserveOpened: "সংরক্ষিত বেড খোলা হয়েছে",
      openReserve: (n) => `${loc(n)}টি সংরক্ষিত বেড খুলুন`, reserveHelp: "কোনো ওয়ার্ডের নয় এমন ভাসমান বেড (অস্থায়ী খাট, মোবাইল দল)। স্থানান্তরের পরে, রোগী যেখানে আছে সেখানেই ব্যবহার হয়।",
      flowTitle: "স্থানান্তর ম্যাপ", noTransfers: "এই সপ্তাহে এই শহরে স্থানান্তর লাগবে না।",
      tCols: ["থেকে", "যেখানে", "রোগী", "দূরত্ব"],
      wCols: ["ওয়ার্ড", "বেড দরকার", "ক্ষমতা", "নিট", "পাঠানো", "গ্রহণ", "সংরক্ষিত", "পরে ব্যবহার"],
      wStatus: { ok: "ঠিক আছে", surge: "চাপ", receiving: "গ্রহণ করছে", floating: "সংরক্ষিত বেড খোলা", unresolved: "শহরের ক্ষমতার বাইরে" },
      legend: { surge: "বাড়তি রোগী পাঠাচ্ছে", receiving: "গ্রহণ করছে", floating: "সংরক্ষিত বেড খোলা", ok: "ক্ষমতার মধ্যে", unresolved: "জায়গা হয়নি" },
      allCities: "সব শহর", target: "লক্ষ্য সপ্তাহ", offline: "সম্পূর্ণ অফলাইনে চলে", km: "কিমি",
    },
  };
  // register the nav label so app.js's applyLang() can fill it
  Object.entries(STR).forEach(([l, o]) => { if (window.I18N && window.I18N[l]) window.I18N[l].tabOps = o.tabOps; });

  const DEF = { stock: 0.6, crews: 60, cap: 1.0, life: 0, basis: "expected", policy: "weighted" };   // what Reset goes back to
  const O = { stock: 0.6, crews: 60, cap: 1.0, float: {}, life: 0, real: { saline: "", kits: "", beds: "" }, basis: "expected", policy: "weighted", city: null, alloc: null, surge: null, prevStatus: {}, built: false, seq: 0 };
  const SK = () => window.SK;
  const L = () => STR[SK().S.lang] || STR.en;
  const loc = (x) => SK().loc(x);
  const pct = (x) => loc(Math.round(x * 100)) + "%";
  const wname = (id) => { const w = SK().ward(id); return w ? `${SK().cityName(w.city)} · ${SK().wardLabel(id)}` : id; };
  const wshort = (id) => (SK().ward(id) ? SK().wardLabel(id) : id);
  const params = () => {
    const S = SK().S;
    return { date: S.mode === "live" ? undefined : S.date, h: S.h, mode: S.mode, rain: S.scen.rain, temp: S.scen.temp, hum: S.scen.hum, basis: O.basis };
  };

  // ------------------------------------------------------------------ layout
  function build() {
    const T = L(), S = SK().S;
    $("#tab-ops").innerHTML = `
      <div class="hero"><div><h1>${T.title}</h1><p>${T.sub}</p></div>
        <span class="ops-offline"><i></i>${T.offline}</span></div>
      <div class="card ops-deck">
        <button type="button" class="ops-reset ops-resetall" id="opsResetAll" title="${T.resetTip}" hidden>↺ ${T.resetAll}</button>
        <div class="ops-row">
          <div class="ops-field"><span class="eyebrow">${T.week}</span>
            <div class="ops-week"><button class="step" data-wk="-1" aria-label="Previous week">‹</button><b id="opsWeek">–</b><button class="step" data-wk="1" aria-label="Next week">›</button></div>
            <small id="opsTarget"></small></div>
          <div class="ops-field"><span class="eyebrow">${T.horizon}</span>
            <div class="seg" id="opsH">${[2, 3, 4].map((h) => `<button data-h="${h}" aria-pressed="${h === S.h}">${loc(h)} ${T.weeks}</button>`).join("")}</div></div>
          <div class="ops-field"><span class="eyebrow">${T.basis}</span>
            <div class="seg" id="opsBasis"><button data-b="expected" aria-pressed="${O.basis === "expected"}">${T.expected}</button><button data-b="upper" aria-pressed="${O.basis === "upper"}">${T.upper}</button></div></div>
          <div class="ops-field"><span class="eyebrow">${T.policy}</span>
            <div class="seg" id="opsPolicy"><button data-p="weighted" aria-pressed="${O.policy === "weighted"}">${T.weighted}</button><button data-p="strict" aria-pressed="${O.policy === "strict"}">${T.strict}</button></div>
            <small id="opsPolicyHelp">${T.policyHelp[O.policy]}</small></div>
        </div>
        <div class="ops-sliders">
          ${slider("opsStock", T.stock, T.stockHelp, 30, 100, 5, Math.round(O.stock * 100), "%", Math.round(DEF.stock * 100))}
          ${slider("opsLife", T.life, T.lifeHelp, 0, 50, 5, Math.round(O.life * 100), "%", Math.round(DEF.life * 100))}
          ${slider("opsCrews", T.crews, T.crewsHelp, 0, 200, 1, O.crews, "", DEF.crews)}
          ${slider("opsCap", T.capScale, T.capHelp, 50, 200, 5, Math.round(O.cap * 100), "%", Math.round(DEF.cap * 100))}
        </div>
        <div class="ops-real"><span class="eyebrow">${T.real}</span>
          ${["saline", "kits", "beds"].map((k) => `<label class="ops-num"><span>${T.realItems[k]}</span><input type="number" min="0" step="1" inputmode="numeric" id="opsReal_${k}" value="${O.real[k]}"></label>`).join("")}
          <button class="btn" id="opsRealClear">${T.clear}</button><small>${T.realNote}</small></div>
      </div>
      <div class="cards" id="opsKpis"></div>
      <div id="opsAlert"></div>
      <div class="card"><div class="toolbar"><div><h3 class="sec-title">${T.tblTitle}</h3><small class="ops-muted">${T.tblSub}</small></div>
        <button class="btn" id="opsCsv">${dl()}<span>${T.csv}</span></button></div>
        <div class="ops-table-wrap"><table class="ops-table" id="opsTable"></table></div></div>
      <h2 class="h-font ops-h2">${T.surgeTitle}</h2>
      <p class="ops-muted ops-p">${T.surgeSub}<br><small>${T.capRule}</small></p>
      <div id="opsHospReset"></div>
      <div class="ops-cities" id="opsCities"></div>
      <div id="opsEsc"></div>
      <div class="two">
        <div class="card"><div class="toolbar"><h3 class="sec-title">${T.flowTitle}</h3><div class="chips" id="opsCityChips"></div></div>
          <div id="opsFlow"></div><div class="ops-legend">${Object.entries(T.legend).map(([k, v]) => `<span><i style="background:${ST_COL[k]}"></i>${v}</span>`).join("")}</div></div>
        <div class="card"><div class="toolbar"><h3 class="sec-title" id="opsTransTitle"></h3><button class="btn" id="opsCsvT">${dl()}<span>${T.csvT}</span></button></div>
          <div id="opsTransfers"></div><div class="ops-table-wrap" style="margin-top:12px"><table class="ops-table" id="opsWardTable"></table></div></div>
      </div>`;
    wireDeck();
    O.built = true;
  }
  const dl = () => '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>';
  function slider(id, label, help, min, max, step, val, unit, def) {
    return `<label class="ops-slider" for="${id}"><span class="ops-sl-top"><span class="eyebrow">${label}</span>
      <span class="ops-sl-r"><button type="button" class="ops-reset" data-reset="${id}" data-def="${def}" title="${L().resetTip}" hidden>↺ ${L().reset}</button><b id="${id}V">${loc(val)}${unit}</b></span></span>
      <input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${val}" data-unit="${unit}" style="--p:${(val - min) / (max - min) * 100}%">
      <small>${help}</small></label>`;
  }
  function wireDeck() {
    const S = SK().S, go = SK().debounce(load, 140);
    $$("#tab-ops input[type=range]").forEach((inp) => inp.addEventListener("input", () => {
      const v = +inp.value, u = inp.dataset.unit;
      $(`#${inp.id}V`).textContent = loc(v) + u;
      inp.style.setProperty("--p", ((v - inp.min) / (inp.max - inp.min) * 100) + "%");
      if (inp.id === "opsStock") O.stock = v / 100;
      if (inp.id === "opsCrews") O.crews = v;
      if (inp.id === "opsCap") O.cap = v / 100;
      if (inp.id === "opsLife") O.life = v / 100;
      updateResets(); go();
    }));
    $$("#tab-ops .ops-reset[data-reset]").forEach((b) => b.onclick = (e) => { e.preventDefault(); e.stopPropagation(); setSlider(b.dataset.reset, +b.dataset.def); });
    $("#opsResetAll").onclick = resetAll;
    const goReal = SK().debounce(load, 350);
    ["saline", "kits", "beds"].forEach((k) => $(`#opsReal_${k}`).addEventListener("input", (e) => {
      const v = e.target.value.trim();
      O.real[k] = v === "" ? "" : String(Math.max(0, Math.floor(+v) || 0));
      goReal(); markReal(); updateResets();
    }));
    $("#opsRealClear").onclick = () => { O.real = { saline: "", kits: "", beds: "" }; ["saline", "kits", "beds"].forEach((k) => $(`#opsReal_${k}`).value = ""); markReal(); updateResets(); load(); };
    markReal();
    $$("#opsH button").forEach((b) => b.onclick = () => { SK().setH(+b.dataset.h); $$("#opsH button").forEach((x) => x.setAttribute("aria-pressed", x === b)); load(); });
    $$("#opsBasis button").forEach((b) => b.onclick = () => { O.basis = b.dataset.b; $$("#opsBasis button").forEach((x) => x.setAttribute("aria-pressed", x === b)); updateResets(); load(); });
    $$("#opsPolicy button").forEach((b) => b.onclick = () => {
      O.policy = b.dataset.p; $$("#opsPolicy button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      $("#opsPolicyHelp").textContent = L().policyHelp[O.policy]; updateResets(); load();
    });
    $$(".ops-week .step").forEach((b) => b.onclick = () => {
      const i = S.dates.indexOf(S.date) + +b.dataset.wk;
      if (S.mode === "replay" && i >= 0 && i < S.dates.length) { SK().setDate(S.dates[i]); load(); }
    });
    $("#opsCsv").onclick = csvAlloc;
    $("#opsCsvT").onclick = csvTransfers;
    updateResets();
  }
  // Reset buttons show only where something differs from the default
  const anyReserve = () => Object.values(O.float).some((n) => n > 0);
  const anyReal = () => ["saline", "kits", "beds"].some((k) => O.real[k] !== "");
  function updateResets() {
    $$("#tab-ops .ops-reset[data-reset]").forEach((b) => { const inp = $("#" + b.dataset.reset); b.hidden = !inp || +inp.value === +b.dataset.def; });
    const changed = O.stock !== DEF.stock || O.crews !== DEF.crews || O.cap !== DEF.cap || O.life !== DEF.life
      || O.basis !== DEF.basis || O.policy !== DEF.policy || anyReal() || anyReserve();
    const all = $("#opsResetAll"); if (all) all.hidden = !changed;
    const hr = $("#opsHospReset");
    if (hr) {
      hr.innerHTML = anyReserve() ? `<button type="button" class="ops-reset" id="opsClearReserve" title="${L().resetTip}">↺ ${L().clearReserve}</button>` : "";
      const cb = $("#opsClearReserve"); if (cb) cb.onclick = () => { O.float = {}; updateResets(); load(); };
    }
  }
  function resetAll() {
    Object.assign(O, DEF, { float: {}, real: { saline: "", kits: "", beds: "" } });
    build(); load();
  }

  // ------------------------------------------------------------------ data
  const floatParam = () => Object.entries(O.float).filter(([, n]) => n > 0).map(([c, n]) => `${c}:${n}`).join(",") || undefined;
  async function load() {
    const my = ++O.seq, p = params();
    $("#tab-ops").classList.add("busy");
    try {
      const [a, s] = await Promise.all([
        SK().api("/api/ops/allocate", { ...p, stock: O.stock, crews: O.crews, policy: O.policy, min_share: O.life || undefined,
          saline_stock: O.real.saline || undefined, kits_stock: O.real.kits || undefined, beds_stock: O.real.beds || undefined }),
        SK().api("/api/ops/surge", { ...p, cap_scale: O.cap, floating: floatParam() }),
      ]);
      if (my !== O.seq) return;
      O.alloc = a; O.surge = s;
      if (!O.city || !s.cities.some((c) => c.city === O.city)) {
        const worst = s.cities.slice().sort((x, y) => (y.transferred + y.unresolved) - (x.transferred + x.unresolved) || y.util_before - x.util_before)[0];
        O.city = worst ? worst.city : CITIES[0];
      }
      render();
    } catch (e) { if (my === O.seq) SK().toast(e.message); }
    finally { if (my === O.seq) $("#tab-ops").classList.remove("busy"); }
  }

  function markReal() {
    const on = ["saline", "kits", "beds"].some((k) => O.real[k] !== "");
    const card = $("#opsStock") && $("#opsStock").closest(".ops-slider");
    if (card) { card.classList.toggle("ops-off", on); const b = $("#opsStockV"); if (b) b.title = on ? L().overridden : ""; }
  }
  function fixBlock(a) {
    const T = L(), e = a.fix.extra, p = Math.min(100, Math.max(30, Math.ceil(Math.round(a.fix.stock_pct * 100) / 5) * 5));   // slider moves in 5% steps
    const lifeFits = a.items.saline_bags_needed.stock >= Math.ceil(0.5 * a.items.saline_bags_needed.required);
    return `<div class="ops-fixbox"><span class="eyebrow">${T.fixTitle}</span>
      <div class="ops-fixopt"><span>${T.fixMore(e.saline_bags_needed, e.diagnostic_kits_needed, e.hospital_beds_required)}</span>
        <button class="btn ops-fixbtn" data-fix="stock" data-p="${p}">${T.fixTry(p)}</button></div>
      ${O.life < 0.5 ? `<div class="ops-fixopt"><span>${lifeFits ? T.fixLife : T.fixNoStock}${lifeFits ? `<br><small>${T.fixLifeNote}</small>` : ""}</span>
        ${lifeFits ? `<button class="btn ops-fixbtn" data-fix="life">${T.fixLifeBtn}</button>` : ""}</div>` : ""}</div>`;
  }
  function setSlider(id, v) {
    const inp = $("#" + id); if (!inp) return;
    inp.value = v; inp.dispatchEvent(new Event("input"));
  }

  // ------------------------------------------------------------------ render
  function render() {
    const T = L(), a = O.alloc, s = O.surge, S = SK().S;
    updateResets();
    $("#opsWeek").textContent = SK().fmtDate(a.ref_date, true);
    $("#opsTarget").textContent = `${T.target}: ${SK().fmtDate(a.target_date, true)}`;
    // KPI cards
    const kp = ITEMS.map((k) => {
      const x = a.items[k], f = x.required ? x.allocated / x.required : 1, col = f >= 0.999 ? "#22C55E" : f >= 0.6 ? "#FACC15" : "#F43F5E";
      return card(T.items[k], x.allocated, x.required, f, col, x.unmet ? `${loc(x.unmet.toLocaleString())} ${T.unmet}` : "");
    });
    const c = a.crews, cf = c.needed ? c.assigned / c.needed : 1;
    kp.push(card(T.crewsK, c.assigned, c.needed, cf, cf >= 0.999 ? "#22C55E" : cf >= 0.6 ? "#FACC15" : "#F43F5E",
      c.unmet ? `${loc(c.unmet)} ${T.unmet}` : (c.idle ? `${loc(c.idle)} ${T.idle}` : "")));
    $("#opsKpis").innerHTML = kp.join("");
    // alert strip
    const crit = a.warnings.filter((w) => w.level === "critical");
    $("#opsAlert").innerHTML = a.n_deficit
      ? `<div class="action alert ops-alert"><b>⚠</b><div><b>${T.alertN(a.n_deficit, a.n_critical)}</b> <small>${T.critWhy}</small>
          <div class="ops-chipline">${(crit.length ? crit : a.warnings).slice(0, 12).map((w) => `<span class="ops-wchip ${w.level}">${wname(w.ward)} · ${pct(w.min_fill)}</span>`).join("")}${a.n_deficit > 12 ? `<span class="ops-wchip">+${loc(a.n_deficit - 12)}</span>` : ""}</div>
          ${a.fix && a.fix.needed && a.n_critical ? fixBlock(a) : ""}</div></div>`
      : `<div class="action ok"><b>✓</b><div>${T.alertOk}</div></div>`;
    $$("#opsAlert .ops-fixbtn").forEach((b) => b.onclick = () => {
      if (b.dataset.fix === "stock") {
        O.real = { saline: "", kits: "", beds: "" }; ["saline", "kits", "beds"].forEach((k) => { const i = $(`#opsReal_${k}`); if (i) i.value = ""; });
        markReal(); setSlider("opsStock", +b.dataset.p);
      } else setSlider("opsLife", 50);
    });
    // allocation table
    const bar = (x) => { const f = x.required ? x.allocated / x.required : 1;
      return `<div class="ops-cell"><span>${loc(x.allocated)}<small>/${loc(x.required)}</small></span><i><b style="width:${Math.round(f * 100)}%;background:${f >= 0.999 ? "#22C55E" : f >= 0.6 ? "#FACC15" : "#F43F5E"}"></b></i></div>`; };
    const changed = new Set(a.wards.filter((w) => O.prevStatus[w.ward] && O.prevStatus[w.ward] !== w.status).map((w) => w.ward));
    $("#opsTable").innerHTML = `<thead><tr>${T.cols.map((x) => `<th>${x}</th>`).join("")}</tr></thead><tbody>${a.wards.map((w) => `
      <tr class="${changed.has(w.ward) ? "flash" : ""}"><td class="ops-rank">${loc(w.rank)}</td><td><b>${wname(w.ward)}</b></td>
        <td><span class="ops-tier" style="background:${SK().HEX[w.tier]};color:${SK().TEXT_ON[w.tier]}">${SK().tierName(w.tier)}</span></td>
        <td>${loc(w.density.toLocaleString())}</td><td>${SK().nf(w.cases)}</td>
        <td>${bar(w.saline_bags_needed)}</td><td>${bar(w.diagnostic_kits_needed)}</td><td>${bar(w.hospital_beds_required)}</td>
        <td>${bar({ allocated: w.crews_assigned, required: w.crews_needed })}</td>
        <td><span class="ops-st ${w.status}">${T.status[w.status]}</span></td></tr>`).join("")}</tbody>`;
    O.prevStatus = Object.fromEntries(a.wards.map((w) => [w.ward, w.status]));

    // ---- surge: city cards
    $("#opsCities").innerHTML = s.cities.map((x) => {
      const ub = x.util_before || 0, ua = x.util_after || 0;
      return `<div role="button" tabindex="0" class="ops-city ${x.status} ${x.city === O.city ? "on" : ""}" data-city="${x.city}">
        <span class="eyebrow">${SK().cityName(x.city)}</span><span class="ops-cst ${x.status}">${T.cityStatus[x.status]}</span>
        <div class="ops-gauge"><i class="b" style="width:${Math.min(100, ub * 100)}%"></i><i class="a" style="width:${Math.min(100, ua * 100)}%"></i>${ub > 1 ? `<em style="left:${100 / ub}%"></em>` : ""}</div>
        <div class="ops-cnums"><span><b>${pct(ub)}</b>${T.before}</span><span><b>${pct(ua)}</b>${T.after}</span></div>
        <small>${loc(x.required)} / ${loc(x.capacity)} ${T.items.hospital_beds_required.toLowerCase()} · <b>${loc(x.headroom)}</b> ${T.headroom}${x.transferred ? ` · <b>${loc(x.transferred)}</b> ${T.moved}` : ""}${x.unresolved ? ` · <b class="red">${loc(x.unresolved)}</b> ${T.unresolvedN}` : ""}</small>
        <div class="ops-reserve" title="${T.reserveHelp}"><span>${T.reserve}</span>
          <button class="ops-rb" data-rc="${x.city}" data-d="-1" aria-label="${T.reserve} −">−</button><b>${loc(x.floating_pool || 0)}</b><button class="ops-rb" data-rc="${x.city}" data-d="1" aria-label="${T.reserve} +">+</button>
          ${x.floating_pool ? `<small>${T.reserveUsed(loc(x.floating_used), loc(x.floating_pool))}</small>` : ""}</div></div>`;
    }).join("");
    $$("#opsCities .ops-city").forEach((b) => {
      b.onclick = () => { O.city = b.dataset.city; render(); };
      b.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); b.click(); } };
    });
    $$("#opsCities .ops-rb").forEach((b) => b.onclick = (e) => {
      e.stopPropagation();
      const c = b.dataset.rc;
      O.float[c] = Math.max(0, Math.min(99, (O.float[c] || 0) + +b.dataset.d));
      O.city = c; load();
    });
    const esc = s.cities.filter((x) => x.unresolved);
    $("#opsEsc").innerHTML = esc.length ? `<div class="action alert" style="flex-direction:column;gap:6px">${esc.map((x) => `<div class="ops-escrow"><span>⚠ ${T.escalate(SK().cityName(x.city), x.unresolved)}</span>
      <button class="btn ops-fix" data-fc="${x.city}" data-n="${x.unresolved}">${T.openReserve(x.unresolved)}</button></div>`).join("")}</div>` : "";
    $$("#opsEsc .ops-fix").forEach((b) => b.onclick = () => {
      const c = b.dataset.fc; O.float[c] = (O.float[c] || 0) + +b.dataset.n; O.city = c; load();
    });
    $("#opsCityChips").innerHTML = s.cities.map((x) => `<button class="chip" aria-pressed="${x.city === O.city}" data-city="${x.city}">${SK().cityName(x.city)}</button>`).join("");
    $$("#opsCityChips .chip").forEach((b) => b.onclick = () => { O.city = b.dataset.city; render(); });
    renderFlow();
    // transfers + ward table for the selected city
    const tr = s.transfers.filter((x) => x.city === O.city);
    $("#opsTransTitle").textContent = `${SK().cityName(O.city)} · ${T.moved}: ${loc(tr.reduce((n, x) => n + x.patients, 0))}`;
    $("#opsTransfers").innerHTML = tr.length
      ? `<table class="ops-table"><thead><tr>${T.tCols.map((x) => `<th>${x}</th>`).join("")}</tr></thead><tbody>${tr.map((x) => `<tr><td><b>${wshort(x.from)}</b></td><td>→ ${wshort(x.to)}</td><td><b>${loc(x.patients)}</b></td><td>${x.km != null ? SK().nf(x.km) + " " + T.km : "–"}</td></tr>`).join("")}</tbody></table>`
      : `<p class="ops-muted">${T.noTransfers}</p>`;
    const ws = s.wards.filter((w) => w.city === O.city).sort((x, y) => x.net - y.net);
    $("#opsWardTable").innerHTML = `<thead><tr>${T.wCols.map((x) => `<th>${x}</th>`).join("")}</tr></thead><tbody>${ws.map((w) => `
      <tr><td><b>${wshort(w.ward)}</b> <span class="ops-st ${w.status}">${T.wStatus[w.status]}</span></td><td>${loc(w.required)}</td><td>${loc(w.capacity)}</td>
      <td class="${w.net < 0 ? "red" : "green"}"><b>${w.net > 0 ? "+" : ""}${loc(w.net)}</b></td><td>${w.sent ? loc(w.sent) : "–"}</td><td>${w.received ? loc(w.received) : "–"}</td>
      <td>${w.floating ? `<b class="purple">+${loc(w.floating)}</b>` : "–"}</td>
      <td>${w.util_after != null ? pct(w.util_after) : "–"}</td></tr>`).join("")}</tbody>`;
  }
  function card(label, got, need, f, col, foot) {
    const r = 26, C = 2 * Math.PI * r;
    return `<div class="stat ops-kpi"><div class="ops-kpi-top"><div><small>${label}</small><span class="v">${loc(got.toLocaleString())}</span>
      <small>${L().allocated} · ${loc(need.toLocaleString())} ${L().required}</small></div>
      <svg width="64" height="64" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="${r}" fill="none" stroke="#1B2742" stroke-width="7"/>
        <circle cx="32" cy="32" r="${r}" fill="none" stroke="${col}" stroke-width="7" stroke-linecap="round" stroke-dasharray="${C * Math.min(1, f)} ${C}" transform="rotate(-90 32 32)"/>
        <text x="32" y="37" text-anchor="middle" fill="#E8EEF9" font-size="14" font-weight="700">${pct(f)}</text></svg></div>
      ${foot ? `<small class="ops-foot" style="color:${col}">${foot}</small>` : ""}</div>`;
  }

  // ------------------------------------------------------------------ flow map (SVG)
  function renderFlow() {
    const s = O.surge, T = L();
    const ws = s.wards.filter((w) => w.city === O.city);
    if (!ws.length) { $("#opsFlow").innerHTML = ""; return; }
    const P = s.positions, W = 520, H = 340, pad = 46;
    const lats = ws.map((w) => P[w.ward][0]), lons = ws.map((w) => P[w.ward][1]);
    const [la0, la1, lo0, lo1] = [Math.min(...lats), Math.max(...lats), Math.min(...lons), Math.max(...lons)];
    const sc = Math.min((W - 2 * pad) / Math.max(lo1 - lo0, 1e-3), (H - 2 * pad) / Math.max(la1 - la0, 1e-3));
    const xy = (id) => [W / 2 + (P[id][1] - (lo0 + lo1) / 2) * sc, H / 2 - (P[id][0] - (la0 + la1) / 2) * sc];
    const maxReq = Math.max(1, ...ws.map((w) => Math.max(w.required, w.capacity)));
    const rad = (v) => 7 + 15 * Math.sqrt(v / maxReq);
    const tr = s.transfers.filter((x) => x.city === O.city);
    const arrows = tr.map((x, i) => {
      const [x1, y1] = xy(x.from), [x2, y2] = xy(x.to), mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1, bend = 0.22 * len;
      const cx = mx - dy / len * bend, cy = my + dx / len * bend;
      return { path: `<path d="M${x1},${y1} Q${cx},${cy} ${x2},${y2}" class="ops-arrow" stroke-width="${1.5 + 1.6 * x.patients}" marker-end="url(#opsHead)" style="animation-delay:${i * 0.15}s"/>`,
        label: `<text x="${0.25 * x1 + 0.5 * cx + 0.25 * x2}" y="${0.25 * y1 + 0.5 * cy + 0.25 * y2 - 8}" class="ops-alabel">${loc(x.patients)}</text>` };
    });
    const nodes = ws.map((w) => {
      const [x, y] = xy(w.ward), rc = rad(w.capacity), rr = rad(w.required), col = ST_COL[w.status];
      return `<g class="ops-node"><title>${wname(w.ward)} — ${loc(w.required)}/${loc(w.capacity)}</title>
        <circle cx="${x}" cy="${y}" r="${rc}" fill="none" stroke="#3A4A6E" stroke-dasharray="3 3"/>
        <circle cx="${x}" cy="${y}" r="${rr}" fill="${col}" fill-opacity=".85" ${w.status === "unresolved" ? 'class="ops-pulse"' : ""}/>
        <text x="${x}" y="${y + 4}" text-anchor="middle" class="ops-nlabel">${loc(SK().ward(w.ward).n)}</text>
        ${w.floating ? `<g class="ops-float"><rect x="${x + rr - 4}" y="${y - rr - 10}" width="30" height="18" rx="9"/><text x="${x + rr + 11}" y="${y - rr + 3}" text-anchor="middle">+${loc(w.floating)}</text></g>` : ""}</g>`;
    }).join("");
    $("#opsFlow").innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="ops-svg" role="img" aria-label="${T.flowTitle}">
      <defs><marker id="opsHead" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#22D3EE"/></marker></defs>
      ${arrows.map((a) => a.path).join("")}${nodes}${arrows.map((a) => a.label).join("")}</svg>
      <small class="ops-muted">○ ${T.wCols[2]} · ● ${T.wCols[1]}</small>`;
  }

  // ------------------------------------------------------------------ exports
  function download(name, rows) {
    const csv = rows.map((r) => r.map((v) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : v)).join(",")).join("\n");
    const el = document.createElement("a");
    el.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); el.download = name; el.click();
    SK().toast(L().csvDone);
  }
  function csvAlloc() {
    const a = O.alloc; if (!a) return;
    const rows = [["rank", "ward_id", "city", "tier", "population_density_sqkm", "planning_cases", "priority_weight",
      "saline_required", "saline_allocated", "kits_required", "kits_allocated", "beds_required", "beds_allocated",
      "crews_needed", "crews_assigned", "status", "forecast_week", "target_week", "stock_pct", "crews_available", "policy", "basis"]];
    a.wards.forEach((w) => rows.push([w.rank, w.ward, w.city, w.tier, w.density, w.cases, w.priority,
      w.saline_bags_needed.required, w.saline_bags_needed.allocated, w.diagnostic_kits_needed.required, w.diagnostic_kits_needed.allocated,
      w.hospital_beds_required.required, w.hospital_beds_required.allocated, w.crews_needed, w.crews_assigned, w.status,
      a.ref_date, a.target_date, a.stock_pct, a.crews.available, a.policy, a.basis]));
    download(`shotorko_allocation_${a.target_date}_stock${Math.round(a.stock_pct * 100)}.csv`, rows);
  }
  function csvTransfers() {
    const s = O.surge; if (!s) return;
    const rows = [["city", "from_ward", "to_ward", "patients", "km_schematic", "target_week"]];
    s.transfers.forEach((x) => rows.push([x.city, x.from, x.to, x.patients, x.km, s.target_date]));
    const fl = s.wards.filter((w) => w.floating);
    if (fl.length) { rows.push([]); rows.push(["city", "ward_with_reserve_beds", "reserve_beds_opened"]); fl.forEach((w) => rows.push([w.city, w.ward, w.floating])); }
    rows.push([]); rows.push(["city", "capacity", "beds_needed", "util_before", "util_after", "spare_beds_left", "moved", "reserve_beds", "reserve_used", "unplaced", "status"]);
    s.cities.forEach((x) => rows.push([x.city, x.capacity, x.required, x.util_before, x.util_after, x.headroom, x.transferred, x.floating_pool, x.floating_used, x.unresolved, x.status]));
    download(`shotorko_bed_transfers_${s.target_date}.csv`, rows);
  }

  // ------------------------------------------------------------------ hooks
  function open() { build(); load(); }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("nav.tabs button");
    if (b && b.dataset.tab === "ops") setTimeout(open, 0);
    if (e.target.closest("#langEn, #langBn") && O.built && $("#tab-ops").classList.contains("active")) setTimeout(() => { build(); render(); }, 0);
  });
})();
