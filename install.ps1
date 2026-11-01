<#
AI 主機一鍵安裝（Windows 10／11、Windows Server 2019 以上）

裝 Ollama（可指定版本）→ 放好面板 → 開機自動啟動 → 打開瀏覽器。
直接雙擊「安裝.bat」會一步一步問；也可以全部用參數：

  .\install.ps1 -Quiet -OllamaVersion 0.40.13 -Models qwen3.5:9b -Lan
  .\install.ps1 -ListVersions          列出 Ollama 最近的版本
#>
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\AIServerKit",
    [string]$OllamaVersion = "",         # 空白＝最新版；例如 0.40.13
    [int]$PanelPort = 8780,
    [switch]$Lan,                        # 讓同一個網路的手機／電腦也能連
    [string]$ModelsDir = "",             # 模型放哪裡（預設 C:\Users\你\.ollama\models）
    [string[]]$Models = @(),             # 裝好先下載的模型，例如 qwen3.5:9b
    [switch]$NoTerminal,                 # 面板不要終端機
    [switch]$SkipOllama,                 # 不裝／不更新 Ollama
    [switch]$UseSystemPython,            # 用這台已經有的 Python，不另外下載
    [switch]$NoAutoStart,                # 不要開機自動啟動
    [switch]$NoShortcuts,                # 不建開始功能表／桌面捷徑
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [switch]$ListVersions,
    [switch]$Quiet                       # 不問問題、不開瀏覽器
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # PowerShell 5.1 的進度條會讓下載慢十倍
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$PyVer = "3.12.10"
$TaskName = "AIServerKit"
$RuleName = "AI Server Kit 面板"

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Step($msg) { Write-Host ""; Write-Host "▶ $msg" -ForegroundColor Cyan }
function Ask($q, $def = "") {
    if ($Quiet) { return $def }
    $a = Read-Host $q
    if ([string]::IsNullOrWhiteSpace($a)) { return $def }
    return $a.Trim()
}
function IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Download($url, $dst) {
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source -L --fail --retry 3 -o $dst $url
        if ($LASTEXITCODE -ne 0) { throw "下載失敗：$url" }
    } else {
        Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing
    }
}
function OllamaUp {
    try { Invoke-RestMethod "$OllamaUrl/api/version" -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}
function FindOllama {
    $c = Get-Command ollama -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $p = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $p) { return $p }
    return $null
}
function OllamaVer($exe) {
    try {
        $o = (& $exe --version 2>$null) -join " "
        if ($o -match "(\d+\.\d+\.\d+)") { return $Matches[1] }
    } catch {}
    return $null
}

# ── 只列版本 ──
if ($ListVersions) {
    $rel = Invoke-RestMethod "https://api.github.com/repos/ollama/ollama/releases?per_page=20" -TimeoutSec 20
    Say "Ollama 最近的版本（裝的時候用 -OllamaVersion 指定）：" "Cyan"
    foreach ($r in $rel) {
        $tag = $r.tag_name -replace "^v", ""
        $kind = ""
        if ($r.prerelease) { $kind = "（測試版）" }
        Say ("  {0,-12} {1}{2}" -f $tag, ([datetime]$r.published_at).ToString("yyyy-MM-dd"), $kind)
    }
    exit 0
}

Write-Host ""
Write-Host "  AI 主機一鍵安裝" -ForegroundColor White
Write-Host "  ───────────────" -ForegroundColor DarkGray
if (-not [Environment]::Is64BitOperatingSystem) { throw "只支援 64 位元 Windows" }

# ── 硬體 ──
Step "檢查硬體"
$ramBytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
$vramBytes = 0
$gpuNames = @()
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $rows = & $smi.Source --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null
    foreach ($row in @($rows)) {
        $p = $row -split ","
        if ($p.Count -ge 2) { $gpuNames += $p[0].Trim(); $vramBytes += [double]$p[1].Trim() * 1MB }
    }
}
Say ("  系統記憶體 {0:N0} GB" -f ($ramBytes / 1GB))
if ($gpuNames.Count) { Say ("  NVIDIA 顯示卡：{0}（共 {1:N0} GB）" -f ($gpuNames -join "、"), ($vramBytes / 1GB)) "Green" }
else { Say "  沒有偵測到 NVIDIA 顯示卡：模型會用 CPU 跑（比較慢）。AMD 顯示卡 Ollama 也支援，但面板不顯示細節。" "Yellow" }

