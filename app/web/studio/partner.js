"use strict";
// 專案裡的第三位成員：使用者自己訂閱的 AI（夥伴）。Kiki 用本機模型，夥伴用雲端；兩個 AI 可以互相討論。
// 訊息多一個 by 欄位：user／kiki／<夥伴代號>。接在 myai.js 後面載入。

const PT = { depth: 0 };
const PROV_BY_HANDLE = { claude: "anthropic", gpt: "openai", gemini: "gemini", grok: "xai" };
const MAX_HOPS = 2;   // AI 互相接話最多幾回合，免得無限迴圈

function partner() { return S.cur?.partner || null; }
function partnerLabel(p) { return p ? `${p.name}（${p.model}）` : ""; }

function renderPartnerChip() {
  const p = partner();
  const chip = $("#partner-chip"), pop = $("#partner-pop");
  chip.querySelector("span").textContent = p ? p.name : "＋ 加入我的 AI";
  chip.classList.toggle("on", !!p);
  const mine = (typeof AI !== "undefined" ? AI.mine : []).filter(a => a.has_key);
  pop.innerHTML = `<div class="pop-head">我的 AI 夥伴 <span class="muted small">加入後這個專案有三個人：你、Kiki、它</span></div>` +
    (mine.length ? mine.map(a => `<button data-partner="${a.provider}" class="${p?.provider === a.provider ? "on" : ""}"><span>${esc(a.name)}</span><span class="muted">${esc(a.model)} · @${esc(a.handle)}</span></button>`).join("")
      : `<div class="muted small" style="padding:8px">先到「自訂 → 我的 AI」填金鑰</div>`) +
    (p ? `<label class="toggle" title="預設只告訴夥伴附了哪些檔案，不給內容"><input type="checkbox" id="partner-files" ${S.cur?.settings?.partnerFiles ? "checked" : ""}><span>讓 ${esc(p.name)} 也讀附件的文字</span></label><button data-partner="" class="danger">把 ${esc(p.name)} 移出這個專案</button>` : "");
  $("#party").textContent = p ? `你、Kiki、${p.name}` : "你、Kiki";
  $("#discuss-btn").hidden = !p || !S.cur?.messages?.some(m => (m.by || (m.role === "assistant" ? "kiki" : "user")) === "kiki");
}
$("#partner-chip").addEventListener("click", e => {
  e.stopPropagation();
  const pop = $("#partner-pop"), r = e.currentTarget.getBoundingClientRect();
  renderPartnerChip(); pop.hidden = !pop.hidden;
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - 370)) + "px"; pop.style.top = Math.max(8, r.top - 8) + "px"; pop.style.transform = "translateY(-100%)";
});
$("#partner-pop").addEventListener("change", e => {
  if (e.target.id !== "partner-files" || !S.cur) return;
  S.cur.settings = Object.assign({}, S.cur.settings, { partnerFiles: e.target.checked }); queueSave(0);
  toast(e.target.checked ? "之後夥伴會收到附件的文字（會送到它的雲端服務）" : "夥伴只會知道附了哪些檔案，看不到內容");
});
$("#partner-pop").addEventListener("click", e => {
  const b = e.target.closest("[data-partner]");
  if (!b || !S.cur) return;
  $("#partner-pop").hidden = true;
  const a = AI.mine.find(x => x.provider === b.dataset.partner);
  S.cur.partner = a ? { provider: a.provider, model: a.model, name: a.name, handle: a.handle } : null;
  renderPartnerChip(); queueSave(0);
  if (a) toast(`${a.name} 加入了。打 @${a.handle} 叫它；按「討論」讓它和 Kiki 互相討論`);
});
document.addEventListener("click", e => { if (!e.target.closest("#partner-pop, #partner-chip")) $("#partner-pop").hidden = true; });

