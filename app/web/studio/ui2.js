"use strict";
// 介面強化：首頁最近專案、右欄分頁、匯出、拖拉檔案、成員頭像。接在 heat.js 後面載入。

// ── 首頁：最近的專案 ──
function renderRecent() {
  const box = $("#recent");
  if (!box) return;
  const rows = S.sessions.filter(s => s.count).slice(0, 4);
  const when = t => { const d = (Date.now() / 1000 - t) / 3600; return d < 1 ? "剛剛" : d < 24 ? `${Math.floor(d)} 小時前` : `${Math.floor(d / 24)} 天前`; };
  let h = rows.map(s => `<button class="rcard" data-open-s="${esc(s.id)}"><b>${esc(s.title || "（沒有標題）")}</b><span>${s.count} 則 · ${esc((s.model || "—").split(":")[0])}${s.partner ? " · " + esc(s.partner.name) : ""}</span><i>${when(s.updated)}</i></button>`).join("");
  if (S.me?.role === "admin" && S.me.pending) h = `<button class="rcard warn" id="rcard-pending"><b>有 ${S.me.pending} 個帳號申請</b><span>等你核准</span><i>成員</i></button>` + h;
  box.innerHTML = h ? `<div class="recent-head">最近</div><div class="recent">${h}</div>` : "";
}
$("#recent").addEventListener("click", e => {
  const p = e.target.closest("#rcard-pending");
  if (p) { $("#users-btn").click(); return; }
  const b = e.target.closest("[data-open-s]");
  if (b && !S.busy) openSession(b.dataset.openS);
});

// ── 右欄分頁 ──
function showRTab(name) {
  $$(".rtabs button").forEach(b => b.classList.toggle("on", b.dataset.rt === name));
  $$(".rpane").forEach(p => (p.hidden = p.dataset.rt !== name));
  ls.set("rtab", name);
  if (name === "usage") loadHeat("#heat-side");
  if (name === "project") renderProjectPane();
  if (name === "members" && S.me?.role === "admin") renderUsers("#users-side").catch(() => {});
}
$(".rtabs").addEventListener("click", e => { const b = e.target.closest("[data-rt]"); if (b) showRTab(b.dataset.rt); });
function renderProjectPane() {
  const box = $("#project-pane");
  const c = S.cur, room = typeof RM !== "undefined" && RM.cur;
  if (room) { box.innerHTML = `<div class="stat"><h4>群組</h4><div class="v">${esc(room.title)}</div><div class="kv"><span>成員</span><span>${room.members.length} 人</span></div><div class="kv"><span>模型</span><span>${esc(room.model || "—")}</span></div></div><button class="btn sm" id="pp-members">成員與外部 AI</button>`; return; }
  if (!c) { box.innerHTML = ""; return; }
  const fmt = t => t ? new Date(t * 1000).toLocaleString("zh-Hant", { hour12: false }) : "—";
  box.innerHTML = `<div class="stat"><h4>專案</h4><div class="v">${esc(c.title || "新對話")}</div>
      <div class="kv"><span>Kiki 的模型</span><span>${esc(c.model || "—")}</span></div>
      <div class="kv"><span>我的 AI 夥伴</span><span>${c.partner ? esc(c.partner.name + "（" + c.partner.model + "）") : "沒有"}</span></div>
      <div class="kv"><span>群組</span><span>${esc(c.group || "—")}</span></div>
      <div class="kv"><span>訊息</span><span>${c.messages.length} 則</span></div>
      <div class="kv"><span>最後更新</span><span>${fmt(c.updated)}</span></div></div>
    <div class="stat"><h4>匯出（存在你的裝置）</h4>
      <div class="foot-btns"><button class="btn sm" data-exp="txt">純文字對話</button><button class="btn sm" data-exp="md">AI 記憶檔（Markdown）</button></div>
      <div class="foot-btns" style="margin-top:8px"><button class="btn sm primary" id="exp-all">匯出我的全部專案與群組（zip）</button></div>
      <p class="muted small" style="margin:8px 0 0">記憶檔整理成別的 AI 讀得懂的格式：標題、成員、每一輪的重點、完整對話。</p></div>`;
}
$("#project-pane").addEventListener("click", async e => {
  if (e.target.closest("#pp-members")) { $("#members-btn").click(); return; }
  if (e.target.closest("#exp-all")) {
    const btn = e.target.closest("#exp-all"); btn.disabled = true;
    try {
      const r = await api("/api/export/all", { raw: true });
      const name = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "KikiCloude.zip";
      const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      toast("已下載：" + name);
    } catch (err) { toast(err.message, true); }
    btn.disabled = false; return;
  }
  const b = e.target.closest("[data-exp]");
  if (b && S.cur) exportSession(b.dataset.exp);
});

