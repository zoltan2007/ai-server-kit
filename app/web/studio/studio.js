"use strict";
// Kiki Cloude：像 Claude 程式介面的對話頁。純 JavaScript、不載入外部檔案；對話存在主機（/api/sessions）。

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ls = {
  get(k, d) { try { const v = localStorage.getItem("studio." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("studio." + k, JSON.stringify(v)); } catch { /* 私密模式等，忽略 */ } },
};
const GiB = 1024 ** 3;
const gib = b => b == null ? "—" : (b / GiB).toFixed(b / GiB >= 100 ? 0 : 1) + " GB";
const pct = (a, b) => b ? Math.max(0, Math.min(100, a / b * 100)) : 0;
const heat = p => p >= 95 ? "crit" : p >= 80 ? "hot" : "";
const later = typeof requestAnimationFrame === "function" ? requestAnimationFrame : f => setTimeout(f, 16);

let TOKEN = ls.get("token", "") || (() => { try { return JSON.parse(localStorage.getItem("aisk.token") || '""'); } catch { return ""; } })();
const S = { status: null, models: [], caps: {}, sessions: [], cur: null, busy: false, ctrl: null, attach: [], saveTimer: 0, me: null, listMode: "sessions" };

// ── 連線 ──
async function api(path, opt = {}) {
  const init = { method: opt.body !== undefined ? "POST" : "GET", headers: { Authorization: "Bearer " + TOKEN }, signal: opt.signal };
  if (opt.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opt.body); }
  const r = await fetch(path, init);
  if (r.status === 401) { showLogin("登入已失效，請重新登入"); throw new Error("請先登入"); }
  if (opt.raw && r.ok) return r;
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "HTTP " + r.status);
  return j;
}
async function* lines(resp) {
  const rd = resp.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await rd.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const l = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (l) { try { yield JSON.parse(l); } catch { /* 半截 */ } }
    }
  }
  if (buf.trim()) { try { yield JSON.parse(buf); } catch { /* 忽略 */ } }
}
let toastT = 0;
function toast(msg, bad) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => (t.hidden = true), bad ? 5000 : 2500);
}
async function copyText(t) { try { await navigator.clipboard.writeText(t); toast("已複製"); } catch { toast("複製失敗", true); } }
function autosize(ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, innerHeight * 0.4) + "px"; }

// ── 登入／申請／建管理員 ──
const gate = { mode: "user" };
function showLogin(msg) {
  $("#app").hidden = true; $("#login").hidden = false;
  showCard("login-form"); $("#login-err").textContent = msg || "";
  setTimeout(() => $(gate.mode === "token" ? "#login-token" : "#login-user").focus(), 50);
}
function showCard(id) { for (const f of $$("#login form")) f.hidden = f.id !== id; }
function setMode(mode) {
  gate.mode = mode;
  $("#login-user-fields").hidden = mode === "token"; $("#login-token").hidden = mode !== "token";
  $("#to-token").textContent = mode === "token" ? "用帳號密碼" : "用管理員登入碼";
  $("#login-tip").textContent = mode === "token" ? "安裝時顯示的登入碼；忘記的話在主機上執行「顯示登入碼」。" : "用你的帳號登入；還沒有帳號可以向管理員申請。";
}
async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "HTTP " + r.status);
  return j;
}
$("#to-token").addEventListener("click", () => { setMode(gate.mode === "token" ? "user" : "token"); $("#login-err").textContent = ""; });
$("#to-apply").addEventListener("click", () => { showCard("apply-form"); $("#apply-user").focus(); });
$("#to-login").addEventListener("click", () => showLogin());
$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  const body = gate.mode === "token" ? { token: $("#login-token").value.trim() }
    : { user: $("#login-user").value.trim(), password: $("#login-pass").value };
  try {
    const r = await post("/api/login", body);
    TOKEN = r.token; ls.set("token", r.token);
    $("#login-token").value = ""; $("#login-pass").value = "";
    if (r.master && r.needs_setup) { showCard("setup-form"); $("#setup-user").focus(); return; }
    boot();
  } catch (err) { $("#login-err").textContent = err.message; }
});
$("#apply-form").addEventListener("submit", async e => {
  e.preventDefault();
  if ($("#apply-pass").value !== $("#apply-pass2").value) { $("#apply-err").textContent = "兩次密碼不一樣"; return; }
  try {
    const r = await post("/api/apply", { user: $("#apply-user").value.trim(), email: $("#apply-email").value.trim(), password: $("#apply-pass").value, note: $("#apply-note").value.trim() });
    gate.applyUser = $("#apply-user").value.trim().toLowerCase();
    $("#apply-form").reset();
    if (r.status === "verify") { showCard("verify-form"); $("#verify-code").focus(); }
    else showLogin("申請已送出，等管理員核准後就能用這個帳號登入");
  } catch (err) { $("#apply-err").textContent = err.message; }
});
$("#verify-form").addEventListener("submit", async e => {
  e.preventDefault();
  try { await post("/api/apply/verify", { user: gate.applyUser, code: $("#verify-code").value.trim() }); $("#verify-form").reset(); showLogin("信箱驗證完成，等管理員核准後就能登入"); }
  catch (err) { $("#verify-err").textContent = err.message; }
});
$("#verify-resend").addEventListener("click", async () => { try { await post("/api/apply/resend", { user: gate.applyUser }); $("#verify-err").textContent = ""; $("#verify-tip").textContent = "已重寄，看一下信箱（含垃圾信）。"; } catch (err) { $("#verify-err").textContent = err.message; } });
$("#verify-back").addEventListener("click", () => showLogin());
$("#setup-form").addEventListener("submit", async e => {
  e.preventDefault();
  try {
    await api("/api/users/setup", { body: { user: $("#setup-user").value.trim(), password: $("#setup-pass").value } });
    const r = await post("/api/login", { user: $("#setup-user").value.trim(), password: $("#setup-pass").value });
    TOKEN = r.token; ls.set("token", r.token); $("#setup-form").reset(); boot();
  } catch (err) { $("#setup-err").textContent = err.message; }
});
$("#setup-skip").addEventListener("click", () => boot());
$("#logout-btn").addEventListener("click", async () => { try { await api("/api/logout", { body: {} }); } catch { /* 憑證可能已失效 */ } TOKEN = ""; ls.set("token", ""); showLogin(); });

