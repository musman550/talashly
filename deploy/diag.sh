#!/usr/bin/env bash
# VM par chalta hai: kaun sa code live hai, load, build chal raha hai?, mesh, aur asli chat ka waqt. Sirf padhta hai, kuch badalta nahi.
cd ~/talashly 2>/dev/null || cd "$(dirname "$0")/.."
echo "== code (VM)";            git log -1 --format='%h %cd %s' --date=iso 2>/dev/null
echo "== code (GitHub)";        git fetch -q origin main 2>/dev/null; git log -1 --format='%h %cd %s' --date=iso origin/main 2>/dev/null
echo "== load / RAM";           uptime; free -m | sed -n 1,3p
echo "== docker build chal raha?"; (pgrep -af 'docker.*build|buildx|buildkit' | head -3) || true; echo "(upar kuch na ho to build nahi chal raha)"
echo "== CPU steal/load (st = Oracle ne CPU roka; wa = disk intezar)"; vmstat 1 4 | tail -4
echo "== sab se bhari process"; ps aux --sort=-%cpu | head -5 | cut -c1-140
echo "== 6 baar mesh/status ka waqt (atakna dikhega)"; for i in 1 2 3 4 5 6; do curl -s -o /dev/null -m 20 -w '%{time_total}s ' http://127.0.0.1:8000/api/mesh/status; sleep 1; done; echo
echo "== containers";           sudo docker compose ps 2>/dev/null | cut -c1-150
echo "== CPU/RAM per container"; sudo docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' 2>/dev/null
echo "== image (GHCR se aayi ya local build?)"; sudo docker images --format '{{.Repository}}:{{.Tag}} {{.CreatedSince}}' | head -4
echo "== update log";           sudo journalctl --no-pager -n 600 2>/dev/null | grep -iE 'code update|prebuilt|build|health fail' | tail -6
echo "== health + mesh";        curl -s -m 20 -w '  [%{time_total}s]\n' http://127.0.0.1:8000/api/mesh/status
echo "== chat (Deep, email) - asli waqt"
cat > /tmp/diag_body.json <<'J'
{"message":"write a short email to my landlord about a leaking roof","private":true,"model":"mus-7.01-deep","history":[]}
J
out=$(curl -s -N -m 150 -X POST http://127.0.0.1:8000/api/chat -H 'Content-Type: application/json' -H 'X-Client-Id: diag'$RANDOM -d @/tmp/diag_body.json -w '\nTIMES first_byte=%{time_starttransfer}s total=%{time_total}s\n')
echo "$out" | grep -oE '"mode": *"[^"]*"' | tail -1
echo "$out" | grep -E '^TIMES'
echo "$out" | grep -E '^data:' | head -c 400; echo
echo "== search container log (aakhri)"; sudo docker compose logs --no-color --tail 8 search 2>/dev/null | cut -c1-200
