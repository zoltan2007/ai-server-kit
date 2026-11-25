// 創作：右欄用本機的 ComfyUI 生圖、生影片。生成中看得到進度和即時預覽；成品貼進對話，也留在右欄。
// 伺服器只收提示詞和幾個數字，工作流程是內建的；成品只有本人拿得到（/api/gen/file 要帶登入憑證，所以用 blob 網址顯示）。
const GEN = {
  kind: ls.get("gen.kind", "image"), model: ls.get("gen.model", {}), ratio: ls.get("gen.ratio", "1:1"), sec: ls.get("gen.sec", {}),
  info: null, loading: null, draft: "", active: new Map(), blobs: new Map(), view: "", timer: 0,
};
const GEN_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4"];
const GEN_EXT = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "video/mp4": "mp4", "video/webm": "webm" };
const genWord = k => (k === "video" ? "影片" : "圖片");
// 對話紀錄是使用者自己可以改的資料：數字一律轉成數字再放進頁面
const genNum = v => (Number.isFinite(+v) ? +v : 0);
const genAR = g => `${genNum(g.w) || 1}/${genNum(g.h) || 1}`;

async function genInfo(force) {
  if (GEN.info && !force) return GEN.info;
  if (!GEN.loading) GEN.loading = api("/api/gen/models").then(r => (GEN.info = r)).finally(() => { GEN.loading = null; });
  return GEN.loading;
}
const genModels = kind => (GEN.info?.models || []).filter(m => m.kind === kind);
function genCur() { const ms = genModels(GEN.kind); return ms.find(m => m.id === GEN.model[GEN.kind]) || ms[0] || null; }
function genSize(m, ratio) {
  // 跟模型預設尺寸一樣的像素量，換成想要的比例
  const d = m.defaults, l = m.limits, mult = Math.max(8, l.multiple || 16), area = d.width * d.height;
  const [a, b] = ratio.split(":").map(Number), r = a / b;
  const fit = x => Math.max(l.min_side || 256, Math.min(l.max_side || 2048, Math.round(x / mult) * mult));
  return [fit(Math.sqrt(area * r)), fit(Math.sqrt(area / r))];
}
function genSecs(m) {
  const d = m.defaults, max = (m.limits.max_length || d.length) / d.fps, def = Math.round(d.length / d.fps * 10) / 10;
  const opts = [...new Set([2, 3, 5, 8, 10, 15].filter(s => s < max - 0.05).concat([Math.floor(max * 10) / 10, def]))].sort((a, b) => a - b);
  return { opts, def };
}
// 存對話時只留這些（進度、預覽是暫時的）
function genKeep(g) {
  const out = {};
  for (const k of ["id", "kind", "model", "variant", "name", "prompt", "status", "error", "w", "h", "length", "fps", "seed", "steps", "mime"]) if (g[k] != null) out[k] = g[k];
  if (out.status === "sending") out.status = "error", out.error = "送出時中斷了";
  return out;
}

// ── 成品的檔案（帶憑證抓，轉成 blob 網址） ──
function genMedia(id) {
  if (!GEN.blobs.has(id)) {
    const p = api("/api/gen/file?id=" + encodeURIComponent(id), { raw: true }).then(r => r.blob()).then(b => URL.createObjectURL(b));
    p.catch(() => GEN.blobs.delete(id));
    GEN.blobs.set(id, p);
  }
  return GEN.blobs.get(id);
}
async function genPreview(m) {
  try {
    const r = await api("/api/gen/preview?id=" + encodeURIComponent(m.gen.id), { raw: true });
    const url = URL.createObjectURL(await r.blob());
    if (m.gen._pv) URL.revokeObjectURL(m.gen._pv);
    m.gen._pv = url; genPaint(m);
  } catch { /* 預覽拿不到不要緊 */ }
}
async function genDownload(g) {
  try {
    const url = await genMedia(g.id), a = document.createElement("a");
    a.href = url; a.download = `kiki-${g.id}.${GEN_EXT[g.mime] || (g.kind === "video" ? "mp4" : "png")}`;
    document.body.appendChild(a); a.click(); a.remove();
  } catch (e) { toast(e.message, true); }
}