// ── 帳號管理（管理員） ──
async function loadMe() {
  const me = await api("/api/me");
  S.me = me;
  $("#who").textContent = (me.role === "admin" ? "管理員 " : "") + me.name;
  const q = me.quota || {}, used = me.used || {};
  $("#quota-line").hidden = !(q.day || q.week);
  $("#quota-line").textContent = [q.day ? `今天 ${fmtTok(used.day || 0)} / ${fmtTok(q.day)}` : "", q.week ? `本週 ${fmtTok(used.week || 0)} / ${fmtTok(q.week)}` : ""].filter(Boolean).join(" · ");
  $("#users-btn").hidden = me.role !== "admin"; $("#panel-link").hidden = me.role !== "admin"; $("#rt-members").hidden = me.role !== "admin"; $("#dash-btn").hidden = me.role !== "admin"; $("#mail-btn").hidden = me.role !== "admin";
  $("#pw-details").hidden = !!me.master;
  $("#users-badge").hidden = !me.pending; $("#users-badge").textContent = me.pending || 0;
  if (me.pending) toast(`有 ${me.pending} 個帳號申請等你核准`);
}
async function renderUsers(target = "#users-list") {
  const r = await api("/api/users");
  const zh = { pending: "等核准", active: "可用", disabled: "已停用" };
  const act = (a, u, label, cls = "") => `<button class="btn sm ${cls}" data-act="${a}" data-u="${esc(u)}">${label}</button>`;
  $(target).innerHTML = r.users.map(u => {
    const acts = u.status === "pending" ? act("approve", u.name, "核准", "primary") + act("reject", u.name, "拒絕")
      : u.status === "active" ? act("disable", u.name, "停用") + (u.role !== "admin" ? act("admin", u.name, "設為管理員") : "")
      : act("enable", u.name, "啟用") + act("reject", u.name, "刪除");
    return `<div class="user"><span class="n">${esc(u.name)}</span><span class="s ${u.status}">${zh[u.status] || u.status}${u.role === "admin" ? " · 管理員" : ""}${u.email ? ` · ${esc(u.email)}${u.verified ? " ✓" : "（未驗證）"}` : ""}</span>
      <span class="acts">${acts}${act("reset", u.name, "重設密碼")}${act("quota", u.name, "用量上限")}</span>${u.note ? `<span class="note">${esc(u.note)}</span>` : ""}${(u.quota?.day || u.quota?.week) ? `<span class="note">上限：${u.quota.day ? "每天 " + fmtTok(u.quota.day) : ""}${u.quota.day && u.quota.week ? " · " : ""}${u.quota.week ? "每週 " + fmtTok(u.quota.week) : ""}</span>` : ""}</div>`;
  }).join("") || `<div class="muted small">還沒有任何帳號申請</div>`;
}
$("#users-btn").addEventListener("click", async () => { $("#users-modal").hidden = false; try { await renderUsers(); } catch (err) { toast(err.message, true); } });
$("#users-close").addEventListener("click", () => ($("#users-modal").hidden = true));
$("#users-modal").addEventListener("click", async e => {
  if (e.target === e.currentTarget) { $("#users-modal").hidden = true; return; }
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const { act, u } = b.dataset, body = { user: u };
  if (act === "reject" && !confirm(`刪除帳號 ${u}？`)) return;
  if (act === "reset") { const pw = prompt(`${u} 的新密碼（至少 8 個字）`); if (!pw) return; body.password = pw; }
  if (act === "quota") {
    const d = prompt(`${u} 每天的 token 上限（千，0 = 不限）`, "0"); if (d == null) return;
    const w = prompt(`${u} 每週的 token 上限（千，0 = 不限）`, "0"); if (w == null) return;
    body.day = Math.round(+d * 1000) || 0; body.week = Math.round(+w * 1000) || 0;
  }
  try { await api("/api/users/" + act, { body }); toast("完成"); await renderUsers(); await loadMe(); }
  catch (err) { toast(err.message, true); }
});
$("#pw-form").addEventListener("submit", async e => {
  e.preventDefault();
  try { await api("/api/password", { body: { old: $("#pw-old").value, password: $("#pw-new").value } }); $("#pw-form").reset(); toast("密碼改好了，請重新登入"); showLogin(); }
  catch (err) { toast(err.message, true); }
});

// ── 版面開關 ──
const app = $("#app");
const wide = () => matchMedia("(min-width: 900px)").matches;
function openSide(on) { app.classList.toggle(wide() ? "side-off" : "side-on", wide() ? !on : on); if (!wide() && on) app.classList.remove("right-on"); }
function openRight(on) { app.classList.toggle("right-on", on); $("#right-open").classList.toggle("on", on); if (!wide() && on) app.classList.remove("side-on"); ls.set("right", on); }
$("#side-open").addEventListener("click", () => openSide(true));
$("#side-close").addEventListener("click", () => openSide(false));
$("#right-open").addEventListener("click", () => openRight(!app.classList.contains("right-on")));
$("#right-close").addEventListener("click", () => openRight(false));
$("#scrim").addEventListener("click", () => { app.classList.remove("side-on", "right-on"); $("#right-open").classList.remove("on"); });
$("#theme-btn").addEventListener("click", () => {
  $("#settings-pop").hidden = true;
  const light = document.documentElement.dataset.theme !== "light";
  document.documentElement.dataset.theme = light ? "light" : "dark"; ls.set("theme", light ? "light" : "dark");
});
if (ls.get("theme", "dark") === "light") document.documentElement.dataset.theme = "light";

