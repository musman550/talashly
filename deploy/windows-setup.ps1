# Windows PowerShell: ek hi baar paste/chalayein. Key khud dhoondta, permissions khud theek karta, VM par sab setup + update khud karta.
$ErrorActionPreference = "Stop"
$ip = "137.23.39.13"
$user = "ubuntu"
$src = Get-ChildItem -Path "$HOME\Downloads","$HOME\Desktop","$HOME\Documents","$HOME\.ssh" -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -like "ssh-key-*" -or $_.Extension -eq ".key" } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $src) { Write-Host "Key file nahi mili (ssh-key-....key). Usay Downloads mein rakhein aur dobara chalayein." -ForegroundColor Red; return }
New-Item -ItemType Directory -Force "$HOME\.ssh" | Out-Null
$key = "$HOME\.ssh\talashly.key"
Copy-Item $src.FullName $key -Force
icacls $key /inheritance:r | Out-Null
icacls $key /grant:r "$($env:USERNAME):(R)" | Out-Null
Write-Host "Key mili: $($src.Name)" -ForegroundColor Green
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
