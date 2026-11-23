/* PocketBolt front end - vanilla JS single-page app (TEST MODE). */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const app = $("#app");
  const S = { user: null, config: null, sources: [], requests: [], activity: [], pending: 0,
              tab: "home", pay: { amount: "0", to: "", note: "", source: "wallet" }, lastSeenTx: null,
              installEvt: null };

  // ---------------------------------------------------------------- utils
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (c) => "$" + (c / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const initials = (n) => esc((n || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase());
  const when = (ts) => {
    const d = new Date(ts.replace(" ", "T") + "Z"), s = (Date.now() - d) / 1000;
    if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };
  const hue = (s) => [...(s || "")].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
  const avatar = (name, user) => `<div class="avatar" style="background:hsl(${hue(user || name)} 45% 28%)">${initials(name)}</div>`;
  const buzz = (p) => { try { navigator.vibrate && navigator.vibrate(p); } catch (_) {} };
  const payLink = (u) => `${location.origin}/pay/${encodeURIComponent(u)}`;

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      method: opts.method || (opts.body ? "POST" : "GET"),
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      credentials: "same-origin",
    });
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok || data.ok === false) {
      const e = new Error(data.error || `Something went wrong (${res.status}).`);
      e.status = res.status; throw e;
    }
    return data;
  }

  let toastTimer;
  function toast(msg, bad) {
    const t = $("#toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : "");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
  }

  function openSheet(html, onMount) {
    const sh = $("#sheet");
    sh.innerHTML = `<div class="grab"></div>` + html;
    sh.classList.remove("hidden"); $("#sheet-backdrop").classList.remove("hidden");
    onMount && onMount(sh);
    const f = sh.querySelector("input,select"); f && setTimeout(() => f.focus(), 150);
  }
  function closeSheet() { $("#sheet").classList.add("hidden"); $("#sheet-backdrop").classList.add("hidden"); $("#sheet").innerHTML = ""; }
  $("#sheet-backdrop").addEventListener("click", closeSheet);

  async function withBusy(btn, fn) {
    const old = btn.textContent; btn.disabled = true; btn.textContent = "…";
    try { return await fn(); } finally { btn.disabled = false; btn.textContent = old; }
  }

  const sourceOptions = (includeWallet, selected) =>
    (includeWallet ? `<option value="wallet">PocketBolt balance (${money(S.user.balance_cents)})</option>` : "") +
    S.sources.map((s) => `<option value="${s.id}" ${String(selected) === String(s.id) ? "selected" : ""}>
      ${s.kind === "bank" ? "🏦" : "💳"} ${esc(s.label)} ••${esc(s.last4)}${s.provider !== "simulated" ? " · " + esc(s.provider) : ""}</option>`).join("");

  // ---------------------------------------------------------------- data
  async function refresh(quiet) {
    try {
      const [me, src, req, act] = await Promise.all([api("/api/me"), api("/api/funding_sources"),
        api("/api/requests"), api("/api/activity")]);
      const prevTop = S.lastSeenTx;
      S.user = me.user; S.pending = me.pending_requests; S.sources = src.sources;
      S.requests = req.requests; S.activity = act.items;
      const top = S.activity[0];
      if (quiet && top && prevTop !== null && top.id !== prevTop && top.direction === "in" && top.type === "transfer") {
        notify(`${top.title.replace("From ", "")} sent you ${top.amount}`, top.note);
      }
      S.lastSeenTx = top ? top.id : 0;
      const b = $("#badge"); b.textContent = S.pending; b.classList.toggle("hidden", !S.pending);
      return true;
    } catch (e) {
      if (e.status === 401) { S.user = null; renderAuth(); }
      return false;
    }
  }

  function notify(title, body) {
    toast("⚡ " + title); buzz([30, 40, 30]);
    if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
      navigator.serviceWorker?.getRegistration().then((r) =>
        r ? r.showNotification(title, { body: body || "", icon: "/static/icons/icon-192.png" }) : new Notification(title, { body }));
    }
  }

  // ---------------------------------------------------------------- auth
  function renderAuth(mode = "login") {
    $("#tabs").classList.add("hidden");
    app.innerHTML = `
      <div class="auth">
        <div class="brand" style="font-size:30px;justify-content:center;margin-top:10px"><div class="logo">⚡</div>PocketBolt</div>
        <p class="muted" style="text-align:center">Send, request and receive money in a flash.</p>
        <div class="seg"><button data-m="login" class="${mode === "login" ? "on" : ""}">Log in</button>
          <button data-m="signup" class="${mode === "signup" ? "on" : ""}">Sign up</button></div>
        <form id="authf" class="card" autocomplete="on">
          ${mode === "signup" ? `
            <label>Full name</label><input name="display_name" autocomplete="name" required>
            <label>Pick your $handle</label><input name="username" autocomplete="username" placeholder="e.g. denzel" required>
            <label>Email</label><input name="email" type="email" autocomplete="email" required>
            <label>Phone (optional)</label><input name="phone" type="tel" autocomplete="tel" placeholder="555 010 0000">
            <label>Password</label><input name="password" type="password" autocomplete="new-password" minlength="8" required>`
          : `<label>$handle or email</label><input name="login" autocomplete="username" required>
            <label>Password</label><input name="password" type="password" autocomplete="current-password" required>`}
          <div class="err" id="autherr"></div>
          <button class="btn primary block" type="submit">${mode === "signup" ? "Create account" : "Log in"}</button>
          ${mode === "login" ? `<div class="hint">Demo accounts: <b>denzel</b>, <b>maya</b>, <b>jordan</b> · password <b>demo1234</b></div>` : ""}
        </form>
      </div>`;
    app.querySelectorAll(".seg button").forEach((b) => b.onclick = () => renderAuth(b.dataset.m));
    $("#authf").onsubmit = async (ev) => {
      ev.preventDefault();
      const data = Object.fromEntries(new FormData(ev.target));
      const btn = ev.target.querySelector("button[type=submit]");
      try {
        await withBusy(btn, () => api(mode === "signup" ? "/api/signup" : "/api/login", { body: data }));
        await boot();
      } catch (e) { $("#autherr").textContent = e.message; }
    };
  }

  // ---------------------------------------------------------------- views
  function activityItem(i) {
    const icon = i.type === "deposit" ? `<div class="avatar">⬇️</div>` : i.type === "withdrawal" ? `<div class="avatar">🏦</div>`
      : avatar(i.title.replace(/^(To|From) /, ""), i.subtitle);
    return `<div class="item">${icon}<div class="grow"><div class="t">${esc(i.title)}</div>
      <div class="s">${esc(i.note || i.subtitle)} · ${when(i.created_at)}</div></div>
      <div class="${i.direction === "in" ? "amt-in" : "amt-out"}">${i.direction === "in" ? "+" : "−"}${esc(i.amount)}</div></div>`;
  }

  function requestItem(r) {
    const other = r.incoming ? r.requester : r.payer;
    return `<div class="item">${avatar(other.display_name, other.username)}
      <div class="grow"><div class="t">${r.incoming ? esc(other.display_name) + " requests" : "You requested from " + esc(other.display_name)}</div>
      <div class="s">${esc(r.note || "$" + other.username)} · ${when(r.created_at)}</div>
      <div class="row" style="margin-top:8px">${r.incoming
        ? `<button class="btn primary sm" data-pay="${r.id}">Pay ${esc(r.amount)}</button><button class="btn ghost sm" data-decline="${r.id}">Decline</button>`
        : `<span class="chip">${esc(r.amount)} pending</span><button class="btn ghost sm" data-cancel="${r.id}">Cancel</button>`}</div>
      </div></div>`;
  }

  function bindRequestButtons(root) {
    root.querySelectorAll("[data-pay]").forEach((b) => b.onclick = () => payRequestSheet(S.requests.find((r) => r.id == b.dataset.pay)));
    root.querySelectorAll("[data-decline]").forEach((b) => b.onclick = () => withBusy(b, async () => {
      try { await api(`/api/requests/${b.dataset.decline}/decline`, { body: {} }); toast("Request declined"); await refresh(); render(); }
      catch (e) { toast(e.message, true); } }));
    root.querySelectorAll("[data-cancel]").forEach((b) => b.onclick = () => withBusy(b, async () => {
      try { await api(`/api/requests/${b.dataset.cancel}/cancel`, { body: {} }); toast("Request cancelled"); await refresh(); render(); }
      catch (e) { toast(e.message, true); } }));
  }

  function viewHome() {
    const incoming = S.requests.filter((r) => r.incoming);
    app.innerHTML = `
      <div class="row between"><div class="brand"><div class="logo">⚡</div>PocketBolt</div>
        <button class="btn ghost sm" id="qrbtn">My code</button></div>
      <div class="card balance" style="margin-top:16px">
        <div class="muted small">PocketBolt balance · Hi, ${esc(S.user.display_name.split(" ")[0])}</div>
        <div class="amt">${esc(S.user.balance)}</div>
        <div class="row"><button class="btn primary grow" id="addbtn">Add cash</button><button class="btn grow" id="outbtn">Cash out</button></div>
      </div>
      <div class="qa">
        <button data-go="pay"><span>⚡</span>Pay</button>
        <button id="reqbtn"><span>🙏</span>Request</button>
        <button id="scanbtn"><span>🔗</span>Share</button>
        <button data-go="cards"><span>💳</span>Link</button>
      </div>
      ${S.installEvt ? `<button class="btn ghost block" id="installbtn" style="margin-top:14px">📲 Install PocketBolt on this device</button>` : ""}
      ${incoming.length ? `<h2>Waiting on you</h2><div class="list">${incoming.map(requestItem).join("")}</div>` : ""}
      <h2>Recent activity</h2>
      <div class="list">${S.activity.slice(0, 5).map(activityItem).join("") || `<div class="empty">No activity yet. Send your first payment ⚡</div>`}</div>`;
    $("#addbtn").onclick = () => fundsSheet("add");
    $("#outbtn").onclick = () => fundsSheet("out");
    $("#qrbtn").onclick = qrSheet; $("#scanbtn").onclick = shareHandle;
    $("#reqbtn").onclick = () => { S.payMode = "request"; go("pay"); };
    app.querySelectorAll("[data-go]").forEach((b) => b.onclick = () => go(b.dataset.go));
    const ib = $("#installbtn"); if (ib) ib.onclick = install;
    bindRequestButtons(app);
  }

  function viewPay() {
    const P = S.pay;
    const contactsOK = "contacts" in navigator && "ContactsManager" in window;
    app.innerHTML = `
      <div class="pay-amt ${P.amount.length > 7 ? "small" : ""}" id="amt">$${esc(P.amount)}</div>
      <div class="suggest">
        <div class="row"><input id="to" class="grow" placeholder="To: $handle, phone or email" value="${esc(P.to)}" autocomplete="off" inputmode="email">
        ${contactsOK ? `<button class="btn ghost sm" id="contacts" title="Pick from contacts">👥</button>` : ""}</div>
        <div class="menu list hidden" id="menu"></div>
      </div>
      <input id="note" placeholder="What's it for?" value="${esc(P.note)}" maxlength="140" style="margin-top:8px">
      <div class="keypad">${["1","2","3","4","5","6","7","8","9",".","0","⌫"].map((k) => `<button data-k="${k}">${k}</button>`).join("")}</div>
      <label style="margin-top:0">Pay with</label>
      <select id="src">${sourceOptions(true, P.source)}</select>
      <div class="row" style="margin-top:14px"><button class="btn grow" id="doreq">Request</button><button class="btn primary grow" id="dopay">Pay</button></div>
      <p class="muted small" style="text-align:center">Test mode: payments move simulated money between PocketBolt users.</p>`;
    $("#src").value = P.source;
    app.querySelectorAll("[data-k]").forEach((b) => b.onclick = () => { key(b.dataset.k); });
    const to = $("#to"), menu = $("#menu");
    let t;
    to.oninput = () => { P.to = to.value; clearTimeout(t); t = setTimeout(async () => {
      if (!to.value.trim()) { menu.classList.add("hidden"); return; }
      try {
        const r = await api("/api/users/search?q=" + encodeURIComponent(to.value));
        menu.innerHTML = r.users.map((u) => `<div class="item" data-u="${esc(u.username)}" style="cursor:pointer">${avatar(u.display_name, u.username)}
          <div><div class="t">${esc(u.display_name)}</div><div class="s">$${esc(u.username)}</div></div></div>`).join("");
        menu.classList.toggle("hidden", !r.users.length);
        menu.querySelectorAll("[data-u]").forEach((el) => el.onclick = () => { to.value = P.to = "$" + el.dataset.u; menu.classList.add("hidden"); });
      } catch (_) {} }, 200); };
    to.onblur = () => setTimeout(() => menu.classList.add("hidden"), 200);
    $("#note").oninput = (e) => P.note = e.target.value;
    $("#src").onchange = (e) => P.source = e.target.value;
    const cb = $("#contacts");
    if (cb) cb.onclick = async () => {
      try {
        const [c] = await navigator.contacts.select(["name", "email", "tel"], { multiple: false });
        if (c) { to.value = P.to = (c.email && c.email[0]) || (c.tel && c.tel[0]) || ""; to.dispatchEvent(new Event("input")); }
      } catch (e) { toast("Couldn't open contacts", true); }
    };
    $("#dopay").onclick = (e) => submitPay(e.target, "pay");
    $("#doreq").onclick = (e) => submitPay(e.target, "request");
    if (S.payMode === "request") { S.payMode = null; $("#doreq").classList.add("primary"); $("#dopay").classList.remove("primary"); }
  }

  function key(k) {
    const P = S.pay; let a = P.amount;
    if (k === "⌫") a = a.length > 1 ? a.slice(0, -1) : "0";
    else if (k === ".") { if (!a.includes(".")) a += "."; }
    else {
      if (a.includes(".") && a.split(".")[1].length >= 2) return;
      if (a.replace(".", "").length >= 7) return;
      a = a === "0" ? k : a + k;
    }
    P.amount = a; buzz(8);
    const el = $("#amt"); el.textContent = "$" + a; el.classList.toggle("small", a.length > 7);
  }

  async function submitPay(btn, mode) {
    const P = S.pay;
    if (!(parseFloat(P.amount) > 0)) return toast("Enter an amount", true);
    if (!P.to.trim()) return toast("Who's it for? Enter a $handle, phone or email", true);
    try {
      const r = await withBusy(btn, () => mode === "pay"
        ? api("/api/send", { body: { to: P.to, amount: P.amount, note: P.note, source_id: P.source } })
        : api("/api/request", { body: { from: P.to, amount: P.amount, note: P.note } }));
      buzz([20, 30, 60]);
      openSheet(`<div class="success"><div class="big">${mode === "pay" ? "⚡" : "🙏"}</div><h1>${esc(r.message)}</h1>
        <p class="muted">${esc(P.note)}</p><button class="btn primary block" id="ok">Done</button></div>`,
        (sh) => sh.querySelector("#ok").onclick = () => { closeSheet(); go("activity"); });
      S.pay = { amount: "0", to: "", note: "", source: "wallet" };
      await refresh();
    } catch (e) { toast(e.message, true); buzz(120); }
  }

  function viewActivity() {
    app.innerHTML = `<h1>Activity</h1>
      ${S.requests.length ? `<h2>Pending requests</h2><div class="list">${S.requests.map(requestItem).join("")}</div>` : ""}
      <h2>History</h2>
      <div class="list">${S.activity.map(activityItem).join("") || `<div class="empty">Nothing yet.</div>`}</div>`;
    bindRequestButtons(app);
  }

  function viewCards() {
    const cfg = S.config;
    app.innerHTML = `<h1>Banks & cards</h1>
      <div class="list">${S.sources.map((s) => `<div class="item">
        <div class="avatar">${s.kind === "bank" ? "🏦" : "💳"}</div>
        <div class="grow"><div class="t">${esc(s.label)} ••${esc(s.last4)}</div>
        <div class="s">${s.is_default ? "Default · " : ""}${s.provider === "simulated" ? "Sandbox" : esc(s.provider) + " test"}</div></div>
        ${s.is_default ? "" : `<button class="btn ghost sm" data-def="${s.id}">Default</button>`}
        <button class="btn danger sm" data-del="${s.id}" aria-label="Remove">✕</button></div>`).join("")
        || `<div class="empty">Link a bank or card to add cash and pay.</div>`}</div>
      <div class="row" style="margin-top:14px"><button class="btn primary grow" id="lbank">🏦 Link bank</button><button class="btn grow" id="lcard">💳 Add card</button></div>
      <div class="hint">Bank linking: <b>${cfg.bank_provider === "plaid" ? "Plaid Link (sandbox)" : "built-in sandbox"}</b> ·
        Cards: <b>${cfg.card_provider === "stripe" ? "Stripe test mode" : "built-in sandbox"}</b>. Never enter a real card here.</div>`;
    $("#lbank").onclick = linkBank; $("#lcard").onclick = linkCard;
    app.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
      if (!confirm("Remove this payment method?")) return;
      try { await api(`/api/funding_sources/${b.dataset.del}`, { method: "DELETE" }); await refresh(); render(); toast("Removed"); }
      catch (e) { toast(e.message, true); } });
    app.querySelectorAll("[data-def]").forEach((b) => b.onclick = async () => {
      try { await api(`/api/funding_sources/${b.dataset.def}/default`, { body: {} }); await refresh(); render(); }
      catch (e) { toast(e.message, true); } });
  }

  function viewProfile() {
    const u = S.user;
    const notifState = !("Notification" in window) ? "unsupported" : Notification.permission;
    app.innerHTML = `<h1>Profile</h1>
      <div class="card" style="text-align:center">
        ${avatar(u.display_name, u.username).replace('class="avatar"', 'class="avatar" style="width:72px;height:72px;font-size:26px;margin:0 auto;background:hsl(' + hue(u.username) + ' 45% 28%)"')}
        <h1 style="margin:10px 0 0">${esc(u.display_name)}</h1><div class="muted">$${esc(u.username)}</div>
        <div class="qr"><img alt="QR code for your pay link" src="/api/qr.svg?data=${encodeURIComponent(payLink(u.username))}"></div>
        <div class="muted small">Scan to pay $${esc(u.username)}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary grow" id="share">Share my link</button><button class="btn grow" id="copy">Copy</button></div>
      </div>
      <h2>Account</h2>
      <div class="list">
        <div class="item"><div class="grow"><div class="t">Email</div><div class="s">${esc(u.email)}</div></div></div>
        <div class="item"><div class="grow"><div class="t">Phone</div><div class="s">${esc(u.phone || "Not added")}</div></div></div>
        <div class="item"><div class="grow"><div class="t">Notifications</div><div class="s">${esc(notifState)}</div></div>
          ${notifState === "default" ? `<button class="btn sm" id="notif">Enable</button>` : ""}</div>
        <div class="item"><div class="grow"><div class="t">Install app</div><div class="s">${matchMedia("(display-mode: standalone)").matches ? "Installed ✓" : /iphone|ipad/i.test(navigator.userAgent) ? "Tap Share → Add to Home Screen" : "Add PocketBolt to your home screen"}</div></div>
          ${S.installEvt ? `<button class="btn sm" id="inst">Install</button>` : ""}</div>
      </div>
      <button class="btn danger block" id="logout" style="margin-top:20px">Log out</button>
      <p class="muted small" style="text-align:center;margin-top:16px">PocketBolt prototype · test mode · no real money moves.</p>`;
    $("#share").onclick = shareHandle;
    $("#copy").onclick = async () => { try { await navigator.clipboard.writeText(payLink(u.username)); toast("Link copied"); } catch (_) { toast(payLink(u.username)); } };
    $("#logout").onclick = async () => { await api("/api/logout", { body: {} }); S.user = null; renderAuth(); };
    const n = $("#notif"); if (n) n.onclick = async () => { await Notification.requestPermission(); render(); };
    const i = $("#inst"); if (i) i.onclick = install;
  }

  // ---------------------------------------------------------------- sheets
  function fundsSheet(kind) {
    if (!S.sources.length) { toast("Link a bank or card first"); return go("cards"); }
    const add = kind === "add";
    openSheet(`<h1>${add ? "Add cash" : "Cash out"}</h1>
      <p class="muted">${add ? "Move money from a linked card or bank into PocketBolt." : `Available: ${esc(S.user.balance)}`}</p>
      <label>Amount</label><input id="famt" inputmode="decimal" placeholder="0.00">
      <div class="row" style="margin-top:8px">${[10, 25, 50, 100].map((v) => `<button class="btn sm" data-v="${v}">$${v}</button>`).join("")}</div>
      <label>${add ? "From" : "To"}</label><select id="fsrc">${sourceOptions(false)}</select>
      <div class="err" id="ferr"></div>
      <button class="btn primary block" id="fgo">${add ? "Add cash" : "Cash out"}</button>
      <p class="muted small">Simulated in test mode. ${add ? "" : "Instant to debit card, 1–3 days to bank in a live app."}</p>`,
    (sh) => {
      sh.querySelectorAll("[data-v]").forEach((b) => b.onclick = () => sh.querySelector("#famt").value = b.dataset.v);
      sh.querySelector("#fgo").onclick = (e) => withBusy(e.target, async () => {
        try {
          const r = await api(add ? "/api/add_funds" : "/api/cash_out",
            { body: { amount: sh.querySelector("#famt").value, source_id: sh.querySelector("#fsrc").value } });
          closeSheet(); toast(r.message); buzz(30); await refresh(); render();
        } catch (err) { sh.querySelector("#ferr").textContent = err.message; }
      });
    });
  }

  function payRequestSheet(r) {
    if (!r) return;
    openSheet(`<h1>Pay ${esc(r.requester.display_name)}</h1>
      <div class="pay-amt small">${esc(r.amount)}</div><p class="muted" style="text-align:center">${esc(r.note)}</p>
      <label>Pay with</label><select id="rsrc">${sourceOptions(true)}</select>
      <div class="err" id="rerr"></div><button class="btn primary block" id="rgo">Pay ${esc(r.amount)}</button>`,
    (sh) => sh.querySelector("#rgo").onclick = (e) => withBusy(e.target, async () => {
      try { const res = await api(`/api/requests/${r.id}/pay`, { body: { source_id: sh.querySelector("#rsrc").value } });
        closeSheet(); toast(res.message); buzz([20, 30, 60]); await refresh(); render(); }
      catch (err) { sh.querySelector("#rerr").textContent = err.message; }
    }));
  }

  function qrSheet() {
    const u = S.user;
    openSheet(`<div style="text-align:center"><h1>Pay $${esc(u.username)}</h1>
      <div class="qr"><img alt="QR code" src="/api/qr.svg?data=${encodeURIComponent(payLink(u.username))}"></div>
      <p class="muted small">${esc(payLink(u.username))}</p>
      <button class="btn primary block" id="qs">Share link</button></div>`, (sh) => sh.querySelector("#qs").onclick = shareHandle);
  }

  async function shareHandle() {
    const u = S.user, url = payLink(u.username);
    const data = { title: "Pay me on PocketBolt", text: `Pay $${u.username} on PocketBolt ⚡`, url };
    try {
      if (navigator.share) await navigator.share(data);
      else { await navigator.clipboard.writeText(url); toast("Pay link copied"); }
    } catch (e) { if (e.name !== "AbortError") toast(url); }
  }

  const loadScript = (src) => new Promise((ok, bad) => {
    if (document.querySelector(`script[src="${src}"]`)) return ok();
    const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = () => bad(new Error("Couldn't load " + src));
    document.head.appendChild(s);
  });

  async function linkBank() {
    if (S.config.bank_provider === "plaid") {
      try {
        await loadScript("https://cdn.plaid.com/link/v2/stable/link-initialize.js");
        const { link_token } = await api("/api/plaid/link_token", { body: {} });
        const handler = window.Plaid.create({
          token: link_token,
          onSuccess: async (public_token, meta) => {
            try {
              await api("/api/plaid/exchange", { body: { public_token, account_id: meta.accounts?.[0]?.id, institution: meta.institution } });
              toast("Bank linked via Plaid"); await refresh(); render();
            } catch (e) { toast(e.message, true); }
          },
        });
        handler.open();
      } catch (e) { toast(e.message, true); }
      return;
    }
    const { banks } = await api("/api/banks");
    openSheet(`<h1>Link a bank</h1><p class="muted">Sandbox bank login. No real bank is contacted.</p>
      <label>Bank</label><select id="bk">${banks.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}</select>
      <label>Account type</label><select id="bt"><option>checking</option><option>savings</option></select>
      <label>Online banking username</label><input id="bu" value="" autocomplete="off" placeholder="user_good">
      <label>Password</label><input id="bp" type="password" autocomplete="off" placeholder="pass_good">
      <div class="hint">Sandbox login: <b>user_good</b> / <b>pass_good</b> (same as Plaid's sandbox).</div>
      <div class="err" id="berr"></div><button class="btn primary block" id="bgo">Link bank</button>`,
    (sh) => sh.querySelector("#bgo").onclick = (e) => withBusy(e.target, async () => {
      try {
        await api("/api/link/bank", { body: { institution_id: sh.querySelector("#bk").value, account_type: sh.querySelector("#bt").value,
          username: sh.querySelector("#bu").value, password: sh.querySelector("#bp").value } });
        closeSheet(); toast("Bank linked"); await refresh(); render();
      } catch (err) { sh.querySelector("#berr").textContent = err.message; }
    }));
  }

  async function linkCard() {
    if (S.config.card_provider === "stripe") {
      openSheet(`<h1>Add a card</h1><p class="muted">Stripe test mode. Use 4242 4242 4242 4242, any future date, any CVC.</p>
        <div id="card-el" class="card" style="padding:16px"></div><div class="err" id="cerr"></div>
        <button class="btn primary block" id="cgo">Save card</button>`, async (sh) => {
        try {
          await loadScript("https://js.stripe.com/v3/");
          const stripe = window.Stripe(S.config.stripe_publishable_key);
          const card = stripe.elements({ appearance: { theme: "night" } }).create("card", { style: { base: { color: "#eef3f8", fontSize: "16px" } } });
          card.mount("#card-el");
          sh.querySelector("#cgo").onclick = (e) => withBusy(e.target, async () => {
            try {
              const { client_secret } = await api("/api/stripe/setup_intent", { body: {} });
              const { setupIntent, error } = await stripe.confirmCardSetup(client_secret, { payment_method: { card } });
              if (error) throw new Error(error.message);
              await api("/api/stripe/confirm", { body: { setup_intent_id: setupIntent.id } });
              closeSheet(); toast("Card saved with Stripe"); await refresh(); render();
            } catch (err) { sh.querySelector("#cerr").textContent = err.message; }
          });
        } catch (err) { sh.querySelector("#cerr").textContent = err.message; }
      });
      return;
    }
    openSheet(`<h1>Add a debit or credit card</h1><p class="muted">Sandbox card. Only the brand and last 4 digits are saved.</p>
      <label>Card number</label><input id="cn" inputmode="numeric" autocomplete="off" placeholder="4242 4242 4242 4242" maxlength="23">
      <div class="row"><div class="grow"><label>Expiry (MM/YY)</label><input id="ce" inputmode="numeric" placeholder="12/29" maxlength="5"></div>
      <div class="grow"><label>CVC</label><input id="cc" inputmode="numeric" placeholder="123" maxlength="4"></div></div>
      <label>Type</label><select id="cf"><option value="debit">Debit</option><option value="credit">Credit</option></select>
      <div class="hint">Test cards: <b>4242 4242 4242 4242</b> works · <b>4000 0000 0000 0002</b> is declined. Never use a real card.</div>
      <div class="err" id="cerr"></div><button class="btn primary block" id="cgo">Add card</button>`,
    (sh) => {
      const cn = sh.querySelector("#cn");
      cn.oninput = () => { cn.value = cn.value.replace(/\D/g, "").slice(0, 19).replace(/(.{4})/g, "$1 ").trim(); };
      const ce = sh.querySelector("#ce");
      ce.oninput = () => { const d = ce.value.replace(/\D/g, "").slice(0, 4); ce.value = d.length > 2 ? d.slice(0, 2) + "/" + d.slice(2) : d; };
      sh.querySelector("#cgo").onclick = (e) => withBusy(e.target, async () => {
        const [m, y] = ce.value.split("/");
        try {
          await api("/api/link/card", { body: { number: cn.value, exp_month: m, exp_year: y, cvc: sh.querySelector("#cc").value, funding: sh.querySelector("#cf").value } });
          closeSheet(); toast("Card added"); await refresh(); render();
        } catch (err) { sh.querySelector("#cerr").textContent = err.message; }
      });
    });
  }

  // ---------------------------------------------------------------- PWA bits
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); S.installEvt = e; if (S.user && ["home", "profile"].includes(S.tab)) render(); });
  async function install() { if (!S.installEvt) return; S.installEvt.prompt(); await S.installEvt.userChoice; S.installEvt = null; render(); }
  if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
  window.addEventListener("online", () => { toast("Back online"); S.user && refresh(true).then(render); });
  window.addEventListener("offline", () => toast("You're offline. Payments need a connection.", true));

  // ---------------------------------------------------------------- routing
  const views = { home: viewHome, pay: viewPay, activity: viewActivity, cards: viewCards, profile: viewProfile };
  function render() {
    if (!S.user) return renderAuth();
    $("#tabs").classList.remove("hidden");
    document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === S.tab));
    (views[S.tab] || viewHome)();
  }
  function go(tab) { if (location.hash !== "#" + tab) location.hash = tab; else { S.tab = tab; render(); } window.scrollTo(0, 0); }
  window.addEventListener("hashchange", () => { const t = location.hash.slice(1); if (views[t]) { S.tab = t; closeSheet(); render(); } });

  async function boot() {
    S.config = await api("/api/config").catch(() => ({ bank_provider: "simulated", card_provider: "simulated" }));
    // Deep links: /pay/<handle>?amount=12 and /app/<tab>
    const m = location.pathname.match(/^\/pay\/([^/]+)/), t = location.pathname.match(/^\/app\/(\w+)/);
    if (m) { S.pay.to = "$" + decodeURIComponent(m[1]); const a = new URLSearchParams(location.search).get("amount"); if (a && /^\d+(\.\d{1,2})?$/.test(a)) S.pay.amount = a; }
    const ok = await refresh();
    if (!ok) return renderAuth();
    if (m || t) { history.replaceState(null, "", "/#" + (m ? "pay" : t[1])); }
    const h = location.hash.slice(1);
    S.tab = views[h] ? h : "home";
    render();
  }

  // Stay live: poll while visible, refresh when the app comes back to the foreground.
  setInterval(() => { if (S.user && !document.hidden && $("#sheet").classList.contains("hidden")) refresh(true).then((ok) => ok && S.tab !== "pay" && render()); }, 10000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && S.user) refresh(true).then((ok) => ok && S.tab !== "pay" && render()); });

  boot();
})();
