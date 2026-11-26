/* Talashly Toolsmith: kisi bhi website par sidebar AI (Gemini-style). Istemal:
   <script src="https://YOUR-TALASHLY/static/toolsmith.js" defer></script>
   Optional: data-open="1" (khula shuru ho), data-mode="plan|build". "Get my plans" button: window.TalashlyToolsmith.open("plan", "mujhe ... chahiye") */
(function () {
  "use strict";
  var me = document.currentScript, base = me ? new URL(me.src).origin : "";
  var S = { mode: (me && me.dataset.mode) || "plan", hist: [], busy: false };
  var cid; try { cid = localStorage.getItem("ts_cid"); if (!cid) { cid = "ts" + Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem("ts_cid", cid); } } catch (e) { cid = "ts" + Math.random().toString(36).slice(2) + "000000"; }
  function el(t, c, x) { var e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; }
  var css = el("style"); css.textContent = ".tsb{position:fixed;right:18px;bottom:18px;z-index:99998;background:#4f46e5;color:#fff;border:0;border-radius:999px;padding:12px 18px;font:600 15px system-ui;cursor:pointer;box-shadow:0 6px 24px #0004}" +
    ".tsp{position:fixed;top:0;right:0;height:100%;width:min(420px,100%);z-index:99999;background:#fff;color:#0f172a;border-left:1px solid #e2e8f0;display:flex;flex-direction:column;transform:translateX(100%);transition:transform .25s;font:15px/1.5 system-ui}.tsp.o{transform:none}" +
    "@media (prefers-color-scheme:dark){.tsp{background:#0b1020;color:#e2e8f0;border-color:#24304f}.tsp textarea,.tsp .m.b{background:#131a30;color:#e2e8f0;border-color:#24304f}}" +
    ".tsh{display:flex;gap:8px;align-items:center;padding:12px 14px;border-bottom:1px solid #e2e8f055}.tsh b{flex:1}.tsh button{border:1px solid #94a3b855;background:none;color:inherit;border-radius:8px;padding:4px 10px;cursor:pointer}.tsh button.on{background:#4f46e5;color:#fff;border-color:#4f46e5}" +
    ".tsl{flex:1;overflow:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px}.m{padding:9px 12px;border-radius:12px;white-space:pre-wrap;word-break:break-word;max-width:94%}.m.u{align-self:flex-end;background:#4f46e5;color:#fff}.m.b{background:#f1f5f9;border:1px solid #e2e8f0}" +
    ".tsp pre{background:#0f172a;color:#e2e8f0;padding:10px;border-radius:8px;overflow:auto;font-size:12.5px}.tsp a.dl{display:inline-block;margin-top:6px;background:#4f46e5;color:#fff;padding:8px 14px;border-radius:10px;font-weight:700;text-decoration:none}" +
    ".tsf{display:flex;gap:8px;padding:12px;border-top:1px solid #e2e8f055}.tsf textarea{flex:1;resize:none;height:44px;border:1px solid #cbd5e1;border-radius:10px;padding:8px;font:inherit}.tsf button{background:#4f46e5;color:#fff;border:0;border-radius:10px;padding:0 16px;font-weight:700;cursor:pointer}.tsn{font-size:12px;opacity:.7;padding:0 14px 8px}";
  document.head.appendChild(css);
  var btn = el("button", "tsb", "✨ Get my plan"), p = el("aside", "tsp"), list = el("div", "tsl"), note = el("div", "tsn", "");
  p.setAttribute("aria-label", "Talashly Toolsmith");
  var h = el("div", "tsh"), mp = el("button", "", "Plan"), mb = el("button", "", "Build code"), x = el("button", "", "✕");
  h.append(el("b", "", "Talashly Toolsmith"), mp, mb, x);
  var f = el("div", "tsf"), ta = el("textarea"), go = el("button", "", "Send"); ta.placeholder = "Kaunsa tool/service chahiye? (Roman Urdu / English)"; ta.maxLength = 4000;
  f.append(ta, go); p.append(h, list, note, f); document.body.append(btn, p);
  function setMode(m) { S.mode = m; mp.className = m === "plan" ? "on" : ""; mb.className = m === "build" ? "on" : ""; }
  function open(m, text) { if (m) setMode(m); p.classList.add("o"); if (text) { ta.value = text; } ta.focus(); }
  mp.onclick = function () { setMode("plan"); }; mb.onclick = function () { setMode("build"); }; btn.onclick = function () { open(); }; x.onclick = function () { p.classList.remove("o"); };
  setMode(S.mode); if (me && me.dataset.open === "1") open();
  function render(box, text) {
    box.textContent = ""; text.split("```").forEach(function (part, i) {
      if (i % 2) { var pre = el("pre"); pre.appendChild(el("code", "", part.replace(/^[^\n]*\n/, ""))); box.appendChild(pre); } else if (part) box.appendChild(document.createTextNode(part));
    });
  }
  async function send() {
    var q = ta.value.trim(); if (!q || S.busy) return; S.busy = true; ta.value = "";
    list.appendChild(el("div", "m u", q)); var b = el("div", "m b", "…"); list.appendChild(b); list.scrollTop = 1e9;
    var text = "", dl = null;
    try {
      var r = await fetch(base + "/api/toolsmith", { method: "POST", headers: { "Content-Type": "application/json", "X-Client-Id": cid }, body: JSON.stringify({ message: q, mode: S.mode, history: S.hist.slice(-6) }) });
      if (!r.ok) throw new Error(r.status === 429 ? "Thori der baad try karein." : "Server error " + r.status);
      var rd = r.body.getReader(), dec = new TextDecoder(), buf = "";
      for (;;) {
        var c = await rd.read(); if (c.done) break; buf += dec.decode(c.value, { stream: true });
        var parts = buf.split("\n\n"); buf = parts.pop();
        parts.forEach(function (blk) {
          var ev = (blk.match(/^event: (.*)$/m) || [])[1], dt = (blk.match(/^data: (.*)$/m) || [])[1], d; try { d = JSON.parse(dt); } catch (e) { return; }
          if (ev === "token") { text += d; render(b, text); list.scrollTop = 1e9; }
          else if (ev === "meta") note.textContent = "Aaj ki free requests baqi: " + d.left_today;
          else if (ev === "check") note.textContent = d.lines.join(" | ");
          else if (ev === "download" && /^\/api\/dl\/[\w-]+$/.test(d.url)) dl = d;
          else if (ev === "error") { text = d.message || "Error"; b.textContent = text; }
        });
      }
      if (dl) { var a = el("a", "dl", "⬇ Download " + dl.name); a.href = base + dl.url; a.download = dl.name; b.appendChild(document.createElement("br")); b.appendChild(a); }
      S.hist.push({ role: "user", content: q }, { role: "assistant", content: text.slice(0, 3000) });
    } catch (e) { b.textContent = String(e.message || e); }
    S.busy = false; list.scrollTop = 1e9;
  }
  go.onclick = send; ta.onkeydown = function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  window.TalashlyToolsmith = { open: open, send: function (t, m) { open(m || "plan", t); send(); } };
})();