# 依顯示卡大小推薦模型（從 app\catalog.json 的「通用聊天」挑放得下的最大三個）
$catalog = Get-Content (Join-Path $Here "app\catalog.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$cands = @()
foreach ($g in $catalog.groups) {
    if ($g.id -ne "chat") { continue }
    foreach ($m in $g.models) { foreach ($t in $m.tags) { $cands += [pscustomobject]@{ Name = "$($m.name):$($t.tag)"; Size = [double]$t.size } } }
}
if ($vramBytes -gt 0) { $budget = $vramBytes } else { $budget = [math]::Min($ramBytes * 0.5, 6e9) }
$recommend = @($cands | Where-Object { $_.Size * 1.15 + 0.7e9 -le $budget } | Sort-Object Size -Descending | Select-Object -First 3)

# ── 問問題 ──
if (-not $Quiet) {
    Step "設定（直接按 Enter 用預設值）"
    if (-not $SkipOllama -and -not $OllamaVersion) {
        $v = Ask "  Ollama 版本 [Enter＝最新版，輸入 list 看有哪些版本]" ""
        if ($v -eq "list") {
            & $MyInvocation.MyCommand.Path -ListVersions
            $v = Ask "  要裝哪個版本 [Enter＝最新版]" ""
        }
        $OllamaVersion = $v
    }
    if (-not $Lan) {
        $a = Ask "  讓同一個網路的手機／其他電腦也能連進面板？(y/N)" "n"
        if ($a -match "^[yY]") { $Lan = $true }
    }
    if (-not $Models.Count) {
        Say "  要先下載一個模型嗎？依你的硬體推薦：" "Gray"
        $i = 1
        foreach ($r in $recommend) { Say ("    {0}. {1}（{2:N1} GB）" -f $i, $r.Name, ($r.Size / 1e9)); $i++ }
        Say "    0. 先不要（之後在面板裡挑）"
        $a = Ask "  選號碼，或直接打模型名稱" "0"
        if ($a -match "^\d+$") {
            $n = [int]$a
            if ($n -ge 1 -and $n -le $recommend.Count) { $Models = @($recommend[$n - 1].Name) }
        } elseif ($a) { $Models = @($a) }
    }
}

# ── Ollama ──
if (-not $SkipOllama) {
    Step "Ollama"
    $exe = FindOllama
    $have = $null
    if ($exe) { $have = OllamaVer $exe }
    $want = $OllamaVersion -replace "^v", ""
    $need = (-not $exe) -or ($want -and $have -ne $want)
    if ($have) { Say "  已安裝 $have" }
    if ($need) {
        if ($want) { $url = "https://github.com/ollama/ollama/releases/download/v$want/OllamaSetup.exe"; $label = $want }
        else { $url = "https://ollama.com/download/OllamaSetup.exe"; $label = "最新版" }
        $setup = Join-Path $env:TEMP "OllamaSetup-$label.exe"
        Say "  下載 Ollama $label（約 1 GB，請稍候）…"
        Download $url $setup
        $sig = Get-AuthenticodeSignature $setup
        if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "Ollama") {
            throw "OllamaSetup.exe 的數位簽章不對（$($sig.Status)），停止安裝。"
        }
        Say "  簽章正確（$($sig.SignerCertificate.Subject -replace ',.*','')），安裝中…"
        if ($ModelsDir) { [Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $ModelsDir, "User"); $env:OLLAMA_MODELS = $ModelsDir }
        $p = Start-Process $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "Ollama 安裝程式回傳 $($p.ExitCode)" }
        Remove-Item $setup -ErrorAction SilentlyContinue
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        $exe = FindOllama
        Say "  裝好了：$(OllamaVer $exe)" "Green"
    } elseif ($ModelsDir -and $env:OLLAMA_MODELS -ne $ModelsDir) {
        [Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $ModelsDir, "User"); $env:OLLAMA_MODELS = $ModelsDir
        Say "  模型位置改成 $ModelsDir，重開 Ollama…"
        Get-Process -Name "ollama app", "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep 2
    }
    if ($ModelsDir) { New-Item -ItemType Directory -Force $ModelsDir | Out-Null }
    if (-not (OllamaUp)) {
        $dir = Split-Path -Parent $exe
        $appExe = @("ollama app.exe", "Ollama.exe") | ForEach-Object { Join-Path $dir $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($appExe) { Start-Process $appExe } else { Start-Process $exe -ArgumentList "serve" -WindowStyle Hidden }
        for ($i = 0; $i -lt 40 -and -not (OllamaUp); $i++) { Start-Sleep 1 }
    }
    if (OllamaUp) { Say "  Ollama 執行中（$OllamaUrl）" "Green" }
    else { Say "  Ollama 還沒起來，裝完後到開始功能表打開 Ollama。" "Yellow" }
}

