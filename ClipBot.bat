@echo off
rem ClipBot-Starter fuer Windows: Doppelklick genuegt.
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
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%ProgramFiles%\nodejs;%PATH%"

rem Laeuft schon? Dann nur oeffnen.
curl.exe -s -o nul -m 2 "%URL%/api/state" && (
  start "" "%URL%"
  exit /b 0
)

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
echo [1/4] Python wird installiert ...
call :winget Python.Python.3.12 --scope user
call :findpy
if not defined PYEXE (
  echo Python konnte nicht installiert werden.
  echo Bitte Python 3.12 von https://www.python.org/downloads/ installieren
  echo und dabei "Add python.exe to PATH" ankreuzen. Danach ClipBot.bat erneut starten.
  goto :fail
)
:havepy
echo [1/4] Python gefunden: %PYEXE% %PYARG%

rem ---------- ffmpeg (schneidet und rendert die Clips)
where ffmpeg >nul 2>&1 && goto :haveffmpeg
echo [2/4] ffmpeg wird installiert ...
call :winget Gyan.FFmpeg
where ffmpeg >nul 2>&1 || (
  echo ffmpeg konnte nicht installiert werden. Bitte in einer Eingabeaufforderung ausfuehren:
  echo   winget install Gyan.FFmpeg
  echo Danach ClipBot.bat erneut starten.
  goto :fail
)
:haveffmpeg
echo [2/4] ffmpeg gefunden.

rem ---------- Node.js (nur fuer Erklaervideos noetig, deshalb kein Abbruch bei Fehlern)
where node >nul 2>&1 && goto :havenode
echo [3/4] Node.js wird installiert (fuer Erklaervideos) ...
call :winget OpenJS.NodeJS.LTS
where node >nul 2>&1 || echo     Node.js fehlt noch. Clips gehen trotzdem, nur Erklaervideos brauchen es (nodejs.org).
:havenode

rem ---------- Python-Umgebung
echo [4/4] Python-Pakete werden installiert ...
"%PYEXE%" %PYARG% -m venv .venv || (
  echo Konnte keine Python-Umgebung anlegen.
  goto :fail
)
.venv\Scripts\python.exe -m pip install --upgrade pip >>"%LOG%" 2>&1
call :shortcut

:packages
rem Pakete nur neu installieren, wenn sich requirements.txt geaendert hat
fc /b requirements.txt .venv\.requirements-installed >nul 2>&1 && goto :start
echo Pakete werden installiert, das kann ein paar Minuten dauern ...
.venv\Scripts\python.exe -m pip install --upgrade -r requirements.txt || (
  echo Pakete konnten nicht installiert werden. Ist das Internet verbunden?
  goto :fail
)
copy /y requirements.txt .venv\.requirements-installed >nul

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
