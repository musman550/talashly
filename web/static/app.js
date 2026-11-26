"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };
  const S = { models: [], model: "mus-7.01", conv: null, convs: [], ctl: null, busy: false, stick: true, lastText: "", priv: false, hist: [] };

  function clientId() {
    let id = store.get("tl_cid");
    if (!id || !/^[A-Za-z0-9_-]{16,64}$/.test(id)) {
      const a = new Uint8Array(16);
      (window.crypto || window.msCrypto).getRandomValues(a);
      id = Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
      store.set("tl_cid", id);
    }
    return id;
  }
  const CID = clientId();
  function headers(json) {
    const h = { "X-Client-Id": CID };
    const k = store.get("tl_key");
    if (k) h["Authorization"] = "Bearer " + k;
    if (json) h["Content-Type"] = "application/json";
    return h;
  }
  function safeUrl(u) { return /^https?:\/\//i.test(u || "") ? u : "#"; }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  async function errText(r) {
    try { const j = await r.json(); return (j.detail && (j.detail.message || j.detail)) || (j.error && j.error.message) || ("Error " + r.status); }
    catch (e) { return "Error " + r.status; }
  }

  // ---------- safe mini-markdown (never innerHTML)
  function inline(parent, text, sources) {
    const re = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[\d{1,2}\])/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      const t = m[0];
      if (t.startsWith("**")) parent.appendChild(el("strong", null, t.slice(2, -2)));
      else if (t.startsWith("`")) parent.appendChild(el("code", null, t.slice(1, -1)));
      else {
        const n = parseInt(t.slice(1, -1), 10);
        const s = (sources || []).find((x) => x.n === n);
        if (s) {
          const a = el("a", "cite", String(n));
          a.href = safeUrl(s.url); a.target = "_blank"; a.rel = "noopener noreferrer nofollow";
          a.title = (s.title || s.domain || "") + " - " + (s.domain || "");
          parent.appendChild(a);
        } else parent.appendChild(document.createTextNode(t));
      }
      last = m.index + t.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }
  function render(body, text, sources) {
    body.textContent = "";
    const parts = text.split(/```/);
    parts.forEach((part, i) => {
      if (i % 2 === 1) {
        const pre = el("pre"); const code = el("code", null, part.replace(/^[^\n]*\n/, ""));
        pre.appendChild(code); body.appendChild(pre);
      } else {
        part.split(/\n{2,}/).forEach((para) => {
          if (!para.trim()) return;
          const p = el("p"); p.dir = "auto"; inline(p, para.trim(), sources); body.appendChild(p);
        });
      }
    });
  }

  // ---------- thread
  const msgs = $("msgs"), thread = $("thread");
  function scroll() { if (S.stick) thread.scrollTop = thread.scrollHeight; }
  let lastTop = 0;  // sirf user ke UPAR scroll karne par auto-scroll band; lamba content aane se nahi
  thread.addEventListener("scroll", () => {
    const gap = thread.scrollHeight - thread.scrollTop - thread.clientHeight;
    if (thread.scrollTop < lastTop - 2) S.stick = gap < 80; else if (gap < 80) S.stick = true;
    lastTop = thread.scrollTop;
  });
  function addUser(text) {
    const m = el("div", "msg user"); const b = el("div", "bubble", text); b.dir = "auto"; m.appendChild(b);
    msgs.appendChild(m); $("welcome").hidden = true; scroll();
  }
  function addBot() {
    const m = el("div", "msg bot");
    const who = el("div", "who"); who.appendChild(el("span", null, "Talashly"));
    const status = el("div", "status"); const body = el("div", "body cursor"); const extra = el("div"); const foot = el("div", "foot");
    m.append(who, status, body, extra, foot); msgs.appendChild(m); scroll();
    return { m, status, body, extra, foot, text: "", sources: [] };
  }
  function showSources(b) {
    b.extra.textContent = "";
    if (!b.sources.length) return;
    const g = el("div", "srcs");
    b.sources.forEach((s) => {
      const a = el("a", "src"); a.href = safeUrl(s.url); a.target = "_blank"; a.rel = "noopener noreferrer nofollow";
      a.appendChild(el("b", null, s.n + ". " + (s.title || s.domain)));
      a.appendChild(el("span", null, s.domain || ""));
      g.appendChild(a);
    });
    b.extra.appendChild(g);
  }
  function showCode(b, items) {
    items.slice(0, 5).forEach((c) => {
      const h = el("div", "note", c.path + " : lines " + c.start + "-" + c.end);
      const pre = el("pre"); pre.appendChild(el("code", null, c.text));
      b.extra.append(h, pre);
    });
  }
  function footer(b, done, retryText) {
    b.foot.textContent = "";
    const bits = [];
    if (done && done.model) bits.push(done.model);
    if (done && done.took_ms != null) bits.push(done.took_ms + " ms");
    if (bits.length) b.foot.appendChild(el("span", null, bits.join(" · ")));
    const copy = el("button", null, "Copy"); copy.type = "button";
    copy.onclick = async () => { try { await navigator.clipboard.writeText(b.text); copy.textContent = "Copied"; } catch (e) { copy.textContent = "Copy failed"; } };
    b.foot.appendChild(copy);
    if (b.q && done && ["extractive", "llm-direct", "llm", "builder", "learned"].includes(done.mode) && b.text) {
      ["up", "down"].forEach((v) => {
        const btn = el("button", null, v === "up" ? "\u{1F44D}" : "\u{1F44E}"); btn.type = "button"; btn.title = v === "up" ? "Good answer" : "Not helpful";
        btn.onclick = async () => {
          try { await fetch("/api/feedback", { method: "POST", headers: headers(true), body: JSON.stringify({ question: b.q, answer: b.text.slice(0, 3000), vote: v }) }); } catch (e) { /* ignore */ }
          b.foot.querySelectorAll(".fb").forEach((x) => x.remove()); b.foot.appendChild(el("span", "fb", "Thanks!"));
        };
        btn.classList.add("fb"); b.foot.appendChild(btn);
      });
    }
    if (retryText) { const r = el("button", null, "Retry"); r.type = "button"; r.onclick = () => send(retryText); b.foot.appendChild(r); }
  }

  // ---------- SSE
  async function send(text) {
    text = (text || "").trim();
    if (!text || S.busy) return;
    S.busy = true; S.stick = true; S.lastText = text;
    $("send").textContent = "Stop"; $("send").type = "button";
    addUser(text);
    const b = addBot(); b.q = text;
    const ctl = new AbortController(); S.ctl = ctl;
    let done = null, failed = false, stopped = false;
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: headers(true), signal: ctl.signal,
        body: JSON.stringify({ message: text, conversation_id: S.priv ? null : S.conv, model: S.model, private: S.priv, history: S.priv ? S.hist.slice(-8) : undefined }) });
      if (!r.ok) throw new Error(await errText(r));
      const rd = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done: fin } = await rd.read();
        if (fin) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          let name = "message", data = "";
          block.split("\n").forEach((l) => { if (l.startsWith("event: ")) name = l.slice(7); else if (l.startsWith("data: ")) data += l.slice(6); });
          let d; try { d = JSON.parse(data); } catch (e) { continue; }
          handle(b, name, d, (x) => { done = x; });
        }
      }
    } catch (e) {
      failed = true;
      if (e.name === "AbortError") stopped = true;
      else { b.body.classList.remove("cursor"); b.body.appendChild(el("p", "err", "Could not get an answer: " + e.message)); b.status.textContent = ""; }
    } finally {
      b.body.classList.remove("cursor"); b.status.textContent = stopped ? "Stopped." : "";
      if (S.priv && !failed && b.text) { S.hist.push({ role: "user", content: text.slice(0, 4000) }, { role: "assistant", content: b.text.slice(0, 4000) }); }
      footer(b, done, failed && !stopped ? text : null);
      scroll();
      S.busy = false; S.ctl = null; $("send").textContent = "Send"; $("send").type = "submit";
      loadConvs(); $("in").focus();
    }
  }
  function showDl(b) {
    if (!b.dl || !/^\/api\/dl\/[\w-]+$/.test(b.dl.url)) return;
    let a = b.body.querySelector("a.dl");
    if (!a) { a = el("a", "dl", "⬇ Download " + b.dl.name); a.href = b.dl.url; a.setAttribute("download", b.dl.name); a.style.cssText = "display:inline-block;margin:8px 0;padding:9px 16px;border-radius:10px;background:var(--acc,#4f46e5);color:#fff;font-weight:700;text-decoration:none"; }
    b.body.appendChild(a);
  }
  function handle(b, name, d, setDone) {
    if (name === "meta") { if (d.conversation_id) S.conv = d.conversation_id; }
    else if (name === "step") b.status.textContent = d.msg;
    else if (name === "plan") b.status.textContent = d.intent === "code" ? "Searching code..." : "Searching sources...";
    else if (name === "rewrite") b.status.textContent = "Understood as: " + d.query;
    else if (name === "sources") { b.sources = d; showSources(b); }
    else if (name === "code") showCode(b, d);
    else if (name === "token") { b.text += d; b.status.textContent = ""; render(b.body, b.text, b.sources); scroll(); }
    else if (name === "replace") { b.text = d; render(b.body, b.text, b.sources); }
    else if (name === "download") { b.dl = d; showDl(b); }
    else if (name === "error") { b.body.appendChild(el("p", "err", d.message || "Error")); }
    else if (name === "done") { setDone(d); render(b.body, b.text, b.sources); showDl(b); }
  }

  // ---------- conversations
  async function loadConvs() {
    if (S.priv) { $("convs").textContent = ""; return; }  // private mode: server par history hai hi nahi
    try {
      const r = await fetch("/api/conversations", { headers: headers() });
      if (!r.ok) return;
      S.convs = (await r.json()).conversations;
    } catch (e) { return; }
    const box = $("convs"); box.textContent = "";
    S.convs.forEach((c) => {
      const row = el("div", "conv" + (c.id === S.conv ? " on" : ""));
      const t = el("button", "t", c.title || "New chat"); t.type = "button"; t.dir = "auto"; t.onclick = () => openConv(c.id);
      const x = el("button", "x", "✕"); x.type = "button"; x.setAttribute("aria-label", "Delete chat");
      x.onclick = async () => { await fetch("/api/conversations/" + c.id, { method: "DELETE", headers: headers() }); if (S.conv === c.id) newChat(); else loadConvs(); };
      row.append(t, x); box.appendChild(row);
    });
  }
  async function openConv(id) {
    if (S.busy) return;
    const r = await fetch("/api/conversations/" + id, { headers: headers() });
    if (!r.ok) return;
    const j = await r.json(); S.conv = id; msgs.textContent = ""; $("welcome").hidden = true;
    j.messages.forEach((m) => {
      if (m.role === "user") addUser(m.content);
      else { const b = addBot(); b.text = m.content; b.sources = (m.meta && m.meta.sources) || []; b.body.classList.remove("cursor"); render(b.body, b.text, b.sources); showSources(b); footer(b, (m.meta && m.meta.done) || null, null); }
    });
    closeNav(); loadConvs(); S.stick = true; scroll();
  }
  function newChat() { if (S.busy && S.ctl) S.ctl.abort(); S.conv = null; S.hist = []; msgs.textContent = ""; $("welcome").hidden = false; closeNav(); loadConvs(); $("in").focus(); }

  // ---------- models
  async function loadModels() {
    try { const r = await fetch("/api/models"); const j = await r.json(); S.models = j.models; S.model = j.default; S.priv = !!j.private; } catch (e) { S.models = []; }
    const saved = store.get("tl_model");
    if (saved && S.models.some((m) => m.id === saved)) S.model = saved;
    const sel = $("model"); sel.textContent = "";
    S.models.forEach((m) => { const o = el("option", null, m.name + " - " + m.tagline); o.value = m.id; sel.appendChild(o); });
    sel.value = S.model; describe();
    if (S.priv) { const n = el("p", "mut", "Private mode: your chats are not saved. Refresh clears them."); n.style.cssText = "padding:8px 12px;font-size:12px;opacity:.7"; $("convs").appendChild(n); }
  }
  function describe() { const m = S.models.find((x) => x.id === S.model); $("mdesc").textContent = m ? m.description : ""; }
  $("model").addEventListener("change", (e) => { S.model = e.target.value; store.set("tl_model", S.model); describe(); });

  // ---------- API key dialog
  const dlg = $("keydlg");
  $("keybtn").onclick = () => { $("keyin").value = store.get("tl_key") || ""; $("keymsg").textContent = ""; $("keyex").hidden = true; dlg.showModal ? dlg.showModal() : dlg.setAttribute("open", ""); };
  $("keyclose").onclick = () => dlg.close();
  $("keysave").onclick = () => { const v = $("keyin").value.trim(); if (v) { store.set("tl_key", v); $("keymsg").textContent = "Saved in this browser."; loadConvs(); } };
  $("keyclear").onclick = () => { store.del("tl_key"); $("keyin").value = ""; $("keymsg").textContent = "Removed."; loadConvs(); };
  $("keymake").onclick = async () => {
    $("keymsg").textContent = "Creating...";
    try {
      const r = await fetch("/api/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: "web" }) });
      if (!r.ok) { $("keymsg").textContent = await errText(r); return; }
      const j = await r.json(); $("keyin").value = j.api_key; store.set("tl_key", j.api_key);
      $("keymsg").textContent = "Key created and saved in this browser. Copy it now - it is shown only once.";
      const ex = $("keyex"); ex.hidden = false;
      ex.textContent = "curl " + location.origin + "/v1/chat/completions \\\n  -H \"Authorization: Bearer " + j.api_key + "\" \\\n  -H \"Content-Type: application/json\" \\\n  -d '{\"model\":\"mus-7.01\",\"messages\":[{\"role\":\"user\",\"content\":\"What is a polite crawler?\"}]}'";
      loadConvs();
    } catch (e) { $("keymsg").textContent = "Network error: " + e.message; }
  };

  // ---------- misc UI
  function closeNav() { document.body.classList.remove("nav"); }
  $("menu").onclick = () => document.body.classList.toggle("nav");
  document.addEventListener("click", (e) => { if (document.body.classList.contains("nav") && !$("side").contains(e.target) && e.target !== $("menu")) closeNav(); });
  $("new").onclick = newChat;
  $("theme").onclick = () => {
    const cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme:dark)").matches ? "dark" : "light");
    const nx = cur === "dark" ? "light" : "dark"; document.documentElement.setAttribute("data-theme", nx); store.set("tl_theme", nx);
  };
  const th = store.get("tl_theme"); if (th === "dark" || th === "light") document.documentElement.setAttribute("data-theme", th);

  const inp = $("in");
  function grow() { inp.style.height = "auto"; inp.style.height = Math.min(inp.scrollHeight, 200) + "px"; }
  inp.addEventListener("input", grow);
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); } });
  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    if (S.busy) { if (S.ctl) S.ctl.abort(); return; }
    const t = inp.value; inp.value = ""; grow(); send(t);
  });
  $("send").addEventListener("click", (e) => { if (S.busy) { e.preventDefault(); if (S.ctl) S.ctl.abort(); } });

  const SUG = ["What does a polite web crawler do?", "Explain Python asyncio simply", "کریولر کیا کام کرتا ہے؟", "Compare SQLite and PostgreSQL"];
  SUG.forEach((s) => { const b = el("button", null, s); b.type = "button"; b.dir = "auto"; b.onclick = () => send(s); $("sug").appendChild(b); });

  loadModels().then(() => {
    loadConvs();
    const q = new URLSearchParams(location.search).get("q");
    if (q) send(q.slice(0, 4000));
  });
})();
