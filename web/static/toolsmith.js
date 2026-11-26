(function () {
  "use strict";
  var $ = function (i) { return document.getElementById(i); }, list = $("list"), q = $("q"), go = $("go"), note = $("note"), hist = [], busy = false;
  var cid; try { cid = localStorage.getItem("ts_cid"); if (!cid) { cid = "ts" + Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem("ts_cid", cid); } } catch (e) { cid = "ts" + Math.random().toString(36).slice(2) + "00000000"; }
  function el(t, c, x) { var e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; }
  function render(box, text) {
    box.textContent = "";
    text.split("```").forEach(function (part, i) {
      if (i % 2) {
        var nl = part.indexOf("\n"), lang = nl > -1 ? part.slice(0, nl).trim() : "", body = nl > -1 ? part.slice(nl + 1) : "";
        var w = el("div", "code"), h = el("div", "h"), cp = el("button", "", "Copy"); h.append(el("span", "", lang || "code"), cp);
        var pre = el("pre"); pre.appendChild(el("code", "", body)); w.append(h, pre); box.appendChild(w);
        cp.onclick = function () { try { navigator.clipboard.writeText(body); cp.textContent = "Copied"; setTimeout(function () { cp.textContent = "Copy"; }, 1200); } catch (e) { /* clipboard blocked */ } };
      } else if (part.trim()) { var p = el("p"); p.dir = "auto"; p.textContent = part.replace(/^\n+|\n+$/g, ""); box.appendChild(p); }
    });
  }
  function grow() { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, window.innerHeight * .4) + "px"; }
  q.addEventListener("input", grow);
  q.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("form").requestSubmit(); } });
  $("form").addEventListener("submit", async function (e) {
    e.preventDefault(); var msg = q.value.trim(); if (!msg || busy) return;
    busy = true; go.disabled = true; q.value = ""; grow(); document.body.classList.add("chat"); note.textContent = "";
    var u = el("div", "m u", msg); u.dir = "auto"; var b = el("div", "m b"); b.appendChild(el("span", "st", "Soch raha hoon…")); list.append(u, b); window.scrollTo(0, document.body.scrollHeight);
    var text = "", dl = null;
    try {
      var r = await fetch("/api/toolsmith", { method: "POST", headers: { "Content-Type": "application/json", "X-Client-Id": cid }, body: JSON.stringify({ message: msg, history: hist.slice(-6) }) });
      if (!r.ok) throw new Error(r.status === 429 ? "Thori der baad try karein." : "Server error " + r.status);
      var rd = r.body.getReader(), dec = new TextDecoder(), buf = "";
      for (;;) {
        var c = await rd.read(); if (c.done) break; buf += dec.decode(c.value, { stream: true });
        var parts = buf.split("\n\n"); buf = parts.pop();
        parts.forEach(function (blk) {
          var ev = (blk.match(/^event: (.*)$/m) || [])[1], dt = (blk.match(/^data: (.*)$/m) || [])[1], d; try { d = JSON.parse(dt); } catch (x) { return; }
          if (ev === "token") { text += d; render(b, text); window.scrollTo(0, document.body.scrollHeight); }
          else if (ev === "step") { note.textContent = d.msg; }
          else if (ev === "meta") { note.textContent = "Aaj ki free requests baqi: " + d.left_today; }
          else if (ev === "check") { note.textContent = d.lines.join(" | "); }
          else if (ev === "download" && /^\/api\/dl\/[\w-]+$/.test(d.url)) { dl = d; }
          else if (ev === "error") { text = d.message || "Error"; b.textContent = text; }
        });
      }
      if (dl) { var a = el("a", "dl", "⬇ Download " + dl.name); a.href = dl.url; a.download = dl.name; b.appendChild(a); }
      hist.push({ role: "user", content: msg.slice(0, 3000) }, { role: "assistant", content: text.slice(0, 3000) });
    } catch (x) { b.textContent = String(x.message || x); }
    busy = false; go.disabled = false; q.focus();
  });
  q.focus();
})();
