"use strict";
// 成品窗格：把 AI 寫的程式碼、整則回答、你附的檔案開在右側，可以複製、下載、預覽。接在 files.js 後面載入。
// 預覽不會執行任何程式：HTML 放進完全鎖住的 sandbox iframe（不能跑 JavaScript、拿不到登入資料），SVG 當圖片顯示。

const ART = { cur: null, view: "code", items: [] };
const ART_EXT = { python: "py", py: "py", javascript: "js", js: "js", typescript: "ts", ts: "ts", jsx: "jsx", tsx: "tsx", html: "html", css: "css",
  json: "json", bash: "sh", sh: "sh", shell: "sh", zsh: "sh", powershell: "ps1", ps1: "ps1", bat: "bat", markdown: "md", md: "md", svg: "svg",
  xml: "xml", sql: "sql", yaml: "yml", yml: "yml", toml: "toml", ini: "ini", c: "c", cpp: "cpp", "c++": "cpp", h: "h", java: "java", go: "go",
  rust: "rs", rs: "rs", php: "php", ruby: "rb", rb: "rb", swift: "swift", kotlin: "kt", kt: "kt", csharp: "cs", cs: "cs", csv: "csv", log: "log" };
const ART_PROSE = new Set(["", "text", "txt", "log", "markdown", "md"]);
const ART_ICON = { back: '<svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>' };
function artKind(a) {
  const l = (a.lang || "").toLowerCase();
  if (l === "markdown" || l === "md") return "md";
  if (l === "svg" || /^\s*(<\?xml[^>]*>\s*)?<svg[\s>]/i.test(a.code)) return "svg";
  if (l === "html" || l === "htm" || /^\s*(<!doctype html|<html[\s>])/i.test(a.code)) return "html";
  return "";
}
const artMeta = a => [a.lang && !ART_PROSE.has(a.lang.toLowerCase()) ? a.lang : "", a.code.split("\n").length + " 行", a.note].filter(Boolean).join(" · ");
const fileLang = name => (name.includes(".") ? name.split(".").pop().toLowerCase() : "text");

// 這個對話裡所有能開的東西：AI 寫的程式碼（三行以上）、使用者附的檔案
function collectArts() {
  const out = [], c = S.cur;
  if (!c) return out;
  c.messages.forEach(m => {
    if (m.role === "user") {
      for (const f of m.files || []) out.push({ title: f.name, name: f.type === "pdf" ? f.name + ".txt" : f.name, lang: f.type === "pdf" ? "text" : fileLang(f.name), code: f.text, note: f.type === "pdf" ? "從 PDF 抽出來的文字" : "你附的檔案" });
      return;
    }
    const L = splitThink(m).content.split("\n"), who = nameOf(m.by || "kiki");
    for (let i = 0; i < L.length; i++) {
      const h = L[i].match(/^\s*```\s*([\w+#.-]*)/);
      if (!h) continue;
      const buf = [];
      for (i++; i < L.length && !/^\s*```\s*$/.test(L[i]); i++) buf.push(L[i]);
      if (buf.length >= 3) out.push({ title: (buf.find(x => x.trim()) || h[1] || "程式碼").trim().slice(0, 48), lang: h[1], code: buf.join("\n"), note: who + " 寫的" });
    }
  });
  return out;
}

