"use strict";
// 群組：好幾個帳號一起聊，Kiki 默默觀察、被 @ 才回。接在 studio.js 後面載入，共用 $、api、S、toast 等。

const RM = { list: [], cur: null, seq: 0, timer: 0, members: [] };

function renderRooms() {
  const rows = RM.list.map(r => `<button class="sess room ${RM.cur?.id === r.id ? "on" : ""}" data-room="${r.id}">
    <svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M3 20a6 6 0 0 1 12 0M16 4a3.5 3.5 0 0 1 0 7M21 20a6 6 0 0 0-5-5.9"/></svg>
    <span class="t">${esc(r.title)}</span><span class="grow"></span><span class="m">${r.members.length} 人</span></button>`).join("");
  return `<div class="grp-row"><span class="grp open"><span>我的群組</span></span>
    <button class="tool" id="room-new" title="建立群組" aria-label="建立群組"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button></div>
    ${rows || `<div class="muted small" style="padding:6px 12px">還沒有群組，按 ＋ 建一個；群組和你的專案是分開的</div>`}`;
}
async function loadRooms() {
  try { RM.list = (await api("/api/rooms")).rooms; } catch (err) { toast(err.message, true); }
  renderList();
}
$("#sess-list").addEventListener("click", async e => {
  if (e.target.closest("#room-new")) {
    const t = prompt("群組名稱");
    if (!t) return;
    try { const r = await api("/api/rooms/create", { body: { title: t.trim(), model: S.cur?.model || "" } }); await loadRooms(); openRoom(r.id); }
    catch (err) { toast(err.message, true); }
    return;
  }
  const b = e.target.closest("[data-room]");
  if (b && !S.busy) { openRoom(b.dataset.room); if (!wide()) openSide(false); }
});

// ── 進出群組 ──
function leaveRoomView() {
  if (!RM.cur) return;
  clearTimeout(RM.timer); RM.cur = null; RM.seq = 0;
  document.body.classList.remove("in-room");
  $("#title").readOnly = false;
}
async function openRoom(id) {
  leaveRoomView();
  let r;
  try { r = await api("/api/rooms/get?id=" + id + "&since=0"); } catch (err) { toast(err.message, true); return; }
  RM.cur = r; RM.seq = 0; S.cur = null;
  setListMode("rooms");
  document.body.classList.add("in-room");
  $("#title").value = r.title; $("#title").readOnly = true;
  $("#model-name").textContent = r.model || "（沒有模型）";
  $("#pin-btn").classList.remove("on");
  const box = $("#msgs");
  $$(".msg, .sys", box).forEach(x => x.remove());
  $("#empty").hidden = true;
  if (!r.messages.length) box.insertAdjacentHTML("beforeend", `<div class="sys">邀請成員進來聊；打 @Kiki 她才會回話</div>`);
  appendRoomMsgs(r.messages);
  renderList();
  pollRoom();
  $("#input").focus();
}
function roomMsgEl(m) {
  if (m.role === "system") { const d = document.createElement("div"); d.className = "sys"; d.textContent = m.content; return d; }
  const mine = m.role === "user" && m.user === S.me?.name;
  const d = document.createElement("div");
  d.className = "msg " + (m.role === "assistant" ? "ai" : mine ? "user" : "other");
  d.dataset.seq = m.seq;
  fillRoomMsg(d, m);
  return d;
}
function fillRoomMsg(d, m) {
  const t = new Date(m.ts * 1000).toLocaleTimeString("zh-Hant", { hour: "2-digit", minute: "2-digit" });
  if (m.role === "assistant") {
    d.innerHTML = `<div class="av">K</div><div class="body"><div class="name">Kiki <span class="time">${t}</span></div><div class="${m.done ? "" : "cursor"}">${md(m.content || "")}</div>${m.error ? `<div class="err">${esc(m.error)}</div>` : ""}</div>`;
    return;
  }
  const mine = m.user === S.me?.name;
  d.innerHTML = `<div class="bubble">${mine ? "" : `<div class="name">${esc(m.user)}</div>`}${esc(m.content)}<div class="time">${t}</div></div>`;
}
function appendRoomMsgs(msgs) {
  const box = $("#msgs");
  for (const m of msgs) {
    const old = m.seq ? $(`[data-seq="${m.seq}"]`, box) : null;
    if (old) fillRoomMsg(old, m); else box.appendChild(roomMsgEl(m));
    RM.seq = Math.max(RM.seq, m.seq || 0);
  }
  if (msgs.length) scrollMsgs();
}
async function pollRoom() {
  if (!RM.cur) return;
  const id = RM.cur.id;
  try {
    const r = await api(`/api/rooms/get?id=${id}&since=${RM.seq}`);
    if (!RM.cur || RM.cur.id !== id) return;
    // 正在生成的 Kiki 回覆 seq 不變，用 since-1 再抓一次才看得到更新
    RM.cur.members = r.members; RM.cur.title = r.title;
    appendRoomMsgs(r.messages);
    const live = $("#msgs .msg.ai .cursor");
    if (live) {
      const seq = +live.closest(".msg").dataset.seq;
      const again = await api(`/api/rooms/get?id=${id}&since=${seq - 1}`);
      if (RM.cur && RM.cur.id === id) appendRoomMsgs(again.messages.filter(m => m.seq === seq));
    }
  } catch (err) {
    if (/404/.test(err.message) || /沒有這個群組/.test(err.message)) { toast("這個群組已經不存在"); leaveRoomView(); newSession(); loadRooms(); return; }
  }
  RM.timer = setTimeout(pollRoom, $("#msgs .msg.ai .cursor") ? 700 : 2500);
}
async function roomSend(text) {
  try {
    const m = await api("/api/rooms/post", { body: { id: RM.cur.id, content: text } });
    $("#msgs .sys")?.remove();
    appendRoomMsgs([m]);
    clearTimeout(RM.timer); RM.timer = setTimeout(pollRoom, 300);
  } catch (err) { toast(err.message, true); }
}

