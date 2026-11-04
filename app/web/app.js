"use strict";
// AI 主機面板：狀態、模型、對話、終端機。純 JavaScript，不載入任何外部檔案（離線主機也能用）。

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ls = {
  get(k, d) { try { const v = localStorage.getItem("aisk." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("aisk." + k, JSON.stringify(v)); } catch { /* 私密模式等，忽略 */ } },
  del(k) { try { localStorage.removeItem("aisk." + k); } catch { /* 忽略 */ } },
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const GiB = 1024 ** 3;
const gib = b => b == null ? "—" : (b / GiB).toFixed(b / GiB >= 100 ? 0 : 1) + " GB";       // 記憶體、磁碟
const gb = b => b == null ? "—" : (b / 1e9).toFixed(b / 1e9 >= 100 ? 0 : 1) + " GB";         // 模型下載量（跟 Ollama 網站一致）
const pct = (a, b) => b ? Math.max(0, Math.min(100, a / b * 100)) : 0;
const heat = p => p >= 95 ? "crit" : p >= 80 ? "hot" : "";
function dur(sec) {
  if (sec == null) return "—";
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  return d ? `${d} 天 ${h} 小時` : h ? `${h} 小時 ${m} 分` : `${m} 分`;
}

let TOKEN = ls.get("token", "");
const S = { tab: "status", status: null, models: null, jobs: [], plat: "", caps: {} };

// ── 連線 ──
async function api(path, opt = {}) {
  const init = { method: opt.method || (opt.body !== undefined ? "POST" : "GET"), headers: { Authorization: "Bearer " + TOKEN }, signal: opt.signal };
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
      const l = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (l) { try { yield JSON.parse(l); } catch { /* 不完整的行 */ } }
    }
  }
  if (buf.trim()) { try { yield JSON.parse(buf); } catch { /* 忽略 */ } }
}
let toastT;
function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => (t.hidden = true), bad ? 6000 : 3000);
}
async function copyText(s) {
  try { await navigator.clipboard.writeText(s); }
  catch { const ta = document.createElement("textarea"); ta.value = s; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
  toast("已複製");
}
function autosize(ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight + 2, 180) + "px"; }
// 串流時合併重畫。不用 requestAnimationFrame：分頁在背景時它會停，回來前畫面不會更新
const later = fn => setTimeout(fn, 40);

// ── 登入 ──
function showLogin(msg) {
  $("#app").hidden = true; $("#login").hidden = false;
  $("#login-err").textContent = msg || "";
  setTimeout(() => $("#login-token").focus(), 50);
}
$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  const tok = $("#login-token").value.trim();
  $("#login-err").textContent = "";
  try {
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: tok }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || "登入失敗");
    TOKEN = tok; ls.set("token", tok); $("#login-token").value = "";
    start();
  } catch (err) { $("#login-err").textContent = err.message; }
});
$("#logout-btn").addEventListener("click", () => { TOKEN = ""; ls.del("token"); showLogin(); });

// ── 分頁 ──
function switchTab(tab) {
  S.tab = tab; ls.set("tab", tab);
  document.body.classList.toggle("fill", tab === "chat" || tab === "term");
  $$("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  $$(".view").forEach(v => v.classList.toggle("on", v.id === "view-" + tab));
  if (tab === "status") pollStatus();
  if (tab === "models") loadModels();
  if (tab === "chat") { if (!S.models) loadModels(); scrollMsgs(true); }
  if (tab === "term") initTerm();
}
$$("#tabs button").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.tab)));

let started = false;
async function start() {
  // 先確認有沒有簽使用協議
  try {
    const a = await api("/api/agreement");
    if (!a.accepted) { showAgreement(a); return; }
  } catch { /* 連不到協議就先讓他用，下次再簽 */ }
  enterApp();
}
function enterApp() {
  $("#login").hidden = true; $("#agree").hidden = true; $("#app").hidden = false;
  if (started) return;
  started = true;
  switchTab(ls.get("tab", "status"));
  statusLoop();
  refreshJobs();
  if (S.tab !== "models") loadModels();
  restoreChat();
}

// 使用協議頁
function showAgreement(a) {
  $("#login").hidden = true; $("#app").hidden = true; $("#agree").hidden = false;
  $("#agree-text").textContent = a.text || "";
  $("#agree-form").dataset.version = a.version || "";
  $("#agree-ok").checked = false;      // 一個勾：預設不打勾，打勾才能進去
  $("#agree-btn").disabled = true;
  $("#agree-err").textContent = "";
}
$("#agree-ok").addEventListener("change", () => { $("#agree-btn").disabled = !$("#agree-ok").checked; });
$("#agree-form").addEventListener("submit", async e => {
  e.preventDefault();
  if (!$("#agree-ok").checked) return;
  try {
    await api("/api/agreement/accept", { body: { version: $("#agree-form").dataset.version } });
    enterApp();
  } catch (err) { $("#agree-err").textContent = err.message; }
});

// ═════════ 狀態 ═════════
const hist = { cpu: [], mem: [], gpu: {} };
function push(arr, v) { arr.push(v); if (arr.length > 90) arr.shift(); }
function spark(svg, arr) {
  if (!svg) return;
  const n = arr.length;
  const pts = n < 2 ? "" : arr.map((v, i) => `${i / (n - 1) * 100},${30 - (v ?? 0) / 100 * 28 - 1}`).join(" ");
  $("polyline", svg).setAttribute("points", pts);
}
async function statusLoop() {
  for (;;) {
    await pollStatus();
    await sleep(S.tab === "status" && !document.hidden ? 2000 : 10000);
  }
}
let polling = false;
async function pollStatus() {
  if (polling || !TOKEN || $("#app").hidden) return;
  polling = true;
  try { const s = await api("/api/status"); S.status = s; S.plat = s.platform; renderStatus(s); }
  catch { $("#ollama-pill").className = "pill"; }
  finally { polling = false; }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollStatus(); });

