"use strict";
// 七天 token 熱力圖（新對話畫面）：格子只上色、不寫數字，滑鼠移上去才顯示。接在 myai.js 後面載入。

const fmtTok = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "k" : String(n);

async function loadHeat() {
  const box = $("#heat");
  if (!box) return;
  let days;
  try { days = (await api("/api/usage/me?days=7")).days; } catch { box.hidden = true; return; }
  const max = Math.max(1, ...days.flatMap(d => d.cells));
  const lvl = v => v <= 0 ? 0 : Math.min(5, 1 + Math.floor(v / max * 4.999));
  box.hidden = false;
  box.innerHTML = `<div class="heat-head"><b>時段記錄</b><span class="legend">少 ${[1, 2, 3, 4, 5].map(i => `<i class="c${i}"></i>`).join("")} 多</span></div>` +
    days.map(d => `<div class="heat-row"><div class="heat-lab"><b>${esc(d.label)}</b><span>${d.calls} 次 · ${fmtTok(d.total)}</span></div>
      <div class="heat-cells">${d.cells.map((v, h) => `<i class="c${lvl(v)}" data-tip="${esc(d.label)} ${String(h).padStart(2, "0")}:00–${String(h + 1).padStart(2, "0")}:00 · ${v.toLocaleString()} tokens"></i>`).join("")}</div></div>`).join("") +
    `<div class="heat-row"><div class="heat-lab"></div><div class="heat-axis">${[0, 3, 6, 9, 12, 15, 18, 21].map(h => `<span>${h}:00</span>`).join("")}</div></div>`;
}
const tip = document.createElement("div");
tip.className = "heat-tip"; tip.hidden = true; document.body.appendChild(tip);
document.addEventListener("mouseover", e => {
  const c = e.target.closest("[data-tip]");
  if (!c) { tip.hidden = true; return; }
  tip.textContent = c.dataset.tip; tip.hidden = false;
  const r = c.getBoundingClientRect();
  tip.style.left = Math.min(r.left + r.width / 2, innerWidth - 150) + "px"; tip.style.top = r.top - 8 + "px";
});
document.addEventListener("mouseout", e => { if (e.target.closest && e.target.closest("[data-tip]")) tip.hidden = true; });
document.addEventListener("click", e => { const c = e.target.closest("[data-tip]"); if (c) { tip.textContent = c.dataset.tip; tip.hidden = false; const r = c.getBoundingClientRect(); tip.style.left = Math.min(r.left + r.width / 2, innerWidth - 150) + "px"; tip.style.top = r.top - 8 + "px"; } });
