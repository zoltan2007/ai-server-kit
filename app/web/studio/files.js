"use strict";
// 附加檔案：圖片、文字／程式碼檔、PDF。檔案在瀏覽器裡處理（縮圖、抽文字），原檔不會上傳到主機；
// 跟著訊息存進對話的只有縮過的圖和抽出來的文字。接在 pdftext.js 後面載入。

const AT = { maxFiles: 8, pdfRaw: 12 * 1024 * 1024, textRaw: 2 * 1024 * 1024, imgRaw: 20 * 1024 * 1024,
  fileChars: 120000, msgChars: 240000, imgSide: 1568, pasteChars: 6000 };
const FILE_ICON = '<svg viewBox="0 0 24 24"><path d="M7 3h7l5 5v13H7zM14 3v5h5"/></svg>';
const PIN_ICON = '<svg viewBox="0 0 24 24"><path d="M12 17v5M8 3h8l-1 7 3 3H6l3-3z"/></svg>';
const canSee = () => (S.caps[S.cur?.model]?.capabilities || []).includes("vision");
const fmtChars = n => (n >= 10000 ? (n / 10000).toFixed(1) + " 萬字" : n.toLocaleString() + " 字");
const fileMeta = f => `${f.type === "pdf" ? `PDF · ${f.pages} 頁 · ` : ""}${fmtChars(f.text.length)}${f.cut ? " · 只附前段" : ""}`;
const dataUrl = f => new Promise((ok, no) => { const fr = new FileReader(); fr.onload = () => ok(String(fr.result)); fr.onerror = () => no(new Error("讀不了這個檔案")); fr.readAsDataURL(f); });

// ── 讀檔 ──
async function readImage(f) {
  if (f.size > AT.imgRaw) throw new Error("圖片太大（最多 20 MB）");
  let bmp;
  try { bmp = await createImageBitmap(f); } catch { throw new Error("這張圖讀不了"); }
  const k = Math.min(1, AT.imgSide / Math.max(bmp.width, bmp.height));
  if (k === 1 && f.size <= 400 * 1024 && /^image\/(png|jpeg)$/.test(f.type)) {
    bmp.close();
    const url = await dataUrl(f);
    return { kind: "image", name: f.name, b64: url.split(",")[1], url };
  }
  // 大圖縮到長邊 1568 再存：手機照片動輒 5 MB，原圖會把對話檔撐爆，模型也用不到那麼多像素
  const cv = document.createElement("canvas");
  cv.width = Math.max(1, Math.round(bmp.width * k)); cv.height = Math.max(1, Math.round(bmp.height * k));
  const g = cv.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, cv.width, cv.height); g.drawImage(bmp, 0, 0, cv.width, cv.height);
  bmp.close();
  const url = cv.toDataURL("image/jpeg", 0.85);
  return { kind: "image", name: f.name, b64: url.split(",")[1], url };
}
async function readText(f) {
  if (f.size > AT.textRaw) throw new Error("太大（文字檔最多 2 MB）");
  const b = new Uint8Array(await f.arrayBuffer());
  if (b[0] === 0xff && b[1] === 0xfe) return new TextDecoder("utf-16le").decode(b);
  if (b[0] === 0xfe && b[1] === 0xff) return new TextDecoder("utf-16be").decode(b);
  if (b.subarray(0, 8000).includes(0)) throw new Error("不支援這種檔案（可以附圖片、文字／程式碼檔、PDF）");
  try { return new TextDecoder("utf-8", { fatal: true }).decode(b); }
  catch { return new TextDecoder("big5").decode(b); }      // 舊的中文文字檔、Excel 另存的 CSV 常是 Big5
}
async function addFiles(list) {
  const files = [...list];
  if (!files.length) return;
  if (inRoom()) { toast("群組裡還不能附檔案", true); return; }
  if (!S.cur) return;
  for (const f of files) {
    if (S.attach.length >= AT.maxFiles) { toast(`一則訊息最多附 ${AT.maxFiles} 個`, true); break; }
    try {
      if (f.type.startsWith("image/")) {
        if (!canSee()) throw new Error("目前的模型不能看圖，先換一個有「看圖」能力的模型");
        S.attach.push(await readImage(f));
      } else S.attach.push(await readDoc(f));
    } catch (err) { toast(`${f.name || "檔案"}：${err.message}`, true); }
    renderAttach();
  }
  $("#input").focus();
}
async function readDoc(f) {
  const pdf = f.type === "application/pdf" || /\.pdf$/i.test(f.name), extra = {};
  let text;
  if (pdf) {
    if (f.size > AT.pdfRaw) throw new Error("太大（PDF 最多 12 MB）");
    toast(`正在讀 ${f.name}…`);
    const r = await pdfText(await f.arrayBuffer(), AT.fileChars);
    if (r.text.length < 12) throw new Error(r.lost ? "這個 PDF 的字型沒有附文字對照表，抽不出文字" : "抽不出文字（可能是掃描的圖片檔）");
    text = r.text; extra.pages = r.pages; extra.cut = r.truncated;
  } else text = await readText(f);
  text = text.replace(/\r\n?/g, "\n").replace(/\u0000/g, "");
  if (!text.trim()) throw new Error("檔案是空的");
  return fitDoc(f.name, pdf ? "pdf" : "text", f.size, text, extra);
}
function fitDoc(name, type, size, text, extra = {}) {
  const used = S.attach.reduce((a, x) => a + (x.text ? x.text.length : 0), 0), lim = Math.min(AT.fileChars, AT.msgChars - used);
  if (lim <= 0) throw new Error("這則訊息的附件文字已經到上限");
  if (text.length > lim) { text = text.slice(0, lim); extra.cut = true; toast(`${name} 太長，只附前面 ${fmtChars(lim)}`, true); }
  return Object.assign({ kind: "file", name: String(name).slice(0, 120), type, size, text }, extra);
}