function ollamaHint() {
  return S.plat === "windows"
    ? "到開始功能表打開「Ollama」，或在終端機執行 <code>ollama serve</code>。"
    : "在終端機執行 <code>sudo systemctl start ollama</code>。";
}
function renderStatus(s) {
  $("#host-name").textContent = `${s.hostname} · ${s.os}`;
  const ol = s.ollama || {};
  const pill = $("#ollama-pill");
  pill.className = "pill " + (ol.up ? "up" : "down");
  $("span", pill).textContent = ol.up ? "Ollama " + ol.version : "Ollama 未執行";
  const down = $("#ollama-down");
  down.hidden = !!ol.up;
  if (!ol.up) down.innerHTML = "Ollama 沒有在執行，模型和對話暫時不能用。" + ollamaHint();
  const jb = $("#jobs-badge"), n = s.panel?.jobs_running || 0;
  jb.hidden = !n; jb.textContent = n;

  $("#cpu-val").textContent = s.cpu == null ? "—" : Math.round(s.cpu);
  $("#cpu-name").textContent = `${s.cpu_name || ""} · ${s.cpu_count} 執行緒`;
  push(hist.cpu, s.cpu); spark($("#cpu-spark"), hist.cpu);
  if (s.mem) {
    const p = pct(s.mem.used, s.mem.total);
    $("#mem-val").textContent = (s.mem.used / GiB).toFixed(1);
    $("#mem-total").textContent = " / " + gib(s.mem.total);
    const b = $("#mem-bar"); b.style.width = p + "%"; b.className = heat(p);
    push(hist.mem, p); spark($("#mem-spark"), hist.mem);
  }

  const gg = $("#gpu-grid");
  if (!s.gpus.length) {
    gg.innerHTML = `<div class="card"><div class="card-h">顯示卡</div><p class="muted small" style="margin:0">沒有偵測到 NVIDIA 顯示卡（AMD／Intel 顯示卡目前不顯示細節）。沒有顯示卡也能跑，只是會用 CPU、比較慢。</p></div>`;
  } else {
    if (gg.children.length !== s.gpus.length || !$(".gpu-card", gg)) {
      gg.innerHTML = s.gpus.map(g => `<div class="card gpu-card" data-i="${g.index}">
        <div class="card-h"><span class="g-name"></span><span class="muted small">#${g.index}</span></div>
        <div class="big"><span class="g-util">—</span><small>% 使用中</small></div>
        <div class="muted small g-mem"></div><div class="bar"><i class="g-bar"></i></div>
        <svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none"><polyline/></svg>
        <div class="metrics"><div class="metric"><b class="g-temp">—</b><span>溫度</span></div>
        <div class="metric"><b class="g-pow">—</b><span>功耗</span></div><div class="metric"><b class="g-fan">—</b><span>風扇</span></div></div></div>`).join("");
    }
    for (const g of s.gpus) {
      const c = $(`.gpu-card[data-i="${g.index}"]`, gg);
      if (!c) continue;
      const mp = pct(g.mem_used, g.mem_total);
      $(".g-name", c).textContent = g.name.replace(/^NVIDIA\s+/, "");
      $(".g-util", c).textContent = g.util == null ? "—" : Math.round(g.util);
      $(".g-mem", c).textContent = `顯示卡記憶體 ${gib(g.mem_used)} / ${gib(g.mem_total)}`;
      const b = $(".g-bar", c); b.style.width = mp + "%"; b.className = "g-bar " + heat(mp);
      $(".g-temp", c).textContent = g.temp == null ? "—" : Math.round(g.temp) + "°C";
      $(".g-pow", c).textContent = g.power == null ? "—" : Math.round(g.power) + (g.power_limit ? "/" + Math.round(g.power_limit) : "") + " W";
      $(".g-fan", c).textContent = g.fan == null ? "—" : Math.round(g.fan) + "%";
      const h = hist.gpu[g.index] || (hist.gpu[g.index] = []);
      push(h, g.util); spark($(".spark", c), h);
    }
  }

  const ld = ol.loaded || [];
  $("#loaded-list").innerHTML = !ol.up ? `<div class="empty-li">Ollama 沒有在執行</div>`
    : !ld.length ? `<div class="empty-li">目前沒有載入模型（第一次對話時會自動載入）</div>`
    : ld.map(m => {
      const vp = m.size ? Math.round(pct(m.size_vram, m.size)) : 0;
      const where = vp >= 99 ? "全部在顯示卡" : vp <= 1 ? "全部在系統記憶體（CPU 跑）" : `顯示卡 ${vp}%、其他在系統記憶體`;
      const left = m.expires_at ? Math.round((new Date(m.expires_at) - Date.now()) / 60000) : null;
      return `<div class="li"><div class="li-main"><div class="li-title">${esc(m.name)}</div>
        <div class="li-sub">${gib(m.size)} · ${where}${left != null && left > 0 && left < 100000 ? ` · ${left} 分後自動卸載` : ""}</div></div>
        <div class="li-act"><button class="btn sm" data-unload="${esc(m.name)}">卸載</button></div></div>`;
    }).join("");
  const known = s.gpu_procs.filter(p => p.mem != null).sort((a, b) => b.mem - a.mem);
  const unknown = s.gpu_procs.filter(p => p.mem == null).map(p => p.name);
  $("#gpu-procs").innerHTML = !s.gpu_procs.length ? `<div class="empty-li">沒有</div>`
    : known.map(p => `<div class="li"><div class="li-main"><div class="li-title">${esc(p.name)}</div>
      <div class="li-sub">PID ${p.pid ?? "—"} · 顯示卡 ${p.gpu ?? "—"}</div></div><b>${gib(p.mem)}</b></div>`).join("") +
      (unknown.length ? `<details class="empty-li"><summary>${unknown.length} 個程式在用顯示卡（Windows 不提供各程式用量）</summary>${esc([...new Set(unknown)].join("、"))}</details>` : "");
  $("#disk-list").innerHTML = s.disks.map(d => {
    const p = pct(d.used, d.total);
    return `<div><div class="li-sub" style="display:flex;justify-content:space-between"><b style="color:var(--text)">${esc(d.mount)}</b>
      <span>剩 ${gib(d.total - d.used)} / ${gib(d.total)}</span></div><div class="bar"><i class="${heat(p)}" style="width:${p}%"></i></div></div>`;
  }).join("") || `<div class="empty-li">—</div>`;
  $("#host-kv").innerHTML = [["主機名稱", s.hostname], ["系統", s.os], ["CPU", s.cpu_name], ["開機時間", dur(s.uptime)],
    ["Ollama", ol.up ? ol.version + "（" + ol.base + "）" : "未執行"], ["面板", s.panel?.version + " · Python " + s.python]]
    .map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");
}
$("#loaded-list").addEventListener("click", async e => {
  const b = e.target.closest("[data-unload]");
  if (!b) return;
  b.disabled = true;
  try { await api("/api/models/unload", { body: { name: b.dataset.unload } }); toast("已卸載"); pollStatus(); }
  catch (err) { toast(err.message, true); b.disabled = false; }
});

