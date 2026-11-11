@echo off
rem ClipBot-Starter fuer Windows: Doppelklick genuegt.
rem Bei jedem Start holt es die neueste Version von GitHub (update.ps1).
rem Beim ersten Start wird alles eingerichtet (Python, ffmpeg, Node.js, Pakete) und eine
rem Verknuepfung auf dem Desktop angelegt. Danach startet das Dashboard und der Browser oeffnet sich.
rem Laeuft ClipBot schon, wird nur der Browser geoeffnet.
rem Beenden: im Dashboard auf "ClipBot beenden" oder das Fenster "ClipBot" in der Taskleiste schliessen.
setlocal
title ClipBot
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "URL=http://127.0.0.1:8765"
set "LOG=data\clipbot.log"

if not exist app.py (
  echo app.py fehlt. ClipBot.bat muss im ClipBot-Ordner liegen, neben app.py.
  goto :fail
)
if not exist data mkdir data

rem Programme, die winget installiert, sind sofort nutzbar (auch ohne Neuanmeldung)
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%ProgramFiles%\WinGet\Links;%ProgramFiles%\nodejs;%PATH%"

rem Laeuft schon? Dann nur oeffnen.
curl.exe -s -o nul -m 2 "%URL%/api/state" && (
  start "" "%URL%"
  exit /b 0
)

rem Neue Version von GitHub holen. Muss in EINER Zeile stehen, weil update.ps1 diese Datei ersetzen
rem kann: cmd liest die Zeile vorher ganz ein und startet danach die neue ClipBot.bat.
if exist "%ROOT%update.ps1" (powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%update.ps1" & if errorlevel 10 (set "CLIPBOT_NO_UPDATE=1" & start "ClipBot" "%ROOT%ClipBot.bat" & exit /b 0))

rem ---------- ffmpeg (schneidet und rendert die Clips), wird bei jedem Start geprueft
call :findffmpeg && goto :haveffmpeg
echo ffmpeg wird installiert ...
call :winget Gyan.FFmpeg
call :findffmpeg || (
  echo ffmpeg wurde nicht gefunden. Bitte dieses Fenster schliessen und ClipBot.bat
  echo noch einmal per Doppelklick starten. Hilft das nicht, einmal ab- und wieder
  echo anmelden. Fehlt ffmpeg dann immer noch: winget install Gyan.FFmpeg ausfuehren.
  goto :fail
)
:haveffmpeg

if exist .venv\Scripts\python.exe goto :packages

echo.
echo  ==================================================
echo   ClipBot wird eingerichtet. Das dauert beim ersten
echo   Start einige Minuten. Bitte das Fenster offen lassen.
echo  ==================================================
echo.

rem ---------- Python (3.12 bevorzugt, dafuer gibt es fertige Pakete fuer die Spracherkennung)
call :findpy
if defined PYEXE goto :havepy
echo [1/3] Python wird installiert ...
call :winget Python.Python.3.12 --scope user
call :findpy
if not defined PYEXE (
  echo Python konnte nicht installiert werden.
  echo Bitte Python 3.12 von https://www.python.org/downloads/ installieren
  echo und dabei "Add python.exe to PATH" ankreuzen. Danach ClipBot.bat erneut starten.
  goto :fail
)
:havepy
echo [1/3] Python gefunden: %PYEXE% %PYARG%

rem ---------- Node.js (nur fuer Erklaervideos noetig, deshalb kein Abbruch bei Fehlern)
where node >nul 2>&1 && goto :havenode
echo [2/3] Node.js wird installiert (fuer Erklaervideos) ...
call :winget OpenJS.NodeJS.LTS
where node >nul 2>&1 || echo     Node.js fehlt noch. Clips gehen trotzdem, nur Erklaervideos brauchen es (nodejs.org).
:havenode

rem ---------- Python-Umgebung
echo [3/3] Python-Pakete werden installiert ...
"%PYEXE%" %PYARG% -m venv .venv || (
  echo Konnte keine Python-Umgebung anlegen.
  goto :fail
)
.venv\Scripts\python.exe -m pip install --upgrade pip >>"%LOG%" 2>&1

:packages
rem Pakete nur neu installieren, wenn sich requirements.txt geaendert hat
fc /b requirements.txt .venv\.requirements-installed >nul 2>&1 && goto :start
echo Pakete werden installiert. Das dauert beim ersten Mal 5 bis 15 Minuten,
echo die Spracherkennung ist mehrere hundert MB gross. Bitte das Fenster offen lassen.
.venv\Scripts\python.exe -m pip install --upgrade -r requirements.txt || (
  echo Pakete konnten nicht installiert werden. Ist das Internet verbunden?
  goto :fail
)
copy /y requirements.txt .venv\.requirements-installed >nul
call :shortcut

:start
if not exist .env if exist .env.example (
  echo Hinweis: Es gibt noch keine .env-Datei. Fuer Uploads und KI .env.example nach .env kopieren
  echo und die eigenen Schluessel eintragen, siehe ANLEITUNG-WINDOWS.md.
)
echo ClipBot startet ...
echo === Start %DATE% %TIME% ===>>"%LOG%"
start "ClipBot - Fenster schliessen beendet ClipBot" /min cmd /c ".venv\Scripts\python.exe app.py --no-browser >>%LOG% 2>&1"
for /l %%i in (1,1,90) do (
  curl.exe -s -o nul -m 2 "%URL%/api/state" && (
    start "" "%URL%"
    exit /b 0
  )
  timeout /t 1 /nobreak >nul
)
echo ClipBot ist nicht gestartet. Die letzten Zeilen aus %LOG%:
powershell -NoProfile -Command "Get-Content -Tail 25 '%LOG%'"
goto :fail

rem ================================================================ Hilfsroutinen
:findpy
set "PYEXE="
set "PYARG="
for %%v in (3.12 3.11 3.13) do (
  if not defined PYEXE py -%%v -c "import sys" >nul 2>&1 && (
    set "PYEXE=py"
    set "PYARG=-%%v"
  )
)
if defined PYEXE exit /b 0
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  exit /b 0
)
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PYEXE=python"
exit /b 0