function renderArt() {
  const box = $("#art-pane");
  if (!box || box.hidden) return;
  if (!ART.cur) {
    ART.items = collectArts();
    box.innerHTML = ART.items.length
      ? `<div class="recent-head" style="margin:2px 2px 0">這個對話裡的程式碼與檔案</div>` + ART.items.map((a, i) => `<button class="rcard" data-art-i="${i}"><b>${esc(a.title)}</b><span>${esc(artMeta(a))}</span></button>`).join("")
      : `<div class="stat"><h4>成品</h4><p class="muted small" style="margin:0">這個對話還沒有可以開的東西。Kiki 寫的程式碼、你附的檔案會列在這裡；回答下面的文件圖示可以把整則回答開成文件。</p></div>`;
    return;
  }
  const a = ART.cur, kind = artKind(a), view = kind && ART.view === "preview" ? "preview" : "code";
  const seg = kind ? `<div class="seg"><button data-art-do="code" class="${view === "code" ? "on" : ""}">原始碼</button><button data-art-do="preview" class="${view === "preview" ? "on" : ""}">預覽</button></div>` : "";
  box.innerHTML = `<div class="art-head"><button class="icon-btn" data-art-do="back" title="回清單" aria-label="回清單">${ART_ICON.back}</button>
      <div class="art-title"><b>${esc(a.title)}</b><span class="muted small">${esc(artMeta(a))}</span></div></div>
    <div class="art-bar">${seg}<span class="grow"></span><button class="btn sm" data-art-do="copy">複製</button><button class="btn sm primary" data-art-do="save">下載</button></div>
    <div class="art-body" id="art-body"></div>
    ${view === "preview" && kind === "html" ? `<p class="muted small" style="margin:0">預覽不會執行 JavaScript，也不會載入外部的圖片或字型。要看完整效果請下載後用瀏覽器打開。</p>` : ""}`;
  const body = $("#art-body");
  if (view === "code") {
    const pre = document.createElement("pre");
    pre.className = "art-code" + (ART_PROSE.has((a.lang || "").toLowerCase()) ? " wrap" : "");
    pre.textContent = a.code; body.appendChild(pre);
  } else if (kind === "md") { body.classList.add("body"); body.innerHTML = md(a.code); }
  else if (kind === "svg") {
    const img = new Image();
    img.alt = "SVG 預覽"; img.className = "art-img"; img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(a.code);
    body.appendChild(img);
  } else {
    const f = document.createElement("iframe");
    f.setAttribute("sandbox", ""); f.setAttribute("referrerpolicy", "no-referrer");
    f.title = "HTML 預覽"; f.className = "art-frame"; f.srcdoc = a.code;
    body.appendChild(f);
  }
}
function openArt(a) {
  ART.cur = a; ART.view = a.view || (artKind(a) === "md" ? "preview" : "code");
  openRight(true); showRTab("art");
}
function openDoc(text) {
  const first = (text.split("\n").find(l => l.trim()) || "回答").replace(/^[#>*\-\s]+/, "").slice(0, 48);
  openArt({ title: first || "回答", name: "回答.md", lang: "markdown", code: text, note: "整則回答", view: "preview" });
}
function artFileName(a) {
  if (a.name) return a.name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_");
  const ext = ART_EXT[(a.lang || "").toLowerCase()] || (artKind(a) === "html" ? "html" : artKind(a) === "svg" ? "svg" : "txt");
  return `kiki-${new Date().toISOString().slice(0, 10)}.${ext}`;
}
$("#art-pane").addEventListener("click", e => {
  const pick = e.target.closest("[data-art-i]");
  if (pick) { openArt(ART.items[+pick.dataset.artI]); return; }
  if (e.target.closest("[data-copy]")) { copyText($("pre", e.target.closest(".code")).textContent); return; }
  const b = e.target.closest("[data-art-do]"), a = ART.cur;
  if (!b || !a) return;
  const act = b.dataset.artDo;
  if (act === "back") { ART.cur = null; renderArt(); }
  if (act === "code" || act === "preview") { ART.view = act; renderArt(); }
  if (act === "copy") copyText(a.code);
  if (act === "save") {
    const link = document.createElement("a"), name = artFileName(a);
    link.href = URL.createObjectURL(new Blob([a.code], { type: "text/plain;charset=utf-8" })); link.download = name;
    document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(link.href), 60000);
    toast("已下載：" + name);
  }
});
// 程式碼區塊上的「開啟」
document.addEventListener("click", e => {
  const b = e.target.closest(".code [data-art]");
  if (!b) return;
  const box = b.closest(".code"), label = $(".code-bar span", box).textContent, code = $("pre", box).textContent;
  openArt({ title: (code.split("\n").find(x => x.trim()) || label).trim().slice(0, 48), lang: label === "程式碼" ? "" : label, code, note: "" });
});

// 右欄切到「成品」時加寬；換對話、AI 回完時更新清單
const _showRTabArt = showRTab;
showRTab = function (name) { _showRTabArt(name); app.classList.toggle("art-wide", name === "art"); if (name === "art") renderArt(); };
const _renderCurArt = renderCur;
renderCur = function () { ART.cur = null; _renderCurArt(); renderArt(); };
const _askAIArt = askAI;
askAI = async function (who) {
  const r = await _askAIArt(who);
  if (!ART.cur) renderArt();
  if (!$("#project-pane").hidden && document.activeElement?.id !== "pp-inst") renderProjectPane();     // 訊息數、更新時間跟著動；正在打專案指示就先不重畫
  return r;
};
if (ls.get("rtab", "host") === "art") showRTab("art");
SLASH.push({ k: "/成品", d: "在右側看這個對話裡的程式碼與檔案", run: () => { ART.cur = null; openRight(true); showRTab("art"); } });