// ═════════ 模型 ═════════
let loadingModels = null;
function loadModels() {
  if (loadingModels) return loadingModels;
  loadingModels = (async () => {
    try { S.models = await api("/api/models"); renderModels(); fillChatModels(); loadImports(); }
    catch (err) { toast(err.message, true); }
    finally { loadingModels = null; }
  })();
  return loadingModels;
}
function fit(size) {
  const hw = S.models?.hw || {};
  const need = size * 1.15 + 0.7e9;  // 權重＋對話暫存（預設上下文）的粗估
  if (hw.vram && need <= hw.vram) return ["ok", "顯示卡放得下"];
  if (hw.vram && hw.ram && need <= hw.vram + hw.ram * 0.6) return ["mid", "會借用系統記憶體，比較慢"];
  if (!hw.vram && hw.ram && need <= hw.ram * 0.7) return ["mid", "沒有顯示卡，用 CPU 跑（慢）"];
  return ["no", "記憶體不夠"];
}
const installedNames = () => new Set((S.models?.installed || []).map(m => m.name));
const isEmbed = n => /embed|bge-|minilm|arctic-embed/i.test(n);
let groupFilter = ls.get("group", "chat");

function renderModels() {
  const m = S.models;
  const down = $("#models-down");
  down.hidden = m.up;
  if (!m.up) down.innerHTML = "Ollama 沒有在執行：" + esc(m.error || "") + "<br>" + ollamaHint();
  const inst = m.installed;
  $("#installed-sum").textContent = inst.length ? `${inst.length} 個 · 共 ${gb(inst.reduce((a, x) => a + (x.size || 0), 0))}` : "";
  $("#installed-list").innerHTML = !inst.length ? `<div class="empty-li">還沒有模型。從下面挑一個下載，或匯入自己的。</div>`
    : inst.map(x => {
      const d = x.details || {};
      const sub = [gb(x.size), d.parameter_size, d.quantization_level, d.family].filter(Boolean).join(" · ");
      return `<div class="li"><div class="li-main"><div class="li-title">${esc(x.name)} ${x.loaded ? '<span class="tag ok">已載入</span>' : ""}</div>
        <div class="li-sub">${esc(sub)}</div></div><div class="li-act">
        ${isEmbed(x.name) ? "" : `<button class="btn sm" data-act="chat" data-name="${esc(x.name)}">對話</button>`}
        <button class="btn sm" data-act="${x.loaded ? "unload" : "load"}" data-name="${esc(x.name)}">${x.loaded ? "卸載" : "載入"}</button>
        <button class="btn sm danger" data-act="delete" data-name="${esc(x.name)}">刪除</button></div></div>`;
    }).join("");

  const hw = m.hw;
  $("#hw-line").textContent = (hw.gpus ? `這台有 ${hw.gpus} 張顯示卡、共 ${gib(hw.vram)} 顯示卡記憶體` : "這台沒有偵測到 NVIDIA 顯示卡")
    + `，系統記憶體 ${gib(hw.ram)}。版本旁的字是放不放得下的估計。`;
  const groups = m.catalog.groups;
  $("#group-chips").innerHTML = [["all", "全部"], ...groups.map(g => [g.id, g.name])]
    .map(([id, n]) => `<button class="chip ${groupFilter === id ? "on" : ""}" data-g="${id}">${esc(n)}</button>`).join("");
  const have = installedNames();
  const caps = { vision: "看圖", tools: "工具", thinking: "思考", embedding: "嵌入" };
  $("#catalog").innerHTML = groups.filter(g => groupFilter === "all" || g.id === groupFilter).flatMap(g => g.models).map(md0 =>
    `<div class="model"><h4>${esc(md0.name)} ${md0.caps.map(c => `<span class="tag">${caps[c] || c}</span>`).join("")}</h4><p>${esc(md0.desc)}</p>
    <div class="vers">${md0.tags.map(t => {
      const full = md0.name + ":" + t.tag, [cls, label] = fit(t.size || 0), got = have.has(full);
      return `<button class="ver fit-${cls} ${got ? "have" : ""}" data-pull="${esc(full)}" data-size="${t.size || 0}">${esc(t.tag)} · ${gb(t.size)}<small>${got ? "已安裝" : label}</small></button>`;
    }).join("")}</div></div>`).join("");
  $("#catalog-note").textContent = m.catalog.updated ? `清單更新於 ${m.catalog.updated}。大小是 Ollama 官方模型庫的實際下載量。` : "";
}
$("#group-chips").addEventListener("click", e => {
  const c = e.target.closest("[data-g]");
  if (!c) return;
  groupFilter = c.dataset.g; ls.set("group", groupFilter); renderModels();
});
$("#catalog").addEventListener("click", e => {
  const b = e.target.closest("[data-pull]");
  if (!b) return;
  const name = b.dataset.pull, size = +b.dataset.size;
  if (installedNames().has(name)) { chatWith(name); return; }
  if (confirm(`下載 ${name}（${gb(size)}）？\n估計：${fit(size)[1]}`)) pull(name);
});
$("#pull-form").addEventListener("submit", async e => {
  e.preventDefault();
  const name = $("#pull-name").value.trim();
  if (!name) return;
  let msg = `下載 ${name}？`;
  try { const r = await api("/api/models/size?name=" + encodeURIComponent(name)); if (r.size) msg = `下載 ${name}（${gb(r.size)}）？\n估計：${fit(r.size)[1]}`; } catch { /* 查不到大小也可以下載 */ }
  if (confirm(msg)) { pull(name); $("#pull-name").value = ""; }
});
async function pull(name) {
  try {
    const r = await api("/api/models/pull", { body: { name } });
    watchJob(r.id); toast("開始下載，右上角可以看進度");
  } catch (err) { toast(err.message, true); }
}

