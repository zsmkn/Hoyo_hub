# ZZZ Proxy Hub - get Zenless Zone Zero signal search (gacha) history URL
# Read-only: finds the game folder from Player.log and reads the newest
# getGachaLog URL from the in-game browser cache. Nothing is uploaded.
# Usage: open Signal Search > History in game first, then run this script.

$ErrorActionPreference = "Stop"

$logs = Get-ChildItem "$env:USERPROFILE\AppData\LocalLow\miHoYo" -Recurse -Filter "Player.log" -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName

if (-not $logs) {
    Write-Host "Player.log not found. Please launch the game first." -ForegroundColor Red
    return
}

$gameData = $null
foreach ($log in $logs) {
    $m = Select-String -Path $log -Pattern '([A-Za-z]:[\\/].+?ZenlessZoneZero_Data)' | Select-Object -First 1
    if ($m) { $gameData = $m.Matches[0].Groups[1].Value; break }
}
if (-not $gameData) {
    Write-Host "Game folder not found in Player.log." -ForegroundColor Red
    return
}

$cacheRoot = Join-Path $gameData "webCaches"
$latest = Get-ChildItem $cacheRoot -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$data2 = Join-Path $latest.FullName "Cache\Cache_Data\data_2"
if (-not (Test-Path $data2)) {
    Write-Host "Cache file not found: $data2" -ForegroundColor Red
    return
}

$tmp = Join-Path $env:TEMP "zzz_data_2.tmp"
Copy-Item $data2 $tmp -Force
$text = [System.Text.Encoding]::UTF8.GetString([System.IO.File]::ReadAllBytes($tmp))
Remove-Item $tmp -Force

$found = [regex]::Matches($text, 'https://[^\s"\x00]+?getGachaLog\?[^\s"\x00]+')
if ($found.Count -eq 0) {
    $found = [regex]::Matches($text, 'https://[^\s"\x00]+?authkey=[^\s"\x00]+')
}
if ($found.Count -eq 0) {
    Write-Host "URL not found. Open Signal Search > History in game, then run again." -ForegroundColor Yellow
    return
}

$url = $found[$found.Count - 1].Value
Set-Clipboard -Value $url
Write-Host ""
Write-Host $url
Write-Host ""
Write-Host "Copied to clipboard! Paste it into ZZZ Proxy Hub." -ForegroundColor Green
