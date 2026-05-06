// Pratyaya — shared theme + nav controller. Loaded on every page.
(function () {
  const KEY = "pratyaya:theme";

  function applyTheme(t) {
    document.documentElement.dataset.theme = t;
    const btn = document.getElementById("theme-icon");
    if (btn) btn.textContent = t === "light" ? "☀️" : "🌙";
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      "content", t === "light" ? "#f3f6fb" : "#0c1a30"
    );
  }
  function getTheme() {
    try { return localStorage.getItem(KEY) || "dark"; }
    catch { return "dark"; }
  }
  function setTheme(t) {
    try { localStorage.setItem(KEY, t); } catch {}
    applyTheme(t);
  }
  function toggleTheme() {
    setTheme(getTheme() === "dark" ? "light" : "dark");
  }

  applyTheme(getTheme());

  document.addEventListener("DOMContentLoaded", () => {
    const tBtn = document.getElementById("theme-toggle");
    if (tBtn) tBtn.addEventListener("click", toggleTheme);

    // Mobile menu
    const mt = document.getElementById("menu-toggle");
    const nav = document.getElementById("nav");
    if (mt && nav) {
      mt.addEventListener("click", () => {
        const open = nav.classList.toggle("open");
        mt.setAttribute("aria-expanded", open ? "true" : "false");
      });
      document.addEventListener("click", (e) => {
        if (!nav.contains(e.target) && !mt.contains(e.target)) nav.classList.remove("open");
      });
    }

    // Global Shift+T shortcut
    document.addEventListener("keydown", (e) => {
      if (e.shiftKey && (e.key === "T" || e.key === "t") && !isTyping(e.target)) {
        e.preventDefault();
        toggleTheme();
      }
    });
  });

  function isTyping(el) {
    if (!el) return false;
    const tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || el.isContentEditable;
  }

  window.PratyayaTheme = { get: getTheme, set: setTheme, toggle: toggleTheme };
})();