// ── 主機狀態（右欄＋左下角一行） ──
function renderStatus(s) {
  const dot = $("#host-line .dot");
  dot.className = "dot " + (s.ollama?.up ? "ok" : "bad");
  const g = s.gpus?.[0];
  $("#host-name").textContent = `${s.hostname || "主機"} · ${g ? `GPU ${g.temp ?? "—"}°C` : "沒有顯示卡"}`;
  const box = $("#status");
  const bar = (used, total, label) => `<div class="meter"><i class="${heat(pct(used, total))}" style="width:${pct(used, total)}%"></i></div><div class="kv"><span>${label}</span><span>${gib(used)} / ${gib(total)}</span></div>`;
  let h = `<div class="stat"><h4>${esc(s.hostname || "主機")}</h4><div class="v">${esc(s.os || "")}</div><div class="kv"><span>CPU ${s.cpu ?? "—"}%</span><span>開機 ${dur(s.uptime)}</span></div>${s.mem ? bar(s.mem.used, s.mem.total, "記憶體") : ""}</div>`;
  for (const gp of s.gpus || []) {
    const t = gp.temp ?? 0;
    h += `<div class="stat"><h4>${esc(gp.name)}</h4><div class="kv"><span>溫度 <b style="color:${t >= 85 ? "var(--danger)" : t >= 75 ? "var(--warn)" : "inherit"}">${gp.temp ?? "—"}°C</b></span><span>${gp.power ?? "—"} W · ${gp.util ?? "—"}%</span></div>${bar(gp.mem_used, gp.mem_total, "顯示記憶體")}</div>`;
  }
  const loaded = s.ollama?.loaded || [];
  h += `<div class="stat"><h4>Ollama ${s.ollama?.up ? "執行中" : "沒連上"}</h4>${loaded.length ? loaded.map(m => `<div class="kv"><span>${esc(m.name)}</span><span>${gib(m.size)}</span></div>`).join("") : `<div class="muted small">沒有載入中的模型</div>`}</div>`;
  for (const d of s.disks || []) h += `<div class="stat">${bar(d.used, d.total, "磁碟 " + esc(d.mount))}</div>`;
  box.innerHTML = h;
}
function dur(sec) {
  if (sec == null) return "—";
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  return d ? `${d} 天 ${h} 小時` : h ? `${h} 小時 ${m} 分` : `${m} 分`;
}
async function pollStatus() {
  try { S.status = await api("/api/status"); renderStatus(S.status); }
  catch (err) { $("#host-name").textContent = err.message; }
}
function statusForAI() {
  const s = S.status;
  if (!s) return "";
  const lite = {
    hostname: s.hostname, os: s.os, cpu_percent: s.cpu, uptime_sec: s.uptime,
    memory: s.mem && { used_gb: +(s.mem.used / GiB).toFixed(1), total_gb: +(s.mem.total / GiB).toFixed(1) },
    gpus: (s.gpus || []).map(g => ({ name: g.name, util: g.util, vram_used_gb: +(g.mem_used / GiB).toFixed(1), vram_total_gb: +(g.mem_total / GiB).toFixed(1), temp_c: g.temp, power_w: g.power })),
    disks: (s.disks || []).map(d => ({ mount: d.mount, free_gb: +((d.total - d.used) / GiB).toFixed(0), total_gb: +(d.total / GiB).toFixed(0) })),
    ollama_loaded: (s.ollama?.loaded || []).map(m => m.name),
  };
  return "以下是這台主機現在的狀態（JSON），使用者問到機器的事情時參考：\n" + JSON.stringify(lite);
}

// ── 模型 ──
const isEmbed = n => /embed|bge|e5-|minilm/i.test(n);
async function loadModels() {
  try { S.models = ((await api("/api/models")).installed || []).map(m => m.name).filter(n => !isEmbed(n)); }
  catch (err) { toast(err.message, true); S.models = []; }
  if (S.cur && !S.cur.model && S.models.length) S.cur.model = ls.get("model", "") && S.models.includes(ls.get("model", "")) ? ls.get("model", "") : S.models[0];
  renderModelChip();
}
function renderModelChip() {
  const m = S.cur?.model || "";
  $("#model-name").textContent = m || (S.models.length ? "選模型" : "還沒有模型");
  $("#model-list").innerHTML = S.models.length
    ? S.models.map(n => `<button data-model="${esc(n)}" class="${n === m ? "on" : ""}"><span>${esc(n)}</span>${n === m ? '<span class="muted">目前</span>' : ""}</button>`).join("")
    : `<div class="muted small" style="padding:8px">還沒有模型，先到面板下載</div>`;
  if (typeof renderPartnerChip === "function" && S.cur) renderPartnerChip();
  updateCaps();
}
async function updateCaps() {
  const name = S.cur?.model;
  if (!name) { $("#caps").textContent = ""; $("#attach-btn").hidden = true; return; }
  if (!S.caps[name]) { try { S.caps[name] = await api("/api/models/show?name=" + encodeURIComponent(name)); } catch { S.caps[name] = { capabilities: [] }; } }
  const caps = S.caps[name].capabilities || [];
  $("#attach-btn").hidden = !caps.includes("vision");
  $("#think").disabled = !caps.includes("thinking");
  const zh = { completion: "對話", vision: "看圖", tools: "工具", thinking: "思考", embedding: "嵌入", insert: "補全" };
  $("#caps").textContent = caps.map(c => zh[c] || c).join("、");
}
$("#model-chip").addEventListener("click", e => {
  const p = $("#model-pop"), r = e.currentTarget.getBoundingClientRect();
  p.hidden = !p.hidden;
  p.style.top = Math.max(8, r.top - 8) + "px"; p.style.transform = "translateY(-100%)"; p.style.left = Math.max(8, Math.min(r.left, innerWidth - 370)) + "px";
});
$("#model-list").addEventListener("click", e => {
  const b = e.target.closest("[data-model]");
  if (!b) return;
  if (!S.cur) { $("#model-pop").hidden = true; return; }
  S.cur.model = b.dataset.model; ls.set("model", b.dataset.model); $("#model-pop").hidden = true;
  renderModelChip(); queueSave();
});
document.addEventListener("click", e => { if (!e.target.closest("#model-pop, #model-chip")) $("#model-pop").hidden = true; });