// ── 輸入框上方的附件列 ──
function renderAttach() {
  $("#attach-list").innerHTML = S.attach.map((a, i) => (a.kind === "image"
    ? `<span class="athumb"><img src="${a.url}" alt="${esc(a.name || "圖片")}"><button type="button" data-rm="${i}" aria-label="移除圖片">×</button></span>`
    : `<span class="fchip">${FILE_ICON}<span class="fn"><b>${esc(a.name)}</b><i>${fileMeta(a)}</i></span><button type="button" data-rm="${i}" aria-label="移除 ${esc(a.name)}">×</button></span>`)).join("");
}
function takeAttach() {      // 送出時把附件掛到訊息上
  const out = {}, imgs = S.attach.filter(a => a.kind === "image"), docs = S.attach.filter(a => a.kind === "file");
  if (imgs.length) out.images = imgs.map(a => a.b64);
  if (docs.length) out.files = docs.map(({ name, type, size, text, pages, cut }) => ({ name, type, size, text, pages, cut }));
  S.attach = []; renderAttach();
  return out;
}
$("#attach-btn").addEventListener("click", () => $("#attach-input").click());
$("#attach-input").addEventListener("change", async e => { const list = [...e.target.files]; e.target.value = ""; await addFiles(list); });
$("#attach-list").addEventListener("click", e => { const b = e.target.closest("[data-rm]"); if (b) { S.attach.splice(+b.dataset.rm, 1); renderAttach(); } });
const comp = $("#composer");
["dragenter", "dragover"].forEach(ev => comp.addEventListener(ev, e => { e.preventDefault(); comp.classList.add("drop"); }));
["dragleave", "drop"].forEach(ev => comp.addEventListener(ev, () => comp.classList.remove("drop")));
comp.addEventListener("drop", e => { e.preventDefault(); addFiles(e.dataTransfer.files); });
$("#input").addEventListener("paste", e => {
  const cd = e.clipboardData;
  if (!cd) return;
  if (cd.files && cd.files.length) { e.preventDefault(); addFiles(cd.files); return; }
  const text = cd.getData("text/plain");
  if (text.length > AT.pasteChars && !inRoom()) {          // 貼一大段：收成附件，輸入框留給你要問的話
    e.preventDefault();
    if (S.attach.length >= AT.maxFiles) { toast(`一則訊息最多附 ${AT.maxFiles} 個`, true); return; }
    try { S.attach.push(fitDoc("貼上的文字.txt", "text", text.length, text.replace(/\r\n?/g, "\n"))); renderAttach(); toast("內容比較長，已經收成附件"); }
    catch (err) { toast(err.message, true); }
  }
});