// ── 給兩種 AI 看的對話內容 ──
function speaker(m) { return m.by || (m.role === "assistant" ? "kiki" : "user"); }
function nameOf(by) { const p = partner(); return by === "user" ? (S.me?.name || "使用者") : by === "kiki" ? "Kiki" : (p && by === p.handle ? p.name : by); }
function contextFor(who, upto, conv = S.cur) {
  // who 自己說過的話當 assistant，其他人的話當 user 並標名字。
  // 專案指示、主機狀態只給 Kiki（本機）；雲端夥伴只拿到對話文字（伺服器端 providers._clean 也會把 system 丟掉）。
  // 舊對話的主模型可能是 ext:（雲端）：那就算是「Kiki」的位置，也一律照雲端的規矩，只給對話文字。
  const msgs = [], sys = [];
  const p = partner(), local = who === "kiki" && !String(conv?.model || "").startsWith("ext:");
  if (local) {
    const inst = (conv?.instructions || "").trim();
    if (inst) sys.push("使用者為這個專案設定的固定指示，每次回答都要遵守：\n" + inst);
    if (p) sys.push(`這個專案有三位成員：使用者「${S.me?.name}」、你（Kiki）、使用者自己的 AI 夥伴「${p.name}」（代號 @${p.handle}）。夥伴的話會標「${p.name}：」；它可能對你下指令或和你討論，照常回應，必要時用 @${p.handle} 回它。`);
    if ($("#with-status").checked && statusForAI()) sys.push(statusForAI());
    if (sys.length) msgs.push({ role: "system", content: sys.join("\n\n") });
  }
  // 圖片很占資源：只跟「最近一則有圖的訊息」一起送，除非使用者把那則的圖固定住（m.keep）
  const lastImg = local ? upto.reduce((a, m, i) => (m.images?.length ? i : a), -1) : -1;
  upto.forEach((m, i) => {
    const by = speaker(m);
    let text = (by === "user" ? m.content : splitThink(m).content) || "";
    if (m.files?.length) text = filesText(m.files, local || !!conv?.settings?.partnerFiles) + (text ? "\n\n" + text : "");
    const withImg = local && !!m.images?.length && (i === lastImg || !!m.keep);
    if (local && m.images?.length && !withImg) text += `${text ? "\n" : ""}（這則原本附了 ${m.images.length} 張圖片，之後有更新的圖所以沒有重送；需要再看的話請使用者重新附上或把它固定）`;
    if (!text && !withImg) return;
    if (by === who) { msgs.push({ role: "assistant", content: text }); return; }
    const x = { role: "user", content: by === "user" ? text : `${nameOf(by)}：${text}` };
    if (withImg) x.images = m.images;
    msgs.push(x);
  });
  return msgs;
}

// ── 讓某一位 AI 回一則 ──
async function askAI(who) {
  const conv = S.cur, p = partner();
  if (!conv) return null;
  const model = who === "kiki" ? conv.model : `ext:${p.provider}:${p.model}`;
  if (!model) { toast("先選一個模型", true); return null; }
  const ai = { role: "assistant", by: who, content: "", thinking: "" };
  conv.messages.push(ai);
  const el = msgEl(ai, true); $("#msgs").appendChild(el); scrollMsgs(true);
  const body = { model, messages: contextFor(who, conv.messages.slice(0, -1), conv) };
  if (who === "kiki") {
    const caps = S.caps[model]?.capabilities || [];
    if (caps.includes("thinking")) body.think = /^gpt-oss/.test(model) ? (S.think === "off" ? "low" : S.think === "high" ? "high" : "medium") : S.think !== "off";
    const ctx = ctxOptions(model, body.messages);      // 內容長（例如附了文件）就把模型一次能讀的長度調大
    if (ctx) { body.options = { num_ctx: ctx.num_ctx }; if (ctx.over) toast("內容很長，超過模型一次能讀的長度，前面的部分可能會被略過", true); }
  }
  S.busy = true; S.ctrl = new AbortController();
  $("#send").hidden = true; $("#stop").hidden = false;
  let raf = 0, live = true, lastPaint = 0, gotDone = false;
  const paint = () => { if (!raf) raf = later(() => { raf = 0; if (live && performance.now() - lastPaint > 120) { lastPaint = performance.now(); const follow = nearBottom(); paintLive(el, ai); if (follow) scrollMsgs(true); } }); };
  try {
    const r = await api("/api/chat", { body, raw: true, signal: S.ctrl.signal });
    for await (const ev of lines(r)) {
      if (ev.error) { ai.error = ev.error; break; }
      const m = ev.message || {};
      if (m.thinking) ai.thinking += m.thinking;
      if (m.content) ai.content += m.content;
      if (ev.done) {
        gotDone = true;
        if (ev.eval_count) {
          const tps = ev.eval_duration ? ev.eval_count / ev.eval_duration * 1e9 : 0;
          ai.stats = `${nameOf(who)} · ${model.replace(/^ext:[^:]+:/, "")} · ${ev.eval_count} tokens${tps ? ` · ${tps.toFixed(1)} tokens/秒` : ""}`;
        }
      }
      paint();
    }
    if (!gotDone && !ai.error) ai.error = "連線中斷，回答可能不完整";
  } catch (err) {
    if (err.name === "AbortError") ai.stopped = true; else ai.error = err.message;
  } finally {
    live = false; S.busy = false; S.ctrl = null;
    $("#send").hidden = false; $("#stop").hidden = true;
    fillMsg(el, ai, false); scrollMsgs(); saveNow(conv); if (S.cur === conv) renderPartnerChip();
  }
  return ai;
}