// ── 對話清單（群組 ▸ 對話 ▸ 分支） ──
function setListMode(m) { S.listMode = m; $("#rooms-btn").classList.toggle("on", m === "rooms"); }
$("#rooms-btn").addEventListener("click", () => { setListMode(S.listMode === "rooms" ? "sessions" : "rooms"); renderList(); });
const H = { list: [], i: -1 };   // 開過的對話，給上一個／下一個用
function remember(id) {
  if (!id || H.list[H.i] === id) return;
  H.list = H.list.slice(0, H.i + 1).concat(id); H.i = H.list.length - 1; histBtns();
}
function histBtns() { $("#hist-back").disabled = H.i <= 0; $("#hist-fwd").disabled = H.i >= H.list.length - 1; }
async function goHist(d) {
  const j = H.i + d;
  if (j < 0 || j >= H.list.length || S.busy) return;
  H.i = j; await openSession(H.list[j], false); histBtns();
}
$("#hist-back").addEventListener("click", () => goHist(-1));
$("#hist-fwd").addEventListener("click", () => goHist(1));

function newSession(extra = {}) {
  if (typeof leaveRoomView === "function") leaveRoomView();
  setListMode("sessions");
  S.cur = Object.assign({ id: "", title: "", model: ls.get("model", "") || S.models[0] || "", pinned: false, group: "", parent: "",
    messages: [], settings: { think: true, status: false } }, extra);
  renderCur(); renderList();
  if (typeof loadHeat === "function") loadHeat();
  if (typeof renderRecent === "function") renderRecent();
  $("#input").focus();
}
async function openSession(id, track = true) {
  if (typeof leaveRoomView === "function") leaveRoomView();
  setListMode("sessions");
  try { S.cur = await api("/api/sessions/get?id=" + id); }
  catch (err) { toast(err.message, true); return; }
  S.cur.settings = S.cur.settings || {};
  if (track) remember(id);
  renderCur(); renderList();
}
async function loadList() {
  try { S.sessions = (await api("/api/sessions")).sessions; } catch (err) { toast(err.message, true); }
  renderList();
}
const open = ls.get("open", {});                 // 群組、父對話是否展開
function isOpen(key, d = true) { return key in open ? open[key] : d; }
function toggleOpen(key, d = true) { open[key] = !isOpen(key, d); ls.set("open", open); renderList(); }
function sessRow(s, kids) {
  const cur = S.cur?.id === s.id;
  const caret = kids.length ? `<span class="caret" title="${kids.length} 個分支對話">${isOpen("s:" + s.id, false) ? "▼" : "▶"}</span>` : "";
  return `<button class="sess ${cur ? "on" : ""}" data-id="${s.id}" data-kids="${kids.length}"><span class="t">${esc(s.title || "（沒有標題）")}</span>${caret}<span class="grow"></span>${s.pinned ? '<span class="pin">●</span>' : ""}</button>`;
}
function renderTree(list, roots) {
  let h = "";
  for (const s of roots) {
    const kids = list.filter(k => k.parent === s.id);
    h += sessRow(s, kids);
    if (!kids.length) continue;
    if (isOpen("s:" + s.id, false)) h += `<div class="tree">${renderTree(list, kids)}</div>`;   // 收起時只留標題旁的箭頭
  }
  return h;
}
function renderList() {
  const q = $("#search").value.trim().toLowerCase();
  const all = S.sessions;
  const list = q ? all.filter(s => (s.title || "").toLowerCase().includes(q) || (s.model || "").toLowerCase().includes(q)) : all;
  const ids = new Set(list.map(s => s.id));
  const roots = list.filter(s => !s.parent || !ids.has(s.parent));
  const groups = [...new Set(all.map(s => s.group).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-Hant"));
  let h = "";
  for (const g of groups) {
    const mine = roots.filter(s => s.group === g);
    if (q && !mine.length) continue;
    const on = isOpen("g:" + g);
    h += `<button class="grp ${on ? "open" : ""}" data-open="g:${esc(g)}"><span>${esc(g)}</span><span class="caret">▶</span></button>`;
    if (on) h += `<div class="tree">${renderTree(list, mine)}</div>`;
  }
  h += renderTree(list, roots.filter(s => !s.group));
  if (S.listMode === "rooms" && typeof renderRooms === "function") h = renderRooms();
  const box = $("#sess-list"), keepTop = box.scrollTop;          // 重畫清單時捲動位置不要跳
  box.querySelector(":scope > .liquid")?.remove();
  box.innerHTML = h || `<div class="muted small" style="padding:12px">還沒有對話</div>`;
  box.scrollTop = keepTop;
}
$("#search-btn").addEventListener("click", () => { const b = $("#search"); b.hidden = !b.hidden; if (!b.hidden) b.focus(); else { b.value = ""; renderList(); } });
$("#search").addEventListener("input", renderList);
$("#sess-list").addEventListener("click", async e => {
  const o = e.target.closest("[data-open]");
  if (o) { toggleOpen(o.dataset.open, o.dataset.open.startsWith("g:")); return; }
  const b = e.target.closest("[data-id]");
  if (!b || S.busy) return;
  if (e.target.closest(".caret")) { toggleOpen("s:" + b.dataset.id, false); return; }
  await openSession(b.dataset.id);
  if (!wide()) openSide(false);
});
// 右鍵（手機長按）選單
let ctxId = "";
function showCtx(x, y, id) {
  ctxId = id; const p = $("#ctx"); p.hidden = false;
  p.style.left = Math.min(x, innerWidth - 190) + "px"; p.style.top = Math.max(8, Math.min(y, innerHeight - 270)) + "px";
}
$("#sess-list").addEventListener("contextmenu", e => { const b = e.target.closest("[data-id]"); if (b) { e.preventDefault(); showCtx(e.clientX, e.clientY, b.dataset.id); } });
let pressT = 0;
$("#sess-list").addEventListener("touchstart", e => { const b = e.target.closest("[data-id]"); if (b) pressT = setTimeout(() => showCtx(e.touches[0].clientX, e.touches[0].clientY, b.dataset.id), 550); }, { passive: true });
$("#sess-list").addEventListener("touchend", () => clearTimeout(pressT));
$("#sess-list").addEventListener("touchmove", () => clearTimeout(pressT));
document.addEventListener("keydown", e => { if (e.key === "Escape") { $("#ctx").hidden = true; $("#settings-pop").hidden = true; $("#model-pop").hidden = true; } });
document.addEventListener("click", e => { if (!e.target.closest("#ctx")) $("#ctx").hidden = true; if (!e.target.closest("#settings-pop, #settings-btn")) $("#settings-pop").hidden = true; });
$("#ctx").addEventListener("click", async e => {
  const b = e.target.closest("[data-c]");
  if (!b) return;
  $("#ctx").hidden = true;
  const row = S.sessions.find(s => s.id === ctxId);
  if (!row) return;
  const c = b.dataset.c;
  if (c === "open") return openSession(ctxId);
  if (c === "fork") return forkSession(ctxId);
  if (c === "delete") { if (!confirm(`刪除「${row.title}」？`)) return; await deleteSession(ctxId); return; }
  const full = await api("/api/sessions/get?id=" + ctxId).catch(err => { toast(err.message, true); });
  if (!full) return;
  if (c === "pin") full.pinned = !full.pinned;
  if (c === "rename") { const t = prompt("新名稱", full.title); if (t == null) return; full.title = t.trim() || full.title; }
  if (c === "group") { const g = prompt("群組名稱（留空＝不分群組）", full.group || ""); if (g == null) return; full.group = g.trim(); }
  await saveSession(full);
  if (S.cur?.id === ctxId) { S.cur = full; renderCur(); }
  renderList();
});
async function forkSession(id) {
  const src = await api("/api/sessions/get?id=" + id).catch(err => { toast(err.message, true); });
  if (!src) return;
  newSession({ title: (src.title || "對話") + "（分支）", model: src.model, group: src.group || "", parent: id,
    messages: src.messages || [], settings: src.settings || {} });
  open["s:" + id] = true; ls.set("open", open);
  await saveNow(); renderList(); toast("已分支出新對話");
}
async function deleteSession(id) {
  try { await api("/api/sessions/delete", { body: { id } }); } catch (err) { toast(err.message, true); return; }
  S.sessions = S.sessions.filter(s => s.id !== id);
  for (const k of S.sessions.filter(s => s.parent === id)) k.parent = "";
  if (S.cur?.id === id) newSession(); else renderList();
}
async function saveSession(c) {
  const r = await api("/api/sessions/save", { body: { id: c.id, title: c.title, model: c.model, pinned: c.pinned, group: c.group, parent: c.parent,
    partner: c.partner || null, settings: c.settings, messages: c.messages } });
  const i = S.sessions.findIndex(s => s.id === r.id);
  const row = { id: r.id, title: r.title, model: r.model, updated: r.updated, pinned: r.pinned, group: r.group, parent: r.parent, count: r.messages.length };
  if (i >= 0) S.sessions[i] = row; else S.sessions.unshift(row);
  S.sessions.sort((a, b) => (b.pinned - a.pinned) || (b.updated - a.updated));
  return r;
}
$("#new-btn").addEventListener("click", () => { if (!S.busy) { newSession(); if (!wide()) openSide(false); } });
$("#models-btn").addEventListener("click", e => { e.stopPropagation(); $("#model-chip").click(); });
$("#right-open2").addEventListener("click", () => openRight(!app.classList.contains("right-on")));
$("#settings-btn").addEventListener("click", e => {
  const p = $("#settings-pop"), r = e.currentTarget.getBoundingClientRect();
  p.hidden = !p.hidden; p.style.top = Math.max(8, r.top - 8) + "px"; p.style.transform = "translateY(-100%)"; p.style.left = Math.max(8, Math.min(r.left - 150, innerWidth - 210)) + "px";
});
$("#pw-btn").addEventListener("click", () => { $("#settings-pop").hidden = true; $("#users-modal").hidden = false; $("#pw-details").open = true; $("#users-list").innerHTML = ""; if (S.me?.role === "admin") renderUsers().catch(() => {}); });
$("#pin-btn").addEventListener("click", () => { if (!S.cur || !S.cur.id) return; S.cur.pinned = !S.cur.pinned; renderCur(); queueSave(0); });
$("#del-btn").addEventListener("click", async () => {
  if (!S.cur) return;
  if (!S.cur.id) { newSession(); return; }
  if (!confirm("刪除這個對話？")) return;
  await deleteSession(S.cur.id);
});
$("#title").addEventListener("change", () => { if (!S.cur) return; S.cur.title = $("#title").value.trim(); queueSave(0); });
["#think", "#with-status"].forEach(s => $(s).addEventListener("change", () => {
  if (!S.cur) return;
  S.cur.settings = { think: $("#think").checked, status: $("#with-status").checked }; queueSave();
}));

function queueSave(delay = 800) {
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(saveNow, delay);
}
async function saveNow() {
  const c = S.cur;
  if (!c || !c.messages.length && !c.title) return;
  if (!c.title) c.title = (c.messages.find(m => m.role === "user")?.content || "").slice(0, 40).replace(/\n.*/s, "") || "新對話";
  const body = Object.assign({}, c, { messages: c.messages.map(m => ({ role: m.role, by: m.by, content: m.content, thinking: m.thinking, stats: m.stats, error: m.error, images: m.images })) });
  try {
    const r = await saveSession(body);
    c.id = r.id; c.updated = r.updated;
    ls.set("last", r.id); remember(r.id);
    $("#title").value = c.title; renderList();
  } catch (err) { toast("存對話失敗：" + err.message, true); }
}

// ── 畫面 ──
function renderCur() {
  const c = S.cur;
  $("#title").value = c.title || "";
  $("#pin-btn").classList.toggle("on", !!c.pinned);
  $("#think").checked = c.settings?.think !== false;
  $("#with-status").checked = !!c.settings?.status;
  if (c.model && !c.model.startsWith("ext:") && !S.models.includes(c.model) && S.models.length) toast(`這個對話用的模型 ${c.model} 已經不在了，先換一個`, true);
  renderModelChip();
  if (typeof renderThinkChip === "function") renderThinkChip();
  const box = $("#msgs");
  $$(".msg", box).forEach(x => x.remove());
  $("#empty").hidden = !!c.messages.length;
  for (const m of c.messages) box.appendChild(msgEl(m));
  scrollMsgs(true);
}
function scrollMsgs(force) {
  const b = $("#msgs");
  if (force || b.scrollHeight - b.scrollTop - b.clientHeight < 120) b.scrollTop = b.scrollHeight;
}

// 簡單的 Markdown：程式碼區塊、行內程式碼、粗體、標題、清單、表格
function inline(s) {
  return s.split(/(`[^`]+`)/g).map(p => p.startsWith("`") && p.endsWith("`") && p.length > 1
    ? "<code>" + esc(p.slice(1, -1)) + "</code>"
    : esc(p).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")).join("");
}
function codeBlock(code, lang) {
  return `<div class="code"><div class="code-bar"><span>${esc(lang || "程式碼")}</span><button type="button" class="btn sm" data-copy>複製</button></div><pre><code>${esc(code)}</code></pre></div>`;
}
function md(src) {
  const L = src.split("\n"), out = [];
  let para = [], i = 0, m;
  const flush = () => { if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; } };
  const cells = l => l.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
  while (i < L.length) {
    const l = L[i];
    if ((m = l.match(/^\s*```\s*([\w+#.-]*)/))) {
      flush(); const buf = []; i++;
      while (i < L.length && !/^\s*```\s*$/.test(L[i])) buf.push(L[i++]);
      i++; out.push(codeBlock(buf.join("\n"), m[1])); continue;
    }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < L.length && /^\s*\|?\s*:?-{2,}/.test(L[i + 1])) {
      flush(); const head = cells(l); i += 2; const rows = [];
      while (i < L.length && /^\s*\|.*\|\s*$/.test(L[i])) rows.push(cells(L[i++]));
      out.push("<table><tr>" + head.map(c => "<th>" + inline(c) + "</th>").join("") + "</tr>" +
        rows.map(r => "<tr>" + r.map(c => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("") + "</table>");
      continue;
    }
    if ((m = l.match(/^#{1,6}\s+(.*)/))) { flush(); out.push("<h4>" + inline(m[1]) + "</h4>"); i++; continue; }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
      flush(); const ordered = /^\s*\d/.test(l), items = [];
      while (i < L.length && (m = L[i].match(/^\s*([-*+]|\d+[.)])\s+(.*)/))) { items.push(m[2]); i++; }
      const t = ordered ? "ol" : "ul";
      out.push(`<${t}>` + items.map(x => "<li>" + inline(x) + "</li>").join("") + `</${t}>`); continue;
    }
    if (!l.trim()) { flush(); i++; continue; }
    para.push(l); i++;
  }
  flush();
  return out.join("");
}
function splitThink(m) {
  let content = m.content || "", thinking = m.thinking || "";
  const s = content.indexOf("<think>");
  if (s >= 0 && !content.slice(0, s).trim()) {
    const e = content.indexOf("</think>");
    thinking += e >= 0 ? content.slice(s + 7, e) : content.slice(s + 7);
    content = e >= 0 ? content.slice(e + 8).replace(/^\s+/, "") : "";
  }
  return { content, thinking };
}
function msgEl(m, live) {
  const d = document.createElement("div");
  d.className = "msg " + (m.role === "user" ? "user" : "ai");
  d.dataset.by = m.by || (m.role === "assistant" ? "kiki" : "user");
  const prev = $("#msgs").lastElementChild;
  if (prev && prev.classList.contains("msg") && prev.dataset.by === d.dataset.by) d.classList.add("cont");
  fillMsg(d, m, live);
  return d;
}
function tldr(src) {
  // 長回答最上面放「重點」：第一段＋前幾個清單項目（不叫模型，純文字擷取）
  if (src.length < 1200) return "";
  const lines = src.split("\n").map(l => l.trim()).filter(Boolean);
  const first = lines.find(l => !/^(#|```|\||[-*+]|\d+[.)])/.test(l)) || "";
  const items = lines.filter(l => /^([-*+]|\d+[.)])\s+/.test(l)).slice(0, 5).map(l => l.replace(/^([-*+]|\d+[.)])\s+/, ""));
  if (!first && !items.length) return "";
  return `<details class="tldr"><summary>重點</summary>${first ? `<p>${inline(first.slice(0, 200))}</p>` : ""}${items.length ? "<ul>" + items.map(i => "<li>" + inline(i.slice(0, 120)) + "</li>").join("") + "</ul>" : ""}</details>`;
}
function fillMsg(d, m, live) {
  if (m.role === "user") {
    const imgs = (m.images || []).map(b => `<img src="data:image/*;base64,${esc(b)}" alt="">`).join("");
    d.innerHTML = `<div class="bubble">${imgs}${esc(m.content)}</div>`;
    return;
  }
  const { content, thinking } = splitThink(m);
  const by = m.by || "kiki", isP = by !== "kiki";
  d.classList.toggle("partner", isP);
  const prov = isP && typeof partner === "function" ? (partner()?.handle === by ? partner().provider : (typeof PROV_BY_HANDLE !== "undefined" ? PROV_BY_HANDLE[by] : "")) : "";
  if (prov) d.dataset.prov = prov; else delete d.dataset.prov;
  const pname = typeof nameOf === "function" ? nameOf(by) : by;
  let h = `<div class="av">${esc(isP ? pname[0].toUpperCase() : "K")}</div><div class="body"><div class="who">${esc(pname)}</div>`;
  if (thinking) h += `<details class="think" ${live && !content ? "open" : ""}><summary>思考過程</summary><div>${esc(thinking)}</div></details>`;
  h += `<div class="${live ? "cursor" : ""}">${live ? "" : tldr(content)}${md(content)}</div>`;
  if (m.error) h += `<div class="err">${esc(m.error)}</div>`;
  if (m.stopped) h += `<div class="stats">已停止</div>`;
  if (m.stats) h += `<div class="stats">${esc(m.stats)}</div>`;
  d.innerHTML = h + "</div>";
}
$("#msgs").addEventListener("click", e => {
  if (e.target.closest("[data-copy]")) copyText($("pre", e.target.closest(".code")).textContent);
});

// ── 送出 ──
async function sendChatLegacy(text) {
  const model = S.cur.model;
  if (!model) { toast("先選一個模型", true); return; }
  const user = { role: "user", content: text };
  if (S.attach.length) user.images = S.attach.map(a => a.b64);
  S.attach = []; renderAttach();
  const ai = { role: "assistant", content: "", thinking: "" };
  S.cur.messages.push(user, ai);
  $("#empty").hidden = true;
  const box = $("#msgs");
  box.appendChild(msgEl(user));
  const el = msgEl(ai, true);
  box.appendChild(el);
  scrollMsgs(true);

  const msgs = $("#with-status").checked && statusForAI() ? [{ role: "system", content: statusForAI() }] : [];
  for (const m of S.cur.messages.slice(0, -1)) {
    const x = { role: m.role, content: m.role === "assistant" ? splitThink(m).content : m.content };
    if (m.images) x.images = m.images;
    msgs.push(x);
  }
  const body = { model, messages: msgs };
  const caps = S.caps[model]?.capabilities || [];
  if (caps.includes("thinking")) {
    const on = $("#think").checked;
    body.think = /^gpt-oss/.test(model) ? (on ? "medium" : "low") : on;
  }
  S.busy = true; S.ctrl = new AbortController();
  $("#send").hidden = true; $("#stop").hidden = false;
  let raf = 0, live = true;
  const paint = () => { if (!raf) raf = later(() => { raf = 0; if (live) { fillMsg(el, ai, true); scrollMsgs(); } }); };
  try {
    const r = await api("/api/chat", { body, raw: true, signal: S.ctrl.signal });
    for await (const ev of lines(r)) {
      if (ev.error) { ai.error = ev.error; break; }
      const m = ev.message || {};
      if (m.thinking) ai.thinking += m.thinking;
      if (m.content) ai.content += m.content;
      if (ev.done && ev.eval_count) {
        const tps = ev.eval_duration ? ev.eval_count / ev.eval_duration * 1e9 : 0;
        const first = ((ev.load_duration || 0) + (ev.prompt_eval_duration || 0)) / 1e9;
        ai.stats = `${model} · ${tps.toFixed(1)} tokens/秒 · ${ev.eval_count} tokens · 開始回答前等了 ${first.toFixed(1)} 秒`;
      }
      paint();
    }
  } catch (err) {
    if (err.name === "AbortError") ai.stopped = true; else ai.error = err.message;
  } finally {
    live = false; S.busy = false; S.ctrl = null;
    $("#send").hidden = false; $("#stop").hidden = true;
    fillMsg(el, ai, false); scrollMsgs();
    queueSave(0);
  }
}
$("#composer").addEventListener("submit", e => {
  e.preventDefault();
  const ta = $("#input"), text = ta.value.trim();
  if (!text || S.busy) return;
  if (text.startsWith("/") && runSlash(text)) { ta.value = ""; autosize(ta); return; }
  ta.value = ""; autosize(ta); $("#slash").hidden = true;
  if (typeof RM !== "undefined" && RM.cur) roomSend(text); else sendChat(text);
});
$("#input").addEventListener("keydown", e => {
  const menu = $("#slash");
  if (!menu.hidden && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault(); const items = $$("button", menu), i = items.findIndex(b => b.classList.contains("on"));
    items.forEach(b => b.classList.remove("on"));
    items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].classList.add("on"); return;
  }
  if (!menu.hidden && e.key === "Tab") { e.preventDefault(); $("button.on", menu)?.click(); return; }
  if (e.key === "Escape") menu.hidden = true;
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
});
$("#input").addEventListener("input", e => { autosize(e.target); renderSlash(e.target.value); });
$("#stop").addEventListener("click", () => S.ctrl?.abort());