# ── Python ──
Step "面板"
New-Item -ItemType Directory -Force $InstallDir | Out-Null
$py = $null
if ($UseSystemPython) {
    foreach ($cand in @("py", "python")) {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if (-not $c -or $c.Source -match "WindowsApps") { continue }
        $args0 = @()
        if ($cand -eq "py") { $args0 = @("-3") }
        try {
            $exePath = (& $c.Source @args0 -c "import sys;print(sys.executable if sys.version_info>=(3,8) else '')").Trim()
            if ($exePath) { $py = $exePath; break }
        } catch {}
    }
    if (-not $py) { Say "  找不到 Python 3.8 以上，改用附帶的 Python。" "Yellow" }
}
if (-not $py) {
    $pyDir = Join-Path $InstallDir "python"
    $py = Join-Path $pyDir "python.exe"
    if (-not (Test-Path $py)) {
        Say "  下載 Python $PyVer 精簡版（約 11 MB，只給面板用，不影響這台的其他 Python）…"
        $zip = Join-Path $env:TEMP "python-$PyVer-embed-amd64.zip"
        Download "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip" $zip
        New-Item -ItemType Directory -Force $pyDir | Out-Null
        Expand-Archive -Path $zip -DestinationPath $pyDir -Force
        Remove-Item $zip -ErrorAction SilentlyContinue
        $sig = Get-AuthenticodeSignature $py
        if ($sig.Status -ne "Valid") { throw "python.exe 的數位簽章不對（$($sig.Status)）" }
    }
}
$pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
Say "  Python：$py"

# ── 放面板檔案（設定檔保留） ──
$appDir = Join-Path $InstallDir "app"
$pidFile = Join-Path $InstallDir "panel.pid"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
if (Test-Path $pidFile) {
    $oldPid = [int](Get-Content $pidFile -Raw)
    $old = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($old -and $old.ProcessName -match "^python") { Stop-Process -Id $oldPid -Force; Start-Sleep 1 }
}
if (Test-Path $appDir) { Remove-Item $appDir -Recurse -Force }
Copy-Item (Join-Path $Here "app") $appDir -Recurse
Get-ChildItem $appDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Copy-Item (Join-Path $Here "uninstall.ps1") $InstallDir -Force
Copy-Item (Join-Path $Here "show-token.ps1") $InstallDir -Force

$cfgPath = Join-Path $InstallDir "config.json"
$token = $null
if (Test-Path $cfgPath) {
    try { $token = (Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).token } catch {}
}
if (-not $token) {
    $chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789".ToCharArray()
    $bytes = New-Object byte[] 24
    (New-Object Security.Cryptography.RNGCryptoServiceProvider).GetBytes($bytes)
    $token = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}
if ($Lan) { $bindHost = "0.0.0.0" } else { $bindHost = "127.0.0.1" }
$cfg = [ordered]@{
    host = $bindHost; port = $PanelPort; token = $token; terminal = (-not $NoTerminal)
    ollama = $OllamaUrl; data_dir = $InstallDir
}
[IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))

