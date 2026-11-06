@echo off
rem Oeffnet das ClipBot-Dashboard im Browser.
cd /d "%~dp0"
if not exist .venv (
  echo Erste Einrichtung, das dauert einen Moment...
  python -m venv .venv || goto :err
  .venv\Scripts\python -m pip install --upgrade pip
)
.venv\Scripts\python -m pip install -q --upgrade -r requirements.txt || goto :err
.venv\Scripts\python app.py %*
pause
exit /b
:err
echo Einrichtung fehlgeschlagen. Ist Python 3.10+ installiert und im PATH?
pause