// 搜尋全網開源模型
const CAPZH = { vision: "看圖", tools: "工具", thinking: "思考", embedding: "嵌入", audio: "聲音", cloud: "雲端", insert: "補全", decision: "決策" };
const nf = n => n == null ? "" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n);
let searching = false;
$("#search-form").addEventListener("submit", async e => {
  e.preventDefault();
  const q = $("#search-q").value.trim();
  const box = $("#search-results");
  if (q.length < 2) { box.innerHTML = `<p class="muted small">搜尋字至少 2 個字</p>`; return; }
  if (searching) return;
  searching = true;
  box.innerHTML = `<p class="muted small">搜尋「${esc(q)}」…</p>`;
  $("#catalog-wrap").open = false;
  try { renderSearch(await api("/api/models/search?q=" + encodeURIComponent(q) + "&source=all")); }
  catch (err) { box.innerHTML = `<p class="muted small">搜尋失敗：${esc(err.message)}</p>`; }
  finally { searching = false; }
});
function renderSearch(r) {
  const have = installedNames();
  const card = (title, sub, caps, actions) => `<div class="model"><h4>${esc(title)} ${(caps || []).map(c => `<span class="tag">${CAPZH[c] || c}</span>`).join("")}</h4>
    <p>${esc(sub || "")}</p><div class="vers">${actions}</div></div>`;
  const ol = (r.ollama || []).map(m => card(m.name, m.desc, m.caps,
    `<button class="btn sm primary" data-pullname="${esc(m.name)}">${have.has(m.name) || have.has(m.name + ":latest") ? "已安裝" : "下載"}</button>
     <button class="btn sm" data-fill="${esc(m.name)}:">選版本</button>
     <span class="muted small" style="align-self:center">${m.pulls ? "⤓ " + nf(m.pulls) : ""}</span>`)).join("");
  const hf = (r.hf || []).map(m => card(m.name, "Hugging Face · ⤓ " + nf(m.pulls) + " · ♥ " + nf(m.likes), m.gated ? ["需同意授權"] : [],
    `<button class="btn sm" data-hf="${esc(m.repo)}">選量化版本</button><div class="hf-tags" data-tags-for="${esc(m.repo)}"></div>`)).join("");
  const body = (ol ? `<div class="sec-h">Ollama 官方庫</div>${ol}` : r.ollama_err ? `<p class="muted small">Ollama 搜尋：${esc(r.ollama_err)}</p>` : "")
    + (hf ? `<div class="sec-h">Hugging Face（GGUF）</div>${hf}` : r.hf_err ? `<p class="muted small">HF 搜尋：${esc(r.hf_err)}</p>` : "");
  $("#search-results").innerHTML = body || `<p class="muted small">沒找到「${esc(r.q)}」。試更短的關鍵字，或直接在上面的下載框輸入完整名稱。</p>`;
}
$("#search-results").addEventListener("click", async e => {
  const pn = e.target.closest("[data-pullname]");
  if (pn) return pullByName(pn.dataset.pullname);
  const fill = e.target.closest("[data-fill]");
  if (fill) { const i = $("#pull-name"); i.value = fill.dataset.fill; i.focus(); i.scrollIntoView({ block: "center" }); return; }
  const hp = e.target.closest("[data-hfpull]");
  if (hp) return pullByName(hp.dataset.hfpull);
  const hf = e.target.closest("[data-hf]");
  if (hf) {
    const box = $(`[data-tags-for="${CSS.escape(hf.dataset.hf)}"]`);
    if (box.dataset.loaded) { box.innerHTML = ""; delete box.dataset.loaded; return; }
    box.innerHTML = `<span class="muted small">讀取版本…</span>`;
    try {
      const t = await api("/api/models/hf_tags?repo=" + encodeURIComponent(hf.dataset.hf));
      box.dataset.loaded = "1";
      box.innerHTML = t.tags.length ? t.tags.map(tag => `<button class="chip" data-hfpull="hf.co/${esc(t.repo)}:${esc(tag)}">${esc(tag)}</button>`).join("")
        : `<span class="muted small">這個倉庫沒有可直接下載的 GGUF 量化檔</span>`;
    } catch (err) { box.innerHTML = `<span class="muted small">${esc(err.message)}</span>`; }
  }
});
async function pullByName(name) {
  let msg = `下載 ${name}？`;
  try { const r = await api("/api/models/size?name=" + encodeURIComponent(name)); if (r.size) msg = `下載 ${name}（${gb(r.size)}）？\n估計：${fit(r.size)[1]}`; } catch { /* 查不到大小也能下載 */ }
  if (confirm(msg)) pull(name);
}
$("#installed-list").addEventListener("click", async e => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const name = b.dataset.name, act = b.dataset.act;
  if (act === "chat") return chatWith(name);
  if (act === "delete" && !confirm(`確定刪除 ${name}？要用的話得重新下載。`)) return;
  b.disabled = true;
  if (act === "load") b.textContent = "載入中…";
  try {
    await api("/api/models/" + act, { body: { name } });
    toast({ load: "已載入", unload: "已卸載", delete: "已刪除" }[act]);
    await loadModels();
  } catch (err) { toast(err.message, true); b.disabled = false; }
});

// 匯入
let impMode = "path";
$("#import-seg").addEventListener("click", e => {
  const b = e.target.closest("[data-mode]");
  if (!b) return;
  impMode = b.dataset.mode;
  $$("#import-seg button").forEach(x => x.classList.toggle("on", x === b));
  $("#imp-path-box").hidden = impMode !== "path";
  $("#imp-upload-box").hidden = impMode !== "upload";
});
async function loadImports() {
  try {
    const r = await api("/api/imports");
    $("#imp-files").innerHTML = r.files.map(f => `<button type="button" class="chip" data-path="${esc(f.path)}">${esc(f.name)} · ${gb(f.size)}</button>`).join("");
    $("#imp-path").placeholder = (S.plat === "windows" ? "例如 D:\\models\\my-model.gguf" : "例如 /data/models/my-model.gguf") + `（上傳的檔案在 ${r.dir}）`;
  } catch { /* 忽略 */ }
}
$("#imp-files").addEventListener("click", e => {
  const c = e.target.closest("[data-path]");
  if (!c) return;
  $("#imp-path").value = c.dataset.path;
  if (!$("#imp-name").value) $("#imp-name").value = nameFromFile(c.textContent.split(" · ")[0]);
  scheduleIdentify();
});