:winget
where winget >nul 2>&1 || (
  echo     winget fehlt. Bitte "App Installer" aus dem Microsoft Store installieren.
  exit /b 1
)
winget install -e --id %* --silent --accept-package-agreements --accept-source-agreements
set "WG=%errorlevel%"
call :refreshpath
exit /b %WG%

:refreshpath
rem winget aendert den PATH nur fuer neue Fenster. Hier neu aus der Registry laden.
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%ProgramFiles%\WinGet\Links;%%p"
exit /b 0

:findffmpeg
where ffmpeg >nul 2>&1 && exit /b 0
rem Fallback: direkt im winget-Paketordner suchen
for /d %%d in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*") do for /d %%e in ("%%d\*") do if exist "%%e\bin\ffmpeg.exe" set "PATH=%%e\bin;%PATH%"
where ffmpeg >nul 2>&1
exit /b %errorlevel%

:shortcut
rem Verknuepfung "ClipBot" auf dem Desktop (mit Symbol), nur beim ersten Start
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $l=Join-Path $d 'ClipBot.lnk'; if (-not (Test-Path $l)) { $s=(New-Object -ComObject WScript.Shell).CreateShortcut($l); $s.TargetPath='%ROOT%ClipBot.bat'; $s.WorkingDirectory='%ROOT%'; $s.IconLocation='%ROOT%ClipBot.ico'; $s.WindowStyle=7; $s.Save(); Write-Host '    Verknuepfung ClipBot auf dem Desktop angelegt.' }" 2>nul
exit /b 0

:fail
echo.
echo Etwas ist schiefgelaufen. Details stehen auch in %LOG%.
pause
exit /b 1
