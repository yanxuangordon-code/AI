// ---- Config ----
// Replace this with your Render backend URL after deploying
const BACKEND_URL = "https://your-backend.onrender.com";

// ---- Monaco Editor Setup ----
require.config({ paths: { vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs" } });

let editor;

require(["vs/editor/editor.main"], function () {
  editor = monaco.editor.create(document.getElementById("editor"), {
    value: getDefaultCode("python"),
    language: "python",
    theme: "vs-dark",
    fontSize: 15,
    minimap: { enabled: false },
    automaticLayout: true,
  });
});

// ---- Language Switcher ----
document.getElementById("language").addEventListener("change", function () {
  const lang = this.value;
  const monacoLang = lang === "cpp" ? "cpp" : lang === "html" ? "html" : lang;
  monaco.editor.setModelLanguage(editor.getModel(), monacoLang);
  editor.setValue(getDefaultCode(lang));

  // Show/hide HTML preview
  document.getElementById("previewContainer").style.display = lang === "html" ? "block" : "none";
  document.getElementById("output").style.display = lang === "html" ? "none" : "block";
});

// ---- Default Code Snippets ----
function getDefaultCode(lang) {
  const defaults = {
    python: '# Python\nprint("Hello, World!")',
    javascript: '// JavaScript\nconsole.log("Hello, World!");',
    cpp: '#include <iostream>\nusing namespace std;\n\nint main() {\n    cout << "Hello, World!" << endl;\n    return 0;\n}',
    html: '<!DOCTYPE html>\n<html>\n<body>\n  <h1>Hello, World!</h1>\n</body>\n</html>',
  };
  return defaults[lang] || "";
}

// ---- Run Code ----
document.getElementById("runBtn").addEventListener("click", async function () {
  const language = document.getElementById("language").value;
  const code = editor.getValue();
  const outputEl = document.getElementById("output");

  if (language === "html") {
    // Render HTML in iframe
    const iframe = document.getElementById("htmlPreview");
    iframe.srcdoc = code;
    return;
  }

  outputEl.textContent = "Running...";
  outputEl.style.color = "#cdd6f4";

  try {
    const res = await fetch(`${BACKEND_URL}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language, code }),
    });

    const data = await res.json();

    if (data.error && data.error.trim()) {
      outputEl.textContent = data.error;
      outputEl.style.color = "#f38ba8";
    } else {
      outputEl.textContent = data.output || "(no output)";
      outputEl.style.color = "#a6e3a1";
    }
  } catch (err) {
    outputEl.textContent = "Could not connect to backend. Is it running?";
    outputEl.style.color = "#f38ba8";
  }
});

// ---- Save & Share ----
document.getElementById("saveBtn").addEventListener("click", async function () {
  const language = document.getElementById("language").value;
  const code = editor.getValue();

  try {
    const res = await fetch(`${BACKEND_URL}/snippets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language, code }),
    });

    const data = await res.json();
    const shareUrl = `${window.location.origin}${window.location.pathname}?snippet=${data.id}`;

    document.getElementById("shareLink").value = shareUrl;
    document.getElementById("shareBox").style.display = "flex";
  } catch (err) {
    alert("Could not save snippet. Is the backend running?");
  }
});

// ---- Copy Share Link ----
function copyLink() {
  const input = document.getElementById("shareLink");
  input.select();
  document.execCommand("copy");
  alert("Link copied!");
}

// ---- Clear ----
document.getElementById("clearBtn").addEventListener("click", function () {
  editor.setValue("");
  document.getElementById("output").textContent = "Run your code to see output here...";
  document.getElementById("output").style.color = "#a6e3a1";
  document.getElementById("shareBox").style.display = "none";
});

// ---- Load Snippet from URL ----
window.addEventListener("load", async function () {
  const params = new URLSearchParams(window.location.search);
  const snippetId = params.get("snippet");

  if (snippetId) {
    try {
      const res = await fetch(`${BACKEND_URL}/snippets/${snippetId}`);
      const data = await res.json();
      if (data.language && data.code) {
        document.getElementById("language").value = data.language;
        document.getElementById("language").dispatchEvent(new Event("change"));
        // Wait for editor to be ready
        setTimeout(() => editor.setValue(data.code), 500);
      }
    } catch (err) {
      console.error("Could not load snippet");
    }
  }
});
