"use strict";
// PDF 抽文字：純瀏覽器、不載外部程式庫，檔案不會離開使用者的裝置（主機只會收到抽出來的文字）。
// 處理最常見的情況：FlateDecode 壓縮、物件串流、ToUnicode 對照表、標準西文編碼、表單 XObject。
// 抽不出來的：掃描檔（整頁是圖）、加密檔、沒有 ToUnicode 的雙位元組字型。

// codes：單一字型對照表的筆數上限；ms：整份最多讀多久（防特製的壞檔把分頁卡死）
const PDF_LIMIT = { stream: 24 * 1024 * 1024, total: 96 * 1024 * 1024, pages: 500, codes: 200000, cmap: 2 * 1024 * 1024, forms: 2000, ms: 20000 };
const PDF_IDENT = [1, 0, 0, 1, 0, 0];
const pdfIsWs = c => c === 32 || c === 10 || c === 13 || c === 9 || c === 12 || c === 0;
const pdfIsDict = v => !!v && typeof v === "object" && !Array.isArray(v) && v.r === undefined && v.s === undefined && v.n === undefined && v.op === undefined;
const pdfMul = (m, k) => [m[0] * k[0] + m[1] * k[2], m[0] * k[1] + m[1] * k[3], m[2] * k[0] + m[3] * k[2], m[2] * k[1] + m[3] * k[3],
  m[4] * k[0] + m[5] * k[2] + k[4], m[4] * k[1] + m[5] * k[3] + k[5]];
let pdfLatin = null;

function pdfBin(bytes) {            // Uint8Array → 每個位元組一個字元的字串
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return s;
}