// ── 快捷指令（輸入 / 開頭） ──
const SLASH = [
  { k: "/新", d: "開新對話", run: () => newSession() },
  { k: "/模型", d: "切換 Kiki 用的模型", run: () => $("#model-chip").click() },
  { k: "/夥伴", d: "加入或移出我的 AI 夥伴", run: () => $("#partner-chip").click() },
  { k: "/討論", d: "讓夥伴和 Kiki 討論上一題", run: () => $("#discuss-btn").click() },
  { k: "/狀態", d: "把主機狀態貼進對話，讓 AI 看", run: () => { $("#with-status").checked = true; sendChat("幫我看一下這台主機現在的狀態，哪裡該注意？"); } },
  { k: "/釘選", d: "釘選／取消釘選這個對話", run: () => $("#pin-btn").click() },
  { k: "/分支", d: "把目前對話分支出一個新對話", run: () => S.cur.id && forkSession(S.cur.id) },
  { k: "/群組", d: "把目前對話移到群組", run: () => { const g = prompt("群組名稱（留空＝不分群組）", S.cur.group || ""); if (g != null) { S.cur.group = g.trim(); queueSave(0); } } },
  { k: "/清除", d: "清掉目前對話的訊息（保留標題）", run: () => { if (confirm("清掉這個對話的所有訊息？")) { S.cur.messages = []; renderCur(); queueSave(0); } } },
  { k: "/面板", d: "到舊面板：下載模型、匯入、終端機", run: () => (location.href = "../") },
];
function renderSlash(v) {
  const menu = $("#slash");
  if (!v.startsWith("/") || v.includes("\n")) { menu.hidden = true; return; }
  const hits = SLASH.filter(c => c.k.startsWith(v.trim()) || v.trim() === "/");
  menu.hidden = !hits.length;
  menu.innerHTML = hits.map((c, i) => `<button type="button" data-k="${c.k}" class="${i ? "" : "on"}"><b>${c.k}</b><span>${c.d}</span></button>`).join("");
}
$("#slash").addEventListener("click", e => { const b = e.target.closest("[data-k]"); if (b) { $("#input").value = ""; autosize($("#input")); $("#slash").hidden = true; runSlash(b.dataset.k); } });
function runSlash(text) {
  const c = SLASH.find(x => text.trim() === x.k);
  if (!c) return false;
  $("#slash").hidden = true; c.run(); return true;
}

