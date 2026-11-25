// 右欄「創作」分頁：選模型、比例、長度、寫提示詞、按生成；下面看正在做的和這個對話的作品。
function genPaneHead(info) {
  const ok = info.installed && info.running;
  return `<div class="gen-head"><div class="seg" role="tablist" aria-label="要生成什麼">
      <button type="button" role="tab" data-gk="image" class="${GEN.kind === "image" ? "on" : ""}" aria-selected="${GEN.kind === "image"}">圖片</button>
      <button type="button" role="tab" data-gk="video" class="${GEN.kind === "video" ? "on" : ""}" aria-selected="${GEN.kind === "video"}">影片</button></div>
    <span class="grow"></span><span class="pill ${ok ? "ok" : "warn"}" title="生成用的是這台主機上的 ComfyUI">${!info.installed ? "沒裝 ComfyUI" : ok ? "ComfyUI 在跑" : "ComfyUI 沒在跑"}</span></div>`;
}
function genEmpty(info) {
  const word = genWord(GEN.kind);
  if (!info.installed) return `<div class="gen-empty"><b>這台還沒裝 ComfyUI</b><p class="muted small">生${word}要用 ComfyUI。${info.admin ? "重新跑安裝檔並加 <code>--comfyui</code>，或在 config.json 的 comfyui.dir 指到裝好的資料夾。" : "請管理員安裝。"}</p></div>`;
  const miss = (info.missing || []).filter(m => m.kind === GEN.kind);
  return `<div class="gen-empty"><b>還沒有可以用的${word}模型</b><p class="muted small">${info.admin ? `到模型庫下載${miss.length ? "（例如 " + miss.slice(0, 3).map(m => esc(m.name)).join("、") + "）" : ""}，下載完就會出現在這裡。` : "請管理員到模型庫下載。"}</p>
    ${info.admin ? `<button class="btn sm primary" type="button" data-ghub>打開模型庫</button>` : ""}</div>`;
}
function genForm(m, info) {
  const [w, h] = genSize(m, GEN.ratio), ms = genModels(GEN.kind);
  let html = `<label class="gen-f"><span>模型</span><select id="gen-model" aria-label="模型">${ms.map(x => `<option value="${esc(x.id)}" ${x.id === m.id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
    <textarea id="gen-prompt" rows="4" maxlength="2000" aria-label="提示詞" placeholder="描述你想要的${genWord(m.kind)}：主角、場景、光線、風格…越具體越好（Ctrl＋Enter 生成）">${esc(GEN.draft)}</textarea>
    <div class="gen-f"><span>比例</span><div class="seg" id="gen-ratio">${GEN_RATIOS.map(r => `<button type="button" data-gr="${r}" class="${r === GEN.ratio ? "on" : ""}">${r}</button>`).join("")}</div><small class="muted">${w}×${h}</small></div>`;
  if (m.kind === "video") {
    const { opts, def } = genSecs(m), cur = GEN.sec[m.id] || def;
    html += `<div class="gen-f"><span>長度</span><div class="seg" id="gen-sec">${opts.map(s => `<button type="button" data-gs="${s}" class="${s === cur ? "on" : ""}">${s} 秒</button>`).join("")}</div></div>`;
  }
  html += `<details class="gen-adv"><summary>進階</summary>
    ${m.steps ? `<label class="gen-f"><span>步數</span><input id="gen-steps" type="number" min="1" max="100" placeholder="${m.defaults.steps || ""}（預設）"></label>` : ""}
    <label class="gen-f"><span>種子</span><input id="gen-seed" type="number" min="0" placeholder="留空＝每次不一樣"></label>
    ${m.negative ? `<label class="gen-f col"><span>不要出現</span><textarea id="gen-neg" rows="2" maxlength="1000" placeholder="留空用模型預設"></textarea></label>` : ""}
    <p class="muted small">${m.kind === "video" ? "影片很吃顯示卡，常要幾分鐘到十幾分鐘。" : "第一次用某個模型要先載入，會比較久。"}</p></details>
    <button class="btn primary gen-go" type="button" id="gen-go" ${info.running ? "" : "disabled"}>生成${genWord(m.kind)}</button>
    ${info.running ? "" : `<p class="muted small">${info.admin ? "ComfyUI 沒在跑：在主機上執行 <code>sudo systemctl start comfyui</code>" : "ComfyUI 沒在跑，請管理員啟動"}</p>`}`;
  return html;
}
function renderGen() {
  const p = $("#gen-pane");
  if (!p || p.hidden) return;
  if (!GEN.info) {
    p.innerHTML = `<p class="muted">載入中…</p>`;
    genInfo().then(renderGen).catch(e => { p.innerHTML = `<p class="err">${esc(e.message)}</p>`; });
    return;
  }
  const info = GEN.info, m = genCur();
  p.innerHTML = genPaneHead(info) + `<div class="gen-view" id="gen-view"></div>`
    + (m ? `<form class="gen-form" id="gen-form">${genForm(m, info)}</form>` : genEmpty(info))
    + `<div id="gen-now"></div><div class="gen-gal-wrap" id="gen-gal"></div>`;
  renderGenLive();
}
function genItems() {
  return (S.cur?.messages || []).filter(m => m.role === "assistant" && m.gen);
}
function renderGenLive() {
  const p = $("#gen-pane");
  if (!p || p.hidden || !$("#gen-now", p)) return;
  const items = genItems(), live = [...GEN.active.values()].map(x => x.m).filter(m => !items.includes(m));
  const now = items.filter(m => ["sending", "queued", "running"].includes(m.gen.status)).concat(live);
  $("#gen-now", p).innerHTML = now.length ? `<h4 class="gen-h">正在做</h4>` + now.map(m => `<div class="gen-now-row">${genLiveBox(m.gen)}<div class="stats">${genMeta(m.gen)}</div></div>`).join("") : "";
  const done = items.filter(m => m.gen.status === "done").reverse(), gal = $("#gen-gal", p);
  const sig = done.map(m => m.gen.id).join() + "|" + GEN.view + "|" + (items.length + now.length > 0) + "|" + GEN.kind;
  if (gal.dataset.sig !== sig) genGallery(gal, sig, done, items, now);
  genViewer(p, items);
}
function genGallery(gal, sig, done, items, now) {
  gal.dataset.sig = sig;
  gal.innerHTML = done.length ? `<h4 class="gen-h">這個對話的作品（${done.length}）</h4><div class="gen-gal">${done.map(m =>
    `<button type="button" class="gen-thumb ${GEN.view === m.gen.id ? "on" : ""}" data-gview="${esc(m.gen.id)}" title="${esc(m.gen.prompt || "")}" data-gtid="${esc(m.gen.id)}">
      ${m.gen.kind === "video" ? `<span class="gen-vid"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></span>` : ""}</button>`).join("")}</div>`
    : (items.length || now.length ? "" : `<p class="muted small gen-hint">做好的${genWord(GEN.kind)}會留在這裡，也會貼進對話。</p>`);
  for (const b of $$("[data-gtid]", gal)) {
    const g = done.find(m => m.gen.id === b.dataset.gtid).gen;
    if (g.kind !== "video") genMedia(g.id).then(u => { if (b.isConnected) b.style.backgroundImage = `url("${u}")`; }).catch(() => b.classList.add("gone"));
  }
}
function genViewer(p, items) {
  const v = $("#gen-view", p), sel = GEN.view && items.find(m => m.gen.id === GEN.view && m.gen.status === "done");
  if (!sel) { v.innerHTML = ""; v.hidden = true; v.dataset.shown = ""; return; }
  const g = sel.gen;
  if (v.dataset.shown === g.id) return;          // 已經在看這個：不要重畫（影片會從頭播）
  v.dataset.shown = g.id; v.hidden = false;
  v.innerHTML = `<div class="gen-media big" data-gid="${esc(g.id)}" style="aspect-ratio:${genAR(g)}"></div>
    <p class="gen-prompt-txt">${esc(genPromptBefore(S.cur, sel))}</p><div class="stats">${genMeta(g)}</div>
    <div class="gen-acts"><button class="btn sm" data-gdl="${esc(g.id)}">下載</button><button class="btn sm" data-gagain="${esc(g._k || g.id)}">再生一次</button>
    <button class="btn sm" data-guse="${esc(g._k || g.id)}" title="把這次的提示詞、模型和種子帶回上面">用這組設定</button><button class="btn sm danger" data-gdel="${esc(g.id)}">刪除</button>
    <span class="grow"></span><button class="btn sm" data-gclose>關閉</button></div>`;
  genFill($(".gen-media", v), g, g.kind === "video");
}

// ── 右欄的操作 ──
const genPane = $("#gen-pane");
genPane.addEventListener("click", e => {
  const t = e.target.closest("[data-gk],[data-gr],[data-gs],[data-ghub],#gen-go");
  if (!t) return genClick(e);
  if (t.dataset.gk) { GEN.kind = t.dataset.gk; ls.set("gen.kind", GEN.kind); renderGen(); return; }
  if (t.dataset.gr) { GEN.ratio = t.dataset.gr; ls.set("gen.ratio", GEN.ratio); renderGen(); return; }
  if (t.dataset.gs) { const m = genCur(); GEN.sec[m.id] = +t.dataset.gs; ls.set("gen.sec", GEN.sec); renderGen(); return; }
  if (t.dataset.ghub !== undefined) { if (!wide()) openRight(false); openHub(); return; }
  if (t.id === "gen-go") genGo();
});
genPane.addEventListener("submit", e => e.preventDefault());   // 在欄位按 Enter 不要送出表單
genPane.addEventListener("input", e => { if (e.target.id === "gen-prompt") GEN.draft = e.target.value; });
genPane.addEventListener("change", e => {
  if (e.target.id === "gen-model") { GEN.model[GEN.kind] = e.target.value; ls.set("gen.model", GEN.model); renderGen(); }
});
genPane.addEventListener("keydown", e => {
  if (e.target.id === "gen-prompt" && e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); genGo(); }
});
function genGo() {
  const ta = $("#gen-prompt"), text = ta?.value.trim();
  if (!text) { toast("先寫下想生成的內容", true); ta?.focus(); return; }
  genStart(text);
}
function openGen(kind, model) {
  if (kind) { GEN.kind = kind; ls.set("gen.kind", kind); }
  if (model) { GEN.model[GEN.kind] = model; ls.set("gen.model", GEN.model); }
  openRight(true); showRTab("gen");
  later(() => $("#gen-prompt")?.focus());
}

// ── 接到其他地方 ──
const _showRTabGen = showRTab;
showRTab = function (name) {
  _showRTabGen(name);
  app.classList.toggle("art-wide", name === "art" || name === "gen");
  if (name === "gen") genInfo(true).catch(() => {}).finally(renderGen);
};
const _renderCurGen = renderCur;
renderCur = function () {
  _renderCurGen();
  for (const m of S.cur.messages) if (m.gen?.id && ["queued", "running"].includes(m.gen.status)) genTrack(m, S.cur);
  if (GEN.view && !genItems().some(m => m.gen.id === GEN.view)) GEN.view = "";
  renderGenLive();
};
// 輸入框打「/生圖 一隻貓」直接生成；只打「/生圖」就打開右欄
$("#composer").addEventListener("submit", e => {
  const text = $("#input").value.trim(), m = text.match(/^\/(生圖|生影片)\s+([\s\S]+)$/);
  if (!m || S.busy) return;
  e.preventDefault(); e.stopImmediatePropagation();
  $("#input").value = ""; later(() => autosize($("#input"))); $("#slash").hidden = true;
  GEN.kind = m[1] === "生圖" ? "image" : "video"; ls.set("gen.kind", GEN.kind);
  genStart(m[2]);
}, true);
SLASH.push({ k: "/生圖", d: "用這台主機生成圖片（也可以直接打「/生圖 內容」）", run: () => openGen("image") });
SLASH.push({ k: "/生影片", d: "用這台主機生成影片（也可以直接打「/生影片 內容」）", run: () => openGen("video") });
// 模型庫的「在 Kiki 裡用」
$("#hub").addEventListener("click", e => {
  const t = e.target.closest("[data-use]");
  if (!t) return;
  closeHub(true);
  genInfo(true).catch(() => {}).finally(() => openGen(t.dataset.kind, t.dataset.use));
});
if (ls.get("rtab", "host") === "gen") showRTab("gen");
