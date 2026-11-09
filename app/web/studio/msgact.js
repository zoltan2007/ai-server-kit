"use strict";
// 訊息操作（複製／重新生成／編輯重送）、對話內搜尋、快捷鍵。接在 admin.js 後面載入。

const MSG = new WeakMap();      // 訊息的 DOM → 訊息物件
const inRoom = () => typeof RM !== "undefined" && !!RM.cur;
const ICON = {
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>',
  regen: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/></svg>',
  doc: '<svg viewBox="0 0 24 24"><path d="M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6"/></svg>',
};
const actBtn = (a, label) => `<button type="button" data-a="${a}" title="${label}" aria-label="${label}">${ICON[a]}</button>`;

// ── 每則訊息下面的操作列 ──
const _fillMsg = fillMsg;
fillMsg = function (d, m, live) {
  _fillMsg(d, m, live);
  MSG.set(d, m);
  if (live || inRoom()) return;
  const acts = document.createElement("div");
  acts.className = "macts";
  acts.innerHTML = m.role === "user" ? actBtn("copy", "複製") + actBtn("edit", "編輯後重送")
    : actBtn("copy", "複製") + actBtn("regen", "重新生成") + (typeof openDoc === "function" ? actBtn("doc", "在右側開成文件") : "");
  (m.role === "user" ? d : d.querySelector(".body")).appendChild(acts);
};
$("#msgs").addEventListener("click", e => {
  const b = e.target.closest(".macts [data-a]");
  if (!b) return;
  const el = b.closest(".msg"), m = MSG.get(el);
  if (!m) return;
  const a = b.dataset.a, text = m.role === "user" ? m.content : splitThink(m).content;
  if (a === "copy") copyText(text);
  if (a === "regen") regenerate(el, m);
  if (a === "edit") startEdit(el, m);
  if (a === "doc") openDoc(text);
});

async function regenerate(el, m) {
  const conv = S.cur;
  if (!conv || S.busy) return;
  const i = conv.messages.indexOf(m);
  if (i < 0 || i !== conv.messages.length - 1) return;      // 只有最後一則能重來
  const p = partner(), by = m.by || "kiki";
  conv.messages.splice(i, 1); el.remove();
  PT.depth = 0;
  await chain(by !== "kiki" && p && p.handle === by ? by : "kiki", conv);
}

function startEdit(el, m) {
  if (S.busy) { toast("等這則回完再編輯"); return; }
  if (el.querySelector(".edit")) return;
  const bubble = el.querySelector(".bubble"), acts = el.querySelector(".macts");
  const form = document.createElement("form");
  form.className = "edit";
  form.innerHTML = `<textarea rows="1" aria-label="編輯訊息"></textarea>
    <div class="edit-btns"><span class="muted small">送出後，這則之後的回答會重新產生</span><span class="grow"></span>
    <button type="button" class="btn sm" data-x>取消</button><button class="btn sm primary" type="submit">儲存並重送</button></div>`;
  const ta = form.querySelector("textarea");
  ta.value = m.content;
  bubble.hidden = true; acts.hidden = true; el.appendChild(form);
  autosize(ta); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  const cancel = () => { form.remove(); bubble.hidden = false; acts.hidden = false; $("#input").focus(); };
  form.querySelector("[data-x]").addEventListener("click", cancel);
  ta.addEventListener("input", () => autosize(ta));
  ta.addEventListener("keydown", e => {
    if (e.key === "Escape") { e.stopPropagation(); cancel(); }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229 && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const text = ta.value.trim(), conv = S.cur;
    if ((!text && !m.files?.length && !m.images?.length) || S.busy || !conv) return;      // 只有附件的訊息可以不打字就重送
    const i = conv.messages.indexOf(m);
    if (i < 0) { cancel(); return; }
    const later = conv.messages.length - i - 1;
    if (later > 1 && !confirm(`這則之後的 ${later} 則訊息會被移除、重新回答。\n想保留原本的內容，先按取消，再用「分支出新對話」。要繼續嗎？`)) return;
    conv.messages.splice(i);
    for (let n = el; n;) { const nx = n.nextElementSibling; n.remove(); n = nx; }
    await sendChat(text, { images: m.images, files: m.files, keep: m.keep });
  });
}
function editLast() {
  const els = $$("#msgs .msg.user");
  const el = els[els.length - 1], m = el && MSG.get(el);
  if (m) { startEdit(el, m); el.scrollIntoView({ block: "nearest" }); }
}