// ── 誰該接話 ──
const mentionsKiki = t => /(^|[\s,，。！？!?(（])@(kiki|小機|ai)\b/i.test(t);
const mentionsPartner = t => { const p = partner(); return !!p && new RegExp(`(^|[\\s,，。！？!?(（])@${p.handle}\\b`, "i").test(t); };
async function sendChat(text, carry) {
  if (!S.cur.model) { toast("先選一個模型", true); return; }
  const user = { role: "user", by: "user", content: text };
  if (carry) { for (const k of ["images", "files", "keep"]) if (carry[k]) user[k] = carry[k]; }   // 編輯重送：沿用原本那則的附件
  else {
    Object.assign(user, takeAttach());
  }
  const conv = S.cur;
  conv.messages.push(user);
  $("#empty").hidden = true;
  $("#msgs").appendChild(msgEl(user)); scrollMsgs(true);
  saveNow(conv);                      // 使用者的話先存，重新整理或手機休眠也不會掉
  const p = partner();
  const toPartner = p && mentionsPartner(text), toKiki = !p || !toPartner || mentionsKiki(text);
  PT.depth = 0;
  if (toKiki) await chain("kiki", conv);
  if (toPartner && !S.busy && S.cur === conv) await chain(p.handle, conv);
}
async function chain(who, conv) {
  // 一位 AI 回完，如果它 @ 了另一位，另一位接著回；最多 MAX_HOPS 回合；使用者切走就停
  if (S.cur !== conv) return;
  const ai = await askAI(who);
  if (!ai || ai.error || ai.stopped || PT.depth >= MAX_HOPS || S.cur !== conv) return;
  const p = partner(), text = splitThink(ai).content;
  const next = who === "kiki" ? (p && mentionsPartner(text) ? p.handle : null) : (mentionsKiki(text) ? "kiki" : null);
  if (next) { PT.depth++; await chain(next, conv); }
}
// 討論：夥伴評論 Kiki 最後的回答，Kiki 再回一次
$("#discuss-btn").addEventListener("click", async () => {
  const p = partner();
  if (!p || S.busy) return;
  const user = { role: "user", by: "user", content: `@${p.handle} 請看上面 Kiki 的回答，指出你同意或不同意的地方、補充遺漏，最後用 @kiki 把要修正的地方交代給她。` };
  S.cur.messages.push(user); $("#msgs").appendChild(msgEl(user)); scrollMsgs(true);
  PT.depth = 0; await chain(p.handle, S.cur);
});

// ── 思考強度（像 Claude Code 的「高」） ──
S.think = ls.get("think", "high");
function renderThinkChip() {
  const zh = { high: "高", mid: "中", off: "關" };
  $("#think-chip span").textContent = "思考 " + zh[S.think];
  $("#think").checked = S.think !== "off";
}
$("#think-chip").addEventListener("click", () => {
  S.think = { high: "mid", mid: "off", off: "high" }[S.think]; ls.set("think", S.think); renderThinkChip();
  if (S.cur) { S.cur.settings = Object.assign({}, S.cur.settings, { think: S.think !== "off" }); queueSave(); }
});
renderThinkChip();
