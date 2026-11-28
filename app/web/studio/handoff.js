"use strict";
// 交接檔（自動壓縮）：對話變長，本機模型會變慢、也容易忽略前面的重點。所以超過門檻時，
// 先把前面的對話整理成一份「交接檔」，之後 Kiki 只看「交接檔＋最近幾輪原文」。
// 原始訊息照樣留在畫面和存檔裡，只是不再送給模型。雲端夥伴不受影響（它們的上限大，照舊拿完整對話）。
//
// conv.handoff = { text, upto, sig, at, model, edited }
//   前 upto 則訊息已經整理進 text；sig 是那 upto 則的指紋，前面的訊息被編輯重送／刪掉時對不上，交接檔就作廢重做。
// 接在 files.js 後面載入（用到 estTokens、ctxOptions、filesText）。

const HO = {
  MAX_CTX: 32768,      // 跟 ctxOptions 一樣，Kiki 一次最多開到 32K
  TRIGGER: 12000,      // 要送的內容超過這麼多 token（或模型上限的六成，取小的）就整理
  KEEP: 0.35,          // 最近的對話保留原文的份量（觸發門檻的比例）
  MIN_OLD: 1500,       // 舊的部分太少就不整理（自動時）
  CHUNK: 16000,        // 一次最多拿多少 token 的舊對話去整理，超過就分段、一段一段滾進交接檔
  OUT: 3000,           // 交接檔最長（給模型的 num_predict）
  FILE_CHARS: 3000,    // 整理時每個附件最多看多少字
  MAX_TEXT: 12000,     // 交接檔存檔上限（字）
  MIN_CAP: 8192,       // 模型一次能讀的長度比這個短就不整理（交接檔＋整理用的提示詞就塞滿了）
  RETRY_AFTER: 6,      // 整理失敗後，再多幾則訊息才自動重試
};

const hoTok = s => estTokens([{ content: s }]);

// 前面 n 則訊息的指紋：只要其中任何一則被改、刪、插入，就會不一樣
function hoSig(msgs) {
  let h = 5381;
  for (const m of msgs) {
    const s = `${m.role}|${m.by || ""}|${m.content || ""}\u0001`;
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  }
  return msgs.length + ":" + (h >>> 0).toString(36);
}

// 現在還能用的交接檔（list = 要送的訊息清單）；對不上就回 null
function liveHandoff(conv, list) {
  const h = conv?.handoff;
  if (!h || !h.text || !(h.upto > 0) || h.upto > list.length) return null;
  return h.sig === hoSig(list.slice(0, h.upto)) ? h : null;
}

function hoLimit(model) {
  const cap = Math.min(HO.MAX_CTX, S.caps[model]?.context_length || HO.MAX_CTX);
  const trigger = Math.min(HO.TRIGGER, Math.round(cap * 0.6));
  return { cap, trigger, keep: Math.round(trigger * HO.KEEP), chunk: Math.max(2000, Math.min(HO.CHUNK, cap - HO.OUT - 1500)) };
}

// 一則訊息在逐字稿裡的樣子（給整理用的模型看）
function hoLine(m) {
  const by = speaker(m);
  let text = ((by === "user" ? m.content : splitThink(m).content) || "").trim();
  if (m.files?.length) {
    const f = m.files.map(x => `〈附件：${x.name}〉\n${String(x.text || "").slice(0, HO.FILE_CHARS)}${String(x.text || "").length > HO.FILE_CHARS ? "\n…（附件後面省略）" : ""}`).join("\n");
    text = f + (text ? "\n" + text : "");
  }
  if (m.images?.length) text += `${text ? "\n" : ""}（附了 ${m.images.length} 張圖片）`;
  if (m.gen) text += `${text ? "\n" : ""}（生成了一${m.gen.kind === "video" ? "段影片" : "張圖"}）`;
  return text ? `${nameOf(by)}：${text}` : "";
}
// 單一則太長（例如很大的附件）就頭尾各留一段
function hoClip(line, maxTok) {
  if (hoTok(line) <= maxTok) return line;
  const keep = Math.max(500, Math.floor(line.length * maxTok / hoTok(line) / 2));
  return line.slice(0, keep) + "\n…（中間太長，省略）…\n" + line.slice(-keep);
}

