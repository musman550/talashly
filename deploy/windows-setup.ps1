# Windows PowerShell: poora block ek saath paste karein. Key khud dhoondta, har key VM par try karta, phir setup + update khud karta.
$ErrorActionPreference = "Continue"
$ip = "137.23.39.13"
$cands = Get-ChildItem "$HOME\Downloads","$HOME\Desktop","$HOME\Documents","$HOME\.ssh" -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -ne ".pub" -and $_.Name -like "ssh-key-*" -and $_.Length -lt 20000 } | Where-Object { (Get-Content $_.FullName -TotalCount 1 -ErrorAction SilentlyContinue) -like "-----BEGIN*" } | Sort-Object LastWriteTime -Descending
if (-not $cands) { Write-Host "Private key (ssh-key-....key) nahi mili." -ForegroundColor Red; return }
$remote = "curl -fsSL https://raw.githubusercontent.com/musman550/talashly/main/deploy/oracle-setup.sh | bash; cd ~/talashly && sudo bash deploy/auto-update.sh; sleep 25; sudo docker compose ps; curl -s http://127.0.0.1:8000/api/health; echo; echo === TAYYAR ==="
$done = $false
foreach ($c in $cands) {
  $k = Join-Path $env:TEMP ("tl-" + (Get-Random) + ".key")
  Copy-Item $c.FullName $k -Force
  icacls $k /inheritance:r | Out-Null
  icacls $k /grant:r ($env:USERNAME + ":(R)") | Out-Null
  $t = ssh -i $k -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 ("ubuntu@" + $ip) "echo ok" 2>&1
  if ($LASTEXITCODE -eq 0) {
    Write-Host ("Sahi key: " + $c.Name) -ForegroundColor Green
    ssh -i $k -o ServerAliveInterval=30 ("ubuntu@" + $ip) $remote
    $done = $true
    break
  }
  Write-Host ("Match nahi hui: " + $c.Name + " -> " + ($t | Out-String).Trim()) -ForegroundColor Yellow
}
if (-not $done) { Write-Host "Koi key VM se match nahi hui, ya VM band/unreachable hai (Oracle Console mein VM 'Running' check karein)." -ForegroundColor Red }
