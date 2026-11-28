// Studiamo Markdown in the browser: makes the blocks made by
// src/content/StudiamoMarkdown.py interactive.
//
//   .smd-run      editable code + Run (python, javascript)
//   .smd-ide      several files, tabs, Run the entry file
//   .smd-diagram  draggable boxes and arrows; numbers the reader edits, formulas that update
//
// Code never runs in this page. It runs in a sandboxed iframe (no access to
// the site, its cookies or the page) inside Web Workers, which are stopped
// after RUN_TIMEOUT_MS, so an endless loop cannot freeze the page.
(function () {
  "use strict";

  var RUN_TIMEOUT_MS = 10000;
  var PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";

  // ------------------------------------------------------------------ runner
  // Lives inside the sandboxed iframe. Kept as a function so it is real code;
  // its source text is put into the iframe.
  function runnerMain(PYODIDE_URL) {
    var py = null;          // python worker (kept: loading Python takes seconds)
    var pyReady = false;
    var current = {};       // id -> {worker, timer, kind}

    function send(msg) { parent.postMessage(msg, "*"); }

    var PY_WORKER = [
      "importScripts(" + JSON.stringify(PYODIDE_URL + "pyodide.js") + ");",
      "var ready = loadPyodide({indexURL: " + JSON.stringify(PYODIDE_URL) + "});",
      "var DRIVER = " + JSON.stringify([
        "import os, sys, runpy, shutil, traceback",
        "root = '/home/pyodide/run'",
        "shutil.rmtree(root, ignore_errors=True)",
        "os.makedirs(root)",
        "for path, code in files.items():",
        "    full = os.path.normpath(os.path.join(root, path))",
        "    if not full.startswith(root + '/'):",
        "        raise SystemExit('bad file name: ' + path)",
        "    os.makedirs(os.path.dirname(full), exist_ok=True)",
        "    open(full, 'w').write(code)",
        "for name, mod in list(sys.modules.items()):",
        "    if (getattr(mod, '__file__', None) or '').startswith(root):",
        "        del sys.modules[name]",
        "os.chdir(root)",
        "if root not in sys.path: sys.path.insert(0, root)",
        "try:",
        "    runpy.run_path(os.path.join(root, entry), run_name='__main__')",
        "except SystemExit:",
        "    pass",
        "except BaseException as e:",
        "    tb = traceback.extract_tb(e.__traceback__)",
        "    tb = [f for f in tb if f.filename.startswith(root)]",
        "    print('Traceback (most recent call last):', file=sys.stderr)",
        "    for line in traceback.format_list(tb): print(line.replace(root + '/', ''), end='', file=sys.stderr)",
        "    print(''.join(traceback.format_exception_only(type(e), e)), end='', file=sys.stderr)",
      ].join("\n")) + ";",
      "onmessage = async function (e) {",
      "  var d = e.data;",
      "  try { var p = await ready; } catch (err) { postMessage({id: d.id, kind: 'err', text: 'Could not load Python: ' + err + '\\n'}); postMessage({id: d.id, kind: 'done'}); return; }",
      "  postMessage({id: d.id, kind: 'ready'});",
      "  p.setStdout({batched: function (s) { postMessage({id: d.id, kind: 'out', text: s + '\\n'}); }});",
      "  p.setStderr({batched: function (s) { postMessage({id: d.id, kind: 'err', text: s + '\\n'}); }});",
      "  p.setStdin({stdin: function () { return null; }});",
      "  try { p.globals.set('files', p.toPy(d.files)); p.globals.set('entry', d.entry); await p.runPythonAsync(DRIVER); }",
      "  catch (err) { postMessage({id: d.id, kind: 'err', text: String(err) + '\\n'}); }",
      "  postMessage({id: d.id, kind: 'done'});",
      "};",
    ].join("\n");

    var JS_SHIM = [
      "function fmt(v) {",
      "  if (typeof v === 'string') return v;",
      "  try { if (v instanceof Error) return v.name + ': ' + v.message; return JSON.stringify(v, function (k, x) { return typeof x === 'bigint' ? x + 'n' : (x === undefined ? null : x); }) ?? String(v); } catch (e) { return String(v); }",
      "}",
      "function out(kind) { return function () { postMessage({kind: kind, text: Array.prototype.map.call(arguments, fmt).join(' ') + '\\n'}); }; }",
      "console.log = console.info = console.debug = out('out');",
      "console.error = console.warn = out('err');",
      "self.addEventListener('unhandledrejection', function (e) { postMessage({kind: 'err', text: 'Uncaught (in promise) ' + fmt(e.reason) + '\\n'}); });",
      "self.addEventListener('error', function (e) { postMessage({kind: 'err', text: (e.message || 'Error') + '\\n'}); });",
    ].join("\n");

    function blobURL(text) { return URL.createObjectURL(new Blob([text], {type: "text/javascript"})); }

    function dirname(p) { var i = p.lastIndexOf("/"); return i < 0 ? "" : p.slice(0, i + 1); }
    function resolve(from, spec) {
      var parts = (dirname(from) + spec).split("/"), out = [];
      parts.forEach(function (x) { if (x === "..") out.pop(); else if (x !== "." && x !== "") out.push(x); });
      return out.join("/");
    }
    var IMPORT_RE = /(\bfrom\s*|\bimport\s*\(?\s*)(["'])(\.{1,2}\/[^"']+)\2/g;

    // one blob per file, imports of "./x.js" rewritten to the blobs
    function buildModules(files) {
      var urls = {}, visiting = {};
      function build(path) {
        if (urls[path]) return urls[path];
        if (!(path in files)) throw new Error("Cannot find module '" + path + "'");
        if (visiting[path]) throw new Error("Circular imports are not supported (" + path + ")");
        visiting[path] = true;
        var code = files[path].replace(IMPORT_RE, function (m, pre, q, spec) {
          return pre + q + build(resolve(path, spec)) + q;
        });
        visiting[path] = false;
        return (urls[path] = blobURL(code));
      }
      return build;
    }

    function stop(id, reason) {
      var c = current[id];
      if (!c) return;
      clearTimeout(c.timer);
      if (c.idle) clearTimeout(c.idle);
      if (c.kind === "js") c.worker.terminate();
      if (c.kind === "python" && reason) { py.terminate(); py = null; pyReady = false; }
      delete current[id];
      if (reason) send({id: id, kind: "err", text: reason + "\n"});
      send({id: id, kind: "done"});
    }

    function runPython(d) {
      if (!py) {
        py = new Worker(blobURL(PY_WORKER));
        py.onmessage = function (e) {
          var m = e.data;
          if (m.kind === "ready") { pyReady = true; if (current[m.id]) { clearTimeout(current[m.id].timer); current[m.id].timer = setTimeout(function () { stop(m.id, tr("Stopped: the program ran for more than %(n)s s.", {n: RUN_TIMEOUT_MS / 1000})); }, RUN_TIMEOUT_MS); } return; }
          if (m.kind === "done") { if (current[m.id]) { clearTimeout(current[m.id].timer); delete current[m.id]; } send(m); return; }
          send(m);
        };
      }
      if (!pyReady) send({id: d.id, kind: "info", text: tr("Loading Python (the first run takes a few seconds)…")});
      // the timer starts when Python is loaded ("ready"); until then allow a long load
      current[d.id] = {kind: "python", worker: py, timer: setTimeout(function () { stop(d.id, tr("Python did not load. Check your connection and try again.")); }, 60000)};
      py.postMessage(d);
    }

    function runJs(d) {
      var worker;
      try {
        var build = buildModules(d.files);
        var entryURL = build(d.entry);
        var main = "await import(" + JSON.stringify(blobURL(JS_SHIM)) + ");" +
          "try { await import(" + JSON.stringify(entryURL) + "); } catch (e) { postMessage({kind: 'err', text: (e && e.stack ? String(e.stack).split('\\n')[0] : String(e)) + '\\n'}); }" +
          "postMessage({kind: 'loaded'});";
        worker = new Worker(blobURL(main), {type: "module"});
      } catch (err) {
        send({id: d.id, kind: "err", text: String(err.message || err) + "\n"});
        send({id: d.id, kind: "done"});
        return;
      }
      var c = current[d.id] = {kind: "js", worker: worker};
      c.timer = setTimeout(function () { stop(d.id, tr("Stopped: the program ran for more than %(n)s s.", {n: RUN_TIMEOUT_MS / 1000})); }, RUN_TIMEOUT_MS);
      // after the code has run, wait for timers/promises to go quiet, then finish
      function quiet() { if (c.idle) clearTimeout(c.idle); if (c.loaded) c.idle = setTimeout(function () { stop(d.id); }, 800); }
      worker.onmessage = function (e) {
        var m = e.data;
        if (m.kind === "loaded") { c.loaded = true; quiet(); return; }
        send({id: d.id, kind: m.kind, text: m.text});
        quiet();
      };
      worker.onerror = function (e) { send({id: d.id, kind: "err", text: (e.message || "Error") + "\n"}); e.preventDefault(); };
    }

    window.addEventListener("message", function (e) {
      if (e.source !== parent) return;
      var d = e.data;
      if (d.lang === "python") runPython(d); else runJs(d);
    });
    send({kind: "runner-ready"});
  }

  var Runner = {
    frame: null, ready: null, handlers: {}, seq: 0,
    start: function () {
      if (this.ready) return this.ready;
      var self = this;
      this.ready = new Promise(function (resolve) {
        var f = document.createElement("iframe");
        f.setAttribute("sandbox", "allow-scripts");
        f.setAttribute("aria-hidden", "true");
        f.setAttribute("tabindex", "-1");
        f.style.cssText = "position:absolute;width:0;height:0;border:0;left:-9999px";
        f.srcdoc = "<!doctype html><script>(" + runnerMain.toString() + ")(" + JSON.stringify(PYODIDE_URL) + ");<\/script>";
        window.addEventListener("message", function (e) {
          if (e.source !== f.contentWindow) return;
          var m = e.data || {};
          if (m.kind === "runner-ready") { resolve(); return; }
          var h = self.handlers[m.id];
          if (h) { h(m); if (m.kind === "done") delete self.handlers[m.id]; }
        });
        document.body.appendChild(f);
        self.frame = f;
      });
      return this.ready;
    },
    run: function (lang, files, entry, onMessage) {
      var self = this, id = "r" + (++this.seq);
      this.handlers[id] = onMessage;
      return this.start().then(function () {
        self.frame.contentWindow.postMessage({id: id, lang: lang, files: files, entry: entry}, "*");
      });
    }
  };

  // --------------------------------------------------------------- editors
  function autosize(t) { t.style.height = "auto"; t.style.height = Math.max(60, t.scrollHeight + 4) + "px"; }
  function editor(t, onRun) {
    t.dataset.original = t.value;
    autosize(t);
    t.addEventListener("input", function () { autosize(t); });
    t.addEventListener("keydown", function (e) {
      if (e.key === "Tab" && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
        // Tab indents; Esc then Tab leaves the editor (keyboard users)
        if (t.dataset.escaped === "1") { t.dataset.escaped = ""; return; }
        e.preventDefault();
        var s = t.selectionStart, end = t.selectionEnd;
        t.value = t.value.slice(0, s) + "    " + t.value.slice(end);
        t.selectionStart = t.selectionEnd = s + 4;
        autosize(t);
      } else if (e.key === "Escape") {
        t.dataset.escaped = "1";
      } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        onRun();
      }
    });
  }

  function runBlock(block, lang, files, entry) {
    var out = block.querySelector(".smd-out"), pre = out.querySelector("pre");
    var play = block.querySelector(".smd-play");
    out.hidden = false;
    pre.textContent = "";
    pre.classList.remove("smd-has-err");
    var info = document.createElement("span");
    info.className = "smd-info";
    info.textContent = tr("Running…");
    pre.appendChild(info);
    play.disabled = true;
    var any = false;
    Runner.run(lang, files, entry, function (m) {
      if (m.kind === "info") { info.textContent = m.text; return; }
      if (m.kind === "ready") return;
      if (m.kind === "done") {
        play.disabled = false;
        if (info.parentNode) info.remove();
        if (!any) { var e = document.createElement("span"); e.className = "smd-info"; e.textContent = "(no output)"; pre.appendChild(e); }
        return;
      }
      if (info.parentNode) info.remove();
      any = true;
      var span = document.createElement("span");
      if (m.kind === "err") { span.className = "smd-err"; pre.classList.add("smd-has-err"); }
      span.textContent = m.text;
      pre.appendChild(span);
    });
  }

  function initRun(block) {
    var t = block.querySelector(".smd-editor"), lang = block.dataset.lang;
    var entry = lang === "python" ? "main.py" : "main.js";
    function go() { var f = {}; f[entry] = t.value; runBlock(block, lang, f, entry); }
    editor(t, go);
    block.querySelector(".smd-play").addEventListener("click", go);
    block.querySelector(".smd-reset").addEventListener("click", function () {
      t.value = t.dataset.original; autosize(t); block.querySelector(".smd-out").hidden = true;
    });
  }

  function initIde(block) {
    var lang = block.dataset.lang, entry = block.dataset.entry;
    var files = Array.prototype.slice.call(block.querySelectorAll(".smd-file"));
    var tabs = Array.prototype.slice.call(block.querySelectorAll(".smd-tab"));
    function go() {
      var f = {};
      files.forEach(function (t) { f[t.dataset.path] = t.value; });
      runBlock(block, lang, f, entry);
    }
    files.forEach(function (t) { t.hidden = false; editor(t, go); t.hidden = t.dataset.path !== entry; });
    tabs.forEach(function (b) {
      b.addEventListener("click", function () {
        tabs.forEach(function (x) { x.removeAttribute("aria-current"); });
        b.setAttribute("aria-current", "true");
        files.forEach(function (t) { t.hidden = t.dataset.path !== b.dataset.path; if (!t.hidden) { autosize(t); t.focus(); } });
      });
    });
    block.querySelector(".smd-play").addEventListener("click", go);
    block.querySelector(".smd-reset").addEventListener("click", function () {
      files.forEach(function (t) { t.value = t.dataset.original; });
      files.forEach(function (t) { if (!t.hidden) autosize(t); });
      block.querySelector(".smd-out").hidden = true;
    });
  }

  // --------------------------------------------------------------- diagram
  // A tiny, safe formula language: numbers, box ids, + - * / % ^ ( ) and
  // min max abs sqrt round floor ceil pow. No eval.
  var FUNCS = {min: Math.min, max: Math.max, abs: Math.abs, sqrt: Math.sqrt, round: Math.round,
               floor: Math.floor, ceil: Math.ceil, pow: Math.pow};
  function parseExpr(src) {
    var toks = src.match(/\d+(?:\.\d+)?|[A-Za-z_]\w*|[-+*/%^(),]/g) || [], i = 0;
    function peek() { return toks[i]; }
    function next() { return toks[i++]; }
    function primary() {
      var t = next();
      if (t === undefined) throw new Error("incomplete formula");
      if (t === "(") { var e = add(); if (next() !== ")") throw new Error("missing )"); return e; }
      if (t === "-") { var u = power(); return function (v) { return -u(v); }; }
      if (/^\d/.test(t)) { var n = parseFloat(t); return function () { return n; }; }
      if (/^[A-Za-z_]/.test(t)) {
        if (peek() === "(" && FUNCS[t]) {
          next(); var args = [];
          if (peek() !== ")") { args.push(add()); while (peek() === ",") { next(); args.push(add()); } }
          if (next() !== ")") throw new Error("missing )");
          var fn = FUNCS[t];
          return function (v) { return fn.apply(null, args.map(function (a) { return a(v); })); };
        }
        return function (v) { if (!(t in v)) throw new Error("unknown " + t); return v[t]; };
      }
      throw new Error("unexpected " + t);
    }
    function power() { var b = primary(); if (peek() === "^") { next(); var x = power(); return function (v) { return Math.pow(b(v), x(v)); }; } return b; }
    function mul() {
      var l = power();
      while (peek() === "*" || peek() === "/" || peek() === "%") {
        var op = next(), r = power(), a = l;
        l = op === "*" ? function (v) { return a(v) * r(v); } : op === "/" ? function (v) { return a(v) / r(v); } : function (v) { return a(v) % r(v); };
      }
      return l;
    }
    function add() {
      var l = mul();
      while (peek() === "+" || peek() === "-") {
        var op = next(), r = mul(), a = l;
        l = op === "+" ? function (v) { return a(v) + r(v); } : function (v) { return a(v) - r(v); };
      }
      return l;
    }
    var f = add();
    if (i < toks.length) throw new Error("unexpected " + toks[i]);
    return f;
  }
  function fmtNum(x) {
    if (typeof x !== "number" || !isFinite(x)) return "?";
    return Math.abs(x) >= 1e6 || (Math.abs(x) < 1e-3 && x !== 0) ? x.toExponential(3) : String(Math.round(x * 1000) / 1000);
  }

  var GAP_X = 70, GAP_Y = 28, PAD = 16, MIN_SCALE = 0.55;

  function initDiagram(fig) {
    var data;
    try { data = JSON.parse(fig.dataset.diagram); } catch (e) { return; }
    var canvas = fig.querySelector(".smd-canvas");
    canvas.textContent = "";
    // everything sits on a stage, in "diagram units" (the positions saved in the note);
    // the stage is scaled down proportionally when the page is narrower than the diagram
    var stage = document.createElement("div");
    stage.className = "smd-stage";
    canvas.appendChild(stage);
    var scale = 1;
    var byId = {}, nodes = data.nodes, edges = data.edges;
    nodes.forEach(function (n) { byId[n.id] = n; if (n.expr) { try { n.fn = parseExpr(n.expr); } catch (e) { n.parseError = e.message; } } });

    // layout: column = longest chain of arrows leading to the box
    var depth = {};
    function d(id, seen) {
      if (depth[id] !== undefined) return depth[id];
      if (seen[id]) return 0;
      seen[id] = true;
      var m = 0;
      edges.forEach(function (e) { if (e.to === id && byId[e.from]) m = Math.max(m, d(e.from, seen) + 1); });
      return (depth[id] = m);
    }
    nodes.forEach(function (n) { n.col = d(n.id, {}); n.saved = n.x !== undefined; });

    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("class", "smd-edges");
    svg.setAttribute("aria-hidden", "true");
    var mid = "smd-arrow-" + Math.random().toString(36).slice(2);
    svg.innerHTML = '<defs><marker id="' + mid + '" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" style="fill:var(--ink)"/></marker></defs>';
    stage.appendChild(svg);

    var lines = edges.map(function (e) {
      var g = document.createElementNS(svgNS, "g");
      var p = document.createElementNS(svgNS, "path");
      p.setAttribute("marker-end", "url(#" + mid + ")");
      g.appendChild(p);
      var t = document.createElementNS(svgNS, "text");
      t.textContent = e.label || "";
      g.appendChild(t);
      svg.appendChild(g);
      return {e: e, path: p, text: t};
    });

    nodes.forEach(function (n) {
      var el = document.createElement("div");
      el.className = "smd-node" + ("value" in n ? " smd-input" : n.expr ? " smd-formula" : "");
      el.tabIndex = 0;
      el.setAttribute("aria-label", n.label + " (drag, or use the arrow keys, to move)");
      var label = document.createElement("span");
      label.className = "smd-node-label";
      label.textContent = n.label;
      el.appendChild(label);
      if ("value" in n) {
        var inp = document.createElement("input");
        inp.type = "number"; inp.step = "any"; inp.value = n.value;
        inp.setAttribute("aria-label", n.label);
        inp.addEventListener("input", function () { var x = parseFloat(inp.value); n.value = isNaN(x) ? 0 : x; compute(); });
        inp.addEventListener("pointerdown", function (e) { e.stopPropagation(); });
        el.appendChild(inp);
        n.input = inp;
      } else if (n.expr) {
        var out = document.createElement("output");
        out.title = n.expr;
        el.appendChild(out);
        var f = document.createElement("span");
        f.className = "smd-node-expr";
        f.textContent = "= " + n.expr;
        el.appendChild(f);
        n.output = out;
      }
      stage.appendChild(el);
      n.el = el;

      // drag with mouse, pen or touch
      el.addEventListener("pointerdown", function (e) {
        if (e.button !== 0) return;
        el.setPointerCapture(e.pointerId);
        var sx = e.clientX, sy = e.clientY, ox = n.x, oy = n.y, moved = false;
        el.classList.add("smd-dragging");
        function move(ev) {                       // pointer pixels / scale = diagram units
          n.x = Math.max(0, Math.round(ox + (ev.clientX - sx) / scale)); n.y = Math.max(0, Math.round(oy + (ev.clientY - sy) / scale));
          moved = true; place(n); redraw(false);
        }
        function up() {
          el.classList.remove("smd-dragging"); el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); el.removeEventListener("pointercancel", up);
          if (moved) { fit(); announce(); }
        }
        el.addEventListener("pointermove", move);
        el.addEventListener("pointerup", up);
        el.addEventListener("pointercancel", up);
      });
      el.addEventListener("keydown", function (e) {
        if (e.target !== el) return;
        var step = e.shiftKey ? 40 : 10, dx = 0, dy = 0;
        if (e.key === "ArrowLeft") dx = -step; else if (e.key === "ArrowRight") dx = step;
        else if (e.key === "ArrowUp") dy = -step; else if (e.key === "ArrowDown") dy = step; else return;
        e.preventDefault();
        n.x = Math.max(0, n.x + dx); n.y = Math.max(0, n.y + dy); place(n); redraw(); announce();
      });
    });

    // automatic layout for boxes without a saved position: one column per step of
    // arrows, each column as wide as its widest box, rows as tall as their boxes
    var colW = {}, colY = {};
    function bw(n) { return n.el.offsetWidth || 140; }      // drawn while hidden: guess a size
    function bh(n) { return n.el.offsetHeight || 60; }
    nodes.forEach(function (n) { colW[n.col] = Math.max(colW[n.col] || 0, bw(n)); });
    var colX = {}, x = PAD;
    Object.keys(colW).map(Number).sort(function (a, b) { return a - b; }).forEach(function (c) { colX[c] = x; x += colW[c] + GAP_X; });
    nodes.forEach(function (n) {
      if (!n.saved) {
        colY[n.col] = colY[n.col] === undefined ? PAD : colY[n.col];
        n.x = colX[n.col]; n.y = colY[n.col];
        colY[n.col] += bh(n) + GAP_Y;
      }
      n.x0 = n.x; n.y0 = n.y; n.v0 = n.value;
      place(n);
    });

    // tell the page (the note editor) where the boxes are now, in diagram units
    function announce() {
      var pos = {};
      nodes.forEach(function (n) { pos[n.id] = [Math.round(n.x), Math.round(n.y)]; });
      fig.dispatchEvent(new CustomEvent("smd-diagram-moved", {bubbles: true, detail: {positions: pos}}));
    }

    function place(n) { n.el.style.left = n.x + "px"; n.el.style.top = n.y + "px"; }

    function centre(n) { return {x: n.x + n.el.offsetWidth / 2, y: n.y + n.el.offsetHeight / 2, w: n.el.offsetWidth, h: n.el.offsetHeight}; }
    // where the line from a's centre towards b leaves a's box
    function edgePoint(c, toward) {
      var dx = toward.x - c.x, dy = toward.y - c.y;
      if (!dx && !dy) return {x: c.x, y: c.y};
      var sx = (c.w / 2) / Math.abs(dx || 1e-9), sy = (c.h / 2) / Math.abs(dy || 1e-9), s = Math.min(sx, sy);
      return {x: c.x + dx * s, y: c.y + dy * s};
    }
    var size = {w: 0, h: 0};
    // scale the stage so the whole diagram fits the width it has (never above 100%)
    function fit() {
      var avail = canvas.clientWidth || size.w;
      scale = size.w > avail ? Math.max(MIN_SCALE, avail / size.w) : 1;
      stage.style.transform = scale < 1 ? "scale(" + scale + ")" : "";
      stage.style.width = size.w + "px";
      stage.style.height = size.h + "px";
      canvas.style.height = Math.ceil(size.h * scale) + "px";
    }
    function redraw(refit) {
      var w = 0, h = 0;
      nodes.forEach(function (n) { w = Math.max(w, n.x + n.el.offsetWidth); h = Math.max(h, n.y + n.el.offsetHeight); });
      size = {w: w + PAD, h: h + PAD};
      if (refit !== false) fit();
      svg.setAttribute("width", size.w); svg.setAttribute("height", size.h);
      lines.forEach(function (l) {
        var a = byId[l.e.from], b = byId[l.e.to];
        if (!a || !b) return;
        var ca = centre(a), cb = centre(b);
        var p1 = edgePoint(ca, cb), p2 = edgePoint(cb, ca);
        l.path.setAttribute("d", "M" + p1.x + " " + p1.y + "L" + p2.x + " " + p2.y);
        l.text.setAttribute("x", (p1.x + p2.x) / 2 + 4);
        l.text.setAttribute("y", (p1.y + p2.y) / 2 - 6);
      });
    }

    function compute() {
      var vals = {}, state = {};
      function val(id) {
        var n = byId[id];
        if (!n) throw new Error("unknown " + id);
        if (state[id] === 1) throw new Error("loop");
        if (state[id] === 2) return vals[id];
        state[id] = 1;
        var v;
        if ("value" in n) v = n.value;
        else if (n.fn) {
          var scope = new Proxy({}, {has: function (t, k) { return k in byId; }, get: function (t, k) { return val(k); }});
          v = n.fn(scope);
        } else v = NaN;
        state[id] = 2;
        return (vals[id] = v);
      }
      nodes.forEach(function (n) {
        if (!n.output) return;
        var before = n.output.textContent, text;
        if (n.parseError) text = "error";
        else { try { text = fmtNum(val(n.id)); } catch (e) { text = e.message === "loop" ? "loop!" : "?"; n.output.title = e.message; } }
        n.output.textContent = text;
        if (before && before !== text) { n.el.classList.remove("smd-changed"); void n.el.offsetWidth; n.el.classList.add("smd-changed"); }
      });
    }

    fig.querySelector(".smd-reset").addEventListener("click", function () {
      nodes.forEach(function (n) { n.x = n.x0; n.y = n.y0; if ("value" in n) { n.value = n.v0; n.input.value = n.v0; } place(n); });
      compute(); redraw();
    });
    compute();
    redraw();
    window.addEventListener("resize", function () { fit(); });
  }

  function init(root) {
    (root || document).querySelectorAll(".smd-run:not([data-ready])").forEach(function (b) { b.dataset.ready = "1"; initRun(b); });
    (root || document).querySelectorAll(".smd-ide:not([data-ready])").forEach(function (b) { b.dataset.ready = "1"; initIde(b); });
    (root || document).querySelectorAll(".smd-diagram:not([data-ready])").forEach(function (b) { b.dataset.ready = "1"; initDiagram(b); });
  }
  window.StudiamoMarkdown = {init: init};
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { init(); });
  else init();
})();