// 從哪一則開始保留原文：至少保留最後兩則，盡量從使用者的話開始
function hoCut(list, from, keepTok) {
  let cut = list.length - 1, tok = hoTok(hoLine(list[cut]));
  while (cut > from && tok + hoTok(hoLine(list[cut - 1])) <= keepTok) { cut--; tok += hoTok(hoLine(list[cut])); }
  if (cut > list.length - 2 && list.length - 2 > from) cut = list.length - 2;
  let c = cut;
  while (c > from && speaker(list[c]) !== "user") c--;
  return c > from ? c : cut;
}

const HO_SYS = "你負責寫「交接檔」：把一段對話整理成重點，讓接手的 AI 不看原始對話也能無縫接著做。只輸出交接檔本身，不要開場白、不要解釋。用繁體中文（台灣用語）。";
function hoPrompt(prev, transcript) {
  return (prev ? `【目前的交接檔（更早的對話整理）】\n${prev}\n\n【接下來的對話】\n` : "【要整理的對話】\n") + transcript + `

請寫一份${prev ? "整合後的新" : ""}交接檔，照下面的格式（沒有內容的段落寫「無」）：
## 目標
使用者想完成什麼。
## 已確定的事
做過的決定、結論、已經完成的東西。
## 重要細節
名字、數字、日期、檔名、路徑、網址、指令、程式碼關鍵片段，照原樣保留，不要改寫或四捨五入。
## 使用者的偏好與要求
語氣、格式、限制、不要做的事。
## 還沒解決／待辦
## 最後在做什麼
最後幾輪在討論什麼、接手後要從哪裡繼續。

${prev ? "舊交接檔裡仍然有效的內容要保留；被後來的對話推翻或更新的，改成最新的。" : ""}要具體、不寫空話，總長控制在 1500 字以內。`;
}

// 叫模型寫一段交接檔。numCtx 整個整理過程用同一個值：Ollama 每換一次長度就要重新載入模型
async function hoAsk(model, prev, transcript, numCtx, signal) {
  const messages = [{ role: "system", content: HO_SYS }, { role: "user", content: hoPrompt(prev, transcript) }];
  const body = { model, messages, options: { temperature: 0.3, num_predict: HO.OUT } };
  if (numCtx) body.options.num_ctx = numCtx;
  if ((S.caps[model]?.capabilities || []).includes("thinking")) body.think = /^gpt-oss/.test(model) ? "low" : false;   // 整理不用長考
  const r = await api("/api/chat", { body, raw: true, signal });
  let out = "", done = false, reason = "";
  for await (const ev of lines(r)) {
    if (ev.error) throw new Error(ev.error);
    if (ev.message?.content) out += ev.message.content;
    if (ev.done) { done = true; reason = ev.done_reason || ""; }
  }
  const text = splitThink({ content: out }).content.trim();
  if (!done) throw new Error("連線中斷");
  if (reason === "length") throw new Error("交接檔寫到一半就到長度上限，沒寫完");   // 寫一半的不能用，後面幾段會永遠遺失
  if (text.length < 40) throw new Error("模型沒有寫出交接檔");
  return text.slice(0, HO.MAX_TEXT);
}

// 把 list[from, cut) 滾進交接檔（太長就分段）
async function hoBuild(model, list, from, cut, prev, chunkTok, signal) {
  let text = prev || "", i = from;
  const need = Math.min(chunkTok, hoTok(text) + list.slice(from, cut).reduce((n, m) => n + hoTok(hoLine(m)), 0)) + HO.OUT + 1500;
  const cap = hoLimit(model).cap;      // 跟 ctxOptions 同一套長度級距
  const numCtx = need <= 4096 ? 0 : Math.min(cap, need <= 8192 ? 8192 : need <= 16384 ? 16384 : 32768);
  while (i < cut) {
    const budget = Math.max(1500, chunkTok - hoTok(text));
    const part = [];
    let tok = 0;
    for (; i < cut; i++) {
      const line = hoLine(list[i]);
      if (!line) continue;
      const t = hoTok(line);
      if (part.length && tok + t > budget) break;
      part.push(hoClip(line, budget)); tok += Math.min(t, budget);
    }
    if (part.length) text = await hoAsk(model, text, part.join("\n\n"), numCtx, signal);
  }
  return text;
}

