# Talashly ko live karna (sab ko chat + API dene ke liye)

GitHub Pages sirf static website chalata hai, is liye Talashly ka asli server (Python) kahin aur chalana hota hai.
Teen raaste, sab free se shuru:

## A. Apne PC se, free public link (sab se aasan, 24/7 sirf jab PC on ho)
1. `INSTALL.bat`, phir `START.bat` (server http://127.0.0.1:8000 par).
2. Cloudflare Tunnel (free, account ke baghair "quick tunnel"): https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
   se `cloudflared` install karein, phir:  `cloudflared tunnel --url http://localhost:8000`
3. Jo `https://....trycloudflare.com` link mile wahi aap ki live website hai. Link har dafa badalta hai.
4. `config.toml` mein `[site] url = "https://....trycloudflare.com"` daalein taake SEO/sitemap sahi hon, aur server restart karein.

## B. Render.com (free plan, repo se seedha)
1. render.com par GitHub se login, **New -> Blueprint**, repo `musman550/talashly` chunein (`render.yaml` khud detect hogi).
2. Deploy ke baad Environment mein `SS_SITE_URL` = apna `https://<naam>.onrender.com` daalein, redeploy.
3. Limits (free plan): 15 min idle par service so jati hai (crawler bhi ruk jata hai), restart par `data/` mit jata hai
   (index seeds se dobara bantaa hai). 24/7 ke liye paid plan + disk (render.yaml mein comment mein hai).

## C. Hugging Face Spaces (Docker, free)
1. huggingface.co -> New Space -> SDK **Docker**. Repo ki files upload/push karein.
2. Space ke `README.md` front-matter mein `app_port: 8000` rakhein. Variables mein `SS_SITE_URL` aur `SS_TRUST_PROXY=1` daalein.

## Zaroori settings (live par)
- `[server] require_key = false` rakhein agar sab ko bina key chat karni ho; API (`/v1`) hamesha key maangti hai.
- Quota/limits `config.toml` ke `[chat]` section mein (`daily_quota_anon`, `daily_quota_key`, `max_concurrent`, `queue_max`);
  `0` quota = unlimited. Limits server ko dump hone se bachati hain.
- Admin key `data/admin_key.txt` mein hoti hai (hosting par logs/shell se lein); kabhi public na karein.
- `SS_TRUST_PROXY=1` sirf proxy ke peeche (Render/HF/Cloudflare) lagayein, warna rate-limit IP spoof ho sakta hai.
