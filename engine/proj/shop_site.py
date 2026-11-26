"""Shopify-style shop website: HTML + CSS + JavaScript (koi build step nahi; index.html double-click)."""


def files(brand: str = "Your Store") -> dict[str, str]:
    brand = (brand or "Your Store").strip()[:40]
    f: dict[str, str] = {}
    f["index.html"] = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__BRAND__ - Online Store</title>
<meta name="description" content="Shop quality products at __BRAND__. Fast delivery and easy returns.">
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="top">
  <a class="brand" href="#">__BRAND__</a>
  <input id="search" type="search" placeholder="Search products..." aria-label="Search products">
  <button id="cartBtn" class="cart-btn" aria-label="Open cart">Cart <span id="cartCount">0</span></button>
</header>
<section class="hero"><h1>New season, new favourites</h1><p>Free delivery over $50 &middot; 30-day returns</p><a class="btn" href="#shop">Shop now</a></section>
<main id="shop">
  <div class="bar">
    <div id="cats" class="cats" role="tablist"></div>
    <select id="sort" aria-label="Sort"><option value="pop">Popular</option><option value="low">Price: low to high</option><option value="high">Price: high to low</option></select>
  </div>
  <div id="grid" class="grid"></div>
  <p id="empty" class="empty" hidden>No products match your search.</p>
</main>
<aside id="drawer" class="drawer" aria-hidden="true">
  <div class="drawer-head"><h2>Your cart</h2><button id="closeCart" aria-label="Close cart">&times;</button></div>
  <div id="cartItems"></div>
  <div class="drawer-foot"><div class="total"><span>Total</span><strong id="total">$0.00</strong></div><button id="checkout" class="btn full">Checkout</button></div>
</aside>
<div id="overlay" class="overlay" hidden></div>
<dialog id="dlg"><form method="dialog" id="orderForm"><h2>Checkout</h2>
  <label>Full name<input required name="n" autocomplete="name"></label><label>Email<input required type="email" name="e" autocomplete="email"></label>
  <label>Address<input required name="a" autocomplete="street-address"></label>
  <p class="note">Demo store: no payment is taken. Connect Stripe or Shopify Buy Button to go live (see README).</p>
  <div class="row"><button value="cancel" class="btn ghost">Cancel</button><button value="ok" class="btn">Place order</button></div></form></dialog>