// 送給 Kiki 之前呼叫：太長就先整理。回傳 "done"／"skip"／"short-model"（模型能讀的長度太短）／"failed"／"aborted"（使用者按停止）
async function maybeHandoff(conv, model, opt = {}) {
  if (!conv) return "skip";
  const list = conv.messages;
  let h = opt.fresh ? null : liveHandoff(conv, list);
  if (conv.handoff && !h && !opt.fresh) { conv.handoff = null; if (S.cur === conv) renderHandoff(); }   // 前面被改過，舊的作廢
  if (!model || String(model).startsWith("ext:")) return "skip";
  const lim = hoLimit(model), from = h ? h.upto : 0;
  if (lim.cap < HO.MIN_CAP) return "short-model";   // 一次只能讀幾千字的模型，交接檔本身就塞不下
  if (!opt.force) {
    if (conv.settings?.noHandoff) return "skip";
    if (conv._hoFailAt && list.length - conv._hoFailAt < HO.RETRY_AFTER) return "skip";   // 剛失敗過：多聊幾則再試，不要每則都重來
    if (estTokens(contextFor("kiki", list, conv)) + 2048 <= lim.trigger) return "skip";
  }
  const cut = hoCut(list, from, lim.keep);
  if (cut - from < 2) return "skip";
  if (!opt.force && list.slice(from, cut).reduce((n, m) => n + hoTok(hoLine(m)), 0) < HO.MIN_OLD) return "skip";

  const box = $("#msgs"), note = document.createElement("div");
  note.className = "handoff working";
  note.textContent = h ? "對話又變長了，正在更新交接檔…" : "對話變長了，正在把前面的對話整理成交接檔…（整理好之後 Kiki 只看交接檔＋最近的對話，會比較快）";
  if (S.cur === conv) { box.appendChild(note); scrollMsgs(true); }
  S.busy = true; S.ctrl = new AbortController();
  $("#send").hidden = true; $("#stop").hidden = false;
  const sig = hoSig(list.slice(0, cut));
  try {
    const text = await hoBuild(model, list.slice(), from, cut, h ? h.text : "", lim.chunk, S.ctrl.signal);
    if (hoSig(conv.messages.slice(0, cut)) !== sig) return "skip";   // 整理期間前面的對話被改了，這份不算數
    conv.handoff = { text, upto: cut, sig, at: Math.round(Date.now() / 1000), model, edited: false };
    conv._hoFailAt = 0;
    if (conv.settings?.noHandoff) conv.settings = Object.assign({}, conv.settings, { noHandoff: false });
    saveNow(conv);
    toast(`已把前面 ${cut} 則訊息整理成交接檔；Kiki 之後只看交接檔＋最近 ${list.length - cut} 則`);
    return "done";
  } catch (err) {
    if (err.name === "AbortError") return "aborted";
    conv._hoFailAt = list.length;
    toast("整理交接檔失敗：" + err.message + "。這次先照原本的方式送，多聊幾則後會再試", true);
    return "failed";
  } finally {
    note.remove();
    S.busy = false; S.ctrl = null;
    $("#send").hidden = false; $("#stop").hidden = true;
    if (S.cur === conv) renderHandoff();
  }
}