# ── 開機自動啟動＋現在啟動 ──
$server = Join-Path $appDir "server.py"
$argLine = "`"$server`" --config `"$cfgPath`""
if (-not $NoAutoStart) {
    try {
        $act = New-ScheduledTaskAction -Execute $pyw -Argument $argLine -WorkingDirectory $InstallDir
        $trg = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
        $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trg -Settings $set -Description "AI 主機面板" -Force | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        Say "  已設定登入後自動啟動（工作排程器：$TaskName）"
    } catch {
        Say "  工作排程器設定失敗（$($_.Exception.Message)），改用啟動資料夾捷徑。" "Yellow"
        $ws = New-Object -ComObject WScript.Shell
        $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Startup")) "AI 主機面板.lnk"))
        $lnk.TargetPath = $pyw; $lnk.Arguments = $argLine; $lnk.WorkingDirectory = $InstallDir; $lnk.Save()
        Start-Process $pyw -ArgumentList $argLine -WorkingDirectory $InstallDir
    }
} else {
    Start-Process $pyw -ArgumentList $argLine -WorkingDirectory $InstallDir
}

$panelUrl = "http://127.0.0.1:$PanelPort/"
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    try { Invoke-RestMethod ($panelUrl + "api/ping") -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { Start-Sleep 1 }
}
if ($ok) { Say "  面板執行中" "Green" } else { Say "  面板沒有起來，看 $InstallDir\panel.log" "Red" }

# ── 區網 ──
$lanUrls = @()
if ($Lan) {
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notmatch "^(127\.|169\.254\.)" -and $_.PrefixOrigin -ne "WellKnown" } | Select-Object -ExpandProperty IPAddress
    $lanUrls = @($ips | ForEach-Object { "http://$($_):$PanelPort/" })
    if (IsAdmin) {
        Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP -LocalPort $PanelPort -Action Allow -Profile Private | Out-Null
        Say "  防火牆已開放 $PanelPort（只限「私人」網路）"
    } else {
        Say "  要讓手機連進來，還要開防火牆：用「系統管理員」開 PowerShell 執行：" "Yellow"
        Say "    New-NetFirewallRule -DisplayName `"$RuleName`" -Direction Inbound -Protocol TCP -LocalPort $PanelPort -Action Allow -Profile Private" "Yellow"
    }
}

# ── 開始功能表 ──
if (-not $NoShortcuts) {
    $menu = Join-Path ([Environment]::GetFolderPath("Programs")) "AI 主機面板"
    New-Item -ItemType Directory -Force $menu | Out-Null
    [IO.File]::WriteAllText((Join-Path $menu "開啟面板.url"), "[InternetShortcut]`r`nURL=$panelUrl`r`n")
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut((Join-Path $menu "顯示登入碼.lnk"))
    $s.TargetPath = "powershell.exe"; $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -NoExit -File `"$InstallDir\show-token.ps1`""; $s.Save()
    $s = $ws.CreateShortcut((Join-Path $menu "解除安裝.lnk"))
    $s.TargetPath = "powershell.exe"; $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -NoExit -File `"$InstallDir\uninstall.ps1`" -InstallDir `"$InstallDir`""; $s.Save()
    $desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "AI 主機面板.url"
    [IO.File]::WriteAllText($desk, "[InternetShortcut]`r`nURL=$panelUrl`r`n")
}

# ── 先下載模型 ──
$ol = FindOllama
foreach ($m in $Models) {
    if (-not $ol -or -not (OllamaUp)) { Say "  Ollama 沒在執行，模型 $m 之後在面板裡下載。" "Yellow"; break }
    Step "下載模型 $m"
    & $ol pull $m
}

# ── 完成 ──
Write-Host ""
Write-Host "  ✔ 安裝完成" -ForegroundColor Green
Write-Host "  面板：$panelUrl" -ForegroundColor White
foreach ($u in $lanUrls) { Write-Host "  手機／其他電腦：$u" -ForegroundColor White }
Write-Host "  登入碼：$token" -ForegroundColor Yellow
Write-Host "  （忘記的話：開始功能表 → AI 主機面板 → 顯示登入碼）" -ForegroundColor DarkGray
if ($Lan) { Write-Host "  注意：區網連線沒有加密，只在自己家裡／公司內網用；要從外面連請用 Tailscale 之類的 VPN。" -ForegroundColor DarkGray }
Write-Host ""
if (-not $Quiet -and $ok) { Start-Process $panelUrl }
