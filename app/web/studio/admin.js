"use strict";
// 管理員儀表板：全部使用者的用量圖（日／週／月／年）、每個人的專案與對話。接在 ui2.js 後面載入。

const DASH = { period: ls.get("dash.period", "day"), table: false, data: null, user: null };
// 系列顏色：固定順序、照帳號名字排，深淺各自一組（dataviz 驗證過的六色）
const SERIES = { light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"], dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"] };
const seriesColor = i => SERIES[document.documentElement.dataset.theme === "light" ? "light" : "dark"][i % 6];

function openDash() {
  if (S.me?.role !== "admin") return;
  if (typeof leaveRoomView === "function") leaveRoomView();
  document.body.classList.add("in-dash");
  $("#dash-btn").classList.add("on");
  $("#title").value = "儀表板"; $("#title").readOnly = true;
  loadDash();
  if (!wide()) openSide(false);
}
function closeDash() {
  document.body.classList.remove("in-dash");
  $("#dash-btn").classList.remove("on");
  $("#title").readOnly = false;
}
$("#dash-btn").addEventListener("click", () => (document.body.classList.contains("in-dash") ? closeDash() : openDash()));

async function loadDash() {
  const box = $("#dash");
  box.innerHTML = `<div class="muted small">載入中…</div>`;
  try {
    const [ov, us] = await Promise.all([api("/api/admin/overview"), api(`/api/admin/usage?period=${DASH.period}&limit=${DASH.period === "day" ? 30 : DASH.period === "week" ? 26 : DASH.period === "month" ? 24 : 10}`)]);
    DASH.data = { ov, us };
    renderDash();
  } catch (err) { box.innerHTML = `<div class="err">${esc(err.message)}</div>`; }
}
const zhP = { day: "日", week: "週", month: "月", year: "年" };
function renderDash() {
  const { ov, us } = DASH.data;
  const names = Object.keys(us.users).sort();
  const totTok = Object.values(us.total).reduce((a, b) => a + b.prompt + b.completion, 0);
  const totCalls = Object.values(us.total).reduce((a, b) => a + b.calls, 0);
  const pending = ov.users.filter(u => u.status === "pending").length;
  $("#dash").innerHTML = `
    <div class="tiles">
      <div class="tile"><span>這 ${us.buckets.length} ${zhP[DASH.period]}的 token</span><b>${fmtTok(totTok)}</b></div>
      <div class="tile"><span>對話次數</span><b>${totCalls.toLocaleString()}</b></div>
      <div class="tile"><span>使用者</span><b>${ov.users.filter(u => u.status === "active").length}</b>${pending ? `<i>${pending} 個待核准</i>` : ""}</div>
      <div class="tile"><span>專案／群組</span><b>${ov.users.reduce((a, u) => a + u.projects, 0)} / ${ov.rooms}</b></div>
    </div>
    <div class="stat chart-card">
      <div class="chart-head"><h4>token 用量（每人堆疊）</h4>
        <div class="seg">${Object.keys(zhP).map(p => `<button data-period="${p}" class="${DASH.period === p ? "on" : ""}">${zhP[p]}</button>`).join("")}</div>
        <button class="btn sm" id="dash-table">${DASH.table ? "看圖" : "看表格"}</button></div>
      ${DASH.table ? usageTable(us, names) : usageChart(us, names)}
      ${names.length > 1 ? `<div class="legend-row">${names.map((n, i) => `<span><i style="background:${seriesColor(i)}"></i>${esc(n)}</span>`).join("")}</div>` : ""}
    </div>
    <div class="stat"><h4>使用者</h4>
      <table class="dtable"><tr><th>帳號</th><th>狀態</th><th>專案</th><th>訊息</th><th>token</th><th>最後登入</th></tr>
      ${ov.users.map(u => `<tr data-user="${esc(u.name)}" class="row-link"><td>${esc(u.name)}${u.role === "admin" ? " <small>管理員</small>" : ""}</td><td>${esc({ active: "可用", pending: "待核准", disabled: "停用", master: "主登入碼", deleted: "已刪" }[u.status] || u.status)}</td><td>${u.projects}</td><td>${u.messages}</td><td>${fmtTok(u.tokens)}</td><td>${u.last_login ? new Date(u.last_login * 1000).toLocaleDateString("zh-Hant") : "—"}</td></tr>`).join("")}
      </table><p class="muted small" style="margin:8px 0 0">點帳號看他的專案和對話內容。使用者在協議裡已被告知管理員看得到。</p></div>
    <div class="stat" id="dash-user" hidden></div>`;
}
function usageChart(us, names) {
  const W = Math.max(320, us.buckets.length * 28 + 50), H = 220, padL = 44, padB = 26, top = 10;
  const max = Math.max(1, ...us.buckets.map(b => us.total[b].prompt + us.total[b].completion));
  const scale = v => (H - padB - top) * v / max;
  const bw = Math.min(22, (W - padL - 10) / us.buckets.length - 4);
  let bars = "";
  us.buckets.forEach((b, i) => {
    const x = padL + i * ((W - padL - 10) / us.buckets.length) + 2;
    let y = H - padB;
    names.forEach((n, k) => {
      const c = us.users[n][b]; if (!c) return;
      const v = c.prompt + c.completion, hgt = Math.max(0, scale(v) - 2);
      y -= hgt;
      bars += `<rect x="${x}" y="${y}" width="${bw}" height="${hgt}" rx="3" fill="${seriesColor(k)}" data-tip="${esc(n)} · ${esc(b)} · ${v.toLocaleString()} tokens（${c.calls} 次）"/>`;
      y -= 2;
    });
    const lab = DASH.period === "day" ? b.slice(5) : DASH.period === "week" ? b.slice(5) : DASH.period === "month" ? b.slice(2) : b;
    if (i % Math.ceil(us.buckets.length / 8) === 0) bars += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle" class="ax">${esc(lab)}</text>`;
  });
  const ticks = [0, 0.5, 1].map(t => `<line x1="${padL - 4}" x2="${W}" y1="${H - padB - scale(max * t)}" y2="${H - padB - scale(max * t)}" class="grid"/><text x="${padL - 8}" y="${H - padB - scale(max * t) + 4}" text-anchor="end" class="ax">${fmtTok(Math.round(max * t))}</text>`).join("");
  return `<div class="chart-scroll"><svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" class="chart">${ticks}${bars}</svg></div>`;
}
function usageTable(us, names) {
  return `<div class="chart-scroll"><table class="dtable"><tr><th>${zhP[DASH.period]}</th>${names.map(n => `<th>${esc(n)}</th>`).join("")}<th>合計</th></tr>
    ${us.buckets.map(b => `<tr><td>${esc(b)}</td>${names.map(n => `<td>${us.users[n][b] ? (us.users[n][b].prompt + us.users[n][b].completion).toLocaleString() : "—"}</td>`).join("")}<td><b>${(us.total[b].prompt + us.total[b].completion).toLocaleString()}</b></td></tr>`).join("")}</table></div>`;
}
$("#dash").addEventListener("click", async e => {
  const p = e.target.closest("[data-period]");
  if (p) { DASH.period = p.dataset.period; ls.set("dash.period", DASH.period); loadDash(); return; }
  if (e.target.closest("#dash-table")) { DASH.table = !DASH.table; renderDash(); return; }
  const s = e.target.closest("[data-sess]");
  if (s) { await showTranscript(s.dataset.user, s.dataset.sess); return; }
  const u = e.target.closest("[data-user]");
  if (u) await showUserProjects(u.dataset.user);
});
async function showUserProjects(name) {
  const box = $("#dash-user"); box.hidden = false;
  try {
    const r = await api("/api/admin/sessions?user=" + encodeURIComponent(name));
    box.innerHTML = `<h4>${esc(name)} 的專案（${r.sessions.length}）</h4>` + (r.sessions.length
      ? r.sessions.map(s => `<button class="rcard" data-sess="${s.id}" data-user="${esc(name)}"><b>${esc(s.title || "（沒有標題）")}</b><span>${s.count} 則 · ${esc(s.model)}${s.group ? " · " + esc(s.group) : ""}</span><i>${new Date(s.updated * 1000).toLocaleString("zh-Hant", { hour12: false })}</i></button>`).join("")
      : `<div class="muted small">沒有專案</div>`) + `<div id="dash-transcript"></div>`;
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) { toast(err.message, true); }
}
async function showTranscript(user, id) {
  const box = $("#dash-transcript");
  try {
    const s = await api(`/api/admin/session?user=${encodeURIComponent(user)}&id=${id}`);
    const who = m => m.by || (m.role === "assistant" ? "kiki" : "user");
    box.innerHTML = `<div class="transcript"><h4>${esc(s.title)} <span class="muted small">${esc(user)} · ${esc(s.model)}</span></h4>` +
      s.messages.map(m => `<div class="tline ${who(m)}"><b>${esc(who(m) === "user" ? user : who(m) === "kiki" ? "Kiki" : (s.partner?.name || who(m)))}</b>${esc((m.content || "").replace(/^<think>[\s\S]*?<\/think>\s*/, ""))}</div>`).join("") + `</div>`;
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) { toast(err.message, true); }
}
// 離開儀表板：開任何對話或新對話時
const _ns = newSession; newSession = function (extra) { closeDash(); return _ns(extra); };
const _os = openSession; openSession = async function (id, track) { closeDash(); return _os(id, track); };
