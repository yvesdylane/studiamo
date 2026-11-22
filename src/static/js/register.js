// Sign-up page: two steps on one page, and the Google button follows the role.
// Without JavaScript both steps simply show one under the other.
(function () {
  document.documentElement.classList.add("js");
  var form = document.getElementById("signup");
  if (!form) return;
  var steps = form.querySelectorAll(".step");
  var marks = document.querySelectorAll(".steps span");
  var google = document.getElementById("google-signup");

  function role() {
    var r = form.querySelector('input[name="role"]:checked');
    return r ? r.value : null;
  }

  function show(n, focus) {
    steps.forEach(function (s) { s.hidden = s.dataset.step !== String(n); });
    marks.forEach(function (m) { m.classList.toggle("on", Number(m.dataset.step) <= n); });
    if (focus) {
      var first = form.querySelector('.step[data-step="' + n + '"] input:not([type=hidden]), .step[data-step="' + n + '"] select');
      // a field of the other role is hidden: pick the first visible one
      var all = form.querySelectorAll('.step[data-step="' + n + '"] input:not([type=hidden]), .step[data-step="' + n + '"] select');
      for (var i = 0; i < all.length; i++) { if (all[i].offsetParent !== null) { first = all[i]; break; } }
      if (first) first.focus();
    }
  }

  function stepOneValid() {
    var fields = form.querySelectorAll('.step[data-step="1"] input:not([type=hidden])');
    for (var i = 0; i < fields.length; i++) {
      if (!fields[i].checkValidity()) { fields[i].reportValidity(); return false; }
    }
    return true;
  }

  function syncGoogle() {
    if (!google) return;
    var url = new URL(google.dataset.base, window.location.href);
    var r = role();
    if (r) url.searchParams.set("role", r); else url.searchParams.delete("role");
    google.href = url.pathname + url.search;
  }

  form.querySelectorAll('input[name="role"]').forEach(function (r) {
    r.addEventListener("change", function () {
      syncGoogle();
      // keep the address in step with the choice, without leaving the page
      var url = new URL(window.location.href);
      url.searchParams.set("role", r.value);
      history.replaceState(null, "", url);
    });
  });
  var next = document.getElementById("step-next");
  if (next) next.addEventListener("click", function () { if (stepOneValid()) show(2, true); });
  var back = document.getElementById("step-back");
  if (back) back.addEventListener("click", function () { show(1, true); });
  // Enter in step one goes to step two instead of submitting
  form.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.closest('.step[data-step="1"]') && e.target.tagName === "INPUT") {
      e.preventDefault();
      if (stepOneValid()) show(2, true);
    }
  });

  syncGoogle();
  show(Number(form.dataset.step) || 1, false);
})();