// ── 對話內搜尋 ──
const FIND = { hits: [], i: -1, t: 0 };
function clearMarks() {
  for (const mk of $$("#msgs mark.hit")) { const p = mk.parentNode; mk.replaceWith(document.createTextNode(mk.textContent)); p.normalize(); }
  FIND.hits = []; FIND.i = -1;
}
function runFind(raw) {
  clearMarks();
  const q = raw.trim().toLowerCase();
  if (!q) { $("#find-count").textContent = ""; return; }
  const skip = ".macts, .edit, .code-bar, .who, .stats, .time, .name";
  const w = document.createTreeWalker($("#msgs"), NodeFilter.SHOW_TEXT, {
    acceptNode: n => (n.parentElement.closest(".msg") && !n.parentElement.closest(skip) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT) });
  const nodes = [];
  while (w.nextNode()) nodes.push(w.currentNode);
  for (const n of nodes) {
    const text = n.nodeValue, low = text.toLowerCase();
    let pos = low.indexOf(q), last = 0;
    if (pos < 0 || low.length !== text.length) continue;
    const frag = document.createDocumentFragment();
    while (pos >= 0 && FIND.hits.length < 2000) {
      frag.append(text.slice(last, pos));
      const mk = document.createElement("mark");
      mk.className = "hit"; mk.textContent = text.slice(pos, pos + q.length);
      frag.append(mk); FIND.hits.push(mk);
      last = pos + q.length; pos = low.indexOf(q, last);
    }
    frag.append(text.slice(last)); n.replaceWith(frag);
  }
  gotoHit(FIND.hits.length ? FIND.hits.length - 1 : -1);      // 從最新的那一筆開始，往上找
}
function gotoHit(i) {
  FIND.hits = FIND.hits.filter(h => h.isConnected);
  const n = FIND.hits.length;
  $$("#msgs mark.hit.cur").forEach(h => h.classList.remove("cur"));
  if (!n) { FIND.i = -1; $("#find-count").textContent = $("#find-input").value.trim() ? "找不到" : ""; return; }
  FIND.i = ((i % n) + n) % n;
  const h = FIND.hits[FIND.i];
  for (let d = h.closest("details"); d; d = d.parentElement?.closest("details")) d.open = true;
  h.classList.add("cur"); h.scrollIntoView({ block: "center" });
  $("#find-count").textContent = `${FIND.i + 1} / ${n}`;
}
function openFind() {
  if (document.body.classList.contains("in-dash")) return;
  $("#findbar").hidden = false; $("#find-btn").classList.add("on");
  const inp = $("#find-input"); inp.focus(); inp.select();
  if (inp.value) runFind(inp.value);
}
function closeFind() { $("#findbar").hidden = true; $("#find-btn").classList.remove("on"); clearMarks(); $("#find-count").textContent = ""; }
$("#find-btn").addEventListener("click", () => ($("#findbar").hidden ? openFind() : closeFind()));
$("#find-close").addEventListener("click", closeFind);
$("#find-next").addEventListener("click", () => gotoHit(FIND.i + 1));
$("#find-prev").addEventListener("click", () => gotoHit(FIND.i - 1));
$("#find-input").addEventListener("input", e => { clearTimeout(FIND.t); FIND.t = setTimeout(() => runFind(e.target.value), 160); });
$("#find-input").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); gotoHit(FIND.i + (e.shiftKey ? 1 : -1)); }
});
const _renderCur = renderCur;
renderCur = function () { _renderCur(); if (!$("#findbar").hidden) runFind($("#find-input").value); };

// ── 快捷鍵 ──
const MAC = /Mac|iPhone|iPad/.test(navigator.platform || "");
const KEYS = [
  ["K", "搜尋對話清單"], ["Shift+O", "開新對話"], ["B", "收起／打開側欄"], ["Shift+F", "在這個對話裡找"],
  [".", "打開／收起右側窗格"], ["/", "看這張快捷鍵表"],
];
function renderKeys() {
  const mod = MAC ? "⌘" : "Ctrl";
  $("#keys-list").innerHTML = KEYS.map(([k, d]) => `<div class="kv"><span>${d}</span><span>${(mod + "+" + k).split("+").map(x => `<kbd>${x}</kbd>`).join(" ")}</span></div>`).join("") +
    `<div class="kv"><span>停止生成／關掉選單</span><span><kbd>Esc</kbd></span></div>
     <div class="kv"><span>編輯我上一則訊息（輸入框是空的時候）</span><span><kbd>↑</kbd></span></div>
     <div class="kv"><span>送出／換行</span><span><kbd>Enter</kbd> ／ <kbd>Shift</kbd> <kbd>Enter</kbd></span></div>
     <div class="kv"><span>快捷指令</span><span><kbd>/</kbd></span></div>`;
}
function openKeys() { $("#settings-pop").hidden = true; renderKeys(); $("#keys-modal").hidden = false; }
$("#keys-btn").addEventListener("click", openKeys);
$("#keys-close").addEventListener("click", () => ($("#keys-modal").hidden = true));
$("#keys-modal").addEventListener("click", e => { if (e.target === e.currentTarget) e.currentTarget.hidden = true; });
document.addEventListener("keydown", e => {
  if ($("#app").hidden) return;
  const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
  if (mod && !e.altKey) {
    const hit = !e.shiftKey && k === "k" ? () => { openSide(true); const s = $("#search"); s.hidden = false; s.focus(); s.select(); }
      : e.shiftKey && k === "o" ? () => { if (!S.busy) newSession(); }
      : !e.shiftKey && k === "b" ? () => openSide(wide() ? app.classList.contains("side-off") : !app.classList.contains("side-on"))
      : e.shiftKey && k === "f" ? openFind
      : !e.shiftKey && k === "." ? () => openRight(!app.classList.contains("right-on"))
      : k === "/" ? openKeys : null;
    if (hit) { e.preventDefault(); hit(); }
    return;
  }
  if (e.key === "Escape") {
    const open = $$(".modal, .popover, #slash").some(x => !x.hidden) || e.target.closest?.(".edit");
    if (open) return;                                    // 先讓選單、彈窗自己關
    if (!$("#findbar").hidden) { closeFind(); $("#input").focus(); return; }
    if (S.busy) S.ctrl?.abort();
    return;
  }
  if (e.key === "ArrowUp" && e.target === $("#input") && !e.target.value && !inRoom() && !S.busy && S.cur?.messages.length) { e.preventDefault(); editLast(); }
}, true);

SLASH.push(
  { k: "/搜尋", d: "在這個對話裡找字", run: openFind },
  { k: "/重來", d: "重新生成最後一則回答", run: () => { const els = $$("#msgs .msg.ai"), el = els[els.length - 1], m = el && MSG.get(el); if (m) regenerate(el, m); } },
  { k: "/快捷鍵", d: "看鍵盤快捷鍵", run: openKeys },
  { k: "/指示", d: "設定這個專案固定給 Kiki 的指示", run: () => openInstructions() },
);
