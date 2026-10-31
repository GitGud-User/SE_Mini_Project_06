// Shared helpers for every SPMS page: API calls, login token, header.
const SPMS = {
  // SR-004: escape anything that came from the server or a user before putting it into HTML.
  esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },
  token() { try { return localStorage.getItem("spms_token"); } catch { return null; } },
  user() { try { return JSON.parse(localStorage.getItem("spms_user") || "null"); } catch { return null; } },
  saveLogin(data) {
    try {
      localStorage.setItem("spms_token", data.access_token);
      localStorage.setItem("spms_user", JSON.stringify({ name: data.name, role: data.role }));
    } catch { /* storage blocked: login lasts for this page only */ }
  },
  logout() {
    try { localStorage.removeItem("spms_token"); localStorage.removeItem("spms_user"); } catch {}
    location.href = "login.html";
  },
  async api(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    const t = SPMS.token();
    if (t) headers.Authorization = "Bearer " + t;
    const res = await fetch(path, { ...options, headers });
    let body = null;
    try { body = await res.json(); } catch {}
    if (!res.ok) {
      let detail = body && body.detail;
      if (Array.isArray(detail)) detail = detail.map(d => d.msg).join("; ");
      const error = new Error(detail || ("Request failed (" + res.status + ")"));
      error.status = res.status;
      throw error;
    }
    return body;
  },
  // Download a file from an authenticated endpoint (e.g. a receipt PDF).
  async download(path, filename) {
    const res = await fetch(path, { headers: { Authorization: "Bearer " + SPMS.token() } });
    if (!res.ok) throw new Error("Download failed (" + res.status + ")");
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: filename });
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  },
  show(el, text, ok) { el.textContent = text; el.className = "msg " + (ok ? "ok" : "err"); },
  requireRole(roles) {
    const u = SPMS.user();
    if (!u || !roles.includes(u.role)) { location.href = "login.html?next=" + encodeURIComponent(location.pathname); return false; }
    return true;
  },
  header(active) {
    const u = SPMS.user();
    const links = [["index.html", "Availability"]];
    if (u && (u.role === "ATTENDANT" || u.role === "ADMIN")) links.push(["gate.html", "Gate"]);
    if (u && u.role === "ADMIN") links.push(["admin.html", "Admin"]);
    links.push(["/docs", "API docs"]);
    const nav = links.map(([h, t]) => `<a href="${h}" class="${h === active ? "active" : ""}">${t}</a>`).join("");
    const who = u ? `${SPMS.esc(u.name)} (${SPMS.esc(u.role)}) · <a href="#" id="logout">Log out</a>` : `<a href="login.html">Log in</a>`;
    document.querySelector("header").innerHTML = `<span class="brand">SPMS</span><nav>${nav}</nav><span id="who">${who}</span>`;
    const lo = document.getElementById("logout");
    if (lo) lo.onclick = (e) => { e.preventDefault(); SPMS.logout(); };
  },
  async fillLots(select) {
    const lots = await SPMS.api("/api/v1/lots");
    select.innerHTML = lots.map(l => `<option value="${Number(l.id)}">${SPMS.esc(l.name)}</option>`).join("");
    return lots;
  },
};
