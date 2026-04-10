// monaco-diagnostics.js
// Drop this file in your repo root and include it in editor.html and multifile.html
// AFTER Monaco is loaded. Provides:
//   - Red underlines: syntax/runtime errors (misspelled keywords, unmatched brackets, etc.)
//   - Yellow underlines: warnings (unused vars, missing semicolons, etc.)
//   - Right-click context menu actions: "Fix", "Explain error", "Ignore"

(function() {
  if (typeof monaco === "undefined") return;

  // ── Enable built-in Monaco diagnostics ────────────────────────────────────
  // JavaScript / TypeScript — enable all checks
  monaco.languages.typescript.javascriptDefaults.setDiagnosticsOptions({
    noSemanticValidation: false,
    noSyntaxValidation:   false,
    noSuggestionDiagnostics: false,
  });
  monaco.languages.typescript.javascriptDefaults.setCompilerOptions({
    target:  monaco.languages.typescript.ScriptTarget.ES2020,
    allowJs: true,
    checkJs: true,
    strict:  false,
    noUnusedLocals:     true,
    noUnusedParameters: false,
  });

  // HTML — enable validation
  monaco.languages.html.htmlDefaults.setOptions({
    validate: true,
    suggest:  { html5: true },
  });

  // CSS — enable validation
  monaco.languages.css.cssDefaults.setOptions({ validate: true });
  monaco.languages.css.lessDefaults.setOptions({ validate: true });

  // JSON — enable schema validation
  monaco.languages.json.jsonDefaults.setDiagnosticsOptions({
    validate: true,
    allowComments: false,
  });

  // ── Python / C++ custom linter ────────────────────────────────────────────
  // These languages don't have full Monaco language servers, so we add
  // lightweight pattern-based markers for common mistakes.

  var PYTHON_RULES = [
    // Errors (red)
    { re: /\bpirnt\s*\(/g,      msg: 'Did you mean "print"?',           sev: "error"   },
    { re: /\bprnit\s*\(/g,      msg: 'Did you mean "print"?',           sev: "error"   },
    { re: /\bimoprt\b/g,        msg: 'Did you mean "import"?',          sev: "error"   },
    { re: /\bimprot\b/g,        msg: 'Did you mean "import"?',          sev: "error"   },
    { re: /\bdeef\s+/g,         msg: 'Did you mean "def"?',             sev: "error"   },
    { re: /\bclsas\s+/g,        msg: 'Did you mean "class"?',           sev: "error"   },
    { re: /\bretunr\b/g,        msg: 'Did you mean "return"?',          sev: "error"   },
    { re: /\bTure\b/g,          msg: 'Did you mean "True"?',            sev: "error"   },
    { re: /\bFlase\b/g,         msg: 'Did you mean "False"?',           sev: "error"   },
    { re: /\bNoen\b/g,          msg: 'Did you mean "None"?',            sev: "error"   },
    { re: /\bexcpet\b/g,        msg: 'Did you mean "except"?',          sev: "error"   },
    { re: /\bexcept\s*:/g,      msg: 'Bare "except:" catches all — consider "except Exception as e:"', sev: "warning" },
    // Warnings (yellow)
    { re: /^\s*#\s*TODO/gm,     msg: 'TODO: remember to implement this', sev: "warning" },
    { re: /^\s*#\s*FIXME/gm,    msg: 'FIXME: this needs fixing',         sev: "warning" },
    { re: /\beval\s*\(/g,       msg: 'Avoid eval() — it can be unsafe',  sev: "warning" },
    { re: /\bexec\s*\(/g,       msg: 'Avoid exec() — it can be unsafe',  sev: "warning" },
    { re: /\bfrom\s+\S+\s+import\s+\*/g, msg: 'Wildcard import — prefer explicit imports', sev: "warning" },
  ];

  var CPP_RULES = [
    // Errors (red)
    { re: /\bcuot\s*<</g,       msg: 'Did you mean "cout"?',            sev: "error"   },
    { re: /\bcin\s*>>/g,        msg: 'Use cin >> carefully — no overflow protection', sev: "warning" },
    { re: /\binclude\s*</g,     msg: 'Missing # before include',        sev: "error",  checkFn: function(line) { return !/^\s*#/.test(line); } },
    { re: /\bmian\s*\(/g,       msg: 'Did you mean "main"?',            sev: "error"   },
    { re: /\bvoid\s+mian/g,     msg: 'Did you mean "main"?',            sev: "error"   },
    { re: /\busing\s+namepsace/g,msg:'Did you mean "namespace"?',       sev: "error"   },
    // Warnings
    { re: /\bgoto\b/g,          msg: 'Avoid "goto" — use loops or functions', sev: "warning" },
    { re: /\/\/\s*TODO/g,       msg: 'TODO: remember to implement this', sev: "warning" },
    { re: /\/\/\s*FIXME/g,      msg: 'FIXME: this needs fixing',         sev: "warning" },
    { re: /\bsystem\s*\(\s*["']/g, msg: 'Avoid system() calls — security risk', sev: "warning" },
  ];

  var SEV_MAP = {
    error:   monaco.MarkerSeverity.Error,
    warning: monaco.MarkerSeverity.Warning,
    info:    monaco.MarkerSeverity.Info,
  };

  function lintModel(model) {
    var lang = model.getLanguageId();
    var rules = lang === "python" ? PYTHON_RULES : lang === "cpp" ? CPP_RULES : null;
    if (!rules) return; // JS/HTML/CSS/JSON handled natively by Monaco

    var text    = model.getValue();
    var lines   = text.split("\n");
    var markers = [];

    rules.forEach(function(rule) {
      // Reset regex state
      rule.re.lastIndex = 0;
      var match;
      while ((match = rule.re.exec(text)) !== null) {
        // Find line/col from offset
        var offset = match.index;
        var lineIdx = 0, col = offset;
        for (var i = 0; i < lines.length; i++) {
          if (col <= lines[i].length) { lineIdx = i; break; }
          col -= lines[i].length + 1;
        }

        // Optional per-match check
        if (rule.checkFn && !rule.checkFn(lines[lineIdx])) continue;

        markers.push({
          severity:        SEV_MAP[rule.sev] || monaco.MarkerSeverity.Warning,
          message:         rule.msg,
          startLineNumber: lineIdx + 1,
          startColumn:     col + 1,
          endLineNumber:   lineIdx + 1,
          endColumn:       col + match[0].length + 1,
          source:          "CodeSponge Linter",
        });

        // Prevent infinite loop on zero-width matches
        if (match[0].length === 0) rule.re.lastIndex++;
      }
      rule.re.lastIndex = 0;
    });

    monaco.editor.setModelMarkers(model, "codesponge-linter", markers);
  }

  // ── Attach linter to all editors ──────────────────────────────────────────
  var lintTimers = new WeakMap();

  function attachLinter(editor) {
    // Lint on content change (debounced 600ms)
    editor.onDidChangeModelContent(function() {
      var model = editor.getModel();
      if (!model) return;
      var t = lintTimers.get(model);
      clearTimeout(t);
      lintTimers.set(model, setTimeout(function() { lintModel(model); }, 600));
    });

    // Lint on model change (file switch)
    editor.onDidChangeModel(function() {
      var model = editor.getModel();
      if (model) setTimeout(function() { lintModel(model); }, 300);
    });

    // Lint immediately on attach
    var model = editor.getModel();
    if (model) lintModel(model);
  }

  // ── Right-click context actions ───────────────────────────────────────────
  function registerContextActions(editor) {

    // "Explain this error" — shows the marker message in a notification
    editor.addAction({
      id:    "codesponge.explainError",
      label: "Explain this error",
      contextMenuGroupId: "codesponge",
      contextMenuOrder:   1,
      run: function(ed) {
        var pos     = ed.getPosition();
        var model   = ed.getModel();
        var markers = monaco.editor.getModelMarkers({ resource: model.uri })
          .filter(function(m) {
            return m.startLineNumber <= pos.lineNumber &&
                   m.endLineNumber   >= pos.lineNumber;
          });
        if (markers.length === 0) {
          showNotification("No errors or warnings on this line.", "info");
        } else {
          var msgs = markers.map(function(m) {
            return (m.severity === monaco.MarkerSeverity.Error ? "Error" : "Warning") + ": " + m.message;
          }).join("\n");
          showNotification(msgs, markers[0].severity === monaco.MarkerSeverity.Error ? "error" : "warning");
        }
      }
    });

    // "Quick fix — replace with suggestion"
    editor.addAction({
      id:    "codesponge.quickFix",
      label: "Quick fix",
      contextMenuGroupId: "codesponge",
      contextMenuOrder:   0,
      run: function(ed) {
        var pos     = ed.getPosition();
        var model   = ed.getModel();
        var markers = monaco.editor.getModelMarkers({ resource: model.uri })
          .filter(function(m) {
            return m.startLineNumber <= pos.lineNumber &&
                   m.endLineNumber   >= pos.lineNumber &&
                   m.severity === monaco.MarkerSeverity.Error;
          });

        if (markers.length === 0) { showNotification("No quick fix available here.", "info"); return; }

        var m    = markers[0];
        var fix  = extractFix(m.message);
        if (!fix) { showNotification("No automatic fix available. " + m.message, "warning"); return; }

        ed.executeEdits("codesponge-fix", [{
          range: new monaco.Range(m.startLineNumber, m.startColumn, m.endLineNumber, m.endColumn),
          text:  fix,
        }]);
        showNotification("Fixed: replaced with \"" + fix + "\"", "success");
      }
    });

    // "Ignore this warning"
    editor.addAction({
      id:    "codesponge.ignoreWarning",
      label: "Ignore this warning",
      contextMenuGroupId: "codesponge",
      contextMenuOrder:   2,
      run: function(ed) {
        var pos     = ed.getPosition();
        var model   = ed.getModel();
        var markers = monaco.editor.getModelMarkers({ resource: model.uri });
        // Remove warning markers on this line
        var filtered = markers.filter(function(m) {
          return !(m.startLineNumber === pos.lineNumber && m.severity === monaco.MarkerSeverity.Warning);
        });
        monaco.editor.setModelMarkers(model, "codesponge-linter", filtered);
        showNotification("Warning ignored.", "info");
      }
    });
  }

  // Extract suggested fix from error message like 'Did you mean "print"?'
  function extractFix(msg) {
    var m = msg.match(/Did you mean "([^"]+)"\?/);
    return m ? m[1] : null;
  }

  // ── Notification toast ────────────────────────────────────────────────────
  function showNotification(msg, type) {
    var existing = document.getElementById("cs-notification");
    if (existing) existing.remove();

    var colors = {
      error:   { bg:"rgba(248,81,73,.15)",  border:"rgba(248,81,73,.4)",  color:"#f85149" },
      warning: { bg:"rgba(227,179,65,.15)", border:"rgba(227,179,65,.4)", color:"#e3b341" },
      success: { bg:"rgba(63,185,80,.15)",  border:"rgba(63,185,80,.4)",  color:"#3fb950" },
      info:    { bg:"rgba(79,142,247,.15)", border:"rgba(79,142,247,.4)", color:"#4f8ef7" },
    };
    var c = colors[type] || colors.info;

    var el = document.createElement("div");
    el.id = "cs-notification";
    el.style.cssText = [
      "position:fixed","bottom:40px","right:20px","z-index:9999",
      "background:" + c.bg,"border:1px solid " + c.border,"color:" + c.color,
      "border-radius:9px","padding:10px 16px","font-size:12px","font-family:'Inter',sans-serif",
      "max-width:340px","line-height:1.5","white-space:pre-wrap",
      "box-shadow:0 8px 24px rgba(0,0,0,.4)",
      "animation:cs-slide-in .2s ease",
    ].join(";");
    el.textContent = msg;
    document.body.appendChild(el);

    // Auto-dismiss after 4s
    setTimeout(function() {
      el.style.opacity = "0";
      el.style.transition = "opacity .3s";
      setTimeout(function() { if (el.parentNode) el.remove(); }, 300);
    }, 4000);
  }

  // Inject slide-in animation
  var style = document.createElement("style");
  style.textContent = "@keyframes cs-slide-in{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}";
  document.head.appendChild(style);

  // ── Public API — call this after creating your editor ────────────────────
  window.csInitDiagnostics = function(editor) {
    attachLinter(editor);
    registerContextActions(editor);
  };

  // Also expose lintModel for external calls (e.g. after file switch)
  window.csLintModel = lintModel;

})();