// 匯入前先辨識：這是什麼模型、網路上找不找得到
let identifyTimer, lastIdentified = "";
function scheduleIdentify() { clearTimeout(identifyTimer); identifyTimer = setTimeout(runIdentify, 450); }
$("#imp-path").addEventListener("input", scheduleIdentify);
async function runIdentify() {
  const path = $("#imp-path").value.trim();
  const box = $("#imp-identify");
  if (!path) { box.innerHTML = ""; lastIdentified = ""; return; }
  if (path === lastIdentified) return;
  lastIdentified = path;
  box.innerHTML = `<div class="idbox"><div class="li-sub">辨識中…</div></div>`;
  let r;
  try { r = await api("/api/models/identify", { body: { path } }); }
  catch (err) { box.innerHTML = `<div class="idbox"><div class="li-sub">辨識：${esc(err.message)}</div></div>`; return; }
  if ($("#imp-path").value.trim() !== path) return;  // 使用者又改了路徑
  renderIdentify(r);
}
function renderIdentify(r) {
  const i = r.info || {}, box = $("#imp-identify");
  const parts = [i.name, i.architecture && ("架構 " + i.architecture), i.model_type, i.size_label,
    i.quant_version && ("量化格式 v" + i.quant_version)].filter(Boolean);
  let h = `<div class="idbox"><div class="li-title">${r.files.length > 1 ? r.files.length + " 個檔案" : esc(r.files[0] || "")}</div>`;
  h += `<div class="li-sub">${parts.length ? esc(parts.join(" · ")) : "讀不出中介資料（還是可以匯入）"}</div>`;
  const ol = r.online?.ollama || [], hf = r.online?.hf || [];
  if (r.found) {
    const names = [...ol.map(m => m.name), ...hf.map(m => m.name)].slice(0, 3);
    h += `<div class="li-sub ok">網路上有類似的公開模型：${names.map(esc).join("、")}（也可以直接下載官方版本）</div>`;
  } else {
    h += `<div class="li-sub warn">Ollama 官方庫和 Hugging Face 上都找不到 → 看起來是自製或非公開的模型。</div>`;
    const sh = S.models?.share;
    if (sh?.enabled) {
      h += `<div><button type="button" class="btn sm" id="share-btn">匯入並回傳給${esc(sh.label)}</button>
        <span class="muted small">按了才會把整個模型檔上傳給${esc(sh.label)}；不按不會傳。</span></div>`;
    } else if (sh?.configured) {
      h += `<div class="muted small">模型分享目前關閉（尚未同意使用協議），不會上傳到任何地方。</div>`;
    } else {
      h += `<div class="muted small">這台沒有設定回傳對象，不會上傳到任何地方。</div>`;
    }
  }
  box.innerHTML = h + `</div>`;
}
$("#imp-identify").addEventListener("click", async e => {
  const btn = e.target.closest("#share-btn");
  if (!btn) return;
  const sh = S.models?.share || {}, body = importBody();
  if (!body.name) { toast("請先填模型名稱", true); $("#imp-name").focus(); return; }
  if (!body.path) { toast("找不到模型路徑", true); return; }
  if (!confirm(`是否確認要將此 AI 模型匯入系統並回傳給「${sh.label}」？\n\n匯入：這台電腦可以使用這個模型。\n回傳：會把完整的模型檔案上傳給${sh.label}。`)) return;
  btn.disabled = true;
  try {
    const imp = await api("/api/models/import", { body });   // 先在本機匯入
    watchJob(imp.id);
    const shr = await api("/api/models/share", { body: { path: body.path } });  // 再回傳
    watchJob(shr.id);
    toast("開始匯入並回傳，右上角可看進度");
  } catch (err) { toast(err.message, true); btn.disabled = false; }
});
const nameFromFile = f => f.replace(/\.gguf$/i, "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[^a-z0-9]+/, "").slice(0, 60) || "my-model";
$("#imp-file").addEventListener("change", () => {
  const f = $("#imp-file").files[0];
  if (!f) return;
  let safe = f.name.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^[^A-Za-z0-9]+/, "");
  if (!/\.gguf$/i.test(safe)) { toast("只能上傳 .gguf 檔", true); return; }
  const bar = $("#imp-up-bar"), txt = $("#imp-up-txt");
  bar.hidden = false; $("i", bar).style.width = "0%";
  const xhr = new XMLHttpRequest();
  xhr.open("PUT", "/api/upload?name=" + encodeURIComponent(safe));
  xhr.setRequestHeader("Authorization", "Bearer " + TOKEN);
  xhr.upload.onprogress = ev => { if (ev.lengthComputable) { $("i", bar).style.width = pct(ev.loaded, ev.total) + "%"; txt.textContent = `上傳中 ${gb(ev.loaded)} / ${gb(ev.total)}`; } };
  xhr.onload = () => {
    let j = {}; try { j = JSON.parse(xhr.responseText); } catch { /* 忽略 */ }
    if (xhr.status !== 200) { txt.textContent = "上傳失敗：" + (j.error || xhr.status); return; }
    txt.textContent = "上傳完成，按「開始匯入」。";
    $("#imp-path").value = j.path;
    if (!$("#imp-name").value) $("#imp-name").value = nameFromFile(safe);
    loadImports();
    runIdentify();
  };
  xhr.onerror = () => { txt.textContent = "上傳中斷"; };
  xhr.send(f);
});
function importBody() {
  return { name: $("#imp-name").value.trim(), path: $("#imp-path").value.trim(), system: $("#imp-system").value.trim(),
    num_ctx: +$("#imp-ctx").value || null, quantize: $("#imp-quant").value || null };
}
$("#import-form").addEventListener("submit", async e => {
  e.preventDefault();
  const body = importBody();
  if (!body.path) { toast(impMode === "upload" ? "請先選檔案上傳" : "請填主機上的路徑", true); return; }
  try { const r = await api("/api/models/import", { body }); watchJob(r.id); toast("開始匯入，右上角可以看進度"); }
  catch (err) { toast(err.message, true); }
});

// ═════════ 背景工作 ═════════
const watching = new Map();
async function refreshJobs() {
  try {
    const r = await api("/api/jobs");
    S.jobs = r.jobs;
    for (const j of S.jobs) if (j.state === "running" && j.kind !== "term") watchJob(j.id);
    renderJobs();
  } catch { /* 忽略 */ }
}
async function watchJob(id) {
  if (watching.has(id)) return;
  const w = { offset: 0, log: "", prog: null, done: false };
  watching.set(id, w);
  if (!S.jobs.find(j => j.id === id)) { await refreshJobs(); }
  for (let tries = 0; tries < 200 && !w.done; tries++) {
    try {
      const r = await api(`/api/jobs/stream?id=${id}&from=${w.offset}`, { raw: true });
      for await (const ev of lines(r)) {
        if (ev.t === "hb") continue;
        if (ev.t === "end") {
          w.done = true;
          const j = S.jobs.find(x => x.id === id);
          if (j) { j.state = ev.state; j.result = ev.result; }
          if (j && j.kind !== "term") {
            if (ev.state === "done") toast(j.title + " 完成");
            else if (ev.state === "error") toast(j.title + " 失敗：" + (ev.result?.error || ""), true);
            loadModels();
          }
          break;
        }
        w.offset++;
        if (ev.t === "p") w.prog = ev;
        if (ev.t === "o") w.log = (w.log + ev.d).slice(-4000);
        scheduleJobs();
      }
    } catch (err) {
      if (/找不到/.test(err.message)) { w.done = true; break; }
      await sleep(2000);
    }
  }
  renderJobs();
}
let jobsRaf = 0;
function scheduleJobs() { if (!jobsRaf) jobsRaf = later(() => { jobsRaf = 0; renderJobs(); }); }
function renderJobs() {
  const run = S.jobs.filter(j => j.state === "running").length;
  const jb = $("#jobs-badge"); jb.hidden = !run; jb.textContent = run;
  if ($("#jobs-drawer").hidden) return;
  const label = { running: "進行中", done: "完成", error: "失敗", killed: "已停止" };
  $("#jobs-list").innerHTML = !S.jobs.length ? `<div class="empty-li">沒有背景工作</div>` : S.jobs.map(j => {
    const w = watching.get(j.id) || {}, p = w.prog || j.progress;
    const bar = p && p.total ? `<div class="bar"><i style="width:${pct(p.completed || 0, p.total)}%"></i></div>
      <div class="li-sub">${esc(p.status)} · ${gb(p.completed || 0)} / ${gb(p.total)}</div>` : p ? `<div class="li-sub">${esc(p.status)}</div>` : "";
    const err = j.result?.error ? `<div class="li-sub" style="color:var(--danger)">${esc(j.result.error)}</div>` : "";
    const exit = j.kind === "term" && j.state !== "running" && j.result?.exit != null ? ` · 結束碼 ${j.result.exit}` : "";
    return `<div class="li job" style="display:block"><div style="display:flex;justify-content:space-between;gap:8px">
      <div class="li-title">${esc(j.kind === "term" ? "> " + j.title : j.title)}</div>
      ${j.state === "running" ? `<button class="btn sm danger" data-kill="${j.id}">停止</button>` : ""}</div>
      <div class="li-sub">${label[j.state]}${exit} · ${new Date(j.started * 1000).toLocaleTimeString("zh-TW", { hour12: false })}</div>
      ${j.state === "running" ? bar : ""}${err}${w.log && j.kind !== "term" ? `<div class="job-log">${esc(w.log.slice(-600))}</div>` : ""}</div>`;
  }).join("");
}
$("#jobs-btn").addEventListener("click", () => { $("#jobs-drawer").hidden = false; refreshJobs(); });
$("#jobs-close").addEventListener("click", () => ($("#jobs-drawer").hidden = true));
$("#jobs-drawer").addEventListener("click", async e => {
  if (e.target.id === "jobs-drawer") { $("#jobs-drawer").hidden = true; return; }
  const k = e.target.closest("[data-kill]");
  if (k) { try { await api("/api/jobs/kill", { body: { id: k.dataset.kill } }); } catch (err) { toast(err.message, true); } refreshJobs(); }
});
$("#jobs-clear").addEventListener("click", async () => { await api("/api/jobs/clear", { body: {} }).catch(() => {}); refreshJobs(); });

