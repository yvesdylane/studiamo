/* Exam guard: basic protection while an exam is taken (tests/take.html).

   A web page cannot stop someone from opening another app or tab: browsers do
   not allow it. What this does is make the exam full screen, notice when the
   student leaves it, act on it, and report everything to the server, where the
   lecturer sees it on the exam's Results page:

   - the questions stay hidden until the exam is in full screen (when the device
     cannot do full screen, e.g. an iPhone, the exam starts without it);
   - leaving full screen hides the questions again until the student comes back;
   - leaving the window or tab (another app, another tab, minimised): the first
     time a warning, the next time the exam is submitted automatically;
   - Back, or a link out of the page: "this submits your exam", Submit or Stay;
   - copy, cut, paste, drag-and-drop and the right-click menu are blocked;
   - closing or reloading the tab asks the browser's own "Leave site?" question.

   Settings come from the form: data-guard-url (where events are sent) and
   data-guard-strikes (how many times leaving is allowed before it submits).
*/
(function () {
  "use strict";
  var form = document.getElementById("take");
  if (!form || !form.dataset.guardUrl) return;

  var url = form.dataset.guardUrl;
  var maxStrikes = parseInt(form.dataset.guardStrikes || "2", 10);
  var csrf = (form.querySelector('input[name="csrf_token"]') || {}).value || "";
  var canFullscreen = !!(document.fullscreenEnabled && document.documentElement.requestFullscreen);
  var armed = false, submitting = false, paused = false, strikes = 0, lastStrike = 0;
  var gate = document.getElementById("guard-gate");
  var modal = document.getElementById("guard-modal");
  var sentOnce = {};

  /* ------------------------------------------------------------ reporting */
  function report(event) {
    var d = new FormData();
    d.append("csrf_token", csrf);
    d.append("event", event);
    if (navigator.sendBeacon && navigator.sendBeacon(url, d)) return;   // still goes out while the page closes
    fetch(url, { method: "POST", body: d, credentials: "same-origin", keepalive: true }).catch(function () {});
  }
  function reportOnce(event) {        // copy / paste attempts: once each is enough
    if (sentOnce[event]) return;
    sentOnce[event] = true;
    report(event);
  }

  /* ---------------------------------------------------------- submitting */
  function release() { submitting = true; armed = false; }
  function autoSubmit(reason) {
    if (submitting) return;
    release();
    var input = document.createElement("input");
    input.type = "hidden"; input.name = "auto_submit"; input.value = reason;
    form.appendChild(input);
    window.timeUp = true;             // skips the "unanswered questions" question
    form.submit();                    // form.submit() does not fire the submit event
  }
  // the normal Submit buttons (after the page's own checks)
  form.addEventListener("submit", function (e) { if (!e.defaultPrevented) release(); });
  // the page's own confirm() (unanswered questions) must not count as leaving
  var realConfirm = window.confirm.bind(window);
  window.confirm = function (msg) { paused = true; try { return realConfirm(msg); } finally { setTimeout(function () { paused = false; }, 400); } };
  window.examGuard = { release: release };

  /* --------------------------------------------------------------- modal */
  function show(title, text, buttons) {
    modal.querySelector("h2").textContent = title;
    modal.querySelector("p").textContent = text;
    var row = modal.querySelector(".guard-buttons");
    row.innerHTML = "";
    buttons.forEach(function (b, i) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn" + (b.ghost ? " ghost" : b.danger ? " danger" : "");
      btn.textContent = b.label;
      btn.addEventListener("click", function () { hide(); b.run && b.run(); });
      row.appendChild(btn);
      if (i === buttons.length - 1) setTimeout(function () { btn.focus(); }, 0);
    });
    modal.hidden = false;
    document.body.classList.add("guard-blocked");
  }
  function hide() {
    modal.hidden = true;
    if (gate.hidden) document.body.classList.remove("guard-blocked");
  }
  function askLeave(event) {
    report(event);
    show(tr("LEAVE THE EXAM?"),
      tr("Going back or leaving this page submits your exam now, with the answers you have given so far. You cannot come back to it."),
      [{ label: tr("SUBMIT AND LEAVE"), danger: true, run: function () { autoSubmit("back"); } },
       { label: tr("STAY IN THE EXAM"), run: enterFullscreen }]);
  }

  /* ---------------------------------------------------------- full screen */
  function enterFullscreen() {
    if (!canFullscreen || document.fullscreenElement) return Promise.resolve();
    return document.documentElement.requestFullscreen({ navigationUI: "hide" }).catch(function () {});
  }
  function lockGate(text) {
    gate.querySelector("[data-gate-text]").textContent = text;
    gate.hidden = false;
    document.body.classList.add("guard-blocked");
  }
  function unlockGate() {
    gate.hidden = true;
    if (modal.hidden) document.body.classList.remove("guard-blocked");
  }
  gate.querySelector("[data-gate-start]").addEventListener("click", function () {
    enterFullscreen().then(function () {
      if (canFullscreen && !document.fullscreenElement) {
        gate.querySelector("[data-gate-text]").textContent = tr("Your browser did not allow full screen. Press the button again, or allow full screen for this site.");
        return;
      }
      if (!armed) {
        armed = true;
        report(canFullscreen ? "started_fullscreen" : "no_fullscreen");
        history.pushState({ examGuard: 1 }, "", location.href);    // so Back stays on this page first
      }
      unlockGate();
    });
  });
  document.addEventListener("fullscreenchange", function () {
    if (!armed || submitting || document.fullscreenElement) return;
    report("left_fullscreen");
    lockGate(tr("You left full screen. The questions are hidden until you go back to full screen."));
  });

  /* ------------------------------------------------- leaving the window */
  function strike() {
    if (!armed || submitting || paused) return;
    var now = Date.now();
    if (now - lastStrike < 1500) return;      // one switch fires blur and hidden: count it once
    lastStrike = now;
    strikes += 1;
    report("left_window");
    if (strikes >= maxStrikes) { autoSubmit("left"); return; }
    var left = maxStrikes - strikes;
    show(tr("YOU LEFT THE EXAM"),
      tr("Leaving the exam (another tab, another app, or minimising) is recorded and your lecturer sees it.") + " " +
      (left === 1 ? tr("If you leave once more, your exam is submitted automatically.")
                  : tr("After %(n)s more times, your exam is submitted automatically.", {n: left})),
      [{ label: tr("BACK TO THE EXAM"), run: enterFullscreen }]);
  }
  document.addEventListener("visibilitychange", function () { if (document.hidden) strike(); });
  window.addEventListener("blur", function () {
    // clicking into a code runner (an iframe inside a question) also blurs the window: not leaving
    setTimeout(function () {
      var a = document.activeElement;
      if (a && a.tagName === "IFRAME") return;
      if (!document.hasFocus()) strike();
    }, 0);
  });

  /* ----------------------------------------------------- back and links */
  window.addEventListener("popstate", function () {
    if (!armed || submitting) return;
    history.pushState({ examGuard: 1 }, "", location.href);
    askLeave("back");
  });
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a || !armed || submitting || a.getAttribute("href").charAt(0) === "#") return;
    e.preventDefault();
    askLeave("leave_link");
  }, true);
  window.addEventListener("beforeunload", function (e) {
    if (!armed || submitting) return;
    report("page_closed");
    e.preventDefault();
    e.returnValue = "";     // browsers show their own "Leave site?" text; a custom message is not allowed
  });

  /* -------------------------------------------------- copy, paste, menu */
  ["copy", "cut"].forEach(function (t) {
    document.addEventListener(t, function (e) { if (armed) { e.preventDefault(); reportOnce("copy"); } });
  });
  ["paste", "drop"].forEach(function (t) {
    document.addEventListener(t, function (e) { if (armed) { e.preventDefault(); reportOnce("paste"); } });
  });
  document.addEventListener("contextmenu", function (e) { if (armed) { e.preventDefault(); reportOnce("context_menu"); } });

  /* ---------------------------------------------------------------- start */
  if (!canFullscreen) {
    gate.querySelector("[data-gate-start]").textContent = tr("START THE EXAM »");
    gate.querySelector("[data-gate-note]").textContent =
      tr("This device cannot show the exam in full screen. Stay on this page: leaving it is still detected.");
  }
  lockGate(gate.querySelector("[data-gate-text]").textContent);
})();
