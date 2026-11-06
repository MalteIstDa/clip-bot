@echo off
rem Kompletter Lauf ohne Oberflaeche (fuer die Windows-Aufgabenplanung).
cd /d "%~dp0"
.venv\Scripts\python run.py %*