// ── 語法：一次讀一個記號 ──
function pdfTok(s, i) {
  const n = s.length;
  for (;;) {
    while (i < n && pdfIsWs(s.charCodeAt(i))) i++;
    if (s[i] !== "%") break;
    while (i < n && s[i] !== "\n" && s[i] !== "\r") i++;
  }
  if (i >= n) return null;
  const c = s[i];
  if (c === "(") {
    let depth = 1, out = "", j = i + 1;
    while (j < n && depth) {
      const ch = s[j];
      if (ch === "\\") {
        const d = s[j + 1];
        if (d >= "0" && d <= "7") {
          let k = j + 1, oct = "";
          while (k < j + 4 && s[k] >= "0" && s[k] <= "7") oct += s[k++];
          out += String.fromCharCode(parseInt(oct, 8) & 255); j = k; continue;
        }
        if (d === "\r") { j += s[j + 2] === "\n" ? 3 : 2; continue; }
        if (d === "\n") { j += 2; continue; }
        out += { n: "\n", r: "\r", t: "\t", b: "\b", f: "\f" }[d] ?? (d || ""); j += 2; continue;
      }
      if (ch === "(") depth++;
      else if (ch === ")" && !--depth) { j++; break; }
      out += ch; j++;
    }
    return { t: "s", v: out, e: j };
  }
  if (c === "<" && s[i + 1] === "<") return { t: "<<", e: i + 2 };
  if (c === ">" && s[i + 1] === ">") return { t: ">>", e: i + 2 };
  if (c === "<") {
    const j = s.indexOf(">", i), end = j < 0 ? n : j;
    const hex = s.slice(i + 1, end).replace(/[^0-9A-Fa-f]/g, "");
    let out = "";
    for (let k = 0; k < hex.length; k += 2) out += String.fromCharCode(parseInt((hex.substr(k, 2) + "0").slice(0, 2), 16));
    return { t: "s", v: out, e: Math.min(n, end + 1) };
  }
  if ("[]{})>".includes(c)) return { t: c, e: i + 1 };
  let j = i + (c === "/" ? 1 : 0);
  while (j < n && !pdfIsWs(s.charCodeAt(j)) && !"()<>[]{}/%".includes(s[j])) j++;
  if (c === "/") return { t: "n", v: s.slice(i + 1, j).replace(/#([0-9A-Fa-f]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16))), e: j };
  if (j === i) return { t: "?", e: i + 1 };
  const w = s.slice(i, j);
  return w.length <= 40 && /^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(w) ? { t: "num", v: parseFloat(w), e: j } : { t: "w", v: w, e: j };
}
// 一個完整的值：字典 {…}、陣列、名稱 {n}、字串 {s}、參照 {r}、數字、運算子 {op}。回傳 [值, 結束位置]
function pdfVal(s, i, depth = 0) {
  const t = pdfTok(s, i);
  if (!t) return [null, s.length];
  if (depth > 60) return [null, t.e];
  if (t.t === "<<" || t.t === "[") {
    const dict = t.t === "<<", close = dict ? ">>" : "]", box = dict ? {} : [];
    let p = t.e;
    for (;;) {
      const k = pdfTok(s, p);
      if (!k || k.t === close) return [box, k ? k.e : s.length];
      if (dict && k.t !== "n") { p = k.e; continue; }
      const [v, e] = pdfVal(s, dict ? k.e : p, depth + 1);
      if (dict) box[k.v] = v; else box.push(v);
      p = Math.max(e, k.e);
    }
  }
  if (t.t === "num") {             // 可能是「12 0 R」
    const a = pdfTok(s, t.e);
    if (a && a.t === "num") { const b = pdfTok(s, a.e); if (b && b.t === "w" && b.v === "R") return [{ r: t.v }, b.e]; }
    return [t.v, t.e];
  }
  if (t.t === "n") return [{ n: t.v }, t.e];
  if (t.t === "s") return [{ s: t.v }, t.e];
  if (t.t === "w") return [t.v === "true" ? true : t.v === "false" ? false : t.v === "null" ? null : { op: t.v }, t.e];
  return [null, t.e];
}

// ── 物件與資料流 ──
function pdfScan(s) {
  const objs = new Map(), re = /(\d{1,10})\s{1,64}(\d{1,5})\s{1,64}obj\b/g;      // 長度都有上限：一長串數字或空白不會讓比對變成平方時間
  let m;
  while ((m = re.exec(s))) {
    const [val, end] = pdfVal(s, re.lastIndex);
    const o = { v: val }, t = pdfTok(s, end);
    re.lastIndex = end;
    if (t && t.t === "w" && t.v === "stream" && pdfIsDict(val)) {
      let st = t.e;
      if (s[st] === "\r") st++;
      if (s[st] === "\n") st++;
      const len = typeof val.Length === "number" ? val.Length : -1;
      let en = len >= 0 && /^\s*endstream/.test(s.substr(st + len, 16)) ? st + len : s.indexOf("endstream", st);
      if (en < 0) en = s.length;
      o.st = st; o.en = en; re.lastIndex = en;
    }
    objs.set(+m[1], o);            // 同一個編號後面的蓋掉前面的（增量更新）
  }
  return objs;
}
const pdfD = (pdf, v) => { for (let k = 0; k < 30 && v && typeof v === "object" && v.r !== undefined; k++) v = pdf.objs.get(v.r)?.v; return v; };
async function pdfInflate(bytes, budget) {
  const rd = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate")).getReader();
  const parts = [];
  let n = 0;
  try {
    for (;;) {
      const { value, done } = await rd.read();
      if (done) break;
      n += value.length;
      if (n > PDF_LIMIT.stream || n > budget.left) { try { await rd.cancel(); } catch { /* 已經關了 */ } throw new RangeError("PDF 解開後太大"); }
      parts.push(value);
    }
  } catch (err) {
    if (err instanceof RangeError) throw err;
    if (!parts.length) return null;      // 壞掉的資料流：解得出多少算多少
  }
  budget.left -= n;
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}
async function pdfStream(pdf, o) {
  if (!o || o.st == null) return null;
  if (o.data !== undefined) return o.data;
  const f = pdfD(pdf, o.v.Filter), filters = (Array.isArray(f) ? f : f ? [f] : []).map(x => pdfD(pdf, x)?.n);
  const parms = pdfD(pdf, Array.isArray(pdfD(pdf, o.v.DecodeParms)) ? pdfD(pdf, o.v.DecodeParms)[0] : o.v.DecodeParms);
  let data = pdf.bytes.subarray(o.st, o.en);
  for (const name of filters) {
    if ((name === "FlateDecode" || name === "Fl") && !(pdfIsDict(parms) && parms.Predictor > 1)) data = await pdfInflate(data, pdf.budget);
    else data = null;
    if (!data) break;
  }
  return (o.data = data);
}
async function pdfExpand(pdf) {      // 把物件串流（ObjStm）裡的物件攤開
  for (const o of [...pdf.objs.values()]) {
    if (!pdfIsDict(o.v) || o.v.Type?.n !== "ObjStm") continue;
    const d = await pdfStream(pdf, o);
    if (!d) continue;
    const t = pdfBin(d), first = pdfD(pdf, o.v.First) | 0, count = pdfD(pdf, o.v.N) | 0;
    const head = t.slice(0, first).trim().split(/\s+/).map(Number);
    for (let k = 0; k < count; k++) {
      const num = head[2 * k], off = first + head[2 * k + 1];
      if (!Number.isFinite(off)) break;
      if (!pdf.objs.has(num)) pdf.objs.set(num, { v: pdfVal(t, off)[0] });
    }
    o.data = undefined;               // 攤開後不用留著
  }
}
function pdfPages(pdf) {
  const pages = [], seen = new Set();
  const walk = (ref, inherited, depth) => {
    const node = pdfD(pdf, ref);
    if (!pdfIsDict(node) || seen.has(node) || depth > 40 || pages.length >= PDF_LIMIT.pages) return;
    seen.add(node);
    const res = node.Resources !== undefined ? node.Resources : inherited, kids = pdfD(pdf, node.Kids);
    if (Array.isArray(kids)) for (const k of kids) walk(k, res, depth + 1);
    else if (node.Contents !== undefined || node.Type?.n === "Page") pages.push({ node, res });
  };
  for (const o of pdf.objs.values()) if (pdfIsDict(o.v) && o.v.Type?.n === "Catalog") walk(o.v.Pages, undefined, 0);
  if (!pages.length) {
    for (const [, o] of [...pdf.objs].sort((a, b) => a[0] - b[0])) if (pdfIsDict(o.v) && o.v.Type?.n === "Page" && pages.length < PDF_LIMIT.pages) pages.push({ node: o.v, res: o.v.Resources });
  }
  return pages;
}

// ── 字型：字碼 → Unicode ──
function* pdfSections(t, open, close) {      // 「beginX … endX」中間的內容。用 indexOf 找：只有開頭沒有結尾的壞檔不會變成平方時間
  for (let a = t.indexOf(open); a >= 0;) {
    const b = t.indexOf(close, a + open.length);
    if (b < 0) return;
    yield t.slice(a + open.length, b);
    a = t.indexOf(open, b + close.length);
  }
}
function pdfCMap(t, bytes) {
  const map = new Map();
  let left = PDF_LIMIT.codes;                // 最多寫這麼多筆；同一段範圍重複很多次也算在內
  t = t.slice(0, PDF_LIMIT.cmap);
  const cs = /begincodespacerange\s*<([0-9A-Fa-f]+)>/.exec(t);
  if (cs) bytes = Math.max(1, Math.min(4, cs[1].length >> 1));
  const u16 = hex => {
    hex = hex.replace(/\s/g, "");
    if (hex.length % 4) hex = "0".repeat(4 - hex.length % 4) + hex;
    let out = "";
    for (let i = 0; i < hex.length; i += 4) out += String.fromCharCode(parseInt(hex.substr(i, 4), 16));
    return out;
  };
  for (const sec of pdfSections(t, "beginbfchar", "endbfchar")) {
    for (const m of sec.matchAll(/<([0-9A-Fa-f\s]{1,64})>\s*<([0-9A-Fa-f\s]{0,2048})>/g)) {
      if (left-- <= 0) return { map, bytes };
      map.set(parseInt(m[1].replace(/\s/g, ""), 16), u16(m[2]));
    }
  }
  for (const sec of pdfSections(t, "beginbfrange", "endbfrange")) {
    for (const m of sec.matchAll(/<([0-9A-Fa-f]{1,16})>\s*<([0-9A-Fa-f]{1,16})>\s*(\[[^\]]{0,4096}\]|<[0-9A-Fa-f\s]{0,2048}>)/g)) {
      const lo = parseInt(m[1], 16), hi = Math.min(parseInt(m[2], 16), lo + 65535);
      if (m[3][0] === "[") {
        const arr = [...m[3].matchAll(/<([0-9A-Fa-f\s]*)>/g)].map(x => u16(x[1]));
        for (let c = lo; c <= hi && c - lo < arr.length && left-- > 0; c++) map.set(c, arr[c - lo]);
      } else {
        const base = u16(m[3].slice(1, -1));
        if (!base) continue;
        const head = base.slice(0, -1), last = base.charCodeAt(base.length - 1);
        for (let c = lo; c <= hi && left-- > 0; c++) map.set(c, head + String.fromCharCode((last + c - lo) & 0xffff));
      }
      if (left <= 0) return { map, bytes };
    }
  }
  return { map, bytes };
}
async function pdfFonts(pdf, res) {
  const out = {}, fd = pdfD(pdf, pdfD(pdf, res)?.Font);
  if (!pdfIsDict(fd)) return out;
  for (const [name, ref] of Object.entries(fd)) {
    const key = ref && ref.r !== undefined ? ref.r : null;
    if (key !== null && pdf.fonts.has(key)) { out[name] = pdf.fonts.get(key); continue; }
    const f = pdfD(pdf, ref);
    if (!pdfIsDict(f)) continue;
    let info = { map: null, bytes: f.Subtype?.n === "Type0" ? 2 : 1 };
    const tu = f.ToUnicode && f.ToUnicode.r !== undefined ? pdf.objs.get(f.ToUnicode.r) : null;
    const d = tu ? await pdfStream(pdf, tu) : null;
    if (d) info = pdfCMap(pdfBin(d), info.bytes);
    pdfWidths(pdf, f, info);
    if (key !== null) pdf.fonts.set(key, info);
    out[name] = info;
  }
  return out;
}
function pdfWidths(pdf, f, info) {    // 字寬（千分之一字高），用來判斷兩段字中間是不是真的有空格
  const wm = new Map(), num = v => (typeof v === "number" ? v : 0);
  let left = PDF_LIMIT.codes;                // 寫入筆數上限，重複的範圍也算
  if (f.Subtype?.n === "Type0") {
    const df = pdfD(pdf, (pdfD(pdf, f.DescendantFonts) || [])[0]) || {}, W = pdfD(pdf, df.W);
    info.dw = typeof pdfD(pdf, df.DW) === "number" ? pdfD(pdf, df.DW) : 1000;
    for (let k = 0; Array.isArray(W) && k + 1 < W.length && left > 0;) {
      const c = num(W[k]), nx = pdfD(pdf, W[k + 1]);
      if (Array.isArray(nx)) { for (let j = 0; j < nx.length && left-- > 0; j++) wm.set(c + j, num(nx[j])); k += 2; }
      else { for (let q = c; q <= num(nx) && left-- > 0; q++) wm.set(q, num(W[k + 2])); k += 3; }
    }
  } else {
    const ws = pdfD(pdf, f.Widths), first = num(pdfD(pdf, f.FirstChar));
    info.dw = 500;
    for (let j = 0; Array.isArray(ws) && j < ws.length && left-- > 0; j++) wm.set(first + j, num(pdfD(pdf, ws[j])));
  }
  info.wm = wm;
}
// 回傳 [文字, 寬度（千分之一字高）]
function pdfDecode(str, font, st) {
  const nb = font ? font.bytes : 1, wm = font && font.wm, dw = font ? font.dw : 500;
  const mapped = !!(font && font.map && font.map.size);
  let out = "", w = 0;
  for (let i = 0; i + nb <= str.length; i += nb) {
    let code = 0;
    for (let k = 0; k < nb; k++) code = code * 256 + str.charCodeAt(i + k);
    w += wm && wm.has(code) ? wm.get(code) : dw;
    if (!mapped) continue;
    const u = font.map.get(code);
    if (u !== undefined) out += u; else if (nb === 1 && code >= 32) out += String.fromCharCode(code); else st.lost++;
  }
  if (mapped) return [out, w];
  if (nb === 2) { st.lost += str.length >> 1; return ["", w]; }   // 沒有對照表的雙位元組字型，還原不了
  pdfLatin = pdfLatin || new TextDecoder("windows-1252");
  return [pdfLatin.decode(Uint8Array.from(str, c => c.charCodeAt(0))), w];
}

// ── 讀內容串流裡的文字運算子 ──
async function pdfContent(pdf, s, res, st, ctm, depth) {
  const fonts = await pdfFonts(pdf, res), stack = [], saved = [];
  let font = null, size = 12, lead = 0, tlm = PDF_IDENT, moved = true, tx = 0, i = 0;
  const show = str => {
    const [text, w] = pdfDecode(str, font, st), m = pdfMul(tlm, ctm);
    if (moved && text) {             // 換了位置：跟上一段字的結尾比，決定要換行、空格還是直接接上
      const em = size * Math.hypot(m[0], m[1]) || size, x = m[4], y = m[5];
      if (st.y !== null) {
        const gap = x - st.x;
        if (Math.abs(y - st.y) > Math.max(2, 0.45 * em)) st.out.push("\n");
        else if (gap > 2 * em) st.out.push("\t");                         // 同一行隔很遠：多半是表格的下一格
        else if (gap > 0.18 * em || gap < -0.5 * em) st.out.push(" ");
      }
      st.y = y; moved = false;
    }
    tx += w / 1000 * size;
    if (!moved) st.x = pdfMul([1, 0, 0, 1, tx, 0], m)[4];
    if (text) { st.out.push(text); st.len += text.length; }
  };
  const move = (x, y) => { tlm = pdfMul([1, 0, 0, 1, x, y], tlm); moved = true; tx = 0; };
  while (i < s.length && st.len < st.max) {
    if (++st.ops % 4096 === 0 && performance.now() > st.deadline) throw new RangeError("這個 PDF 太複雜，讀不完");   // 例如表單互相呼叫的壞檔
    const t = pdfTok(s, i);
    if (!t) break;
    if (t.t === "[" || t.t === "<<") { const [v, e] = pdfVal(s, i); stack.push(v); i = e; continue; }
    i = t.e;
    if (t.t === "num") stack.push(t.v);
    else if (t.t === "s") stack.push({ s: t.v });
    else if (t.t === "n") stack.push({ n: t.v });
    if (t.t !== "w") continue;
    const a = stack, n = a.length, num = k => (typeof a[n - k] === "number" ? a[n - k] : 0);
    switch (t.v) {
      case "q": saved.push(ctm); break;
      case "Q": if (saved.length) ctm = saved.pop(); break;
      case "cm": if (n >= 6) ctm = pdfMul(a.slice(n - 6).map(Number), ctm); break;
      case "BT": tlm = PDF_IDENT; moved = true; tx = 0; break;
      case "Tf": font = fonts[a[n - 2]?.n] || null; size = Math.abs(num(1)) || 12; break;
      case "TL": lead = num(1); break;
      case "Td": move(num(2), num(1)); break;
      case "TD": lead = -num(1); move(num(2), num(1)); break;
      case "Tm": if (n >= 6) { tlm = a.slice(n - 6).map(Number); moved = true; tx = 0; } break;
      case "T*": move(0, -lead); break;
      case "'": case "\"": move(0, -lead); if (a[n - 1]?.s !== undefined) show(a[n - 1].s); break;
      case "Tj": if (a[n - 1]?.s !== undefined) show(a[n - 1].s); break;
      case "TJ":
        for (const x of Array.isArray(a[n - 1]) ? a[n - 1] : []) {
          if (x && x.s !== undefined) show(x.s);
          else if (typeof x === "number") {
            tx -= x / 1000 * size;
            if (x < -180 && !moved && !/\s$/.test(st.out[st.out.length - 1] || "")) st.out.push(" ");
          }
        }
        break;
      case "BI": {                     // 行內圖片：跳到 EI
        let e = i;
        do { e = s.indexOf("EI", e + 2); } while (e >= 0 && !(pdfIsWs(s.charCodeAt(e - 1)) && (e + 2 >= s.length || pdfIsWs(s.charCodeAt(e + 2)))));
        i = e < 0 ? s.length : e + 2;
        break;
      }
      case "Do": {
        const xo = pdfD(pdf, pdfD(pdf, res)?.XObject), ref = pdfIsDict(xo) ? xo[a[n - 1]?.n] : null;
        const o = ref && ref.r !== undefined ? pdf.objs.get(ref.r) : null;
        if (o && pdfIsDict(o.v) && o.v.Subtype?.n === "Form" && depth < 4 && ++st.forms <= PDF_LIMIT.forms) {   // 表單展開有總次數上限：互相呼叫的壞檔不會展開成天文數字
          const d = await pdfStream(pdf, o), mx = pdfD(pdf, o.v.Matrix);
          if (d) await pdfContent(pdf, pdfBin(d), o.v.Resources !== undefined ? o.v.Resources : res, st, Array.isArray(mx) && mx.length === 6 ? pdfMul(mx.map(Number), ctm) : ctm, depth + 1);
          moved = true;
        }
        break;
      }
    }
    stack.length = 0;
  }
}

// 入口：ArrayBuffer → { text, pages, read, truncated, lost }
async function pdfText(buf, maxChars = 120000) {
  const bytes = new Uint8Array(buf), s = pdfBin(bytes);
  if (s.indexOf("%PDF-") < 0 || s.indexOf("%PDF-") > 1024) throw new Error("這不是 PDF 檔");
  if (/\/Encrypt\s*(\d+\s+\d+\s+R|<<)/.test(s)) throw new Error("加密的 PDF 讀不了，先解除密碼再附");
  const pdf = { bytes, objs: pdfScan(s), budget: { left: PDF_LIMIT.total }, fonts: new Map() };
  await pdfExpand(pdf);
  const pages = pdfPages(pdf), st = { out: [], len: 0, max: maxChars, x: 0, y: null, lost: 0, ops: 0, forms: 0, deadline: performance.now() + PDF_LIMIT.ms };
  let read = 0;
  for (const pg of pages) {
    if (st.len >= st.max) break;
    if (performance.now() > st.deadline) throw new RangeError("這個 PDF 太複雜，讀不完");
    read++;
    if (pages.length > 1) st.out.push(`${read > 1 ? "\n\n" : ""}【第 ${read} 頁】\n`);
    st.y = null;
    const c = pg.node.Contents, direct = c && c.r !== undefined && pdf.objs.get(c.r)?.st != null;
    const list = direct ? [c] : Array.isArray(pdfD(pdf, c)) ? pdfD(pdf, c) : [];
    let content = "";
    for (const ref of list) {
      const d = await pdfStream(pdf, ref && ref.r !== undefined ? pdf.objs.get(ref.r) : null);
      if (d) content += pdfBin(d) + "\n";
    }
    await pdfContent(pdf, content, pg.res, st, PDF_IDENT, 0);
    if (read % 6 === 0) await new Promise(r => setTimeout(r));      // 讓畫面有機會更新
  }
  const cjk = "\\u2e80-\\u9fff\\uf900-\\ufaff\\uff00-\\uffef\\u3000-\\u303f";
  let text = st.out.join("").replace(/\u0000/g, "").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n")
    .replace(/[⺀-⿟豈-﫿]/g, c => c.normalize("NFKC"))     // Chrome 會把「用」對到康熙部首那一區，換回一般的字
    .replace(new RegExp(`([${cjk}]) (?=[${cjk}])`, "g"), "$1").trim();
  const truncated = text.length > maxChars || read < pages.length;
  if (text.length > maxChars) text = text.slice(0, maxChars);
  return { text, pages: pages.length, read, truncated, lost: st.lost };
}
