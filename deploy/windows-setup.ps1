# Windows PowerShell: ek hi baar paste/chalayein. Key khud dhoondta, permissions khud theek karta, VM par sab setup + update khud karta.
$ErrorActionPreference = "Stop"
$ip = "137.23.39.13"
$user = "ubuntu"
$cands = Get-ChildItem -Path "$HOME\Downloads","$HOME\Desktop","$HOME\Documents","$HOME\.ssh" -File -Recurse -Depth 2 -ErrorAction SilentlyContinue |
  Where-Object { $_.Extension -ne ".pub" -and ($_.Name -like "ssh-key-*" -or $_.Extension -in ".key",".pem") -and $_.Length -lt 20000 -and $_.Name -ne "talashly.key" } |
  Where-Object { (Get-Content $_.FullName -TotalCount 1 -ErrorAction SilentlyContinue) -like "-----BEGIN*" } |
  Sort-Object LastWriteTime -Descending
if (-not $cands) { Write-Host "Private key nahi mili (ssh-key-....key, .pub nahi). Usay Downloads mein rakhein." -ForegroundColor Red; return }
New-Item -ItemType Directory -Force "$HOME\.ssh" | Out-Null
$key = $null
foreach ($c in $cands) {
  $k = "$HOME\.ssh\talashly.key"
  if ($c.FullName -ne $k) { Copy-Item $c.FullName $k -Force }
  icacls $k /inheritance:r | Out-Null
  icacls $k /grant:r "$($env:USERNAME):(R)" | Out-Null
  ssh -i $k -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$user@$ip" "echo ok" 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { $key = $k; Write-Host "Sahi key mili: $($c.Name)" -ForegroundColor Green; break }
  Write-Host "Ye key VM se match nahi hui: $($c.Name)" -ForegroundColor Yellow
}
if (-not $key) { Write-Host "Koi key VM se match nahi hui. Oracle Console mein VM ki key (ya VM ka IP) check karein." -ForegroundColor Red; return }
$remote = @'
set -e
curl -fsSL https://raw.githubusercontent.com/musman550/talashly/main/deploy/oracle-setup.sh | bash
cd ~/talashly && sudo bash deploy/auto-update.sh || true
sleep 25
sudo docker compose ps
curl -s http://127.0.0.1:8000/api/health; echo
sudo cat data/autopilot/report.md 2>/dev/null | head -15 || true
echo "=== TAYYAR: ab VM har 5 minute mein khud update hoti hai, aap ko kuch nahi karna ==="
'@ -replace "`r", ""
$remote | ssh -i $key -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 "$user@$ip" "bash -s"
