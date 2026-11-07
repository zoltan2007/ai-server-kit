"use strict";
// 我的 AI：把自己訂閱的 Claude／OpenAI／Gemini／Grok 接進來。金鑰只送到主機、不會再回傳。接在 rooms.js 後面載入。

const AI = { mine: [], providers: [] };

async function loadMyAI() {
  try { const r = await api("/api/ai"); AI.mine = r.ai; AI.providers = r.providers; }
  catch { AI.mine = []; }
  renderModelChip();
}
function myModels() {
  // 給模型選單用：ext:<provider>:<model>
  return AI.mine.filter(a => a.has_key).map(a => ({ value: `ext:${a.provider}:${a.model}`, label: `${a.name}（我的）`, sub: a.model }));
}
function renderMyAI() {
  const mine = Object.fromEntries(AI.mine.map(a => [a.provider, a]));
  $("#myai-list").innerHTML = AI.providers.map(p => {
    const m = mine[p.provider];
    return `<form class="myai" data-p="${p.provider}">
      <div class="myai-head"><b>${esc(p.name)}</b><span class="muted small">群組裡的代號 @${esc(p.handle)}</span>${m?.has_key ? '<span class="ok-tag">已接上</span>' : ""}</div>
      <input name="key" type="password" autocomplete="off" placeholder="${m?.has_key ? "金鑰已存在主機；要換再填" : "API 金鑰"}">
      <input name="model" placeholder="模型名稱，例如 ${esc(p.example)}" value="${esc(m?.model || "")}">
      <div class="foot-btns"><button class="btn sm primary" type="submit">儲存</button><button class="btn sm" type="button" data-test="${p.provider}" ${m?.has_key ? "" : "disabled"}>測試</button><button class="btn sm" type="button" data-del="${p.provider}" ${m?.has_key ? "" : "disabled"}>移除</button></div>
    </form>`;
  }).join("");
}
$("#myai-btn").addEventListener("click", () => { $("#settings-pop").hidden = true; $("#myai-modal").hidden = false; renderMyAI(); });
$("#myai-close").addEventListener("click", () => ($("#myai-modal").hidden = true));
$("#myai-modal").addEventListener("click", async e => {
  if (e.target === e.currentTarget) { $("#myai-modal").hidden = true; return; }
  const t = e.target.closest("[data-test]"), d = e.target.closest("[data-del]");
  if (t) { t.disabled = true; try { const r = await api("/api/ai/test", { body: { provider: t.dataset.test } }); toast("可以用，它回：" + r.reply); } catch (err) { toast(err.message, true); } t.disabled = false; }
  if (d && confirm("移除這家的金鑰？")) { try { AI.mine = (await api("/api/ai/remove", { body: { provider: d.dataset.del } })).ai; renderMyAI(); renderModelChip(); } catch (err) { toast(err.message, true); } }
});
$("#myai-modal").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target, body = { provider: f.dataset.p, key: f.key.value.trim(), model: f.model.value.trim() };
  try { AI.mine = (await api("/api/ai/set", { body })).ai; toast("已儲存，按「測試」確認能用"); renderMyAI(); renderModelChip(); }
  catch (err) { toast(err.message, true); }
});

// 群組：群主把自己的 AI 接進來
async function renderRoomBots() {
  const r = RM.cur, mine = r.owner === S.me?.name;
  const bots = Object.entries(r.bots || {});
  $("#bots-list").innerHTML = bots.map(([h, b]) => `<div class="user"><span class="n">@${esc(h)}</span><span class="s">${esc(b.name)} · ${esc(b.model)} · ${esc(b.owner)} 的</span>
    <span class="acts">${mine ? `<button class="btn sm" data-unbot="${esc(h)}">移除</button>` : ""}</span></div>`).join("");
  const can = mine ? AI.mine.filter(a => a.has_key && !(r.bots || {})[a.handle]) : [];
  $("#bot-row").hidden = !mine;
  $("#bot-sel").innerHTML = can.length ? can.map(a => `<option value="${a.provider}">${esc(a.name)}（${esc(a.model)}）</option>`).join("") : `<option value="">（先到自訂 → 我的 AI 設定金鑰）</option>`;
}
$("#bot-btn").addEventListener("click", async () => {
  const p = $("#bot-sel").value;
  if (!p) return;
  try { RM.cur = Object.assign(RM.cur, await api("/api/rooms/bot", { body: { id: RM.cur.id, provider: p } })); toast("接上了，群組裡 @ 它就會回"); renderRoomBots(); }
  catch (err) { toast(err.message, true); }
});
$("#members-modal").addEventListener("click", async e => {
  const b = e.target.closest("[data-unbot]");
  if (!b) return;
  try { RM.cur = Object.assign(RM.cur, await api("/api/rooms/bot_remove", { body: { id: RM.cur.id, handle: b.dataset.unbot } })); renderRoomBots(); }
  catch (err) { toast(err.message, true); }
});
