# ClipBot-Aktualisierung fuer Windows (wird von ClipBot.bat bei jedem Start aufgerufen).
# Holt die neueste Version von GitHub und kopiert sie ueber den ClipBot-Ordner.
# Eigene Dateien (.env, client_secret.json, config.yaml, tokens, data, .venv) sind nicht im
# Download enthalten und bleiben deshalb unberuehrt.
# Exit-Code 10 = Dateien wurden ersetzt (ClipBot.bat startet sich dann neu, ohne erneut zu pruefen),
# 0 = nichts zu tun oder offline.
$ErrorActionPreference = 'Stop'
$repo = 'MalteIstDa/clip-bot'
$branch = 'main'
$root = $PSScriptRoot
$stamp = Join-Path $root 'data\version.txt'

# Ordner, die mit git verwaltet werden (z. B. GitHub Desktop), aktualisiert git selbst.
if (Test-Path (Join-Path $root '.git')) { exit 0 }
if ($env:CLIPBOT_NO_UPDATE) { exit 0 }

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $headers = @{ 'User-Agent' = 'ClipBot-Updater' }
    $latest = [string](Invoke-RestMethod -TimeoutSec 10 -Headers $headers `
        -Uri "https://api.github.com/repos/$repo/branches/$branch").commit.sha
    if ($latest -notmatch '^[0-9a-f]{40}$') { exit 0 }

    $current = ''
    if (Test-Path $stamp) { $current = (Get-Content $stamp -Raw).Trim() }
    if ($current -eq $latest) { exit 0 }

    Write-Host 'Neue ClipBot-Version gefunden, wird geladen ...'
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('clipbot-update-' + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp 'clipbot.zip'
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 -Headers $headers `
            -Uri "https://github.com/$repo/archive/$latest.zip" -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $src = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
        if (-not $src -or -not (Test-Path (Join-Path $src.FullName 'app.py'))) { throw 'Download unvollstaendig' }

        # Ordner zusammenfuehren, vorhandene Programmdateien ueberschreiben
        robocopy $src.FullName $root /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) {
            # Teilweise kopiert: ClipBot.bat neu starten lassen, beim naechsten Start erneut versuchen
            Write-Host "Aktualisierung unvollstaendig (robocopy $LASTEXITCODE), naechster Start versucht es erneut."
            exit 10
        }
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }

    New-Item -ItemType Directory -Force -Path (Split-Path $stamp) | Out-Null
    Set-Content -Path $stamp -Value $latest -Encoding ASCII
    Write-Host 'ClipBot wurde aktualisiert.'
    exit 10
} catch {
    # Kein Internet oder GitHub nicht erreichbar: mit der vorhandenen Version weitermachen.
    Write-Host "Aktualisierung uebersprungen: $($_.Exception.Message)"
    exit 0
}
