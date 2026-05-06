// Pratyaya — shared sidebar nav. Inject into any page that has <div id="shell-mount">.
// Highlights the current page automatically based on data-page attribute on body.

(function () {
  function buildSidebar(active) {
    const items = [
      { group: "" },
      { href: "/", icon: "🏠", label: "Overview", id: "overview" },
      { href: "/agent", icon: "🎧", label: "Agent dashboard", id: "agent" },
      { href: "/analytics", icon: "📈", label: "Civic sensor", id: "analytics" },

      { group: "Demo" },
      { href: "/citizen", icon: "📞", label: "Citizen call", id: "citizen", target: "_blank" },

      { group: "Tools" },
      { href: "/api/audit/verify", icon: "🛡️", label: "Verify audit chain", id: "audit", target: "_blank" },
      { href: "/api/analytics", icon: "📊", label: "Analytics API (JSON)", id: "api-analytics", target: "_blank" },
      { href: "/docs", icon: "📚", label: "API docs", id: "docs", target: "_blank" },
    ];

    const navHtml = items.map(it => {
      if (it.group !== undefined) {
        return it.group ? `<div class="group-title">${escape(it.group)}</div>` : "";
      }
      const isActive = it.id === active;
      const tgt = it.target ? `target="${it.target}"` : "";
      return `<a href="${it.href}" ${tgt} class="${isActive ? "active" : ""}">
        <span class="icon" aria-hidden="true">${it.icon}</span>
        <span>${escape(it.label)}</span>
      </a>`;
    }).join("");

    return `
      <aside class="sidebar" id="sidebar" aria-label="Main navigation">
        <div class="brand">
          <div style="display:flex; align-items:center; gap:10px;">
            <div class="brand-mark" aria-hidden="true">प्र</div>
            <div>
              <div class="name" style="font-weight:700; font-size:15px;">Pratyaya</div>
              <div class="sub" style="color:var(--muted); font-size:11px;">1092 helpline</div>
            </div>
          </div>
        </div>
        <nav role="navigation">${navHtml}</nav>
        <div class="footer">
          <span class="live-pill" id="sb-live"><span class="dot red"></span>—</span>
          <button id="theme-toggle" class="btn" aria-label="Toggle theme" style="justify-content:center;">
            <span id="theme-icon">🌙</span>&nbsp;<span>Toggle theme</span>
          </button>
        </div>
      </aside>
    `;
  }

  function escape(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function buildHeader(title, crumb) {
    return `
      <header class="shell-header" role="banner">
        <div style="display:flex; align-items:center; gap:10px;">
          <button class="sidebar-burger" id="sidebar-burger" aria-label="Toggle navigation">☰</button>
          <div class="page-title">
            <h1>${escape(title)}</h1>
            <div class="crumb" id="page-crumb">${escape(crumb || "")}</div>
          </div>
        </div>
        <div class="header-tools" id="header-tools"></div>
      </header>
    `;
  }

  function init() {
    const mount = document.getElementById("shell-mount");
    if (!mount) return;
    const active = document.body.dataset.page || "";
    const title = mount.dataset.title || "Pratyaya";
    const crumb = mount.dataset.crumb || "";

    // Move existing children of mount into a content wrapper.
    const existingNodes = [...mount.childNodes];
    mount.innerHTML = "";
    mount.classList.add("shell");
    mount.insertAdjacentHTML("beforeend", buildSidebar(active));
    mount.insertAdjacentHTML("beforeend", '<div class="shell-main" id="shell-main"></div>');
    const main = mount.querySelector("#shell-main");
    main.insertAdjacentHTML("beforeend", buildHeader(title, crumb));
    const content = document.createElement("div");
    content.className = "shell-content" + (mount.dataset.fluid === "true" ? " fluid" : "");
    existingNodes.forEach(n => content.appendChild(n));
    main.appendChild(content);

    // backdrop for mobile
    const backdrop = document.createElement("div");
    backdrop.className = "sidebar-backdrop";
    backdrop.id = "sidebar-backdrop";
    document.body.appendChild(backdrop);

    const sb = document.getElementById("sidebar");
    const burger = document.getElementById("sidebar-burger");
    burger.addEventListener("click", () => {
      sb.classList.toggle("open");
      backdrop.classList.toggle("show", sb.classList.contains("open"));
    });
    backdrop.addEventListener("click", () => {
      sb.classList.remove("open");
      backdrop.classList.remove("show");
    });

    // live count poll
    pollLive();
    setInterval(pollLive, 5000);
  }

  async function pollLive() {
    try {
      const r = await fetch("/api/calls?limit=1");
      if (!r.ok) return;
      const d = await r.json();
      const el = document.getElementById("sb-live");
      if (el) el.innerHTML = `<span class="dot red"></span>${d.live_count} live`;
    } catch {}
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