// ── 匯出 ──
function exportSession(fmt) {
  const c = S.cur, who = m => (typeof nameOf === "function" ? nameOf(m.by || (m.role === "assistant" ? "kiki" : "user")) : m.role);
  const body = m => (m.role === "assistant" ? splitThink(m).content : m.content) || "";
  const stamp = new Date().toISOString().slice(0, 10);
  let text, name;
  if (fmt === "txt") {
    text = `${c.title}\n${"=".repeat(Math.min(40, c.title.length * 2))}\n\n` + c.messages.map(m => `${who(m)}：\n${body(m)}\n`).join("\n");
    name = `${c.title || "對話"}_${stamp}.txt`;
  } else {
    const members = ["使用者 " + (S.me?.name || ""), "Kiki（本機模型 " + (c.model || "—") + "）"].concat(c.partner ? [`${c.partner.name}（${c.partner.model}，使用者自己的 AI）`] : []);
    const turns = c.messages.map((m, i) => `### ${i + 1}. ${who(m)}\n\n${body(m)}\n`).join("\n");
    text = `# ${c.title}\n\n> 這是 Kiki Cloude 匯出的專案記憶檔，給另一個 AI 接手用。讀完後請先以這裡的事實與決定為準，再回答使用者。\n\n` +
      `- 匯出日期：${stamp}\n- 成員：${members.join("、")}\n- 訊息數：${c.messages.length}\n${c.group ? `- 群組：${c.group}\n` : ""}\n` +
      `## 使用者在這個專案裡提出的事\n\n${c.messages.filter(m => (m.by || m.role) === "user" || m.role === "user").map(m => "- " + (m.content || "").split("\n")[0].slice(0, 120)).join("\n")}\n\n## 完整對話\n\n${turns}`;
    name = `${c.title || "對話"}_記憶檔_${stamp}.md`;
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" })); a.download = name.replace(/[\\/:*?"<>|]/g, "_");
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  toast("已下載：" + a.download);
}

// ── 拖拉檔案進輸入框 ──
const comp = $("#composer");
["dragenter", "dragover"].forEach(ev => comp.addEventListener(ev, e => { e.preventDefault(); comp.classList.add("drop"); }));
["dragleave", "drop"].forEach(ev => comp.addEventListener(ev, () => comp.classList.remove("drop")));
comp.addEventListener("drop", async e => {
  e.preventDefault();
  for (const f of [...e.dataTransfer.files].slice(0, 8)) {
    if (f.type.startsWith("image/")) {
      if ($("#attach-btn").hidden) { toast("這個模型不能看圖", true); continue; }
      const url = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(f); });
      S.attach.push({ b64: String(url).split(",")[1], url }); renderAttach();
    } else if (f.size <= 64 * 1024) {
      const t = await f.text();
      const ta = $("#input");
      ta.value = (ta.value ? ta.value + "\n\n" : "") + `檔案 ${f.name}：\n\`\`\`\n${t}\n\`\`\`\n`; autosize(ta);
    } else toast(`${f.name} 太大（最多 64 KB）`, true);
  }
});

// ── 標題旁的成員頭像 ──
function renderParty() {
  const p = partner(), el = $("#party");
  el.innerHTML = `<i class="pav u" title="你">${esc((S.me?.name || "你")[0].toUpperCase())}</i><i class="pav k" title="Kiki">K</i>` +
    (p ? `<i class="pav p" data-prov="${esc(p.provider)}" title="${esc(p.name)}">${esc(p.name[0])}</i>` : `<i class="pav add" title="加入我的 AI">+</i>`);
}
$("#party").addEventListener("click", () => $("#partner-chip").click());

$("#users-side").addEventListener("click", async e => {
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
  try { await api("/api/users/" + act, { body }); toast("完成"); await renderUsers("#users-side"); await loadMe(); }
  catch (err) { toast(err.message, true); }
});

// 啟動後的掛鉤
const _origRenderPartnerChip = renderPartnerChip;
renderPartnerChip = function () { _origRenderPartnerChip(); renderParty(); };
showRTab(ls.get("rtab", "host"));

// ── Liquid bulge：清單的滑鼠反白是一塊會跟著游標滑動、微微鼓起的軟墊 ──
function liquid(container) {
  if (!container || container.querySelector(":scope > .liquid")) return;
  const pad = document.createElement("div");
  pad.className = "liquid"; container.prepend(pad);
  let cur = null, raf = 0;
  const place = (el, mx, my) => {
    const c = container.getBoundingClientRect(), r = el.getBoundingClientRect();
    const dx = mx == null ? 0 : ((mx - (r.left + r.width / 2)) / r.width) * 6;    // 往游標那邊靠一點點，像液體被推
    const dy = my == null ? 0 : ((my - (r.top + r.height / 2)) / r.height) * 4;
    pad.style.width = r.width + "px"; pad.style.height = r.height + "px";
    pad.style.transform = `translate(${r.left - c.left + container.scrollLeft + dx}px, ${r.top - c.top + container.scrollTop + dy}px)`;
    pad.style.opacity = "1";
  };
  container.addEventListener("mousemove", e => {
    let fresh = false;
    if (!pad.isConnected) { container.prepend(pad); fresh = true; }   // 清單用 innerHTML 重畫過，墊子要放回去
    const el = e.target.closest(".sess, .grp, .item, .rcard, #model-list button, .menu button");
    if (!el || !container.contains(el)) { pad.style.opacity = "0"; cur = null; return; }
    const appear = fresh || cur === null;       // 從看不見變成看得見：直接出現在原地，不要從老遠滑過來
    if (el !== cur) { cur = el; if (!appear) { pad.classList.remove("bulge"); void pad.offsetWidth; pad.classList.add("bulge"); } }
    if (!raf) raf = requestAnimationFrame(() => {
      raf = 0;
      if (!cur) return;
      if (appear) { pad.style.transition = "none"; place(cur, e.clientX, e.clientY); void pad.offsetWidth; pad.style.transition = ""; }
      else place(cur, e.clientX, e.clientY);
    });
  });
  container.addEventListener("mouseleave", () => { pad.style.opacity = "0"; cur = null; });
  container.addEventListener("scroll", () => { if (cur && cur.isConnected) place(cur); else { pad.style.opacity = "0"; cur = null; } }, { passive: true });
}
["#sess-list", ".menu-list", "#recent", "#model-list", "#settings-pop", "#partner-pop", "#ctx"].forEach(s => liquid($(s)));