// ── 附圖 ──
$("#attach-btn").addEventListener("click", () => $("#attach-input").click());
$("#attach-input").addEventListener("change", async e => {
  for (const f of [...e.target.files].slice(0, 8)) {
    const url = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(f); });
    S.attach.push({ b64: String(url).split(",")[1], url });
  }
  e.target.value = ""; renderAttach();
});
function renderAttach() {
  $("#attach-list").innerHTML = S.attach.map((a, i) => `<span><img src="${a.url}" alt=""><button type="button" data-rm="${i}">×</button></span>`).join("");
}
$("#attach-list").addEventListener("click", e => { const b = e.target.closest("[data-rm]"); if (b) { S.attach.splice(+b.dataset.rm, 1); renderAttach(); } });

// ── 啟動 ──
async function boot() {
  $("#login").hidden = true; $("#app").hidden = false;
  try { await loadMe(); } catch (err) { showLogin(err.message); return; }
  await Promise.all([pollStatus(), loadModels(), loadList(), typeof loadRooms === "function" ? loadRooms() : null, typeof loadMyAI === "function" ? loadMyAI() : null]);
  const last = ls.get("last", "");
  if (last && S.sessions.some(s => s.id === last)) await openSession(last); else newSession();
  if (!S.cur) newSession();
  histBtns();
  if (wide() && ls.get("right", true)) openRight(true);
  setInterval(pollStatus, 15000);
}
if (TOKEN) {
  fetch("/api/me", { headers: { Authorization: "Bearer " + TOKEN } })
    .then(r => (r.ok ? boot() : showLogin())).catch(() => showLogin("連不到主機"));
} else showLogin();

