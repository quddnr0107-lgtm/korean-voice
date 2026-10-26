(() => {
  "use strict";
  const script = document.currentScript;
  const scope = script && script.dataset.insuranceSite;
  const banner = document.querySelector(".insurance-consultation");
  if (!scope || !banner || banner.dataset.popupInitialized === "true") return;
  if (/(?:^|\/)(?:admin|login|auth|checkout|payment|billing|privacy|policy|policies|terms|licenses)(?:\/|\.|$)/i.test(location.pathname)) return;
  if (scope === "korean-voice" && !/^\/app(?:\.html)?\/?$/.test(location.pathname)) return;
  banner.dataset.popupInitialized = "true";
  const prefix = "insurance-consultation:" + scope + ":";
  const dayKey = () => new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const read = (storage, key) => { try { return window[storage].getItem(key); } catch (_) { return null; } };
  const write = (storage, key, value) => { try { window[storage].setItem(key, value); } catch (_) {} };
  const suppressed = () => read("sessionStorage", prefix + "shown") === "1" || read("localStorage", prefix + "hidden-day") === dayKey();
  if (suppressed()) return;
  const source = banner.querySelector(".insurance-consultation__image-link");
  if (!source) return;
  let dialog = null;
  let previousFocus = null;
  let observer = null;
  let timer = null;
  const otherModal = () => Array.from(document.querySelectorAll('dialog[open], [aria-modal="true"], [role="dialog"], [role="alertdialog"]')).find(el => {
    if (el === dialog || el.getAttribute("aria-hidden") === "true" || !el.getClientRects().length) return false;
    const style = getComputedStyle(el);
    return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
  });
  const busy = () => document.hidden || otherModal() || (document.activeElement && document.activeElement.matches('input, textarea, select, [contenteditable="true"]'));
  const close = () => { if (dialog && dialog.open) dialog.close(); };
  const show = () => {
    if (suppressed()) return;
    dialog = document.createElement("dialog");
    if (typeof dialog.showModal !== "function") return;
    dialog.className = "insurance-consultation-dialog";
    dialog.setAttribute("aria-label", "보험 상담 광고");
    const link = source.cloneNode(true);
    const img = link.querySelector("img");
    img.loading = "eager";
    const controls = document.createElement("div");
    controls.className = "insurance-consultation-dialog__controls";
    const today = document.createElement("button");
    today.type = "button";
    today.textContent = "오늘 하루 보지 않기";
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.textContent = "닫기 ×";
    dismiss.setAttribute("aria-label", "보험 상담 광고 닫기");
    controls.append(today, dismiss);
    dialog.append(link, controls);
    today.addEventListener("click", () => { write("localStorage", prefix + "hidden-day", dayKey()); close(); });
    dismiss.addEventListener("click", close);
    dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
    dialog.addEventListener("click", event => { if (event.target === dialog) close(); });
    dialog.addEventListener("close", () => {
      if (observer) observer.disconnect();
      if (previousFocus && previousFocus.isConnected && !otherModal()) previousFocus.focus({ preventScroll: true });
      dialog.remove();
    }, { once: true });
    previousFocus = document.activeElement;
    document.body.append(dialog);
    try {
      dialog.showModal();
      write("sessionStorage", prefix + "shown", "1");
      dismiss.focus({ preventScroll: true });
      observer = new MutationObserver(() => { if (dialog.open && otherModal()) close(); });
      observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["open", "hidden", "aria-hidden", "aria-modal", "class", "style"] });
    } catch (_) { dialog.remove(); }
  };
  const deadline = Date.now() + 60000;
  const attempt = () => {
    if (suppressed() || Date.now() > deadline) return;
    if (busy()) { timer = window.setTimeout(attempt, 500); return; }
    show();
  };
  timer = window.setTimeout(attempt, 1500);
  window.addEventListener("pagehide", () => { window.clearTimeout(timer); if (observer) observer.disconnect(); }, { once: true });
})();
