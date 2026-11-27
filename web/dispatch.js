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
    stock: +(url.get("stock") || 100), crews: +(url.get("crews") || 20), tier: ["all", "high", "crit"].includes(url.get("tier")) ? url.get("tier") : "high",
    ward: url.get("ward"), ch: "sms", filt: "all", book: { fallback: null, wards: {} }, editNum: false, list: [], detail: null, seqL: 0, seqD: 0, dates: [], gw: null, box: {}, poll: null,
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
  const segBtns = (el, items, cur, on) => {
    el.innerHTML = items.map(([k, l]) => `<button data-k="${k}" aria-pressed="${String(k) === String(cur)}">${l}</button>`).join("");
    $$("button", el).forEach((b) => b.onclick = () => on(b.dataset.k));
  };
  function buildControls() {
    $("#fWeek").innerHTML = S.dates.slice().reverse().map((d) => `<option value="${d}" ${d === S.date ? "selected" : ""}>${fmtDate(d)}</option>`).join("");
    segBtns($("#fH"), [2, 3, 4].map((h) => [h, `${loc(h)} ${t("weeks")}`]), S.h, (k) => { S.h = +k; buildControls(); loadList(); });
    segBtns($("#fTier"), [["crit", t("dxCrit")], ["high", t("dxHigh")], ["all", t("dxAll")]], S.tier, (k) => { S.tier = k; buildControls(); loadList(); });
    $("#fStock").innerHTML = [100, 90, 80, 70, 60, 50, 40, 30].map((v) => `<option value="${v}" ${v === S.stock ? "selected" : ""}>${loc(v)}%</option>`).join("");
    $("#fCrews").innerHTML = [5, 10, 20, 30, 40, 60].map((v) => `<option value="${v}" ${v === S.crews ? "selected" : ""}>${loc(v)}</option>`).join("");
    $("#assumeNow").textContent = t("dxAssumeNow")({ stock: loc(S.stock), crews: loc(S.crews) });
    $("#fWeek").onchange = (e) => { S.date = e.target.value; loadList(); };
    $("#fStock").onchange = (e) => { S.stock = +e.target.value; buildControls(); loadList(); };
    $("#fCrews").onchange = (e) => { S.crews = +e.target.value; buildControls(); loadList(); };
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
      S.box = {}; renderList(); loadDetail(); loadOutbox();
    } catch (e) { if (my === S.seqL) { $("#wards").innerHTML = `<p class="dp-empty">${esc(e.message || t("error"))}</p>`; } }
  }
  function wardName(id) { const [, , n] = id.split("_"); return `${t("ward")} ${loc(n)}`; }
  const isSent = (wid) => { const m = S.box[wid]; return !!m && ["queued", "sending", "sent", "delivered", "dry-run"].includes(m.status); };
  function statusChip(wid) {
    const m = S.box[wid];
    if (!m) return canReach(wid) ? `<span class="dx-st none">${t("dxNotSent")}</span>` : `<span class="dx-st nonum">${t("dxNoNum")}</span>`;
    const ok = ["sent", "delivered"].includes(m.status);
    if (isWaiting(m)) return `<span class="dx-st waiting">${t("smsWaiting")}</span>`;
    return `<span class="dx-st ${m.status}">${ok ? ICON_OK : ""}${t("smsSt")[m.status] || m.status}</span>`;
  }
  // handed to the phone service over a minute ago, but the phone has not picked it up yet
  const isWaiting = (m) => m.status === "sending" && !!m.provider_id && Date.now() - new Date(m.updated || m.created).getTime() > 60000;
  const clock = (iso) => loc(new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
  const ICON_OK = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
  const ICON_WARN = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>';
  const PHONE_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="2"/><path d="M11 18h2"/></svg>';
  const contact = (wid) => S.book.wards[wid] || { name: "", phone: "", has_phone: false };
  const canReach = (wid) => contact(wid).has_phone || !!S.book.fallback;
  async function loadContacts() {
    try { S.book = await api("/api/sms/contacts"); } catch (e) { /* keep the last copy */ }
  }
  function recipientHtml(wid) {
    const c = contact(wid);
    if (S.editNum) {
      return `<form class="dx-num-form" id="numForm" novalidate>
        <label><span>${t("dxName")}</span><input id="numName" autocomplete="off" maxlength="60" value="${esc(c.name)}"></label>
        <label><span>${t("dxPhone")}</span><input id="numPhone" inputmode="tel" autocomplete="off" placeholder="${t("dxPhonePh")}" required></label>
        <p class="dx-num-help">${t("dxNumHelp")}</p>
        <p class="dx-num-err" id="numErr" role="alert" hidden></p>
        <div class="dx-num-btns"><button class="btn disp-primary" type="submit">${t("dxSave")}</button>
          <button class="btn" type="button" id="numCancel">${t("dxCancel")}</button>
          ${c.has_phone ? `<button class="btn dx-num-rm" type="button" id="numRemove">${t("dxRemoveNum")}</button>` : ""}</div>
      </form>`;
    }
    const who = c.has_phone
      ? `<span class="dx-rec-ok">${PHONE_ICON}<b>${esc(c.name || t("dxOfficer"))}</b> ${esc(c.phone)}</span>`
      : `<span class="dx-rec-none">${PHONE_ICON}${t("dxRecNone")} ${S.book.fallback ? t("dxRecFallback")(esc(S.book.fallback)) : t("dxRecBlocked")}</span>`;
    return `<div class="dx-rec">${who}<button class="btn dx-rec-btn" type="button" id="numEdit">${c.has_phone ? t("dxChangeNum") : t("dxAddNum")}</button></div>`;
  }
  function wireRecipient(wid) {
    const ed = $("#numEdit"); if (ed) ed.onclick = () => { S.editNum = true; renderDetail(); const i = $("#numPhone"); if (i) i.focus(); };
    const cancel = $("#numCancel"); if (cancel) cancel.onclick = () => { S.editNum = false; renderDetail(); };
    const save = async (phone) => {
      const err = $("#numErr");
      try {
        const r = await fetch("/api/sms/contact", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ward_id: wid, name: $("#numName") ? $("#numName").value : "", phone }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || r.statusText);
        S.editNum = false; await loadContacts(); renderList(); renderDetail(); toast(phone ? t("dxSaved") : t("dxRemoved"));
      } catch (e) { if (err) { err.hidden = false; err.textContent = t("dxBadNum"); } }
    };
    const form = $("#numForm");
    if (form) form.onsubmit = (e) => { e.preventDefault(); const v = $("#numPhone").value.trim(); if (!v) { $("#numErr").hidden = false; $("#numErr").textContent = t("dxBadNum"); return; } save(v); };
    const rm = $("#numRemove"); if (rm) rm.onclick = () => save("");
  }
  function renderFilter() {
    const sent = S.list.filter((x) => isSent(x.ward_id)).length, n = S.list.length;
    const cnt = { all: n, todo: n - sent, done: sent };
    segBtns($("#fList"), Object.entries(t("dxFilt")).map(([k, l]) => [k, `${l} <small>${loc(cnt[k])}</small>`]), S.filt, (k) => { S.filt = k; renderList(); });
  }
  function renderProgress() {
    const n = S.list.length, sent = S.list.filter((x) => isSent(x.ward_id)).length, left = n - sent;
    $("#roundHead").textContent = t("dxRoundTitle")({ week: fmtDate(targetWeek()), n: loc(n) });
    $("#progSent").textContent = t("dxSentN")(loc(sent), loc(n));
    $("#progLeft").textContent = left ? t("dxLeft")(loc(left)) : "";
    const own = S.list.filter((x) => contact(x.ward_id).has_phone).length;
    $("#numCount").innerHTML = PHONE_ICON + t("dxNumCount")(loc(own), loc(n)) + (own < n ? " " + (S.book.fallback ? t("dxNumRestFb") : t("dxNumRestNone")) : "");
    $("#progBar").style.width = n ? (100 * sent / n).toFixed(1) + "%" : "0";
    $("#sendRoundTxt").textContent = left ? t("dxSendRest")(loc(left)) : t("dxAllSent");
    $("#sendRound").disabled = !left;
    $("#copyAll").disabled = !n;
  }
  function renderList() {
    renderProgress(); renderFilter();
    if (!S.list.length) { $("#wards").innerHTML = `<p class="dp-empty">${t("dpNone")}</p>`; $("#view").innerHTML = ""; return; }
    const rows = S.list.filter((n) => S.filt === "all" || (S.filt === "done") === isSent(n.ward_id));
    if (!rows.length) { $("#wards").innerHTML = `<p class="dp-empty">${t("dxEmptyFilt")}</p>`; return; }
    $("#wards").innerHTML = rows.map((n) => `<button class="dx-row" role="option" data-w="${n.ward_id}" aria-selected="${n.ward_id === S.ward}" style="--c:${HEX[n.tier]}">
      <span class="rk">${loc(n.priority_rank)}</span>
      <span class="who"><b>${wardName(n.ward_id)}</b><small>${esc(t("cities")[n.city] || n.city)}</small>${contact(n.ward_id).has_phone ? `<i class="dx-has-num" title="${t("dxHasNum")}">${PHONE_ICON}</i>` : ""}</span>
      <span class="dx-tier"><i></i>${t("tiers")[n.tier]}</span>
      <span class="dx-sup-st ${n.deficit_level !== "none" ? "short" : ""}">${n.deficit_level !== "none" ? ICON_WARN + t("dxShortW") : t("dxEnough")}</span>
      <span>${statusChip(n.ward_id)}</span></button>`).join("");
    $$(".dx-row").forEach((b) => b.onclick = () => { S.ward = b.dataset.w; $$(".dx-row").forEach((x) => x.setAttribute("aria-selected", x === b)); S.editNum = false; syncUrl(); loadDetail();
      if (matchMedia("(max-width:1100px)").matches) $("#view").scrollIntoView({ behavior: "smooth", block: "start" }); });
    keepSelectedVisible();
  }
  // Scroll only the ward list (not the page) so the chosen ward is in view.
  function keepSelectedVisible() {
    const box = $("#wards"), row = box.querySelector('.dx-row[aria-selected="true"]');
    if (!row) return;
    const top = row.offsetTop - box.offsetTop, bottom = top + row.offsetHeight;
    if (top < box.scrollTop) box.scrollTop = top;
    else if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight;
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
    const d = S.detail, N = d.notice, ch = S.ch, hx = HEX[N.tier];
    const sup = N.supplies.map((s) => {
      const pct = s.required ? Math.min(100, 100 * s.allocated / s.required) : 100;
      return `<div class="dx-sup ${s.deficit ? "short" : ""}"><small>${esc(s.label)}</small>
        <b>${loc(s.allocated)}<span> ${t("dxOf")} ${loc(s.required)}</span></b>
        <div class="dx-mini"><i style="width:${pct.toFixed(0)}%"></i></div>
        <em>${s.deficit ? t("dxShortBy")(loc(s.deficit)) : t("dxEnough")}</em></div>`;
    }).join("") + `<div class="dx-sup crew"><small>${t("crewsLbl")}</small><b>${loc(N.spray_teams)}</b><em class="muted">${t("dxCrewsSub")}</em></div>`;
    let body;
    if (ch === "sms") body = `<div class="dx-phone"><div class="disp-msg sms">${esc(d.sms.text)}</div>
      <div class="disp-meta">${t("segs")({ chars: loc(d.sms.chars), segs: loc(d.sms.segments) })}</div></div>`;
    else if (ch === "whatsapp") body = `<div class="disp-msg">${esc(d.whatsapp).replace(/\*([^*\n]+)\*/g, "<b>$1</b>")}</div>`;
    else body = `<div class="disp-frame"><iframe title="${t("chBrief")}" src="${d.brief_url}"></iframe></div>`;
    $("#view").innerHTML = `
      <div class="dx-vhead" style="--c:${hx}">
        <h2>${esc(N.ward)} <span>${esc(N.city)}</span></h2>
        <span class="dx-badge">${t("tiers")[N.tier]}</span>
      </div>
      <p class="dx-line">${t("dxLine")({ cases: `<b>~${loc(N.cases)}</b>`, week: esc(N.week), range: loc(N.range) })} <span class="muted">${t("dxPrio")(loc(N.priority_rank), loc(S.list.length))}</span></p>
      <h3 class="dx-h3">${t("dxMsgTitle")}</h3>
      <div class="seg disp-tabs" role="tablist">${[["sms", t("chSms")], ["whatsapp", t("chWa")], ["brief", t("chBrief")]].map(([k, l]) =>
        `<button role="tab" data-ch="${k}" aria-selected="${k === ch}" aria-pressed="${k === ch}">${l}</button>`).join("")}</div>
      <p class="dx-hint">${t("dxChHint")[ch]}</p>
      ${ch === "sms" ? `<div class="dx-recip"><h4>${t("dxRecTitle")}</h4>${recipientHtml(N.ward_id)}</div>` : ""}
      ${body}
      <div class="disp-actions">${ch === "brief"
        ? `<a class="btn disp-primary" href="${d.brief_url}" target="_blank" rel="noopener">${t("openBrief")}</a>`
        : ch === "sms"
          ? `<button class="btn disp-primary" id="sendOne" ${canReach(N.ward_id) ? "" : "disabled"}>${isSent(N.ward_id) ? t("dxResend") : t("smsSend")}</button><button class="btn" id="copyOne">${t("copy")}</button>`
          : `<button class="btn disp-primary" id="copyOne">${t("copy")}</button>`}</div>
      ${ch === "sms" ? smsLog(N.ward_id) : ""}
      <h3 class="dx-h3">${t("dxSupTitle")}</h3>
      <div class="dx-sups">${sup}</div>`;
    $$(".disp-tabs button").forEach((b) => b.onclick = () => { S.ch = b.dataset.ch; S.editNum = false; renderDetail(); });
    if (ch === "sms") wireRecipient(N.ward_id);
    const c1 = $("#copyOne");
    const s1 = $("#sendOne");
    if (s1) s1.onclick = () => sendSms({ ward_id: N.ward_id }, 1, s1);
    if (c1) c1.onclick = async () => { await copy(ch === "sms" ? d.sms.text : d.whatsapp); c1.textContent = t("copied"); setTimeout(() => { c1.textContent = t("copy"); }, 1400); };
  }


  // ------------------------------------------------------------------ automatic SMS (Android phone gateway)
  const targetWeek = () => { const d = new Date(S.date + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + 7 * S.h); return d.toISOString().slice(0, 10); };
  function smsLog(wid) {
    const m = S.box[wid];
    if (!m) return `<div class="sms-log">${t("dxNever")}</div>`;
    const when = (m.updated || "").replace("T", " ").slice(0, 16);
    return `<div class="sms-log">${statusChip(wid)} ${esc(m.phone || "")} · ${loc(when)}${m.route ? ` · ${(t("smsRoute") || {})[m.route] || m.route}` : ""}${m.error ? ` · ${esc(m.error)}` : ""}</div>${isWaiting(m) ? `<p class="dx-hint dx-wait">${t("smsWaitHint")}</p>` : ""}`;
  }
  async function loadGateway() {
    try {
      S.gw = await api("/api/sms/status");
      const el = $("#gw"), g = S.gw;
      const idle = g.reachable && g.last_seen && Date.now() - new Date(g.last_seen).getTime() > 10 * 60000;
      const cls = g.mode !== "android" ? "dry" : !g.reachable ? "down" : idle ? "idle" : "ok";
      el.className = "gw " + cls;
      let txt = t("dxGw")[cls];
      if (cls === "ok" && g.route) txt += " · " + ((t("smsRoute") || {})[g.route] || g.route);
      if (g.last_seen && cls !== "dry") txt += " · " + t("gwSeen")(clock(g.last_seen));
      $("span", el).textContent = txt;
      el.title = idle ? t("gwIdleTip") : (g.detail || "");
    } catch (e) { /* status is informative only */ }
  }
  async function loadOutbox() {
    clearTimeout(S.poll);
    try {
      const d = await api("/api/sms/outbox", { week: targetWeek(), limit: 500 });
      const latest = {};
      d.messages.forEach((m) => { if (!latest[m.ward_id]) latest[m.ward_id] = m; });   // newest first
      S.box = latest;
      renderList();
      if (S.detail && S.ch === "sms") renderDetail();
      if (d.messages.some((m) => ["queued", "sending"].includes(m.status))) S.poll = setTimeout(loadOutbox, 3000);
    } catch (e) { S.poll = setTimeout(loadOutbox, 8000); }
  }
  async function sendSms(target, n, btn) {
    if (!S.gw) await loadGateway(); else loadGateway();   // use the last known state; refresh it in the background
    const g = S.gw || { mode: "dry_run" };
    await loadContacts();
    const ids = target.ward_id ? [target.ward_id] : S.list.filter((x) => !isSent(x.ward_id)).map((x) => x.ward_id);
    const own = ids.filter((w) => contact(w).has_phone).length, fb = S.book.fallback ? ids.length - own : 0, none = ids.length - own - fb;
    if (!confirm(t("dxConfirm")({ n: loc(ids.length), own: loc(own), fb: loc(fb), fbNum: S.book.fallback || "", none: loc(none), dry: g.mode !== "android" }))) return;
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
    $("#sendRound").onclick = () => sendSms({ round: true, min_tier: TIER_MIN[S.tier] }, S.list.filter((x) => !isSent(x.ward_id)).length, $("#sendRound"));
    loadGateway(); setInterval(loadGateway, 20000);
    await loadContacts();
    loadList();
  }
  boot();
})();