// ── 寄信設定（管理員） ──
$("#mail-btn").addEventListener("click", async () => {
  $("#settings-pop").hidden = true; $("#mail-modal").hidden = false;
  try { const m = await api("/api/mail"); const f = $("#mail-form"); f.user.value = m.user; f.sender.value = m.sender; f.require.checked = m.require; $("#mail-state").textContent = m.configured ? "已設定" : "還沒設定：申請帳號時不會寄驗證信（會直接進待核准）"; }
  catch (err) { toast(err.message, true); }
});
$("#mail-close").addEventListener("click", () => ($("#mail-modal").hidden = true));
$("#mail-modal").addEventListener("click", e => { if (e.target === e.currentTarget) $("#mail-modal").hidden = true; });
$("#mail-form").addEventListener("submit", async e => {
  e.preventDefault(); const f = e.target;
  try { const m = await api("/api/mail/set", { body: { user: f.user.value.trim(), password: f.password.value, sender: f.sender.value.trim(), require: f.require.checked } }); f.password.value = ""; $("#mail-state").textContent = m.configured ? "已儲存" : "還沒設定密碼"; toast("已儲存"); }
  catch (err) { toast(err.message, true); }
});
$("#mail-test").addEventListener("click", async () => { try { const r = await api("/api/mail/test", { body: {} }); toast("測試信已寄到 " + r.to); } catch (err) { toast(err.message, true); } });
