# 顯示 AI 主機面板的網址與登入碼
param([string]$InstallDir = $PSScriptRoot)
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$cfgPath = Join-Path $InstallDir "config.json"
if (-not (Test-Path $cfgPath)) { Write-Host "找不到 $cfgPath" -ForegroundColor Red; exit 1 }
$cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host ""
Write-Host "  面板：http://127.0.0.1:$($cfg.port)/" -ForegroundColor White
if ($cfg.host -eq "0.0.0.0") {
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notmatch "^(127\.|169\.254\.)" -and $_.PrefixOrigin -ne "WellKnown" } |
        ForEach-Object { Write-Host "  手機／其他電腦：http://$($_.IPAddress):$($cfg.port)/" -ForegroundColor White }
}
Write-Host "  登入碼：$($cfg.token)" -ForegroundColor Yellow
Write-Host ""
