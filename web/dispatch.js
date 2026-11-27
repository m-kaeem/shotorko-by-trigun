/* Shotorko dispatch centre (Phase 2, Requirement 4).
   Lists the wards for a week, shows each ward's SMS / WhatsApp / printable brief, and copies a whole
   round of SMS at once. Everything comes from the local API; no external services. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const BN_DIG = "০১২৩৪৫৬৭৮৯";
  const HEX = { Low: "#22C55E", Medium: "#FACC15", High: "#FB923C", Critical: "#F43F5E" };
  const MONTHS = { en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    bn: ["জানু", "ফেব্রু", "মার্চ", "এপ্রিল", "মে", "জুন", "জুলাই", "আগস্ট", "সেপ্টে", "অক্টো", "নভে", "ডিসে"] };
  const TIER_MIN = { all: "Low", high: "High", crit: "Critical" };
  const url = new URLSearchParams(location.search);
  const S = {
    lang: url.get("lang") === "bn" ? "bn" : "en", date: url.get("date") || "2025-07-07", h: +(url.get("h") || 3),
    stock: +(url.get("stock") || 100), crews: +(url.get("crews") || 20), tier: url.get("tier") || "high",
    ward: url.get("ward"), ch: "sms", list: [], detail: null, seqL: 0, seqD: 0, dates: [], gw: null, box: {}, poll: null,
  };
  try { if (!url.get("lang")) { const l = localStorage.getItem("sk-lang"); if (l === "bn" || l === "en") S.lang = l; } } catch (e) { /* ignore */ }

  const t = (k) => window.I18N[S.lang][k];
  const loc = (s) => (S.lang === "bn" ? String(s).replace(/[0-9]/g, (d) => BN_DIG[d]) : String(s));
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtDate = (iso) => { const [y, m, d] = iso.split("-").map(Number); return loc(`${d} ${MONTHS[S.lang][m - 1]} ${y}`); };
  function toast(msg) { const el = $("#toast"); el.textContent = msg; el.classList.add("on"); clearTimeout(toast.k); toast.k = setTimeout(() => el.classList.remove("on"), 2400); }
  async function api(path, params = {}) {
    const u = new URL(path, location.origin);
    Object.entries(params).forEach(([k, v]) => v != null && u.searchParams.set(k, v));
    const r = await fetch(u);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  }
  const common = () => ({ date: S.date, h: S.h, stock_pct: S.stock, total_crews: S.crews, lang: S.lang });
  function syncUrl() {
    const q = new URLSearchParams({ date: S.date, h: S.h, stock: S.stock, crews: S.crews, tier: S.tier, lang: S.lang });
    if (S.ward) q.set("ward", S.ward);
    history.replaceState(null, "", "?" + q.toString());
    $("#backLink").href = "/?" + new URLSearchParams({ lang: S.lang }).toString();
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) {
      const ta = document.createElement("textarea"); ta.value = text; document.body.append(ta); ta.select();
      const ok = document.execCommand("copy"); ta.remove(); return ok;
    }
  }

  // ------------------------------------------------------------------ controls
  function buildControls() {
    $("#fWeek").innerHTML = S.dates.slice().reverse().map((d) => `<option value="${d}" ${d === S.date ? "selected" : ""}>${fmtDate(d)}</option>`).join("");
    $("#fH").innerHTML = [2, 3, 4].map((h) => `<button data-h="${h}" aria-pressed="${h === S.h}">${loc(h)} ${t("weeks")}</button>`).join("");
    $("#fStock").innerHTML = [100, 90, 80, 70, 60, 50, 40, 30].map((v) => `<option value="${v}" ${v === S.stock ? "selected" : ""}>${loc(v)}%</option>`).join("");
    $("#fCrews").innerHTML = [5, 10, 20, 30, 40, 60].map((v) => `<option value="${v}" ${v === S.crews ? "selected" : ""}>${loc(v)}</option>`).join("");
    $("#fTier").innerHTML = [["high", t("dpHighPlus")], ["crit", t("dpCritOnly")], ["all", t("dpAll")]]
      .map(([k, l]) => `<option value="${k}" ${k === S.tier ? "selected" : ""}>${l}</option>`).join("");
    $("#fWeek").onchange = (e) => { S.date = e.target.value; loadList(); };
    $$("#fH button").forEach((b) => b.onclick = () => { S.h = +b.dataset.h; buildControls(); loadList(); });
    $("#fStock").onchange = (e) => { S.stock = +e.target.value; loadList(); };
    $("#fCrews").onchange = (e) => { S.crews = +e.target.value; loadList(); };
    $("#fTier").onchange = (e) => { S.tier = e.target.value; loadList(); };
  }
  function applyLang() {
    document.documentElement.lang = S.lang;
    document.body.classList.toggle("lang-bn", S.lang === "bn");
    $$("[data-i]").forEach((el) => { const v = t(el.dataset.i); if (typeof v === "string") el.textContent = v; });
    $("#langEn").setAttribute("aria-pressed", S.lang === "en"); $("#langBn").setAttribute("aria-pressed", S.lang === "bn");
    document.title = `${t("dpTitle")} · Shotorko`;
    try { localStorage.setItem("sk-lang", S.lang); } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------------------------ ward list
  async function loadList() {
    const my = ++S.seqL;
    syncUrl();
    $("#wards").innerHTML = '<div class="skeleton" style="height:220px"></div>';
    try {
      const d = await api("/api/dispatch", { ...common(), min_tier: TIER_MIN[S.tier] });
      if (my !== S.seqL) return;
      S.list = d.notices;
      if (!S.list.some((n) => n.ward_id === S.ward)) S.ward = S.list.length ? S.list[0].ward_id : null;
      renderList(); loadDetail(); loadOutbox();
    } catch (e) { if (my === S.seqL) { $("#wards").innerHTML = `<p class="dp-empty">${esc(e.message || t("error"))}</p>`; } }
  }
  function wardName(id) { const [, , n] = id.split("_"); return `${t("ward")} ${loc(n)}`; }
  function renderList() {
    $("#listHead").textContent = t("dpWards")(loc(S.list.length));
    if (!S.list.length) { $("#wards").innerHTML = `<p class="dp-empty">${t("dpNone")}</p>`; $("#view").innerHTML = ""; return; }
    const lvl = { none: "✓", partial: "!", critical: "!!" };
    $("#wards").innerHTML = S.list.map((n) => `<button class="dp-ward" role="option" data-w="${n.ward_id}" aria-selected="${n.ward_id === S.ward}">
      <span class="rk">${loc(n.priority_rank)}</span>
      <span><b>${wardName(n.ward_id)} · ${esc(t("cities")[n.city] || n.city)}</b>
        <small><span class="dot" style="background:${HEX[n.tier]}"></span>${t("tiers")[n.tier]}</small></span>
      <span class="tags"><span class="def ${n.deficit_level}" title="${n.deficit_level}">${n.deficit_level === "none" ? lvl.none : (S.lang === "bn" ? "ঘাটতি" : "deficit")}</span>${smsTag(n.ward_id)}</span></button>`).join("");
    $$(".dp-ward").forEach((b) => b.onclick = () => { S.ward = b.dataset.w; $$(".dp-ward").forEach((x) => x.setAttribute("aria-selected", x === b)); syncUrl(); loadDetail(); });
  }

  // ------------------------------------------------------------------ one ward's notice
  async function loadDetail() {
    if (!S.ward) return;
    const my = ++S.seqD;
    try {
      const d = await api("/api/dispatch/" + S.ward, common());
      if (my !== S.seqD) return;
      S.detail = d; renderDetail();
      $("#stubNote").hidden = d.notice.source !== "stub";
    } catch (e) { if (my === S.seqD) $("#view").innerHTML = `<p class="dp-empty">${esc(e.message || t("error"))}</p>`; }
  }
  function renderDetail() {
    const d = S.detail, N = d.notice, ch = S.ch;
    const sup = N.supplies.map((s) => `<div class="${s.deficit ? "short" : ""}"><small>${esc(s.label)}</small>
      <b>${loc(s.allocated)}<span> / ${loc(s.required)}</span></b>${s.deficit ? `<em>${S.lang === "bn" ? "ঘাটতি" : "short"} ${loc(s.deficit)}</em>` : ""}</div>`).join("");
    let body;
    if (ch === "sms") body = `<div class="disp-msg sms">${esc(d.sms.text)}</div><div class="disp-meta">${t("segs")({ chars: loc(d.sms.chars), segs: loc(d.sms.segments) })}</div>`;
    else if (ch === "whatsapp") body = `<div class="disp-msg">${esc(d.whatsapp).replace(/\*([^*\n]+)\*/g, "<b>$1</b>")}</div>`;
    else body = `<div class="disp-frame"><iframe title="${t("chBrief")}" src="${d.brief_url}"></iframe></div>`;
    $("#view").innerHTML = `
      <div class="dp-view-head"><div><h2>${esc(N.city)} · ${esc(N.ward)}</h2>
        <div class="sub">${t("tiers")[N.tier]} · ~${N.cases} (${N.range}) · ${N.week} · ${t("dpRank")} #${loc(N.priority_rank)} · ${t("crewsLbl")}: ${loc(N.spray_teams)}</div></div>
        <span class="badge" style="border-color:${HEX[N.tier]};color:${HEX[N.tier]}">● ${t("tiers")[N.tier]}</span></div>
      <div class="dp-supplies">${sup}</div>
      <div class="seg disp-tabs" role="tablist" aria-label="${t("dispTitle")}">${[["sms", t("chSms")], ["whatsapp", t("chWa")], ["brief", t("chBrief")]].map(([k, l]) =>
        `<button role="tab" data-ch="${k}" aria-selected="${k === ch}" aria-pressed="${k === ch}">${l}</button>`).join("")}</div>
      ${body}
      <div class="disp-actions">${ch === "brief"
        ? `<a class="btn disp-primary" href="${d.brief_url}" target="_blank" rel="noopener">${t("openBrief")}</a>`
        : ch === "sms"
          ? `<button class="btn disp-primary" id="sendOne">${t("smsSend")}</button><button class="btn" id="copyOne">${t("copy")}</button>`
          : `<button class="btn disp-primary" id="copyOne">${t("copy")}</button>`}</div>
      ${ch === "sms" ? smsLog(N.ward_id) : ""}`;
    $$(".disp-tabs button").forEach((b) => b.onclick = () => { S.ch = b.dataset.ch; renderDetail(); });
    const c1 = $("#copyOne");
    const s1 = $("#sendOne");
    if (s1) s1.onclick = () => sendSms({ ward_id: N.ward_id }, 1, s1);
    if (c1) c1.onclick = async () => { await copy(ch === "sms" ? d.sms.text : d.whatsapp); c1.textContent = t("copied"); setTimeout(() => { c1.textContent = t("copy"); }, 1400); };
  }


  // ------------------------------------------------------------------ automatic SMS (Android phone gateway)
  const targetWeek = () => { const d = new Date(S.date + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + 7 * S.h); return d.toISOString().slice(0, 10); };
  function smsTag(wid) {
    const m = S.box[wid]; if (!m) return "";
    return `<span class="sms-st ${m.status}" title="${t("smsLast")}: ${esc(m.updated || "")}">SMS · ${t("smsSt")[m.status] || m.status}</span>`;
  }
  function smsLog(wid) {
    const m = S.box[wid]; if (!m) return "";
    const when = (m.updated || "").replace("T", " ").slice(0, 16);
    return `<div class="sms-log">${t("smsLast")}: <b>${t("smsSt")[m.status] || m.status}</b> · ${esc(m.phone || "")} · ${loc(when)}${m.error ? ` · ${esc(m.error)}` : ""}</div>`;
  }
  async function loadGateway() {
    try {
      S.gw = await api("/api/sms/status");
      const el = $("#gw"), g = S.gw;
      const cls = g.mode !== "android" ? "dry" : g.reachable ? "ok" : "down";
      el.className = "gw " + cls;
      $("span", el).textContent = cls === "dry" ? t("gwDry") : cls === "ok" ? t("gwOk") : t("gwDown");
      el.title = g.detail || "";
    } catch (e) { /* status is informative only */ }
  }
  async function loadOutbox() {
    clearTimeout(S.poll);
    try {
      const d = await api("/api/sms/outbox", { week: targetWeek(), limit: 500 });
      const latest = {};
      d.messages.forEach((m) => { if (!latest[m.ward_id]) latest[m.ward_id] = m; });   // newest first
      S.box = latest;
      $$(".dp-ward").forEach((b) => { const tags = $(".tags", b); if (tags) { $$(".sms-st", tags).forEach((x) => x.remove()); tags.insertAdjacentHTML("beforeend", smsTag(b.dataset.w)); } });
      if (S.detail && S.ch === "sms") { const old = $(".sms-log"); const html = smsLog(S.detail.notice.ward_id); if (old) old.outerHTML = html || "<span></span>"; else if (html) $("#view").insertAdjacentHTML("beforeend", html); }
      if (d.messages.some((m) => ["queued", "sending"].includes(m.status))) S.poll = setTimeout(loadOutbox, 3000);
    } catch (e) { S.poll = setTimeout(loadOutbox, 8000); }
  }
  async function sendSms(target, n, btn) {
    await loadGateway();
    const g = S.gw || { mode: "dry_run" };
    const to = g.override_to ? t("smsToOverride")(g.override_to) : t("smsToOfficers");
    if (!confirm(t("smsConfirm")({ n: loc(n), to, dry: g.mode !== "android" }))) return;
    if (btn) btn.disabled = true;
    try {
      const r = await fetch("/api/sms/send", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...target, date: S.date, h: S.h, stock_pct: S.stock, total_crews: S.crews, lang: S.lang }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      toast(t("smsDone")({ q: loc(j.queued), d: j.duplicates ? loc(j.duplicates) : 0, s: j.skipped ? loc(j.skipped) : 0 }));
      loadOutbox();
    } catch (e) { toast(e.message || t("error")); }
    finally { if (btn) btn.disabled = false; }
  }


  // ------------------------------------------------------------------ offline badge (Phase 2, Requirement 5)
  async function checkNet() {
    let offline = !navigator.onLine;
    try { const h = await fetch("/api/health", { cache: "no-store" }).then((r) => r.json()); offline = offline || !h.internet; } catch (e) { /* server is local; ignore */ }
    const b = document.getElementById("netBadge"); if (!b) return;
    b.hidden = !offline; b.title = window.I18N[document.documentElement.lang === "bn" ? "bn" : "en"].netOfflineTip;
  }
  addEventListener("online", checkNet); addEventListener("offline", checkNet);
  setInterval(checkNet, 30000); checkNet();

  // ------------------------------------------------------------------ boot
  async function boot() {
    applyLang();
    try { const meta = await api("/api/meta"); S.dates = meta.dates; if (!S.dates.includes(S.date)) S.date = meta.default_date; }
    catch (e) { toast(t("error")); return; }
    buildControls();
    $("#langEn").onclick = () => { S.lang = "en"; applyLang(); buildControls(); loadList(); };
    $("#langBn").onclick = () => { S.lang = "bn"; applyLang(); buildControls(); loadList(); };
    $("#copyAll").onclick = async () => {
      if (!S.list.length) return;
      const ok = await copy(S.list.map((n) => n.sms).join("\n\n"));
      if (ok) toast(t("copiedN")(loc(S.list.length)));
    };
    $("#sendRound").onclick = () => sendSms({ round: true, min_tier: TIER_MIN[S.tier] }, S.list.length, $("#sendRound"));
    loadGateway(); setInterval(loadGateway, 20000);
    loadList();
  }
  boot();
})();