// ── 成員 ──
$("#members-btn").addEventListener("click", async () => {
  if (!RM.cur) return;
  $("#members-modal").hidden = false;
  await renderMembers();
});
$("#members-close").addEventListener("click", () => ($("#members-modal").hidden = true));
async function renderMembers() {
  const r = RM.cur, me = S.me?.name, mine = r.owner === me;
  let people = [];
  if (mine) { try { people = (await api("/api/rooms/people")).people.filter(p => !r.members.includes(p)); } catch { /* 沒有也沒關係 */ } }
  $("#members-list").innerHTML = r.members.map(n => `<div class="user"><span class="n">${esc(n)}</span><span class="s">${n === r.owner ? "建立者" : ""}</span>
    <span class="acts">${mine && n !== me ? `<button class="btn sm" data-kick="${esc(n)}">移出</button>` : ""}</span></div>`).join("")
    + `<div class="user"><span class="n">Kiki</span><span class="s">AI 成員 · 被 @ 才回</span></div>`;
  $("#invite-row").hidden = !mine;
  $("#invite-sel").innerHTML = people.length ? people.map(p => `<option>${esc(p)}</option>`).join("") : `<option value="">（沒有可以邀的人）</option>`;
  $("#room-leave").textContent = mine ? "刪除群組" : "離開群組";
  $("#room-rename").hidden = !mine;
  if (typeof renderRoomBots === "function") await renderRoomBots();
}
$("#members-modal").addEventListener("click", async e => {
  if (e.target === e.currentTarget) { $("#members-modal").hidden = true; return; }
  const k = e.target.closest("[data-kick]");
  if (k) { try { RM.cur = Object.assign(RM.cur, await api("/api/rooms/remove", { body: { id: RM.cur.id, user: k.dataset.kick } })); await renderMembers(); loadRooms(); } catch (err) { toast(err.message, true); } }
});
$("#invite-btn").addEventListener("click", async () => {
  const u = $("#invite-sel").value;
  if (!u) return;
  try { RM.cur = Object.assign(RM.cur, await api("/api/rooms/invite", { body: { id: RM.cur.id, user: u } })); toast(`已把 ${u} 加進來`); await renderMembers(); loadRooms(); }
  catch (err) { toast(err.message, true); }
});
$("#room-rename").addEventListener("click", async () => {
  const t = prompt("群組名稱", RM.cur.title);
  if (!t) return;
  try { RM.cur = Object.assign(RM.cur, await api("/api/rooms/rename", { body: { id: RM.cur.id, title: t.trim() } })); $("#title").value = RM.cur.title; loadRooms(); } catch (err) { toast(err.message, true); }
});
$("#room-leave").addEventListener("click", async () => {
  const mine = RM.cur.owner === S.me?.name;
  if (!confirm(mine ? "刪除整個群組？所有人都會看不到" : "離開這個群組？")) return;
  try { await api("/api/rooms/remove", { body: { id: RM.cur.id } }); } catch (err) { toast(err.message, true); return; }
  $("#members-modal").hidden = true; leaveRoomView(); newSession(); loadRooms();
});

// ── @ 提示 ──
$("#input").addEventListener("input", e => {
  if (!RM.cur) return;
  const v = e.target.value, m = v.match(/(?:^|\s)@([\w.-]*)$/);
  const menu = $("#slash");
  if (!m) { if (menu.dataset.at) { menu.hidden = true; delete menu.dataset.at; } return; }
  const names = ["Kiki", ...Object.keys(RM.cur.bots || {}), ...RM.cur.members.filter(n => n !== S.me?.name)].filter(n => n.toLowerCase().startsWith(m[1].toLowerCase()));
  menu.dataset.at = "1"; menu.hidden = !names.length;
  menu.innerHTML = names.map((n, i) => `<button type="button" data-at="${esc(n)}" class="${i ? "" : "on"}"><b>@${esc(n)}</b><span>${n === "Kiki" ? "叫 AI 回話" : (RM.cur.bots || {})[n] ? "外部 AI" : "成員"}</span></button>`).join("");
});
$("#slash").addEventListener("click", e => {
  const b = e.target.closest("[data-at]");
  if (!b) return;
  const ta = $("#input");
  ta.value = ta.value.replace(/@[\w.-]*$/, "@" + b.dataset.at + " "); $("#slash").hidden = true; delete $("#slash").dataset.at; ta.focus();
});