// ── 畫面：在被整理的訊息下面放一條「交接檔」 ──
function hoTime(t) { try { return new Date(t * 1000).toLocaleString("zh-Hant", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }); } catch { return ""; } }
function renderHandoff() {
  const box = $("#msgs"), c = S.cur;
  $$(":scope > .handoff:not(.working)", box).forEach(x => x.remove());
  const els = $$(":scope > .msg", box);
  els.forEach(e => e.classList.remove("compacted"));
  if (!c) return;
  const h = liveHandoff(c, c.messages);
  if (!h) { if (c.handoff) { c.handoff = null; queueSave(); } return; }
  els.slice(0, h.upto).forEach(e => e.classList.add("compacted"));
  const d = document.createElement("div");
  d.className = "handoff";
  d.innerHTML = `<details><summary><b>交接檔</b><span>前面 ${h.upto} 則已整理，Kiki 現在只看這份＋下面的對話</span><span class="muted small">${esc(hoTime(h.at))}${h.edited ? " · 改過" : ""}</span></summary>
    <div class="ho-body">${md(h.text)}</div>
    <div class="ho-acts"><button type="button" class="btn sm" data-ho="edit">編輯</button><button type="button" class="btn sm" data-ho="redo">重新整理</button><button type="button" class="btn sm" data-ho="off">移除</button></div></details>`;
  if (els[h.upto]) { box.insertBefore(d, els[h.upto]); els[h.upto].classList.remove("cont"); } else box.appendChild(d);
}

$("#msgs").addEventListener("click", async e => {
  const b = e.target.closest("[data-ho]");
  if (!b) return;
  const c = S.cur, h = c && liveHandoff(c, c.messages), d = b.closest(".handoff");
  if (!h) { renderHandoff(); return; }
  const act = b.dataset.ho;
  if (act === "edit") {
    const body = $(".ho-body", d);
    body.innerHTML = `<textarea class="ho-edit" rows="14" aria-label="交接檔內容"></textarea>`;
    $(".ho-edit", body).value = h.text;
    $(".ho-acts", d).innerHTML = `<button type="button" class="btn sm primary" data-ho="save">存</button><button type="button" class="btn sm" data-ho="cancel">取消</button>`;
    $(".ho-edit", body).focus();
  } else if (act === "save") {
    const t = $(".ho-edit", d).value.trim();
    if (!t) { toast("交接檔不能是空的；不要的話按「移除」", true); return; }
    c.handoff = Object.assign({}, h, { text: t.slice(0, HO.MAX_TEXT), edited: true });
    queueSave(0); renderHandoff(); $(".handoff details")?.setAttribute("open", "");
  } else if (act === "cancel") {
    renderHandoff(); $(".handoff details")?.setAttribute("open", "");
  } else if (act === "redo") {
    if (S.busy) { toast("等這則回完再整理"); return; }
    if (!confirm("從頭重新整理交接檔？（會用目前的模型重讀前面所有的對話，對話很長時要等一下）")) return;
    await maybeHandoff(c, c.model, { force: true, fresh: true });
  } else if (act === "off") {
    if (!confirm("移除交接檔？之後會把完整對話送給 Kiki，這個對話也不再自動整理（對話很長時會比較慢）。要整理時打 /壓縮。")) return;
    c.handoff = null;
    c.settings = Object.assign({}, c.settings, { noHandoff: true });
    queueSave(0); renderHandoff(); toast("已移除交接檔，這個對話不再自動整理");
  }
});

// /壓縮：現在就整理（不管長度）
async function compactNow() {
  const c = S.cur;
  if (!c || S.busy) return;
  if (!c.model) { toast("先選一個模型", true); return; }
  if (c.model.startsWith("ext:")) { toast("雲端模型不用整理交接檔"); return; }
  const r = await maybeHandoff(c, c.model, { force: true });
  if (r === "short-model") toast(`這個模型一次只能讀約 ${hoLimit(c.model).cap} 個 token，太短，不適合整理交接檔`, true);
  else if (r === "skip") toast(liveHandoff(c, c.messages) ? "交接檔已經是最新的，沒有新的內容要整理" : "對話還很短，不用整理");
}
SLASH.push({ k: "/壓縮", d: "把前面的對話整理成交接檔（之後 Kiki 只看交接檔＋最近幾輪）", run: () => compactNow() });