<div id="toast" class="toast" role="status"></div>
<footer><p>&copy; <span id="yr"></span> __BRAND__. All rights reserved.</p></footer>
<script src="app.js"></script>
</body>
</html>
'''.replace("__BRAND__", brand)
    f["style.css"] = ''':root{--bg:#fff;--fg:#0f172a;--mut:#64748b;--card:#f8fafc;--line:#e2e8f0;--acc:#4f46e5;--acc2:#4338ca}
@media (prefers-color-scheme:dark){:root{--bg:#0b1020;--fg:#e2e8f0;--mut:#94a3b8;--card:#131a30;--line:#24304f;--acc:#818cf8;--acc2:#a5b4fc}}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}
.top{position:sticky;top:0;z-index:5;display:flex;gap:12px;align-items:center;padding:12px 20px;background:var(--bg);border-bottom:1px solid var(--line)}
.brand{font-weight:800;font-size:1.25rem;color:var(--acc);text-decoration:none}#search{flex:1;max-width:420px;margin:0 auto;padding:10px 14px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.cart-btn{border:1px solid var(--line);background:var(--card);color:var(--fg);padding:8px 14px;border-radius:999px;cursor:pointer;font-weight:600}.cart-btn span{background:var(--acc);color:#fff;border-radius:999px;padding:1px 8px;margin-left:4px;font-size:.85rem}
.hero{text-align:center;padding:64px 20px;background:linear-gradient(135deg,#eef2ff,#fdf2f8)}@media (prefers-color-scheme:dark){.hero{background:linear-gradient(135deg,#171d3d,#2a1736)}}
.hero h1{font-size:clamp(2rem,6vw,3.4rem);margin:0 0 8px}.hero p{color:var(--mut);margin:0 0 20px}
.btn{display:inline-block;background:var(--acc);color:#fff;border:0;border-radius:12px;padding:12px 22px;font-weight:700;cursor:pointer;text-decoration:none}.btn:hover{background:var(--acc2)}.btn.ghost{background:transparent;color:var(--fg);border:1px solid var(--line)}.btn.full{width:100%}
main{max-width:1100px;margin:0 auto;padding:28px 20px}.bar{display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap;margin-bottom:20px}
.cats{display:flex;gap:8px;flex-wrap:wrap}.cat{border:1px solid var(--line);background:var(--card);color:var(--fg);padding:7px 14px;border-radius:999px;cursor:pointer}.cat.on{background:var(--acc);color:#fff;border-color:var(--acc)}
select{padding:8px 12px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:18px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;display:flex;flex-direction:column}
.img{aspect-ratio:4/3;display:grid;place-items:center;font-size:3.2rem}.body{padding:14px;display:flex;flex-direction:column;gap:6px;flex:1}.body h3{margin:0;font-size:1.02rem}.muted{color:var(--mut);font-size:.88rem}
.price{font-weight:800;font-size:1.1rem}.card .btn{margin-top:auto;padding:10px;width:100%}.empty{text-align:center;color:var(--mut)}
.drawer{position:fixed;top:0;right:0;height:100%;width:min(400px,100%);background:var(--bg);border-left:1px solid var(--line);transform:translateX(100%);transition:transform .25s;z-index:20;display:flex;flex-direction:column}.drawer.open{transform:none}
.drawer-head{display:flex;justify-content:space-between;align-items:center;padding:16px 20px;border-bottom:1px solid var(--line)}.drawer-head h2{margin:0}.drawer-head button{font-size:1.6rem;background:none;border:0;color:var(--fg);cursor:pointer}
#cartItems{flex:1;overflow:auto;padding:12px 20px}.line{display:grid;grid-template-columns:48px 1fr auto;gap:10px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}.line .t{font-size:1.8rem;text-align:center}
.qty{display:flex;align-items:center;gap:6px;margin-top:4px}.qty button{width:26px;height:26px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}.rm{background:none;border:0;color:var(--mut);cursor:pointer;font-size:.85rem}
.drawer-foot{padding:16px 20px;border-top:1px solid var(--line)}.total{display:flex;justify-content:space-between;margin-bottom:12px;font-size:1.1rem}.overlay{position:fixed;inset:0;background:#0006;z-index:10}
dialog{border:1px solid var(--line);border-radius:16px;background:var(--bg);color:var(--fg);width:min(420px,92%)}dialog::backdrop{background:#0008}dialog label{display:block;margin:10px 0;font-size:.9rem}
dialog input{display:block;width:100%;margin-top:4px;padding:10px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--fg)}.row{display:flex;gap:10px;justify-content:flex-end}.note{font-size:.82rem;color:var(--mut)}
.toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,80px);background:var(--fg);color:var(--bg);padding:10px 18px;border-radius:999px;opacity:0;transition:.25s;z-index:30}.toast.show{transform:translate(-50%,0);opacity:1}
footer{text-align:center;color:var(--mut);padding:30px 0;border-top:1px solid var(--line);margin-top:30px}
'''
    f["app.js"] = '''"use strict";
const PRODUCTS = [
  {id:1,name:"Classic Hoodie",cat:"Clothing",price:49.0,pop:95,e:"\\u{1F9E5}",c:"#c7d2fe"},
  {id:2,name:"Everyday Sneakers",cat:"Shoes",price:79.0,pop:99,e:"\\u{1F45F}",c:"#fbcfe8"},
  {id:3,name:"Canvas Backpack",cat:"Bags",price:59.0,pop:80,e:"\\u{1F392}",c:"#bbf7d0"},
  {id:4,name:"Wireless Earbuds",cat:"Tech",price:89.0,pop:97,e:"\\u{1F3A7}",c:"#fde68a"},
  {id:5,name:"Smart Watch",cat:"Tech",price:129.0,pop:90,e:"\\u231A",c:"#bae6fd"},
  {id:6,name:"Denim Jacket",cat:"Clothing",price:69.0,pop:70,e:"\\u{1F454}",c:"#ddd6fe"},
  {id:7,name:"Leather Wallet",cat:"Bags",price:35.0,pop:75,e:"\\u{1F45B}",c:"#fed7aa"},
  {id:8,name:"Running Shoes",cat:"Shoes",price:95.0,pop:88,e:"\\u{1F45E}",c:"#a7f3d0"},
  {id:9,name:"Sunglasses",cat:"Accessories",price:29.0,pop:85,e:"\\u{1F576}",c:"#fecaca"},
  {id:10,name:"Ceramic Mug",cat:"Home",price:14.0,pop:60,e:"\\u2615",c:"#e9d5ff"},
  {id:11,name:"Desk Lamp",cat:"Home",price:39.0,pop:65,e:"\\u{1F4A1}",c:"#fef08a"},
  {id:12,name:"Bluetooth Speaker",cat:"Tech",price:59.0,pop:92,e:"\\u{1F50A}",c:"#99f6e4"}
];
const $ = (id) => document.getElementById(id);
const money = (n) => "$" + n.toFixed(2);
const KEY = "shop-cart-v1";
let cart = {}, cat = "All", q = "", sort = "pop";
try { cart = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { cart = {}; }
function save() { try { localStorage.setItem(KEY, JSON.stringify(cart)); } catch (e) { /* private mode */ } }
function toast(t) { const el = $("toast"); el.textContent = t; el.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove("show"), 1600); }
function cats() {
  const list = ["All", ...new Set(PRODUCTS.map((p) => p.cat))];
  $("cats").innerHTML = "";
  list.forEach((c) => { const b = document.createElement("button"); b.className = "cat" + (c === cat ? " on" : ""); b.textContent = c; b.onclick = () => { cat = c; cats(); grid(); }; $("cats").appendChild(b); });
}
function grid() {
  let items = PRODUCTS.filter((p) => (cat === "All" || p.cat === cat) && p.name.toLowerCase().includes(q));
  items.sort((a, b) => sort === "low" ? a.price - b.price : sort === "high" ? b.price - a.price : b.pop - a.pop);
  const g = $("grid"); g.innerHTML = "";
  items.forEach((p) => {
    const d = document.createElement("article"); d.className = "card";
    d.innerHTML = `<div class="img" style="background:${p.c}">${p.e}</div><div class="body"><h3></h3><span class="muted"></span><span class="price"></span><button class="btn">Add to cart</button></div>`;
    d.querySelector("h3").textContent = p.name; d.querySelector(".muted").textContent = p.cat; d.querySelector(".price").textContent = money(p.price);
    d.querySelector("button").onclick = () => add(p.id);
    g.appendChild(d);
  });
  $("empty").hidden = items.length > 0;
}
function add(id) { cart[id] = (cart[id] || 0) + 1; save(); renderCart(); toast("Added to cart"); }
function setQty(id, n) { if (n <= 0) delete cart[id]; else cart[id] = Math.min(n, 99); save(); renderCart(); }
function renderCart() {
  const box = $("cartItems"); box.innerHTML = ""; let total = 0, count = 0;
  Object.keys(cart).forEach((id) => {
    const p = PRODUCTS.find((x) => x.id === +id); if (!p) { delete cart[id]; return; }
    const n = cart[id]; total += p.price * n; count += n;
    const d = document.createElement("div"); d.className = "line";
    d.innerHTML = `<div class="t" style="background:${p.c};border-radius:10px">${p.e}</div><div><div></div><div class="qty"><button aria-label="Less">-</button><span>${n}</span><button aria-label="More">+</button></div></div><div><strong>${money(p.price * n)}</strong><br><button class="rm">Remove</button></div>`;
    d.children[1].children[0].textContent = p.name;
    const [minus, plus] = d.querySelectorAll(".qty button");
    minus.onclick = () => setQty(p.id, n - 1); plus.onclick = () => setQty(p.id, n + 1); d.querySelector(".rm").onclick = () => setQty(p.id, 0);
    box.appendChild(d);
  });
  if (!count) box.innerHTML = '<p class="empty">Your cart is empty.</p>';
  $("total").textContent = money(total); $("cartCount").textContent = count; $("checkout").disabled = !count;
}
function openCart(o) { $("drawer").classList.toggle("open", o); $("overlay").hidden = !o; $("drawer").setAttribute("aria-hidden", String(!o)); }
$("cartBtn").onclick = () => openCart(true); $("closeCart").onclick = () => openCart(false); $("overlay").onclick = () => openCart(false);
$("search").oninput = (e) => { q = e.target.value.trim().toLowerCase(); grid(); }; $("sort").onchange = (e) => { sort = e.target.value; grid(); };
$("checkout").onclick = () => { openCart(false); $("dlg").showModal(); };
$("dlg").addEventListener("close", () => {
  if ($("dlg").returnValue === "ok") { cart = {}; save(); renderCart(); toast("Order placed. Thank you!"); }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") openCart(false); });
$("yr").textContent = new Date().getFullYear();
cats(); grid(); renderCart();
'''
    f["README.md"] = f"""# {brand} - shop website (HTML, CSS, JavaScript)

Open **index.html** in your browser. No build step, no server needed.

## What it has
Product grid, category filter, search, sorting, cart drawer with quantities (saved in the browser), checkout form (demo), dark mode, mobile-friendly layout.

## Make it yours
- Products: edit the `PRODUCTS` list at the top of **app.js** (name, category, price, emoji, colour). For photos, replace the emoji block in `grid()` with an `<img src=...>`.
- Colours: change the variables at the top of **style.css**.
- Brand name and texts: **index.html**.

## Go live
- Host the folder free on GitHub Pages, Netlify or Vercel.
- Take real payments: replace the demo checkout with Stripe Payment Links or Shopify Buy Button. Do not store card details yourself.

See **PLAN.md** for next steps.
"""
    f["PLAN.md"] = """# Plan: shop website

1. Phase 1 (done): catalogue, search, filter, sort, cart, demo checkout, dark mode.
2. Phase 2: real product photos, product detail page, discount code field.
3. Phase 3: payments (Stripe Payment Links), order emails, analytics.
4. Phase 4: move products to a JSON file or Shopify/Headless CMS, SEO pages per product.
"""
    return f
