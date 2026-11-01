<#
解除安裝 AI 主機面板。預設保留 Ollama 和已下載的模型。

  .\uninstall.ps1                 移除面板（會問）
  .\uninstall.ps1 -RemoveOllama   連 Ollama 一起移除（模型檔還是留著，位置會印出來）
  .\uninstall.ps1 -Yes            不問
#>
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\AIServerKit",
    [switch]$RemoveOllama,
    [switch]$Yes
)
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$TaskName = "AIServerKit"
$RuleName = "AI Server Kit 面板"

if (-not $Yes) {
    $a = Read-Host "確定要移除 AI 主機面板（$InstallDir）？(y/N)"
    if ($a -notmatch "^[yY]") { Write-Host "取消"; exit 0 }
}

# 停掉面板：用面板自己寫的 PID
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已移除工作排程 $TaskName"
}
$pidFile = Join-Path $InstallDir "panel.pid"
if (Test-Path $pidFile) {
    $panelPid = [int](Get-Content $pidFile -Raw)
    $p = Get-Process -Id $panelPid -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -match "^python") { Stop-Process -Id $panelPid -Force; Write-Host "已停止面板（PID $panelPid）" }
}
$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "AI 主機面板.lnk"
Remove-Item $startup -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath("Programs")) "AI 主機面板") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath("Desktop")) "AI 主機面板.url") -ErrorAction SilentlyContinue

$rule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($rule) {
    try { $rule | Remove-NetFirewallRule -ErrorAction Stop; Write-Host "已移除防火牆規則" }
    catch { Write-Host "防火牆規則「$RuleName」要用系統管理員才能刪（不刪也沒關係）" -ForegroundColor Yellow }
}

Start-Sleep 1
Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path $InstallDir) { Write-Host "有些檔案還在使用中，沒刪乾淨：$InstallDir" -ForegroundColor Yellow }
else { Write-Host "已刪除 $InstallDir" }

if ($RemoveOllama) {
    $un = Join-Path $env:LOCALAPPDATA "Programs\Ollama\unins000.exe"
    Get-Process -Name "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
    if (Test-Path $un) { Start-Process $un -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES" -Wait; Write-Host "已移除 Ollama" }
    else { Write-Host "找不到 Ollama 的解除安裝程式，到「設定 → 應用程式」移除。" -ForegroundColor Yellow }
    $md = [Environment]::GetEnvironmentVariable("OLLAMA_MODELS", "User")
    if (-not $md) { $md = Join-Path $env:USERPROFILE ".ollama\models" }
    Write-Host "模型檔留在 $md（不要的話自己刪，通常很大）"
}
Write-Host "完成"
