// Active reading time for the skill graph (Reading axis).
// Counts a second only while the page is visible AND the reader did something
// in the last minute (scroll, mouse, keys, touch). Sends at most every 30 s and
// when leaving; the server caps it anyway (see ProgressService.add_reading_time).
(function () {
  "use strict";
  var el = document.getElementById("reading-time");
  if (!el) return;
  var url = el.dataset.url, token = el.dataset.csrf, IDLE = 60, SEND_EVERY = 30;
  var lastActive = Date.now(), pending = 0;
  ["scroll", "mousemove", "keydown", "pointerdown", "touchstart", "wheel", "input"].forEach(function (ev) {
    window.addEventListener(ev, function () { lastActive = Date.now(); }, {passive: true, capture: true});
  });
  function data(seconds) { var d = new FormData(); d.append("seconds", String(seconds)); d.append("csrf_token", token); return d; }
  function flush(leaving) {
    if (pending < 1) return;
    var s = Math.min(pending, 60); pending -= s;
    if (leaving && navigator.sendBeacon) navigator.sendBeacon(url, data(s));
    else fetch(url, {method: "POST", body: data(s), credentials: "same-origin", keepalive: true}).catch(function () {});
  }
  setInterval(function () {
    if (document.visibilityState === "visible" && Date.now() - lastActive < IDLE * 1000) pending++;
    if (pending >= SEND_EVERY) flush(false);
  }, 1000);
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") flush(true); });
  window.addEventListener("pagehide", function () { flush(true); });
})();
