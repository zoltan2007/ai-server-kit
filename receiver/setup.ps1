# 製作人端收模型伺服器：建一份 config.json（隨機通關碼）並印出怎麼跑。
param([int]$Port = 8790, [string]$Dir = "received")
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfgPath = Join-Path $here "config.json"

if (Test-Path $cfgPath) {
    $token = (Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).token
    Write-Host "config.json 已存在，沿用原本的通關碼。" -ForegroundColor Yellow
} else {
    $chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789".ToCharArray()
    $bytes = New-Object byte[] 28
    (New-Object Security.Cryptography.RNGCryptoServiceProvider).GetBytes($bytes)
    $token = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
    $cfg = [ordered]@{ host = "0.0.0.0"; port = $Port; token = $token; dir = $Dir; recv_path = "/upload" }
    [IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    Write-Host "已建立 $cfgPath" -ForegroundColor Green
}

Write-Host ""
Write-Host "  通關碼（填到面板 config.json 的 share.token）：" -ForegroundColor White
Write-Host "  $token" -ForegroundColor Yellow
Write-Host ""
Write-Host "  啟動：  python `"$here\server.py`" --config `"$cfgPath`"" -ForegroundColor White
Write-Host "  清單頁：http://127.0.0.1:$Port/" -ForegroundColor White
Write-Host ""
Write-Host "  面板那邊設： \"share\": { \"url\": \"https://你的網域/upload\", \"label\": \"製作人\", \"token\": \"$token\" }" -ForegroundColor Gray
Write-Host "  ⚠ 對外公開請用 HTTPS（反向代理），不要把純 HTTP 的埠直接開到網路上。" -ForegroundColor DarkGray