// ── 送出 ──
function genPromptBefore(conv, m) {
  const i = conv.messages.indexOf(m);
  return m.gen.prompt || (i > 0 && conv.messages[i - 1].role === "user" ? conv.messages[i - 1].content : "");
}
async function genStart(prompt, over = {}) {
  if (typeof RM !== "undefined" && RM.cur) { toast("群組裡還不能生成，請到自己的專案用", true); return; }
  try { await genInfo(); } catch (e) { toast(e.message, true); return; }
  const m = over.model ? GEN.info.models.find(x => x.id === over.model) : genCur();
  prompt = (prompt || "").trim();
  if (!m) { toast(`這台還沒有可以用的${genWord(GEN.kind)}模型`, true); return; }
  if (!prompt) { toast("先寫下想生成的內容", true); return; }
  if (!S.cur) newSession();
  const conv = S.cur, adv = over.adv || genAdv(m);
  const [w, h] = over.size || genSize(m, GEN.ratio);
  const body = { model: m.id, variant: over.variant || m.variants[0], prompt, width: w, height: h, ...adv };
  if (m.kind === "video") body.seconds = over.seconds ?? (GEN.sec[m.id] || genSecs(m).def);
  const um = { role: "user", content: prompt, gen: { kind: m.kind } };
  const am = { role: "assistant", by: "kiki", content: `（用 ${m.name} 生成了一${m.kind === "video" ? "段影片" : "張圖"}）`,
    gen: { kind: m.kind, model: m.id, name: m.name, prompt, status: "sending", w, h } };
  conv.messages.push(um, am);
  if (S.cur === conv) { const box = $("#msgs"); box.appendChild(msgEl(um)); box.appendChild(msgEl(am)); $("#empty").hidden = true; scrollMsgs(true); }
  renderGenLive();
  try {
    const r = await api("/api/gen/start", { body });
    Object.assign(am.gen, { id: r.id, status: r.status, w: r.width, h: r.height, seed: r.seed, steps: r.steps, length: r.length, fps: r.fps, variant: r.variant });
    genTrack(am, conv);
  } catch (e) { Object.assign(am.gen, { status: "error", error: e.message }); }
  genPaint(am); renderGenLive(); saveNow(conv);
}
function genAdv(m) {
  const o = {}, st = $("#gen-steps")?.value, sd = $("#gen-seed")?.value, ng = $("#gen-neg");
  if (m.steps && st) o.steps = +st;
  if (sd !== undefined && sd !== "") o.seed = +sd;
  if (m.negative && ng && ng.value.trim()) o.negative = ng.value.trim();
  return o;
}

// ── 進度 ──
function genTrack(m, conv) {
  if (!m.gen?.id) return;
  GEN.active.set(m.gen.id, { m, conv });
  if (!GEN.timer) GEN.timer = setTimeout(genPoll, 700);
}
async function genPoll() {
  GEN.timer = 0;
  for (const [id, { m, conv }] of [...GEN.active]) {
    try {
      const s = await api("/api/gen/status?id=" + encodeURIComponent(id));
      Object.assign(m.gen, { status: s.status, error: s.error || "", _p: s.progress || 0, _step: s.step || "", _q: s.queue || 0, mime: s.mime || m.gen.mime });
      if (s.pn && s.pn !== m.gen._pn) { m.gen._pn = s.pn; genPreview(m); }
      if (!["queued", "running"].includes(s.status)) {
        GEN.active.delete(id);
        if (m.gen._pv) { URL.revokeObjectURL(m.gen._pv); delete m.gen._pv; }
        saveNow(conv);
        if (s.status === "done" && (S.cur !== conv || document.hidden)) toast(`${m.gen.name} 的${genWord(m.gen.kind)}好了，在「${conv.title || "新對話"}」`);
      }
      genPaint(m);
    } catch (e) {
      if (/找不到/.test(e.message)) { GEN.active.delete(id); Object.assign(m.gen, { status: "error", error: e.message }); genPaint(m); saveNow(conv); }
    }
  }
  renderGenLive();
  if (GEN.active.size) GEN.timer = setTimeout(genPoll, document.hidden ? 4000 : 1200);
}

