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

## D. Oracle Cloud Always Free VM (sab se behtar: kabhi sota nahi, disk mehfooz, crawler 24/7)
Oracle ki docs ke mutabiq (jun 2026): Always Free mein Arm A1 total **2 OCPU + 12 GB RAM** (pehle 4 OCPU/24 GB tha), ya 2 AMD Micro VM (1 GB RAM),
200 GB block storage, 10 TB/month outbound. Free account ke liye credit card verification lagti hai; kuch regions mein A1 "out of capacity" aa sakta hai.
1. cloud.oracle.com par account banayein (home region soch kar chunein), phir Compute -> Instances -> Create instance:
   image **Ubuntu 22.04/24.04**, shape **VM.Standard.A1.Flex** (2 OCPU, 12 GB) ya AMD Micro. SSH key download karein.
2. Networking -> VCN -> Security List -> Ingress rule: Source `0.0.0.0/0`, TCP, port **8000**.
3. VM par SSH karke: `curl -fsSL https://raw.githubusercontent.com/musman550/talashly/main/deploy/oracle-setup.sh | bash`
   (Docker install, code download, firewall, `docker compose up -d` — restart/reboot par khud chalu). **Ye script maine asli Oracle VM par chala kar test nahi ki**; error aaye to output bhej dein.
4. Browser: `http://<VM-public-IP>:8000`. HTTPS ke liye free Cloudflare Tunnel (option A wala `cloudflared`) VM par chalayein, phir `.env` mein `SS_SITE_URL=https://...` daal kar `sudo docker compose up -d`.
5. Oracle **idle** instance wapas le sakta hai (7 din tak CPU/network/memory 20% se kam). Crawler chalta rahe to theek; sirf ping se ye nahi rukta.

## Sleep/alert ke liye alag repo: `talashly-monitor`
Alag chhota GitHub repo jo har 10 minute server ko ping karta hai (Render jaise sone wale hosts ke liye) aur down hone par GitHub Issue + email alert deta hai.
Oracle VM ko ping ki zarurat nahi, lekin alert ke liye ye phir bhi kaam ka hai. Files: `talashly-monitor` repo ki README dekhein.

## Zaroori settings (live par)
- `[server] require_key = false` rakhein agar sab ko bina key chat karni ho; API (`/v1`) hamesha key maangti hai.
- Quota/limits `config.toml` ke `[chat]` section mein (`daily_quota_anon`, `daily_quota_key`, `max_concurrent`, `queue_max`);
  `0` quota = unlimited. Limits server ko dump hone se bachati hain.
- Admin key `data/admin_key.txt` mein hoti hai (hosting par logs/shell se lein); kabhi public na karein.
- `SS_TRUST_PROXY=1` sirf proxy ke peeche (Render/HF/Cloudflare) lagayein, warna rate-limit IP spoof ho sakta hai.


## Auto-update (Oracle VM)
`deploy/oracle-setup.sh` ek systemd timer lagata hai jo har 5 minute mein GitHub `main` dekhta hai; naya code ho to
khud rebuild + restart karta hai (data `./data` safe rehta hai), aur health fail ho to container dobara chalata hai.
Ek dafa script dobara chalayein (purane VM par): `curl -fsSL https://raw.githubusercontent.com/musman550/talashly/main/deploy/oracle-setup.sh | bash`


## Apna AI writer (no API, no key)
Open-source Qwen3 1.7B (Apache-2.0) via llama.cpp, running on your own VM. Needs a VM with 6 GB+ RAM (Oracle Always Free A1: 2 OCPU / 12 GB).
On the VM: `bash ~/talashly/deploy/enable-llm.sh`. The script refuses on 1 GB Micro VMs. Talashly then sends tasks and questions the index cannot answer to it.
