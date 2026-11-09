"use strict";
// 彈窗的鍵盤操作與無障礙標記：打開時把焦點移進去並鎖在裡面（Tab 不會跑到後面的頁面），Esc 關閉，
// 關掉後焦點回到原本按的那顆按鈕。靠 hidden 屬性的變化運作，各個彈窗原本的開關程式不用改。最後載入。

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';
const MODAL = { stack: [] };          // 開著的彈窗（最後一個在最上面）：{ el, back }
const modalItems = el => $$(FOCUSABLE, el).filter(x => !x.closest("[hidden]") && x.getClientRects().length);
const modalCard = el => el.querySelector(".modal-card") || el;

function modalOpened(el) {
  if (MODAL.stack.some(m => m.el === el)) return;
  MODAL.stack.push({ el, back: document.activeElement });
  const items = modalItems(el);
  // 先落在第一個欄位或動作上；只有關閉鈕的話就落在卡片本身，免得一按 Enter 就關掉
  const first = items.find(x => !x.matches(".side-head .icon-btn")) || modalCard(el);
  later(() => { if (!el.hidden) first.focus({ preventScroll: true }); });
}
function modalClosed(el) {
  const i = MODAL.stack.findIndex(m => m.el === el);
  if (i < 0) return;
  const [m] = MODAL.stack.splice(i, 1);
  if (m.back && m.back.isConnected && !el.contains(m.back) && m.back !== document.body) m.back.focus({ preventScroll: true });
}
for (const el of $$(".modal")) {
  el.setAttribute("role", "dialog"); el.setAttribute("aria-modal", "true");
  modalCard(el).setAttribute("tabindex", "-1");
  const head = el.querySelector(".side-head b");
  if (head) { head.id = head.id || el.id + "-title"; el.setAttribute("aria-labelledby", head.id); }
  new MutationObserver(() => (el.hidden ? modalClosed(el) : modalOpened(el))).observe(el, { attributes: true, attributeFilter: ["hidden"] });
  el.addEventListener("keydown", e => {
    if (e.key === "Escape") { e.stopPropagation(); el.hidden = true; return; }
    if (e.key !== "Tab") return;
    const items = modalItems(el), at = document.activeElement;
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && (at === first || at === modalCard(el))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && at === last) { e.preventDefault(); first.focus(); }
  });
}
// 焦點被別的東西帶到彈窗外面（例如點了後面的頁面）就拉回來
document.addEventListener("focusin", e => {
  const top = MODAL.stack[MODAL.stack.length - 1];
  if (top && !top.el.hidden && !top.el.contains(e.target)) (modalItems(top.el)[0] || modalCard(top.el)).focus({ preventScroll: true });
});

// 選單類的浮層：標成 menu，方向鍵可以上下移動
for (const sel of ["#ctx", "#settings-pop"]) {
  const p = $(sel);
  p.setAttribute("role", "menu");
  $$("button", p).forEach(b => b.setAttribute("role", "menuitem"));
  p.addEventListener("keydown", e => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const items = $$("button", p).filter(b => !b.hidden);
    const i = items.indexOf(document.activeElement);
    e.preventDefault();
    items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1) + items.length) % items.length]?.focus();
  });
}