// ── 對話裡的卡片 ──
function genLiveBox(g) {
  const pv = g._pv ? `<img src="${g._pv}" alt="">` : "";
  const txt = g.status === "sending" ? "送出中…" : g.status === "queued" ? (g._q ? `排隊中，前面還有 ${g._q} 個` : "排隊中…")
    : `生成中${g._step ? " " + esc(g._step) : "…"}`;
  return `<div class="gen-live" style="aspect-ratio:${genAR(g)}">${pv}<div class="gen-over"><span>${txt}</span>
    <span class="hub-prog"><i style="width:${Math.round((g._p || 0) * 100)}%"></i></span>
    ${g.id ? `<button class="btn sm" data-gcancel="${esc(g.id)}">取消</button>` : ""}</div></div>`;
}
function genMeta(g) {
  const sec = g.kind === "video" && genNum(g.length) && genNum(g.fps) ? ` · ${(genNum(g.length) / genNum(g.fps)).toFixed(1)} 秒` : "";
  return `${esc(g.name || "")}${genNum(g.w) ? ` · ${genNum(g.w)}×${genNum(g.h)}` : ""}${sec}${g.seed != null ? ` · 種子 ${esc(g.seed)}` : ""}`;
}
function genActs(g) {
  return `<div class="gen-acts">${g.status === "done" ? `<button class="btn sm" data-gopen="${esc(g.id)}">在右側看</button><button class="btn sm" data-gdl="${esc(g.id)}">下載</button>` : ""}
    <button class="btn sm" data-gagain="${esc(g._k)}">${g.status === "error" ? "再試一次" : "再生一次"}</button>
    ${g.id && g.status !== "deleted" ? `<button class="btn sm danger" data-gdel="${esc(g.id)}">刪除</button>` : ""}</div>`;
}
function genCard(m) {
  const g = m.gen;
  let h;
  if (["sending", "queued", "running"].includes(g.status)) h = genLiveBox(g);
  else if (g.status === "done") h = `<div class="gen-media" data-gid="${esc(g.id)}" style="aspect-ratio:${genAR(g)}">${g.kind === "video" ? `<button class="gen-play" data-gplay="${esc(g.id)}" aria-label="播放影片"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></button>` : ""}</div>`;
  else if (g.status === "cancelled") h = `<div class="stats">已取消</div>`;
  else if (g.status === "deleted") h = `<div class="stats">這個${genWord(g.kind)}已經刪掉了</div>`;
  else h = `<div class="err">${esc(g.error || "生成失敗")}</div>`;
  return h + `<div class="stats">${genMeta(g)}</div>` + (g.status === "sending" ? "" : genActs(g));
}
async function genFill(box, g, video) {
  try {
    const url = await genMedia(g.id);
    if (!box.isConnected) return;
    box.innerHTML = video ? `<video src="${url}" controls playsinline loop preload="metadata"></video>` : `<img src="${url}" alt="${esc(g.prompt || "")}" loading="lazy">`;
    if (video) box.querySelector("video").play().catch(() => {});
  } catch { box.innerHTML = `<div class="stats">拿不到這個${genWord(g.kind)}（可能已經刪掉了）</div>`; }
}
const _fillGen = fillMsg;
fillMsg = function (d, m, live) {
  if (!m.gen) return _fillGen(d, m, live);
  if (!/^[a-z0-9]{4,16}$/.test(m.gen._k || "")) m.gen._k = Math.random().toString(36).slice(2, 12);
  d.dataset.gk = m.gen._k;
  if (m.role === "user") { d.innerHTML = `<div class="bubble"><span class="gen-tag">${m.gen.kind === "video" ? "生影片" : "生圖"}</span>${esc(m.content)}</div>`; return; }
  d.innerHTML = `<div class="av">K</div><div class="body"><div class="who">Kiki</div>${genCard(m)}</div>`;
  const box = d.querySelector(".gen-media");
  if (box && m.gen.kind !== "video") genFill(box, m.gen, false);
};
function genPaint(m) {
  const el = m.gen._k && $(`#msgs .msg[data-gk="${m.gen._k}"]`);
  if (el) fillMsg(el, m);
}
function genFind(key) {
  for (const conv of [S.cur, ...[...GEN.active.values()].map(x => x.conv)]) {
    const m = conv?.messages.find(x => x.gen && (x.gen._k === key || x.gen.id === key));
    if (m) return { m, conv };
  }
  return null;
}

// ── 按鈕（對話裡、右欄都用同一套） ──
async function genClick(e) {
  const t = e.target.closest("[data-gopen],[data-gdl],[data-gagain],[data-gdel],[data-gcancel],[data-gplay],[data-gview],[data-guse],[data-gclose]");
  if (!t) return;
  e.preventDefault();
  if (t.dataset.gplay) { const f = genFind(t.dataset.gplay); if (f) genFill(t.closest(".gen-media"), f.m.gen, true); return; }
  if (t.dataset.gclose !== undefined) { GEN.view = ""; renderGenLive(); return; }
  if (t.dataset.gopen || t.dataset.gview) { GEN.view = t.dataset.gopen || t.dataset.gview; openRight(true); showRTab("gen"); return; }
  const f = genFind(t.dataset.gdl || t.dataset.gagain || t.dataset.gdel || t.dataset.gcancel || t.dataset.guse);
  if (!f) return;
  const g = f.m.gen;
  if (t.dataset.gdl) return genDownload(g);
  if (t.dataset.gcancel) { try { await api("/api/gen/cancel", { body: { id: g.id } }); } catch (err) { toast(err.message, true); } return; }
  if (t.dataset.guse) {
    GEN.kind = g.kind; GEN.model[g.kind] = g.model; GEN.draft = genPromptBefore(f.conv, f.m);
    renderGen(); const s = $("#gen-seed"); if (s) { s.value = g.seed ?? ""; s.closest("details").open = true; } return;
  }
  if (t.dataset.gagain) {
    const sec = g.kind === "video" && g.length && g.fps ? g.length / g.fps : undefined;
    return genStart(genPromptBefore(f.conv, f.m), { model: g.model, variant: g.variant, size: g.w ? [g.w, g.h] : undefined, seconds: sec, adv: g.steps ? { steps: g.steps } : {} });
  }
  if (t.dataset.gdel) {
    if (!confirm(`刪除這個${genWord(g.kind)}？刪了就找不回來。`)) return;
    try { await api("/api/gen/delete", { body: { id: g.id } }); } catch (err) { toast(err.message, true); return; }
    GEN.active.delete(g.id); GEN.blobs.get(g.id)?.then(u => URL.revokeObjectURL(u)).catch(() => {}); GEN.blobs.delete(g.id);
    g.status = "deleted"; if (GEN.view === g.id) GEN.view = "";
    genPaint(f.m); renderGenLive(); saveNow(f.conv);
  }
}
$("#msgs").addEventListener("click", genClick);