// ═════════ 對話 ═════════
const C = { messages: [], busy: false, ctrl: null, attach: [] };
function saveChat() {
  ls.set("chat", {
    model: $("#chat-model").value, system: $("#chat-system").value, think: $("#chat-think").checked, status: $("#chat-status").checked,
    temp: $("#chat-temp").value, ctx: $("#chat-ctx").value,
    messages: C.messages.map(m => ({ role: m.role, content: m.content, thinking: m.thinking, stats: m.stats, error: m.error, nimg: m.images?.length || m.nimg })),
  });
}
function restoreChat() {
  const c = ls.get("chat", null);
  if (!c) return;
  $("#chat-system").value = c.system || ""; $("#chat-think").checked = c.think !== false; $("#chat-status").checked = !!c.status;
  $("#chat-temp").value = c.temp || 0.7; $("#chat-temp-v").textContent = $("#chat-temp").value; $("#chat-ctx").value = c.ctx || "";
  C.messages = c.messages || [];
  C.savedModel = c.model;
  renderAllMsgs();
}
function fillChatModels() {
  const sel = $("#chat-model"), cur = sel.value || C.savedModel;
  const names = (S.models?.installed || []).map(m => m.name).filter(n => !isEmbed(n));
  sel.innerHTML = names.length ? names.map(n => `<option>${esc(n)}</option>`).join("") : `<option value="">（還沒有模型，先到「模型」下載）</option>`;
  if (names.includes(cur)) sel.value = cur;
  onModelChange();
}
async function onModelChange() {
  const name = $("#chat-model").value;
  saveChat();
  if (!name) { $("#chat-caps").textContent = ""; $("#attach-btn").hidden = true; return; }
  if (!S.caps[name]) { try { S.caps[name] = await api("/api/models/show?name=" + encodeURIComponent(name)); } catch { S.caps[name] = { capabilities: [] }; } }
  const info = S.caps[name], caps = info.capabilities || [];
  $("#attach-btn").hidden = !caps.includes("vision");
  $("#chat-think").disabled = !caps.includes("thinking");
  const zh = { completion: "對話", vision: "看圖", tools: "工具", thinking: "思考", embedding: "嵌入", insert: "補全" };
  $("#chat-caps").textContent = `能力：${caps.map(c => zh[c] || c).join("、") || "—"}${info.context_length ? ` · 最長上下文 ${info.context_length.toLocaleString()}` : ""}`;
}
$("#chat-model").addEventListener("change", onModelChange);
$("#chat-opt-btn").addEventListener("click", () => ($("#chat-opts").hidden = !$("#chat-opts").hidden));
$("#chat-temp").addEventListener("input", () => { $("#chat-temp-v").textContent = $("#chat-temp").value; saveChat(); });
["#chat-system", "#chat-think", "#chat-status", "#chat-ctx"].forEach(s => $(s).addEventListener("change", saveChat));
$("#chat-new").addEventListener("click", () => {
  if (C.busy) return;
  if (C.messages.length && !confirm("清掉目前的對話，開新的？")) return;
  C.messages = []; saveChat(); renderAllMsgs();
});
function chatWith(name) {
  switchTab("chat");
  const sel = $("#chat-model");
  if (![...sel.options].some(o => o.value === name)) fillChatModels();
  sel.value = name; onModelChange();
  $("#chat-input").focus();
}

