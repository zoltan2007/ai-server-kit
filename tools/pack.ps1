# 打包成一個 zip（給其他主機用）：dist\AI-Server-Kit-<版本>.zip
# 解壓後 Windows 雙擊「安裝.bat」，Linux 執行 sudo bash install.sh
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ver = ([regex]::Match((Get-Content (Join-Path $root "app\server.py") -Raw -Encoding UTF8), 'VERSION = "([^"]+)"')).Groups[1].Value
$name = "AI-Server-Kit-$ver"
$dist = Join-Path $root "dist"
$stage = Join-Path $env:TEMP $name
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage, $dist | Out-Null

foreach ($f in @("安裝.bat", "install.ps1", "uninstall.ps1", "show-token.ps1", "install.sh", "uninstall.sh",
        "README.md", "README.zh-TW.md", "LICENSE")) {
    Copy-Item (Join-Path $root $f) $stage
}
Copy-Item (Join-Path $root "app") (Join-Path $stage "app") -Recurse
Copy-Item (Join-Path $root "receiver") (Join-Path $stage "receiver") -Recurse
Remove-Item (Join-Path $stage "app\catalog.json.bak") -ErrorAction SilentlyContinue
# 收模型伺服器的本機產物不要打包
Remove-Item (Join-Path $stage "receiver\config.json"), (Join-Path $stage "receiver\received") -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
# Linux 腳本要 LF 換行
foreach ($f in @("install.sh", "uninstall.sh")) {
    $p = Join-Path $stage $f
    $t = [IO.File]::ReadAllText($p) -replace "`r`n", "`n"
    [IO.File]::WriteAllText($p, $t, (New-Object Text.UTF8Encoding($false)))
}

$zip = Join-Path $dist "$name.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
# 一個一個加：.NET Framework 的 CreateFromDirectory 用反斜線當路徑，Linux 解壓會壞
$fs = [IO.File]::Open($zip, [IO.FileMode]::Create)
$za = New-Object IO.Compression.ZipArchive($fs, [IO.Compression.ZipArchiveMode]::Create, $false, [Text.Encoding]::UTF8)
foreach ($f in Get-ChildItem $stage -Recurse -File) {
    $rel = $name + "/" + ($f.FullName.Substring($stage.Length + 1) -replace "\\", "/")
    [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($za, $f.FullName, $rel, [IO.Compression.CompressionLevel]::Optimal) | Out-Null
}
$za.Dispose()
$fs.Dispose()
Remove-Item $stage -Recurse -Force
Write-Host ("已打包：{0}（{1:N0} KB）" -f $zip, ((Get-Item $zip).Length / 1KB))