// ── 訊息裡的附件：檔案小卡、圖片固定鈕 ──
const _fillMsgFiles = fillMsg;
fillMsg = function (d, m, live) {
  _fillMsgFiles(d, m, live);
  if (m.role !== "user" || inRoom()) return;
  const bubble = d.querySelector(".bubble");
  if (m.files?.length) {
    const box = document.createElement("div");
    box.className = "fchips";
    box.innerHTML = m.files.map((f, i) => `<button type="button" class="fchip" data-file="${i}" aria-label="在右側打開附件 ${esc(f.name)}">${FILE_ICON}<span class="fn"><b>${esc(f.name)}</b><i>${fileMeta(f)}</i></span></button>`).join("");
    d.insertBefore(box, bubble);
  }
  if (bubble && !m.content && !m.images?.length) bubble.hidden = true;
  if (m.images?.length) d.querySelector(".acts")?.insertAdjacentHTML("afterbegin", keepBtn(m));
};
const keepLabel = m => (m.keep ? "已固定：就算之後附了新圖，這些圖每一輪也會一起送給 Kiki（點一下取消）" : "Kiki 每一輪只會看到最近附的那批圖；點一下固定，讓這些圖一直留著");
const keepBtn = m => `<button type="button" data-a="keep" class="${m.keep ? "on" : ""}" title="${keepLabel(m)}" aria-label="${keepLabel(m)}" aria-pressed="${!!m.keep}">${PIN_ICON}</button>`;
$("#msgs").addEventListener("click", e => {
  const k = e.target.closest('.acts [data-a="keep"]'), f = e.target.closest(".fchips [data-file]");
  const m = MSG.get((k || f)?.closest(".msg"));
  if (!m) return;
  if (k) { m.keep = !m.keep; k.outerHTML = keepBtn(m); queueSave(0); toast(m.keep ? "已固定：之後每一輪都會帶上這些圖" : "取消固定：有新圖時這些就不再送"); return; }
  const doc = m.files[+f.dataset.file];
  if (doc && typeof openArt === "function") openArt({ title: doc.name, name: doc.type === "pdf" ? doc.name + ".txt" : doc.name, lang: doc.type === "pdf" ? "text" : fileLang(doc.name), code: doc.text, note: doc.type === "pdf" ? "從 PDF 抽出來的文字" : "你附的檔案" });
});

// ── 給模型看的附件文字 ──
function filesText(files, full) {
  return files.map(f => (full
    ? `〈附件：${f.name}${f.pages ? `（PDF，共 ${f.pages} 頁）` : ""}${f.cut ? "（內容太長，只有前段）" : ""}〉\n${f.text}\n〈附件結束：${f.name}〉`
    : `〈附件：${f.name}（使用者沒有開放內容給你）〉`)).join("\n\n");
}
// 粗估 token 數（中文一個字約一個 token、英數約 3.6 個字元一個；不同模型會差到三四成），只用來決定要不要把 num_ctx 調大
function estTokens(msgs) {
  let n = 0;
  for (const m of msgs) {
    const t = m.content || "", cjk = (t.match(/[⺀-鿿가-힯＀-￯]/g) || []).length;
    n += cjk * 1.1 + (t.length - cjk) / 3.6 + 8 + (m.images ? m.images.length * 800 : 0);
  }
  return Math.round(n);
}
function ctxOptions(model, msgs) {
  const need = estTokens(msgs) + 2048;                    // 留位置給回答
  if (need <= 4096) return null;                          // 短對話不動，照 Ollama 自己的預設
  const cap = Math.min(32768, S.caps[model]?.context_length || 32768);
  return { num_ctx: Math.min(cap, need <= 8192 ? 8192 : need <= 16384 ? 16384 : 32768), over: need > cap };
}
