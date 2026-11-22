// The note editor (studio/note_edit.html).
//
//  * three views: Markdown only, side by side, preview only (remembered)
//  * write in the preview like a word processor: paragraphs, headings, lists and
//    quotes are editable there and turned back into Markdown; interactive blocks
//    (code, diagrams, file trees...) get Edit / move / delete buttons
//  * one undo history for everything: Ctrl+Z, Ctrl+Shift+Z / Ctrl+Y
//  * drag and drop (or click) blocks from the palette; drop or paste images
//
// The Markdown in the textarea is the only truth: the preview is re-rendered by
// the server (with data-src="first,last" source lines on every top-level block).
(function () {
  "use strict";
  var form = document.getElementById("note-form");
  if (!form) return;
  var src = document.getElementById("f-body");
  var out = document.getElementById("preview");
  var probs = document.getElementById("problems");
  var grid = document.getElementById("editor-grid");
  var token = form.querySelector('input[name="csrf_token"]').value;
  var PREVIEW_URL = form.dataset.previewUrl, UPLOAD_URL = form.dataset.uploadUrl;
  var dirty = false;

  // ------------------------------------------------------------ history
  var history = {stack: [{v: src.value, s: 0, e: 0}], i: 0, last: 0};
  function record(group) {
    var top = history.stack[history.i], now = Date.now();
    if (top.v === src.value) return;
    var entry = {v: src.value, s: src.selectionStart, e: src.selectionEnd};
    if (group && now - history.last < 700 && history.i > 0) history.stack[history.i] = entry;   // typing: one step
    else { history.stack.splice(history.i + 1); history.stack.push(entry); history.i++; }
    if (history.stack.length > 300) { history.stack.shift(); history.i--; }
    history.last = group ? now : 0;
    dirty = true;
  }
  function restore(entry) {
    src.value = entry.v;
    try { src.setSelectionRange(entry.s, entry.e); } catch (e) {}
    history.last = 0;
    refresh();
  }
  function undo() { commitActive(); if (history.i > 0) { history.i--; restore(history.stack[history.i]); } }
  function redo() { if (history.i < history.stack.length - 1) { history.i++; restore(history.stack[history.i]); } }
  function setSource(value, caret) {
    src.value = value;
    if (caret != null) { try { src.setSelectionRange(caret, caret); } catch (e) {} }
    record(false);
  }

  document.addEventListener("keydown", function (e) {
    if (!(e.ctrlKey || e.metaKey)) return;
    var k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
    else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); redo(); }
    else if (k === "s") { e.preventDefault(); commitActive(); dirty = false; form.submit(); }
    else if (k === "b" && inEditor(e.target)) { e.preventDefault(); format("bold"); }
    else if (k === "i" && inEditor(e.target)) { e.preventDefault(); format("italic"); }
  });
  function inEditor(el) { return el === src || (el && el.closest && el.closest("#preview")); }

  // -------------------------------------------------------------- views
  var MODE_KEY = "studiamo-editor-mode";
  function setMode(mode) {
    grid.dataset.mode = mode;
    document.querySelectorAll("[data-mode-btn]").forEach(function (b) { b.setAttribute("aria-pressed", b.dataset.modeBtn === mode ? "true" : "false"); });
    try { localStorage.setItem(MODE_KEY, mode); } catch (e) {}
    window.dispatchEvent(new Event("resize"));      // diagrams refit to the new width
  }
  document.querySelectorAll("[data-mode-btn]").forEach(function (b) { b.addEventListener("click", function () { commitActive(); setMode(b.dataset.modeBtn); }); });
  var saved = null;
  try { saved = localStorage.getItem(MODE_KEY); } catch (e) {}
  setMode(saved === "md" || saved === "preview" ? saved : "split");

  // ------------------------------------------------------------ preview
  var timer = null, seq = 0;
  function schedule() { clearTimeout(timer); timer = setTimeout(refresh, 450); }
  function refresh() {
    clearTimeout(timer);
    var mine = ++seq, data = new FormData();
    data.append("body", src.value); data.append("csrf_token", token); data.append("source_map", "1");
    fetch(PREVIEW_URL, {method: "POST", body: data, credentials: "same-origin"})
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (mine !== seq) return;                       // an older answer
        if (out.contains(document.activeElement) && document.activeElement.isContentEditable) return;  // do not yank the caret
        var y = out.parentNode.scrollTop;
        out.innerHTML = j.html;
        decorate();
        window.StudiamoMarkdown.init(out);
        out.parentNode.scrollTop = y;
        probs.hidden = !j.problems.length;
        probs.innerHTML = "";
        j.problems.forEach(function (p) { var d = document.createElement("div"); d.textContent = "⚠ " + p; probs.appendChild(d); });
      }).catch(function () {});
  }
  src.addEventListener("input", function () { record(true); schedule(); });

  // --------------------------------------------------- source line helpers
  function range(el) { var p = (el.dataset.src || "").split(","); return [parseInt(p[0], 10), parseInt(p[1], 10)]; }
  function blocks() { return Array.prototype.filter.call(out.children, function (c) { return c.dataset && c.dataset.src; }); }
  function replaceLines(a, b, text, opts) {
    var lines = src.value.split("\n");
    var add = text === null ? [] : text.split("\n");
    lines.splice.apply(lines, [a, b - a].concat(add));
    var value = lines.join("\n");
    var delta = add.length - (b - a);
    if (opts && opts.el) {           // keep the other blocks' line numbers right without re-rendering
      opts.el.dataset.src = a + "," + (a + add.length);
      blocks().forEach(function (o) { if (o !== opts.el) { var r = range(o); if (r[0] >= b) o.dataset.src = (r[0] + delta) + "," + (r[1] + delta); } });
    }
    setSource(value);
    if (!opts || !opts.quiet) refresh();
  }

  // ------------------------------------------------ HTML -> Markdown (text blocks)
  function esc(t) { return t.replace(/\\/g, "\\\\").replace(/([*_`\[\]])/g, "\\$1"); }
  function inline(node) {
    var outText = "";
    node.childNodes.forEach(function (n) {
      if (n.nodeType === 3) { outText += esc(n.nodeValue.replace(/ /g, " ")); return; }
      if (n.nodeType !== 1) return;
      var tag = n.tagName, inner = inline(n);
      if (tag === "STRONG" || tag === "B") outText += inner.trim() ? "**" + inner + "**" : inner;
      else if (tag === "EM" || tag === "I") outText += inner.trim() ? "*" + inner + "*" : inner;
      else if (tag === "CODE") outText += "`" + n.textContent + "`";
      else if (tag === "S" || tag === "DEL" || tag === "STRIKE") outText += "~~" + inner + "~~";
      else if (tag === "A") outText += "[" + inner + "](" + (n.getAttribute("href") || "") + ")";
      else if (tag === "IMG") outText += "![" + (n.getAttribute("alt") || "") + "](" + (n.getAttribute("src") || "") + ")";
      else if (tag === "BR") outText += "\n";
      else outText += inner;
    });
    return outText;
  }
  // a block's lines, split where the browser put <div>, <p> or <br> (Enter)
  function paragraphs(el) {
    var parts = [], cur = "";
    el.childNodes.forEach(function (n) {
      if (n.nodeType === 1 && (n.tagName === "DIV" || n.tagName === "P")) { if (cur.trim()) parts.push(cur); cur = ""; var t = inline(n); if (t.trim()) parts.push(t); }
      else if (n.nodeType === 1 && n.tagName === "BR") { if (cur.trim()) parts.push(cur); cur = ""; }
      else { var w = document.createElement("span"); w.appendChild(n.cloneNode(true)); cur += inline(w); }
    });
    if (cur.trim()) parts.push(cur);
    return parts.map(function (p) { return p.replace(/\n+/g, " ").trim(); }).filter(Boolean);
  }
  function lineStart(t) { return t.replace(/^(#{1,6}\s|>\s?|[-+*]\s|\d+[.)]\s)/, "\\$1"); }
  function list(el, depth) {
    var ordered = el.tagName === "OL", n = 0, pad = new Array(depth + 1).join(ordered ? "   " : "  "), lines = [];
    el.childNodes.forEach(function (li) {
      if (li.nodeType !== 1) return;
      if (li.tagName === "UL" || li.tagName === "OL") { lines = lines.concat(list(li, depth + 1)); return; }
      var clone = li.cloneNode(true), subs = [];
      clone.querySelectorAll(":scope > ul, :scope > ol").forEach(function (s) { subs.push(s); s.remove(); });
      var text = paragraphs(clone).join(" ");
      if (!text && !subs.length) return;
      n++;
      lines.push(pad + (ordered ? n + ". " : "- ") + text);
      subs.forEach(function (s) { lines = lines.concat(list(s, depth + 1)); });
    });
    return lines;
  }
  function blockToMd(el) {
    var tag = el.tagName;
    if (/^H[1-6]$/.test(tag)) {
      var parts = paragraphs(el);
      if (!parts.length) return "";
      return new Array(+tag[1] + 1).join("#") + " " + parts[0] + (parts.length > 1 ? "\n\n" + parts.slice(1).map(lineStart).join("\n\n") : "");
    }
    if (tag === "UL" || tag === "OL") return list(el, 0).join("\n");
    if (tag === "BLOCKQUOTE") {
      var inner = [];
      el.childNodes.forEach(function (c) { if (c.nodeType === 1) { var m = /^(UL|OL)$/.test(c.tagName) ? list(c, 0).join("\n") : paragraphs(c).join("\n\n"); if (m) inner.push(m); } else if (c.nodeType === 3 && c.nodeValue.trim()) inner.push(esc(c.nodeValue.trim())); });
      return inner.join("\n\n").split("\n").map(function (l) { return l ? "> " + l : ">"; }).join("\n");
    }
    return paragraphs(el).map(lineStart).join("\n\n");
  }

  // ------------------------------------------------- decorating the preview
  var TEXT_BLOCKS = /^(P|H[1-6]|UL|OL|BLOCKQUOTE)$/;
  var active = null, activeTimer = null;
  function decorate() {
    blocks().forEach(function (el) {
      if (TEXT_BLOCKS.test(el.tagName)) {
        el.contentEditable = "true";
        el.classList.add("ed-text");
        el.spellcheck = true;
      } else {
        el.classList.add("ed-atomic");
      }
      var bar = document.createElement("span");
      bar.className = "ed-bar";
      bar.contentEditable = "false";
      bar.innerHTML = (TEXT_BLOCKS.test(el.tagName) ? "" : '<button type="button" data-act="edit">Edit</button>') +
        '<button type="button" data-act="up" aria-label="Move up">↑</button><button type="button" data-act="down" aria-label="Move down">↓</button>' +
        '<button type="button" data-act="del" aria-label="Delete block">✕</button>';
      el.insertBefore(bar, el.firstChild);
    });
  }
  function cleanClone(el) { var c = el.cloneNode(true); c.querySelectorAll(".ed-bar").forEach(function (b) { b.remove(); }); return c; }
  function commit(el) {
    if (!el || !el.isConnected || !el.dataset.src) return;
    var r = range(el), md = blockToMd(cleanClone(el));
    var current = src.value.split("\n").slice(r[0], r[1]).join("\n");
    if (md === current) return;
    replaceLines(r[0], r[1], md === "" ? null : md, {el: el, quiet: true});
  }
  function commitActive() { if (active) { clearTimeout(activeTimer); commit(active); } }
  out.addEventListener("input", function (e) {
    var el = e.target.closest && e.target.closest(".ed-text");
    if (!el) return;
    active = el;
    clearTimeout(activeTimer);
    activeTimer = setTimeout(function () { commit(el); }, 500);
  });
  out.addEventListener("focusin", function (e) { var el = e.target.closest && e.target.closest(".ed-text"); if (el && el !== active) { commitActive(); active = el; } });
  out.addEventListener("focusout", function () {
    setTimeout(function () {
      if (out.contains(document.activeElement)) return;
      commitActive(); active = null; refresh();          // left the preview: show the clean result
    }, 50);
  });

  // block buttons: edit source, move, delete
  out.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest(".ed-bar button");
    if (!btn) return;
    e.preventDefault();
    commitActive();
    var el = btn.closest("[data-src]"), r = range(el), act = btn.dataset.act;
    var list = blocks(), i = list.indexOf(el);
    if (act === "del") {
      var lines = src.value.split("\n"), end = r[1];
      if (lines[end] === "") end++;                         // and the blank line after it
      replaceLines(r[0], end, null);
    } else if (act === "up" || act === "down") {
      var other = list[act === "up" ? i - 1 : i + 1];
      if (!other) return;
      var first = act === "up" ? other : el, second = act === "up" ? el : other;
      var a = range(first), b = range(second), L = src.value.split("\n");
      var moved = L.slice(b[0], b[1]).concat(L.slice(a[1], b[0]), L.slice(a[0], a[1]));
      replaceLines(a[0], b[1], moved.join("\n"));
    } else if (act === "edit") {
      openSourceEditor(el, r);
    }
  });
  function openSourceEditor(el, r) {
    var box = document.createElement("div");
    box.className = "ed-source";
    var ta = document.createElement("textarea");
    ta.value = src.value.split("\n").slice(r[0], r[1]).join("\n");
    ta.rows = Math.min(24, Math.max(4, ta.value.split("\n").length + 1));
    ta.setAttribute("aria-label", "Source of this block");
    var save = document.createElement("button"); save.type = "button"; save.className = "btn xs"; save.textContent = "APPLY";
    var cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "btn xs ghost"; cancel.textContent = "CANCEL";
    box.appendChild(ta); box.appendChild(save); box.appendChild(cancel);
    el.replaceWith(box);
    ta.focus();
    save.addEventListener("click", function () { replaceLines(r[0], r[1], ta.value === "" ? null : ta.value); });
    cancel.addEventListener("click", refresh);
  }

  // ------------------------------------------------------------ toolbar
  function wrapSelection(before, after, placeholder) {
    var s = src.selectionStart, e = src.selectionEnd, v = src.value, sel = v.slice(s, e) || placeholder;
    setSource(v.slice(0, s) + before + sel + after + v.slice(e));
    src.focus(); src.setSelectionRange(s + before.length, s + before.length + sel.length);
    schedule();
  }
  function prefixLines(prefix) {
    var v = src.value, s = v.lastIndexOf("\n", src.selectionStart - 1) + 1, e = v.indexOf("\n", src.selectionEnd); if (e < 0) e = v.length;
    var block = v.slice(s, e).split("\n").map(function (l, i) {
      var bare = l.replace(/^(#{1,6}\s|>\s?|[-+*]\s|\d+[.)]\s)/, "");
      return (typeof prefix === "function" ? prefix(i) : prefix) + bare;
    }).join("\n");
    setSource(v.slice(0, s) + block + v.slice(e)); src.focus(); schedule();
  }
  var BLOCK_PREFIX = {h2: "## ", h3: "### ", ul: "- ", ol: function (i) { return (i + 1) + ". "; }, quote: "> ", p: ""};
  function previewBlock() {
    var sel = window.getSelection(), node = sel && sel.anchorNode;
    var el = node && (node.nodeType === 1 ? node : node.parentNode).closest && (node.nodeType === 1 ? node : node.parentNode).closest("#preview > [data-src]");
    return el || null;
  }
  function format(kind) {
    var inPreview = grid.dataset.mode === "preview" || (document.activeElement && out.contains(document.activeElement));
    if (inPreview && previewBlock() && ["bold", "italic", "code", "link", "strike"].indexOf(kind) >= 0) {
      if (kind === "bold" || kind === "italic" || kind === "strike") document.execCommand(kind === "strike" ? "strikeThrough" : kind);
      else if (kind === "link") { var url = prompt("Link address (https://…)"); if (url) document.execCommand("createLink", false, url); }
      else if (kind === "code") {
        var s = window.getSelection(); if (!s.rangeCount || s.isCollapsed) return;
        var rg = s.getRangeAt(0), c = document.createElement("code"); c.textContent = rg.toString(); rg.deleteContents(); rg.insertNode(c);
      }
      var el = previewBlock(); if (el) { active = el; commit(el); }
      return;
    }
    if (inPreview && previewBlock() && BLOCK_PREFIX.hasOwnProperty(kind)) {
      var b = previewBlock(), r = range(b); commit(b); r = range(b);
      var text = src.value.split("\n").slice(r[0], r[1]).join("\n");
      var p = BLOCK_PREFIX[kind];
      var md = text.split("\n").map(function (l, i) { return (typeof p === "function" ? p(i) : p) + l.replace(/^(#{1,6}\s|>\s?|[-+*]\s|\d+[.)]\s)/, ""); }).join("\n");
      replaceLines(r[0], r[1], md);
      return;
    }
    if (kind === "bold") wrapSelection("**", "**", "bold text");
    else if (kind === "italic") wrapSelection("*", "*", "italic text");
    else if (kind === "strike") wrapSelection("~~", "~~", "text");
    else if (kind === "code") wrapSelection("`", "`", "code");
    else if (kind === "link") { var u = prompt("Link address (https://…)"); if (u) wrapSelection("[", "](" + u + ")", "link text"); }
    else if (BLOCK_PREFIX.hasOwnProperty(kind)) prefixLines(BLOCK_PREFIX[kind]);
  }
  document.querySelectorAll("[data-format]").forEach(function (b) {
    b.addEventListener("mousedown", function (e) { e.preventDefault(); });     // keep the selection
    b.addEventListener("click", function () { format(b.dataset.format); });
  });

  // ----------------------------------------------------- blocks & images
  var SNIPPETS = {
    "code": '"""python Try it yourself\nprint("Hello")\n"""',
    "code-js": '"""javascript Try it yourself\nconsole.log([1, 2, 3].map(n => n * 2));\n"""',
    "ide": '"""python A small project\n--- main.py\nfrom helpers import greet\nprint(greet("Amina"))\n--- helpers.py\ndef greet(name):\n    return "Hello, " + name\n"""',
    "files": '```files "Project"\nmy-project/\n  src/\n    app.py   <- start here\n  README.md\n```',
    "diagram": '```diagram "Try changing the numbers"\nprice: Price = 20\nqty: Quantity = 3\ntotal: Total = price * qty\n```',
    "flow": '```diagram "How it connects"\nclient: Browser\nserver: Server\ndb: Database\nclient -> server : request\nserver -> db : query\n```',
    "note": '```note\nSomething worth remembering.\n```',
    "compare": '```compare "Before" | "After"\nold code\n---\nnew code\n```',
    "table": '| Name | Value |\n|------|-------|\n| a    | 1     |'
  };
  function insertAtCaret(text) {
    var s = src.selectionStart, e = src.selectionEnd, v = src.value, before = v.slice(0, s);
    var pad = before && !before.endsWith("\n\n") ? (before.endsWith("\n") ? "\n" : "\n\n") : "";
    setSource(before + pad + text + "\n\n" + v.slice(e), s + pad.length + text.length + 2);
    refresh();
  }
  function insertAfterBlock(el, text) {
    if (!el) { insertAtEnd(text); return; }
    var r = range(el);
    replaceLines(r[1], r[1], "\n" + text + "\n");
  }
  function insertAtEnd(text) { var v = src.value.replace(/\s*$/, ""); setSource((v ? v + "\n\n" : "") + text + "\n"); refresh(); }
  function insertBlock(text) {
    if (grid.dataset.mode === "preview") insertAfterBlock(previewBlock() || blocks()[blocks().length - 1], text);
    else insertAtCaret(text);
  }
  document.querySelectorAll("[data-snippet]").forEach(function (b) {
    b.addEventListener("click", function () { commitActive(); insertBlock(SNIPPETS[b.dataset.snippet]); });
    b.addEventListener("dragstart", function (e) {
      e.dataTransfer.setData("text/plain", "\n" + SNIPPETS[b.dataset.snippet] + "\n");
      e.dataTransfer.setData("application/x-studiamo-block", b.dataset.snippet);
      e.dataTransfer.effectAllowed = "copy";
    });
  });

  function upload(file) {
    var d = new FormData(); d.append("file", file); d.append("csrf_token", token);
    return fetch(UPLOAD_URL, {method: "POST", body: d, credentials: "same-origin", headers: {"X-CSRF-Token": token}})
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "Upload failed"); return j; }); });
  }
  function images(list) { return Array.prototype.filter.call(list || [], function (f) { return /^image\//.test(f.type); }); }
  function addImages(files, where) {
    files.forEach(function (f) {
      upload(f).then(function (j) { where(j.markdown); }).catch(function (err) { alert(err.message); });
    });
  }

  // textarea: blocks drop as text natively (at the drop point); images upload
  src.addEventListener("drop", function (e) {
    var files = images(e.dataTransfer.files);
    if (files.length) { e.preventDefault(); src.focus(); addImages(files, insertAtCaret); }
    else setTimeout(function () { record(false); schedule(); }, 0);
  });
  src.addEventListener("paste", function (e) {
    var files = images(e.clipboardData && e.clipboardData.files);
    if (files.length) { e.preventDefault(); addImages(files, insertAtCaret); }
  });

  // preview: drop between blocks
  var marker = document.createElement("div"); marker.className = "ed-drop"; marker.setAttribute("aria-hidden", "true");
  function blockAt(y) {
    var list = blocks(), best = null;
    list.forEach(function (el) { var r = el.getBoundingClientRect(); if (r.top + r.height / 2 < y) best = el; });
    return best;                                   // insert after this one (null: at the top)
  }
  out.addEventListener("dragover", function (e) {
    var types = Array.prototype.slice.call(e.dataTransfer.types || []);
    if (types.indexOf("application/x-studiamo-block") < 0 && types.indexOf("Files") < 0) return;
    e.preventDefault();
    var after = blockAt(e.clientY);
    if (after) after.after(marker); else out.insertBefore(marker, out.firstChild);
  });
  out.addEventListener("dragleave", function (e) { if (!out.contains(e.relatedTarget)) marker.remove(); });
  out.addEventListener("drop", function (e) {
    var kind = e.dataTransfer.getData("application/x-studiamo-block"), files = images(e.dataTransfer.files);
    if (!kind && !files.length) return;
    e.preventDefault();
    marker.remove();
    commitActive();
    var after = blockAt(e.clientY);
    function place(text) {
      if (after) insertAfterBlock(after, text);
      else { var b = blocks()[0]; if (b) replaceLines(range(b)[0], range(b)[0], text + "\n"); else insertAtEnd(text); }
    }
    if (kind) place(SNIPPETS[kind]); else addImages(files, place);
  });
  out.addEventListener("paste", function (e) {
    var files = images(e.clipboardData && e.clipboardData.files);
    if (files.length) { e.preventDefault(); var el = previewBlock(); addImages(files, function (md) { insertAfterBlock(el, md); }); return; }
    // paste plain text only (no foreign HTML in the note)
    if (e.target.closest && e.target.closest(".ed-text")) {
      e.preventDefault();
      document.execCommand("insertText", false, (e.clipboardData || window.clipboardData).getData("text/plain"));
    }
  });

  // ---------------------------------------------------------------- tab key / save
  src.addEventListener("keydown", function (e) {
    if (e.key === "Tab" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && src.dataset.esc !== "1") {
      e.preventDefault();
      var s = src.selectionStart; src.value = src.value.slice(0, s) + "    " + src.value.slice(src.selectionEnd);
      src.selectionStart = src.selectionEnd = s + 4; record(false); schedule();
    } else if (e.key === "Escape") src.dataset.esc = "1"; else src.dataset.esc = "";
  });
  form.addEventListener("submit", function () { commitActive(); dirty = false; });
  window.addEventListener("beforeunload", function (e) { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  // ---------------------------------------------------- diagram positions
  // Dragging a box in the preview writes "@ x, y" on its line, so the note keeps
  // the layout (readers see it, scaled to their screen). Undo with Ctrl+Z.
  var ID_LINE = /^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)(?!\s*:)/;
  function withPosition(line, x, y) {
    var cut = line.indexOf("//"), code = cut >= 0 ? line.slice(0, cut) : line, note = cut >= 0 ? line.slice(cut) : "";
    code = code.replace(/\s*@\s*-?\d+\s*,\s*-?\d+\s*$/, "").replace(/\s+$/, "");
    return code + " @ " + x + ", " + y + (note ? "  " + note : "");
  }
  out.addEventListener("smd-diagram-moved", function (e) {
    var block = e.target.closest("[data-src]");
    if (!block) return;
    var r = range(block), lines = src.value.split("\n").slice(r[0], r[1]);
    if (lines.length < 2) return;
    var pos = e.detail.positions, seen = {};
    var body = lines.slice(1, -1).map(function (line) {
      var m = line.match(ID_LINE);
      if (!m || line.indexOf("->") >= 0 || !pos[m[2]]) return line;
      seen[m[2]] = true;
      return withPosition(line, pos[m[2]][0], pos[m[2]][1]);
    });
    // boxes that only appear in arrows get their own line, so they keep a place too
    Object.keys(pos).forEach(function (id) { if (!seen[id]) body.push(id + ": " + id + " @ " + pos[id][0] + ", " + pos[id][1]); });
    var updated = [lines[0]].concat(body, [lines[lines.length - 1]]).join("\n");
    if (updated !== lines.join("\n")) replaceLines(r[0], r[1], updated, {el: block, quiet: true});
  });

  // ------------------------------------------------------------- import
  var dlg = document.getElementById("import-dialog");
  if (dlg) {
    var box = document.getElementById("import-form"), status = document.getElementById("import-status");
    var go = document.getElementById("import-go");
    document.getElementById("import-open").addEventListener("click", function () { commitActive(); status.textContent = ""; dlg.showModal(); });
    document.getElementById("import-cancel").addEventListener("click", function () { dlg.close(); });
    go.addEventListener("click", function () {
      var file = document.getElementById("import-file").files[0], text = document.getElementById("import-text").value;
      var ai = document.getElementById("import-ai");
      if (!file && !text.trim()) { status.textContent = "Choose a file or paste some text."; return; }
      var d = new FormData();
      if (file) d.append("file", file); else d.append("text", text);
      if (ai && ai.checked) d.append("use_ai", "1");
      d.append("title", (document.getElementById("f-title") || {}).value || "");
      d.append("csrf_token", token);
      go.disabled = true;
      status.textContent = ai && ai.checked ? "Converting with Claude… this can take a minute or two." : "Importing…";
      fetch(box.dataset.url, {method: "POST", body: d, credentials: "same-origin"})
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "Import failed"); return j; }); })
        .then(function (j) {
          var where = (box.querySelector('input[name="import-where"]:checked') || {}).value;
          var v = src.value.replace(/\s*$/, "");
          setSource(where === "append" && v ? v + "\n\n" + j.markdown : j.markdown);
          refresh();
          dlg.close();
          var msg = document.createElement("p");
          msg.className = "flash"; msg.setAttribute("role", "status");
          msg.textContent = (j.used_ai ? "Converted by " + j.model + "." : "Imported without AI.") +
            (j.problems.length ? " Check the " + j.problems.length + " problem(s) shown above the preview." : "") + " Ctrl+Z undoes it. Save when it looks right.";
          form.insertBefore(msg, form.firstChild);
        })
        .catch(function (e) { status.textContent = e.message; })
        .then(function () { go.disabled = false; });
    });
    if (form.dataset.openImport === "1") dlg.showModal();
  }

  decorate();
})();