// 簡單的 Markdown：程式碼區塊、行內程式碼、粗體、標題、清單、表格
function inline(s) {
  return s.split(/(`[^`]+`)/g).map(p => p.startsWith("`") && p.endsWith("`") && p.length > 1
    ? "<code>" + esc(p.slice(1, -1)) + "</code>"
    : esc(p).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")).join("");
}
function codeBlock(code, lang) {
  return `<div class="code"><div class="code-bar"><span>${esc(lang || "程式碼")}</span><span>
    <button type="button" class="btn sm" data-copy>複製</button> <button type="button" class="btn sm" data-toterm>送到終端機</button></span></div>
    <pre><code>${esc(code)}</code></pre></div>`;
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
  // 有些模型把思考寫在內容裡的 <think>…</think>
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
  fillMsg(d, m, live);
  return d;
}
function fillMsg(d, m, live) {
  if (m.role === "user") {
    d.innerHTML = (m.urls || []).map(u => `<img src="${esc(u)}" alt="">`).join("") +
      (m.nimg && !m.images ? `<div class="muted small">（附了 ${m.nimg} 張圖）</div>` : "") + esc(m.content);
    return;
  }
  const { content, thinking } = splitThink(m);
  let h = "";
  if (thinking) h += `<details class="think" ${live && !content ? "open" : ""}><summary>思考過程</summary><div>${esc(thinking)}</div></details>`;
  h += `<div class="body ${live ? "cursor" : ""}">${md(content)}</div>`;
  if (m.error) h += `<div class="err">${esc(m.error)}</div>`;
  if (m.stopped) h += `<div class="stats">已停止</div>`;
  if (m.stats) h += `<div class="stats">${esc(m.stats)}</div>`;
  d.innerHTML = h;
}
function renderAllMsgs() {
  const box = $("#msgs");
  $$(".msg", box).forEach(x => x.remove());
  $("#chat-empty").hidden = !!C.messages.length;
  for (const m of C.messages) box.appendChild(msgEl(m));
  scrollMsgs(true);
}
function scrollMsgs(force) {
  const b = $("#msgs");
  if (force || b.scrollHeight - b.scrollTop - b.clientHeight < 120) b.scrollTop = b.scrollHeight;
}
function statusForAI() {
  const s = S.status;
  if (!s) return "";
  const lite = {
    hostname: s.hostname, os: s.os, cpu_percent: s.cpu, cpu: s.cpu_name, uptime_sec: s.uptime,
    memory: s.mem && { used_gb: +(s.mem.used / GiB).toFixed(1), total_gb: +(s.mem.total / GiB).toFixed(1) },
    gpus: s.gpus.map(g => ({ name: g.name, util: g.util, vram_used_gb: +(g.mem_used / GiB).toFixed(1), vram_total_gb: +(g.mem_total / GiB).toFixed(1), temp_c: g.temp, power_w: g.power })),
    disks: s.disks.map(d => ({ mount: d.mount, free_gb: +((d.total - d.used) / GiB).toFixed(0), total_gb: +(d.total / GiB).toFixed(0) })),
    ollama_loaded: (s.ollama?.loaded || []).map(m => ({ name: m.name, size_gb: +(m.size / GiB).toFixed(1), in_vram_percent: m.size ? Math.round(pct(m.size_vram, m.size)) : 0 })),
  };
  return "以下是這台主機現在的狀態（JSON），使用者問到機器的事情時參考：\n" + JSON.stringify(lite);
}
async function sendChat(text) {
  const model = $("#chat-model").value;
  if (!model) { toast("先到「模型」下載一個模型", true); return; }
  const user = { role: "user", content: text };
  if (C.attach.length) { user.images = C.attach.map(a => a.b64); user.urls = C.attach.map(a => a.url); }
  C.attach = []; renderAttach();
  const ai = { role: "assistant", content: "", thinking: "" };
  C.messages.push(user, ai);
  $("#chat-empty").hidden = true;
  const box = $("#msgs");
  box.appendChild(msgEl(user));
  const el = msgEl(ai, true);
  box.appendChild(el);
  scrollMsgs(true);

  const sys = [$("#chat-system").value.trim(), $("#chat-status").checked ? statusForAI() : ""].filter(Boolean).join("\n\n");
  const msgs = sys ? [{ role: "system", content: sys }] : [];
  for (const m of C.messages.slice(0, -1)) {
    const x = { role: m.role, content: m.role === "assistant" ? splitThink(m).content : m.content };
    if (m.images) x.images = m.images;
    msgs.push(x);
  }
  const options = { temperature: +$("#chat-temp").value };
  if (+$("#chat-ctx").value) options.num_ctx = +$("#chat-ctx").value;
  const body = { model, messages: msgs, options };
  const caps = S.caps[model]?.capabilities || [];
  if (caps.includes("thinking")) {
    const on = $("#chat-think").checked;
    body.think = /^gpt-oss/.test(model) ? (on ? "medium" : "low") : on;
  }
  C.busy = true; C.ctrl = new AbortController();
  $("#chat-send").hidden = true; $("#chat-stop").hidden = false;
  let raf = 0;
  const paint = () => { if (!raf) raf = later(() => { raf = 0; fillMsg(el, ai, true); scrollMsgs(); }); };
  try {
    const r = await api("/api/chat", { body, raw: true, signal: C.ctrl.signal });
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
    C.busy = false; C.ctrl = null;
    $("#chat-send").hidden = false; $("#chat-stop").hidden = true;
    clearTimeout(raf);
    fillMsg(el, ai, false); scrollMsgs(); saveChat();
  }
}
$("#composer").addEventListener("submit", e => {
  e.preventDefault();
  const ta = $("#chat-input"), text = ta.value.trim();
  if (!text || C.busy) return;
  ta.value = ""; autosize(ta);
  sendChat(text);
});
$("#chat-input").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
});
$("#chat-input").addEventListener("input", e => autosize(e.target));
$("#chat-stop").addEventListener("click", () => C.ctrl?.abort());
$("#attach-input").addEventListener("change", async e => {
  for (const f of [...e.target.files].slice(0, 8)) {
    const url = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(f); });
    C.attach.push({ b64: String(url).split(",")[1], url });
  }
  e.target.value = ""; renderAttach();
});
function renderAttach() {
  $("#attach-list").innerHTML = C.attach.map((a, i) => `<span><img src="${a.url}" alt=""><button type="button" data-rm="${i}">×</button></span>`).join("");
}
$("#attach-list").addEventListener("click", e => { const b = e.target.closest("[data-rm]"); if (b) { C.attach.splice(+b.dataset.rm, 1); renderAttach(); } });
$("#msgs").addEventListener("click", e => {
  const code = e.target.closest(".code");
  if (!code) return;
  const text = $("pre", code).textContent;
  if (e.target.closest("[data-copy]")) copyText(text);
  if (e.target.closest("[data-toterm]")) {
    switchTab("term");
    const ta = $("#term-input");
    ta.value = text.replace(/\n+$/, ""); autosize(ta); ta.focus();
    toast("指令已放進終端機，確認後按執行");
  }
});

// ═════════ 終端機 ═════════
const T = { inited: false, shells: [], cwd: "", hist: ls.get("termHist", []), hi: -1, running: null, blocks: new Map() };
const QUICK = {
  windows: ["nvidia-smi", "ollama list", "ollama ps", "ipconfig", "dir", "tasklist /fi \"memusage gt 500000\"", "ping -n 4 8.8.8.8"],
  other: ["nvidia-smi", "ollama list", "ollama ps", "ip -br a", "ls -la", "df -h", "free -h", "top -bn1 | head -20", "systemctl status ollama --no-pager"],
};
async function initTerm() {
  if (T.inited) { $("#term-input").focus(); return; }
  T.inited = true;
  let info;
  try { info = await api("/api/term/info"); }
  catch (err) { $("#term-out").innerHTML = `<div class="blk-o">${esc(err.message)}</div>`; $("#term-form").hidden = true; return; }
  T.shells = info.shells; S.plat = info.platform;
  const sel = $("#term-shell");
  sel.innerHTML = info.shells.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  const saved = ls.get("termShell", "");
  if (info.shells.some(s => s.id === saved)) sel.value = saved;
  T.cwd = ls.get("termCwd", "") || info.home;
  showCwd();
  $("#term-quick").innerHTML = (QUICK[info.platform] || QUICK.other).map(q => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("");
  // 重新整理頁面後，把之前跑過的指令接回來
  const cleared = ls.get("termCleared", 0);
  const r = await api("/api/jobs").catch(() => ({ jobs: [] }));
  const old = r.jobs.filter(j => j.kind === "term" && j.started > cleared).reverse().slice(-10);
  for (const j of old) { const blk = addBlock(j.id, j.meta.shell, j.meta.cwd, j.title, j.started); streamTerm(j.id, blk, j.state === "running"); }
  $("#term-input").focus();
}
const promptOf = (shell, cwd) => shell === "cmd" ? cwd + ">" : /powershell|pwsh/.test(shell) ? "PS " + cwd + ">" : "$";
function showCwd() {
  $("#term-cwd").textContent = T.cwd;
  $("#term-prompt").textContent = $("#term-shell").value === "cmd" ? ">" : /power|pwsh/.test($("#term-shell").value) ? "PS>" : "$";
}
$("#term-shell").addEventListener("change", () => { ls.set("termShell", $("#term-shell").value); showCwd(); });
$("#term-cwd").addEventListener("click", () => copyText(T.cwd));
$("#term-quick").addEventListener("click", e => { const c = e.target.closest("[data-q]"); if (c && !T.running) runCmd(c.dataset.q); });
$("#term-clear").addEventListener("click", () => { $("#term-out").innerHTML = ""; T.blocks.clear(); ls.set("termCleared", Math.floor(Date.now() / 1000)); });

function addBlock(id, shell, cwd, cmd, started) {
  const d = document.createElement("div");
  d.className = "blk";
  d.innerHTML = `<div class="blk-h"><span>${esc(promptOf(shell, cwd))} ${esc(cmd)}</span><span class="st">執行中…</span></div><div class="blk-o"></div>`;
  $("#term-out").appendChild(d);
  const blk = { el: d, o: $(".blk-o", d), st: $(".st", d), raw: "", started: started || Date.now() / 1000 };
  T.blocks.set(id, blk);
  return blk;
}
const ANSI = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[=>]/g;
function clean(s) {
  s = s.replace(ANSI, "");
  // 進度條用 \r 回到行首覆寫，只留最後一次
  return s.split("\n").map(l => { const k = l.lastIndexOf("\r"); return k >= 0 ? l.slice(k + 1) : l; }).join("\n");
}
function paintBlock(blk) {
  if (blk.raf) return;
  blk.raf = later(() => {
    blk.raf = 0;
    const out = $("#term-out"), stick = out.scrollHeight - out.scrollTop - out.clientHeight < 80;
    const raw = blk.raw.length > 400000 ? "…（前面太長省略）\n" + blk.raw.slice(-400000) : blk.raw;
    blk.o.textContent = clean(raw);
    if (stick) out.scrollTop = out.scrollHeight;
  });
}
async function streamTerm(id, blk, live) {
  if (live) setRunning(id);
  let offset = 0;
  for (let tries = 0; tries < 300; tries++) {
    try {
      const r = await api(`/api/jobs/stream?id=${id}&from=${offset}`, { raw: true });
      for await (const ev of lines(r)) {
        if (ev.t === "hb") continue;
        if (ev.t === "end") {
          const res = ev.result || {}, secs = Math.max(0, Math.round(Date.now() / 1000 - blk.started));
          blk.st.textContent = ev.state === "killed" ? "已停止" : `結束碼 ${res.exit ?? "—"}${live ? ` · ${secs} 秒` : ""}`;
          blk.st.classList.toggle("bad", ev.state !== "done");
          if (res.cwd && T.running === id) { T.cwd = res.cwd; ls.set("termCwd", T.cwd); showCwd(); }
          if (T.running === id) setRunning(null);
          paintBlock(blk);
          return;
        }
        offset++;
        if (ev.t === "o") { blk.raw += ev.d; paintBlock(blk); }
      }
    } catch (err) {
      if (/找不到/.test(err.message)) { blk.st.textContent = "（伺服器重開過，輸出不見了）"; if (T.running === id) setRunning(null); return; }
      await sleep(1500);
    }
  }
}
function setRunning(id) {
  T.running = id;
  $("#term-kill").hidden = !id;
  $("#term-run").textContent = id ? "送出" : "執行";
  $("#term-input").placeholder = id ? "程式在等輸入的話，打在這裡按 Enter" : "輸入指令，Enter 執行";
}
async function runCmd(cmd) {
  const shell = $("#term-shell").value;
  if (T.running) {
    const blk = T.blocks.get(T.running);
    try { await api("/api/term/input", { body: { id: T.running, text: cmd } }); if (blk) { blk.raw += cmd + "\n"; paintBlock(blk); } }
    catch (err) { toast(err.message, true); }
    return;
  }
  if (shell === "cmd" && cmd.includes("\n")) cmd = cmd.split("\n").map(x => x.trim()).filter(Boolean).join(" & ");
  T.hist = T.hist.filter(h => h !== cmd).concat(cmd).slice(-100); ls.set("termHist", T.hist); T.hi = -1;
  try {
    const r = await api("/api/term/run", { body: { cmd, cwd: T.cwd, shell } });
    const blk = addBlock(r.id, shell, r.cwd, cmd);
    const out = $("#term-out"); out.scrollTop = out.scrollHeight;
    streamTerm(r.id, blk, true);
  } catch (err) { toast(err.message, true); }
}
$("#term-form").addEventListener("submit", e => {
  e.preventDefault();
  const ta = $("#term-input"), cmd = ta.value;
  if (!cmd.trim() && !T.running) return;
  ta.value = ""; autosize(ta);
  runCmd(T.running ? cmd : cmd.trim());
});
$("#term-input").addEventListener("keydown", e => {
  const ta = e.target;
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#term-form").requestSubmit(); return; }
  if (e.key === "c" && e.ctrlKey && T.running && ta.selectionStart === ta.selectionEnd) { e.preventDefault(); $("#term-kill").click(); return; }
  if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !ta.value.includes("\n") && T.hist.length) {
    e.preventDefault();
    if (e.key === "ArrowUp") T.hi = T.hi < 0 ? T.hist.length - 1 : Math.max(0, T.hi - 1);
    else T.hi = T.hi < 0 ? -1 : T.hi + 1 >= T.hist.length ? -1 : T.hi + 1;
    ta.value = T.hi < 0 ? "" : T.hist[T.hi];
  }
});
$("#term-input").addEventListener("input", e => autosize(e.target));
$("#term-kill").addEventListener("click", async () => {
  if (!T.running) return;
  try { await api("/api/jobs/kill", { body: { id: T.running } }); } catch (err) { toast(err.message, true); }
});

// ── 開始 ──
if (TOKEN) {
  api("/api/ping").then(() => fetch("/api/jobs", { headers: { Authorization: "Bearer " + TOKEN } }))
    .then(r => r.ok ? start() : showLogin()).catch(() => showLogin("連不到面板伺服器"));
} else showLogin();
