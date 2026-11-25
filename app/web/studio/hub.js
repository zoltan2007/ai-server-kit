"use strict";
// 模型庫：分類、適合本機排行、能力分數排行、下載。資料與「適不適合」都由主機算好（/api/hub），這裡只負責畫。
// 下載：語言模型走 Ollama（跟面板同一套下載工作），生圖／影片／配音走 ComfyUI 的模型資料夾。只有管理員看得到下載鈕。

const HUB = { data: null, cat: ls.get("hub.cat", "llm"), rank: ls.get("hub.rank", "fit"), profile: ls.get("hub.profile", "host"),
  pick: {}, jobs: {}, loading: false };
const FIT_CLS = ["no", "slow", "split", "ok"];
const gb = n => (n == null ? "—" : (n >= 100 ? Math.round(n) : (+n).toFixed(1)) + " GB");

function inkFor(hex) {      // 頭像底色夠亮就用黑字
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return "#fff";
  const n = parseInt(m[1], 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  return (0.299 * r + 0.587 * g + 0.114 * b) > 165 ? "#111" : "#fff";
}
function avatar(v) {
  const name = v?.name || "?", letter = v?.letter || (name.match(/[A-Za-z0-9]/) || [name[0]])[0].toUpperCase();
  const c = /^#[0-9a-f]{6}$/i.test(v?.color || "") ? v.color : "#7b8190";
  return `<i class="hav" style="background:${c};color:${inkFor(c)}" aria-hidden="true">${esc(letter)}</i>`;
}

function openHub() {
  if (typeof closeDash === "function") closeDash();
  if (typeof leaveRoomView === "function") leaveRoomView();
  document.body.classList.add("in-hub");
  $("#models-btn").classList.add("on");
  $("#title").value = "模型庫"; $("#title").readOnly = true;
  loadHub();
  if (!wide()) openSide(false);
}
function closeHub(back = false) {
  const was = document.body.classList.contains("in-hub");
  document.body.classList.remove("in-hub");
  $("#models-btn").classList.remove("on");
  $("#title").readOnly = false;
  if (back && was) { if (S.cur) renderCur(); else newSession(); }
}
async function loadHub() {
  if (HUB.loading) return;
  HUB.loading = true;
  if (!HUB.data) $("#hub").innerHTML = `<div class="muted small">載入中…</div>`;
  try {
    HUB.data = await api("/api/hub?profile=" + encodeURIComponent(HUB.profile));
    if (!HUB.data.categories.some(c => c.id === HUB.cat)) HUB.cat = HUB.data.categories[0].id;
    renderHub();
    if (HUB.data.admin) resumeJobs();
  } catch (err) { $("#hub").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  finally { HUB.loading = false; }
}

function renderHub() {
  const d = HUB.data, cat = d.categories.find(c => c.id === HUB.cat), src = d.sources[cat.score_src];
  const byId = Object.fromEntries(d.models.map(m => [m.id, m])), order = d.ranks[cat.id]?.[HUB.rank] || [];
  const comfy = d.comfy || {}, needComfy = cat.runtime === "comfy";
  const prof = d.profile, cnt = {};
  for (const g of prof.gpus || []) { const k = `${g.name.replace(/^NVIDIA\s+(GeForce\s+)?/i, "")} ${g.gb} GB`; cnt[k] = (cnt[k] || 0) + 1; }
  const gpus = Object.entries(cnt).map(([k, n]) => esc((n > 1 ? n + "× " : "") + k)).join("、") || "沒有偵測到顯示卡";
  $("#hub").innerHTML = `
    <div class="hub-head">
      <div class="seg" role="group" aria-label="用哪台機器算">${d.profiles.map(p => `<button data-prof="${esc(p.id)}" class="${d.profile_id === p.id ? "on" : ""}">${esc(p.id === "host" ? "這台主機" : "AI 主機")}</button>`).join("")}</div>
      <span class="muted small hub-spec">${gpus} · 記憶體 ${prof.ram || "—"} GB</span>
      <span class="grow"></span>
      <span class="pill ${comfy.running ? "ok" : comfy.installed ? "warn" : ""}" title="生圖、影片、配音要用 ComfyUI">ComfyUI ${comfy.running ? "執行中" : comfy.installed ? "已安裝、沒在跑" : "沒裝"}</span>
    </div>
    <div class="hub-cats" role="tablist">${d.categories.map(c => `<button role="tab" aria-selected="${c.id === cat.id}" data-cat="${esc(c.id)}" class="${c.id === cat.id ? "on" : ""}">${esc(c.name)}<small>${(d.ranks[c.id]?.fit || []).length}</small></button>`).join("")}</div>
    <div class="hub-bar">
      <div class="seg" role="group" aria-label="排行方式"><button data-rank="fit" class="${HUB.rank === "fit" ? "on" : ""}">適合本機</button><button data-rank="score" class="${HUB.rank === "score" ? "on" : ""}">能力分數</button></div>
      <span class="muted small">${HUB.rank === "fit"
        ? "先看跑不跑得動：全部放得進顯卡的排前面（拆到好幾張也算），再來是要借記憶體的、放不下的；同一級裡分數高的在前。顯存需求是粗估。"
        : src ? `分數：<a href="${esc(src.url)}" target="_blank" rel="noopener noreferrer">${esc(src.name)}</a>（${esc(src.date)}）${src.lower_is_better ? "，越低越好" : ""}` : "這一類還沒有可比較的公開分數"}</span>
      ${d.admin && (cat.id === "llm" || cat.id === "code") ? `<a class="btn sm" href="../#models">搜尋全網其他模型</a>` : ""}
    </div>
    ${needComfy && !comfy.installed ? `<div class="stat hub-note"><b>這一類要用 ComfyUI 跑</b><p class="muted small">這台還沒裝。Linux 重新跑安裝檔時加 <code>--comfyui</code> 就會一起裝好；裝好之後這裡的「下載」會把模型放進 ComfyUI 的模型資料夾。</p></div>` : ""}
    <div class="hub-grid">${order.map((id, i) => card(byId[id], i + 1, cat)).join("") || `<div class="muted small">這一類還沒有資料</div>`}</div>
    <p class="muted small hub-foot">資料整理日期：${esc(d.updated || "—")}。檔案大小是實際下載量；「運行」是跑起來大約要的顯存（粗估，context 越長越多）。</p>`;
}

function card(m, rank, cat) {
  if (!m) return "";
  const d = HUB.data, v0 = HUB.pick[m.id] ?? m.best, v = m.variants[v0] || m.variants[m.best], vendor = d.vendors[m.vendor];
  const s = (m.score || {})[cat.id], job = HUB.jobs[m.id + "|" + v.label];
  const nodeUrl = (v.source.needs || "").match(/https:\/\/github\.com\/[\w.-]+\/[\w.-]+/)?.[0];
  const needs = v.source.kind === "node" ? `<p class="hub-warn">要先裝 ComfyUI 擴充節點${nodeUrl ? `（<a href="${esc(nodeUrl)}" target="_blank" rel="noopener noreferrer">${esc(nodeUrl.split("/").pop())}</a>）` : `：${esc(v.source.needs)}`}，模型照節點的說明放。面板不替它下載。</p>` : "";
  let act = "";
  if (v.source.kind === "node") act = `<span class="muted small">只列資訊</span>`;
  else if (v.source.gated) act = `<span class="muted small">要先登入 Hugging Face 同意授權，面板還不能下載</span>`;
  else if (v.have) act = `<span class="pill ok">已下載</span>${(d.gen_ids || []).includes(m.id) ? `<button class="btn sm primary" data-use="${esc(m.id)}" data-kind="${esc(cat.id)}">在 Kiki 裡用</button>` : ""}${d.admin && v.source.kind === "comfy" ? `<button class="btn sm" data-rm="${esc(m.id)}">刪除</button>` : ""}`;
  else if (job && job.state === "running") act = `<span class="hub-prog"><i style="width:${job.pct || 0}%"></i></span><span class="muted small">${esc(job.text || "下載中")}</span>`;
  else if (!d.admin) act = `<span class="muted small">下載要請管理員</span>`;
  else if (v.source.kind === "comfy" && !d.comfy.installed) act = `<span class="muted small">要先裝 ComfyUI</span>`;
  else act = `<button class="btn sm primary" data-dl="${esc(m.id)}">下載 ${gb(v.file_gb)}</button>`;
  if (job && job.state === "error") act += `<span class="hub-warn">${esc(job.text)}</span>`;
  return `<article class="hcard" data-id="${esc(m.id)}">
    <div class="hc-top"><b class="hc-rank">${rank}</b>${avatar(vendor)}<div class="hc-name"><b>${esc(m.name)}</b><span>${esc(vendor?.name || m.vendor)}${m.license ? " · " + esc(m.license) : ""}${m.released ? " · " + esc(m.released) : ""}</span></div>
      ${s && typeof s.value === "number" ? `<span class="hc-score" title="${esc((d.sources[s.src]?.name || "") + (s.note ? "，測的是 " + s.note : ""))}">${s.value}</span>` : `<span class="hc-score none" title="這個排行查不到它的分數">—</span>`}</div>
    ${m.desc ? `<p class="hc-desc">${esc(m.desc)}</p>` : ""}
    <div class="hc-stats"><span><small>檔案</small>${gb(v.file_gb)}</span><span><small>運行</small>${gb(v.run_gb)}</span><span><small>執行</small>${v.source.kind === "ollama" ? "Ollama" : v.source.kind === "node" ? "ComfyUI＋節點" : "ComfyUI"}</span></div>
    ${m.variants.length > 1 ? `<select class="hc-var" data-var="${esc(m.id)}" aria-label="版本">${m.variants.map((x, i) => `<option value="${i}" ${i === v0 ? "selected" : ""}>${esc(x.label)} · ${gb(x.file_gb)}${x.fit === 3 ? "" : " · " + ["放不下", "會很慢", "拆卡"][x.fit]}</option>`).join("")}</select>` : ""}
    <p class="hc-fit ${FIT_CLS[v.fit]}">${esc(v.fit_text)}${v.run_note ? "；" + esc(v.run_note) : ""}</p>
    ${v.note ? `<p class="hub-warn">${esc(v.note)}</p>` : ""}${v.warn ? `<p class="hub-warn">${esc(v.warn)}</p>` : ""}${needs}
    <div class="hc-act">${act}<span class="grow"></span>${m.url ? `<a class="hc-link" href="${esc(m.url)}" target="_blank" rel="noopener noreferrer">官方頁面</a>` : ""}</div>
  </article>`;
}

// ── 下載進度：重用面板的背景工作（/api/jobs/stream） ──
async function follow(key, id) {
  HUB.jobs[key] = { state: "running", pct: 0, text: "準備中", id };
  renderHub();
  let last = 0;
  try {
    const r = await api(`/api/jobs/stream?id=${id}&from=0`, { raw: true });
    for await (const ev of lines(r)) {
      const j = HUB.jobs[key];
      if (ev.t === "p") { j.pct = ev.total ? Math.min(100, ev.completed / ev.total * 100) : j.pct; j.text = `${ev.status || ""}${ev.total ? " " + j.pct.toFixed(0) + "%" : ""}`; }
      if (ev.t === "end") {
        j.state = ev.state === "done" ? "done" : "error"; j.text = ev.result?.error || (ev.state === "done" ? "完成" : "沒有完成");
        toast(j.state === "done" ? "下載完成" : "下載失敗：" + j.text, j.state !== "done");
        break;
      }
      if (performance.now() - last > 400 && document.body.classList.contains("in-hub")) { last = performance.now(); renderHub(); }
    }
  } catch (err) { HUB.jobs[key] = { state: "error", text: err.message }; }
  if (document.body.classList.contains("in-hub")) loadHub();
  if (typeof loadModels === "function") loadModels();
}
async function resumeJobs() {      // 重新整理頁面後，還在跑的下載接著顯示進度
  try {
    for (const j of (await api("/api/jobs")).jobs || []) {
      if (j.state !== "running") continue;
      let key = null;
      if (j.kind === "hub") key = j.meta.model + "|" + j.meta.variant;
      if (j.kind === "pull") { const m = HUB.data.models.find(x => x.variants.some(v => v.source.kind === "ollama" && v.source.name === j.meta.model)); if (m) key = m.id + "|" + m.variants.find(v => v.source.name === j.meta.model).label; }
      if (key && !HUB.jobs[key]) follow(key, j.id);
    }
  } catch { /* 一般帳號看不到工作清單 */ }
}

$("#hub").addEventListener("click", async e => {
  const t = e.target.closest("[data-cat], [data-rank], [data-prof], [data-dl], [data-rm]");
  if (!t) return;
  if (t.dataset.cat) { HUB.cat = t.dataset.cat; ls.set("hub.cat", HUB.cat); renderHub(); return; }
  if (t.dataset.rank) { HUB.rank = t.dataset.rank; ls.set("hub.rank", HUB.rank); renderHub(); return; }
  if (t.dataset.prof) { HUB.profile = t.dataset.prof; ls.set("hub.profile", HUB.profile); loadHub(); return; }
  const id = t.dataset.dl || t.dataset.rm, m = HUB.data.models.find(x => x.id === id), v = m.variants[HUB.pick[id] ?? m.best];
  if (t.dataset.dl) {
    if (v.fit === 0 && !confirm(`${m.name}（${v.label}）這台應該跑不動，還是要下載 ${gb(v.file_gb)}？`)) return;
    t.disabled = true;
    try { const r = await api("/api/hub/download", { body: { id, variant: v.label } }); follow(id + "|" + v.label, r.id); }
    catch (err) { toast(err.message, true); t.disabled = false; }
    return;
  }
  if (!confirm(`刪除 ${m.name}（${v.label}）的模型檔？其他已下載模型也在用的檔案會留著。`)) return;
  try { const r = await api("/api/hub/remove", { body: { id, variant: v.label } }); toast(`刪了 ${r.removed.length} 個檔案${r.kept ? `，${r.kept} 個共用的留著` : ""}`); loadHub(); }
  catch (err) { toast(err.message, true); }
});
$("#hub").addEventListener("change", e => {
  const s = e.target.closest("[data-var]");
  if (s) { HUB.pick[s.dataset.var] = +s.value; renderHub(); }
});

// 側欄「模型」打開模型庫（換 Kiki 的模型還是用輸入框下面的模型鈕）
const _modelsBtn = $("#models-btn"), _modelsBtnNew = _modelsBtn.cloneNode(true);
_modelsBtn.replaceWith(_modelsBtnNew);
_modelsBtnNew.addEventListener("click", () => (document.body.classList.contains("in-hub") ? closeHub(true) : openHub()));
const _nsHub = newSession; newSession = function (extra) { closeHub(); return _nsHub(extra); };
const _osHub = openSession; openSession = async function (id, track) { closeHub(); return _osHub(id, track); };
if (typeof openDash === "function") { const _od = openDash; openDash = function () { closeHub(); return _od(); }; }
if (typeof openRoom === "function") { const _or = openRoom; openRoom = async function (id) { closeHub(); return _or(id); }; }
SLASH.push({ k: "/模型庫", d: "看分類模型排行、下載模型", run: openHub });
